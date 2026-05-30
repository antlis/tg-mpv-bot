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
    cats / noop          → back to categories / inert (page counter)

``ci`` / ``si`` index the case-insensitively sorted category / subcategory
lists, which are deterministic for a fixed library (same stability contract as
the global ``pl:`` index).
"""

from __future__ import annotations

from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup

from .playlists import Playlist

PER_PAGE = 8

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
def categories_keyboard(playlists: list[Playlist]) -> InlineKeyboardMarkup:
    rows = []
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
            [InlineKeyboardButton(text=f"{sub} ({count})", callback_data=f"s:{ci}:{si}")]
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
        [InlineKeyboardButton(text=playlists[i].name, callback_data=f"pl:{i}")]
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
