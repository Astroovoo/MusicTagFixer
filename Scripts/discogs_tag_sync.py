#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import os
import random
import re
import sys
import time
import webbrowser
from dataclasses import dataclass
from pathlib import Path
from typing import Dict, Iterable, List, Optional, Sequence, Tuple

import requests
from mutagen.id3 import (
    ID3,
    ID3NoHeaderError,
    TALB,
    TCOM,
    TCON,
    TPE1,
    TPE2,
    TRCK,
    TPOS,
    TIT2,
    TYER,
)

try:
    from requests_oauthlib import OAuth1Session
except ImportError:
    print(
        "Missing dependency: requests_oauthlib. Install with: pip install requests_oauthlib",
        file=sys.stderr,
    )
    sys.exit(2)


API_BASE = "https://api.discogs.com"
REQUEST_TOKEN_URL = API_BASE + "/oauth/request_token"
ACCESS_TOKEN_URL = API_BASE + "/oauth/access_token"
AUTHORIZE_URL = "https://www.discogs.com/oauth/authorize"

RETRYABLE_STATUS = {408, 425, 429, 500, 502, 503, 504}
COMPOSER_ROLES = {"composed by", "composer", "written-by", "written by", "lyrics by"}


@dataclass
class LocalTrackInfo:
    path: Path
    title: str
    artist: str
    album: str
    track_number: Optional[int]


@dataclass
class DiscogsTrackMatch:
    release_id: int
    release_title: str
    release_year: Optional[int]
    release_artists: List[str]
    track_title: str
    track_position: str
    track_number: Optional[int]
    disc_number: Optional[int]
    genres: List[str]
    composers: List[str]


class DiscogsError(RuntimeError):
    pass


