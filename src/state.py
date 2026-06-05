"""Tiny JSON state file — remembers the last-played item across restarts.

Written by :func:`src.player.play` / :func:`src.player.play_url` (the only
launch paths, so button taps, ``/mpv_play``, bare-URL messages and
``/mpv_last`` itself all count) and read by ``/mpv_last``. mpv's
``--save-position-on-quit`` already restores the position within the file or
stream; this restores *what was playing* after a reboot or bot restart.
"""

from __future__ import annotations

import json
import logging
import time
from pathlib import Path

logger = logging.getLogger(__name__)


def record_last_played(state_file: Path, target: str | Path) -> None:
    """Persist ``target`` (playlist path or URL) as most recently launched."""
    try:
        state_file.parent.mkdir(parents=True, exist_ok=True)
        state_file.write_text(
            json.dumps({"last_played": str(target), "at": int(time.time())})
        )
    except OSError as exc:  # a broken state file must never break playback
        logger.warning("Could not write state file %s: %s", state_file, exc)


def last_played(state_file: Path) -> str | None:
    """The last-played playlist path or URL; ``None`` if unknown/gone/corrupt.

    URLs are returned as-is; local paths only if the file still exists (the
    library may have been reorganised since).
    """
    try:
        data = json.loads(state_file.read_text())
    except (OSError, ValueError):
        return None
    raw = data.get("last_played", "") if isinstance(data, dict) else ""
    if not raw:
        return None
    if raw.startswith(("http://", "https://")):
        return raw
    return raw if Path(raw).is_file() else None
