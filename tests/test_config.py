import os
from pathlib import Path

import pytest

from src import config
from src.config import Settings, _parse_int_list, _parse_path_list, get_settings


def test_parse_int_list_basic():
    assert _parse_int_list("1, 2 ,3") == [1, 2, 3]


def test_parse_int_list_empty():
    assert _parse_int_list("") == []
    assert _parse_int_list(None) == []
    assert _parse_int_list(" , ,") == []


def test_parse_path_list():
    raw = f"/a/b{os.pathsep}~/c"
    parsed = _parse_path_list(raw)
    assert parsed[0] == Path("/a/b")
    assert parsed[1] == Path("~/c").expanduser()


def test_parse_path_list_empty():
    assert _parse_path_list("") is None
    assert _parse_path_list(None) is None


def test_is_restricted():
    assert Settings(bot_token="x", allowed_users=[1]).is_restricted is True
    assert Settings(bot_token="x", allowed_users=[]).is_restricted is False


def test_missing_bot_token_raises(monkeypatch):
    monkeypatch.delenv("BOT_TOKEN", raising=False)
    get_settings.cache_clear()
    with pytest.raises(SystemExit):
        get_settings()
    get_settings.cache_clear()


def test_get_settings_reads_env(monkeypatch):
    monkeypatch.setenv("BOT_TOKEN", "tok")
    monkeypatch.setenv("ALLOWED_USERS", "10,20")
    monkeypatch.setenv("MPV_SOCKET", "/tmp/custom-socket")
    monkeypatch.setenv("PLAYLIST_DIRS", "/x/playlists")
    monkeypatch.setenv("PRE_PLAY_HOOK", "i3-msg workspace 7")
    monkeypatch.setenv("POST_PLAY_HOOK", "notify-send hi")
    monkeypatch.setenv("KILL_STRAY_MPV", "0")
    get_settings.cache_clear()
    s = get_settings()
    assert s.bot_token == "tok"
    assert s.allowed_users == [10, 20]
    assert s.mpv_socket == "/tmp/custom-socket"
    assert s.playlist_dirs == [Path("/x/playlists")]
    assert s.pre_play_hook == "i3-msg workspace 7"
    assert s.post_play_hook == "notify-send hi"
    assert s.kill_stray_mpv is False  # default is True
    get_settings.cache_clear()


def test_default_playlist_dirs_shape(monkeypatch):
    monkeypatch.setenv("VIDEOS_DIR", "/media/vids")
    dirs = config._default_playlist_dirs()
    assert dirs == [
        Path("/media/vids/cartoons/playlists"),
        Path("/media/vids/movie/playlists"),
        Path("/media/vids/shows/playlists"),
        Path("/media/vids/tutorials/playlists"),
    ]