class DiscogsOAuthClient:
    def __init__(
        self,
        consumer_key: str,
        consumer_secret: str,
        user_agent: str,
        timeout: Tuple[float, float],
        retries: int,
        backoff_seconds: float,
    ) -> None:
        self.consumer_key = consumer_key
        self.consumer_secret = consumer_secret
        self.user_agent = user_agent
        self.timeout = timeout
        self.retries = retries
        self.backoff_seconds = backoff_seconds

        self._access_token: Optional[str] = None
        self._access_token_secret: Optional[str] = None
        self._session: Optional[OAuth1Session] = None

    def _new_session(
        self,
        token: Optional[str] = None,
        token_secret: Optional[str] = None,
    ) -> OAuth1Session:
        kwargs = {
            "client_key": self.consumer_key,
            "client_secret": self.consumer_secret,
        }
        if token:
            kwargs["resource_owner_key"] = token
        if token_secret:
            kwargs["resource_owner_secret"] = token_secret
        return OAuth1Session(**kwargs)

    def _sleep_backoff(self, attempt: int, retry_after: Optional[float] = None) -> None:
        if retry_after is not None and retry_after > 0:
            time.sleep(retry_after)
            return
        base = self.backoff_seconds * (2 ** (attempt - 1))
        jitter = random.uniform(0.0, 0.35)
        time.sleep(base + jitter)

    def _perform_with_retry(self, op_name: str, call) -> Dict[str, object]:
        last_exc: Optional[Exception] = None
        for attempt in range(1, self.retries + 1):
            try:
                return call()
            except requests.Timeout as exc:
                last_exc = exc
            except requests.ConnectionError as exc:
                last_exc = exc
            except ValueError as exc:
                # oauthlib may raise ValueError for transient fetch token issues.
                last_exc = exc

            if attempt < self.retries:
                self._sleep_backoff(attempt)

        raise DiscogsError("{0} failed after retries: {1}".format(op_name, last_exc))

    def _ensure_session(self) -> OAuth1Session:
        if self._session is None:
            if not self._access_token or not self._access_token_secret:
                raise DiscogsError("OAuth session is not initialized.")
            self._session = self._new_session(self._access_token, self._access_token_secret)
            self._session.headers.update({"User-Agent": self.user_agent})
        return self._session

    def _request_json(self, method: str, endpoint: str, params: Optional[Dict[str, str]] = None) -> Dict[str, object]:
        session = self._ensure_session()
        url = endpoint if endpoint.startswith("http") else API_BASE + endpoint

        last_error: Optional[str] = None
        for attempt in range(1, self.retries + 1):
            try:
                response = session.request(method, url, params=params, timeout=self.timeout)
            except (requests.Timeout, requests.ConnectionError) as exc:
                last_error = str(exc)
                if attempt < self.retries:
                    self._sleep_backoff(attempt)
                    continue
                break

            if response.status_code in RETRYABLE_STATUS and attempt < self.retries:
                retry_after = None
                if response.status_code == 429:
                    header = response.headers.get("Retry-After")
                    if header:
                        try:
                            retry_after = float(header)
                        except Exception:
                            retry_after = None
                self._sleep_backoff(attempt, retry_after)
                continue

            if response.status_code >= 400:
                text = response.text.strip()[:500]
                raise DiscogsError(
                    "Discogs API {0} {1} failed: HTTP {2} {3}".format(
                        method, endpoint, response.status_code, text
                    )
                )

            try:
                return response.json()
            except Exception as exc:
                raise DiscogsError("Invalid JSON from Discogs: {0}".format(exc))

        raise DiscogsError("Discogs API request failed after retries: {0}".format(last_error or endpoint))

    def _load_cached_token(self, token_file: Path) -> Optional[Tuple[str, str]]:
        if not token_file.exists():
            return None
        try:
            data = json.loads(token_file.read_text(encoding="utf-8"))
            token = str(data.get("oauth_token", "")).strip()
            secret = str(data.get("oauth_token_secret", "")).strip()
            if token and secret:
                return token, secret
            return None
        except Exception:
            return None

    def _save_cached_token(self, token_file: Path, token: str, secret: str) -> None:
        token_file.parent.mkdir(parents=True, exist_ok=True)
        payload = {
            "oauth_token": token,
            "oauth_token_secret": secret,
            "saved_at": int(time.time()),
        }
        token_file.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")

    def _validate_token(self) -> bool:
        try:
            data = self._request_json("GET", "/oauth/identity")
            return bool(data.get("id"))
        except Exception:
            return False

    def authorize(
        self,
        token_file: Path,
        force_reauth: bool,
        open_browser: bool,
    ) -> None:
        if not force_reauth:
            cached = self._load_cached_token(token_file)
            if cached:
                self._access_token, self._access_token_secret = cached
                self._session = None
                if self._validate_token():
                    return

        request_session = self._new_session()

        request_token = self._perform_with_retry(
            "request token",
            lambda: request_session.fetch_request_token(REQUEST_TOKEN_URL, timeout=self.timeout),
        )

        owner_token = request_token.get("oauth_token")
        owner_secret = request_token.get("oauth_token_secret")
        if not owner_token or not owner_secret:
            raise DiscogsError("Failed to obtain request token from Discogs.")

        auth_url = request_session.authorization_url(AUTHORIZE_URL)
        print("\nAuthorize this app in browser:")
        print(auth_url)
        if open_browser:
            try:
                webbrowser.open(auth_url)
            except Exception:
                pass

        verifier = input("Enter Discogs verifier/PIN: ").strip()
        if not verifier:
            raise DiscogsError("Verifier is required for OAuth authorization.")

        access_session = self._new_session(owner_token, owner_secret)
        access_token = self._perform_with_retry(
            "access token",
            lambda: access_session.fetch_access_token(
                ACCESS_TOKEN_URL,
                verifier=verifier,
                timeout=self.timeout,
            ),
        )

        token = str(access_token.get("oauth_token", "")).strip()
        secret = str(access_token.get("oauth_token_secret", "")).strip()
        if not token or not secret:
            raise DiscogsError("Failed to obtain access token from Discogs.")

        self._access_token = token
        self._access_token_secret = secret
        self._session = None

        if not self._validate_token():
            raise DiscogsError("OAuth token validation failed (identity API check).")

        self._save_cached_token(token_file, token, secret)

    def get_json(self, endpoint: str, params: Optional[Dict[str, str]] = None) -> Dict[str, object]:
        return self._request_json("GET", endpoint, params=params)


def normalize_name(name: str) -> str:
    out = re.sub(r"\s*\(\d+\)$", "", (name or "").strip())
    return out.strip()


