"""Tests for the pure helpers in src.commands (no Telegram / IPC needed)."""

from src.commands import _episode_list, _episodes_text


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


def test_episodes_text():
    assert _episodes_text(["a", "b"], 0) == "📜 2 items (now: #1) — tap to jump:"
    assert _episodes_text(["a"], None) == "📜 1 items — tap to jump:"
