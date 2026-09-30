"""Extractor-plugin loader + headless-browser candidate ranking."""

from src import browser, plugins


def _write(tmp_path, name, body):
    (tmp_path / name).write_text(body)


def test_load_and_resolve_first_match_wins(tmp_path):
    _write(tmp_path, "a_site.py", "def match(u): return 'a.com' in u\n"
           "def resolve(u): return 'https://cdn/a.m3u8'\n")
    _write(tmp_path, "b_site.py", "def match(u): return True\n"
           "async def resolve(u): return ('https://cdn/b.m3u8', 'https://ref/')\n")
    loaded = plugins.load_plugins(str(tmp_path))
    assert [p.name for p in loaded] == ["a_site", "b_site"]

    hit = plugins.resolve_with_plugins("https://a.com/x", loaded)
    assert hit.media_url == "https://cdn/a.m3u8"
    assert hit.referer == "https://a.com/x"  # defaults to the page URL

    hit = plugins.resolve_with_plugins("https://other.com/x", loaded)
    assert (hit.media_url, hit.referer) == ("https://cdn/b.m3u8", "https://ref/")


def test_broken_and_helper_plugins_are_skipped(tmp_path):
    _write(tmp_path, "_helper.py", "def match(u): return True\ndef resolve(u): return 'x'\n")
    _write(tmp_path, "bad_import.py", "import definitely_not_a_module\n")
    _write(tmp_path, "no_api.py", "x = 1\n")
    _write(tmp_path, "raises.py", "def match(u): return True\n"
           "def resolve(u): raise RuntimeError('boom')\n")
    _write(tmp_path, "none.py", "def match(u): return True\ndef resolve(u): return None\n")
    loaded = plugins.load_plugins(str(tmp_path))
    assert [p.name for p in loaded] == ["none", "raises"]
    assert plugins.resolve_with_plugins("https://x/y", loaded) is None


def test_missing_dir_or_no_plugins():
    assert plugins.load_plugins("") == []
    assert plugins.load_plugins("/nonexistent/dir") == []
    assert plugins.resolve_with_plugins("https://x", []) is None


def test_result_object_passthrough(tmp_path):
    _write(tmp_path, "r.py", "from src.plugins import ResolveResult\n"
           "def match(u): return True\n"
           "def resolve(u): return ResolveResult('file:///t.m3u8', headers={'Origin': 'o'})\n")
    hit = plugins.resolve_with_plugins("https://x/y", plugins.load_plugins(str(tmp_path)))
    assert hit.media_url == "file:///t.m3u8"
    assert hit.headers == {"Origin": "o"}
    assert hit.referer == "https://x/y"


def test_browser_prefers_manifest_and_drops_junk():
    captured = {
        "https://cdn/seg1.ts": "r1",
        "https://cdn/master.m3u8?t=1": "r2",
        "https://cdn/trailer.mp4": "r3",
    }
    assert browser.pick_best(captured) == ("https://cdn/master.m3u8?t=1", "r2")
    assert browser.pick_best({"https://cdn/preview.mp4": "r"}) is None
    assert browser.pick_best({}) is None
