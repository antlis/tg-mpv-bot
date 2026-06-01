"""Generate playlists for newly-added media — idempotent.

The bot only plays ``*.m3u`` files, so media dropped on disk is invisible until
a playlist points at it. This scans the media dirs and writes a playlist for
anything not already covered, leaving existing playlists (and manual edits)
untouched.

Two layouts, matching :func:`src.playlists.discover`:
- **flat** (cartoons / movie / shows): each top-level item (a movie/show folder
  or a loose video file) → one playlist. New items are detected by content, so
  a differently-named existing playlist won't be duplicated.
- **nested** (tutorials): ``<provider>/<course>`` → one playlist per course plus
  an ``All <provider>`` playlist. New ones are detected by filename.
"""

from __future__ import annotations

import logging
import os
import re
from pathlib import Path

from .config import Settings
from .playlists import VIDEO_SUFFIXES, read_entries

logger = logging.getLogger(__name__)


def _natkey(s: str):
    return [int(t) if t.isdigit() else t.lower() for t in re.split(r"(\d+)", s)]


def _videos_under(d: Path) -> list[Path]:
    if d.is_file():
        return [d] if d.suffix.lower() in VIDEO_SUFFIXES else []
    out: list[Path] = []
    for dp, dirs, files in os.walk(d):
        dirs[:] = [x for x in dirs if not x.startswith("._")]
        for f in files:
            if not f.startswith("._") and Path(f).suffix.lower() in VIDEO_SUFFIXES:
                out.append(Path(dp) / f)
    out.sort(key=lambda p: _natkey(str(p.relative_to(d))))
    return out


def _safe_name(name: str) -> str:
    name = name.replace("/", "-").strip()
    if len(name.encode()) > 200:
        name = name.encode()[:200].decode("utf-8", "ignore").strip()
    return name


