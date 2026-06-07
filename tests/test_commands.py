"""Tests for the pure helpers in src.commands (no Telegram / IPC needed)."""

from pathlib import Path

import pytest

from src.commands import (
    _URL_RE,
    _episode_list,
    _episodes_text,
    _local_api_path,
    _parse_goto,
    _parse_sleep,
    _status_text,
)
from src.config import get_settings
from src.mpv_ipc import MpvError


class FakePlaylistClient:
    def __init__(self, items):
        self._items = items

    def get_playlist(self):
        return self._items


def test_episode_list_names_and_current():
    items = [
        {"filename": "/v/Show.S01E01.1080p.WEB.x265.mkv"},
        {"filename": "/v/Show.S01E02.1080p.WEB.x265.mkv", "current": True},
        {"filename": "https://example.com/stream", "title": "Live Stream"},
    ]
    names, current = _episode_list(FakePlaylistClient(items))
    assert current == 1
    assert names[0] == "Show S01E01"     # prettified, junk stripped
    assert names[2] == "Live Stream"     # title wins over filename


def test_episode_list_empty():
    assert _episode_list(FakePlaylistClient([])) == ([], None)


@pytest.mark.parametrize("raw,expected", [
    ("1:23:45", ("time", 5025.0)),
    ("23:45", ("time", 1425.0)),
    ("90", ("time", 90.0)),
    ("0", ("time", 0.0)),
    ("75%", ("percent", 75.0)),
    ("0%", ("percent", 0.0)),
    ("100%", ("percent", 100.0)),
])
def test_parse_goto_valid(raw, expected):
    assert _parse_goto(raw) == expected


@pytest.mark.parametrize("raw", [
    "", "  ", "abc", "1:2:3:4", "-5", "1:-2", "101%", "-1%", "x%",
])
def test_parse_goto_invalid(raw):
    assert _parse_goto(raw) is None


@pytest.mark.parametrize("text,matches", [
    ("https://soundcloud.com/forss/flickermood", True),
    ("http://youtu.be/xyz", True),
    ("watch this https://youtu.be/xyz", False),   # URL must be the whole message
    ("https://a.com/x and more", False),
    ("ftp://a.com/x", False),
    ("/mpv_play deadwood", False),
    ("-not-a-url", False),
])
def test_url_re(text, matches):
    assert bool(_URL_RE.match(text)) is matches


@pytest.mark.parametrize("raw,minutes", [
    ("45", 45), ("45m", 45), ("90min", 90), ("2h", 120), ("1.5h", 90),
    ("0", None), ("25h", None), ("soon", None), ("", None), ("-5", None),
])
def test_parse_sleep(raw, minutes):
    assert _parse_sleep(raw) == minutes


def test_prune_api_files(tmp_path):
    from src.commands import _prune_api_files
    token = tmp_path / "123:AA"
    for sub in ("videos", "music"):
        (token / sub).mkdir(parents=True)
    (token / "td.binlog").write_text("bookkeeping")  # must survive
    old_video = token / "videos" / "old.mp4"
    old_song = token / "music" / "old.mp3"
    current = token / "videos" / "now-playing.mp4"
    for f in (old_video, old_song, current):
        f.write_text("x")

    _prune_api_files(current)
    assert current.exists()                  # the playing file survives
    assert not old_video.exists()            # same-dir sibling pruned
    assert not old_song.exists()             # other media dirs pruned too
    assert (token / "td.binlog").exists()    # server bookkeeping untouched


def test_prune_api_files_ignores_non_media_paths(tmp_path):
    from src.commands import _prune_api_files
    f = tmp_path / "tg-mpv-files" / "movie.mp4"
    f.parent.mkdir()
    f.write_text("x")
    sibling = tmp_path / "tg-mpv-files" / "other.mp4"
    sibling.write_text("x")
    _prune_api_files(f)  # /tmp copy path — not the server layout
    assert sibling.exists()


def test_local_api_path_mapping(monkeypatch):
    monkeypatch.setenv("BOT_TOKEN", "x")
    monkeypatch.setenv("API_LOCAL_FILES_DIR", "/srv/botapi")
    get_settings.cache_clear()
    assert _local_api_path("/var/lib/telegram-bot-api/123:AA/videos/file_1.mp4") == Path(
        "/srv/botapi/123:AA/videos/file_1.mp4"
    )
    assert _local_api_path("relative/cloud/path.mp4") is None  # cloud-style path
    monkeypatch.delenv("API_LOCAL_FILES_DIR")
    get_settings.cache_clear()
    assert _local_api_path("/var/lib/telegram-bot-api/x") is None  # mapping unset
    get_settings.cache_clear()


def test_episodes_text():
    assert _episodes_text(["a", "b"], 0) == "📜 2 items (now: #1) — tap to jump:"
    assert _episodes_text(["a"], None) == "📜 1 items — tap to jump:"


class FakePropClient:
    """get_property backed by a dict; missing keys raise MpvError like real mpv."""

    def __init__(self, props):
        self._props = props

    def get_property(self, name):
        if name in self._props:
            return self._props[name]
        raise MpvError(f"property unavailable: {name}")


def test_status_text_radio_shows_icy_title():
    client = FakePropClient({
        "media-title": "Record Techno",          # forced station name
        "metadata/icy-title": "SAMDMA - Drip Trip",
        "time-pos": 42.0,
        "pause": False,
        "volume": 100.0,
    })
    text = _status_text(client)
    assert "🎬 Record Techno" in text
    assert "🎵 SAMDMA - Drip Trip" in text
    assert text.index("🎬") < text.index("🎵")  # station first, track under it


def test_status_text_no_icy_for_local_files():
    client = FakePropClient({
        "media-title": "Show S01E01",
        "time-pos": 10.0,
        "duration": 1200.0,
        "pause": False,
    })
    assert "🎵" not in _status_text(client)


def test_status_text_icy_equal_to_title_not_duplicated():
    client = FakePropClient({
        "media-title": "SAMDMA - Drip Trip",     # no forced title: media-title IS the icy title
        "metadata/icy-title": "SAMDMA - Drip Trip",
        "pause": False,
    })
    assert _status_text(client).count("SAMDMA - Drip Trip") == 1