def normalize_text(text: str) -> str:
    text = (text or "").lower()
    text = re.sub(r"\([^)]*\)", " ", text)
    text = re.sub(r"[^0-9a-z\u3040-\u30ff\u3400-\u9fff]+", " ", text)
    text = re.sub(r"\s+", " ", text).strip()
    return text


def similarity(a: str, b: str) -> float:
    a_norm = normalize_text(a)
    b_norm = normalize_text(b)
    if not a_norm or not b_norm:
        return 0.0
    if a_norm == b_norm:
        return 1.0

    # simple token overlap + prefix bonus
    a_tokens = set(a_norm.split())
    b_tokens = set(b_norm.split())
    overlap = len(a_tokens & b_tokens)
    union = len(a_tokens | b_tokens) or 1
    jaccard = overlap / float(union)

    prefix = 0.0
    if a_norm.startswith(b_norm) or b_norm.startswith(a_norm):
        prefix = 0.25
    return min(1.0, jaccard + prefix)


def parse_track_number(text: str) -> Optional[int]:
    if not text:
        return None
    m = re.search(r"(\d+)", text)
    if not m:
        return None
    try:
        return int(m.group(1))
    except Exception:
        return None


def parse_position(position: str) -> Tuple[Optional[int], Optional[int]]:
    pos = (position or "").strip()
    if not pos:
        return None, None

    m = re.match(r"^(\d+)-(\d+)$", pos)
    if m:
        return int(m.group(2)), int(m.group(1))

    m = re.match(r"^[A-Za-z](\d+)$", pos)
    if m:
        return int(m.group(1)), None

    m = re.match(r"^(\d+)$", pos)
    if m:
        return int(m.group(1)), None

    m = re.search(r"(\d+)", pos)
    if m:
        return int(m.group(1)), None

    return None, None


def iter_release_tracks(release: Dict[str, object]) -> Iterable[Dict[str, object]]:
    tracklist = release.get("tracklist", []) if isinstance(release, dict) else []
    if not isinstance(tracklist, list):
        return

    for item in tracklist:
        if not isinstance(item, dict):
            continue
        item_type = str(item.get("type_", "track")).lower()
        if item_type == "heading":
            continue
        if str(item.get("title", "")).strip():
            yield item

        sub_tracks = item.get("sub_tracks", [])
        if isinstance(sub_tracks, list):
            for sub in sub_tracks:
                if isinstance(sub, dict) and str(sub.get("title", "")).strip():
                    if "position" not in sub or not str(sub.get("position", "")).strip():
                        sub = dict(sub)
                        sub["position"] = item.get("position", "")
                    yield sub


def unique_keep_order(values: Sequence[str]) -> List[str]:
    seen = set()
    out: List[str] = []
    for v in values:
        t = (v or "").strip()
        if not t:
            continue
        key = t.casefold()
        if key in seen:
            continue
        seen.add(key)
        out.append(t)
    return out


def get_artist_names(artists_obj: object) -> List[str]:
    if not isinstance(artists_obj, list):
        return []
    names: List[str] = []
    for item in artists_obj:
        if not isinstance(item, dict):
            continue
        name = normalize_name(str(item.get("name", "")))
        if name:
            names.append(name)
    return unique_keep_order(names)


def collect_composers(release: Dict[str, object], track: Dict[str, object]) -> List[str]:
    names: List[str] = []

    def collect(extra_obj: object) -> None:
        if not isinstance(extra_obj, list):
            return
        for item in extra_obj:
            if not isinstance(item, dict):
                continue
            role = str(item.get("role", "")).strip().lower()
            if not role:
                continue
            if role in COMPOSER_ROLES or any(r in role for r in COMPOSER_ROLES):
                name = normalize_name(str(item.get("name", "")))
                if name:
                    names.append(name)

    collect(track.get("extraartists"))
    collect(release.get("extraartists"))
    return unique_keep_order(names)


