"""Record mpv's currently-playing media to a Telegram-friendly file.

mpv itself isn't involved in the capture — we read its current source, position
and codec over IPC (in :mod:`src.commands`) and run a *separate* ffmpeg that
reads the same file/URL. Video is always re-encoded to clean H.264 yuv420p
AAC regardless of source format, so the output always plays inline on Telegram /
Android without needing a post-processing remux.  Audio/radio goes to an Opus
voice message. Local files are seeked to the live position and paced at realtime
so a manual stop captures exactly what was on screen; live http streams are
captured going forward.

The command-building is a pure function (unit-testable); :func:`spawn` and
:func:`remux_faststart` are the two async helpers.
"""

from __future__ import annotations

import asyncio
import os
from pathlib import Path

RECORD_MAX = 3600  # hard cap (1 hour)

_LOG_DIR = Path("/tmp")


def _errlog_path(out: str) -> str:
    return str(_LOG_DIR / f"tg-mpv-remux-{os.urandom(4).hex()}.log")


def build_record_args(
    src: str, pos: float, dur: float, is_video: bool, vfmt: str, out: str, secs: int = RECORD_MAX
) -> list[str]:
    """ffmpeg argv (after the ``ffmpeg`` binary) to capture ``src`` into ``out``.

    - Local files: seek to ``pos`` (clamped inside ``dur``) and pace at realtime.
    - Live http (radio / streams): no seek, capture going forward.
    - Video: always re-encode to H.264 yuv420p 720p AAC — the pixel format and
      codec profile that Telegram / Android plays inline.  A fragmented mp4
      stays valid even if a stop kills ffmpeg mid-write.
    - Audio-only: mono Opus, sent as a voice message.
    """
    secs = max(1, min(RECORD_MAX, int(secs)))
    is_http = str(src).startswith("http")
    pre: list[str] = []
    if not is_http:
        p = pos or 0
        if dur and dur > 0:
            p = max(0, min(p, dur - 2))
        if p > 0:
            pre += ["-ss", str(int(p))]
        pre += ["-re"]
    if is_video:
        # Always re-encode — even when the source is already h264.  A stream
        # copy preserves the source's pixel format (yuv422p, yuvj420p, etc.)
        # which Telegram / Android can't play inline.
        # -bf 0 (no B-frames) + cfr keep A/V aligned: the B-frame reorder
        # delay otherwise leaves the first video frame at a positive PTS while
        # audio starts at 0, i.e. audio ~80ms ahead of the video.
        venc = [
            "-c:v",
            "libx264",
            "-preset",
            "veryfast",
            "-crf",
            "23",
            "-vf",
            "scale=-2:720",
            "-pix_fmt",
            "yuv420p",
            "-bf",
            "0",
            "-fps_mode",
            "cfr",
        ]
        body = [
            "-i",
            src,
            "-t",
            str(secs),
            "-map",
            "0:v:0",
            "-map",
            "0:a:0",
            *venc,
            "-af",
            "aresample=async=1:first_pts=0",
            "-c:a",
            "aac",
            "-b:a",
            "160k",
            "-movflags",
            "+frag_keyframe+empty_moov+default_base_moof",
            out,
        ]
    else:
        body = [
            "-i",
            src,
            "-t",
            str(secs),
            "-vn",
            "-ac",
            "1",
            "-c:a",
            "libopus",
            "-b:a",
            "64k",
            out,
        ]
    return ["-y", "-nostdin", *pre, *body]


async def spawn(args: list[str], errlog: str):
    """Start ffmpeg with the given argv, sending stderr to ``errlog``."""
    errf = open(errlog, "w")
    try:
        return await asyncio.create_subprocess_exec(
            "ffmpeg",
            *args,
            stdout=asyncio.subprocess.DEVNULL,
            stderr=errf,
        )
    finally:
        errf.close()  # the child keeps its own dup of the fd


async def _logged_reencode(src: str, dst: str, log: str) -> asyncio.subprocess.Process:
    return await asyncio.create_subprocess_exec(
        "ffmpeg",
        "-y",
        "-nostdin",
        "-i",
        src,
        "-c:v",
        "libx264",
        "-preset",
        "veryfast",
        "-crf",
        "23",
        "-pix_fmt",
        "yuv420p",
        "-bf",
        "0",
        "-fps_mode",
        "cfr",
        "-c:a",
        "aac",
        "-b:a",
        "160k",
        "-f",
        "mp4",
        "-movflags",
        "+faststart",
        dst,
        stdout=asyncio.subprocess.DEVNULL,
        stderr=open(log, "w"),
    )


async def remux_faststart(src: str) -> str | None:
    """Re-encode a fragmented-mp4 recording into a plain faststart mp4.

    Always re-encodes — a stream-copy (``-c copy``) from a fragmented-mp4 input
    cannot reliably build the moov sample tables needed for a standard mp4,
    resulting in a file that Telegram / Android can't play inline.

    Returns the path to the final file, or *None* on failure.
    Stderr is logged to ``/tmp/tg-mpv-remux-*.log`` for debugging.
    """
    dst = src[:-4] + "_final.mp4" if src.endswith(".mp4") else src + "_final.mp4"
    log = _errlog_path(src)
    tail: str | None = None
    try:
        p = await _logged_reencode(src, dst, log)
        await asyncio.wait_for(p.wait(), 300)
        if p.returncode == 0 and os.path.exists(dst) and os.path.getsize(dst) > 0:
            Path(log).unlink(missing_ok=True)
            return dst
    except Exception:
        pass
    try:
        tail = Path(log).read_text()[-600:] if Path(log).exists() else None
    except OSError:
        tail = None
    Path(log).unlink(missing_ok=True)
    Path(dst).unlink(missing_ok=True)
    if tail:
        import logging

        logging.getLogger(__name__).warning("remux_faststart failed. ffmpeg tail:\n%s", tail)
    return None
