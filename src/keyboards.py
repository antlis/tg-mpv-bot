"""Inline-keyboard builders for browsing playlists.

A flat numbered list of 100s of playlists overflows Telegram's message limit
and the numbers shift whenever a playlist is added. Instead we browse a
hierarchy:

    category  →  (subcategory)  →  paginated playlist buttons

Categories with no subcategories (cartoons/movie/shows) jump straight to the
paginated playlist list. Categories that do have them (tutorials → provider)
show a subcategory menu first. The grouping/pagination maths is kept as pure,
unit-testable functions.

Callback data grammar — index-based so it stays short and free of delimiter
clashes (≤64 bytes):
    cats                 → category menu
    c:<ci>               → category #ci tapped (→ subcat menu OR playlist page 0)
    c:<ci>:<pg>          → page <pg> of a flat category
    s:<ci>:<si>          → subcategory #si of category #ci (→ playlist page 0)
    s:<ci>:<si>:<pg>     → page <pg> of that subcategory
    pl:<global_index>    → play playlist at that index in the full sorted list
    ep:<n>               → jump to 0-based item <n> of mpv's *current* playlist
    eps:<pg>             → page <pg> of the episode picker
    spd:<value>          → set playback speed (e.g. spd:1.5)
    h:<i>                → replay watch-history entry #i (newest-first order)
    yt:<video_id>        → stream that YouTube result (ids are 11 chars — fits)
    ch:<n> / chs:<pg>    → jump to chapter <n> of the current file / picker page
    cats / noop          → back to categories / inert (page counter)

``/mpv_search`` results reuse the same ``pl:<global_index>`` buttons, so a
search hit plays through the exact same callback path as a browse tap.

``ci`` / ``si`` index the case-insensitively sorted category / subcategory
lists, which are deterministic for a fixed library (same stability contract as
the global ``pl:`` index).
"""

from __future__ import annotations

from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup

from .playlists import Playlist, prettify
from .state import HistoryEntry

PER_PAGE = 8
MAX_SEARCH_RESULTS = 12

CATEGORY_EMOJI = {
    "cartoons": "🎨",
    "movie": "🎬",
    "shows": "📺",
    "tutorials": "🎓",
}


# ── pure grouping / pagination helpers ───────────────────────────────
def categories(playlists: list[Playlist]) -> list[str]:
    """Distinct categories, case-insensitively sorted."""
    return sorted({p.category for p in playlists}, key=str.lower)


def subcategories(playlists: list[Playlist], category: str) -> list[str]:
    """Distinct subcategories within ``category``, case-insensitively sorted."""
    subs = {p.subcategory for p in playlists if p.category == category and p.subcategory}
    return sorted(subs, key=str.lower)


def has_subcategories(playlists: list[Playlist], category: str) -> bool:
    return bool(subcategories(playlists, category))


def indices_for(
    playlists: list[Playlist], category: str, subcategory: str | None
) -> list[int]:
    """Global indices of playlists in (category, subcategory)."""
    return [
        i
        for i, p in enumerate(playlists)
        if p.category == category and p.subcategory == subcategory
    ]


