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
from collections.abc import Callable
from typing import Any, TypeVar

from aiogram import F, Router
from aiogram.enums import ParseMode
from aiogram.exceptions import TelegramBadRequest
from aiogram.filters import Command, CommandObject
from aiogram.types import CallbackQuery, Message

from . import generate, keyboards, player, playlists, state
from .config import get_settings
from .keyboards import (
    categories_keyboard,
    has_subcategories,
    now_playing_keyboard,
    playlists_keyboard,
    search_results_keyboard,
    subcategories_keyboard,
)
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


@router.message(Command("mpv_toggle", "mpv_playpause"))
async def cmd_toggle(message: Message) -> None:
    paused, err = await _ipc(lambda c: c.toggle_pause())
    await message.reply(err or ("⏸ Paused" if paused else "▶ Resumed"))


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


def _cycle_audio_text(client: MpvClient) -> str:
    """Switch to the next audio track and describe the result."""

    def safe(name: str) -> Any:
        try:
            return client.get_property(name)
        except MpvError:
            return None

    client.cycle_audio()
    aid = safe("aid")
    if not aid:
        return "🎧 Audio: off"
    label = (
        safe("current-tracks/audio/title")
        or safe("current-tracks/audio/lang")
        or f"track {aid}"
    )
    return f"🎧 Audio: {label}"


@router.message(Command("mpv_audio", "mpv_atrack"))
async def cmd_audio(message: Message) -> None:
    text, err = await _ipc(_cycle_audio_text)
    await message.reply(err or text)


@router.message(Command("mpv_volup"))
async def cmd_volup(message: Message) -> None:
    vol, err = await _ipc(lambda c: c.adjust_volume(10))
    await message.reply(err or f"🔊 Volume: {vol:.0f}")


@router.message(Command("mpv_voldown"))
async def cmd_voldown(message: Message) -> None:
    vol, err = await _ipc(lambda c: c.adjust_volume(-10))
    await message.reply(err or f"🔉 Volume: {vol:.0f}")


@router.message(Command("mpv_shuffle"))
async def cmd_shuffle(message: Message) -> None:
    await _do(message, lambda c: c.shuffle(), "🔀 Shuffled")


@router.message(Command("mpv_loop"))
async def cmd_loop(message: Message) -> None:
    looping, err = await _ipc(lambda c: c.toggle_loop())
    await message.reply(err or ("🔁 Loop on" if looping else "➡ Loop off"))


@router.message(Command("mpv_ep", "mpv_episode"))
async def cmd_ep(message: Message, command: CommandObject) -> None:
    arg = (command.args or "").strip()
    if not arg.isdigit() or int(arg) < 1:
        await message.reply("Usage: /mpv_ep <number>  — jump to that item in the playlist")
        return
    await _do(message, lambda c: c.set_playlist_pos(int(arg) - 1), f"▶ Jumped to #{arg}")


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
    pct = safe("percent-pos")
    ppos = safe("playlist-pos-1")  # 1-based, -1/None if n/a
    pcount = safe("playlist-count")
    looping = safe("loop-playlist") not in (False, "no", None)

    state = "⏸ paused" if paused else "▶ playing"
    line2 = f"{state}   {pos} / {dur}"
    if isinstance(pct, (int, float)):
        line2 += f" ({pct:.0f}%)"
    parts = [f"🎬 {title}", line2]
    line3 = []
    if vol is not None:
        line3.append(f"🔊 {vol:.0f}")
    if isinstance(ppos, int) and ppos > 0 and isinstance(pcount, int) and pcount > 1:
        line3.append(f"▶ {ppos}/{pcount}")
    if looping:
        line3.append("🔁")
    if line3:
        parts.append("   ".join(line3))
    return "\n".join(parts)


def _panel_state(client: MpvClient) -> tuple[str, bool | None]:
    """Status text plus the current pause state (for the toggle button label)."""
    return _status_text(client), client._safe_get("pause")


async def _send_panel(message: Message, *, edit: bool = False) -> None:
    """Render the now-playing panel (status text + transport buttons)."""
    state, err = await _ipc(_panel_state)
    body, paused = (state[0], state[1]) if state else (err, None)
    kb = now_playing_keyboard(paused)
    if edit:
        try:
            await message.edit_text(body, reply_markup=kb)
        except TelegramBadRequest:
            pass  # "message is not modified" when nothing changed — ignore
    else:
        await message.reply(body, reply_markup=kb)


@router.message(Command("mpv_info", "mpv_status", "mpv_panel"))
async def cmd_info(message: Message) -> None:
    await _send_panel(message)


