"""IPTV channel search and streaming via the iptv-org public catalogue.

/mpv_iptv <name>   — search and pick a channel to stream live
/mpv_iptv          — show links to browse the catalogue
"""

from __future__ import annotations

import asyncio
import logging
import re
import time
import urllib.request

from aiogram import F, Router
from aiogram.filters import Command, CommandObject
from aiogram.types import (
    CallbackQuery,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    Message,
)

from . import player, state
from .config import get_settings

logger = logging.getLogger(__name__)

iptv_router = Router(name="iptv")

_INDEX_URL = "https://iptv-org.github.io/iptv/index.m3u"
_REPO_URL = "https://github.com/iptv-org/iptv"
_COUNTRY_PLAYLISTS_URL = f"{_REPO_URL}#playlists-by-country"
_CACHE_TTL = 12 * 3600

_channels: list[dict] = []
_cache_ts: float = 0.0
_cache_lock = asyncio.Lock()

# chat_id → list of channel dicts from the most recent search
_results: dict[int, list[dict]] = {}


# ── M3U parsing ───────────────────────────────────────────────────────────────


def _parse_m3u(text: str) -> list[dict]:
    out: list[dict] = []
    lines = text.splitlines()
    i = 0
    while i < len(lines):
        line = lines[i].strip()
        if line.startswith("#EXTINF:"):
            nm = re.search(r'tvg-name="([^"]*)"', line)
            lm = re.search(r'tvg-logo="([^"]*)"', line)
            cm = re.search(r'tvg-country="([^"]*)"', line)
            display = line.rsplit(",", 1)[-1].strip() if "," in line else ""
            name = (nm.group(1) if nm and nm.group(1) else display) or "?"
            logo = lm.group(1) if lm else ""
            country = cm.group(1).upper() if cm else ""
            j = i + 1
            while j < len(lines) and (not lines[j].strip() or lines[j].strip().startswith("#")):
                j += 1
            if j < len(lines) and lines[j].strip().startswith("http"):
                out.append(
                    {"name": name, "url": lines[j].strip(), "logo": logo, "country": country}
                )
                i = j + 1
                continue
        i += 1
    return out


async def _get_channels() -> list[dict]:
    global _channels, _cache_ts
    async with _cache_lock:
        if _channels and time.time() - _cache_ts < _CACHE_TTL:
            return _channels
        try:
            logger.info("IPTV: fetching index playlist…")
            raw = await asyncio.to_thread(
                lambda: (
                    urllib.request.urlopen(_INDEX_URL, timeout=60)
                    .read()
                    .decode("utf-8", errors="ignore")
                )
            )
            parsed = _parse_m3u(raw)
            logger.info("IPTV: loaded %d channels", len(parsed))
            _channels = parsed
            _cache_ts = time.time()
        except Exception as exc:
            logger.warning("IPTV: playlist fetch failed: %s", exc)
    return _channels


# ── Search ────────────────────────────────────────────────────────────────────


def _search(channels: list[dict], query: str, limit: int = 8) -> list[dict]:
    q = query.lower()
    ranked = []
    for ch in channels:
        n = ch["name"].lower()
        if q in n:
            score = 0 if n == q else (1 if n.startswith(q) else 2)
            ranked.append((score, ch["name"], ch))
    ranked.sort(key=lambda x: (x[0], x[1]))
    return [r[2] for r in ranked[:limit]]


# ── Keyboards ─────────────────────────────────────────────────────────────────


def _results_keyboard(results: list[dict]) -> InlineKeyboardMarkup:
    rows = []
    for i, ch in enumerate(results):
        label = ch["name"]
        if ch["country"]:
            label += f" · {ch['country']}"
        rows.append([InlineKeyboardButton(text=label[:64], callback_data=f"iptv:{i}")])
    rows.append(
        [
            InlineKeyboardButton(text="📋 Browse by country", url=_COUNTRY_PLAYLISTS_URL),
            InlineKeyboardButton(text="🔍 Search again", callback_data="iptv_help"),
        ]
    )
    return InlineKeyboardMarkup(inline_keyboard=rows)


def _help_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(text="📋 Channels by country", url=_COUNTRY_PLAYLISTS_URL),
                InlineKeyboardButton(text="🌐 Full M3U playlist", url=_INDEX_URL),
            ],
            [InlineKeyboardButton(text="📦 iptv-org/iptv on GitHub", url=_REPO_URL)],
        ]
    )


# ── Command ───────────────────────────────────────────────────────────────────


