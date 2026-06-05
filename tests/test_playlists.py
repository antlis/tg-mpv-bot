from pathlib import Path

import pytest

from src import playlists


@pytest.fixture
def library(tmp_path: Path):
    """Build a fake media tree:  <root>/<category>/playlists/<name>.m3u"""
    root = tmp_path / "Videos"
    dirs = []
    layout = {
        "cartoons": ["futurama", "Adventure Time"],
        "movie": ["fight-club"],
        "shows": ["deadwood"],
    }
    for category, names in layout.items():
        pldir = root / category / "playlists"
        pldir.mkdir(parents=True)
        dirs.append(pldir)
        # a real media file per category for validity checks
        media = root / category / "ep01.mkv"
        media.write_text("x")
        for name in names:
            (pldir / f"{name}.m3u").write_text(str(media) + "\n")
    return root, dirs


def test_discover_sorted_and_categorised(library):
    _, dirs = library
    pls = playlists.discover(dirs)
    names = [p.name for p in pls]
    # case-insensitive sort: "Adventure Time" before "deadwood" before "fight-club"...
    assert names == sorted(names, key=str.lower)
    assert {p.category for p in pls} == {"cartoons", "movie", "shows"}
    cats = {p.name: p.category for p in pls}
    assert cats["futurama"] == "cartoons"
    assert cats["fight-club"] == "movie"


def test_discover_skips_missing_dirs(tmp_path):
    assert playlists.discover([tmp_path / "nope"]) == []


import pytest as _pytest


@_pytest.mark.parametrize("raw,expected", [
    ("28 Years Later (2025) [2160p] [4K] [WEB] [5.1] [YTS.MX]", "28 Years Later (2025)"),
    ("Clerks III (2022) [1080p] [WEBRip] [5.1] [YTS.MX]", "Clerks III (2022)"),
    ("Heavy.Metal.1981.1080p.BluRay.DDP5.1.x265.10bit-GalaxyRG265[TGx]", "Heavy Metal 1981"),
    ("The.Many.Saints.of.Newark.2021.1080p.HMAX.WEBRip.DDP5.1.Atmos.x264-CM",
     "The Many Saints of Newark 2021"),
    ("American Psyco (2000) 1080p", "American Psyco (2000)"),
    ("[CourseClub.NET] FrontendMasters - Responsive Web Typography v2",
     "FrontendMasters - Responsive Web Typography v2"),
    ("fight-club", "Fight Club"),
    ("the-big-lebowski", "The Big Lebowski"),
    ("28-days-later", "28 Days Later"),
])
def test_prettify(raw, expected):
    assert playlists.prettify(raw) == expected


def test_prettify_keeps_plain_titles():
    # nothing junky → returned essentially unchanged
    assert playlists.prettify("Laracasts - Testing Vue") == "Laracasts - Testing Vue"


def test_prettify_never_empty():
    assert playlists.prettify("1080p") == "1080p"  # would-be-empty falls back to raw


def test_display_property_uses_prettify(tmp_path):
    p = playlists.Playlist(name="fight-club", category="movie", path=tmp_path / "x.m3u")
    assert p.display == "Fight Club"
    assert p.name == "fight-club"  # raw name untouched (matching/callbacks rely on it)


def test_discover_nested_subcategories(tmp_path):
    pld = tmp_path / "tutorials" / "playlists"
    pld.mkdir(parents=True)
    # flat playlist directly in the playlists dir → no subcategory
    (pld / "direct.m3u").write_text("/x.mkv\n")
    # nested under a provider folder → subcategory = provider
    (pld / "frontend-masters").mkdir()
    (pld / "frontend-masters" / "Course A.m3u").write_text("/a.mkv\n")
    (pld / "frontend-masters" / "Course B.m3u").write_text("/b.mkv\n")

    pls = playlists.discover([pld])
    assert all(p.category == "tutorials" for p in pls)
    by_name = {p.name: p for p in pls}
    assert by_name["direct"].subcategory is None
    assert by_name["Course A"].subcategory == "frontend-masters"
    assert by_name["Course B"].subcategory == "frontend-masters"


