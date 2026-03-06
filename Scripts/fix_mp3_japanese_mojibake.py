#!/usr/bin/env python3
from __future__ import annotations

import argparse
import os
import re
import sys
from pathlib import Path
from typing import Iterable, List, Optional, Tuple

try:
    from mutagen.id3 import ID3, ID3NoHeaderError, TCON
except ImportError:
    print("Missing dependency: mutagen. Install with: pip install mutagen", file=sys.stderr)
    sys.exit(2)


SUSPICIOUS_CHARS = set(
    "ÃÂãâ¢£¤¥¦§¨©ª«¬®¯°±²³´µ¶·¸¹º»¼½¾¿ÐÑÒÓÔÕÖ×ØÙÚÛÜÝÞßàáäåæçèéêëìíîïðñòóôõö÷øùúûüýþÿ�"
)
FULL_JP_RE = re.compile(r"[\u3040-\u30ff\u3400-\u9fff]")
HALF_KATA_RE = re.compile(r"[\uff66-\uff9f]")


def count_suspicious(text: str) -> int:
    return sum(1 for ch in text if ch in SUSPICIOUS_CHARS or ord(ch) < 32 and ch not in "\t\n\r")


def count_full_japanese(text: str) -> int:
    return len(FULL_JP_RE.findall(text))


def count_half_katakana(text: str) -> int:
    return len(HALF_KATA_RE.findall(text))


def quality_key(text: str) -> Tuple[int, int, int, int]:
    # Lower is better:
    # 1) fewer obvious mojibake chars
    # 2) prefer strings that contain full Japanese characters
    # 3) strongly penalize half-width katakana noise
    # 4) prefer more full Japanese characters
    suspicious = count_suspicious(text)
    full_jp = count_full_japanese(text)
    half_kata = count_half_katakana(text)
    no_full_jp_penalty = 0 if full_jp > 0 else 1
    return (suspicious, no_full_jp_penalty, half_kata, -full_jp)


def transform_once(text: str) -> List[str]:
    if not text:
        return [text]

    candidates = {text}
    encode_list = ["latin1", "cp1252", "cp936", "gbk", "shift_jis", "cp932"]
    decode_list = ["utf-8", "shift_jis", "cp932", "euc_jp"]

    for src in encode_list:
        try:
            raw = text.encode(src)
        except Exception:
            continue
        for dst in decode_list:
            if src == dst:
                continue
            try:
                candidates.add(raw.decode(dst))
            except Exception:
                continue
    return list(candidates)


def fix_mojibake(text: str, max_rounds: int = 1) -> str:
    # Keep clean full-width Japanese text untouched.
    if count_full_japanese(text) > 0 and count_suspicious(text) == 0 and count_half_katakana(text) == 0:
        return text

    best = text
    for _ in range(max_rounds):
        changed = False
        for candidate in transform_once(best):
            if quality_key(candidate) < quality_key(best):
                best = candidate
                changed = True
        if not changed:
            break

    if quality_key(best) < quality_key(text):
        return best
    return text


def iter_mp3_files(root: Path, recursive: bool) -> Iterable[Path]:
    if root.is_file() and root.suffix.lower() == ".mp3":
        yield root
        return
    pattern = "**/*.mp3" if recursive else "*.mp3"
    yield from root.glob(pattern)


def fix_filename(path: Path, dry_run: bool) -> Tuple[bool, str, Optional[Path]]:
    new_stem = fix_mojibake(path.stem)
    if new_stem == path.stem:
        return False, "", None

    new_path = path.with_name(new_stem + path.suffix)
    if new_path.exists():
        return False, f"skip rename (target exists): {new_path.name}", None

    if not dry_run:
        os.rename(path, new_path)
    return True, f"rename: {path.name} -> {new_path.name}", new_path


