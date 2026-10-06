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
from pathlib import Path
from typing import Any

from aiogram import Bot, Dispatcher
from aiogram.client.default import DefaultBotProperties
from aiogram.client.telegram import SimpleFilesPathWrapper, TelegramAPIServer
from aiogram.types import (
    BotCommand,
    BotCommandScopeDefault,
    CallbackQuery,
    ErrorEvent,
    Message,
)

from src import lock, remote
from src.commands import router
from src.config import get_settings
from src.iptv import iptv_router
from src.session import ResilientSession

logger = logging.getLogger("tg-mpv-bot")


def _build_menu() -> list[BotCommand]:
    return [
        BotCommand(command="library", description="Browse playlists with buttons"),
        BotCommand(command="play", description="Play a playlist by name or number"),
        BotCommand(command="search", description="Search playlists (optionally by category)"),
        BotCommand(command="last", description="Resume the last-played playlist/stream"),
        BotCommand(command="history", description="Recently played — tap to replay"),
        BotCommand(command="notify", description="Toggle end-of-playback notifications"),
        BotCommand(command="url", description="Stream a link (YouTube/SoundCloud/…)"),
        BotCommand(command="yt", description="Search YouTube, tap to play"),
        BotCommand(command="radio", description="Internet radio — presets or search 50k stations"),
        BotCommand(command="iptv", description="Live TV — search iptv-org (50k+ channels)"),
        BotCommand(command="info", description="Show current status"),
        BotCommand(command="shot", description="Screenshot the current frame"),
        BotCommand(command="record", description="Record video/radio; /record 01:30 02:00 to clip a range"),
        BotCommand(command="toggle", description="Play/pause toggle"),
        BotCommand(command="pause", description="Pause playback"),
        BotCommand(command="unpause", description="Resume playback"),
        BotCommand(command="quit", description="Stop mpv and quit"),
        BotCommand(command="fwd", description="Seek +30s"),
        BotCommand(command="back", description="Seek -10s"),
        BotCommand(command="goto", description="Seek to time (1:23:45) or percent (75%)"),
        BotCommand(command="next", description="Next in playlist"),
        BotCommand(command="prev", description="Previous in playlist"),
        BotCommand(command="ep", description="Episode picker (or jump to item N)"),
        BotCommand(command="chapters", description="Chapter picker for the current file"),
        BotCommand(command="speed", description="Playback speed (buttons or value)"),
        BotCommand(command="shuffle", description="Shuffle the playlist"),
        BotCommand(command="loop", description="Toggle playlist loop"),
        BotCommand(command="sleep", description="Stop playback after N minutes"),
        BotCommand(command="random", description="Play a random playlist"),
        BotCommand(command="night", description="Toggle loudness normalization"),
        BotCommand(command="audio", description="Switch audio track"),
        BotCommand(command="sub", description="Switch subtitle track"),
        BotCommand(command="sub_toggle", description="Show/hide subtitles"),
        BotCommand(command="volup", description="Volume +10"),
        BotCommand(command="voldown", description="Volume -10"),
        BotCommand(command="mute", description="Toggle mute"),
        BotCommand(command="doctor", description="Check for broken playlists"),
        BotCommand(command="health", description="System health: player, tools, disks"),
        BotCommand(command="fix", description="Repair broken playlists"),
        BotCommand(command="scan", description="Create playlists for new media"),
        BotCommand(command="update_ytdlp", description="Update yt-dlp (when YouTube breaks)"),
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
        server_kwargs = {}
        if settings.api_local_files_dir:
            # TELEGRAM_LOCAL-mode server: getFile returns container paths;
            # map them to the bind-mounted host dir and read directly.
            server_kwargs = dict(
                is_local=True,
                wrap_local_file=SimpleFilesPathWrapper(
                    Path("/var/lib/telegram-bot-api"),
                    Path(settings.api_local_files_dir).expanduser(),
                ),
            )
        # ResilientSession retries connect-phase failures: a single dropped
        # request = a command that silently produced nothing (the old
        # "have to run /library twice" symptom).
        session = ResilientSession(
            api=TelegramAPIServer.from_base(settings.api_server_url, **server_kwargs),
        )
        bot = Bot(token=settings.bot_token, session=session, default=default)
        logger.info("Using local API server at %s", settings.api_server_url)
    else:
        bot = Bot(token=settings.bot_token, session=ResilientSession(), default=default)

    dp = Dispatcher()
    dp.include_router(router)
    dp.include_router(iptv_router)

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

    # End-of-playback notifications (episode advanced / playlist finished).
    from src import notify
    asyncio.create_task(notify.run(bot, settings))

    if settings.ytdlp_update_days > 0:
        asyncio.create_task(_ytdlp_update_loop(bot, settings))
        logger.info("yt-dlp auto-update every %d day(s)", settings.ytdlp_update_days)

    remote_runner = await remote.start(settings)

    logger.info("tg-mpv-bot starting (polling)...")
    try:
        await dp.start_polling(bot)
    finally:
        if remote_runner is not None:
            await remote_runner.cleanup()


async def _ytdlp_update_loop(bot: Bot, settings) -> None:
    """Periodically refresh the venv's yt-dlp nightly (YouTube breaks it
    every few months — better to update before it bites mid-movie). Reports
    actual version bumps to the chat that last started playback."""
    from src import player, state

    while True:
        await asyncio.sleep(settings.ytdlp_update_days * 86400)
        try:
            result = await asyncio.to_thread(player.update_ytdlp)
            logger.info("yt-dlp auto-update: %s", result)
            chat = state.notify_chat(settings.state_file)
            if chat and "updated" in result:
                await bot.send_message(chat, f"🔄 Auto-update: {result}")
        except Exception:
            logger.exception("yt-dlp auto-update failed")


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
