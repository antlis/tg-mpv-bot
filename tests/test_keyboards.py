from pathlib import Path

from src.keyboards import (
    PER_PAGE,
    categories,
    categories_keyboard,
    clamp_page,
    has_subcategories,
    indices_for,
    page_count,
    page_slice,
    playlists_keyboard,
    subcategories,
    subcategories_keyboard,
)
from src.playlists import Playlist


def make_flat(names_by_cat: dict[str, int]) -> list[Playlist]:
    pls = []
    for cat, n in names_by_cat.items():
        for i in range(n):
            pls.append(Playlist(name=f"{cat}-{i:02d}", category=cat, path=Path("/x")))
    return sorted(pls, key=lambda p: p.name.lower())


def make_nested(spec: dict[str, dict[str, int]]) -> list[Playlist]:
    """spec = {category: {subcategory: count}}"""
    pls = []
    for cat, subs in spec.items():
        for sub, n in subs.items():
            for i in range(n):
                pls.append(
                    Playlist(name=f"{sub}-{i:02d}", category=cat, path=Path("/x"), subcategory=sub)
                )
    return sorted(pls, key=lambda p: p.name.lower())


# ── pure helpers ─────────────────────────────────────────────────────
def test_categories_sorted():
    pls = make_flat({"movie": 1, "cartoons": 1, "tutorials": 1})
    assert categories(pls) == ["cartoons", "movie", "tutorials"]


def test_subcategories_sorted():
    pls = make_nested({"tutorials": {"python": 2, "Vue Mastery": 1, "codeSchool": 1}})
    assert subcategories(pls, "tutorials") == ["codeSchool", "python", "Vue Mastery"]


def test_has_subcategories():
    pls = make_flat({"cartoons": 3}) + make_nested({"tutorials": {"python": 1}})
    assert has_subcategories(pls, "cartoons") is False
    assert has_subcategories(pls, "tutorials") is True


def test_indices_for():
    pls = make_nested({"tutorials": {"a": 2, "b": 2}})
    # names: a-00, a-01, b-00, b-01 → sorted that order
    assert indices_for(pls, "tutorials", "a") == [0, 1]
    assert indices_for(pls, "tutorials", "b") == [2, 3]


def test_page_count_and_clamp():
    assert page_count(0) == 1
    assert page_count(PER_PAGE + 1) == 2
    assert clamp_page(-5, 100) == 0
    assert clamp_page(999, PER_PAGE) == 0


def test_page_slice():
    items = list(range(20))
    assert page_slice(items, 0, per_page=8) == list(range(8))
    assert page_slice(items, 2, per_page=8) == list(range(16, 20))
    assert page_slice(items, 99, per_page=8) == list(range(16, 20))  # clamps


# ── keyboards ────────────────────────────────────────────────────────
def test_categories_keyboard_uses_indices():
    pls = make_flat({"cartoons": 3}) + make_nested({"tutorials": {"python": 2}})
    pls = sorted(pls, key=lambda p: p.name.lower())
    kb = categories_keyboard(pls)
    datas = [b.callback_data for row in kb.inline_keyboard for b in row]
    # one button per category, callback c:<ci>
    assert set(datas) == {"c:0", "c:1"}
    texts = [b.text for row in kb.inline_keyboard for b in row]
    assert any("cartoons (3)" in t for t in texts)
    assert any("tutorials (2)" in t for t in texts)


def test_flat_category_keyboard_play_and_back():
    pls = make_flat({"cartoons": 3})
    kb = playlists_keyboard(pls, ci=0, si=None, page=0)
    datas = [b.callback_data for row in kb.inline_keyboard for b in row]
    assert {"pl:0", "pl:1", "pl:2"} <= set(datas)
    assert "cats" in datas  # back to categories


def test_flat_category_pagination():
    pls = make_flat({"big": PER_PAGE * 2 + 1})  # 3 pages
    kb = playlists_keyboard(pls, ci=0, si=None, page=1)
    datas = [b.callback_data for row in kb.inline_keyboard for b in row]
    assert "c:0:0" in datas  # prev page
    assert "c:0:2" in datas  # next page


def test_subcategories_keyboard():
    pls = make_nested({"tutorials": {"python": 2, "wesbos": 3}})
    kb = subcategories_keyboard(pls, ci=0)
    datas = [b.callback_data for row in kb.inline_keyboard for b in row]
    assert "s:0:0" in datas and "s:0:1" in datas
    assert "cats" in datas  # back to categories
    texts = [b.text for row in kb.inline_keyboard for b in row]
    # subcategory labels are prettified for display
    assert any("Python (2)" in t for t in texts)
    assert any("Wesbos (3)" in t for t in texts)


