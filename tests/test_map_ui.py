"""Milestone A (v0.17): the interactive city map: buttons, browsing, pagination, and above all who may press them.

Every test goes through the real handlers (Telegram itself is faked): a typed «مپ», then presses of the buttons the bot actually sent."""
import math
import re

import pytest
from aiogram.methods import AnswerCallbackQuery

import mapkit
import test_bot as tb
from hypemarket import cards, config as C, mapui, presence, world, worldinspect as WI, worldmap, worldplaces
from hypemarket.texts import EN, FA, t

CH, CH2 = -100606, -100777
A, B, X = 777001, 777002, 999009        # two players, and somebody who has never spoken to the bot


@pytest.fixture()
def h():
    worldmap.cache_clear()
    worldplaces.cache_clear()
    return tb.Harness()


@pytest.fixture()
def d(h):
    return mapkit.Driver(h, CH)


def snap(db):
    """Every row of every table."""
    out = {}
    for r in db.qa("SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%' ORDER BY name"):
        out[r["name"]] = [tuple(x) for x in db.qa(f"SELECT * FROM {r['name']}")]
    return out


def layout(h):
    return mapkit.city_of(h, CH).layout


def lots(v):
    return [d_.split(":")[3] for _t, d_ in v.buttons if d_.split(":")[2] == "o" and d_.split(":")[3].startswith("p")]


# ---------------------------------------------------------------------------------------------- the map message and its buttons
def test_map_shows_the_saved_city_with_a_compact_keyboard_and_an_owner_session(h, d):
    v = d.open(A)
    assert v.photo and mapkit.png(v.photo).size[0] > 800
    w = world.get_world(h.db, CH)
    row = d.session(A)
    assert (row["user_id"], row["chat_id"], row["world_id"], row["message_id"]) == (A, CH, w["id"], v.mid) and v.mid
    assert re.fullmatch("[0-9a-f]{8}", row["sid"])
    labels = [x for x, _ in v.buttons]
    for key in ("mp.b.near", "mp.b.places", "mp.b.lots", "mp.b.casino", "mp.b.refresh"):
        assert t("fa", key) in labels, key
    assert len(v.rows) <= 3 and max(len(r) for r in v.rows) <= 2 and len(set(labels)) == len(labels)
    for _x, data in v.buttons:
        parts = data.split(":")
        if parts[0] == "jb":                                                                            # v0.18: the job-board button is bound to its owner like every jb: button
            assert data == f"jb:m:{A}"
            continue
        assert len(data.encode()) <= 64 and parts[:2] == ["mp", row["sid"]] and str(A) not in data     # the player's ID is never in a button
    assert w["name_fa"] in v.caption and len(v.caption) <= 1024
    assert "📍" in v.caption and "👥" in v.caption                                                      # where you are, who is near


def test_the_picture_is_the_saved_city_plus_markers(h, d):
    v = d.open(A)
    assert v.photo == mapkit.shown(h, CH, A, "fa")
    w = dict(world.get_world(h.db, CH))
    d.open(B)
    assert dict(world.get_world(h.db, CH)) == w                                                        # looking never touches the saved city


def test_every_main_button_works_and_edits_the_same_message(h, d):
    v = d.open(A)
    for label, data in v.buttons:
        if data.startswith("jb:"):                                                                      # v0.18: the job board is a text screen, so it goes in a NEW message under the map
            continue                                                                                    # (its own behaviour is tested in tests/test_life_jobs.py::test_the_jobs_button_of_the_map...)
        out = d.press(A, data, v)
        assert out.edits and not out.alerts and not out.sent, label                                    # edited in place, no new messages
        assert out.edit.message_id == v.mid if hasattr(out.edit, "message_id") and out.edit.message_id else True
        assert out.buttons, label                                                                      # and there is always a way on
    assert d.session(A)["message_id"] == v.mid


def test_navigation_buttons_open_the_right_screens(h, d):
    v = d.open(A)
    near = d.go(A, v, t("fa", "mp.b.near"))
    assert t("fa", "mp.nb.title") in near.caption and t("fa", "mp.nb.places") in near.caption
    places = d.go(A, v, t("fa", "mp.b.places"))
    assert t("fa", "mp.pl.title", p="۱", n="۱") in places.caption
    land = d.go(A, v, t("fa", "mp.b.lots"))
    assert "🌿" in land.caption and "صفحه ۱ از" in land.caption
    cas = d.go(A, v, t("fa", "mp.b.casino"))
    assert "L5" in cas.caption and t("fa", "map.lm.casino") in cas.caption
    back = d.go(A, near, t("fa", "mp.b.map"))
    assert t("fa", "mp.pos.near", place="")[:8] in back.caption or t("fa", "mp.pos.at", place="")[:8] in back.caption
    refreshed = d.go(A, v, t("fa", "mp.b.refresh"))
    assert [x for x, _ in refreshed.buttons] == [x for x, _ in v.buttons]
    assert refreshed.photo == mapkit.shown(h, CH, A, "fa")


