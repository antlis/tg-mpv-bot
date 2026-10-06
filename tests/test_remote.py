"""Remote play API (src/remote.py): request validation, the bearer token, and the play/status/ctl calls."""

import pytest
from aiohttp.test_utils import TestClient, TestServer

from src import player, remote
from src.config import Settings
from src.mpv_ipc import CTL_ACTIONS, MpvError, MpvNotRunning

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
    assert (await client.post("/status", headers=AUTH)).status == 405
    assert (await client.get("/ctl", headers=AUTH)).status == 405
    assert (await client.post("/other", json={}, headers=AUTH)).status == 404


# ── GET /status ────────────────────────────────────────────────


class FakeMpvClient:
    """Stands in for MpvClient inside remote.py: fixed status, recorded calls.

    ``__getattr__`` returns a recorder for any control method the shared action
    table might reach, so one fake covers every action without listing them.
    """

    status: dict = {"playing": True, "title": "Test Clip", "position": 12.5, "paused": False}
    error: Exception | None = None
    calls: list = []

    def __init__(self, socket_path: str) -> None:
        self.socket_path = socket_path

    def read_status(self):
        if self.error is not None:
            raise self.error
        return dict(self.status)

    def __getattr__(self, name: str):
        if name.startswith("_"):
            raise AttributeError(name)

        def call(*args):
            if self.error is not None:
                raise self.error
            self.calls.append((name, args))

        return call


@pytest.fixture
def fake_mpv(monkeypatch):
    monkeypatch.setattr(remote, "MpvClient", FakeMpvClient)
    monkeypatch.setattr(FakeMpvClient, "calls", [])
    monkeypatch.setattr(FakeMpvClient, "error", None)
    monkeypatch.setattr(
        FakeMpvClient, "status", {"playing": True, "title": "Test Clip", "position": 12.5, "paused": False}
    )
    return FakeMpvClient


async def test_status_requires_the_token(client, fake_mpv):
    for headers in ({}, {"Authorization": "Bearer wrong"}, {"Authorization": TOKEN}):
        resp = await client.get("/status", headers=headers)
        assert resp.status == 401


async def test_status_reports_now_playing(client, fake_mpv):
    resp = await client.get("/status", headers=AUTH)
    assert resp.status == 200
    body = await resp.json()
    assert body["ok"] is True
    assert body["playing"] is True
    assert body["title"] == "Test Clip"
    assert body["position"] == 12.5


async def test_status_without_media(client, fake_mpv, monkeypatch):
    monkeypatch.setattr(FakeMpvClient, "status", {"playing": False, "title": None})
    body = await (await client.get("/status", headers=AUTH)).json()
    assert body == {"ok": True, "playing": False, "title": None}


async def test_status_when_mpv_is_down(client, fake_mpv, monkeypatch):
    monkeypatch.setattr(FakeMpvClient, "error", MpvNotRunning("mpv is not running"))
    resp = await client.get("/status", headers=AUTH)
    assert resp.status == 503
    assert (await resp.json())["error"] == "mpv is not running"


async def test_status_hides_unexpected_errors(client, fake_mpv, monkeypatch):
    monkeypatch.setattr(FakeMpvClient, "error", RuntimeError("secret path /home/me/..."))
    resp = await client.get("/status", headers=AUTH)
    assert resp.status == 500
    assert "secret" not in await resp.text()


# ── POST /ctl ──────────────────────────────────────────────────


async def test_ctl_requires_the_token(client, fake_mpv):
    for headers in ({}, {"Authorization": "Bearer wrong"}):
        resp = await client.post("/ctl", json={"action": "stop"}, headers=headers)
        assert resp.status == 401
    assert fake_mpv.calls == []  # nothing was sent to mpv


async def test_ctl_runs_the_action(client, fake_mpv):
    resp = await client.post("/ctl", json={"action": "stop"}, headers=AUTH)
    assert resp.status == 200
    assert await resp.json() == {"ok": True, "action": "stop"}
    assert fake_mpv.calls == [("quit", ())]


async def test_ctl_sends_seek_and_volume_to_mpv(client, fake_mpv):
    await client.post("/ctl", json={"action": "back"}, headers=AUTH)
    await client.post("/ctl", json={"action": "fwd"}, headers=AUTH)
    await client.post("/ctl", json={"action": "volup"}, headers=AUTH)
    assert fake_mpv.calls == [("seek", (-10,)), ("seek", (30,)), ("adjust_volume", (10,))]


async def test_ctl_seek_goes_to_an_exact_position(client, fake_mpv):
    resp = await client.post("/ctl", json={"action": "seek", "position": 83.5}, headers=AUTH)
    assert resp.status == 200
    assert (await resp.json()) == {"ok": True, "action": "seek"}
    assert fake_mpv.calls == [("seek_absolute", (83.5,))]


@pytest.mark.parametrize("position", [None, "10", True, -1, 86400 * 7 + 1, [5]])
async def test_ctl_seek_rejects_a_bad_position(client, fake_mpv, position):
    resp = await client.post("/ctl", json={"action": "seek", "position": position}, headers=AUTH)
    assert resp.status == 400
    assert fake_mpv.calls == []


async def test_ctl_seek_without_position_is_400(client, fake_mpv):
    resp = await client.post("/ctl", json={"action": "seek"}, headers=AUTH)
    assert resp.status == 400
    assert fake_mpv.calls == []


async def test_ctl_accepts_every_action_the_table_offers(client, fake_mpv):
    """Panel and API read the same table — nothing may be callable from only one."""
    for action in sorted(CTL_ACTIONS):
        resp = await client.post("/ctl", json={"action": action}, headers=AUTH)
        assert resp.status == 200, f"{action} was rejected"
    assert len(fake_mpv.calls) > 0


async def test_ctl_rejects_an_unknown_action_and_lists_the_valid_ones(client, fake_mpv):
    resp = await client.post("/ctl", json={"action": "explode"}, headers=AUTH)
    assert resp.status == 400
    err = (await resp.json())["error"]
    assert "toggle" in err and "stop" in err and "seek" in err
    assert fake_mpv.calls == []


@pytest.mark.parametrize("body", [{"action": 5}, {"action": "stop "}, {}, ["stop"], "stop", 7])
async def test_ctl_rejects_a_malformed_body(client, fake_mpv, body):
    resp = await client.post("/ctl", json=body, headers=AUTH)
    assert resp.status == 400
    assert fake_mpv.calls == []


async def test_ctl_rejects_a_body_that_is_not_json(client, fake_mpv):
    resp = await client.post("/ctl", data="stop", headers=AUTH)
    assert resp.status == 400
    assert fake_mpv.calls == []


async def test_ctl_without_mpv_is_503(client, fake_mpv, monkeypatch):
    monkeypatch.setattr(FakeMpvClient, "error", MpvNotRunning("mpv is not running"))
    resp = await client.post("/ctl", json={"action": "toggle"}, headers=AUTH)
    assert resp.status == 503


async def test_ctl_reports_the_mpv_error(client, fake_mpv, monkeypatch):
    monkeypatch.setattr(FakeMpvClient, "error", MpvError("property unavailable"))
    resp = await client.post("/ctl", json={"action": "toggle"}, headers=AUTH)
    assert resp.status == 422
    assert (await resp.json())["error"] == "property unavailable"


async def test_ctl_hides_unexpected_errors(client, fake_mpv, monkeypatch):
    monkeypatch.setattr(FakeMpvClient, "error", RuntimeError("secret path /home/me/..."))
    resp = await client.post("/ctl", json={"action": "toggle"}, headers=AUTH)
    assert resp.status == 500
    assert "secret" not in await resp.text()


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
