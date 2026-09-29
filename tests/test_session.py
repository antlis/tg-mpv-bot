"""ResilientSession — retry connect-phase failures, nothing else.

The contract: a request that dies *before* reaching Telegram (DNS/connect
blip) is retried, so a flaky network never presents as "the bot ignored my
command". Anything the server may already have seen is re-raised untouched.
"""

import pytest
from aiogram.client.session.aiohttp import AiohttpSession
from aiogram.exceptions import TelegramBadRequest, TelegramNetworkError
from aiogram.methods import SendMessage

from src.session import ResilientSession

METHOD = SendMessage(chat_id=1, text="hi")


@pytest.fixture(autouse=True)
def no_backoff(monkeypatch):
    monkeypatch.setattr("src.session.BACKOFF", (0.0, 0.0))


def _script(monkeypatch, outcomes):
    """Patch AiohttpSession.make_request with a scripted list of outcomes.

    Each entry is either an exception *message* to raise as
    TelegramNetworkError, or None to succeed. Returns the call counter.
    """
    calls = {"n": 0}
    queue = list(outcomes)

    async def scripted(self, bot, method, timeout=None):
        calls["n"] += 1
        msg = queue[min(calls["n"] - 1, len(queue) - 1)]
        if msg is not None:
            raise TelegramNetworkError(method=method, message=msg)
        return "ok"

    monkeypatch.setattr(AiohttpSession, "make_request", scripted)
    return calls


CONNECT_ERR = "ClientConnectorError: Cannot connect to host api.telegram.org:443"


async def test_connect_error_is_retried_then_succeeds(monkeypatch):
    calls = _script(monkeypatch, [CONNECT_ERR, CONNECT_ERR, None])
    session = ResilientSession()
    assert await session.make_request(None, METHOD) == "ok"
    assert calls["n"] == 3


async def test_gives_up_after_three_attempts(monkeypatch):
    calls = _script(monkeypatch, [CONNECT_ERR])
    session = ResilientSession()
    with pytest.raises(TelegramNetworkError):
        await session.make_request(None, METHOD)
    assert calls["n"] == 3


async def test_non_connect_network_error_is_not_retried(monkeypatch):
    calls = _script(monkeypatch, ["Request timeout error"])
    session = ResilientSession()
    with pytest.raises(TelegramNetworkError):
        await session.make_request(None, METHOD)
    assert calls["n"] == 1


async def test_api_errors_pass_through(monkeypatch):
    calls = {"n": 0}

    async def bad_request(self, bot, method, timeout=None):
        calls["n"] += 1
        raise TelegramBadRequest(method=method, message="Bad Request: chat not found")

    monkeypatch.setattr(AiohttpSession, "make_request", bad_request)
    with pytest.raises(TelegramBadRequest):
        await ResilientSession().make_request(None, METHOD)
    assert calls["n"] == 1
