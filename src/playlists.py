"""Playlist discovery, matching and validation.

Moved out of mpvctl.sh so the most valuable logic — which playlist a query
resolves to, and whether a playlist's files still exist on disk — is plain
Python and unit-testable. The on-disk validator is what catches the kind of
breakage that happens when media directories get renamed.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

VIDEO_SUFFIXES = {".mkv", ".mp4", ".avi", ".webm", ".m4v", ".mov", ".flv", ".mpg", ".mpeg"}


@dataclass(frozen=True)
class Playlist:
    name: str        # filename without .m3u
    category: str    # parent-of-playlists dir name, e.g. "cartoons"
    path: Path

    @property
    def label(self) -> str:
        return f"{self.category}/{self.name}"


def discover(dirs: list[Path]) -> list[Playlist]:
    """Return all ``*.m3u`` playlists across ``dirs``, sorted by name.

    Sort is case-insensitive and stable, so 1-based indices stay consistent
    between a ``/mpv_list`` render and a later ``/mpv_play <n>``.
    """
    found: list[Playlist] = []
    for d in dirs:
        if not d.is_dir():
            continue
        # category = the dir that *contains* the playlists dir
        category = d.parent.name if d.name == "playlists" else d.name
        for f in d.glob("*.m3u"):
            if f.is_file():
                found.append(Playlist(name=f.stem, category=category, path=f))
    found.sort(key=lambda p: p.name.lower())
    return found


def find(playlists: list[Playlist], query: str) -> Playlist | None:
    """Resolve a query to a playlist.

    A purely numeric query is a 1-based index into ``playlists``. Otherwise it
    is a case-insensitive substring match against the name (first match wins,
    in sorted order). Returns ``None`` if nothing matches.
    """
    query = query.strip()
    if not query:
        return None

    if query.isdigit():
        idx = int(query) - 1
        if 0 <= idx < len(playlists):
            return playlists[idx]
        return None

    needle = query.lower()
    for pl in playlists:
        if needle in pl.name.lower():
            return pl
    return None


def read_entries(playlist: Path) -> list[str]:
    """Return the non-comment, non-blank lines of an m3u file."""
    entries: list[str] = []
    with open(playlist, encoding="utf-8", errors="replace") as fh:
        for line in fh:
            s = line.strip()
            if s and not s.startswith("#"):
                entries.append(s)
    return entries


def _resolve(entry: str, playlist_dir: Path) -> str | Path:
    """Resolve an m3u entry to something checkable; URLs pass through as-is."""
    if entry.startswith(("http://", "https://")):
        return entry  # remote — treated as always-present
    if entry.startswith("file://"):
        entry = entry[len("file://"):]
    p = Path(entry)
    return p if p.is_absolute() else (playlist_dir / p)


def missing_entries(playlist: Path) -> list[str]:
    """Return entries whose target file does not exist (URLs are skipped)."""
    missing: list[str] = []
    for entry in read_entries(playlist):
        resolved = _resolve(entry, playlist.parent)
        if isinstance(resolved, Path) and not resolved.exists():
            missing.append(entry)
    return missing


@dataclass(frozen=True)
class ValidationResult:
    playlist: Playlist
    total: int
    missing: list[str]

    @property
    def ok(self) -> bool:
        return not self.missing


def validate(playlists: list[Playlist]) -> list[ValidationResult]:
    """Check every playlist's entries against the filesystem."""
    results: list[ValidationResult] = []
    for pl in playlists:
        entries = read_entries(pl.path)
        results.append(
            ValidationResult(
                playlist=pl,
                total=len(entries),
                missing=missing_entries(pl.path),
            )
        )
    return results
