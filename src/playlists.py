"""Playlist discovery, matching and validation.

Moved out of mpvctl.sh so the most valuable logic — which playlist a query
resolves to, and whether a playlist's files still exist on disk — is plain
Python and unit-testable. The on-disk validator is what catches the kind of
breakage that happens when media directories get renamed.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path

VIDEO_SUFFIXES = {".mkv", ".mp4", ".avi", ".webm", ".m4v", ".mov", ".flv", ".mpg", ".mpeg"}

# Tokens that mark the start of release/quality/source junk in a media name.
# Everything from the first such token onward is dropped for display.
_JUNK_TOKEN = re.compile(
    r"""^(?:
        \d{3,4}p|4k|uhd|
        blu-?ray|b[rd]rip|bdremux|remux|web-?dl|web-?rip|webrip|hdtv|dvd-?rip|hdrip|
        x?26[45]|h\.?26[45]|hevc|avc|xvid|divx|
        10bit|8bit|hi10p|
        aac|ac3|eac3|ddp?\d?|dts|truehd|atmos|flac|opus|
        hdr|hdr10|dovi|sdr|
        yify|yts\w*|rarbg|galaxyrg\w*|proton\w*|tigole|t3nzin|silence|phoenixrg|ositv|frisky|tgx|
        msubs|esubs?
    )$""",
    re.I | re.X,
)
_BRACKETS = re.compile(r"[\[\{][^\]\}]*[\]\}]")  # [..] and {..}
_CHANNELS = re.compile(r"\b\d\.\d\b")            # 5.1 / 7.1 / 2.0


def _keep_paren(inner: str) -> bool:
    """Keep a (parenthesised) group only if it has no junk (e.g. a year)."""
    return not any(_JUNK_TOKEN.match(t) for t in re.split(r"[\s.]+", inner) if t)


def prettify(name: str) -> str:
    """Clean a playlist name for *display* (buttons, 'now playing').

    Strips bracketed tags, source/quality/codec/group junk and scene dotting,
    so 'Heavy.Metal.1981.1080p.BluRay.DDP5.1.x265-GalaxyRG265[TGx]' shows as
    'Heavy Metal 1981' and 'fight-club' as 'Fight Club'. The underlying name
    (used for matching and callbacks) is never changed.
    """
    s = _BRACKETS.sub(" ", name)
    s = re.sub(r"\(([^)]*)\)", lambda m: m.group(0) if _keep_paren(m.group(1)) else " ", s)
    s = _CHANNELS.sub(" ", s)
    s = re.sub(r"[._]+", " ", s)            # scene dots / underscores → spaces
    s = re.sub(r"(?<=\w)-(?=\w)", " ", s)   # word-joining hyphens → spaces
    s = re.sub(r"(?<=\w)-(?=\s)|(?<=\s)-(?=\w)", " ", s)  # dangling hyphens ("Masters- Foo")

    words = s.split()
    kept: list[str] = []
    for w in words:
        if _JUNK_TOKEN.match(w.strip("()")):
            break  # title is everything before the first junk token
        kept.append(w)
    out = " ".join(kept).strip(" ,-")
    if not out:
        return name  # all-junk name → leave it as-is rather than blanking it
    if out.islower():  # a slug like "the-big-lebowski" → Title Case
        out = out.title()
    return out


@dataclass(frozen=True)
class Playlist:
    name: str                      # filename without .m3u
    category: str                  # parent-of-playlists dir name, e.g. "cartoons"
    path: Path
    subcategory: str | None = None  # nested folder under the playlists dir, e.g. provider

    @property
    def label(self) -> str:
        if self.subcategory:
            return f"{self.category}/{self.subcategory}/{self.name}"
        return f"{self.category}/{self.name}"

    @property
    def display(self) -> str:
        """Cleaned name for UI (buttons, 'now playing'); raw name is unchanged."""
        return prettify(self.name)


def discover(dirs: list[Path]) -> list[Playlist]:
    """Return all ``*.m3u`` playlists across ``dirs``, sorted by name.

    ``*.m3u`` directly in a playlists dir have no subcategory. One level of
    nesting is supported: ``<playlists>/<sub>/*.m3u`` gets ``subcategory=<sub>``
    (used to group e.g. tutorials by provider). Sort is case-insensitive and
    stable, so global indices stay consistent between a ``/mpv_list`` render and
    a later ``/mpv_play <n>``.
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
        for sub in d.iterdir():
            if sub.is_dir():
                for f in sub.glob("*.m3u"):
                    if f.is_file():
                        found.append(
                            Playlist(
                                name=f.stem,
                                category=category,
                                path=f,
                                subcategory=sub.name,
                            )
                        )
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


def search(
    playlists: list[Playlist], query: str, category: str | None = None
) -> list[int]:
    """Global indices of playlists matching ``query``, optionally per-category.

    Every whitespace-separated token must appear (case-insensitively) in the
    raw name, the prettified display name or the subcategory — so "big
    lebowski" matches "the-big-lebowski" and "office us" matches scene-dotted
    names. Returns *global* indices so results can drive ``pl:<i>`` callbacks.
    """
    tokens = [t.lower() for t in query.split()]
    if not tokens:
        return []
    matches: list[int] = []
    for i, pl in enumerate(playlists):
        if category is not None and pl.category.lower() != category.lower():
            continue
        hay = " ".join((pl.name, pl.display, pl.subcategory or "")).lower()
        if all(t in hay for t in tokens):
            matches.append(i)
    return matches


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
