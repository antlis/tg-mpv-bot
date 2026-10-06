"""Remote play API (src/remote.py): request validation, the bearer token, and the play call."""

import pytest
from aiohttp.test_utils import TestClient, TestServer

from src import player, remote
from src.config import Settings

TOKEN = "s3cret-token"
VIDEO = "https://www.youtube.com/watch?v=dQw4w9WgXcQ"


def _settings(**kw) -> Settings:
    return Settings(bot_token="x", remote_play_token=kw.pop("token", TOKEN), **kw)


# ── parse_play_request ────────────────────────────────────────


def test_parse_valid_url_without_start():
    assert remote.parse_play_request({"url": VIDEO}) == (VIDEO, None)


def test_parse_start_in_seconds():
    assert remote.parse_play_request({"url": VIDEO, "start": 83.5}) == (VIDEO, 83.5)
    assert remote.parse_play_request({"url": VIDEO, "start": 90}) == (VIDEO, 90.0)


def test_parse_zero_or_null_start_means_from_the_beginning():
    assert remote.parse_play_request({"url": VIDEO, "start": 0}) == (VIDEO, None)
    assert remote.parse_play_request({"url": VIDEO, "start": None}) == (VIDEO, None)


@pytest.mark.parametrize(
    "url",
    [
        "file:///etc/passwd",
        "/home/me/video.mkv",
        "ytdl://https://example.com/x",
        "edl://!no_clip;/etc/passwd",
        "https://example.com/a b",  # whitespace
        "https://example.com/x\n--script=evil.lua",
        "https://" + "a" * remote.MAX_URL_LEN,
        "",
        None,
        42,
        ["https://example.com"],
    ],
)
def test_parse_rejects_anything_but_a_web_link(url):
    with pytest.raises(ValueError):
        remote.parse_play_request({"url": url})


@pytest.mark.parametrize("start", [-1, "30", True, [5], 86400 * 8, float("nan")])
def test_parse_rejects_bad_start(start):
    with pytest.raises(ValueError):
        remote.parse_play_request({"url": VIDEO, "start": start})


@pytest.mark.parametrize("body", [None, [], "https://example.com", 5])
def test_parse_rejects_non_objects(body):
    with pytest.raises(ValueError):
        remote.parse_play_request(body)


# ── the HTTP endpoint ─────────────────────────────────────────


@pytest.fixture
async def client():
    server = TestServer(remote.make_app(_settings()))
    async with TestClient(server) as c:
        yield c


AUTH = {"Authorization": f"Bearer {TOKEN}"}


async def test_play_requires_the_token(client, monkeypatch):
    calls = []
    monkeypatch.setattr(player, "play_url", lambda *a: calls.append(a) or "t")
    for headers in ({}, {"Authorization": "Bearer wrong"}, {"Authorization": TOKEN}, {"Authorization": "Basic " + TOKEN}):
        resp = await client.post("/play", json={"url": VIDEO}, headers=headers)
        assert resp.status == 401
    assert calls == []  # nothing was launched


async def test_play_starts_the_link(client, monkeypatch):
    calls = []

    def fake(settings, url, progress, start):
        calls.append((url, start))
        return "Never Gonna Give You Up"

    monkeypatch.setattr(player, "play_url", fake)
    resp = await client.post("/play", json={"url": VIDEO, "start": 83.5}, headers=AUTH)
    assert resp.status == 200
    assert await resp.json() == {"ok": True, "title": "Never Gonna Give You Up"}
    assert calls == [(VIDEO, 83.5)]


async def test_play_rejects_a_bad_request_without_launching(client, monkeypatch):
    calls = []
    monkeypatch.setattr(player, "play_url", lambda *a: calls.append(a) or "t")
    bad = await client.post("/play", json={"url": "file:///etc/passwd"}, headers=AUTH)
    assert bad.status == 400
    not_json = await client.post("/play", data="not json", headers=AUTH)
    assert not_json.status == 400
    assert calls == []


async def test_play_reports_a_link_that_cannot_play(client, monkeypatch):
    def fake(*a):
        raise player.UrlPlaybackError("Video unavailable")

    monkeypatch.setattr(player, "play_url", fake)
    resp = await client.post("/play", json={"url": VIDEO}, headers=AUTH)
    assert resp.status == 422
    assert (await resp.json())["error"] == "Video unavailable"


async def test_play_hides_unexpected_errors(client, monkeypatch):
    def fake(*a):
        raise RuntimeError("secret path /home/me/...")

    monkeypatch.setattr(player, "play_url", fake)
    resp = await client.post("/play", json={"url": VIDEO}, headers=AUTH)
    assert resp.status == 500
    assert "secret" not in await resp.text()


async def test_other_methods_and_paths_are_not_served(client):
    assert (await client.get("/play", headers=AUTH)).status == 405
    assert (await client.post("/other", json={}, headers=AUTH)).status == 404


# ── enabling ──────────────────────────────────────────────────


async def test_off_without_a_token():
    assert await remote.start(_settings(token="")) is None


def test_settings_defaults_keep_it_off_and_local(monkeypatch):
    from src.config import get_settings

    monkeypatch.setenv("BOT_TOKEN", "x")
    monkeypatch.delenv("REMOTE_PLAY_TOKEN", raising=False)
    monkeypatch.delenv("REMOTE_PLAY_BIND", raising=False)
    get_settings.cache_clear()
    s = get_settings()
    assert s.remote_play_token == ""
    assert s.remote_play_bind == "127.0.0.1:8085"
    get_settings.cache_clear()
