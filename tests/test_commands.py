"""Tests for the pure helpers in src.commands (no Telegram / IPC needed)."""

import pytest

from src.commands import _URL_RE, _episode_list, _episodes_text, _parse_goto, _parse_sleep


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


def test_episodes_text():
    assert _episodes_text(["a", "b"], 0) == "📜 2 items (now: #1) — tap to jump:"
    assert _episodes_text(["a"], None) == "📜 1 items — tap to jump:"
