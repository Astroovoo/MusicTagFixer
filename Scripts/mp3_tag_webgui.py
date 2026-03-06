#!/usr/bin/env python3
from __future__ import annotations

import argparse
import html
import json
import os
import re
import traceback
import webbrowser
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from string import Template
from typing import Dict, List, Optional, Tuple
from urllib.parse import parse_qs, urlparse

from mutagen.id3 import ID3, ID3NoHeaderError

from fix_mp3_japanese_mojibake import fix_mojibake, fix_tags, iter_mp3_files

APP_VERSION = '2026.03.06-scan-paths-v7'


def checked(flag: bool) -> str:
    return "checked" if flag else ""


def selected(value: str, current: str) -> str:
    return "selected" if value == current else ""


def escape(text: str) -> str:
    return html.escape(text, quote=True)


def default_state() -> Dict[str, object]:
    return {
        "path": "",
        "recursive": True,
        "dry_run": True,
        "do_rename": True,
        "do_tags": True,
        "verbose": True,
        "genre": "",
        "genre_mode": "fill",
    }



SUSPICIOUS_CHARS = set(
    "ÃÂãâ¢£¤¥¦§¨©ª«¬®¯°±²³´µ¶·¸¹º»¼½¾¿ÐÑÒÓÔÕÖ×ØÙÚÛÜÝÞßàáäåæçèéêëìíîïðñòóôõö÷øùúûüýþÿ�"
)
FULL_JP_RE = re.compile(r"[\u3040-\u30ff\u3400-\u9fff]")
KANA_RE = re.compile(r"[\u3040-\u30ff]")
HALF_KATA_RE = re.compile(r"[\uff66-\uff9f]")
# Typical characters seen when Shift_JIS Japanese was decoded as GBK/CP936.
MOJIBAKE_JP_MARKERS = set("偺偵偡偲偮偱偳偭偦偧偨偪傫傑傒傕傞儖儗儞")
INVALID_WIN_NAME_CHARS = set('<>:"/\\|?*')


def _count_suspicious(text: str) -> int:
    return sum(1 for ch in text if ch in SUSPICIOUS_CHARS or (ord(ch) < 32 and ch not in "\t\n\r"))


def _count_full_japanese(text: str) -> int:
    return len(FULL_JP_RE.findall(text))


def _count_kana(text: str) -> int:
    return len(KANA_RE.findall(text))


def _count_half_katakana(text: str) -> int:
    return len(HALF_KATA_RE.findall(text))


def _count_mojibake_markers(text: str) -> int:
    return sum(1 for ch in text if ch in MOJIBAKE_JP_MARKERS)


def _quality_filename_text(text: str) -> Tuple[int, int, int, int, int, int, int, int, int]:
    # Lower is better.
    replacement = text.count("\ufffd")
    suspicious = _count_suspicious(text)
    full_jp = _count_full_japanese(text)
    kana = _count_kana(text)
    half_kata = _count_half_katakana(text)
    markers = _count_mojibake_markers(text)
    no_full_jp_penalty = 0 if full_jp > 0 else 1
    no_kana_when_marked_penalty = 1 if (markers > 0 and kana == 0) else 0
    return (
        replacement,
        markers * 4 + suspicious + half_kata * 2,
        no_full_jp_penalty,
        markers,
        no_kana_when_marked_penalty,
        half_kata,
        -kana,
        -full_jp,
        len(text),
    )


def _iter_recode_candidates(text: str, max_depth: int = 2, max_nodes: int = 260) -> List[str]:
    if not text:
        return [text]

    encode_list = ["latin1", "cp1252", "cp932", "shift_jis", "euc_jp", "cp936", "gbk", "utf-8"]
    decode_list = ["utf-8", "cp932", "shift_jis", "euc_jp", "cp936", "gbk", "latin1", "cp1252"]

    seen = {text}
    ordered = [text]
    frontier = [text]

    for _ in range(max_depth):
        new_frontier: List[str] = []
        for current in frontier:
            for src in encode_list:
                try:
                    raw = current.encode(src)
                except Exception:
                    continue

                for dst in decode_list:
                    if src == dst:
                        continue
                    try:
                        candidate = raw.decode(dst)
                    except Exception:
                        continue

                    if candidate in seen:
                        continue
                    seen.add(candidate)
                    ordered.append(candidate)
                    new_frontier.append(candidate)
                    if len(seen) >= max_nodes:
                        return ordered

        if not new_frontier:
            break
        frontier = new_frontier

    return ordered


