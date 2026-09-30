"""The panel's 📥 upload: source resolution, progress parsing, yt-dlp argv."""

import sys
from pathlib import Path

import pytest

from src import download
from src.config import Settings


def _settings(**kw):
    return Settings(bot_token="t", **kw)


def test_parse_progress_line():
    got = download.parse_progress_line("PROG| 42.5%|3.10MiB/s|00:12")
    assert got == {"percent": 42.5, "speed": "3.10MiB/s", "eta": "00:12"}
    assert download.parse_progress_line("\x1b[0mPROG|100%|1MiB/s|00:00")["percent"] == 100.0
    assert download.parse_progress_line("[Merger] Merging formats") is None
    assert download.parse_progress_line("PROG|bad") is None
    assert download.parse_progress_line("PROG|N/A|N/A|N/A")["percent"] is None


def test_render_progress_bar():
    assert download.render_progress_bar(0) == "░" * 10
    assert download.render_progress_bar(50) == "█" * 5 + "░" * 5
    assert download.render_progress_bar(100) == "█" * 10
    assert download.render_progress_bar(None) == "░" * 10
    assert download.render_progress_bar(250) == "█" * 10


def test_resolve_source(tmp_path):
    f = tmp_path / "ep.mkv"
    f.write_text("x")
    # a library file beats any history
    assert download.resolve_source(str(f), "https://x/y") == download.Source("file", str(f))
    # streams: the URL the user sent, not mpv's own (minted/fd) path
    assert download.resolve_source("fd://3", "https://site/page") == download.Source(
        "url", "https://site/page"
    )
    assert download.resolve_source("https://cdn/x.m3u8", "https://site/page").value == (
        "https://site/page"
    )
    # no history → fall back to mpv's http path; a playlist target isn't a URL
    assert download.resolve_source("https://cdn/x.mp4", None).value == "https://cdn/x.mp4"
    assert download.resolve_source("fd://3", "/lists/a.m3u") is None
    assert download.resolve_source(None, None) is None


def test_resolve_source_after_mpv_exited(tmp_path):
    movie = tmp_path / "Movie (2010).mkv"
    movie.write_text("x")
    single = tmp_path / "movie.m3u"
    single.write_text(f"#EXTM3U\n{movie}\n")
    multi = tmp_path / "show.m3u"
    multi.write_text("a.mkv\nb.mkv\n")
    # mpv gone, one-file playlist → that file; URL history → the URL
    assert download.resolve_source(None, str(single)) == download.Source("file", str(movie))
    assert download.resolve_source(None, "https://site/p").kind == "url"
    # ambiguous multi-episode playlist → nothing to guess
    assert download.resolve_source(None, str(multi)) is None
    # single-entry playlist whose file is gone (deleted / disk unmounted)
    gone = tmp_path / "gone.m3u"
    gone.write_text("/nope/x.mkv\n")
    assert download.resolve_source(None, str(gone)) == download.Source("missing", "/nope/x.mkv")
    # mpv was playing a local file that has since been deleted
    assert download.resolve_source("/gone/file.mp4", None) == download.Source(
        "missing", "/gone/file.mp4"
    )


def test_friendly_error():
    assert "no longer available" in download.friendly_error("ERROR: [youtube] x: Video unavailable")
    assert "404" in download.friendly_error("ERROR: HTTP Error 404: Not Found")
    assert download.friendly_error("something odd") == "something odd"


def test_upload_limit_depends_on_local_server():
    assert download.upload_limit(_settings()) == download.CLOUD_LIMIT
    assert download.upload_limit(_settings(api_server_url="http://x:8082")) == download.LOCAL_LIMIT


def test_download_format_without_ffmpeg(tmp_path, monkeypatch):
    monkeypatch.setattr(download.player, "_which", lambda name: None)
    cmd = download.build_download_command(_settings(), "https://x/y", tmp_path)
    assert "--merge-output-format" not in cmd
    assert cmd[cmd.index("-f") + 1] == "b[height<=1080]/b"


def test_build_download_command(tmp_path, monkeypatch):
    monkeypatch.setattr(download.player, "_which", lambda name: "/usr/bin/ffmpeg")
    cmd = download.build_download_command(
        _settings(ytdl_format="best"), "https://www.youtube.com/watch?v=abc", tmp_path
    )
    assert "--no-playlist" in cmd and "--newline" in cmd
    assert cmd[cmd.index("-f") + 1] == "best"
    assert cmd[cmd.index("--merge-output-format") + 1] == "mp4"
    assert cmd[-2:] == ["--", "https://www.youtube.com/watch?v=abc"]
    assert str(tmp_path) in cmd[cmd.index("-o") + 1]


async def test_download_reports_progress_and_returns_file(tmp_path, monkeypatch):
    script = tmp_path / "fake.py"
    script.write_text(
        "import sys, pathlib\n"
        "out = sys.argv[sys.argv.index('-o') + 1]\n"
        "print('PROG| 10.0%|1MiB/s|00:09', flush=True)\n"
        "print('[Merger] Merging formats into x.mp4', flush=True)\n"
        "pathlib.Path(out.replace('%(title).80B [%(id)s].%(ext)s', 'v.mp4')).write_bytes(b'0'*10)\n"
    )
    monkeypatch.setattr(
        download, "build_download_command",
        lambda s, u, d: [sys.executable, str(script), "-o", str(d / "%(title).80B [%(id)s].%(ext)s")],
    )
    seen = []

    async def on_progress(info):
        seen.append(info)

    out_dir = tmp_path / "out"
    out_dir.mkdir()
    out = await download.download(_settings(), "https://x", out_dir, on_progress)
    assert out.name == "v.mp4"
    assert seen[0]["percent"] == 10.0
    assert {"stage": "merging"} in seen


async def test_download_failure_surfaces_error_line(tmp_path, monkeypatch):
    monkeypatch.setattr(
        download, "build_download_command",
        lambda s, u, d: [sys.executable, "-c",
                         "print('ERROR: Video unavailable'); raise SystemExit(1)"],
    )

    async def on_progress(info):
        pass

    with pytest.raises(download.DownloadError, match="no longer available"):
        await download.download(_settings(), "https://x", tmp_path, on_progress)


def test_find_output_ignores_partials(tmp_path):
    (tmp_path / "a.mp4.part").write_bytes(b"0" * 100)
    assert download._find_output(tmp_path) is None
    (tmp_path / "a.mp4").write_bytes(b"0" * 5)
    assert download._find_output(tmp_path) == Path(tmp_path / "a.mp4")
