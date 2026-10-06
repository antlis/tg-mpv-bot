"""The remote queue (src/remote_queue.py): items play in order, and anything else wins."""

import asyncio

import pytest
from aiohttp.test_utils import TestClient, TestServer

from src import mpv_ipc, player, remote, remote_queue
from src.config import Settings
from src.mpv_ipc import MpvNotRunning

TOKEN = "s3cret-token"
AUTH = {"Authorization": f"Bearer {TOKEN}"}
LINKS = [f"https://www.youtube.com/watch?v=video{i:06d}" for i in range(4)]


class Fake:
    """Stands in for player.play_url and mpv: each launch plays out a script, then mpv is gone."""

    def __init__(self) -> None:
        self.launches: list[tuple[str, float | None]] = []
        self.epoch = 0
        self.script: list[dict] = []
        self.next_script = lambda url: [{"playing": True, "position": 97.0, "duration": 100.0}]
        self.fail: set[str] = set()

    def play_url(self, settings, url, progress, start):
        if url in self.fail:
            raise player.UrlPlaybackError("Video unavailable")
        self.launches.append((url, start))
        self.epoch += 1
        self.script = list(self.next_script(url))
        return f"title of {url[-3:]}"

    def read_status(self, _client=None):
        if not self.script:
            raise MpvNotRunning("mpv is not running")
        return self.script.pop(0)


@pytest.fixture
def fake(monkeypatch):
    f = Fake()
    monkeypatch.setattr(player, "play_url", f.play_url)
    monkeypatch.setattr(player, "playback_epoch", lambda: f.epoch)
    monkeypatch.setattr(remote_queue, "MpvClient", lambda path: type("C", (), {"read_status": lambda s: f.read_status()})())
    monkeypatch.setattr(remote_queue, "POLL", 0.01)
    monkeypatch.setattr(remote_queue, "START_GRACE", 0.3)
    yield f
    remote_queue.cancel()


def _settings() -> Settings:
    return Settings(bot_token="x", remote_play_token=TOKEN)


async def _until(predicate, seconds=3.0):
    for _ in range(int(seconds / 0.01)):
        if predicate():
            return
        await asyncio.sleep(0.01)
    raise AssertionError("timed out")


async def test_items_play_one_after_another(fake):
    title, queue = await remote_queue.start(_settings(), LINKS[:3], 0, 12.0)
    assert title == "title of 000"
    await _until(lambda: remote_queue.current() is None)
    assert fake.launches == [(LINKS[0], 12.0), (LINKS[1], None), (LINKS[2], None)]


async def test_it_starts_at_the_index_and_the_start_belongs_to_that_item(fake):
    await remote_queue.start(_settings(), LINKS, 2, 30.0)
    await _until(lambda: remote_queue.current() is None)
    assert fake.launches == [(LINKS[2], 30.0), (LINKS[3], None)]


async def test_an_mpv_that_vanished_mid_video_ends_the_queue(fake):
    fake.next_script = lambda url: [{"playing": True, "position": 10.0, "duration": 100.0}]
    await remote_queue.start(_settings(), LINKS, 0, None)
    await _until(lambda: remote_queue.current() is None)
    assert [u for u, _ in fake.launches] == [LINKS[0]]  # /quit or stop: not an item ending


async def test_another_launch_ends_the_queue(fake):
    fake.next_script = lambda url: [{"playing": True, "position": 1.0, "duration": 100.0}] * 500
    await remote_queue.start(_settings(), LINKS, 0, None)
    fake.epoch += 1  # a link from a chat, say
    await _until(lambda: remote_queue.current() is None)
    assert [u for u, _ in fake.launches] == [LINKS[0]]


async def test_a_link_that_cannot_play_is_passed_over(fake):
    fake.fail = {LINKS[1]}
    _, queue = await remote_queue.start(_settings(), LINKS[:3], 0, None)
    await _until(lambda: remote_queue.current() is None)
    assert [u for u, _ in fake.launches] == [LINKS[0], LINKS[2]]
    assert queue.error == "Video unavailable"


async def test_a_first_link_that_cannot_play_raises_and_leaves_no_queue(fake):
    fake.fail = {LINKS[0]}
    with pytest.raises(player.UrlPlaybackError):
        await remote_queue.start(_settings(), LINKS, 0, None)
    assert remote_queue.current() is None


async def test_a_video_that_never_shows_up_is_given_up_on(fake):
    fake.next_script = lambda url: []  # mpv never answers
    await remote_queue.start(_settings(), LINKS[:2], 0, None)
    await _until(lambda: remote_queue.current() is None)
    assert [u for u, _ in fake.launches] == [LINKS[0], LINKS[1]]


async def test_skip_moves_through_the_list(fake):
    fake.next_script = lambda url: [{"playing": True, "position": 1.0, "duration": 100.0}] * 500
    _, queue = await remote_queue.start(_settings(), LINKS, 1, None)
    assert remote_queue.skip(1)
    await _until(lambda: len(fake.launches) == 2)
    assert fake.launches[1][0] == LINKS[2]
    assert remote_queue.skip(-1)
    await _until(lambda: len(fake.launches) == 3)
    assert fake.launches[2][0] == LINKS[1]
    assert queue.snapshot()["position"] == 1