def read_local_track_info(path: Path) -> LocalTrackInfo:
    title = path.stem
    artist = ""
    album = ""
    track_no = None

    try:
        tags = ID3(str(path))
    except ID3NoHeaderError:
        return LocalTrackInfo(path=path, title=title, artist=artist, album=album, track_number=track_no)
    except Exception:
        return LocalTrackInfo(path=path, title=title, artist=artist, album=album, track_number=track_no)

    frame = tags.get("TIT2")
    if frame and frame.text:
        title = str(frame.text[0]).strip() or title

    frame = tags.get("TPE1")
    if frame and frame.text:
        artist = str(frame.text[0]).strip()

    frame = tags.get("TALB")
    if frame and frame.text:
        album = str(frame.text[0]).strip()

    frame = tags.get("TRCK")
    if frame and frame.text:
        track_no = parse_track_number(str(frame.text[0]))

    return LocalTrackInfo(path=path, title=title, artist=artist, album=album, track_number=track_no)


def get_release_by_id(client: DiscogsOAuthClient, release_id: int) -> Dict[str, object]:
    return client.get_json("/releases/{0}".format(release_id))


def search_releases(client: DiscogsOAuthClient, local: LocalTrackInfo, per_page: int) -> List[int]:
    queries: List[Dict[str, str]] = []

    title = (local.title or "").strip()
    artist = (local.artist or "").strip()
    album = (local.album or "").strip()

    if artist and title:
        queries.append({"artist": artist, "track": title})
    if artist and album:
        queries.append({"artist": artist, "release_title": album})
    if title:
        queries.append({"track": title})
    if album:
        queries.append({"release_title": album})

    release_ids: List[int] = []
    seen = set()

    for q in queries:
        params = dict(q)
        params["type"] = "release"
        params["per_page"] = str(per_page)
        data = client.get_json("/database/search", params=params)
        results = data.get("results", []) if isinstance(data, dict) else []
        if not isinstance(results, list):
            continue

        for item in results:
            if not isinstance(item, dict):
                continue
            rid = item.get("id")
            try:
                rid_int = int(rid)
            except Exception:
                continue
            if rid_int in seen:
                continue
            seen.add(rid_int)
            release_ids.append(rid_int)

        if len(release_ids) >= per_page:
            break

    return release_ids[:per_page]


def score_track_match(local: LocalTrackInfo, release: Dict[str, object], track: Dict[str, object]) -> float:
    local_title = local.title or ""
    local_artist = local.artist or ""

    release_artists = " / ".join(get_artist_names(release.get("artists")))
    track_artists = " / ".join(get_artist_names(track.get("artists")))
    track_title = str(track.get("title", "")).strip()

    title_score = similarity(local_title, track_title)
    artist_target = track_artists or release_artists
    artist_score = similarity(local_artist, artist_target)

    score = title_score * 0.76 + artist_score * 0.22

    track_no, _ = parse_position(str(track.get("position", "")))
    if local.track_number is not None and track_no is not None:
        if local.track_number == track_no:
            score += 0.25
        else:
            score -= 0.10

    return score


def pick_best_match(
    local: LocalTrackInfo,
    releases: Sequence[Dict[str, object]],
) -> Optional[DiscogsTrackMatch]:
    best: Optional[DiscogsTrackMatch] = None
    best_score = -1.0

    for release in releases:
        if not isinstance(release, dict):
            continue

        release_id = int(release.get("id", 0) or 0)
        release_title = str(release.get("title", "")).strip()
        year = release.get("year")
        release_year: Optional[int]
        try:
            release_year = int(year) if year else None
        except Exception:
            release_year = None

        release_artists = get_artist_names(release.get("artists"))

        genres = []
        if isinstance(release.get("genres"), list):
            genres.extend(str(x).strip() for x in release.get("genres", []) if str(x).strip())
        if isinstance(release.get("styles"), list):
            genres.extend(str(x).strip() for x in release.get("styles", []) if str(x).strip())
        genres = unique_keep_order(genres)

        for track in iter_release_tracks(release):
            score = score_track_match(local, release, track)
            track_title = str(track.get("title", "")).strip()
            if not track_title:
                continue

            # Ignore obviously wrong track matches.
            if score < 0.30:
                continue

            pos = str(track.get("position", "")).strip()
            track_no, disc_no = parse_position(pos)
            composers = collect_composers(release, track)

            candidate = DiscogsTrackMatch(
                release_id=release_id,
                release_title=release_title,
                release_year=release_year,
                release_artists=release_artists,
                track_title=track_title,
                track_position=pos,
                track_number=track_no,
                disc_number=disc_no,
                genres=genres,
                composers=composers,
            )

            if score > best_score:
                best = candidate
                best_score = score

    return best


