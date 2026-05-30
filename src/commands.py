"""Telegram command + callback handlers for mpv control.

Thin layer: IPC commands go straight to :mod:`src.mpv_ipc`, browsing uses the
inline keyboards in :mod:`src.keyboards`, and launching defers to
:mod:`src.player`. All blocking socket/subprocess work is pushed to a thread so
the aiogram event loop never stalls.
"""

from __future__ import annotations

import asyncio
import html
import logging
from typing import Any, Awaitable, Callable, TypeVar

from aiogram import F, Router
from aiogram.enums import ParseMode
from aiogram.filters import Command, CommandObject
from aiogram.types import CallbackQuery, Message

from . import player, playlists
from .config import Settings, get_settings
from .keyboards import categories_keyboard, category_keyboard
from .mpv_ipc import MpvClient, MpvError, MpvNotRunning

logger = logging.getLogger(__name__)
router = Router(name="mpv_commands")

T = TypeVar("T")


def _client() -> MpvClient:
    return MpvClient(get_settings().mpv_socket)


async def _ipc(fn: Callable[[MpvClient], T]) -> tuple[T | None, str | None]:
    """Run a blocking IPC call in a thread.

    Returns ``(result, None)`` on success or ``(None, error_message)`` with a
    user-facing string when mpv is unreachable or rejects the command.
    """
    def run() -> T:
        return fn(_client())

    try:
        return await asyncio.to_thread(run), None
    except MpvNotRunning:
        return None, "❌ mpv is not running — use /mpv_list to start something."
    except MpvError as exc:
        return None, f"❌ mpv error: {exc}"
    except Exception as exc:  # noqa: BLE001 — surface anything else to the user
        logger.exception("IPC call failed")
        return None, f"❌ Internal error: {exc}"


async def _do(message: Message, fn: Callable[[MpvClient], Any], ok: str) -> None:
    """Run an IPC action and reply with ``ok`` (or the error)."""
    _, err = await _ipc(fn)
    await message.reply(err or ok)


# ── Playback control ────────────────────────────────────────────────


@router.message(Command("mpv_pause"))
async def cmd_pause(message: Message) -> None:
    await _do(message, lambda c: c.set_pause(True), "⏸ Paused")


@router.message(Command("mpv_unpause", "mpv_resume"))
async def cmd_unpause(message: Message) -> None:
    await _do(message, lambda c: c.set_pause(False), "▶ Resumed")


@router.message(Command("mpv_quit", "mpv_stop"))
async def cmd_quit(message: Message) -> None:
    await _do(message, lambda c: c.quit(), "⏹ Stopped")


@router.message(Command("mpv_mute"))
async def cmd_mute(message: Message) -> None:
    await _do(message, lambda c: c.cycle_mute(), "🔇 Mute toggled")


@router.message(Command("mpv_fwd", "mpv_forward"))
async def cmd_fwd(message: Message) -> None:
    await _do(message, lambda c: c.seek(30), "⏩ +30s")


@router.message(Command("mpv_back", "mpv_rewind"))
async def cmd_back(message: Message) -> None:
    await _do(message, lambda c: c.seek(-10), "⏪ -10s")


@router.message(Command("mpv_next"))
async def cmd_next(message: Message) -> None:
    await _do(message, lambda c: c.playlist_next(), "⏭ Next")


@router.message(Command("mpv_prev", "mpv_previous"))
async def cmd_prev(message: Message) -> None:
    await _do(message, lambda c: c.playlist_prev(), "⏮ Previous")


def _cycle_sub_text(client: MpvClient) -> str:
    """Switch to the next subtitle track and describe the result."""

    def safe(name: str) -> Any:
        try:
            return client.get_property(name)
        except MpvError:
            return None

    client.cycle_sub()
    sid = safe("sid")
    if not sid:  # False / None / "no" → subtitles off
        return "💬 Subtitles: off"
    label = (
        safe("current-tracks/sub/title")
        or safe("current-tracks/sub/lang")
        or f"track {sid}"
    )
    return f"💬 Subtitles: {label}"


@router.message(Command("mpv_sub", "mpv_subtitles"))
async def cmd_sub(message: Message) -> None:
    text, err = await _ipc(_cycle_sub_text)
    await message.reply(err or text)


@router.message(Command("mpv_sub_toggle"))
async def cmd_sub_toggle(message: Message) -> None:
    await _do(message, lambda c: c.toggle_sub_visibility(), "💬 Subtitles toggled")


@router.message(Command("mpv_volup"))
async def cmd_volup(message: Message) -> None:
    vol, err = await _ipc(lambda c: c.adjust_volume(10))
    await message.reply(err or f"🔊 Volume: {vol:.0f}")


@router.message(Command("mpv_voldown"))
async def cmd_voldown(message: Message) -> None:
    vol, err = await _ipc(lambda c: c.adjust_volume(-10))
    await message.reply(err or f"🔉 Volume: {vol:.0f}")


# ── Status ──────────────────────────────────────────────────────────


def _fmt_time(seconds: float | None) -> str:
    if seconds is None:
        return "?"
    seconds = int(seconds)
    h, rem = divmod(seconds, 3600)
    m, s = divmod(rem, 60)
    return f"{h}:{m:02d}:{s:02d}" if h else f"{m}:{s:02d}"