def test_parcels_are_browsed_with_buttons_no_typing(h, d):
    v = d.open(A)
    page = d.go(A, v, t("fa", "mp.b.lots"))
    L = layout(h)
    city = mapkit.city_of(h, CH)
    st = presence.stand(h.db, city, A)
    field = city.net.field(st.pos)
    vacant = {p["id"]: p for p in L["parcels"] if p["use"] == "vacant"}
    expect = sorted(vacant, key=lambda i: (field.to(city.net.door(i, vacant[i]["c"])), int(i[1:])))[:C.MAP_PAGE]
    assert lots(page) == expect                                                                         # real persistent IDs, nearest first, empty land only
    assert all(i in vacant for i in lots(page)) and not any(i == city.rows["casino"]["parcel_id"] for i in lots(page))
    pid = lots(page)[0]
    obj = d.go(A, page, data=f"mp:{page.sid}:o:{pid}")
    assert obj.caption.startswith(WI.inspect("fa", world.get_world(h.db, CH), L, pid, notes=False).text)     # the existing inspection panel for exactly this parcel
    assert f"قطعه {pid.upper()}" in obj.caption and t("fa", "wi.status.vacant") in obj.caption
    assert obj.photo and obj.photo != mapkit.shown(h, CH, A, "fa")                                      # and the map with it marked
    assert t("fa", "mp.b.go") in [x for x, _ in obj.buttons] and not any("buy" in dd or "build" in dd for _x, dd in obj.buttons)
    typed = h.say(A, f"مپ {pid.upper()}", chat=CH)
    assert WI.inspect("fa", world.get_world(h.db, CH), L, pid).text.split("\n")[0] in mapkit.text_of(typed[-1])    # typing still works, as before


def test_pagination_and_back_buttons(h, d):
    v = d.open(A)
    p1 = d.go(A, v, t("fa", "mp.b.lots"))
    n_vacant = sum(1 for p in layout(h)["parcels"] if p["use"] == "vacant")
    pages = math.ceil(min(n_vacant, C.MAP_MAX_LOTS) / C.MAP_PAGE)
    assert pages >= 3 and t("fa", "mp.b.prev") not in [x for x, _ in p1.buttons]                      # first page: no «previous»
    p2 = d.go(A, p1, t("fa", "mp.b.next"))
    assert f"صفحه ۲ از" in p2.caption and lots(p2) != lots(p1) and not set(lots(p1)) & set(lots(p2))
    assert t("fa", "mp.b.prev") in [x for x, _ in p2.buttons] and t("fa", "mp.b.next") in [x for x, _ in p2.buttons]
    back = d.go(A, p2, t("fa", "mp.b.prev"))
    assert lots(back) == lots(p1)
    cur = p1
    for _ in range(pages - 1):
        cur = d.go(A, cur, t("fa", "mp.b.next"))
    assert t("fa", "mp.b.next") not in [x for x, _ in cur.buttons] and t("fa", "mp.b.prev") in [x for x, _ in cur.buttons]   # last page: no «next»
    assert t("fa", "mp.b.map") in [x for x, _ in cur.buttons]
    far = d.press(A, f"mp:{v.sid}:ln:999", v)                                                          # a page number out of range shows the last page
    assert lots(far) == lots(cur)
    d.press(A, f"mp:{v.sid}:ln:zz", v)
    # an object opened from page 2 goes back to page 2
    pid = lots(p2)[1]
    p2 = d.go(A, p1, t("fa", "mp.b.next"))
    obj = d.go(A, p2, data=f"mp:{v.sid}:o:{pid}")
    back_label = t("fa", "mp.b.back")
    again = d.go(A, obj, back_label)
    assert lots(again) == lots(p2) and "صفحه ۲ از" in again.caption
    home = d.go(A, obj, t("fa", "mp.b.map"))
    assert t("fa", "mp.b.near") in [x for x, _ in home.buttons]


def test_public_places_browser_lists_every_place_with_its_real_id(h, d):
    v = d.open(A)
    pl = d.go(A, v, t("fa", "mp.b.places"))
    ids = [dd.split(":")[3] for _x, dd in pl.buttons if dd.split(":")[2] == "o"]
    assert sorted(ids) == sorted(m["id"] for m in layout(h)["landmarks"]) and "l5" in ids
    for i in ids:
        obj = d.go(A, pl, data=f"mp:{v.sid}:o:{i}")
        assert f"<code>{i.upper()}</code>" in obj.caption or i.upper() in obj.caption
        assert t("fa", "mp.b.back") in [x for x, _ in obj.buttons]