def set_text_frame(tags: ID3, frame_id: str, frame_cls, values: Sequence[str]) -> bool:
    cleaned = [str(v).strip() for v in values if str(v).strip()]
    cleaned = unique_keep_order(cleaned)
    if not cleaned:
        return False

    old = []
    old_frame = tags.get(frame_id)
    if old_frame is not None and hasattr(old_frame, "text"):
        old = [str(v) for v in old_frame.text]

    if old == cleaned:
        return False

    tags.setall(frame_id, [frame_cls(encoding=1, text=cleaned)])
    return True


def apply_discogs_tags(
    path: Path,
    match: DiscogsTrackMatch,
    dry_run: bool,
) -> Tuple[bool, List[str]]:
    changed = False
    logs: List[str] = []

    try:
        tags = ID3(str(path))
    except ID3NoHeaderError:
        tags = ID3()
    except Exception as exc:
        # For existing files, fallback to creating new ID3 in-memory.
        if path.exists():
            tags = ID3()
            logs.append("tag warn: {0}; fallback to new ID3".format(exc))
        else:
            return False, ["tag error: {0}".format(exc)]

    if set_text_frame(tags, "TIT2", TIT2, [match.track_title]):
        changed = True
        logs.append("TIT2 -> {0}".format(match.track_title))

    if match.release_artists:
        if set_text_frame(tags, "TPE1", TPE1, match.release_artists):
            changed = True
            logs.append("TPE1 -> {0}".format(match.release_artists))
        if set_text_frame(tags, "TPE2", TPE2, [" / ".join(match.release_artists)]):
            changed = True
            logs.append("TPE2 -> {0}".format(" / ".join(match.release_artists)))

    if match.release_title and set_text_frame(tags, "TALB", TALB, [match.release_title]):
        changed = True
        logs.append("TALB -> {0}".format(match.release_title))

    if match.release_year:
        year_text = str(match.release_year)
        if set_text_frame(tags, "TYER", TYER, [year_text]):
            changed = True
            logs.append("TYER -> {0}".format(year_text))

    if match.track_number:
        trck_text = str(match.track_number)
        if set_text_frame(tags, "TRCK", TRCK, [trck_text]):
            changed = True
            logs.append("TRCK -> {0}".format(trck_text))

    if match.disc_number:
        tpos_text = str(match.disc_number)
        if set_text_frame(tags, "TPOS", TPOS, [tpos_text]):
            changed = True
            logs.append("TPOS -> {0}".format(tpos_text))

    if match.genres and set_text_frame(tags, "TCON", TCON, match.genres):
        changed = True
        logs.append("TCON -> {0}".format(match.genres))

    if match.composers and set_text_frame(tags, "TCOM", TCOM, match.composers):
        changed = True
        logs.append("TCOM -> {0}".format(match.composers))

    if changed and not dry_run:
        tags.save(str(path), v2_version=3)

    return changed, logs


def iter_mp3_files(root: Path, recursive: bool) -> Iterable[Path]:
    if root.is_file() and root.suffix.lower() == ".mp3":
        yield root
        return
    pattern = "**/*.mp3" if recursive else "*.mp3"
    yield from root.glob(pattern)


def resolve_consumer_creds(args: argparse.Namespace) -> Tuple[str, str]:
    key = (args.discogs_consumer_key or os.environ.get("DISCOGS_CONSUMER_KEY", "")).strip()
    secret = (args.discogs_consumer_secret or os.environ.get("DISCOGS_CONSUMER_SECRET", "")).strip()
    if not key or not secret:
        raise DiscogsError(
            "Discogs consumer key/secret required. "
            "Use --discogs-consumer-key/--discogs-consumer-secret or env DISCOGS_CONSUMER_KEY/DISCOGS_CONSUMER_SECRET."
        )
    return key, secret