def apply_genre(
    tags: ID3,
    genre_value: str,
    genre_mode: str,
) -> Tuple[bool, List[str]]:
    changed = False
    logs: List[str] = []
    frames = tags.getall("TCON")
    wanted = genre_value.strip()

    if not wanted:
        return False, logs

    if not frames:
        tags.add(TCON(encoding=1, text=[wanted]))
        return True, [f"TCON: [] -> [{wanted}]"]

    for frame in frames:
        old_values = list(frame.text)
        old_non_empty = [v.strip() for v in old_values if v and v.strip()]

        if genre_mode == "overwrite":
            new_values = [wanted]
        elif genre_mode == "merge":
            if any(v.casefold() == wanted.casefold() for v in old_non_empty):
                new_values = old_values
            else:
                new_values = old_non_empty + [wanted] if old_non_empty else [wanted]
        else:  # fill
            new_values = old_values if old_non_empty else [wanted]

        if new_values != old_values:
            frame.encoding = 1
            frame.text = new_values
            changed = True
            logs.append(f"TCON: {old_values} -> {new_values}")

    return changed, logs


def fix_tags(
    path: Path,
    dry_run: bool,
    genre_value: Optional[str],
    genre_mode: str,
) -> Tuple[bool, List[str]]:
    changed = False
    logs: List[str] = []

    try:
        tags = ID3(path)
    except ID3NoHeaderError:
        return False, []
    except Exception as exc:
        return False, [f"tag error: {exc}"]

    target_frames = ["TIT2", "TPE1", "TPE2", "TALB", "TCOM"]
    for frame_id in target_frames:
        for frame in tags.getall(frame_id):
            old_values = list(frame.text)
            new_values = [fix_mojibake(v) for v in old_values]
            if new_values != old_values:
                frame.encoding = 1
                frame.text = new_values
                changed = True
                logs.append(f"{frame_id}: {old_values} -> {new_values}")

    if genre_value is not None:
        g_changed, g_logs = apply_genre(tags, genre_value, genre_mode)
        if g_changed:
            changed = True
            logs.extend(g_logs)

    if changed and not dry_run:
        tags.save(v2_version=3)
    return changed, logs


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Batch-fix Japanese mojibake in MP3 filenames and ID3 tags."
    )
    parser.add_argument("path", nargs="?", default=".", help="Root directory containing mp3 files")
    parser.add_argument("--no-recursive", action="store_true", help="Scan only the top directory")
    parser.add_argument("--dry-run", action="store_true", help="Preview changes without writing")
    parser.add_argument("--no-rename", action="store_true", help="Do not modify file names")
    parser.add_argument("--no-tags", action="store_true", help="Do not modify ID3 tags")
    parser.add_argument("--set-genre", help="Batch set genre (TCON), e.g. J-Pop")
    parser.add_argument(
        "--genre-mode",
        choices=["fill", "overwrite", "merge"],
        default="fill",
        help="Genre write strategy: fill=only empty, overwrite=replace all, merge=append if missing",
    )
    parser.add_argument("--verbose", action="store_true", help="Print all per-file details")
    args = parser.parse_args()

    root = Path(args.path).resolve()
    if not root.exists():
        print(f"path not found: {root}", file=sys.stderr)
        return 1

    recursive = not args.no_recursive
    dry_run = args.dry_run

    total = 0
    renamed = 0
    tagged = 0
    errors = 0

    for file in iter_mp3_files(root, recursive):
        total += 1
        current_file = file

        if not args.no_rename:
            ok, rename_log, new_path = fix_filename(file, dry_run)
            if rename_log and (ok or args.verbose):
                print(f"[{file}] {rename_log}")
            if ok:
                renamed += 1
                if not dry_run and new_path is not None:
                    current_file = new_path
            elif rename_log.startswith("skip"):
                errors += 1

        if not args.no_tags:
            changed, tag_logs = fix_tags(
                current_file,
                dry_run,
                args.set_genre,
                args.genre_mode,
            )
            if changed:
                tagged += 1
                if args.verbose:
                    for line in tag_logs:
                        print(f"[{current_file}] {line}")
            elif args.verbose and tag_logs:
                for line in tag_logs:
                    print(f"[{current_file}] {line}")
                    errors += 1

    mode = "DRY RUN" if dry_run else "APPLIED"
    print(f"[{mode}] scanned={total}, renamed={renamed}, tag_updated={tagged}, errors={errors}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
