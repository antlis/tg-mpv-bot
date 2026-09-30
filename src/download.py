"""Download whatever is playing so the panel's 📥 button can upload it to Telegram.

Progress mirrors tg-media-bot: yt-dlp prints one ``PROG|`` line per update
(``--newline`` + ``--progress-template``), which is parsed and throttled so
status-message edits stay well under Telegram's rate limit. The upload itself
has no progress signal (the Bot API exposes no callback), so the caller shows
a heartbeat instead.
"""

from __future__ import annotations

import asyncio
import logging
import os
import re
import tempfile
from collections import deque
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from pathlib import Path

from . import player, playlists
from .config import Settings

logger = logging.getLogger(__name__)

_PROGRESS_PREFIX = "PROG|"
_PROGRESS_TEMPLATE = (
    "download:" + _PROGRESS_PREFIX
    + "%(progress._percent_str)s|%(progress._speed_str)s|%(progress._eta_str)s"
)
_PROGRESS_MIN_INTERVAL = 3.0  # seconds between progress callbacks
_PCT_RE = re.compile(r"([\d.]+)%")
_ANSI_RE = re.compile(r"\x1b\[[0-9;]*m")

# yt-dlp output markers meaning the download finished and it is now
# post-processing (merging streams) — there is no percentage for these.
_POSTPROCESS_MARKERS = ("[Merger]", "[VideoConvertor]", "Merging formats", "[Metadata]")

# Bot API upload ceilings: 50 MB on the cloud API, 2000 MB via a local server.
CLOUD_LIMIT = 50 * 1024 * 1024
LOCAL_LIMIT = 2000 * 1024 * 1024

# Containers Telegram plays inline; anything else goes out as a document.
VIDEO_EXTS = {".mp4", ".m4v", ".mov", ".webm"}


# Substring (lowercased) → friendly hint. First match wins.
_ERROR_HINTS = (
    ("video unavailable", "the video is no longer available at the source"),
    ("has been removed", "the video was removed at the source"),
    ("this video is private", "the video is private"),
    ("private video", "the video is private"),
    ("http error 404", "the page is gone (404) — the link may have been removed"),
    ("http error 410", "the page is gone (410) — the link was removed"),
    ("http error 403", "the site refused the download (403) — the link may have expired"),
    ("unsupported url", "yt-dlp can't download from that page"),
    ("sign in to confirm", "the site wants a logged-in session"),
    ("no space left", "the server is out of disk space"),
)


def friendly_error(reason: str) -> str:
    """Map a raw yt-dlp/OS error to a short, actionable sentence."""
    low = reason.lower()
    for needle, hint in _ERROR_HINTS:
        if needle in low:
            return hint
    return reason


class DownloadError(Exception):
    """Raised with a user-facing reason when a download can't complete."""


@dataclass
class Source:
    """What the upload button will send: an existing file or a URL to fetch.

    ``missing`` means we know which local file it was but it is gone now
    (deleted/moved, or the library disk isn't mounted); ``value`` is its path.
    """

    kind: str  # "file" | "url" | "missing"
    value: str


def upload_limit(settings: Settings) -> int:
    return LOCAL_LIMIT if settings.api_server_url else CLOUD_LIMIT


def render_progress_bar(percent: float | None, width: int = 10) -> str:
    """``█/░`` bar for a 0–100 percentage (None → empty bar)."""
    pct = 0.0 if percent is None else max(0.0, min(100.0, percent))
    filled = int(pct / 100 * width)
    return "█" * filled + "░" * (width - filled)


def parse_progress_line(line: str) -> dict | None:
    """Parse a ``PROG|pct|speed|eta`` line; None for anything else."""
    line = _ANSI_RE.sub("", line).strip()
    if not line.startswith(_PROGRESS_PREFIX):
        return None
    parts = line[len(_PROGRESS_PREFIX):].split("|")
    if len(parts) < 3:
        return None
    m = _PCT_RE.search(parts[0])
    return {
        "percent": float(m.group(1)) if m else None,
        "speed": parts[1].strip(),
        "eta": parts[2].strip(),
    }


def _history_source(target: str | None) -> Source | None:
    """Last-played target → source: a URL, or a playlist with exactly one entry
    (a movie). Multi-entry playlists are ambiguous once mpv has exited."""
    if not target:
        return None
    if target.startswith(("http://", "https://")):
        return Source("url", target)
    if target.lower().endswith((".m3u", ".m3u8")) and Path(target).is_file():
        entries = playlists.read_entries(Path(target))
        if len(entries) == 1:
            resolved = playlists._resolve(entries[0], Path(target).parent)
            if isinstance(resolved, Path):
                kind = "file" if resolved.is_file() else "missing"
                return Source(kind, str(resolved))
            return Source("url", resolved)
    return None


