"""Single-instance guard.

Telegram allows only one long-poll per token; a second ``python bot.py`` makes
both instances fight (``TelegramConflictError``) and taps land on whichever
wins — presenting as a hang. An exclusive ``flock`` makes the second instance
exit immediately with a clear message instead.
"""

from __future__ import annotations

import fcntl
import os
from typing import IO


class AlreadyRunning(Exception):
    pass


def acquire(lock_file: str) -> IO:
    """Take an exclusive, non-blocking lock; raise AlreadyRunning if held.

    Returns the open file object — keep a reference for the process lifetime
    (closing it, or exiting, releases the lock).
    """
    fd = open(lock_file, "w")
    try:
        fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except OSError as exc:
        fd.close()
        raise AlreadyRunning(
            f"tg-mpv-bot is already running (lock held on {lock_file}). "
            f"Use 'systemctl --user restart tg-mpv-bot' instead of launching by hand."
        ) from exc
    fd.seek(0)
    fd.truncate()
    fd.write(str(os.getpid()))
    fd.flush()
    return fd
