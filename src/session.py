"""Bot API session that survives a flaky network path.

A single failed request means the user's command silently produces nothing:
they type it again and it "works", which reads as a bug — the old "have to run
``/library`` twice" symptom. The host this bot runs on has VPN tiers and
periodic blips, so connection-level failures do happen.

Only *connect-phase* errors are retried (``ClientConnectorError`` and friends):
those fail before a single byte reaches Telegram, so retrying cannot duplicate
a message. Anything the server may already have seen — timeouts, mid-request
disconnects — is re-raised untouched.
"""

from __future__ import annotations

import asyncio
import logging

from aiogram import Bot
from aiogram.client.session.aiohttp import AiohttpSession
from aiogram.exceptions import TelegramNetworkError
from aiogram.methods import TelegramMethod
from aiogram.methods.base import TelegramType

logger = logging.getLogger(__name__)

RETRIES = 3
BACKOFF = (0.4, 1.2)  # seconds to wait before attempt 2 / attempt 3


class ResilientSession(AiohttpSession):
    """``AiohttpSession`` that retries connect-phase network failures."""

    async def make_request(
        self,
        bot: Bot,
        method: TelegramMethod[TelegramType],
        timeout: int | None = None,
    ) -> TelegramType:
        for attempt in range(RETRIES):
            try:
                return await super().make_request(bot, method, timeout=timeout)
            except TelegramNetworkError as exc:
                detail = str(exc)
                if attempt == RETRIES - 1 or "ClientConnector" not in detail:
                    raise
                delay = BACKOFF[attempt]
                logger.warning(
                    "Bot API unreachable (%s) — retry %d/%d in %.1fs",
                    detail,
                    attempt + 2,
                    RETRIES,
                    delay,
                )
                await asyncio.sleep(delay)
        raise AssertionError("unreachable")  # pragma: no cover