def test_places_pages_follow_the_page_size(h, d, monkeypatch):
    monkeypatch.setattr(C, "MAP_PAGE", 2)
    v = d.open(A)
    pl = d.go(A, v, t("fa", "mp.b.places"))
    assert "۱ از ۳" in pl.caption and len([1 for _x, dd in pl.buttons if dd.split(":")[2] == "o"]) == 2
    nxt = d.go(A, pl, t("fa", "mp.b.next"))
    nxt2 = d.go(A, nxt, t("fa", "mp.b.next"))
    seen = {dd for o in (pl, nxt, nxt2) for _x, dd in o.buttons if dd.split(":")[2] == "o"}
    assert len(seen) == 6 and t("fa", "mp.b.next") not in [x for x, _ in nxt2.buttons]


def test_english_groups_get_english_screens(h, d):
    from hypemarket import social
    d.open(A)
    social.set_group_lang(h.db, CH, "en")
    v = d.open(A, "map")
    assert t("en", "mp.b.lots") in [x for x, _ in v.buttons]
    page = d.go(A, v, t("en", "mp.b.lots"))
    assert "Empty land nearby" in page.caption and "page 1 of" in page.caption


# ---------------------------------------------------------------------------------------------- ownership
def crawl(d, owner, start, limit=60):
    """Press the read-only buttons reachable from `start` as the owner; returns every distinct callback data seen (including walking ones)."""
    seen, todo, outs = set(), [start], []
    while todo and len(seen) < limit:
        v = todo.pop()
        for _x, data in v.buttons:
            if data in seen:
                continue
            seen.add(data)
            if data.split(":")[2] in ("go", "ce", "bj"):
                continue
            o = d.press(owner, data, start)
            outs.append(o)
            if o.buttons:
                todo.append(o)
    return seen, outs


def test_nobody_but_the_owner_can_use_a_map_button(h, d):
    h.say(B, "سلام", chat=CH)
    v = d.open(A)
    seen, outs = crawl(d, A, v)
    for o in outs:                                                                                       # a few screens deeper: a parcel and the casino
        seen |= set(o.data())
    assert len(seen) >= 10
    before = snap(h.db)
    pics = h.session.sent()
    for uid in (B, X):
        for data in sorted(x for x in seen if x.startswith("mp:")):                                    # (the v0.18 «jb:» job button has its own owner lock, tested with the jobs)
            out = d.press(uid, data, v)
            assert out.alerts == [t("fa", "mp.not_yours")], (uid, data)                                  # a short, localized alert...
            assert t("fa", "mp.not_yours") == "این نقشه برای بازیکن دیگری است. برای استفاده از نقشه، دستور مپ را بزن."
            assert not out.edits and not out.sent, (uid, data)                                           # ...the map message is not edited, nothing is sent
            assert "mp:" not in out.alerts[0] and "Traceback" not in out.alerts[0]
    assert snap(h.db) == before                                                                          # not one row anywhere: positions, trips, sessions, money, users
    assert h.db.q1("SELECT COUNT(*) c FROM users WHERE id=?", (X,))["c"] == 0                            # a stranger is not even registered by pressing
    still = d.press(A, v.buttons[0][1], v)
    assert still.edits and not still.alerts                                                              # and the owner is unaffected


def test_owner_check_survives_forged_callback_data(h, d):
    v = d.open(A)
    w = d.session(A)
    before = snap(h.db)
    forged = [f"mp:{v.sid}:rf:{A}", f"mp:{v.sid}:{A}:rf", f"mp:{A}:rf", f"mp:{v.sid.upper()}:rf"]
    for uid in (B, X):
        for data in (f"mp:{v.sid}:rf:{A}", f"mp:{v.sid}:o:p1:{A}", f"mp:{v.sid}:nb"):
            o = d.press(uid, data, v)
            assert o.alerts == [t("fa", "mp.not_yours")] and not o.edits
    assert snap(h.db) == before
    for data in forged[2:]:                                                                              # nonsense never raises or edits
        o = d.press(A, data, v)
        assert not o.edits and len(o.alerts) == 1 and "Traceback" not in o.alerts[0]
    assert snap(h.db) == before


