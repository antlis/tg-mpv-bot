"""Tiny JSON state file — watch history across restarts.

Written by :func:`src.player.play` / :func:`src.player.play_url` (the only
launch paths, so button taps, ``/mpv_play``, bare-URL messages and
``/mpv_last`` itself all count) and read by ``/mpv_last`` / ``/mpv_history``.
mpv's ``--save-position-on-quit`` already restores the position within the
file or stream; this restores *what was playing*.
"""

from __future__ import annotations

import json
import logging
import time
from dataclasses import dataclass
from pathlib import Path

logger = logging.getLogger(__name__)

HISTORY_LIMIT = 20


@dataclass(frozen=True)
class HistoryEntry:
    target: str  # playlist path or URL
    name: str    # display name: playlist stem, or media title for URLs
    at: int      # unix timestamp of the last launch

    @property
    def is_url(self) -> bool:
        return self.target.startswith(("http://", "https://"))


def _load(state_file: Path) -> list[dict]:
    try:
        data = json.loads(state_file.read_text())
    except (OSError, ValueError):
        return []
    if not isinstance(data, dict):
        return []
    raw = data.get("history", [])
    if not raw and data.get("last_played"):  # migrate the pre-history format
        raw = [{"target": data["last_played"], "at": data.get("at", 0)}]
    return [e for e in raw if isinstance(e, dict) and e.get("target")]


def record_last_played(
    state_file: Path, target: str | Path, name: str | None = None
) -> None:
    """Prepend ``target`` to the watch history (deduped, best-effort)."""
    target = str(target)
    entry = {
        "target": target,
        "name": name or (target if "://" in target else Path(target).stem),
        "at": int(time.time()),
    }
    entries = [e for e in _load(state_file) if e["target"] != target]
    entries.insert(0, entry)
    try:
        state_file.parent.mkdir(parents=True, exist_ok=True)
        state_file.write_text(json.dumps({"history": entries[:HISTORY_LIMIT]}))
    except OSError as exc:  # a broken state file must never break playback
        logger.warning("Could not write state file %s: %s", state_file, exc)


def history(state_file: Path) -> list[HistoryEntry]:
    """Watch history, newest first. Local playlists that no longer exist on
    disk are dropped (library reorganised); URLs always survive."""
    out: list[HistoryEntry] = []
    for e in _load(state_file):
        target = str(e["target"])
        if not target.startswith(("http://", "https://")) and not Path(target).is_file():
            continue
        out.append(
            HistoryEntry(
                target=target,
                name=str(e.get("name") or Path(target).stem),
                at=int(e.get("at") or 0),
            )
        )
    return out


def last_played(state_file: Path) -> str | None:
    """The most recent playlist path or URL; ``None`` if unknown/gone/corrupt."""
    entries = history(state_file)
    return entries[0].target if entries else None
