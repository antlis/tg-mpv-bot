from pathlib import Path

from src.config import Settings
from src.player import (
    _hook_env,
    _run_hook,
    _stop_current,
    _ytdl_cli_args,
    build_direct_command,
    build_launch_command,
    build_url_command,
)


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


def test_url_command_basic():
    s = _settings(mpv_runner="")
    cmd = build_url_command(s, "https://soundcloud.com/artist/track")
    assert cmd[1] == "https://soundcloud.com/artist/track"
    assert "--input-ipc-server=/tmp/sock" in cmd
    assert "--save-position-on-quit" in cmd
    assert not any(a.startswith("--ytdl-raw-options") for a in cmd)


def test_url_command_with_ytdl_options():
    s = _settings(mpv_runner="", ytdl_options="format-sort=res:1080")
    cmd = build_url_command(s, "https://youtu.be/x")
    assert "--ytdl-raw-options=format-sort=res:1080" in cmd


def test_cookies_only_for_gated_hosts():
    s = _settings(mpv_runner="", ytdl_cookies_browser="firefox")
    gated = build_url_command(s, "https://www.instagram.com/reel/x")
    assert "--ytdl-raw-options=cookies-from-browser=firefox" in gated
    # Logged-in YouTube cookies stall yt-dlp's extraction → must NOT be sent.
    open_site = build_url_command(s, "https://youtu.be/6gRXToZhO1A")
    assert not any("cookies" in a for a in open_site)
    # Not fooled by a lookalike domain.
    fake = build_url_command(s, "https://evilinstagram.com/x")
    assert not any("cookies" in a for a in fake)


def test_cookies_combine_with_global_options():
    s = _settings(
        mpv_runner="", ytdl_options="format-sort=res:1080", ytdl_cookies_browser="firefox"
    )
    cmd = build_url_command(s, "https://facebook.com/watch?v=1")
    assert "--ytdl-raw-options=format-sort=res:1080,cookies-from-browser=firefox" in cmd


def test_direct_command_single_url():
    s = _settings(mpv_runner="")
    cmd = build_direct_command(s, ["https://cdn.example/v.mp4"], "My Title", {})
    assert cmd[1] == "https://cdn.example/v.mp4"
    assert "--force-media-title=My Title" in cmd
    assert not any(a.startswith("--audio-file") for a in cmd)
    # resolved URLs expire — never key a resume position to one
    assert "--save-position-on-quit" not in cmd


def test_direct_command_split_streams():
    s = _settings(mpv_runner="")
    cmd = build_direct_command(s, ["https://v.example/v", "https://a.example/a"], "T", {})
    assert cmd[1] == "https://v.example/v"
    assert "--audio-file=https://a.example/a" in cmd


def test_direct_command_forwards_cdn_headers():
    # googlevideo 403s fetches whose UA doesn't match the minting client —
    # yt-dlp's http_headers must reach mpv.
    s = _settings(mpv_runner="")
    headers = {"User-Agent": "com.google.android.youtube/19", "Referer": "https://yt.example/w"}
    cmd = build_direct_command(s, ["https://v.example/v"], "T", headers)
    assert "--user-agent=com.google.android.youtube/19" in cmd
    assert "--referrer=https://yt.example/w" in cmd


def test_ytdl_cli_args_translation():
    s = _settings(ytdl_options="format-sort=res:1080,no-check-certificates")
    assert _ytdl_cli_args(s, "https://youtu.be/x") == [
        "--format-sort", "res:1080", "--no-check-certificates",
    ]


def test_ytdl_cli_args_cookies_gated_only():
    s = _settings(ytdl_cookies_browser="firefox")
    assert _ytdl_cli_args(s, "https://www.instagram.com/reel/x") == [
        "--cookies-from-browser", "firefox",
    ]
    assert _ytdl_cli_args(s, "https://youtu.be/x") == []


def test_stop_current_dead_socket_no_pkill(tmp_path):
    # No mpv at the socket and stray-killing off → must be a quiet no-op.
    s = _settings(mpv_socket=str(tmp_path / "no.sock"), kill_stray_mpv=False)
    _stop_current(s)  # must not raise


# ── pre/post-play hooks ──────────────────────────────────────────────


def test_hook_env_exposes_playlist_info():
    s = _settings(display=":7")
    env = _hook_env(s, "/media/shows/playlists/deadwood.m3u", "deadwood")
    assert env["PLAYLIST"] == "/media/shows/playlists/deadwood.m3u"
    assert env["PLAYLIST_NAME"] == "deadwood"
    assert env["MPV_SOCKET"] == "/tmp/sock"
    assert env["DISPLAY"] == ":7"


def test_run_hook_executes_with_env(tmp_path):
    out = tmp_path / "out"
    env = _hook_env(_settings(), "/x/futurama.m3u", "futurama")
    _run_hook("pre-play", f'echo "$PLAYLIST_NAME" > {out}', env)
    assert out.read_text().strip() == "futurama"


def test_run_hook_empty_is_noop():
    _run_hook("pre-play", "", {})  # must not raise


def test_run_hook_failure_never_raises(caplog):
    env = _hook_env(_settings(), "/x.m3u", "x")
    _run_hook("pre-play", "exit 3", env)            # non-zero exit
    _run_hook("pre-play", "/nonexistent-cmd-xyz", env)  # command not found
    assert any("hook" in r.message for r in caplog.records)