def resolve_source(mpv_path: str | None, history_target: str | None) -> Source | None:
    """Decide what to upload from mpv's ``path`` and the last-played history.

    A local file mpv is playing wins (library playback). Otherwise the last
    thing launched — the URL the user sent, or a one-file playlist — is used:
    for streams mpv's own path is a minted CDN URL, ``fd://`` or a temp
    playlist that yt-dlp can't reuse, and once mpv has exited (movie watched
    to the end) history is all that's left.
    """
    if mpv_path and "://" not in mpv_path and Path(mpv_path).is_file():
        return Source("file", mpv_path)
    from_history = _history_source(history_target)
    if from_history is not None:
        return from_history
    if mpv_path and mpv_path.startswith(("http://", "https://")):
        return Source("url", mpv_path)
    if mpv_path and mpv_path.startswith("/"):
        return Source("missing", mpv_path)  # mpv's file was deleted under it
    return None


def build_download_command(settings: Settings, url: str, out_dir: Path) -> list[str]:
    """yt-dlp argv that saves one mp4 into ``out_dir`` and reports progress."""
    ytdlp = player._ytdlp_bin()
    if ytdlp is None:
        raise DownloadError("yt-dlp is not installed")
    if not player._is_youtube_url(url):
        # same impersonating wrapper mpv's ytdl hook uses for other sites
        wrapper = Path(__file__).resolve().parent.parent / "scripts" / "yt-dlp-impersonate.sh"
        if wrapper.exists():
            ytdlp = str(wrapper)
    if player._which("ffmpeg"):
        fmt = ["-f", settings.ytdl_format, "--merge-output-format", "mp4"]
    else:
        # No ffmpeg → yt-dlp can't merge separate video+audio streams, so ask
        # for a single pre-merged file (lower quality on some sites).
        fmt = ["-f", "b[height<=1080]/b"]
    return [
        ytdlp,
        "--no-warnings",
        "--no-playlist",
        "--newline",
        "--progress-template", _PROGRESS_TEMPLATE,
        *fmt,
        "-o", str(out_dir / "%(title).80B [%(id)s].%(ext)s"),
        *player._ytdl_cli_args(settings, url),
        "--", url,
    ]


def make_workdir() -> Path:
    return Path(tempfile.mkdtemp(prefix="tg-mpv-bot-upload-"))


def _find_output(out_dir: Path) -> Path | None:
    files = [
        p for p in out_dir.iterdir()
        if p.is_file() and p.suffix not in {".part", ".ytdl", ".temp"}
    ]
    return max(files, key=lambda p: p.stat().st_size) if files else None


async def download(
    settings: Settings,
    url: str,
    out_dir: Path,
    on_progress: Callable[[dict], Awaitable[None]],
) -> Path:
    """Run yt-dlp, reporting throttled progress dicts; return the saved file.

    ``on_progress`` receives ``{"percent", "speed", "eta"}`` while downloading
    and ``{"stage": "merging"}`` once yt-dlp starts post-processing.
    """
    cmd = build_download_command(settings, url, out_dir)
    logger.info("Upload download: %s", " ".join(cmd))
    proc = await asyncio.create_subprocess_exec(
        *cmd,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.STDOUT,
        env={**os.environ, "PATH": player._augmented_path()},
    )
    tail: deque[str] = deque(maxlen=6)
    last = 0.0
    merging = False
    loop = asyncio.get_running_loop()
    assert proc.stdout is not None
    try:
        async for raw in proc.stdout:
            line = raw.decode(errors="replace").strip()
            info = parse_progress_line(line)
            if info is not None:
                now = loop.time()
                if now - last >= _PROGRESS_MIN_INTERVAL:
                    last = now
                    await on_progress(info)
                continue
            if line:
                tail.append(line)
            if not merging and any(m in line for m in _POSTPROCESS_MARKERS):
                merging = True
                await on_progress({"stage": "merging"})
        rc = await proc.wait()
    except asyncio.CancelledError:
        proc.kill()
        raise
    if rc != 0:
        reason = next((ln for ln in reversed(tail) if ln.startswith("ERROR")), None)
        raise DownloadError(friendly_error(reason or "download failed")[:200])
    out = _find_output(out_dir)
    if out is None:
        raise DownloadError("yt-dlp produced no file")
    return out