def _direct_legacy_jp_candidates(text: str) -> List[str]:
    pairs = [
        ("cp936", "cp932"),
        ("gbk", "cp932"),
        ("cp936", "shift_jis"),
        ("gbk", "shift_jis"),
    ]
    out: List[str] = []
    for src, dst in pairs:
        try:
            cand = text.encode(src).decode(dst)
            # High-confidence only: candidate should round-trip back to the source text.
            if cand.encode(dst).decode(src) != text:
                continue
        except Exception:
            continue
        if cand not in out:
            out.append(cand)
    return out


def _direct_candidate_priority(text: str) -> Tuple[int, int, int, int, int, int]:
    kana = _count_kana(text)
    markers = _count_mojibake_markers(text)
    suspicious = _count_suspicious(text)
    half_kata = _count_half_katakana(text)
    full_jp = _count_full_japanese(text)
    return (
        0 if kana > 0 else 1,
        markers,
        suspicious + half_kata * 2,
        -kana,
        -full_jp,
        len(text),
    )


def _is_likely_mojibake_name(text: str) -> bool:
    return (
        _count_mojibake_markers(text) > 0
        or _count_half_katakana(text) > 0
        or _count_suspicious(text) > 0
    )


def _sanitize_filename_stem(text: str) -> str:
    cleaned = []
    for ch in text:
        if ch in INVALID_WIN_NAME_CHARS or ord(ch) < 32:
            cleaned.append(" ")
        else:
            cleaned.append(ch)
    out = "".join(cleaned)
    out = re.sub(r"\s+", " ", out).strip().rstrip(".")
    return out


def _extract_track_prefix(stem: str) -> str:
    m = re.match(r"^(\d{1,3}\s*[-_.]?\s+)", stem)
    if m:
        return m.group(1)
    return ""


def _title_candidate_from_tags(path: Path, original_stem: str) -> Optional[str]:
    try:
        tags = ID3(str(path))
    except ID3NoHeaderError:
        return None
    except Exception:
        return None

    frames = tags.getall("TIT2")
    if not frames:
        return None

    best: Optional[str] = None
    best_key: Optional[Tuple[int, int, int, int, int, int, int, int, int]] = None

    for frame in frames:
        values = list(getattr(frame, "text", []))
        for raw in values:
            src = str(raw).strip()
            if not src:
                continue

            candidates = [src]
            fixed = fix_mojibake(src, max_rounds=1)
            if fixed != src:
                candidates.append(fixed)

            for cand in candidates:
                stem = _sanitize_filename_stem(cand)
                if not stem:
                    continue
                key = _quality_filename_text(stem)
                if best is None or key < best_key:
                    best = stem
                    best_key = key

    if not best:
        return None

    prefix = _extract_track_prefix(original_stem)
    if prefix and not re.match(r"^\d{1,3}\s*[-_.]?\s+", best):
        best = prefix + best

    best = _sanitize_filename_stem(best)
    if not best or best == original_stem:
        return None
    return best


def fix_mojibake_filename_text(text: str) -> str:
    if not text:
        return text

    original = text
    original_key = _quality_filename_text(original)
    marker_count = _count_mojibake_markers(original)
    original_is_clean = (
        _count_full_japanese(original) > 0
        and _count_suspicious(original) == 0
        and _count_half_katakana(original) == 0
        and marker_count == 0
    )

    # Keep clearly good Japanese text untouched.
    if original_is_clean:
        return original

    # For classic Shift_JIS->GBK mojibake markers, only use high-confidence direct recovery.
    if marker_count > 0:
        direct = _direct_legacy_jp_candidates(original)
        if not direct:
            return original

        best_direct = min(direct, key=_direct_candidate_priority)
        if _quality_filename_text(best_direct) < original_key:
            return best_direct
        return original

    best = original
    best_key = original_key

    # Generic recode search for non-marker cases.
    for cand in _iter_recode_candidates(original, max_depth=2, max_nodes=280):
        key = _quality_filename_text(cand)
        if key < best_key:
            best = cand
            best_key = key

    # More aggressive pass only when source strongly looks mojibaked.
    if _count_half_katakana(original) > 0 or _count_suspicious(original) > 0:
        for cand in _iter_recode_candidates(original, max_depth=3, max_nodes=460):
            key = _quality_filename_text(cand)
            if key < best_key:
                best = cand
                best_key = key

    if best_key < original_key:
        return best
    return original