def _status_text(client: MpvClient) -> str:
    """Build a human-readable now-playing summary. Raises MpvNotRunning."""

    def safe(name: str) -> Any:
        try:
            return client.get_property(name)
        except MpvError:
            return None

    title = safe("media-title") or safe("filename")
    if not title:
        return "⏹ Nothing playing"

    pos = _fmt_time(safe("time-pos"))
    dur = _fmt_time(safe("duration"))
    vol = safe("volume")
    paused = safe("pause")

    state = "⏸ paused" if paused else "▶ playing"
    parts = [f"🎬 {title}", f"{state}   {pos} / {dur}"]
    if vol is not None:
        parts[-1] += f"   🔊 {vol:.0f}"
    return "\n".join(parts)


@router.message(Command("mpv_info", "mpv_status"))
async def cmd_info(message: Message) -> None:
    text, err = await _ipc(_status_text)
    await message.reply(err or text)


# ── Browse / play ───────────────────────────────────────────────────


def _all_playlists() -> list[playlists.Playlist]:
    return playlists.discover(get_settings().playlist_dirs)


@router.message(Command("mpv_list", "mpv_browse"))
async def cmd_list(message: Message) -> None:
    pls = await asyncio.to_thread(_all_playlists)
    if not pls:
        await message.reply("❌ No playlists found.")
        return
    await message.reply(
        f"📋 {len(pls)} playlists — pick a category:",
        reply_markup=categories_keyboard(pls),
    )


@router.callback_query(F.data == "noop")
async def cb_noop(query: CallbackQuery) -> None:
    await query.answer()


@router.callback_query(F.data == "cats")
async def cb_categories(query: CallbackQuery) -> None:
    pls = await asyncio.to_thread(_all_playlists)
    await query.message.edit_text(
        f"📋 {len(pls)} playlists — pick a category:",
        reply_markup=categories_keyboard(pls),
    )
    await query.answer()


@router.callback_query(F.data.startswith("cat:"))
async def cb_category(query: CallbackQuery) -> None:
    # cat:<category>:<page>  — category may itself contain ':' is avoided as
    # categories are dir names; split from the right for the page number.
    body = query.data[len("cat:"):]
    category, _, page_s = body.rpartition(":")
    page = int(page_s) if page_s.isdigit() else 0
    pls = await asyncio.to_thread(_all_playlists)
    await query.message.edit_text(
        f"{category} — tap to play:",
        reply_markup=category_keyboard(pls, category, page),
    )
    await query.answer()


@router.callback_query(F.data.startswith("pl:"))
async def cb_play(query: CallbackQuery) -> None:
    idx = int(query.data[len("pl:"):])
    pls = await asyncio.to_thread(_all_playlists)
    if not (0 <= idx < len(pls)):
        await query.answer("Playlist no longer available", show_alert=True)
        return
    pl = pls[idx]
    await asyncio.to_thread(player.play, get_settings(), pl.path)
    await query.answer(f"▶ {pl.name}")
    await query.message.reply(f"▶ Playing: {pl.name}")


@router.message(Command("mpv_play", "mpv"))
async def cmd_play(message: Message, command: CommandObject) -> None:
    query = (command.args or "").strip()
    if not query:
        await message.reply(
            "Usage:\n"
            "  /mpv_play <number>  — play by number\n"
            "  /mpv_play <name>    — search and play\n"
            "  /mpv_list           — browse with buttons"
        )
        return
    pls = await asyncio.to_thread(_all_playlists)
    pl = playlists.find(pls, query)
    if pl is None:
        await message.reply(f"❌ No playlist matching '{query}'. Try /mpv_list")
        return
    await asyncio.to_thread(player.play, get_settings(), pl.path)
    await message.reply(f"▶ Playing: {pl.name}")


# ── Doctor / help ───────────────────────────────────────────────────


@router.message(Command("mpv_doctor", "mpv_validate"))
async def cmd_doctor(message: Message) -> None:
    pls = await asyncio.to_thread(_all_playlists)
    results = await asyncio.to_thread(playlists.validate, pls)
    broken = [r for r in results if not r.ok]
    if not broken:
        await message.reply(f"✅ All {len(results)} playlists OK.")
        return
    lines = [f"⚠️ {len(broken)}/{len(results)} playlists have missing files:\n"]
    for r in broken[:30]:
        lines.append(f"• {r.playlist.name} — {len(r.missing)}/{r.total} missing")
    if len(broken) > 30:
        lines.append(f"…and {len(broken) - 30} more")
    text = html.escape("\n".join(lines))
    await message.reply(f"<pre>{text}</pre>", parse_mode=ParseMode.HTML)


@router.message(Command("start", "help"))
async def cmd_help(message: Message) -> None:
    text = (
        "🎬 <b>tg-mpv-bot</b> — mpv remote control\n\n"
        "<b>/mpv_list</b> — browse playlists with buttons\n"
        "<b>/mpv_play</b> &lt;query&gt; — play by number or name\n"
        "<b>/mpv_info</b> — now playing\n"
        "<b>/mpv_pause</b> · <b>/mpv_unpause</b> · <b>/mpv_quit</b>\n"
        "<b>/mpv_fwd</b> +30s · <b>/mpv_back</b> -10s\n"
        "<b>/mpv_next</b> · <b>/mpv_prev</b>\n"
        "<b>/mpv_sub</b> switch track · <b>/mpv_sub_toggle</b> show/hide\n"
        "<b>/mpv_volup</b> · <b>/mpv_voldown</b> · <b>/mpv_mute</b>\n"
        "<b>/mpv_doctor</b> — check for broken playlists\n"
    )
    await message.reply(text, parse_mode=ParseMode.HTML)