_CTL_ACTIONS: dict[str, Callable[[MpvClient], Any]] = {
    "toggle": lambda c: c.toggle_pause(),
    "back": lambda c: c.seek(-10),
    "fwd": lambda c: c.seek(30),
    "prev": lambda c: c.playlist_prev(),
    "next": lambda c: c.playlist_next(),
    "volup": lambda c: c.adjust_volume(10),
    "voldown": lambda c: c.adjust_volume(-10),
    "mute": lambda c: c.cycle_mute(),
    "sub": lambda c: c.cycle_sub(),
    "audio": lambda c: c.cycle_audio(),
    "shuffle": lambda c: c.shuffle(),
    "loop": lambda c: c.toggle_loop(),
    "stop": lambda c: c.quit(),
    "refresh": lambda c: None,
}


@router.callback_query(F.data.startswith("ctl:"))
async def cb_ctl(query: CallbackQuery) -> None:
    action = query.data[len("ctl:"):]
    fn = _CTL_ACTIONS.get(action)
    if fn is None:
        await query.answer()
        return
    _, err = await _ipc(fn)
    await query.answer(err.replace("❌ ", "") if err else "✓")
    await _send_panel(query.message, edit=True)


# ── Browse / play ───────────────────────────────────────────────────


_cache: list[playlists.Playlist] | None = None


def _all_playlists(*, refresh: bool = False) -> list[playlists.Playlist]:
    """Discovered playlists, cached.

    The media library lives on a spinning external disk, so re-scanning on
    every button tap can stall (drive spin-up). We scan on the explicit
    entry points (``/mpv_list``, ``/mpv_play``, ``/mpv_doctor``) and reuse the
    cached list for callback navigation — which also keeps the global indices
    encoded in button callbacks stable for the duration of a browse session.
    """
    global _cache
    if refresh or _cache is None:
        _cache = playlists.discover(get_settings().playlist_dirs)
    return _cache


def refresh_cache() -> None:
    """Force a re-scan of the playlist cache (used by the auto-scan loop)."""
    _all_playlists(refresh=True)


@router.message(Command("mpv_list", "mpv_browse"))
async def cmd_list(message: Message) -> None:
    pls = await asyncio.to_thread(_all_playlists, refresh=True)
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


@router.callback_query(F.data.startswith("c:"))
async def cb_category(query: CallbackQuery) -> None:
    # c:<ci>  → subcat menu (if any) or playlist page 0
    # c:<ci>:<page>  → flat-category playlist page
    parts = query.data.split(":")
    pls = await asyncio.to_thread(_all_playlists)
    cats = keyboards.categories(pls)
    ci = int(parts[1])
    if not (0 <= ci < len(cats)):
        await query.answer("Category no longer available", show_alert=True)
        return
    cat = cats[ci]
    if len(parts) == 2 and has_subcategories(pls, cat):
        await query.message.edit_text(
            f"{cat} — pick a section:",
            reply_markup=subcategories_keyboard(pls, ci),
        )
    else:
        page = int(parts[2]) if len(parts) > 2 and parts[2].isdigit() else 0
        await query.message.edit_text(
            f"{cat} — tap to play:",
            reply_markup=playlists_keyboard(pls, ci, None, page),
        )
    await query.answer()