def fix_filename_web(path: Path, dry_run: bool) -> Tuple[bool, str, Optional[Path]]:
    original = path.stem
    algo_stem = fix_mojibake_filename_text(original)
    tag_title_stem = _title_candidate_from_tags(path, original)

    new_stem = original
    if _is_likely_mojibake_name(original) and tag_title_stem:
        # User-expected behavior: when file name is mojibake, prefer tag title.
        new_stem = tag_title_stem
    elif algo_stem != original:
        new_stem = algo_stem
    elif tag_title_stem and _quality_filename_text(tag_title_stem) < _quality_filename_text(original):
        new_stem = tag_title_stem

    new_stem = _sanitize_filename_stem(new_stem)
    if new_stem == original or not new_stem:
        return False, "", None

    if any(ch in INVALID_WIN_NAME_CHARS for ch in new_stem):
        return False, "skip rename (invalid target name): {0}".format(new_stem), None

    new_path = path.with_name(new_stem + path.suffix)
    if new_path.exists():
        return False, "skip rename (target exists): {0}".format(new_path.name), None

    if not dry_run:
        os.rename(str(path), str(new_path))
    return True, "rename: {0} -> {1}".format(path.name, new_path.name), new_path

def _existing_drive_roots() -> List[Path]:
    roots: List[Path] = []
    for letter in "ABCDEFGHIJKLMNOPQRSTUVWXYZ":
        root = Path("{0}:\\".format(letter))
        if root.exists():
            roots.append(root)
    return roots


def _candidate_scan_roots() -> List[Path]:
    home = Path.home()
    user_profile = os.environ.get("USERPROFILE", "")
    user_home = Path(user_profile) if user_profile else home

    candidates = [
        user_home / "Music",
        user_home / "OneDrive" / "Music",
        user_home / "Desktop",
        user_home / "OneDrive" / "Desktop",
        user_home / "Downloads",
        user_home,
        home,
        Path.cwd(),
        Path.cwd().parent,
    ]

    # Add drive roots as a broad fallback.
    candidates.extend(_existing_drive_roots())

    out: List[Path] = []
    seen = set()
    for p in candidates:
        key = str(p).lower()
        if key in seen:
            continue
        seen.add(key)
        if p.exists() and p.is_dir():
            out.append(p)
    return out


def scan_mp3_directories(
    max_depth: int = 5,
    max_results: int = 120,
    max_dirs: int = 90000,
) -> List[Dict[str, object]]:
    roots = _candidate_scan_roots()
    seen_dirs = 0
    results: Dict[str, int] = {}

    for root in roots:
        stack: List[Tuple[Path, int]] = [(root, 0)]

        while stack and seen_dirs < max_dirs and len(results) < max_results:
            current, depth = stack.pop()
            seen_dirs += 1

            mp3_count_here = 0
            subdirs: List[Path] = []

            try:
                with os.scandir(str(current)) as it:
                    for entry in it:
                        try:
                            if entry.is_file(follow_symlinks=False):
                                if entry.name.lower().endswith(".mp3"):
                                    mp3_count_here += 1
                            elif entry.is_dir(follow_symlinks=False) and depth < max_depth:
                                subdirs.append(Path(entry.path))
                        except Exception:
                            continue
            except Exception:
                continue

            if mp3_count_here > 0:
                key = str(current.resolve())
                prev = int(results.get(key, 0))
                if mp3_count_here > prev:
                    results[key] = mp3_count_here

            # DFS order, no sort for speed.
            for sub in subdirs:
                stack.append((sub, depth + 1))

        if seen_dirs >= max_dirs or len(results) >= max_results:
            break

    sorted_items = sorted(results.items(), key=lambda x: (-x[1], x[0].lower()))
    return [{"path": p, "count": c} for p, c in sorted_items[:max_results]]


