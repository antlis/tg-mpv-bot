import pytest

from src import lock


def test_acquire_writes_pid(tmp_path):
    lf = tmp_path / "bot.lock"
    fd = lock.acquire(str(lf))
    assert lf.read_text().strip().isdigit()
    fd.close()


def test_second_acquire_raises(tmp_path):
    lf = tmp_path / "bot.lock"
    fd = lock.acquire(str(lf))
    with pytest.raises(lock.AlreadyRunning):
        lock.acquire(str(lf))  # second flock on same file → blocked
    fd.close()


def test_reacquire_after_release(tmp_path):
    lf = tmp_path / "bot.lock"
    fd = lock.acquire(str(lf))
    fd.close()  # releases the lock
    fd2 = lock.acquire(str(lf))  # now succeeds
    fd2.close()
