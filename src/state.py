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
    pos: int = 0  # last checkpointed position (seconds) — used to resume URLs

    @property
    def is_url(self) -> bool:
        return self.target.startswith(("http://", "https://"))


def _read_doc(state_file: Path) -> dict:
    try:
        data = json.loads(state_file.read_text())
    except (OSError, ValueError):
        return {}
    return data if isinstance(data, dict) else {}


def _write_doc(state_file: Path, doc: dict) -> None:
    try:
        state_file.parent.mkdir(parents=True, exist_ok=True)
        state_file.write_text(json.dumps(doc))
    except OSError as exc:  # a broken state file must never break playback
        logger.warning("Could not write state file %s: %s", state_file, exc)


def _load(state_file: Path) -> list[dict]:
    data = _read_doc(state_file)
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
    old = next((e for e in _load(state_file) if e["target"] == target), None)
    if old and old.get("pos"):
        entry["pos"] = old["pos"]  # replaying keeps the saved resume point
    entries = [e for e in _load(state_file) if e["target"] != target]
    entries.insert(0, entry)
    doc = _read_doc(state_file)
    doc.pop("last_played", None)  # superseded by history
    doc.pop("at", None)
    doc["history"] = entries[:HISTORY_LIMIT]
    _write_doc(state_file, doc)


def update_position(state_file: Path, seconds: float) -> None:
    """Checkpoint the playback position onto the newest history entry.

    Written periodically by the notification listener; mpv's own
    ``--save-position-on-quit`` can't resume piped streams (a pipe has no
    stable resume key), so the bot remembers the position itself and
    relaunches URLs with ``--start``.
    """
    doc = _read_doc(state_file)
    entries = doc.get("history") or []
    if not entries or not isinstance(entries[0], dict):
        return
    entries[0]["pos"] = max(0, int(seconds))
    _write_doc(state_file, doc)


def set_notify_chat(state_file: Path, chat_id: int) -> None:
    """Remember the chat that last started playback (notification target)."""
    doc = _read_doc(state_file)
    if doc.get("notify_chat") != chat_id:
        doc["notify_chat"] = chat_id
        _write_doc(state_file, doc)


def notify_chat(state_file: Path) -> int | None:
    chat = _read_doc(state_file).get("notify_chat")
    return chat if isinstance(chat, int) else None


def set_notify_enabled(state_file: Path, enabled: bool) -> None:
    doc = _read_doc(state_file)
    doc["notify_enabled"] = enabled
    _write_doc(state_file, doc)


def notify_enabled(state_file: Path) -> bool:
    return bool(_read_doc(state_file).get("notify_enabled", True))


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
                pos=int(e.get("pos") or 0),
            )
        )
    return out


def last_played(state_file: Path) -> str | None:
    """The most recent playlist path or URL; ``None`` if unknown/gone/corrupt."""
    entries = history(state_file)
    return entries[0].target if entries else None



def delete_history_entry(state_file: Path, target: str) -> None:
    """Remove one entry from the watch history by target URL/path."""
    doc = _read_doc(state_file)
    doc["history"] = [e for e in doc.get("history", []) if e.get("target") != target]
    _write_doc(state_file, doc)