def _write(path: Path, videos: list[Path]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", encoding="utf-8") as fh:
        fh.write("#EXTM3U\n")
        fh.write("\n".join(str(v) for v in videos) + "\n")


def _is_nested(playlists_dir: Path) -> bool:
    """A nested (provider/course) category already has subdirectories."""
    return playlists_dir.is_dir() and any(p.is_dir() for p in playlists_dir.iterdir())


def _canonical(p: Path) -> str:
    """Real path with symlinks resolved (so ~/Videos and /mnt match), best-effort."""
    try:
        return str(p.resolve())
    except OSError:
        return str(p)


def _covered_paths(playlists_dir: Path) -> set[str]:
    """Canonical video paths already referenced by playlists directly in the dir."""
    covered: set[str] = set()
    for pl in playlists_dir.glob("*.m3u"):
        for entry in read_entries(pl):
            if entry.startswith(("http://", "https://")):
                continue
            if entry.startswith("file://"):
                entry = entry[len("file://"):]
            p = Path(entry)
            covered.add(_canonical(p if p.is_absolute() else playlists_dir / p))
    return covered


def generate_flat(playlists_dir: Path) -> list[str]:
    """Create a playlist for each uncovered top-level media item. Returns names."""
    # Resolve the media root once so walked paths are canonical (/mnt, not the
    # ~/Videos symlink) and match the absolute paths in existing playlists.
    media = playlists_dir.parent.resolve()
    playlists_dir.mkdir(parents=True, exist_ok=True)
    covered = _covered_paths(playlists_dir)
    created: list[str] = []
    for item in sorted(media.iterdir(), key=lambda p: _natkey(p.name)):
        if item.name == "playlists" or item.name.startswith("."):
            continue
        vids = _videos_under(item)
        if not vids:
            continue
        # Already represented if ANY of its videos appears in an existing
        # playlist — never re-generate a partially-curated item (which would
        # otherwise overwrite a hand-curated subset with "all files").
        if any(str(v) in covered for v in vids):
            continue
        name = item.stem if item.is_file() else item.name
        target = playlists_dir / f"{_safe_name(name)}.m3u"
        if target.exists():  # never overwrite an existing playlist
            continue
        _write(target, vids)
        created.append(name)
    return created


def generate_nested(playlists_dir: Path) -> list[str]:
    """Create per-course + per-provider 'All' playlists for new tutorial content."""
    media = playlists_dir.parent.resolve()
    created: list[str] = []
    for prov in sorted(
        (p for p in media.iterdir() if p.is_dir() and p.name != "playlists"),
        key=lambda p: p.name.lower(),
    ):
        course_dirs = [
            c
            for c in sorted(prov.iterdir(), key=lambda p: _natkey(p.name))
            if c.is_dir() and _videos_under(c)
        ]
        prov_videos = _videos_under(prov)
        if not prov_videos:
            continue
        subdir = playlists_dir / _safe_name(prov.name)
        if course_dirs:
            for c in course_dirs:
                target = subdir / f"{_safe_name(c.name)}.m3u"
                if not target.exists():
                    _write(target, _videos_under(c))
                    created.append(f"{prov.name}/{c.name}")
            all_pl = subdir / f"{_safe_name('All ' + prov.name)}.m3u"
            if not all_pl.exists():
                _write(all_pl, prov_videos)
                created.append(f"{prov.name}/All {prov.name}")
        else:
            target = subdir / f"{_safe_name(prov.name)}.m3u"
            if not target.exists():
                _write(target, prov_videos)
                created.append(prov.name)
    return created


def _media_root(playlist_path: Path, subcategory: str | None) -> Path:
    # flat:   <root>/playlists/name.m3u        → root
    # nested: <root>/playlists/<sub>/name.m3u  → root
    pld = playlist_path.parent.parent if subcategory else playlist_path.parent
    return pld.parent.resolve()


def _basename_index(media_root: Path) -> dict[str, list[str]]:
    idx: dict[str, list[str]] = {}
    for dp, dirs, files in os.walk(media_root):
        dirs[:] = [d for d in dirs if d != "playlists" and not d.startswith("._")]
        for f in files:
            if not f.startswith("._") and Path(f).suffix.lower() in VIDEO_SUFFIXES:
                idx.setdefault(f, []).append(os.path.join(dp, f))
    return idx


def repair_playlists(settings: Settings) -> list[str]:
    """Fix playlists with missing files: re-point entries to a renamed/moved
    file (matched by unique basename) and prune entries with no match.

    Backs each changed playlist up to ``<name>.m3u.bak`` first. Returns a
    per-playlist summary of what changed.
    """
    from . import playlists as pl

    broken = [r for r in pl.validate(pl.discover(settings.playlist_dirs)) if not r.ok]
    indexes: dict[str, dict[str, list[str]]] = {}
    summary: list[str] = []

    for r in broken:
        media_root = _media_root(r.playlist.path, r.playlist.subcategory)
        idx = indexes.setdefault(str(media_root), None) or _basename_index(media_root)
        indexes[str(media_root)] = idx

        new_lines: list[str] = []
        repointed = pruned = 0
        for entry in pl.read_entries(r.playlist.path):
            resolved = pl._resolve(entry, r.playlist.path.parent)
            if not isinstance(resolved, Path) or resolved.exists():
                new_lines.append(entry)
                continue
            cands = idx.get(os.path.basename(entry), [])
            if len(cands) == 1:
                new_lines.append(cands[0])
                repointed += 1
            else:
                pruned += 1  # 0 matches → drop; >1 ambiguous → also drop
        if repointed or pruned:
            r.playlist.path.with_suffix(".m3u.bak").write_text(
                r.playlist.path.read_text(encoding="utf-8", errors="replace"),
                encoding="utf-8",
            )
            with open(r.playlist.path, "w", encoding="utf-8") as fh:
                fh.write("#EXTM3U\n")
                if new_lines:
                    fh.write("\n".join(new_lines) + "\n")
            summary.append(f"{r.playlist.label}: repointed {repointed}, pruned {pruned}")
    return summary


def generate_missing(settings: Settings) -> list[str]:
    """Generate playlists for new media across all configured dirs."""
    created: list[str] = []
    for pld in settings.playlist_dirs:
        if not pld.parent.is_dir():
            continue
        try:
            made = generate_nested(pld) if _is_nested(pld) else generate_flat(pld)
        except OSError as exc:
            logger.warning("playlist generation failed for %s: %s", pld, exc)
            continue
        created.extend(made)
    return created
