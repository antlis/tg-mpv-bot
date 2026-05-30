"""Inline-keyboard builders for browsing playlists.

A flat numbered list of 100+ playlists overflows Telegram's message limit and
the numbers shift whenever a playlist is added. Instead we browse by category,
then a paginated list of tappable playlist buttons. The pagination/grouping
maths is kept as pure functions so it can be unit-tested without aiogram.

Callback data grammar (must stay ≤64 bytes):
    cats                 → show category menu
    cat:<category>:<pg>  → page <pg> of <category>
    pl:<global_index>    → play playlist at that index in the full sorted list
    noop                 → inert (page counter button)
"""

from __future__ import annotations

from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup

from .playlists import Playlist

PER_PAGE = 8

CATEGORY_EMOJI = {"cartoons": "🎨", "movie": "🎬", "shows": "📺"}


def category_counts(playlists: list[Playlist]) -> list[tuple[str, int]]:
    """Distinct categories with their playlist counts, in first-seen order."""
    counts: dict[str, int] = {}
    for pl in playlists:
        counts[pl.category] = counts.get(pl.category, 0) + 1
    return list(counts.items())


def indices_for_category(playlists: list[Playlist], category: str) -> list[int]:
    """Global indices (into ``playlists``) belonging to ``category``."""
    return [i for i, pl in enumerate(playlists) if pl.category == category]


def page_count(n_items: int, per_page: int = PER_PAGE) -> int:
    return max(1, (n_items + per_page - 1) // per_page)


def clamp_page(page: int, n_items: int, per_page: int = PER_PAGE) -> int:
    return max(0, min(page, page_count(n_items, per_page) - 1))


def page_slice(items: list[int], page: int, per_page: int = PER_PAGE) -> list[int]:
    page = clamp_page(page, len(items), per_page)
    start = page * per_page
    return items[start:start + per_page]


def categories_keyboard(playlists: list[Playlist]) -> InlineKeyboardMarkup:
    rows = []
    for category, count in category_counts(playlists):
        emoji = CATEGORY_EMOJI.get(category, "📁")
        rows.append([
            InlineKeyboardButton(
                text=f"{emoji} {category} ({count})",
                callback_data=f"cat:{category}:0",
            )
        ])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def category_keyboard(
    playlists: list[Playlist], category: str, page: int
) -> InlineKeyboardMarkup:
    indices = indices_for_category(playlists, category)
    page = clamp_page(page, len(indices))
    rows = [
        [InlineKeyboardButton(text=playlists[i].name, callback_data=f"pl:{i}")]
        for i in page_slice(indices, page)
    ]

    total_pages = page_count(len(indices))
    nav: list[InlineKeyboardButton] = []
    if total_pages > 1:
        nav.append(
            InlineKeyboardButton(
                text="◀" if page > 0 else "·",
                callback_data=f"cat:{category}:{page - 1}" if page > 0 else "noop",
            )
        )
        nav.append(
            InlineKeyboardButton(text=f"{page + 1}/{total_pages}", callback_data="noop")
        )
        nav.append(
            InlineKeyboardButton(
                text="▶" if page < total_pages - 1 else "·",
                callback_data=f"cat:{category}:{page + 1}"
                if page < total_pages - 1
                else "noop",
            )
        )
    if nav:
        rows.append(nav)
    rows.append([InlineKeyboardButton(text="⬅ Categories", callback_data="cats")])
    return InlineKeyboardMarkup(inline_keyboard=rows)
