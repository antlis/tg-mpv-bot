from pathlib import Path

from src import keyboards
from src.keyboards import (
    PER_PAGE,
    category_counts,
    categories_keyboard,
    category_keyboard,
    clamp_page,
    indices_for_category,
    page_count,
    page_slice,
)
from src.playlists import Playlist


def make(names_by_cat: dict[str, int]) -> list[Playlist]:
    pls = []
    for cat, n in names_by_cat.items():
        for i in range(n):
            pls.append(Playlist(name=f"{cat}-{i:02d}", category=cat, path=Path("/x")))
    return pls


def test_category_counts():
    pls = make({"cartoons": 3, "movie": 2})
    assert category_counts(pls) == [("cartoons", 3), ("movie", 2)]


def test_indices_for_category():
    pls = make({"a": 2, "b": 2})
    assert indices_for_category(pls, "a") == [0, 1]
    assert indices_for_category(pls, "b") == [2, 3]


def test_page_count():
    assert page_count(0) == 1
    assert page_count(PER_PAGE) == 1
    assert page_count(PER_PAGE + 1) == 2
    assert page_count(PER_PAGE * 3) == 3


def test_clamp_page():
    assert clamp_page(-5, 100) == 0
    assert clamp_page(999, PER_PAGE) == 0  # only 1 page
    assert clamp_page(1, PER_PAGE * 3) == 1


def test_page_slice():
    items = list(range(20))
    assert page_slice(items, 0, per_page=8) == list(range(8))
    assert page_slice(items, 1, per_page=8) == list(range(8, 16))
    assert page_slice(items, 2, per_page=8) == list(range(16, 20))
    # out-of-range page clamps to last
    assert page_slice(items, 99, per_page=8) == list(range(16, 20))


def test_categories_keyboard_callback_data():
    pls = make({"cartoons": 3, "movie": 1})
    kb = categories_keyboard(pls)
    datas = [btn.callback_data for row in kb.inline_keyboard for btn in row]
    assert "cat:cartoons:0" in datas
    assert "cat:movie:0" in datas
    texts = [btn.text for row in kb.inline_keyboard for btn in row]
    assert any("cartoons (3)" in t for t in texts)


def test_category_keyboard_buttons_use_global_index():
    pls = make({"a": 2, "b": 3})
    kb = category_keyboard(pls, "b", page=0)
    play_btns = [
        btn for row in kb.inline_keyboard for btn in row
        if btn.callback_data and btn.callback_data.startswith("pl:")
    ]
    # category b occupies global indices 2,3,4
    assert {b.callback_data for b in play_btns} == {"pl:2", "pl:3", "pl:4"}


def test_category_keyboard_pagination_nav():
    pls = make({"big": PER_PAGE * 2 + 1})  # 3 pages
    kb = category_keyboard(pls, "big", page=1)
    datas = [btn.callback_data for row in kb.inline_keyboard for btn in row]
    assert "cat:big:0" in datas  # prev
    assert "cat:big:2" in datas  # next
    assert "1/3" not in datas    # counter is text, not callback
    assert "cats" in datas       # back button


def test_category_keyboard_no_nav_single_page():
    pls = make({"small": 3})
    kb = category_keyboard(pls, "small", page=0)
    datas = [btn.callback_data for row in kb.inline_keyboard for btn in row]
    # no prev/next page links, but back-to-categories present
    assert not any(d.startswith("cat:") for d in datas)
    assert "cats" in datas
