from pathlib import Path

from src.config import Settings
from src.player import build_launch_command


def _settings(**kw) -> Settings:
    base = dict(bot_token="x", mpv_socket="/tmp/sock", mpv_runner="/tmp/mpv-runner.sh")
    base.update(kw)
    return Settings(**base)


def test_uses_runner_when_present(tmp_path):
    runner = tmp_path / "mpv-runner.sh"
    runner.write_text("#!/bin/bash\nexec mpv \"$@\"\n")
    s = _settings(mpv_runner=str(runner))
    cmd = build_launch_command(s, Path("/media/show.m3u"))
    assert cmd[0] == "setsid"
    assert cmd[1] == str(runner)
    assert "--playlist=/media/show.m3u" in cmd
    assert "--input-ipc-server=/tmp/sock" in cmd
    assert "--force-window" in cmd


def test_falls_back_to_mpv_when_runner_absent(tmp_path):
    s = _settings(mpv_runner=str(tmp_path / "nonexistent.sh"))
    cmd = build_launch_command(s, Path("/media/show.m3u"))
    assert cmd == [
        "setsid",
        "mpv",
        "--playlist=/media/show.m3u",
        "--input-ipc-server=/tmp/sock",
        "--force-window",
    ]


def test_empty_runner_uses_mpv():
    s = _settings(mpv_runner="")
    cmd = build_launch_command(s, Path("/x.m3u"))
    assert cmd[1] == "mpv"