@router.callback_query(F.data.startswith("s:"))
async def cb_subcategory(query: CallbackQuery) -> None:
    # s:<ci>:<si>[:<page>]
    parts = query.data.split(":")
    pls = await asyncio.to_thread(_all_playlists)
    cats = keyboards.categories(pls)
    ci = int(parts[1])
    if not (0 <= ci < len(cats)):
        await query.answer("Section no longer available", show_alert=True)
        return
    cat = cats[ci]
    subs = keyboards.subcategories(pls, cat)
    si = int(parts[2])
    if not (0 <= si < len(subs)):
        await query.answer("Section no longer available", show_alert=True)
        return
    page = int(parts[3]) if len(parts) > 3 and parts[3].isdigit() else 0
    await query.message.edit_text(
        f"{cat} / {keyboards.prettify(subs[si])} — tap to play:",
        reply_markup=playlists_keyboard(pls, ci, si, page),
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
    await query.answer(f"▶ {pl.display}")
    await query.message.reply(f"▶ Playing: {pl.display}")


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
    pls = await asyncio.to_thread(_all_playlists, refresh=True)
    pl = playlists.find(pls, query)
    if pl is None:
        await message.reply(f"❌ No playlist matching '{query}'. Try /mpv_list")
        return
    await asyncio.to_thread(player.play, get_settings(), pl.path)
    await message.reply(f"▶ Playing: {pl.display}")


@router.message(Command("mpv_last", "mpv_continue"))
async def cmd_last(message: Message) -> None:
    """Relaunch the last-played playlist (mpv restores the position itself)."""
    last = state.last_played(get_settings().state_file)
    if last is None:
        await message.reply("❌ No watch history yet — play something first.")
        return
    await asyncio.to_thread(player.play, get_settings(), last)
    await message.reply(f"▶ Resuming: {playlists.prettify(last.stem)}")


@router.message(Command("mpv_search", "mpv_find"))
async def cmd_search(message: Message, command: CommandObject) -> None:
    """Search playlists and show every hit as a play button.

    Unlike ``/mpv_play <name>`` (plays the *first* match), this lists all
    matches. A leading category name scopes the search:
    ``/mpv_search tutorials docker``.
    """
    raw = (command.args or "").strip()
    if not raw:
        await message.reply(
            "Usage:\n"
            "  /mpv_search <text>             — search all playlists\n"
            "  /mpv_search <category> <text>  — search one category\n"
            "                e.g. /mpv_search tutorials docker"
        )
        return
    pls = await asyncio.to_thread(_all_playlists, refresh=True)

    # A first word naming a category scopes the search to it.
    category: str | None = None
    text = raw
    head, _, rest = raw.partition(" ")
    cats = {c.lower(): c for c in keyboards.categories(pls)}
    if rest.strip() and head.lower() in cats:
        category, text = cats[head.lower()], rest.strip()

    indices = playlists.search(pls, text, category=category)
    scope = f" in {category}" if category else ""
    if not indices:
        await message.reply(f"❌ Nothing matching '{text}'{scope}. Try /mpv_list")
        return
    title = f"🔍 {len(indices)} match(es) for '{text}'{scope} — tap to play:"
    if len(indices) > keyboards.MAX_SEARCH_RESULTS:
        title += f"\nShowing the first {keyboards.MAX_SEARCH_RESULTS} — refine your search."
    await message.reply(title, reply_markup=search_results_keyboard(pls, indices))


# ── Doctor / help ───────────────────────────────────────────────────


@router.message(Command("mpv_doctor", "mpv_validate"))
async def cmd_doctor(message: Message) -> None:
    pls = await asyncio.to_thread(_all_playlists, refresh=True)
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
    lines.append("\nRun /mpv_fix to re-point moved files and prune dead entries.")
    text = html.escape("\n".join(lines))
    await message.reply(f"<pre>{text}</pre>", parse_mode=ParseMode.HTML)


@router.message(Command("mpv_fix", "mpv_repair"))
async def cmd_fix(message: Message) -> None:
    fixed = await asyncio.to_thread(generate.repair_playlists, get_settings())
    await asyncio.to_thread(_all_playlists, refresh=True)
    if not fixed:
        await message.reply("✅ Nothing to fix — no recoverable broken playlists.")
        return
    lines = [f"🔧 Repaired {len(fixed)} playlist(s) (backups saved as *.m3u.bak):\n"]
    lines += [f"• {f}" for f in fixed[:30]]
    if len(fixed) > 30:
        lines.append(f"…and {len(fixed) - 30} more")
    text = html.escape("\n".join(lines))
    await message.reply(f"<pre>{text}</pre>", parse_mode=ParseMode.HTML)


@router.message(Command("mpv_scan", "mpv_refresh"))
async def cmd_scan(message: Message) -> None:
    created = await asyncio.to_thread(generate.generate_missing, get_settings())
    await asyncio.to_thread(_all_playlists, refresh=True)  # pick the new ones up now
    if not created:
        await message.reply("✅ No new media — playlists already up to date.")
        return
    lines = [f"➕ Added {len(created)} playlist(s):"]
    lines += [f"• {c}" for c in created[:30]]
    if len(created) > 30:
        lines.append(f"…and {len(created) - 30} more")
    text = html.escape("\n".join(lines))
    await message.reply(f"<pre>{text}</pre>", parse_mode=ParseMode.HTML)


@router.message(Command("start", "help"))
async def cmd_help(message: Message) -> None:
    text = (
        "🎬 <b>tg-mpv-bot</b> — mpv remote control\n\n"
        "<b>/mpv_list</b> — browse playlists with buttons\n"
        "<b>/mpv_play</b> &lt;query&gt; — play by number or name\n"
        "<b>/mpv_search</b> [category] &lt;text&gt; — find playlists, tap to play\n"
        "<b>/mpv_last</b> — resume the last-played playlist\n"
        "<b>/mpv_info</b> — now-playing panel with controls\n"
        "<b>/mpv_toggle</b> — play/pause (one tap)\n"
        "<b>/mpv_pause</b> · <b>/mpv_unpause</b> · <b>/mpv_quit</b>\n"
        "<b>/mpv_fwd</b> +30s · <b>/mpv_back</b> -10s\n"
        "<b>/mpv_next</b> · <b>/mpv_prev</b> · <b>/mpv_ep</b> &lt;n&gt; jump\n"
        "<b>/mpv_shuffle</b> · <b>/mpv_loop</b>\n"
        "<b>/mpv_audio</b> switch audio track\n"
        "<b>/mpv_sub</b> switch subtitle · <b>/mpv_sub_toggle</b> show/hide\n"
        "<b>/mpv_volup</b> · <b>/mpv_voldown</b> · <b>/mpv_mute</b>\n"
        "<b>/mpv_doctor</b> — check for broken playlists\n"
        "<b>/mpv_fix</b> — repair broken playlists\n"
        "<b>/mpv_scan</b> — create playlists for newly-added media\n"
    )
    await message.reply(text, parse_mode=ParseMode.HTML)