def render_page(
    state: Dict[str, object],
    summary: str = "",
    logs: Optional[List[str]] = None,
    error: str = "",
) -> str:
    logs = logs or []
    logs_text = "\n".join(logs)

    tpl = Template(
        """<!doctype html>
<html lang="zh-CN">
<head>
  <meta charset="utf-8" />
  <meta name="viewport" content="width=device-width, initial-scale=1" />
  <title>MusicTagFixer Web GUI</title>
  <style>
    body { font-family: "Segoe UI", "PingFang SC", "Microsoft YaHei", sans-serif; margin: 24px; background: #f6f8fb; color: #1c2430; }
    .wrap { max-width: 1040px; margin: 0 auto; }
    .card { background: #fff; border: 1px solid #dde3ed; border-radius: 10px; padding: 16px; margin-bottom: 14px; }
    h1 { margin: 0 0 12px 0; font-size: 24px; }
    .grid { display: grid; grid-template-columns: 170px 1fr; gap: 10px 12px; align-items: center; }
    input[type="text"] { width: 100%; padding: 8px; border: 1px solid #c8d2e0; border-radius: 6px; }
    select { width: 100%; padding: 7px; border: 1px solid #c8d2e0; border-radius: 6px; }
    .row { margin: 8px 0; }
    .checks { display: flex; flex-wrap: wrap; gap: 12px; }
    .pickline { display: flex; gap: 10px; align-items: center; }
    .pickline button { flex: none; }
    button { background: #1f6feb; color: #fff; border: 0; border-radius: 8px; padding: 10px 14px; cursor: pointer; }
    button:hover { background: #1559c1; }
    button:disabled { opacity: 0.6; cursor: default; }
    .hint { color: #566377; font-size: 13px; margin-top: 8px; }
    .summary { padding: 10px; border-radius: 8px; background: #eef6ff; border: 1px solid #c9defc; margin-bottom: 12px; }
    .error { white-space: pre-wrap; padding: 10px; border-radius: 8px; background: #fff1f0; border: 1px solid #ffc7c2; color: #7a1411; }
    pre { background: #0f1724; color: #d5e2ff; padding: 12px; border-radius: 8px; max-height: 420px; overflow: auto; }
    #scan_status { font-size: 13px; color: #2d4a74; }
  </style>
</head>
<body>
  <div class="wrap">
    <h1>MusicTagFixer Web GUI <small style="font-size:13px;color:#5b6880;">v$version</small></h1>

    <div class="card">
      <form method="post" action="/run">
        <div class="grid">
          <label for="path">目录或单个 MP3</label>
          <input id="path" name="path" type="text" value="$path" placeholder="例如: C:\\Users\\18766\\Music\\HCJ-ZENBU" />

          <label for="path_candidates">自动发现目录</label>
          <div>
            <div class="pickline">
              <button id="scan_btn" type="button">扫描常用目录</button>
              <span id="scan_status"></span>
            </div>
            <div class="row">
              <select id="path_candidates">
                <option value="">扫描后在这里选目录...</option>
              </select>
            </div>
          </div>
        </div>

        <div class="row checks">
          <label><input type="checkbox" name="recursive" $recursive /> 递归子目录</label>
          <label><input type="checkbox" name="dry_run" $dry_run /> 仅预览 (Dry Run)</label>
          <label><input type="checkbox" name="do_rename" $do_rename /> 修复文件名</label>
          <label><input type="checkbox" name="do_tags" $do_tags /> 修复 ID3 标签</label>
          <label><input type="checkbox" name="verbose" $verbose /> 详细日志</label>
        </div>

        <div class="grid">
          <label for="genre">设置 Genre (可留空)</label>
          <input id="genre" name="genre" type="text" value="$genre" placeholder="例如: J-Pop" />

          <label for="genre_mode">Genre 模式</label>
          <select id="genre_mode" name="genre_mode">
            <option value="fill" $mode_fill>fill (只填空)</option>
            <option value="overwrite" $mode_overwrite>overwrite (全部覆盖)</option>
            <option value="merge" $mode_merge>merge (合并)</option>
          </select>
        </div>

        <div class="row">
          <button type="submit">开始处理</button>
        </div>

        <div class="hint">
          新方案：不依赖浏览器目录选择，直接由服务端扫描常用目录并给你可选绝对路径。
          如果列表里没有你的目录，再手填一次完整路径即可。
        </div>
      </form>
    </div>

    $summary_block
    $error_block
    $logs_block
  </div>

  <script>
    (function () {
      var scanBtn = document.getElementById('scan_btn');
      var scanStatus = document.getElementById('scan_status');
      var candidates = document.getElementById('path_candidates');
      var pathInput = document.getElementById('path');

      if (candidates) {
        candidates.addEventListener('change', function () {
          if (candidates.value) {
            pathInput.value = candidates.value;
          }
        });
      }

      if (scanBtn) {
        scanBtn.addEventListener('click', function () {
          scanBtn.disabled = true;
          scanStatus.textContent = '扫描中，请稍等...';

          fetch('/scan-paths', { method: 'POST' })
            .then(function (res) { return res.json(); })
            .then(function (data) {
              if (!data || !data.ok) {
                throw new Error((data && data.error) ? data.error : '扫描失败');
              }

              while (candidates.options.length > 0) {
                candidates.remove(0);
              }

              var placeholder = document.createElement('option');
              placeholder.value = '';
              placeholder.textContent = '请选择目录...';
              candidates.appendChild(placeholder);

              var items = data.items || [];
              for (var i = 0; i < items.length; i++) {
                var item = items[i];
                var opt = document.createElement('option');
                opt.value = item.path;
                opt.textContent = item.path + '  (mp3: ' + item.count + ')';
                candidates.appendChild(opt);
              }

              scanStatus.textContent = '已发现 ' + items.length + ' 个目录';
            })
            .catch(function (err) {
              scanStatus.textContent = '';
              alert(err && err.message ? err.message : '扫描失败');
            })
            .then(function () {
              scanBtn.disabled = false;
            });
        });
      }
    })();
  </script>
</body>
</html>
"""
    )

    return tpl.safe_substitute(
        version=escape(APP_VERSION),
        path=escape(str(state.get("path", ""))),
        recursive=checked(bool(state.get("recursive", False))),
        dry_run=checked(bool(state.get("dry_run", False))),
        do_rename=checked(bool(state.get("do_rename", False))),
        do_tags=checked(bool(state.get("do_tags", False))),
        verbose=checked(bool(state.get("verbose", False))),
        genre=escape(str(state.get("genre", ""))),
        mode_fill=selected("fill", str(state.get("genre_mode", "fill"))),
        mode_overwrite=selected("overwrite", str(state.get("genre_mode", "fill"))),
        mode_merge=selected("merge", str(state.get("genre_mode", "fill"))),
        summary_block=('<div class="card summary">{0}</div>'.format(escape(summary)) if summary else ""),
        error_block=('<div class="card error">{0}</div>'.format(escape(error)) if error else ""),
        logs_block=('<div class="card"><h3>日志</h3><pre>{0}</pre></div>'.format(escape(logs_text)) if logs else ""),
    )


