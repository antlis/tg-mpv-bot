import json
import socket
import threading

import pytest

from src.mpv_ipc import MpvClient, MpvError, MpvNotRunning


class FakeMpv:
    """Minimal mpv IPC server over a Unix socket for tests.

    Handles one connection per command (matching MpvClient's behaviour),
    maintains a tiny property store, and can be told to emit a spurious
    ``event`` line before the reply to exercise the skip-events read loop.
    """

    def __init__(self, path: str, emit_event: bool = False):
        self.path = path
        self.emit_event = emit_event
        self.props = {"volume": 50.0, "pause": False, "media-title": "Test Clip"}
        self._srv = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        self._srv.bind(path)
        self._srv.listen(8)
        self._srv.settimeout(5)
        self._stop = threading.Event()
        self._thread = threading.Thread(target=self._serve, daemon=True)
        self._thread.start()

    def _serve(self):
        while not self._stop.is_set():
            try:
                conn, _ = self._srv.accept()
            except (socket.timeout, OSError):
                break
            with conn:
                data = b""
                while b"\n" not in data:
                    chunk = conn.recv(4096)
                    if not chunk:
                        break
                    data += chunk
                if b"\n" not in data:
                    continue
                line = data.split(b"\n", 1)[0]
                req = json.loads(line.decode())
                reply = self._handle(req)
                out = b""
                if self.emit_event:
                    out += json.dumps({"event": "tick"}).encode() + b"\n"
                out += json.dumps(reply).encode() + b"\n"
                conn.sendall(out)

    def _handle(self, req: dict) -> dict:
        cmd = req.get("command", [])
        rid = req.get("request_id")
        name = cmd[0] if cmd else None
        if name == "get_property":
            key = cmd[1]
            if key in self.props:
                return {"error": "success", "data": self.props[key], "request_id": rid}
            return {"error": "property unavailable", "request_id": rid}
        if name == "set_property":
            self.props[cmd[1]] = cmd[2]
            return {"error": "success", "request_id": rid}
        if name == "cycle":
            # emulate `cycle sub` advancing the subtitle track id
            if cmd[1:2] == ["sub"]:
                self.props["sid"] = (self.props.get("sid") or 0) + 1
            return {"error": "success", "request_id": rid}
        if name in ("seek", "quit", "playlist-next", "playlist-prev"):
            return {"error": "success", "request_id": rid}
        return {"error": "success", "request_id": rid}

    def close(self):
        self._stop.set()
        self._srv.close()


@pytest.fixture
def fake_mpv(tmp_path):
    srv = FakeMpv(str(tmp_path / "mpv.sock"))
    yield srv
    srv.close()


@pytest.fixture
def fake_mpv_with_events(tmp_path):
    srv = FakeMpv(str(tmp_path / "mpv.sock"), emit_event=True)
    yield srv
    srv.close()


def test_not_running(tmp_path):
    client = MpvClient(str(tmp_path / "does-not-exist.sock"))
    with pytest.raises(MpvNotRunning):
        client.get_property("volume")


def test_get_property(fake_mpv):
    client = MpvClient(fake_mpv.path)
    assert client.get_property("media-title") == "Test Clip"


def test_set_property_roundtrip(fake_mpv):
    client = MpvClient(fake_mpv.path)
    client.set_pause(True)
    assert client.get_property("pause") is True


def test_property_unavailable_raises(fake_mpv):
    client = MpvClient(fake_mpv.path)
    with pytest.raises(MpvError):
        client.get_property("duration")  # not in the store


def test_adjust_volume_clamps_high(fake_mpv):
    client = MpvClient(fake_mpv.path)
    fake_mpv.props["volume"] = 125.0
    assert client.adjust_volume(10) == 130.0
    assert fake_mpv.props["volume"] == 130.0


def test_adjust_volume_clamps_low(fake_mpv):
    client = MpvClient(fake_mpv.path)
    fake_mpv.props["volume"] = 5.0
    assert client.adjust_volume(-10) == 0.0


def test_adjust_volume_normal(fake_mpv):
    client = MpvClient(fake_mpv.path)
    fake_mpv.props["volume"] = 50.0
    assert client.adjust_volume(10) == 60.0


def test_skips_event_lines(fake_mpv_with_events):
    client = MpvClient(fake_mpv_with_events.path)
    # reply is preceded by an {"event": ...} line that must be skipped
    assert client.get_property("volume") == 50.0


def test_simple_commands_succeed(fake_mpv):
    client = MpvClient(fake_mpv.path)
    client.seek(30)
    client.cycle_mute()
    client.quit()
    client.playlist_next()
    client.playlist_prev()
    client.toggle_sub_visibility()


def test_cycle_sub_advances_track(fake_mpv):
    client = MpvClient(fake_mpv.path)
    fake_mpv.props["sid"] = 1
    client.cycle_sub()
    assert fake_mpv.props["sid"] == 2


def test_cycle_sub_text_reports_track(fake_mpv):
    from src.commands import _cycle_sub_text

    client = MpvClient(fake_mpv.path)
    fake_mpv.props["sid"] = 0
    # cycles 0 -> 1; title/lang properties are absent so it falls back to "track N"
    assert _cycle_sub_text(client) == "💬 Subtitles: track 1"
