#!/usr/bin/env python3
"""tg-mpv-bot — standalone Telegram bot for mpv media control.

Usage:
    python bot.py                          # standard Telegram API
    API_SERVER_URL=http://... python bot.py  # local Bot API server
"""

import asyncio
import logging
import sys
from collections.abc import Awaitable, Callable
from typing import Any

from aiogram import Bot, Dispatcher
from aiogram.client.default import DefaultBotProperties
from aiogram.client.session.aiohttp import AiohttpSession
from aiogram.client.telegram import TelegramAPIServer
from aiogram.types import (
    BotCommand,
    BotCommandScopeDefault,
    CallbackQuery,
    ErrorEvent,
    Message,
)

from src import lock
from src.commands import router
from src.config import get_settings

logger = logging.getLogger("tg-mpv-bot")


def _build_menu() -> list[BotCommand]:
    return [
        BotCommand(command="mpv_list", description="Browse playlists with buttons"),
        BotCommand(command="mpv_play", description="Play a playlist by name or number"),
        BotCommand(command="mpv_search", description="Search playlists (optionally by category)"),
        BotCommand(command="mpv_last", description="Resume the last-played playlist"),
        BotCommand(command="mpv_info", description="Show current status"),
        BotCommand(command="mpv_shot", description="Screenshot the current frame"),
        BotCommand(command="mpv_toggle", description="Play/pause toggle"),
        BotCommand(command="mpv_pause", description="Pause playback"),
        BotCommand(command="mpv_unpause", description="Resume playback"),
        BotCommand(command="mpv_quit", description="Stop mpv and quit"),
        BotCommand(command="mpv_fwd", description="Seek +30s"),
        BotCommand(command="mpv_back", description="Seek -10s"),
        BotCommand(command="mpv_next", description="Next in playlist"),
        BotCommand(command="mpv_prev", description="Previous in playlist"),
        BotCommand(command="mpv_ep", description="Episode picker (or jump to item N)"),
        BotCommand(command="mpv_speed", description="Playback speed (buttons or value)"),
        BotCommand(command="mpv_shuffle", description="Shuffle the playlist"),
        BotCommand(command="mpv_loop", description="Toggle playlist loop"),
        BotCommand(command="mpv_audio", description="Switch audio track"),
        BotCommand(command="mpv_sub", description="Switch subtitle track"),
        BotCommand(command="mpv_sub_toggle", description="Show/hide subtitles"),
        BotCommand(command="mpv_volup", description="Volume +10"),
        BotCommand(command="mpv_voldown", description="Volume -10"),
        BotCommand(command="mpv_mute", description="Toggle mute"),
        BotCommand(command="mpv_doctor", description="Check for broken playlists"),
        BotCommand(command="mpv_fix", description="Repair broken playlists"),
        BotCommand(command="mpv_scan", description="Create playlists for new media"),
        BotCommand(command="help", description="Show help"),
    ]


def _setup_logging() -> None:
    # Line-buffer stdout so logs reach journald live (no TTY under systemd).
    try:
        sys.stdout.reconfigure(line_buffering=True)
    except Exception:
        pass
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

    # ── Global error handler ─────────────────────────────────────
    # Any unhandled handler error is logged AND the callback spinner is
    # cleared, so a failure can never present to the user as a hang.
    @dp.errors()
    async def on_error(event: ErrorEvent) -> bool:
        logger.exception("Unhandled error: %s", event.exception)
        cq = event.update.callback_query
        if cq:
            try:
                await cq.answer("⚠️ Something went wrong", show_alert=False)
            except Exception:
                pass
        return True  # mark handled so polling continues

    # ── Auth middleware (messages AND button taps) ───────────────
    if settings.is_restricted:
        allowed = set(settings.allowed_users)
        logger.info("Access restricted to users: %s", allowed)

        @dp.message.outer_middleware()
        async def auth_messages(
            handler: Callable[[Message, dict[str, Any]], Awaitable[Any]],
            message: Message,
            data: dict[str, Any],
        ) -> Any:
            if message.from_user and message.from_user.id in allowed:
                return await handler(message, data)
            await message.reply("⛔ Unauthorized")
            return None

        @dp.callback_query.outer_middleware()
        async def auth_callbacks(
            handler: Callable[[CallbackQuery, dict[str, Any]], Awaitable[Any]],
            query: CallbackQuery,
            data: dict[str, Any],
        ) -> Any:
            if query.from_user and query.from_user.id in allowed:
                return await handler(query, data)
            await query.answer("⛔ Unauthorized", show_alert=True)
            return None
    else:
        logger.warning("ALLOWED_USERS is empty — open to everyone")

    # Register command menu
    await bot.set_my_commands(
        _build_menu(),
        scope=BotCommandScopeDefault(),
    )

    if settings.scan_interval_min > 0:
        asyncio.create_task(_scan_loop(settings))
        logger.info("Auto-scan every %d min", settings.scan_interval_min)

    logger.info("tg-mpv-bot starting (polling)...")
    await dp.start_polling(bot)


async def _scan_loop(settings) -> None:
    """Periodically generate playlists for newly-added media."""
    from src import commands, generate

    while True:
        await asyncio.sleep(settings.scan_interval_min * 60)
        try:
            created = await asyncio.to_thread(generate.generate_missing, settings)
            if created:
                await asyncio.to_thread(commands.refresh_cache)
                logger.info("Auto-scan added %d playlist(s): %s", len(created), created)
        except Exception:
            logger.exception("Auto-scan failed")


if __name__ == "__main__":
    try:
        fd = lock.acquire(get_settings().lock_file)  # keep ref → holds the lock
        asyncio.run(main())
    except lock.AlreadyRunning as exc:
        raise SystemExit(str(exc)) from exc
    except KeyboardInterrupt:
        logger.info("Shutdown by keyboard interrupt")