def page_count(n_items: int, per_page: int = PER_PAGE) -> int:
    return max(1, (n_items + per_page - 1) // per_page)


def clamp_page(page: int, n_items: int, per_page: int = PER_PAGE) -> int:
    return max(0, min(page, page_count(n_items, per_page) - 1))


def page_slice(items: list[int], page: int, per_page: int = PER_PAGE) -> list[int]:
    page = clamp_page(page, len(items), per_page)
    start = page * per_page
    return items[start:start + per_page]


# ── keyboards ────────────────────────────────────────────────────────
def now_playing_keyboard(paused: bool | None = None) -> InlineKeyboardMarkup:
    """Transport controls for the now-playing panel (callback data `ctl:<action>`).

    The middle button is the play/pause toggle; its label reflects state —
    ``▶ Play`` when paused, ``⏸ Pause`` when playing, ``⏯`` when unknown.
    """
    def b(text: str, action: str) -> InlineKeyboardButton:
        return InlineKeyboardButton(text=text, callback_data=f"ctl:{action}")

    toggle = "▶ Play" if paused is True else "⏸ Pause" if paused is False else "⏯"

    return InlineKeyboardMarkup(inline_keyboard=[
        [b("⏮", "prev"), b("⏪", "back"), b(toggle, "toggle"), b("⏩", "fwd"), b("⏭", "next")],
        [b("0%", "p0"), b("25%", "p25"), b("50%", "p50"), b("75%", "p75")],
        [b("🔉", "voldown"), b("🔇", "mute"), b("🔊", "volup"), b("💬", "sub"), b("🎧", "audio")],
        [b("🔀", "shuffle"), b("🔁", "loop"), b("⏹", "stop"), b("🔄 Refresh", "refresh")],
    ])


def search_results_keyboard(
    playlists: list[Playlist], indices: list[int]
) -> InlineKeyboardMarkup:
    """One play button per search hit (callback ``pl:<global_index>``).

    Search results are a one-shot reply (not a browse session), so there is no
    pagination — the handler caps ``indices`` at ``MAX_SEARCH_RESULTS`` and
    tells the user to refine. The category emoji disambiguates same-named
    playlists across categories.
    """
    rows = [
        [
            InlineKeyboardButton(
                text=f"{CATEGORY_EMOJI.get(playlists[i].category, '📁')} {playlists[i].display}",
                callback_data=f"pl:{i}",
            )
        ]
        for i in indices[:MAX_SEARCH_RESULTS]
    ]
    rows.append([InlineKeyboardButton(text="⬅ Categories", callback_data="cats")])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def history_keyboard(entries: list[HistoryEntry]) -> InlineKeyboardMarkup:
    """Replay buttons for the watch history (callback ``h:<i>``).

    Indices follow the newest-first history order at render time; a replay
    reshuffles that order, so handlers re-read the list on tap (same
    freshness contract as the category indices).
    """
    rows = [
        [
            InlineKeyboardButton(
                # URL names are real media titles already; playlist stems get
                # the usual display cleanup.
                text=f"{'🔗' if e.is_url else '📁'} {(e.name if e.is_url else prettify(e.name))[:48]}",
                callback_data=f"h:{i}",
            )
        ]
        for i, e in enumerate(entries)
    ]
    return InlineKeyboardMarkup(inline_keyboard=rows)


def _fmt_duration(seconds: float | None) -> str:
    if not seconds:
        return ""
    s = int(seconds)
    h, rem = divmod(s, 3600)
    m, s = divmod(rem, 60)
    return f" · {h}:{m:02d}:{s:02d}" if h else f" · {m}:{s:02d}"


def chapters_keyboard(
    chapters: list[dict], current: int | None, page: int
) -> InlineKeyboardMarkup:
    """Picker over the current file's chapters (callback ``ch:<n>``).

    Same shape as the episode picker: current chapter marked and inert,
    ``chs:<page>`` pagination. Chapter dicts come straight from mpv's
    ``chapter-list`` (``title`` optional, ``time`` in seconds).
    """
    def label(i: int, c: dict) -> str:
        t = int(c.get("time") or 0)
        h, rem = divmod(t, 3600)
        m, s = divmod(rem, 60)
        stamp = f"{h}:{m:02d}:{s:02d}" if h else f"{m}:{s:02d}"
        return f"{stamp}  {c.get('title') or f'Chapter {i + 1}'}"[:56]

    page = clamp_page(page, len(chapters))
    rows = []
    for i in page_slice(list(range(len(chapters))), page):
        if i == current:
            rows.append([InlineKeyboardButton(text=f"▶ {label(i, chapters[i])}", callback_data="noop")])
        else:
            rows.append([InlineKeyboardButton(text=label(i, chapters[i]), callback_data=f"ch:{i}")])

    total = page_count(len(chapters))
    if total > 1:
        rows.append([
            InlineKeyboardButton(
                text="◀" if page > 0 else "·",
                callback_data=f"chs:{page - 1}" if page > 0 else "noop",
            ),
            InlineKeyboardButton(text=f"{page + 1}/{total}", callback_data="noop"),
            InlineKeyboardButton(
                text="▶" if page < total - 1 else "·",
                callback_data=f"chs:{page + 1}" if page < total - 1 else "noop",
            ),
        ])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def yt_results_keyboard(results: list[dict]) -> InlineKeyboardMarkup:
    """One play button per YouTube search hit (callback ``yt:<video_id>``)."""
    rows = [
        [
            InlineKeyboardButton(
                text=f"▶ {r['title'][:50]}{_fmt_duration(r.get('duration'))}",
                callback_data=f"yt:{r['id']}",
            )
        ]
        for r in results
    ]
    return InlineKeyboardMarkup(inline_keyboard=rows)


SPEEDS = [0.75, 1.0, 1.25, 1.5, 1.75, 2.0]


def speed_keyboard(current: float | None = None) -> InlineKeyboardMarkup:
    """Speed presets (callback ``spd:<value>``); the active one is marked."""
    buttons = [
        InlineKeyboardButton(
            text=f"{'• ' if current is not None and abs(s - current) < 0.01 else ''}{s:g}×",
            callback_data=f"spd:{s:g}",
        )
        for s in SPEEDS
    ]
    return InlineKeyboardMarkup(inline_keyboard=[buttons[:3], buttons[3:]])


def episodes_keyboard(
    names: list[str], current: int | None, page: int
) -> InlineKeyboardMarkup:
    """Picker over mpv's *current* playlist items (callback ``ep:<n>``).

    Unlike the browse keyboards this indexes the live mpv playlist, not the
    library — indices come straight from the ``playlist`` IPC property. The
    current item is marked and unclickable (jumping to it would restart it).
    """
    page = clamp_page(page, len(names))
    rows = []
    for i in page_slice(list(range(len(names))), page):
        if i == current:
            rows.append([InlineKeyboardButton(text=f"▶ {i + 1}. {names[i]}", callback_data="noop")])
        else:
            rows.append([InlineKeyboardButton(text=f"{i + 1}. {names[i]}", callback_data=f"ep:{i}")])

    total = page_count(len(names))
    if total > 1:
        rows.append([
            InlineKeyboardButton(
                text="◀" if page > 0 else "·",
                callback_data=f"eps:{page - 1}" if page > 0 else "noop",
            ),
            InlineKeyboardButton(text=f"{page + 1}/{total}", callback_data="noop"),
            InlineKeyboardButton(
                text="▶" if page < total - 1 else "·",
                callback_data=f"eps:{page + 1}" if page < total - 1 else "noop",
            ),
        ])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def categories_keyboard(
    playlists: list[Playlist], continue_label: str | None = None
) -> InlineKeyboardMarkup:
    rows = []
    if continue_label:
        # h:0 = newest history entry — same replay path as /mpv_history
        rows.append(
            [InlineKeyboardButton(text=f"▶ Continue: {continue_label[:42]}", callback_data="h:0")]
        )
    for ci, cat in enumerate(categories(playlists)):
        count = sum(1 for p in playlists if p.category == cat)
        emoji = CATEGORY_EMOJI.get(cat, "📁")
        rows.append(
            [InlineKeyboardButton(text=f"{emoji} {cat} ({count})", callback_data=f"c:{ci}")]
        )
    return InlineKeyboardMarkup(inline_keyboard=rows)


def subcategories_keyboard(playlists: list[Playlist], ci: int) -> InlineKeyboardMarkup:
    cat = categories(playlists)[ci]
    rows = []
    for si, sub in enumerate(subcategories(playlists, cat)):
        count = len(indices_for(playlists, cat, sub))
        rows.append(
            [InlineKeyboardButton(text=f"{prettify(sub)} ({count})", callback_data=f"s:{ci}:{si}")]
        )
    rows.append([InlineKeyboardButton(text="⬅ Categories", callback_data="cats")])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def playlists_keyboard(
    playlists: list[Playlist], ci: int, si: int | None, page: int
) -> InlineKeyboardMarkup:
    cat = categories(playlists)[ci]
    if si is None:
        indices = indices_for(playlists, cat, None)
        nav_prefix = f"c:{ci}"
        back = [InlineKeyboardButton(text="⬅ Categories", callback_data="cats")]
    else:
        sub = subcategories(playlists, cat)[si]
        indices = indices_for(playlists, cat, sub)
        nav_prefix = f"s:{ci}:{si}"
        back = [InlineKeyboardButton(text="⬅ Back", callback_data=f"c:{ci}")]

    page = clamp_page(page, len(indices))
    rows = [
        [InlineKeyboardButton(text=playlists[i].display, callback_data=f"pl:{i}")]
        for i in page_slice(indices, page)
    ]

    total = page_count(len(indices))
    if total > 1:
        rows.append([
            InlineKeyboardButton(
                text="◀" if page > 0 else "·",
                callback_data=f"{nav_prefix}:{page - 1}" if page > 0 else "noop",
            ),
            InlineKeyboardButton(text=f"{page + 1}/{total}", callback_data="noop"),
            InlineKeyboardButton(
                text="▶" if page < total - 1 else "·",
                callback_data=f"{nav_prefix}:{page + 1}" if page < total - 1 else "noop",
            ),
        ])
    rows.append(back)
    return InlineKeyboardMarkup(inline_keyboard=rows)