def parse_state(raw: Dict[str, List[str]]) -> Dict[str, object]:
    state = default_state()
    state["path"] = (raw.get("path", [""])[0]).strip()
    state["recursive"] = "recursive" in raw
    state["dry_run"] = "dry_run" in raw
    state["do_rename"] = "do_rename" in raw
    state["do_tags"] = "do_tags" in raw
    state["verbose"] = "verbose" in raw
    state["genre"] = (raw.get("genre", [""])[0]).strip()

    mode = raw.get("genre_mode", ["fill"])[0].strip().lower()
    state["genre_mode"] = mode if mode in ("fill", "overwrite", "merge") else "fill"
    return state


def resolve_target_path(state: Dict[str, object]) -> Path:
    path_text = str(state.get("path", "")).strip()
    if not path_text:
        raise ValueError("请先输入路径，或先点“扫描常用目录”再选择。")

    target = Path(path_text).expanduser().resolve()
    if not target.exists():
        raise ValueError("路径不存在: {0}".format(target))
    return target


def process_files(state: Dict[str, object]) -> Tuple[str, List[str]]:
    target = resolve_target_path(state)

    do_rename = bool(state.get("do_rename", False))
    do_tags = bool(state.get("do_tags", False))
    if not do_rename and not do_tags:
        raise ValueError("至少勾选一个处理项：修复文件名 / 修复 ID3 标签。")

    recursive = bool(state.get("recursive", True))
    dry_run = bool(state.get("dry_run", True))
    verbose = bool(state.get("verbose", True))
    genre_text = str(state.get("genre", "")).strip()
    genre_value = genre_text if genre_text else None
    genre_mode = str(state.get("genre_mode", "fill"))

    files = list(iter_mp3_files(target, recursive))
    if not files:
        raise ValueError("没有找到 mp3 文件。")

    logs: List[str] = ["[TARGET] {0}".format(target)]
    total = 0
    renamed = 0
    tagged = 0
    errors = 0

    for index, file in enumerate(files, start=1):
        total += 1
        current_file = file

        if verbose:
            logs.append("[{0}/{1}] {2}".format(index, len(files), file))

        if do_rename:
            ok, rename_log, new_path = fix_filename_web(file, dry_run)
            if rename_log and (ok or verbose):
                logs.append("[{0}] {1}".format(file, rename_log))
            if ok:
                renamed += 1
                if not dry_run and new_path is not None:
                    current_file = new_path
            elif rename_log.startswith("skip"):
                errors += 1

        if do_tags:
            changed, tag_logs = fix_tags(current_file, dry_run, genre_value, genre_mode)
            if changed:
                tagged += 1
                if verbose:
                    for line in tag_logs:
                        logs.append("[{0}] {1}".format(current_file, line))
            elif tag_logs:
                if verbose:
                    for line in tag_logs:
                        logs.append("[{0}] {1}".format(current_file, line))
                for line in tag_logs:
                    if "tag error:" in line:
                        errors += 1

    mode = "DRY RUN" if dry_run else "APPLIED"
    summary = "[{0}] scanned={1}, renamed={2}, tag_updated={3}, errors={4}".format(
        mode, total, renamed, tagged, errors
    )
    return summary, logs


