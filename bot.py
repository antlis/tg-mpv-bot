#!/usr/bin/env python3
"""tg-mpv-bot — standalone Telegram bot for mpv media control.

Usage:
    python bot.py                          # standard Telegram API
    API_SERVER_URL=http://... python bot.py  # local Bot API server
"""

import asyncio
import logging
import sys
from typing import Any, Awaitable, Callable, Dict

from aiogram import Bot, Dispatcher
from aiogram.client.default import DefaultBotProperties
from aiogram.client.session.aiohttp import AiohttpSession
from aiogram.client.telegram import TelegramAPIServer
from aiogram.types import BotCommand, BotCommandScopeDefault, Message

from src.config import get_settings
from src.commands import router

logger = logging.getLogger("tg-mpv-bot")


def _build_menu() -> list[BotCommand]:
    return [
        BotCommand(command="mpv_list", description="Browse playlists with buttons"),
        BotCommand(command="mpv_play", description="Play a playlist by name or number"),
        BotCommand(command="mpv_info", description="Show current status"),
        BotCommand(command="mpv_pause", description="Pause playback"),
        BotCommand(command="mpv_unpause", description="Resume playback"),
        BotCommand(command="mpv_quit", description="Stop mpv and quit"),
        BotCommand(command="mpv_fwd", description="Seek +30s"),
        BotCommand(command="mpv_back", description="Seek -10s"),
        BotCommand(command="mpv_next", description="Next in playlist"),
        BotCommand(command="mpv_prev", description="Previous in playlist"),
        BotCommand(command="mpv_sub", description="Switch subtitle track"),
        BotCommand(command="mpv_sub_toggle", description="Show/hide subtitles"),
        BotCommand(command="mpv_volup", description="Volume +10"),
        BotCommand(command="mpv_voldown", description="Volume -10"),
        BotCommand(command="mpv_mute", description="Toggle mute"),
        BotCommand(command="mpv_doctor", description="Check for broken playlists"),
        BotCommand(command="help", description="Show help"),
    ]


def _setup_logging() -> None:
    level = logging.INFO
    fmt = logging.Formatter("%(asctime)s [%(levelname)s] %(name)s: %(message)s")
    handler = logging.StreamHandler(sys.stdout)
    handler.setFormatter(fmt)
    logging.getLogger().addHandler(handler)
    logging.getLogger().setLevel(level)


async def main() -> None:
    _setup_logging()
    settings = get_settings()

    # Build the bot
    # Replies are plain text by default; handlers that need HTML set it per-message.
    default = DefaultBotProperties(parse_mode=None)
    if settings.api_server_url:
        session = AiohttpSession(
            api=TelegramAPIServer.from_base(settings.api_server_url),
        )
        bot = Bot(token=settings.bot_token, session=session, default=default)
        logger.info("Using local API server at %s", settings.api_server_url)
    else:
        bot = Bot(token=settings.bot_token, default=default)

    dp = Dispatcher()
    dp.include_router(router)

    # ── Auth middleware ──────────────────────────────────────────
    if settings.is_restricted:
        allowed = set(settings.allowed_users)
        logger.info("Access restricted to users: %s", allowed)

        @dp.message.outer_middleware()
        async def auth_middleware(
            handler: Callable[[Message, Dict[str, Any]], Awaitable[Any]],
            message: Message,
            data: Dict[str, Any],
        ) -> Any:
            if message.from_user and message.from_user.id in allowed:
                return await handler(message, data)
            await message.reply("⛔ Unauthorized")
            return None
    else:
        logger.warning("ALLOWED_USERS is empty — open to everyone")

    # Register command menu
    await bot.set_my_commands(
        _build_menu(),
        scope=BotCommandScopeDefault(),
    )

    logger.info("tg-mpv-bot starting (polling)...")
    await dp.start_polling(bot)


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        logger.info("Shutdown by keyboard interrupt")
