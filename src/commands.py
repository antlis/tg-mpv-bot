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
import random
import re
import tempfile
import time
import uuid
from collections.abc import Callable
from pathlib import Path, PurePath
from typing import Any, TypeVar

from aiogram import F, Router
from aiogram.enums import ParseMode
from aiogram.exceptions import TelegramBadRequest
from aiogram.filters import Command, CommandObject
from aiogram.types import CallbackQuery, FSInputFile, Message

from . import generate, keyboards, player, playlists, state
from .config import get_settings
from .keyboards import (
    PER_PAGE,
    categories_keyboard,
    chapters_keyboard,
    episodes_keyboard,
    has_subcategories,
    history_keyboard,
    now_playing_keyboard,
    playlists_keyboard,
    search_results_keyboard,
    speed_keyboard,
    subcategories_keyboard,
    yt_results_keyboard,
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


def _episode_list(client: MpvClient) -> tuple[list[str], int | None]:
    """Display names + current index of mpv's playlist (for the picker)."""
    names: list[str] = []
    current: int | None = None
    for i, item in enumerate(client.get_playlist()):
        if item.get("current"):
            current = i
        title = item.get("title") or PurePath(item.get("filename", "")).stem
        names.append(playlists.prettify(title)[:48] if title else f"item {i + 1}")
    return names, current


def _episodes_text(names: list[str], current: int | None) -> str:
    where = f" (now: #{current + 1})" if current is not None else ""
    return f"📜 {len(names)} items{where} — tap to jump:"


@router.message(Command("mpv_ep", "mpv_episode"))
async def cmd_ep(message: Message, command: CommandObject) -> None:
    arg = (command.args or "").strip()
    if arg.isdigit() and int(arg) >= 1:
        await _do(message, lambda c: c.set_playlist_pos(int(arg) - 1), f"▶ Jumped to #{arg}")
        return
    if arg:
        await message.reply(
            "Usage:\n"
            "  /mpv_ep           — pick an episode with buttons\n"
            "  /mpv_ep <number>  — jump straight to that item"
        )
        return
    res, err = await _ipc(_episode_list)
    if err:
        await message.reply(err)
        return
    names, current = res
    if not names:
        await message.reply("⏹ Playlist is empty")
        return
    page = (current or 0) // PER_PAGE  # open on the page with the current item
    await message.reply(
        _episodes_text(names, current),
        reply_markup=episodes_keyboard(names, current, page),
    )


async def _refresh_episode_picker(query: CallbackQuery, page: int) -> None:
    """Re-render the picker message (current marker / page changed)."""
    res, err = await _ipc(_episode_list)
    if err or not res[0]:
        return  # leave the old picker; the tap itself was already answered
    names, current = res
    try:
        await query.message.edit_text(
            _episodes_text(names, current),
            reply_markup=episodes_keyboard(names, current, page),
        )
    except TelegramBadRequest:
        pass  # "message is not modified" — ignore


@router.callback_query(F.data.startswith("eps:"))
async def cb_episodes_page(query: CallbackQuery) -> None:
    await _refresh_episode_picker(query, int(query.data[len("eps:"):]))
    await query.answer()


@router.callback_query(F.data.startswith("ep:"))
async def cb_episode(query: CallbackQuery) -> None:
    n = int(query.data[len("ep:"):])
    _, err = await _ipc(lambda c: c.set_playlist_pos(n))
    await query.answer(err.replace("❌ ", "") if err else f"▶ #{n + 1}")
    if not err:
        await _refresh_episode_picker(query, n // PER_PAGE)


def _parse_goto(arg: str) -> tuple[str, float] | None:
    """``1:23:45`` / ``23:45`` / ``90`` → ('time', seconds); ``75%`` → ('percent', 75)."""
    arg = arg.strip()
    if not arg:
        return None
    if arg.endswith("%"):
        try:
            pct = float(arg[:-1])
        except ValueError:
            return None
        return ("percent", pct) if 0 <= pct <= 100 else None
    parts = arg.split(":")
    if len(parts) > 3:
        return None
    try:
        nums = [float(p) for p in parts]
    except ValueError:
        return None
    if any(n < 0 for n in nums):
        return None
    seconds = 0.0
    for n in nums:
        seconds = seconds * 60 + n
    return ("time", seconds)


@router.message(Command("mpv_goto", "mpv_seek"))
async def cmd_goto(message: Message, command: CommandObject) -> None:
    parsed = _parse_goto(command.args or "")
    if parsed is None:
        await message.reply(
            "Usage: /mpv_goto <position>\n"
            "  /mpv_goto 1:23:45   — h:mm:ss\n"
            "  /mpv_goto 23:45     — mm:ss\n"
            "  /mpv_goto 90        — seconds\n"
            "  /mpv_goto 75%       — percent of the file"
        )
        return
    kind, value = parsed
    if kind == "percent":
        _, err = await _ipc(lambda c: c.seek_percent(value))
        await message.reply(err or f"⏩ → {value:g}%")
    else:
        _, err = await _ipc(lambda c: c.seek_absolute(value))
        await message.reply(err or f"⏩ → {_fmt_time(value)}")


# ── Chapters ────────────────────────────────────────────────────────


def _chapters_text(n: int, current: int | None) -> str:
    where = f" (now: #{current + 1})" if current is not None else ""
    return f"📖 {n} chapters{where} — tap to jump:"


@router.message(Command("mpv_chapters", "mpv_ch"))
async def cmd_chapters(message: Message) -> None:
    """Browse the current file's chapters as jump buttons."""
    res, err = await _ipc(lambda c: c.get_chapters())
    if err:
        await message.reply(err)
        return
    chapters, current = res
    if not chapters:
        await message.reply("📖 This file has no chapters")
        return
    page = (current or 0) // PER_PAGE
    await message.reply(
        _chapters_text(len(chapters), current),
        reply_markup=chapters_keyboard(chapters, current, page),
    )


async def _refresh_chapters(query: CallbackQuery, page: int) -> None:
    res, err = await _ipc(lambda c: c.get_chapters())
    if err or not res[0]:
        return  # keep the old picker; the tap was already answered
    chapters, current = res
    try:
        await query.message.edit_text(
            _chapters_text(len(chapters), current),
            reply_markup=chapters_keyboard(chapters, current, page),
        )
    except TelegramBadRequest:
        pass


@router.callback_query(F.data.startswith("chs:"))
async def cb_chapters_page(query: CallbackQuery) -> None:
    await _refresh_chapters(query, int(query.data[len("chs:"):]))
    await query.answer()


@router.callback_query(F.data.startswith("ch:"))
async def cb_chapter(query: CallbackQuery) -> None:
    n = int(query.data[len("ch:"):])
    _, err = await _ipc(lambda c: c.set_chapter(n))
    await query.answer(err.replace("❌ ", "") if err else f"📖 #{n + 1}")
    if not err:
        await _refresh_chapters(query, n // PER_PAGE)


@router.message(Command("mpv_random", "mpv_surprise"))
async def cmd_random(message: Message, command: CommandObject) -> None:
    """Play a random playlist — optionally from one category."""
    arg = (command.args or "").strip()
    pls = await asyncio.to_thread(_all_playlists, refresh=True)
    if not pls:
        await message.reply("❌ No playlists found.")
        return
    pool = pls
    if arg:
        cats = {c.lower(): c for c in keyboards.categories(pls)}
        cat = cats.get(arg.lower())
        if cat is None:
            await message.reply(
                f"❌ No category '{arg}'. Have: {', '.join(sorted(cats.values()))}"
            )
            return
        pool = [p for p in pls if p.category == cat]
    pl = random.choice(pool)
    _remember_chat(message)
    await asyncio.to_thread(player.play, get_settings(), pl.path)
    await message.reply(f"🎲 Random pick: {pl.display}  ({pl.category})")


@router.message(Command("mpv_night"))
async def cmd_night(message: Message) -> None:
    """Toggle loudness normalization for late-night viewing."""
    on, err = await _ipc(lambda c: c.toggle_night())
    await message.reply(
        err or ("🌙 Night mode ON — quiet dialogue up, explosions down"
                if on else "🔊 Night mode off")
    )


# ── Sleep timer ─────────────────────────────────────────────────────

_sleep_task: asyncio.Task | None = None
_sleep_until: float = 0.0  # event-loop clock; meaningful while _sleep_task runs


def _parse_sleep(arg: str) -> int | None:
    """``45`` / ``45m`` / ``90min`` / ``2h`` / ``1.5h`` → whole minutes."""
    m = re.fullmatch(r"(\d+(?:\.\d+)?)\s*(m|min|h|hour)?", arg)
    if not m:
        return None
    value = float(m.group(1)) * (60 if m.group(2) in ("h", "hour") else 1)
    minutes = round(value)
    return minutes if 1 <= minutes <= 24 * 60 else None


async def _sleep_fire(message: Message, minutes: int) -> None:
    global _sleep_task
    try:
        await asyncio.sleep(minutes * 60)
        _, err = await _ipc(lambda c: c.quit())
        await message.answer(
            "😴 Sleep timer: stopped playback" if not err
            else "😴 Sleep timer fired — nothing was playing"
        )
    finally:
        _sleep_task = None


@router.message(Command("mpv_sleep"))
async def cmd_sleep(message: Message, command: CommandObject) -> None:
    """Stop playback after N minutes (fall-asleep mode)."""
    global _sleep_task, _sleep_until
    arg = (command.args or "").strip().lower()
    if not arg:
        if _sleep_task:
            left = max(0, _sleep_until - asyncio.get_running_loop().time())
            await message.reply(
                f"😴 Sleep timer active — stopping in {left / 60:.0f} min. "
                "/mpv_sleep off to cancel."
            )
        else:
            await message.reply(
                "Usage: /mpv_sleep <time>  — stop playback after e.g. 45m, 1.5h\n"
                "  /mpv_sleep off — cancel"
            )
        return
    if arg in ("off", "cancel", "stop"):
        if _sleep_task:
            _sleep_task.cancel()
            _sleep_task = None
            await message.reply("⏰ Sleep timer cancelled")
        else:
            await message.reply("No sleep timer is set")
        return
    minutes = _parse_sleep(arg)
    if minutes is None:
        await message.reply("Can't parse that — try 30, 45m or 1.5h (max 24h)")
        return
    if _sleep_task:
        _sleep_task.cancel()
    _sleep_until = asyncio.get_running_loop().time() + minutes * 60
    _sleep_task = asyncio.create_task(_sleep_fire(message, minutes))
    await message.reply(f"😴 Will stop playback in {minutes} min (/mpv_sleep off to cancel)")


def _speed_text(speed: float) -> str:
    return f"⏩ Speed: {speed:g}× — pick:"


@router.message(Command("mpv_speed"))
async def cmd_speed(message: Message, command: CommandObject) -> None:
    arg = (command.args or "").strip().rstrip("x×")
    if arg:
        try:
            val = float(arg)
        except ValueError:
            await message.reply("Usage: /mpv_speed [value]  — e.g. /mpv_speed 1.5, or no arg for buttons")
            return
        speed, err = await _ipc(lambda c: c.set_speed(val))
        await message.reply(err or f"⏩ Speed: {speed:g}×")
        return
    speed, err = await _ipc(lambda c: c.get_property("speed"))
    if err:
        await message.reply(err)
        return
    await message.reply(_speed_text(speed), reply_markup=speed_keyboard(speed))


@router.callback_query(F.data.startswith("spd:"))
async def cb_speed(query: CallbackQuery) -> None:
    val = float(query.data[len("spd:"):])
    speed, err = await _ipc(lambda c: c.set_speed(val))
    await query.answer(err.replace("❌ ", "") if err else f"{speed:g}×")
    if not err:
        try:
            await query.message.edit_text(_speed_text(speed), reply_markup=speed_keyboard(speed))
        except TelegramBadRequest:
            pass  # same speed tapped twice — nothing to update


# ── Screenshot ──────────────────────────────────────────────────────


def _take_screenshot(client: MpvClient) -> tuple[str, str]:
    """Save the current frame to a temp file; returns ``(path, caption)``.

    mpv's screenshot command can complete asynchronously (the IPC reply may
    arrive before the file hits disk), so poll briefly for the file.
    """
    path = Path(tempfile.gettempdir()) / f"tg-mpv-shot-{uuid.uuid4().hex}.jpg"
    client.screenshot_to_file(str(path))
    for _ in range(20):  # up to ~2s
        if path.is_file() and path.stat().st_size > 0:
            break
        time.sleep(0.1)
    title = client._safe_get("media-title") or "screenshot"
    pos = _fmt_time(client._safe_get("time-pos"))
    dur = _fmt_time(client._safe_get("duration"))
    return str(path), f"📸 {title} — {pos} / {dur}"


@router.message(Command("mpv_shot", "mpv_screenshot"))
async def cmd_shot(message: Message) -> None:
    res, err = await _ipc(_take_screenshot)
    if err:
        await message.reply(err)
        return
    path, caption = res
    shot = Path(path)
    if not shot.is_file() or shot.stat().st_size == 0:
        shot.unlink(missing_ok=True)
        await message.reply("❌ mpv produced no screenshot (is video playing?)")
        return
    try:
        await message.reply_photo(FSInputFile(path), caption=caption)
    finally:
        shot.unlink(missing_ok=True)


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
    "p0": lambda c: c.seek_percent(0),
    "p25": lambda c: c.seek_percent(25),
    "p50": lambda c: c.seek_percent(50),
    "p75": lambda c: c.seek_percent(75),
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
    _remember_chat(query.message)
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
    _remember_chat(message)
    await asyncio.to_thread(player.play, get_settings(), pl.path)
    await message.reply(f"▶ Playing: {pl.display}")


@router.message(Command("mpv_last", "mpv_continue"))
async def cmd_last(message: Message) -> None:
    """Relaunch the last-played playlist or URL (mpv restores the position)."""
    last = state.last_played(get_settings().state_file)
    if last is None:
        await message.reply("❌ No watch history yet — play something first.")
        return
    await _replay(message, last)


def _remember_chat(message: Message) -> None:
    """Point playback notifications at the chat that started playback."""
    state.set_notify_chat(get_settings().state_file, message.chat.id)


async def _replay(message: Message, target: str) -> None:
    """Launch a history target — URL, playlist or media file — the right way.

    The distinction matters: playlists go to mpv as ``--playlist=…``, but a
    media file (e.g. a downloaded Telegram video) handed to that flag makes
    mpv parse the video bytes as a playlist and play nothing.
    """
    if target.startswith(("http://", "https://")):
        await _play_url(message, target)
        return
    _remember_chat(message)
    path = Path(target)
    title = playlists.prettify(path.stem)
    if path.suffix.lower() == ".m3u":
        await asyncio.to_thread(player.play, get_settings(), path)
    else:
        await asyncio.to_thread(player.play_file, get_settings(), path, path.stem)
    await message.reply(f"▶ Playing: {title}")


@router.message(Command("mpv_notify"))
async def cmd_notify(message: Message) -> None:
    """Toggle the episode-finished / playlist-done notifications."""
    sf = get_settings().state_file
    enabled = not state.notify_enabled(sf)
    state.set_notify_enabled(sf, enabled)
    await message.reply(
        "🔔 Playback notifications ON — I'll message when an episode ends"
        if enabled else "🔕 Playback notifications OFF"
    )


@router.message(Command("mpv_history", "mpv_recent"))
async def cmd_history(message: Message) -> None:
    entries = state.history(get_settings().state_file)
    if not entries:
        await message.reply("❌ No watch history yet — play something first.")
        return
    await message.reply(
        f"🕘 Last {len(entries)} played — tap to replay:",
        reply_markup=history_keyboard(entries),
    )


@router.callback_query(F.data.startswith("h:"))
async def cb_history(query: CallbackQuery) -> None:
    i = int(query.data[len("h:"):])
    entries = state.history(get_settings().state_file)
    if not (0 <= i < len(entries)):
        await query.answer("History changed — run /mpv_history again", show_alert=True)
        return
    await query.answer(f"▶ {entries[i].name[:60]}")
    await _replay(query.message, entries[i].target)


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


@router.message(Command("mpv_yt", "mpv_youtube"))
async def cmd_yt(message: Message, command: CommandObject) -> None:
    """Search YouTube and offer the top hits as tap-to-play buttons."""
    query = (command.args or "").strip()
    if not query:
        await message.reply("Usage: /mpv_yt <search terms>  — top results, tap to play")
        return
    note = await message.reply(f"🔎 Searching YouTube for '{query}'…")
    try:
        results = await asyncio.to_thread(player.search_youtube, get_settings(), query)
    except player.UrlPlaybackError as exc:
        await note.edit_text(f"❌ Search failed: {exc}")
        return
    caption = f"🔎 Results for '{query}' — tap to play:"
    kb = yt_results_keyboard(results)
    # Lead with the top hit's thumbnail (Telegram fetches the URL itself);
    # a text message can't become a photo, so replace the placeholder.
    thumb = results[0].get("thumb")
    if thumb:
        try:
            await message.reply_photo(thumb, caption=caption, reply_markup=kb)
            await note.delete()
            return
        except TelegramBadRequest:
            pass  # bad/oversized thumbnail — fall back to plain text
    await note.edit_text(caption, reply_markup=kb)


@router.callback_query(F.data.startswith("yt:"))
async def cb_yt(query: CallbackQuery) -> None:
    video_id = query.data[len("yt:"):]
    await query.answer("▶ Starting…")
    await _play_url(query.message, f"https://www.youtube.com/watch?v={video_id}")


# ── Telegram media files ────────────────────────────────────────────

_FILES_DIR = Path(tempfile.gettempdir()) / "tg-mpv-files"
# A TELEGRAM_LOCAL-mode Bot API server returns paths under this prefix.
_LOCAL_API_PREFIX = "/var/lib/telegram-bot-api"
# getFile doesn't answer until the server has pulled the whole file from
# Telegram's datacenter — minutes for a 2 GB video, so not the default 60s.
_FILE_TIMEOUT = 1800


def _local_api_path(file_path: str) -> Path | None:
    """Map a TELEGRAM_LOCAL getFile path to the host bind mount (if set)."""
    files_dir = get_settings().api_local_files_dir
    if files_dir and file_path.startswith(_LOCAL_API_PREFIX):
        return Path(file_path.replace(_LOCAL_API_PREFIX, files_dir, 1)).expanduser()
    return None


async def _fetch_media(message: Message, media: Any, dest: Path) -> Path:
    """Make the file playable locally; returns the path mpv should open.

    With a TELEGRAM_LOCAL server the file already lands on our disk — play
    it from there instead of copying gigabytes through /tmp (tmpfs = RAM).
    """
    file = await message.bot.get_file(media.file_id, request_timeout=_FILE_TIMEOUT)
    local = _local_api_path(file.file_path or "")
    if local is not None and local.is_file():
        return local
    await message.bot.download_file(file.file_path, destination=dest, timeout=_FILE_TIMEOUT)
    return dest


@router.message(F.video | F.audio | F.document)
async def msg_media_file(message: Message) -> None:
    """Send/forward a video or audio file → it plays on the TV."""
    media = message.video or message.audio or message.document
    if message.document and not (
        (message.document.mime_type or "").startswith(("video/", "audio/"))
    ):
        return  # not a media document — none of our business
    name = Path(getattr(media, "file_name", None) or f"file-{media.file_unique_id}.mp4").name
    size_mb = (media.file_size or 0) / 1_000_000
    note = await message.reply(f"⬇️ Fetching {name} ({size_mb:.0f} MB)…")

    _FILES_DIR.mkdir(exist_ok=True)
    for old in _FILES_DIR.iterdir():  # one file plays at a time — drop the previous
        old.unlink(missing_ok=True)

    task = asyncio.create_task(_fetch_media(message, media, _FILES_DIR / name))
    started = time.monotonic()
    while True:  # live elapsed while Telegram transfers a big file
        done, _ = await asyncio.wait({task}, timeout=4)
        if done:
            break
        try:
            await note.edit_text(
                f"⬇️ Fetching {name} ({size_mb:.0f} MB)… {time.monotonic() - started:.0f}s"
            )
        except TelegramBadRequest:
            pass
    try:
        path = task.result()
    except Exception as exc:  # noqa: BLE001 — surface the reason (e.g. 20MB API cap)
        hint = (
            "\nFiles over 20 MB need a local Bot API server (API_SERVER_URL)."
            if not get_settings().api_server_url and size_mb > 19
            else ""
        )
        await note.edit_text(f"❌ Download failed: {exc}{hint}")
        return

    _remember_chat(message)
    title = Path(name).stem
    await asyncio.to_thread(player.play_file, get_settings(), path, title)
    await note.edit_text(f"▶ Playing: {title}")


# Anchored and whitespace-free: only a message that *is* a URL triggers
# playback, and the scheme anchor means it can never be parsed as an mpv flag.
_URL_RE = re.compile(r"^https?://\S+$")


_STAGE_TEXT = {
    "resolving": "⏳ Resolving link…",
    "escalating": "🍪 Site wants sign-in — retrying with browser cookies…",
    "subs": "💬 Fetching subtitles…",
    "starting": "▶ Starting playback…",
}
_SPINNER = "◔◑◕●"


async def _play_url(message: Message, url: str) -> None:
    _remember_chat(message)
    note = await message.reply(_STAGE_TEXT["resolving"])
    stage = {"name": "resolving"}  # written by the worker thread, read here
    started = time.monotonic()

    task = asyncio.create_task(
        asyncio.to_thread(
            player.play_url, get_settings(), url,
            lambda name: stage.__setitem__("name", name),
        )
    )
    # Live status while the probe runs: stage + spinner + elapsed seconds
    # (a real percentage doesn't exist — extraction time is up to the site).
    tick = 0
    while True:
        done, _ = await asyncio.wait({task}, timeout=3)
        if done:
            break
        tick += 1
        status = (
            f"{_STAGE_TEXT[stage['name']]} {_SPINNER[tick % len(_SPINNER)]} "
            f"{time.monotonic() - started:.0f}s"
        )
        try:
            await note.edit_text(status)
        except TelegramBadRequest:
            pass  # unchanged text / message gone — keep waiting either way
    try:
        text = f"▶ Streaming: {task.result()}"
    except player.UrlPlaybackError as exc:
        text = f"❌ Can't play that link: {exc}"
    try:
        await note.edit_text(text)
    except TelegramBadRequest:
        pass


@router.message(Command("mpv_url", "mpv_stream"))
async def cmd_url(message: Message, command: CommandObject) -> None:
    url = (command.args or "").strip()
    if not _URL_RE.match(url):
        await message.reply(
            "Usage: /mpv_url <link>\n"
            "Plays YouTube / SoundCloud / Twitter / Instagram / … via yt-dlp.\n"
            "Tip: just sending a link as a message works too."
        )
        return
    await _play_url(message, url)


@router.message(F.text.regexp(_URL_RE))
async def msg_url(message: Message) -> None:
    """A bare URL message plays it directly — no command needed."""
    await _play_url(message, message.text.strip())


# ── Doctor / help ───────────────────────────────────────────────────


@router.message(Command("mpv_update_ytdlp"))
async def cmd_update_ytdlp(message: Message) -> None:
    """Update the venv's yt-dlp nightly (the fix when YouTube breaks)."""
    note = await message.reply("⏳ Updating yt-dlp to the latest nightly…")
    result = await asyncio.to_thread(player.update_ytdlp)
    try:
        await note.edit_text(result)
    except TelegramBadRequest:
        pass


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
        "<b>/mpv_last</b> — resume the last-played playlist/stream\n"
        "<b>/mpv_history</b> — recently played, tap to replay\n"
        "<b>/mpv_notify</b> — toggle episode-finished notifications\n"
        "<b>/mpv_url</b> &lt;link&gt; — stream YouTube/SoundCloud/… (or just send a link)\n"
        "<b>/mpv_yt</b> &lt;search&gt; — search YouTube, tap a result to play\n"
        "…or just <b>send a video/audio file</b> — it plays on the TV\n"
        "<b>/mpv_info</b> — now-playing panel with controls\n"
        "<b>/mpv_shot</b> — screenshot the current frame to chat\n"
        "<b>/mpv_toggle</b> — play/pause (one tap)\n"
        "<b>/mpv_pause</b> · <b>/mpv_unpause</b> · <b>/mpv_quit</b>\n"
        "<b>/mpv_fwd</b> +30s · <b>/mpv_back</b> -10s · <b>/mpv_goto</b> &lt;pos&gt;\n"
        "<b>/mpv_next</b> · <b>/mpv_prev</b> · <b>/mpv_ep</b> [n] episode picker/jump\n"
        "<b>/mpv_chapters</b> — chapter picker for the current file\n"
        "<b>/mpv_speed</b> [x] — playback speed (buttons or value)\n"
        "<b>/mpv_sleep</b> &lt;time&gt; — stop playback after e.g. 45m / 1.5h\n"
        "<b>/mpv_random</b> [category] — play a random playlist\n"
        "<b>/mpv_night</b> — loudness normalization for late-night viewing\n"
        "<b>/mpv_shuffle</b> · <b>/mpv_loop</b>\n"
        "<b>/mpv_audio</b> switch audio track\n"
        "<b>/mpv_sub</b> switch subtitle · <b>/mpv_sub_toggle</b> show/hide\n"
        "<b>/mpv_volup</b> · <b>/mpv_voldown</b> · <b>/mpv_mute</b>\n"
        "<b>/mpv_doctor</b> — check for broken playlists\n"
        "<b>/mpv_fix</b> — repair broken playlists\n"
        "<b>/mpv_scan</b> — create playlists for newly-added media\n"
        "<b>/mpv_update_ytdlp</b> — update yt-dlp (when YouTube breaks)\n"
    )
    await message.reply(text, parse_mode=ParseMode.HTML)
