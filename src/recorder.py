"""Record mpv's currently-playing media to a Telegram-friendly file.

mpv itself isn't involved in the capture — we read its current source, position
and codec over IPC (in :mod:`src.commands`) and run a *separate* ffmpeg that
reads the same file/URL. Video is always re-encoded to clean H.264 yuv420p
AAC regardless of source format, so the output always plays inline on Telegram /
Android.  Audio/radio goes to an Opus voice message.  Local files are seeked to
the live position and paced at realtime so a manual stop captures exactly what
was on screen; live http streams are captured going forward.

The command-building is a pure function (unit-testable); :func:`spawn` is the
async helper that starts ffmpeg.
"""

from __future__ import annotations

import asyncio

RECORD_MAX = 3600  # hard cap (1 hour)


def build_record_args(
    src: str, pos: float, dur: float, is_video: bool, vfmt: str, out: str, secs: int = RECORD_MAX
) -> list[str]:
    """ffmpeg argv (after the ``ffmpeg`` binary) to capture ``src`` into ``out``.

    - Local files: seek to ``pos`` (clamped inside ``dur``) and pace at realtime.
    - Live http (radio / streams): no seek, capture going forward.
    - Video: always re-encode to H.264 yuv420p 720p AAC — the pixel format and
      codec profile that Telegram / Android plays inline.  faststart moves the
      moov atom to the front so the file is streamable.  If ffmpeg is killed
      before moov is written the file is unplayable (acceptable — same outcome
      as before when the remux step failed).
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
            "+faststart",
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