def test_each_player_opens_and_uses_their_own_map_in_the_same_group(h, d):
    va, vb = d.open(A), d.open(B)
    assert va.sid != vb.sid and va.mid != vb.mid
    sa, sb = h.db.q1("SELECT * FROM map_sessions WHERE sid=?", (va.sid,)), h.db.q1("SELECT * FROM map_sessions WHERE sid=?", (vb.sid,))
    assert (sa["user_id"], sb["user_id"]) == (A, B) and sa["world_id"] == sb["world_id"]
    assert d.press(A, va.find(t("fa", "mp.b.near")), va).edits and d.press(B, vb.find(t("fa", "mp.b.near")), vb).edits
    cross = d.press(B, va.find(t("fa", "mp.b.places")), va)                                              # B on A's message
    assert cross.alerts == [t("fa", "mp.not_yours")] and not cross.edits
    cross = d.press(A, vb.find(t("fa", "mp.b.places")), vb)                                              # A on B's message
    assert cross.alerts == [t("fa", "mp.not_yours")] and not cross.edits
    mine = d.press(A, vb.find(t("fa", "mp.b.places")), va, mid=va.mid)                                    # A's own message but B's button: not A's session
    assert mine.alerts == [t("fa", "mp.not_yours")] and not mine.edits


def test_a_button_only_works_on_its_own_message_and_chat(h, d):
    v = d.open(A)
    data = v.find(t("fa", "mp.b.near"))
    before = snap(h.db)
    wrong_msg = d.press(A, data, v, mid=v.mid + 1)
    assert wrong_msg.alerts == [t("fa", "mp.stale")] and not wrong_msg.edits                           # a copy of the button on another message
    other_chat = d.press(A, data, v, chat=CH2)
    assert other_chat.alerts and not other_chat.edits and not other_chat.sent                           # the same button in another group
    assert snap(h.db) == before


def test_expired_and_unknown_sessions_fail_safely(h, d):
    v = d.open(A)
    data = v.find(t("fa", "mp.b.lots"))
    assert d.press(A, data, v).edits
    h.advance(C.map_session_ttl() - 10)
    assert d.press(A, data, v).edits                                                                     # using the map keeps it alive...
    h.advance(C.map_session_ttl() - 10)
    assert d.press(A, data, v).edits
    before = snap(h.db)
    h.advance(C.map_session_ttl() + 5)                                                                   # ...an untouched one dies after its time
    out = d.press(A, data, v)
    assert out.alerts == [t("fa", "mp.expired")] and not out.edits and not out.sent
    assert snap(h.db) == before
    for junk in ("mp", "mp:zz", "mp:00000000:rf", "mp:00000000", "mp::rf"):
        o = d.press(A, junk, v)
        assert not o.edits and len(o.alerts) == 1
    fresh = d.open(A)
    assert d.press(A, fresh.find(t("fa", "mp.b.near")), fresh).edits                                    # «مپ» again gives a working map


def test_old_sessions_beyond_the_limit_stop_working(h, d):
    views = [d.open(A) for _ in range(C.MAP_SESSIONS_PER_PLAYER + 2)]
    assert h.db.q1("SELECT COUNT(*) c FROM map_sessions WHERE user_id=?", (A,))["c"] == C.MAP_SESSIONS_PER_PLAYER
    gone = d.press(A, views[0].find(t("fa", "mp.b.near")), views[0])
    assert gone.alerts == [t("fa", "mp.expired")] and not gone.edits
    assert d.press(A, views[-1].find(t("fa", "mp.b.near")), views[-1]).edits
    assert d.press(A, views[2].find(t("fa", "mp.b.near")), views[2]).edits


def test_a_stale_screen_button_does_nothing(h, d):
    v = d.open(A)
    # an unknown action of a valid session and a «go» of a place that is not on screen are refused, not guessed
    before = snap(h.db)
    for data in (f"mp:{v.sid}:bogus", f"mp:{v.sid}:go:p1", f"mp:{v.sid}:ce", f"mp:{v.sid}:bj:o", f"mp:{v.sid}:bj:h:1", f"mp:{v.sid}:o:p99999", f"mp:{v.sid}:o:d1"):
        o = d.press(A, data, v)
        assert not o.edits and len(o.alerts) == 1, data
    assert snap(h.db)["world_trips"] == before["world_trips"] and snap(h.db)["casino_games"] == before["casino_games"] == []


