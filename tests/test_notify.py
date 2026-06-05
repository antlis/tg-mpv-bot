"""The PlaybackMonitor state machine — event semantics, no I/O.

The contract: "eof then start-file" = advanced to the next episode,
"eof then disconnect" = playlist finished, anything else stays silent.
"""

from src.notify import PlaybackMonitor


def test_natural_advance_notifies():
    m = PlaybackMonitor()
    assert m.on_event({"event": "end-file", "reason": "eof"}) is None
    assert m.on_event({"event": "start-file"}) == "advanced"


def test_finish_on_disconnect_after_eof():
    m = PlaybackMonitor()
    m.on_event({"event": "end-file", "reason": "eof"})
    assert m.on_disconnect() == "finished"
    assert m.on_disconnect() is None  # one-shot


def test_manual_skip_is_silent():
    # /mpv_next, /mpv_ep etc. end the file with reason "stop"
    m = PlaybackMonitor()
    assert m.on_event({"event": "end-file", "reason": "stop"}) is None
    assert m.on_event({"event": "start-file"}) is None


def test_our_own_relaunch_is_silent():
    # _stop_current quits mpv over IPC → reason "quit", then socket closes
    m = PlaybackMonitor()
    assert m.on_event({"event": "end-file", "reason": "quit"}) is None
    assert m.on_disconnect() is None


def test_error_surfaces():
    m = PlaybackMonitor()
    assert m.on_event({"event": "end-file", "reason": "error"}) == "error"


def test_first_start_after_launch_is_silent():
    # no preceding eof — the bot already replied "Playing: …" itself
    m = PlaybackMonitor()
    assert m.on_event({"event": "start-file"}) is None


def test_unrelated_events_ignored():
    m = PlaybackMonitor()
    m.on_event({"event": "end-file", "reason": "eof"})
    # property chatter between eof and the next file must not eat the flag
    assert m.on_event({"event": "property-change", "name": "pause"}) is None
    assert m.on_event({"event": "start-file"}) == "advanced"
