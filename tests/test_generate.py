from pathlib import Path

from src import generate, playlists


def _mkvid(p: Path) -> None:
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text("x")


# ── flat (movie/cartoons/shows) ──────────────────────────────────────
def test_generate_flat_creates_for_new_items(tmp_path):
    media = tmp_path / "movie"
    pld = media / "playlists"
    pld.mkdir(parents=True)
    _mkvid(media / "office-space" / "office.mkv")
    _mkvid(media / "loose-film.mp4")

    created = generate.generate_flat(pld)
    assert set(created) == {"office-space", "loose-film"}
    assert (pld / "office-space.m3u").exists()
    assert (pld / "loose-film.m3u").exists()
    # the playlist points at the real video
    assert "office.mkv" in (pld / "office-space.m3u").read_text()


def test_generate_flat_skips_covered_items(tmp_path):
    media = tmp_path / "movie"
    pld = media / "playlists"
    pld.mkdir(parents=True)
    vid = media / "fight-club" / "fc.mkv"
    _mkvid(vid)
    # an existing (differently-named) playlist already references it
    (pld / "fc.m3u").write_text(f"{vid}\n")

    created = generate.generate_flat(pld)
    assert created == []
    assert not (pld / "fight-club.m3u").exists()


def test_generate_flat_skips_partially_covered(tmp_path):
    # folder has an extra file beyond what the existing playlist lists —
    # must NOT regenerate (that would overwrite the curated subset).
    media = tmp_path / "cartoons"
    pld = media / "playlists"
    pld.mkdir(parents=True)
    film = media / "dead-leaves" / "film.mkv"
    _mkvid(film)
    _mkvid(media / "dead-leaves" / "extra-trailer.mkv")
    (pld / "dead-leaves.m3u").write_text(f"{film}\n")  # only the film

    assert generate.generate_flat(pld) == []
    # original curation preserved
    assert (pld / "dead-leaves.m3u").read_text() == f"{film}\n"


def test_generate_flat_never_overwrites_existing_file(tmp_path):
    media = tmp_path / "movie"
    pld = media / "playlists"
    pld.mkdir(parents=True)
    _mkvid(media / "thing" / "v.mkv")
    (pld / "thing.m3u").write_text("HAND-WRITTEN\n")  # same name, unrelated content
    assert generate.generate_flat(pld) == []
    assert (pld / "thing.m3u").read_text() == "HAND-WRITTEN\n"


def test_generate_flat_is_idempotent(tmp_path):
    media = tmp_path / "cartoons"
    pld = media / "playlists"
    pld.mkdir(parents=True)
    _mkvid(media / "futurama" / "S01E01.mkv")
    assert generate.generate_flat(pld) == ["futurama"]
    assert generate.generate_flat(pld) == []  # second run adds nothing


def test_generate_flat_natural_sort(tmp_path):
    media = tmp_path / "shows"
    pld = media / "playlists"
    pld.mkdir(parents=True)
    for n in (1, 2, 10):
        _mkvid(media / "show" / f"E{n}.mkv")
    generate.generate_flat(pld)
    lines = [ln for ln in (pld / "show.m3u").read_text().splitlines()
             if ln and not ln.startswith("#")]
    assert [Path(ln).name for ln in lines] == ["E1.mkv", "E2.mkv", "E10.mkv"]


def test_generate_flat_covered_via_symlinked_media(tmp_path):
    # media reached through a symlink; existing playlist stores the real path
    real = tmp_path / "real"
    vid = real / "movieB" / "film.mkv"
    _mkvid(vid)
    link = tmp_path / "link"
    link.symlink_to(real)
    pld = link / "playlists"
    pld.mkdir()
    (pld / "movieB.m3u").write_text(f"{vid.resolve()}\n")

    created = generate.generate_flat(pld)
    assert created == []  # canonicalization makes the symlinked walk match


# ── nested (tutorials) ───────────────────────────────────────────────
def test_generate_nested_per_course_and_all(tmp_path):
    media = tmp_path / "tutorials"
    pld = media / "playlists"
    pld.mkdir(parents=True)
    _mkvid(media / "frontend-masters" / "Course A" / "01.mkv")
    _mkvid(media / "frontend-masters" / "Course B" / "01.mkv")

    created = generate.generate_nested(pld)
    assert "frontend-masters/Course A" in created
    assert "frontend-masters/Course B" in created
    assert "frontend-masters/All frontend-masters" in created
    assert (pld / "frontend-masters" / "Course A.m3u").exists()
    assert (pld / "frontend-masters" / "All frontend-masters.m3u").exists()


def test_generate_nested_standalone_course(tmp_path):
    media = tmp_path / "tutorials"
    pld = media / "playlists"
    pld.mkdir(parents=True)
    _mkvid(media / "TypeScript" / "01.mkv")  # videos directly, no course subdir
    created = generate.generate_nested(pld)
    assert created == ["TypeScript"]
    assert (pld / "TypeScript" / "TypeScript.m3u").exists()


def test_generate_nested_is_idempotent(tmp_path):
    media = tmp_path / "tutorials"
    pld = media / "playlists"
    pld.mkdir(parents=True)
    _mkvid(media / "wesbos" / "React" / "01.mkv")
    generate.generate_nested(pld)
    assert generate.generate_nested(pld) == []


# ── routing ──────────────────────────────────────────────────────────
def test_is_nested_detection(tmp_path):
    flat = tmp_path / "movie" / "playlists"
    flat.mkdir(parents=True)
    (flat / "a.m3u").write_text("/x.mkv\n")
    assert generate._is_nested(flat) is False

    nested = tmp_path / "tutorials" / "playlists"
    (nested / "provider").mkdir(parents=True)
    assert generate._is_nested(nested) is True


def test_repair_repoints_and_prunes(tmp_path, monkeypatch):
    from src import config
    media = tmp_path / "movie"
    pld = media / "playlists"
    pld.mkdir(parents=True)
    # the file exists at a NEW location; the playlist still points at the OLD path
    _mkvid(media / "new-folder" / "film.mkv")
    (pld / "movie.m3u").write_text(
        str(media / "old-folder" / "film.mkv") + "\n"   # moved → repoint by basename
        + str(media / "gone" / "nope.mkv") + "\n"        # no match → prune
    )

    monkeypatch.setenv("BOT_TOKEN", "x")
    monkeypatch.setenv("PLAYLIST_DIRS", str(pld))
    config.get_settings.cache_clear()
    summary = generate.repair_playlists(config.get_settings())
    config.get_settings.cache_clear()

    assert len(summary) == 1
    entries = playlists.read_entries(pld / "movie.m3u")
    assert entries == [str((media / "new-folder" / "film.mkv").resolve())]
    assert (pld / "movie.m3u.bak").exists()  # backup kept


def test_generated_playlists_are_discoverable(tmp_path):
    media = tmp_path / "movie"
    pld = media / "playlists"
    pld.mkdir(parents=True)
    _mkvid(media / "new-movie" / "film.mkv")
    generate.generate_flat(pld)
    pls = playlists.discover([pld])
    assert any(p.name == "new-movie" for p in pls)