def test_the_main_menu_map_button_opens_a_working_owner_map(h, d):
    h.say(A, "!منو", chat=CH)
    calls = h.press(A, f"mn:go:map:{A}", chat=CH)
    ph = [c for c in calls if c.__class__.__name__ == "SendPhoto"]
    assert len(ph) == 1
    row = d.session(A)
    assert row["message_id"] and row["user_id"] == A
    v = mapkit.Out([])
    v.sid, v.mid, v.buttons = row["sid"], row["message_id"], mapkit.buttons_of(ph[0])
    assert d.press(A, v.find(t("fa", "mp.b.near")), v).edits
    other = d.press(B, v.find(t("fa", "mp.b.near")), v)
    assert other.alerts == [t("fa", "mp.not_yours")] and not other.edits
    stranger = h.press(B, f"mn:go:map:{A}", chat=CH)                                                    # not the owner of the menu: nothing happens
    assert not [c for c in stranger if c.__class__.__name__ == "SendPhoto"]


def test_typed_place_lookup_opens_the_same_place_screen_with_buttons(h, d):
    L = None
    v = d.open(A, "مپ L5")
    assert "L5" in v.caption and v.buttons and v.photo
    assert d.session(A)["view"] == "obj" and d.session(A)["sel"] == "l5"
    labels = [x for x, _ in v.buttons]
    assert t("fa", "mp.b.go") in labels and t("fa", "mp.b.map") in labels
    unknown = h.say(A, "مپ P99999", chat=CH)
    assert "پیدا نشد" in tb.Harness.texts(unknown) and h.db.q1("SELECT COUNT(*) c FROM map_sessions")["c"] == 1


def test_private_chat_still_explains_and_creates_no_session(h):
    calls = h.say(A, "مپ")
    assert "گروه" in tb.Harness.texts(calls)
    assert h.db.q1("SELECT COUNT(*) c FROM map_sessions")["c"] == 0 and h.db.q1("SELECT COUNT(*) c FROM world_players")["c"] == 0


def test_captions_fit_telegram_even_with_long_names_in_both_languages(h, d):
    for uid in range(A, A + 9):
        h.say(uid, "سلام", chat=CH, name="نام خیلی خیلی طولانی کاربر " + str(uid))
    for uid in range(A, A + 9):
        d.open(uid)
    v = d.open(A)
    assert len(v.caption) <= 1024
    from hypemarket import social
    social.set_group_lang(h.db, CH, "en")
    ve = d.open(A, "map")
    assert len(ve.caption) <= 1024
    for data in ve.data():
        o = d.press(A, data, ve)
        assert len(o.caption) <= 1024, data


# ---------------------------------------------------------------------------------------------- the label
def test_the_persian_map_label_is_spelled_with_a_space():
    sub = FA["map.subtitle"]
    assert "نقشه ی" in sub and "نقشه‌ی" not in sub and "نقشهی" not in sub
    for k, v in {**FA, **EN}.items():
        if k.startswith(("map.title", "map.subtitle", "map.legend", "map.zone", "map.lm")):
            assert "نقشهی" not in v and "نقشه‌ی" not in v, k


def test_the_renderer_draws_exactly_that_label_and_normalizes_any_source(monkeypatch):
    seen = []
    real = cards.shape
    monkeypatch.setattr(cards, "shape", lambda text: seen.append(text) or real(text))
    worldmap.render(world.build(7), "fa")
    assert any("نقشه ی شروع" in s for s in seen)
    assert not any("نقشهی" in s or "نقشه‌ی" in s for s in seen)
    assert worldmap.MAP_WORD == "نقشه ی"
    for bad in ("نقشهی", "نقشه‌ی", "نقشه‍ی", "نقشه‌ی گنج"):
        assert worldmap.fix_label(bad).startswith("نقشه ی")
    assert worldmap.fix_label("نقشه ی") == "نقشه ی" and worldmap.fix_label("نقشه") == "نقشه" and worldmap.fix_label("مپ") == "مپ"
    assert worldmap.fix_label(worldmap.fix_label("نقشه‌ی")) == "نقشه ی"


def test_no_map_rendering_source_contains_the_old_spelling():
    """The renderer and every string it DRAWS into the picture keep the plain-space «نقشه ی» (the shaper renders the half-space form badly in a PNG).
    Real Telegram text (captions, errors, help) uses the standard «نقشه‌ی» since v0.18.2, so only the drawn strings are checked."""
    import pathlib
    from hypemarket.texts import EN, FA
    src = (pathlib.Path(worldmap.__file__).parent / "worldmap.py").read_text(encoding="utf-8")
    assert "نقشهی" not in src and "نقشه‌ی" not in src
    drawn = [k for k in FA if k.startswith(("map.lm.", "map.zone.", "map.legend.")) or k in ("map.title", "map.subtitle")]
    assert len(drawn) > 10
    for table in (FA, EN):
        for k in drawn:
            assert "نقشهی" not in table.get(k, "") and "نقشه‌ی" not in table.get(k, ""), k