def test_subcategory_playlists_use_global_index_and_back():
    # category a (flat, 2) sorts before tutorials; global indices shift
    pls = make_flat({"aaa": 2}) + make_nested({"tutorials": {"zsub": 3}})
    pls = sorted(pls, key=lambda p: p.name.lower())  # aaa-00,aaa-01,zsub-00,zsub-01,zsub-02
    ci = categories(pls).index("tutorials")
    kb = playlists_keyboard(pls, ci=ci, si=0, page=0)
    datas = [b.callback_data for row in kb.inline_keyboard for b in row]
    assert {"pl:2", "pl:3", "pl:4"} <= set(datas)  # zsub at global 2,3,4
    assert f"c:{ci}" in datas  # back to subcategory menu


def test_now_playing_keyboard():
    from src.keyboards import now_playing_keyboard
    kb = now_playing_keyboard()
    datas = {b.callback_data for row in kb.inline_keyboard for b in row}
    assert {"ctl:toggle", "ctl:fwd", "ctl:back", "ctl:next", "ctl:prev",
            "ctl:volup", "ctl:voldown", "ctl:mute", "ctl:sub", "ctl:audio",
            "ctl:shuffle", "ctl:loop", "ctl:stop", "ctl:refresh"} <= datas


def test_play_pause_toggle_label_reflects_state():
    from src.keyboards import now_playing_keyboard

    def toggle_text(paused):
        kb = now_playing_keyboard(paused)
        return next(b.text for row in kb.inline_keyboard for b in row
                    if b.callback_data == "ctl:toggle")

    assert toggle_text(True) == "▶ Play"    # paused → offer Play
    assert toggle_text(False) == "⏸ Pause"  # playing → offer Pause
    assert toggle_text(None) == "⏯"         # unknown → neutral


def test_search_results_keyboard_uses_global_indices():
    from src.keyboards import search_results_keyboard
    pls = make_flat({"cartoons": 2, "movie": 2})
    kb = search_results_keyboard(pls, [1, 3])
    datas = [b.callback_data for row in kb.inline_keyboard for b in row]
    assert datas == ["pl:1", "pl:3", "cats"]  # play buttons + back to categories
    texts = [b.text for row in kb.inline_keyboard for b in row]
    assert texts[0].startswith("🎨")  # category emoji prefixes each hit
    assert texts[1].startswith("🎬")


def test_search_results_keyboard_caps_results():
    from src.keyboards import MAX_SEARCH_RESULTS, search_results_keyboard
    pls = make_flat({"movie": MAX_SEARCH_RESULTS * 2})
    kb = search_results_keyboard(pls, list(range(len(pls))))
    plays = [b for row in kb.inline_keyboard for b in row
             if b.callback_data.startswith("pl:")]
    assert len(plays) == MAX_SEARCH_RESULTS


def test_episodes_keyboard_marks_current_and_jumps():
    from src.keyboards import episodes_keyboard
    names = [f"Ep {i}" for i in range(1, 4)]
    kb = episodes_keyboard(names, current=1, page=0)
    buttons = [b for row in kb.inline_keyboard for b in row]
    by_text = {b.text: b.callback_data for b in buttons}
    assert by_text["1. Ep 1"] == "ep:0"
    assert by_text["▶ 2. Ep 2"] == "noop"  # current item is marked, not clickable
    assert by_text["3. Ep 3"] == "ep:2"


def test_episodes_keyboard_pagination():
    from src.keyboards import episodes_keyboard
    names = [f"Ep {i}" for i in range(PER_PAGE * 2 + 1)]  # 3 pages
    kb = episodes_keyboard(names, current=None, page=1)
    datas = [b.callback_data for row in kb.inline_keyboard for b in row]
    assert "eps:0" in datas and "eps:2" in datas


def test_subcategory_pagination_prefix():
    pls = make_nested({"tutorials": {"big": PER_PAGE * 2}})  # 2 pages
    kb = playlists_keyboard(pls, ci=0, si=0, page=0)
    datas = [b.callback_data for row in kb.inline_keyboard for b in row]
    assert "s:0:0:1" in datas  # next page within subcategory