async def test_skip_does_not_leave_the_list(fake):
    fake.next_script = lambda url: [{"playing": True, "position": 1.0, "duration": 100.0}] * 500
    _, queue = await remote_queue.start(_settings(), LINKS[:2], 0, None)
    remote_queue.skip(-1)  # before the first
    await asyncio.sleep(0.1)
    assert queue.snapshot()["position"] == 0 and len(fake.launches) == 1
    remote_queue.skip(1)
    await _until(lambda: len(fake.launches) == 2)
    remote_queue.skip(1)  # past the last
    await asyncio.sleep(0.1)
    assert queue.snapshot()["position"] == 1 and len(fake.launches) == 2


async def test_skip_without_a_queue_is_left_to_mpv(fake):
    assert remote_queue.skip(1) is False


async def test_the_panel_and_api_next_go_through_the_queue(fake):
    fake.next_script = lambda url: [{"playing": True, "position": 1.0, "duration": 100.0}] * 500
    await remote_queue.start(_settings(), LINKS, 0, None)
    calls = []

    class Client:
        def playlist_next(self):
            calls.append("mpv next")

    mpv_ipc.CTL_ACTIONS["next"](Client())
    await _until(lambda: len(fake.launches) == 2)
    assert calls == []  # the queue took it
    remote_queue.cancel()
    mpv_ipc.CTL_ACTIONS["next"](Client())
    assert calls == ["mpv next"]  # no queue: mpv's own playlist


# ── over HTTP ──────────────────────────────────────────────────


@pytest.fixture
async def client(fake):
    async with TestClient(TestServer(remote.make_app(_settings()))) as c:
        yield c


async def test_play_with_urls_starts_a_queue(client, fake):
    resp = await client.post("/play", json={"urls": LINKS, "index": 1, "start": 20}, headers=AUTH)
    assert resp.status == 200
    assert await resp.json() == {"ok": True, "title": "title of 001", "queued": 3}
    assert fake.launches == [(LINKS[1], 20.0)]
    assert remote_queue.current() is not None


async def test_status_reports_the_queue_and_survives_the_gap_between_items(client, fake, monkeypatch):
    fake.next_script = lambda url: [{"playing": True, "position": 1.0, "duration": 100.0}] * 500
    await client.post("/play", json={"urls": LINKS}, headers=AUTH)
    fake.script = []  # between two items mpv is gone, and the queue is still going
    monkeypatch.setattr(remote, "MpvClient", lambda path: type("C", (), {"read_status": lambda s: fake.read_status()})())
    body = await (await client.get("/status", headers=AUTH)).json()
    assert body["ok"] and body["playing"] is False
    assert body["queue"] == {"position": 0, "count": 4, "error": None}


async def test_a_single_link_and_stop_end_the_queue(client, fake, monkeypatch):
    fake.next_script = lambda url: [{"playing": True, "position": 1.0, "duration": 100.0}] * 500
    await client.post("/play", json={"urls": LINKS}, headers=AUTH)
    await client.post("/play", json={"url": LINKS[3]}, headers=AUTH)
    await _until(lambda: remote_queue.current() is None)
    await client.post("/play", json={"urls": LINKS}, headers=AUTH)
    assert remote_queue.current() is not None
    monkeypatch.setattr(remote, "CTL_ACTIONS", {"stop": lambda c: None})
    resp = await client.post("/ctl", json={"action": "stop"}, headers=AUTH)
    assert resp.status == 200 and remote_queue.current() is None


async def test_a_full_queue_of_real_links_fits_in_one_request(client, fake):
    links = [f"https://www.youtube.com/watch?v={i:011d}" for i in range(remote_queue.MAX_ITEMS)]
    resp = await client.post("/play", json={"urls": links}, headers=AUTH)
    assert resp.status == 200
    assert (await resp.json())["queued"] == remote_queue.MAX_ITEMS


@pytest.mark.parametrize(
    "body",
    [
        {"urls": []},
        {"urls": "https://example.com/x"},
        {"urls": ["https://example.com/x", "file:///etc/passwd"]},
        {"urls": [f"https://example.com/{i}" for i in range(remote_queue.MAX_ITEMS + 1)]},
        {"urls": LINKS, "index": 4},
        {"urls": LINKS, "index": -1},
        {"urls": LINKS, "index": True},
        {"urls": LINKS, "start": -5},
    ],
)
async def test_bad_queue_requests_launch_nothing(client, fake, body):
    resp = await client.post("/play", json=body, headers=AUTH)
    assert resp.status == 400
    assert fake.launches == []


async def test_a_first_link_that_fails_is_a_422(client, fake):
    fake.fail = {LINKS[0]}
    resp = await client.post("/play", json={"urls": LINKS}, headers=AUTH)
    assert resp.status == 422
    assert (await resp.json())["error"] == "Video unavailable"
    assert remote_queue.current() is None
