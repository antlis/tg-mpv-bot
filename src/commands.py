"""Telegram command handlers for mpv control."""

import asyncio
import subprocess
import shlex
import logging
from typing import Any

from aiogram import Router, F
from aiogram.filters import Command, CommandObject
from aiogram.types import Message
from aiogram.enums import ParseMode

from .config import get_settings

logger = logging.getLogger(__name__)
router = Router(name="mpv_commands")


def _run_mpvctl( subcommand: str, args: str = "") -> str:
    """Run mpvctl.sh with the given subcommand and optional args.

    Blocks synchronously in a thread pool executor to avoid event loop
    blocking. Returns stdout+stderr, or an error message on failure.
    """
    settings = get_settings()
    cmd = [settings.mpvctl_path, subcommand]
    if args:
        # Safe: args is a single string; shlex.split protects injection
        cmd.extend(shlex.split(args))

    try:
        result = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            timeout=30,
        )
        out = result.stdout.strip()
        err = result.stderr.strip()
        if result.returncode != 0:
            return f"❌ Error ({result.returncode}): {err or out or 'Unknown'}"
        return out
    except FileNotFoundError:
        return f"❌ mpvctl.sh not found at {settings.mpvctl_path}"
    except subprocess.TimeoutExpired:
        return "⏱ Command timed out after 30s"
    except Exception as e:
        logger.exception("mpvctl subprocess error")
        return f"❌ Internal error: {e}"


async def _mpvctl(subcommand: str, args: str = "") -> str:
    """Async wrapper for _run_mpvctl — runs in thread pool."""
    return await asyncio.to_thread(_run_mpvctl, subcommand, args)


# ── Simple commands (no arguments) ──────────────────────────────────


@router.message(Command("mpv_pause"))
async def cmd_pause(message: Message) -> Any:
    out = await _mpvctl("pause")
    await message.reply(f"`{out}`", parse_mode=ParseMode.MARKDOWN)


@router.message(Command("mpv_unpause", "mpv_resume"))
async def cmd_unpause(message: Message) -> Any:
    out = await _mpvctl("unpause")
    await message.reply(f"`{out}`", parse_mode=ParseMode.MARKDOWN)


@router.message(Command("mpv_quit", "mpv_stop"))
async def cmd_quit(message: Message) -> Any:
    out = await _mpvctl("quit")
    await message.reply(f"`{out}`", parse_mode=ParseMode.MARKDOWN)


@router.message(Command("mpv_volup"))
async def cmd_volup(message: Message) -> Any:
    out = await _mpvctl("volup")
    await message.reply(f"`{out}`", parse_mode=ParseMode.MARKDOWN)


@router.message(Command("mpv_voldown"))
async def cmd_voldown(message: Message) -> Any:
    out = await _mpvctl("voldown")
    await message.reply(f"`{out}`", parse_mode=ParseMode.MARKDOWN)


@router.message(Command("mpv_mute"))
async def cmd_mute(message: Message) -> Any:
    out = await _mpvctl("mute")
    await message.reply(f"`{out}`", parse_mode=ParseMode.MARKDOWN)


@router.message(Command("mpv_list"))
async def cmd_list(message: Message) -> Any:
    out = await _mpvctl("list")
    await message.reply(f"`{out}`", parse_mode=ParseMode.MARKDOWN)


@router.message(Command("mpv_info", "mpv_status"))
async def cmd_info(message: Message) -> Any:
    out = await _mpvctl("info")
    await message.reply(f"`{out}`", parse_mode=ParseMode.MARKDOWN)


@router.message(Command("mpv_fwd", "mpv_forward"))
async def cmd_fwd(message: Message) -> Any:
    out = await _mpvctl("fwd")
    await message.reply(f"`{out}`", parse_mode=ParseMode.MARKDOWN)


@router.message(Command("mpv_back", "mpv_rewind"))
async def cmd_back(message: Message) -> Any:
    out = await _mpvctl("back")
    await message.reply(f"`{out}`", parse_mode=ParseMode.MARKDOWN)


# ── Play command (takes argument) ───────────────────────────────────


@router.message(Command("mpv_play", "mpv"))
async def cmd_play(message: Message, command: CommandObject) -> Any:
    query = command.args.strip() if command.args else ""
    if not query:
        text = (
            "Usage:\n"
            "  /mpv_play <number>  — play by number from /mpv_list\n"
            "  /mpv_play <name>    — search and play\n"
            "  /mpv_list           — show available playlists"
        )
        await message.reply(text)
        return
    out = await _mpvctl("play", query)
    await message.reply(f"`{out}`", parse_mode=ParseMode.MARKDOWN)


# ── Help ────────────────────────────────────────────────────────────


@router.message(Command("start", "help"))
async def cmd_help(message: Message) -> Any:
    text = (
        "🎬 *tg-mpv-bot* — mpv remote control\n\n"
        "*/mpv_pause* — Pause playback\n"
        "*/mpv_unpause* — Resume playback\n"
        "*/mpv_quit* — Stop mpv and quit\n"
        "*/mpv_volup* — Volume +10\n"
        "*/mpv_voldown* — Volume -10\n"
        "*/mpv_mute* — Toggle mute\n"
        "*/mpv_list* — List all playlists\n"
        "*/mpv_info* — Show current status\n"
        "*/mpv_fwd* — Seek forward 30s\n"
        "*/mpv_back* — Seek backward 10s\n"
        "*/mpv_play* `<query>` — Search & play a playlist\n"
        "*/mpv* `<query>` — Alias for /mpv_play\n\n"
        "_Zero LLM tokens — runs mpvctl.sh directly_"
    )
    await message.reply(text, parse_mode=ParseMode.MARKDOWN)
