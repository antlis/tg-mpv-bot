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


def test_categories_keyboard_continue_row():
    pls = make_flat({"cartoons": 1})
    kb = categories_keyboard(pls, continue_label="Deadwood S01")
    first = kb.inline_keyboard[0][0]
    assert first.text == "▶ Continue: Deadwood S01"
    assert first.callback_data == "h:0"  # replays newest history entry
    # without a label there is no extra row
    assert categories_keyboard(pls).inline_keyboard[0][0].callback_data == "c:0"


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
            "ctl:p0", "ctl:p25", "ctl:p50", "ctl:p75",
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


def test_speed_keyboard_marks_current():
    from src.keyboards import SPEEDS, speed_keyboard
    kb = speed_keyboard(1.5)
    buttons = [b for row in kb.inline_keyboard for b in row]
    assert len(buttons) == len(SPEEDS)
    by_data = {b.callback_data: b.text for b in buttons}
    assert by_data["spd:1.5"] == "• 1.5×"   # active speed marked
    assert by_data["spd:1"] == "1×"
    assert by_data["spd:0.75"] == "0.75×"


def test_history_keyboard():
    from src.keyboards import history_keyboard
    from src.state import HistoryEntry
    entries = [
        HistoryEntry(target="https://youtu.be/x", name="Some Video 1080p Title", at=2),
        HistoryEntry(target="/v/the-big-lebowski.m3u", name="the-big-lebowski", at=1),
    ]
    kb = history_keyboard(entries)
    # each row has [icon, title, trash]; no nav row (only 2 entries < PER_PAGE)
    assert len(kb.inline_keyboard) == 2
    for row in kb.inline_keyboard:
        assert len(row) == 3
    icon_btns  = [row[0] for row in kb.inline_keyboard]
    title_btns = [row[1] for row in kb.inline_keyboard]
    del_btns   = [row[2] for row in kb.inline_keyboard]
    # icon copies, title plays
    assert [b.callback_data for b in icon_btns]  == ["hcp:0", "hcp:1"]
    assert [b.callback_data for b in title_btns] == ["h:0", "h:1"]
    assert [b.callback_data for b in del_btns]   == ["hdel:0:0", "hdel:1:0"]
    assert icon_btns[0].text == "🔗"
    assert icon_btns[1].text == "📁"
    assert all(b.text == "🗑" for b in del_btns)


def test_chapters_keyboard():
    from src.keyboards import chapters_keyboard
    chapters = [
        {"title": "Intro", "time": 0},
        {"title": "The Heist", "time": 754},
        {"time": 3725},  # no title → numbered fallback
    ]
    kb = chapters_keyboard(chapters, current=1, page=0)
    buttons = [b for row in kb.inline_keyboard for b in row]
    by_data = {b.callback_data: b.text for b in buttons}
    assert by_data["ch:0"] == "0:00  Intro"
    assert by_data["ch:2"] == "1:02:05  Chapter 3"
    # current chapter is marked and inert
    assert any(b.text == "▶ 12:34  The Heist" and b.callback_data == "noop" for b in buttons)


def test_radio_keyboard():
    from src.keyboards import radio_keyboard
    kb = radio_keyboard([("Groove Salad", "https://x/gs.pls"), ("FIP", "https://y/f.mp3")])
    buttons = [b for row in kb.inline_keyboard for b in row]
    assert [b.callback_data for b in buttons] == ["rd:0", "rd:1"]  # no nav row when it fits
    assert buttons[0].text == "📻 Groove Salad"


def test_radio_keyboard_paginates_with_global_indices():
    from src.keyboards import radio_keyboard
    stations = [(f"St {i}", f"https://x/{i}") for i in range(PER_PAGE * 2 + 3)]
    kb = radio_keyboard(stations, page=1)
    datas = [b.callback_data for row in kb.inline_keyboard for b in row]
    assert f"rd:{PER_PAGE}" in datas      # page 2 starts at the global index
    assert "rds:0" in datas and "rds:2" in datas  # nav both ways


def test_radio_search_keyboard():
    from src.keyboards import radio_search_keyboard
    kb = radio_search_keyboard([
        {"name": "Technolovers GABBER", "url": "https://x", "codec": "MP3", "bitrate": 192, "country": "DE"},
        {"name": "Minimal", "url": "https://y", "codec": "", "bitrate": 0, "country": ""},
    ])
    buttons = [b for row in kb.inline_keyboard for b in row]
    assert [b.callback_data for b in buttons] == ["rdq:0", "rdq:1"]
    assert buttons[0].text == "📻 Technolovers GABBER · 192k MP3 · DE"
    assert buttons[1].text == "📻 Minimal"  # no meta → no trailing separator


def test_default_station_catalog_sane():
    from src.config import DEFAULT_RADIO_STATIONS
    urls = [u for _, u in DEFAULT_RADIO_STATIONS]
    assert len(urls) == len(set(urls)), "duplicate stream URLs"
    assert sum(1 for u in urls if "somafm.com" in u) >= 40  # the full catalog
    assert all(u.startswith(("https://", "http://")) for u in urls)


def test_listing_keyboard():
    from src.keyboards import listing_keyboard
    kb = listing_keyboard([
        {"title": "Episode One", "url": "https://yt/x", "duration": 754},
        {"title": "Episode Two", "url": "https://yt/y", "duration": None},
    ])
    buttons = [b for row in kb.inline_keyboard for b in row]
    assert [b.callback_data for b in buttons] == ["ple:0", "ple:1"]
    assert buttons[0].text == "▶ Episode One · 12:34"


def test_yt_results_keyboard():
    from src.keyboards import yt_results_keyboard
    kb = yt_results_keyboard([
        {"id": "n61ULEU7CO0", "title": "Best of lofi", "duration": 22258.0},
        {"id": "rPjez8z61rI", "title": "radio", "duration": None},  # live stream
    ])
    buttons = [b for row in kb.inline_keyboard for b in row]
    assert [b.callback_data for b in buttons] == ["yt:n61ULEU7CO0", "yt:rPjez8z61rI"]
    assert buttons[0].text == "▶ Best of lofi · 6:10:58"
    assert buttons[1].text == "▶ radio"  # no duration → no suffix


def test_subcategory_pagination_prefix():
    pls = make_nested({"tutorials": {"big": PER_PAGE * 2}})  # 2 pages
    kb = playlists_keyboard(pls, ci=0, si=0, page=0)
    datas = [b.callback_data for row in kb.inline_keyboard for b in row]
    assert "s:0:0:1" in datas  # next page within subcategory