class AppHandler(BaseHTTPRequestHandler):
    def _send_html(self, page: str, status: int = 200) -> None:
        body = page.encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _send_json(self, payload: Dict[str, object], status: int = 200) -> None:
        body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self) -> None:
        parsed = urlparse(self.path)
        if parsed.path != "/":
            self._send_html("<h1>404 Not Found</h1>", status=404)
            return
        self._send_html(render_page(default_state()))

    def do_POST(self) -> None:
        parsed = urlparse(self.path)

        if parsed.path == "/scan-paths":
            try:
                items = scan_mp3_directories()
                self._send_json({"ok": True, "items": items})
            except Exception as exc:
                self._send_json({"ok": False, "error": str(exc)}, status=500)
            return

        if parsed.path != "/run":
            self._send_html("<h1>404 Not Found</h1>", status=404)
            return

        length = int(self.headers.get("Content-Length", "0"))
        raw_body = self.rfile.read(length).decode("utf-8", errors="replace")
        raw_form = parse_qs(raw_body, keep_blank_values=True)
        state = parse_state(raw_form)

        try:
            summary, logs = process_files(state)
            page = render_page(state, summary=summary, logs=logs, error="")
        except Exception as exc:
            err_text = "{0}\n\n{1}".format(str(exc), traceback.format_exc())
            page = render_page(state, summary="", logs=[], error=err_text)
        self._send_html(page)

    def log_message(self, fmt: str, *args: object) -> None:
        return


def main() -> int:
    parser = argparse.ArgumentParser(description="MusicTagFixer local Web GUI")
    parser.add_argument("--host", default="127.0.0.1", help="Bind host, default 127.0.0.1")
    parser.add_argument("--port", type=int, default=8765, help="Bind port, default 8765")
    parser.add_argument("--open-browser", action="store_true", help="Open browser automatically")
    args = parser.parse_args()

    server = ThreadingHTTPServer((args.host, args.port), AppHandler)
    url = "http://{0}:{1}/".format(args.host, args.port)
    print("MusicTagFixer Web GUI running at: {0} (v{1})".format(url, APP_VERSION))
    print("Press Ctrl+C to stop.")

    if args.open_browser:
        try:
            webbrowser.open(url)
        except Exception:
            pass

    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())




















