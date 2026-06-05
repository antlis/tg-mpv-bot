"""Integration tests against a real ``mpv --idle`` instance.

The fake server in test_mpv_ipc.py checks our client logic; these check the
protocol assumptions against the genuine article (CI installs mpv). Skipped
wherever mpv isn't installed.
"""

import shutil
import subprocess
import time

import pytest

from src.mpv_ipc import MpvClient, MpvError

pytestmark = pytest.mark.skipif(shutil.which("mpv") is None, reason="mpv not installed")


@pytest.fixture
def real_mpv(tmp_path):
    sock = tmp_path / "mpv.sock"
    proc = subprocess.Popen(
        [
            "mpv", "--idle=yes", "--no-video", "--no-audio", "--no-terminal",
            "--no-config", f"--input-ipc-server={sock}",
        ],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    try:
        for _ in range(100):
            if sock.exists():
                break
            time.sleep(0.1)
        else:
            pytest.fail("mpv did not create its IPC socket")
        yield str(sock)
    finally:
        proc.terminate()
        proc.wait(timeout=5)


def test_property_roundtrip(real_mpv):
    client = MpvClient(real_mpv)
    client.set_property("volume", 73)
    assert client.get_property("volume") == 73


def test_pause_toggle(real_mpv):
    client = MpvClient(real_mpv)
    client.set_pause(False)
    assert client.toggle_pause() is True
    assert client.toggle_pause() is False


def test_adjust_volume_clamps(real_mpv):
    client = MpvClient(real_mpv)
    client.set_property("volume", 125)
    assert client.adjust_volume(50) == 130  # upper clamp against real mpv


def test_night_mode_filter(real_mpv):
    client = MpvClient(real_mpv)
    assert client.toggle_night() is True   # real af add @night:loudnorm
    assert client.toggle_night() is False  # and removal by label


def test_unknown_property_raises(real_mpv):
    client = MpvClient(real_mpv)
    with pytest.raises(MpvError):
        client.get_property("definitely-not-a-property")


def test_idle_playlist_empty(real_mpv):
    client = MpvClient(real_mpv)
    assert client.get_property("playlist-count") == 0
    assert client.get_playlist() == []