@iptv_router.message(Command("mpv_iptv"))
async def cmd_iptv(message: Message, command: CommandObject) -> None:
    query = (command.args or "").strip()

    if not query:
        await message.reply(
            "📺 <b>IPTV — live TV from the iptv-org catalogue</b>\n\n"
            "Search by channel name:\n"
            "» <code>/mpv_iptv BBC</code>\n"
            "» <code>/mpv_iptv CNN</code>\n"
            "» <code>/mpv_iptv euronews</code>\n\n"
            "Browse the full catalogue on GitHub — channels are organised by "
            "country, language, and category. The direct M3U link works in VLC "
            "or any IPTV player.",
            reply_markup=_help_keyboard(),
            parse_mode="HTML",
            disable_web_page_preview=True,
        )
        return

    note = await message.reply(f"📺 Searching IPTV channels for <b>{query}</b>…", parse_mode="HTML")
    channels = await _get_channels()
    if not channels:
        await note.edit_text(
            f"❌ Could not load the channel list — try again later.\n"
            f'Browse manually: <a href="{_REPO_URL}">iptv-org/iptv</a>',
            parse_mode="HTML",
            disable_web_page_preview=True,
        )
        return

    results = _search(channels, query)
    if not results:
        await note.edit_text(
            f"❌ No channels found for <code>{query}</code>\n\n"
            f'Browse: <a href="{_COUNTRY_PLAYLISTS_URL}">channels by country</a>',
            parse_mode="HTML",
            disable_web_page_preview=True,
        )
        return

    _results[message.chat.id] = results
    names = "\n".join(
        f"• {ch['name']}{' · ' + ch['country'] if ch['country'] else ''}" for ch in results
    )
    await note.edit_text(
        f"📺 <b>IPTV — results for</b> <code>{query}</code>\n\n{names}\n\n"
        "<i>Tap a channel to stream it:</i>",
        reply_markup=_results_keyboard(results),
        parse_mode="HTML",
    )


# ── Callbacks ─────────────────────────────────────────────────────────────────


@iptv_router.callback_query(F.data.regexp(r"^iptv:(\d+)$"))
async def cb_iptv_pick(query: CallbackQuery) -> None:
    chat_id = query.message.chat.id
    logger.info(
        "IPTV: pick chat=%s user=%s data=%s",
        chat_id,
        query.from_user.id if query.from_user else None,
        query.data,
    )

    m = re.match(r"^iptv:(\d+)$", query.data)
    idx = int(m.group(1))
    cached = _results.get(chat_id, [])
    if idx >= len(cached):
        logger.info("IPTV: results expired chat=%s idx=%s", chat_id, idx)
        try:
            await query.message.edit_text("⚠️ Search session expired — run /mpv_iptv again.")
        except Exception:
            await query.answer("⚠️ Session expired — run /mpv_iptv again.", show_alert=True)
        return

    ch = cached[idx]
    name, url, logo = ch["name"], ch["url"], ch.get("logo", "")
    label = f"{name}{' · ' + ch['country'] if ch['country'] else ''}"

    await query.answer(f"▶ {name}"[:60])
    await query.message.edit_text(f"📺 Tuning to <b>{label}</b>…", parse_mode="HTML")

    settings = get_settings()
    try:
        await asyncio.to_thread(player.play_iptv, settings, url, name)
    except Exception as exc:
        logger.warning("IPTV: play_iptv failed for %s: %s", label, exc)
        try:
            await query.message.edit_text(f"❌ IPTV error: <code>{exc}</code>", parse_mode="HTML")
        except Exception:
            pass
        return

    state.set_notify_chat(settings.state_file, chat_id)
    logger.info("IPTV: streaming %s", label)

    # Replace picker with a photo card if the channel has a logo
    try:
        await query.message.delete()
    except Exception:
        pass

    caption = f"📺 <b>Now streaming:</b> {label}\n🔴 <i>Live IPTV</i>\n\n🔗 <code>{url}</code>"
    if logo:
        try:
            await query.message.answer_photo(logo, caption=caption, parse_mode="HTML")
            return
        except Exception as photo_err:
            logger.warning("IPTV: send_photo failed (%s), falling back to text", photo_err)
    await query.message.answer(caption, parse_mode="HTML")


@iptv_router.callback_query(F.data == "iptv_help")
async def cb_iptv_help(query: CallbackQuery) -> None:
    await query.answer()
    await query.message.edit_text(
        "📺 <b>IPTV search</b>\n\nType <code>/mpv_iptv &lt;channel name&gt;</code> to search.\n\n"
        "Browse the full catalogue on GitHub:",
        reply_markup=_help_keyboard(),
        parse_mode="HTML",
        disable_web_page_preview=True,
    )
