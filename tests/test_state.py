from src import state


def test_roundtrip(tmp_path):
    sf = tmp_path / "deep" / "state.json"  # parent dirs are created on demand
    pl = tmp_path / "show.m3u"
    pl.write_text("/x.mkv\n")
    state.record_last_played(sf, pl)
    assert state.last_played(sf) == pl


def test_last_played_missing_state_file(tmp_path):
    assert state.last_played(tmp_path / "nope.json") is None


def test_last_played_corrupt_state_file(tmp_path):
    sf = tmp_path / "state.json"
    sf.write_text("{not json")
    assert state.last_played(sf) is None
    sf.write_text('["a", "list"]')  # valid JSON, wrong shape
    assert state.last_played(sf) is None


def test_last_played_playlist_deleted(tmp_path):
    sf = tmp_path / "state.json"
    pl = tmp_path / "gone.m3u"
    pl.write_text("x")
    state.record_last_played(sf, pl)
    pl.unlink()  # library reorganised since → don't offer a dead playlist
    assert state.last_played(sf) is None


def test_record_failure_never_raises(tmp_path):
    ro = tmp_path / "file-not-dir"
    ro.write_text("x")  # parent "dir" is actually a file → mkdir/write fails
    state.record_last_played(ro / "state.json", tmp_path / "p.m3u")
