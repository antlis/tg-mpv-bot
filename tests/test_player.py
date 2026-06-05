from pathlib import Path

from src.config import Settings
from src.player import _hook_env, _run_hook, build_launch_command


def _settings(**kw) -> Settings:
    base = dict(bot_token="x", mpv_socket="/tmp/sock", mpv_runner="/tmp/mpv-runner.sh")
    base.update(kw)
    return Settings(**base)


def test_uses_runner_when_present(tmp_path):
    runner = tmp_path / "mpv-runner.sh"
    runner.write_text("#!/bin/bash\nexec mpv \"$@\"\n")
    s = _settings(mpv_runner=str(runner))
    cmd = build_launch_command(s, Path("/media/show.m3u"))
    assert cmd[0] == str(runner)  # no setsid prefix; Popen(start_new_session) detaches
    assert "--playlist=/media/show.m3u" in cmd
    assert "--input-ipc-server=/tmp/sock" in cmd
    assert "--force-window" in cmd
    assert "--save-position-on-quit" in cmd


def test_falls_back_to_mpv_when_runner_absent(tmp_path):
    s = _settings(mpv_runner=str(tmp_path / "nonexistent.sh"))
    cmd = build_launch_command(s, Path("/media/show.m3u"))
    # mpv may resolve to an absolute path depending on the host
    assert cmd[0] == "mpv" or cmd[0].endswith("/mpv")
    assert cmd[1:] == [
        "--playlist=/media/show.m3u",
        "--input-ipc-server=/tmp/sock",
        "--force-window",
        "--save-position-on-quit",
    ]


def test_empty_runner_uses_mpv():
    s = _settings(mpv_runner="")
    cmd = build_launch_command(s, Path("/x.m3u"))
    assert cmd[0] == "mpv" or cmd[0].endswith("/mpv")


# ── pre/post-play hooks ──────────────────────────────────────────────


def test_hook_env_exposes_playlist_info():
    s = _settings(display=":7")
    env = _hook_env(s, Path("/media/shows/playlists/deadwood.m3u"))
    assert env["PLAYLIST"] == "/media/shows/playlists/deadwood.m3u"
    assert env["PLAYLIST_NAME"] == "deadwood"
    assert env["MPV_SOCKET"] == "/tmp/sock"
    assert env["DISPLAY"] == ":7"


def test_run_hook_executes_with_env(tmp_path):
    out = tmp_path / "out"
    env = _hook_env(_settings(), Path("/x/futurama.m3u"))
    _run_hook("pre-play", f'echo "$PLAYLIST_NAME" > {out}', env)
    assert out.read_text().strip() == "futurama"


def test_run_hook_empty_is_noop():
    _run_hook("pre-play", "", {})  # must not raise


def test_run_hook_failure_never_raises(caplog):
    env = _hook_env(_settings(), Path("/x.m3u"))
    _run_hook("pre-play", "exit 3", env)            # non-zero exit
    _run_hook("pre-play", "/nonexistent-cmd-xyz", env)  # command not found
    assert any("hook" in r.message for r in caplog.records)