def test_find_by_index(library):
    _, dirs = library
    pls = playlists.discover(dirs)
    assert playlists.find(pls, "1") is pls[0]
    assert playlists.find(pls, str(len(pls))) is pls[-1]


def test_find_index_out_of_range(library):
    _, dirs = library
    pls = playlists.discover(dirs)
    assert playlists.find(pls, "0") is None
    assert playlists.find(pls, "999") is None


def test_find_by_substring_case_insensitive(library):
    _, dirs = library
    pls = playlists.discover(dirs)
    assert playlists.find(pls, "FUTUR").name == "futurama"
    assert playlists.find(pls, "club").name == "fight-club"


def test_find_no_match(library):
    _, dirs = library
    pls = playlists.discover(dirs)
    assert playlists.find(pls, "nonexistent") is None
    assert playlists.find(pls, "") is None


def test_search_returns_all_matching_indices(library):
    _, dirs = library
    pls = playlists.discover(dirs)
    hits = playlists.search(pls, "a")  # broad — several names contain "a"
    assert hits == [i for i, p in enumerate(pls) if "a" in p.name.lower()]
    assert len(hits) > 1  # unlike find(), search returns every match


def test_search_scoped_to_category(library):
    _, dirs = library
    pls = playlists.discover(dirs)
    hits = playlists.search(pls, "a", category="cartoons")
    assert hits and all(pls[i].category == "cartoons" for i in hits)
    assert playlists.search(pls, "futurama", category="movie") == []
    # category match is case-insensitive
    assert playlists.search(pls, "futurama", category="CARTOONS")


def test_search_all_tokens_must_match(library):
    _, dirs = library
    pls = playlists.discover(dirs)
    [i] = playlists.search(pls, "adventure time")
    assert pls[i].name == "Adventure Time"
    assert playlists.search(pls, "adventure nonexistent") == []


def test_search_matches_display_name(tmp_path):
    # query with spaces matches a slugged raw name via its prettified display
    pls = [playlists.Playlist(name="the-big-lebowski", category="movie", path=tmp_path / "x.m3u")]
    assert playlists.search(pls, "big lebowski") == [0]


def test_search_matches_subcategory(tmp_path):
    pls = [
        playlists.Playlist(
            name="Course A", category="tutorials", path=tmp_path / "a.m3u",
            subcategory="frontend-masters",
        )
    ]
    assert playlists.search(pls, "frontend") == [0]


def test_search_empty_query(library):
    _, dirs = library
    pls = playlists.discover(dirs)
    assert playlists.search(pls, "") == []
    assert playlists.search(pls, "   ") == []


def test_validate_all_ok(library):
    _, dirs = library
    results = playlists.validate(playlists.discover(dirs))
    assert all(r.ok for r in results)
    assert all(r.total == 1 for r in results)


def test_validate_detects_missing(library):
    root, dirs = library
    pldir = root / "cartoons" / "playlists"
    (pldir / "broken.m3u").write_text(
        str(root / "cartoons" / "gone.mkv") + "\n"
        "# a comment line\n"
        "https://example.com/stream\n"  # url: always counted present
    )
    results = playlists.validate(playlists.discover(dirs))
    broken = [r for r in results if not r.ok]
    assert len(broken) == 1
    assert broken[0].playlist.name == "broken"
    assert broken[0].missing == [str(root / "cartoons" / "gone.mkv")]


def test_read_entries_filters_comments(tmp_path):
    f = tmp_path / "p.m3u"
    f.write_text("#EXTM3U\n\n/a/b.mkv\n  \n#x\n/c/d.mkv\n")
    assert playlists.read_entries(f) == ["/a/b.mkv", "/c/d.mkv"]


def test_relative_entry_resolves_against_playlist_dir(tmp_path):
    pldir = tmp_path / "playlists"
    pldir.mkdir()
    (tmp_path / "playlists" / "video.mkv").write_text("x")
    pl = pldir / "rel.m3u"
    pl.write_text("video.mkv\n")
    assert playlists.missing_entries(pl) == []