def run_sync(args: argparse.Namespace) -> int:
    root = Path(args.path).expanduser().resolve()
    if not root.exists():
        print("path not found: {0}".format(root), file=sys.stderr)
        return 1

    key, secret = resolve_consumer_creds(args)
    token_file = Path(args.oauth_token_file).expanduser().resolve()

    client = DiscogsOAuthClient(
        consumer_key=key,
        consumer_secret=secret,
        user_agent=args.user_agent,
        timeout=(args.connect_timeout, args.read_timeout),
        retries=args.http_retries,
        backoff_seconds=args.retry_backoff,
    )

    client.authorize(token_file=token_file, force_reauth=args.force_reauth, open_browser=not args.no_browser)

    if args.auth_only:
        print("OAuth success. Token cached at: {0}".format(token_file))
        return 0

    recursive = not args.no_recursive
    files = list(iter_mp3_files(root, recursive))
    if not files:
        print("No mp3 files found.")
        return 0

    scanned = 0
    matched = 0
    updated = 0
    errors = 0

    for file in files:
        scanned += 1
        local = read_local_track_info(file)

        try:
            release_ids = search_releases(client, local, per_page=args.search_limit)
            releases = [get_release_by_id(client, rid) for rid in release_ids[: args.release_fetch_limit]]
            match = pick_best_match(local, releases)
        except Exception as exc:
            errors += 1
            print("[{0}] search error: {1}".format(file, exc))
            continue

        if not match:
            if args.verbose:
                print("[{0}] no good Discogs match".format(file))
            continue

        matched += 1
        changed, logs = apply_discogs_tags(file, match, dry_run=args.dry_run)
        if changed:
            updated += 1

        if args.verbose:
            print(
                "[{0}] match release={1} track={2}".format(
                    file,
                    match.release_title,
                    match.track_title,
                )
            )
            for line in logs:
                print("[{0}] {1}".format(file, line))

        if args.request_interval > 0:
            time.sleep(args.request_interval)

    mode = "DRY RUN" if args.dry_run else "APPLIED"
    print(
        "[{0}] scanned={1}, matched={2}, updated={3}, errors={4}".format(
            mode,
            scanned,
            matched,
            updated,
            errors,
        )
    )
    return 0


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        description="Update MP3 tags from Discogs (OAuth) with retry and timeout-hardening."
    )
    p.add_argument("path", nargs="?", default=".", help="Root directory or single mp3 file")
    p.add_argument("--no-recursive", action="store_true", help="Scan only top directory")
    p.add_argument("--dry-run", action="store_true", help="Preview changes without writing")
    p.add_argument("--verbose", action="store_true", help="Verbose per-file logs")

    p.add_argument("--discogs-consumer-key", help="Discogs app consumer key")
    p.add_argument("--discogs-consumer-secret", help="Discogs app consumer secret")
    p.add_argument(
        "--oauth-token-file",
        default=str(Path(".discogs_oauth_token.json")),
        help="Path to cache OAuth access token",
    )
    p.add_argument("--force-reauth", action="store_true", help="Ignore cached token and re-auth")
    p.add_argument("--no-browser", action="store_true", help="Do not auto-open browser during OAuth")
    p.add_argument("--auth-only", action="store_true", help="Only complete OAuth and exit")

    p.add_argument("--connect-timeout", type=float, default=6.0, help="HTTP connect timeout seconds")
    p.add_argument("--read-timeout", type=float, default=20.0, help="HTTP read timeout seconds")
    p.add_argument("--http-retries", type=int, default=5, help="Retry attempts for HTTP/OAuth")
    p.add_argument("--retry-backoff", type=float, default=0.9, help="Retry backoff base seconds")
    p.add_argument("--request-interval", type=float, default=0.2, help="Delay between files seconds")

    p.add_argument("--search-limit", type=int, default=12, help="Max release IDs to collect from search")
    p.add_argument("--release-fetch-limit", type=int, default=6, help="Max release details to fetch per file")

    p.add_argument(
        "--user-agent",
        default="MusicTagFixer/1.0 (+https://github.com/Astroovoo/MusicTagFixer)",
        help="Discogs API User-Agent",
    )
    return p


def main() -> int:
    parser = build_parser()
    args = parser.parse_args()

    try:
        return run_sync(args)
    except DiscogsError as exc:
        print("error: {0}".format(exc), file=sys.stderr)
        return 2
    except KeyboardInterrupt:
        print("interrupted", file=sys.stderr)
        return 130


if __name__ == "__main__":
    raise SystemExit(main())

