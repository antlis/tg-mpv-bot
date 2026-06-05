"""Tiny JSON state file — remembers the last-played playlist across restarts.

Written by :func:`src.player.play` (the single launch path, so button taps,
``/mpv_play`` and ``/mpv_last`` itself all count) and read by ``/mpv_last``.
mpv's ``--save-position-on-quit`` already restores the position within the
file; this restores *which playlist* after a reboot or bot restart.
"""

from __future__ import annotations

import json
import logging
import time
from pathlib import Path

logger = logging.getLogger(__name__)


def record_last_played(state_file: Path, playlist: Path) -> None:
    """Persist ``playlist`` as the most recently launched one (best-effort)."""
    try:
        state_file.parent.mkdir(parents=True, exist_ok=True)
        state_file.write_text(
            json.dumps({"last_played": str(playlist), "at": int(time.time())})
        )
    except OSError as exc:  # a broken state file must never break playback
        logger.warning("Could not write state file %s: %s", state_file, exc)


def last_played(state_file: Path) -> Path | None:
    """The last-played playlist path, or ``None`` if unknown/gone/corrupt."""
    try:
        data = json.loads(state_file.read_text())
    except (OSError, ValueError):
        return None
    raw = data.get("last_played", "") if isinstance(data, dict) else ""
    if not raw:
        return None
    p = Path(raw)
    return p if p.is_file() else None
