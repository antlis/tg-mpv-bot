from src import state


def test_roundtrip(tmp_path):
    sf = tmp_path / "deep" / "state.json"  # parent dirs are created on demand
    pl = tmp_path / "show.m3u"
    pl.write_text("/x.mkv\n")
    state.record_last_played(sf, pl)
    assert state.last_played(sf) == str(pl)


def test_url_roundtrip(tmp_path):
    sf = tmp_path / "state.json"
    url = "https://soundcloud.com/forss/flickermood"
    state.record_last_played(sf, url, name="Flickermood")
    assert state.last_played(sf) == url  # URLs skip the on-disk existence check
    [entry] = state.history(sf)
    assert entry.name == "Flickermood"
    assert entry.is_url


def test_history_newest_first_and_deduped(tmp_path):
    sf = tmp_path / "state.json"
    a, b = tmp_path / "a.m3u", tmp_path / "b.m3u"
    a.write_text("x")
    b.write_text("x")
    state.record_last_played(sf, a)
    state.record_last_played(sf, b)
    state.record_last_played(sf, a)  # replay moves it back to the top, no dupe
    targets = [e.target for e in state.history(sf)]
    assert targets == [str(a), str(b)]


def test_history_capped(tmp_path):
    sf = tmp_path / "state.json"
    for i in range(state.HISTORY_LIMIT + 5):
        state.record_last_played(sf, f"https://example.com/{i}", name=f"v{i}")
    entries = state.history(sf)
    assert len(entries) == state.HISTORY_LIMIT
    assert entries[0].name == f"v{state.HISTORY_LIMIT + 4}"  # newest kept


def test_history_drops_deleted_playlists_keeps_urls(tmp_path):
    sf = tmp_path / "state.json"
    pl = tmp_path / "gone.m3u"
    pl.write_text("x")
    state.record_last_played(sf, pl)
    state.record_last_played(sf, "https://example.com/v", name="V")
    pl.unlink()  # library reorganised since → don't offer a dead playlist
    assert [e.target for e in state.history(sf)] == ["https://example.com/v"]


def test_migrates_pre_history_format(tmp_path):
    sf = tmp_path / "state.json"
    pl = tmp_path / "old.m3u"
    pl.write_text("x")
    sf.write_text(f'{{"last_played": "{pl}", "at": 5}}')
    assert state.last_played(sf) == str(pl)
    [entry] = state.history(sf)
    assert entry.name == "old"


def test_last_played_missing_state_file(tmp_path):
    assert state.last_played(tmp_path / "nope.json") is None
    assert state.history(tmp_path / "nope.json") == []


def test_last_played_corrupt_state_file(tmp_path):
    sf = tmp_path / "state.json"
    sf.write_text("{not json")
    assert state.last_played(sf) is None
    sf.write_text('["a", "list"]')  # valid JSON, wrong shape
    assert state.last_played(sf) is None


def test_position_checkpoint_roundtrip(tmp_path):
    sf = tmp_path / "state.json"
    state.record_last_played(sf, "https://youtu.be/x", name="V")
    state.update_position(sf, 754.9)
    [entry] = state.history(sf)
    assert entry.pos == 754
    # replaying the same target keeps the saved resume point
    state.record_last_played(sf, "https://youtu.be/x", name="V")
    assert state.history(sf)[0].pos == 754
    # finishing clears it
    state.update_position(sf, 0)
    assert state.history(sf)[0].pos == 0


def test_update_position_no_history_is_noop(tmp_path):
    state.update_position(tmp_path / "state.json", 100)  # must not raise


def test_notify_chat_roundtrip(tmp_path):
    sf = tmp_path / "state.json"
    assert state.notify_chat(sf) is None
    state.set_notify_chat(sf, 42)
    assert state.notify_chat(sf) == 42
    # history writes must not clobber it
    state.record_last_played(sf, "https://example.com/v", name="V")
    assert state.notify_chat(sf) == 42
    assert state.history(sf)


def test_notify_enabled_default_on(tmp_path):
    sf = tmp_path / "state.json"
    assert state.notify_enabled(sf) is True
    state.set_notify_enabled(sf, False)
    assert state.notify_enabled(sf) is False


def test_record_failure_never_raises(tmp_path):
    ro = tmp_path / "file-not-dir"
    ro.write_text("x")  # parent "dir" is actually a file → mkdir/write fails
    state.record_last_played(ro / "state.json", tmp_path / "p.m3u")
