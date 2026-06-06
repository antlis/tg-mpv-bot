from pathlib import Path

import pytest

from src.config import Settings
from src.player import (
    _hook_env,
    _run_hook,
    _stop_current,
    _ytdl_cli_args,
    build_fetch_command,
    build_launch_command,
    build_pipe_player_command,
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


def test_fetch_command_shape(tmp_path):
    s = _settings(mpv_runner="")
    info = tmp_path / "info.json"
    cmd = build_fetch_command(s, info)
    # downloads from the saved info JSON (no re-extraction), streams to stdout
    assert "--load-info-json" in cmd and str(info) in cmd
    assert cmd[-2:] == ["-o", "-"]
    assert "-f" not in cmd  # single-stream: yt-dlp uses the probed format


def test_fetch_command_per_format(tmp_path):
    s = _settings(mpv_runner="")
    cmd = build_fetch_command(s, tmp_path / "i.json", format_id="303")
    assert cmd[cmd.index("-f") + 1] == "303"


def test_fetch_command_keeps_network_args(tmp_path):
    # URLs are minted for the probe's network path — fetchers must use it too
    s = _settings(mpv_runner="", ytdl_options="force-ipv4,extractor-args=x=y")
    cmd = build_fetch_command(s, tmp_path / "i.json")
    assert "--force-ipv4" in cmd
    assert "--extractor-args" not in cmd  # no extraction happens here


def test_file_command_shape():
    from src.player import build_file_command
    s = _settings(mpv_runner="")
    cmd = build_file_command(s, Path("/tmp/tg-mpv-files/movie.mkv"), "movie")
    assert cmd[1] == "/tmp/tg-mpv-files/movie.mkv"  # positional file, not --playlist
    assert "--force-media-title=movie" in cmd
    assert "--save-position-on-quit" in cmd
    assert "--input-ipc-server=/tmp/sock" in cmd


def test_pipe_player_single_stream_stdin():
    s = _settings(mpv_runner="")
    cmd = build_pipe_player_command(s, "My Title")
    assert cmd[1] == "-"  # stdin
    assert "--force-media-title=My Title" in cmd
    assert "--cache=yes" in cmd
    assert "--input-ipc-server=/tmp/sock" in cmd
    assert not any(a.startswith("--audio-file") for a in cmd)


def test_subs_command_shape(tmp_path):
    from src.player import build_subs_command
    s = _settings(ytdl_sub_langs="en.*,ru.*", ytdl_options="force-ipv4")
    cmd = build_subs_command(s, tmp_path / "i.json")
    assert "--skip-download" in cmd and "--write-auto-subs" in cmd
    assert cmd[cmd.index("--sub-langs") + 1] == "en.*,ru.*"
    assert "--force-ipv4" in cmd  # same network path as the probe
    assert "--load-info-json" in cmd  # no re-extraction


def test_fetch_subtitles_skips_when_none_advertised(tmp_path):
    from src.player import fetch_subtitles
    s = _settings(ytdl_sub_langs="en.*")
    # no subtitles/automatic_captions in the info → no subprocess spawn
    assert fetch_subtitles(s, {"title": "x"}, tmp_path / "i.json") == []
    s_off = _settings(ytdl_sub_langs="")
    assert fetch_subtitles(s_off, {"subtitles": {"en": []}}, tmp_path / "i.json") == []


def test_pipe_player_start_offset():
    s = _settings(mpv_runner="")
    assert "--start=754" in build_pipe_player_command(s, "T", start=754.9)
    assert not any(a.startswith("--start") for a in build_pipe_player_command(s, "T"))
    assert not any(a.startswith("--start") for a in build_pipe_player_command(s, "T", start=0))


def test_pipe_player_sub_files():
    s = _settings(mpv_runner="")
    cmd = build_pipe_player_command(
        s, "T", sub_files=[Path("/tmp/tg-mpv-sub.en.vtt")]
    )
    assert "--sub-file=/tmp/tg-mpv-sub.en.vtt" in cmd


def test_pipe_player_split_streams_use_fds():
    # split A/V can't share one pipe (interleaved bytes = garbage): each
    # stream gets its own fd and mpv muxes them itself.
    s = _settings(mpv_runner="")
    cmd = build_pipe_player_command(s, "T", video_fd=7, audio_fd=9)
    assert cmd[1] == "fd://7"
    assert "--audio-file=fd://9" in cmd


def test_ytdl_cli_args_translation():
    s = _settings(ytdl_options="format-sort=res:1080,no-check-certificates")
    assert _ytdl_cli_args(s, "https://youtu.be/x") == [
        "--format-sort", "res:1080", "--no-check-certificates",
    ]


def test_ytdl_cli_args_value_with_equals_and_semicolons():
    # the lean-YouTube extractor-args value embeds '=' and ';' — only the
    # FIRST '=' splits key from value
    s = _settings(ytdl_options="extractor-args=youtube:player_client=android_vr;player_skip=webpage")
    assert _ytdl_cli_args(s, "https://youtu.be/x") == [
        "--extractor-args", "youtube:player_client=android_vr;player_skip=webpage",
    ]


def test_ytdl_cli_args_cookies_gated_only():
    s = _settings(ytdl_cookies_browser="firefox")
    assert _ytdl_cli_args(s, "https://www.instagram.com/reel/x") == [
        "--cookies-from-browser", "firefox",
    ]
    assert _ytdl_cli_args(s, "https://youtu.be/x") == []


def _probe_attempts(monkeypatch, settings, fail_first_with, second=({"title": "ok"}, "")):
    import src.player as player

    attempts = []

    def fake_probe(settings_, url, extra_args, timeout):
        attempts.append(extra_args)
        if len(attempts) == 1:
            return None, fail_first_with
        return second

    monkeypatch.setattr(player, "_run_probe", fake_probe)
    return attempts


@pytest.mark.parametrize("error", [
    "ERROR: Sign in to confirm you're not a bot.",
    "ERROR: Requested format is not available. Use --list-formats …",
    "site did not respond within 120s (rate-limited?)",
])
def test_probe_escalates_on_degraded_client(monkeypatch, error):
    # YouTube cycles failure modes on flagged IPs — every non-terminal
    # failure of the lean fast path must trigger the stock+cookies retry.
    import src.player as player

    s = _settings(ytdl_cookies_browser="firefox", ytdl_options="extractor-args=x")
    attempts = _probe_attempts(monkeypatch, s, error)
    info, _ = player.probe_url(s, "https://youtu.be/x")
    assert info == {"title": "ok"}
    assert "--extractor-args" in attempts[0]          # fast path first…
    assert "--cookies-from-browser" not in attempts[0]
    assert attempts[1] == ["--cookies-from-browser", "firefox"]  # …stock args + cookies


def test_probe_reports_escalation_progress(monkeypatch):
    import src.player as player

    s = _settings(ytdl_cookies_browser="firefox", ytdl_options="extractor-args=x")
    _probe_attempts(monkeypatch, s, "ERROR: Sign in to confirm you're not a bot.")
    stages = []
    player.probe_url(s, "https://youtu.be/x", progress=stages.append)
    assert stages == ["escalating"]  # the slow rung announces itself


def test_probe_escalation_keeps_network_args(monkeypatch):
    # falling back from the lean client must NOT fall back to a dead address
    # family — force-ipv4/proxy flags carry over into the retry.
    import src.player as player

    s = _settings(
        ytdl_cookies_browser="firefox",
        ytdl_options="force-ipv4,extractor-args=youtube:player_client=android_vr",
    )
    attempts = _probe_attempts(monkeypatch, s, "ERROR: Requested format is not available")
    player.probe_url(s, "https://youtu.be/x")
    assert attempts[1] == ["--force-ipv4", "--cookies-from-browser", "firefox"]
    assert "--extractor-args" not in attempts[1]  # client pinning is dropped


def test_probe_fails_fast_on_terminal_errors(monkeypatch):
    import src.player as player

    s = _settings(ytdl_cookies_browser="firefox")
    attempts = _probe_attempts(monkeypatch, s, "ERROR: Video unavailable")
    with pytest.raises(player.UrlPlaybackError, match="unavailable"):
        player.probe_url(s, "https://youtu.be/x")
    assert len(attempts) == 1  # no slow retry for an error no retry can fix


def test_update_ytdlp_reports_versions(monkeypatch):
    import src.player as player

    versions = iter(["2026.05.25", "2026.06.05"])  # before, after

    def fake_run(cmd, **kw):
        class R:
            returncode = 0
            stderr = ""
            stdout = next(versions) + "\n" if "--version" in cmd else ""
        return R()

    monkeypatch.setattr(player.subprocess, "run", fake_run)
    monkeypatch.setattr(player, "_ytdlp_bin", lambda: "/v/yt-dlp")
    monkeypatch.setattr(player.Path, "exists", lambda self: True)
    assert player.update_ytdlp() == "✅ yt-dlp updated: 2026.05.25 → 2026.06.05"


def test_update_ytdlp_pip_failure(monkeypatch):
    import src.player as player

    def fake_run(cmd, **kw):
        class R:
            returncode = 0 if "--version" in cmd else 1
            stderr = "resolution impossible"
            stdout = "2026.05.25\n"
        return R()

    monkeypatch.setattr(player.subprocess, "run", fake_run)
    monkeypatch.setattr(player, "_ytdlp_bin", lambda: "/v/yt-dlp")
    monkeypatch.setattr(player.Path, "exists", lambda self: True)
    assert player.update_ytdlp().startswith("❌ pip failed: resolution impossible")


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
