"""v0.20: city IDs, the list of cities, and travel between cities.

A city's ID is `worlds.id` (permanent, unique, never reused); `شهرها` lists them read-only; `سفر <ID>` starts a persistent journey (10 real minutes by default) that
survives restarts and offline time, is applied lazily on arrival (exactly once), keeps the birthplace, and closes every city-bound feature (map, jobs, casino, local walking
and hugs) while it lasts. Stock features are untouched."""
import sqlite3
import threading

import pytest

import charkit
import mapkit
import test_bot as tb
from hypemarket import casino, character, config as C, core, fmt, intercity, presence as P, work, world, worldmap, worldplaces
from hypemarket.db import SCHEMA_VERSION, Database
from hypemarket.texts import t

CH1, CH2, CH3 = -100931, -100932, -100933
A, B, C3 = 882001, 882002, 882003
_LAYOUTS: dict = {}


def layout(seed):
    if seed not in _LAYOUTS:
        _LAYOUTS[seed] = world.build(seed)
    return _LAYOUTS[seed]


@pytest.fixture()
def h():
    worldmap.cache_clear()
    worldplaces.cache_clear()
    hh = tb.Harness()
    world.save_world(hh.db, CH1, layout(7))
    world.save_world(hh.db, CH2, layout(5))
    return hh


def join(h, uid, chat=CH1):
    """The first life-game entry (the character's city is this group's city, once); the finished character comes from the suite's fixture."""
    h.say(uid, "مپ", chat=chat)
    assert character.is_done(character.get(h.db, uid))


def wid(h, chat):
    return world.get_world(h.db, chat)["id"]


def txt(calls):
    return tb.Harness.texts(calls)


def active(h, uid=A):
    return intercity.active(h.db, uid)


def snap(db, tables=("characters", "world_players", "world_trips", "intercity_trips", "work_tasks", "casino_games", "life_ledger", "life_accounts", "worlds", "map_sessions", "interactions")):
    return {n: [tuple(r) for r in db.qa(f"SELECT * FROM {n} ORDER BY 1,2")] for n in tables}


def cbdata(calls) -> list:
    """The callback data of every inline button of the messages the bot sent / edited."""
    out = []
    for c in calls:
        kb = getattr(c, "reply_markup", None)
        for row in getattr(kb, "inline_keyboard", None) or []:
            out += [b.callback_data for b in row if getattr(b, "callback_data", None)]
    return out


def go(h, uid, dest, chat=CH1, word="سفر"):
    return h.say(uid, f"{word} {dest}", chat=chat)


# ============================================================================================== IDs
def test_a_city_id_is_the_permanent_worlds_id(h):
    rows = h.db.qa("SELECT id, chat_id FROM worlds ORDER BY id")
    assert [r["id"] for r in rows] == [1, 2] and [r["chat_id"] for r in rows] == [CH1, CH2]          # sequential, in creation order
    assert intercity.city(h.db, 1)["chat_id"] == CH1 and intercity.city(h.db, 2)["chat_id"] == CH2 and intercity.city(h.db, 3) is None


def test_an_id_cannot_be_changed_and_is_never_reused(h):
    with pytest.raises(sqlite3.IntegrityError):
        h.db.x("UPDATE worlds SET id=99 WHERE id=2")
    h.db.x("UPDATE worlds SET name_en='Renamed' WHERE id=2")                                          # everything else about a city may still change
    assert h.db.q1("SELECT id FROM worlds WHERE name_en='Renamed'")["id"] == 2
    h.db.x("DELETE FROM worlds WHERE id=2")
    new, created = world.save_world(h.db, CH3, layout(11))
    assert created and new["id"] == 3                                                                 # the number of a removed city is not given out again


def test_every_saved_city_has_a_unique_id_and_a_name(h):
    for i in range(10):
        world.save_world(h.db, -200000 - i, layout(20 + i))
    ids = [r["id"] for r in h.db.qa("SELECT id FROM worlds")]
    assert len(ids) == len(set(ids)) == 12 and all(isinstance(i, int) and i >= 1 for i in ids)
    assert all(r["name_fa"] and r["name_en"] for r in h.db.qa("SELECT * FROM worlds"))


@pytest.mark.parametrize("raw,expected", [
    ("2", 2), ("12", 12), ("007", 7), ("۲", 2), ("۱۲", 12), ("٢", 2), ("١٢", 12), (" 5 ", 5), ("1۲", 12), ("123456789", 123456789),
    ("", None), ("abc", None), ("1.5", None), ("1,5", None), ("-1", None), ("+1", None), ("1 2", None), ("2x", None), ("x2", None), ("²", None), ("１", None),
    ("0x10", None), ("1234567890", None), ("۱٫۵", None), ("#2", None), ("٢٠٢٣٤٥٦٧٨٩٠", None), (None, None), (5, None),
])
def test_city_id_parsing_reads_only_digits(raw, expected):
    assert intercity.parse_city_id(raw) == expected


def test_a_city_id_that_names_no_city_is_unknown_not_an_error_of_format(h):
    for bad in (0, -1, 3, 10 ** 9, True, "2", 2.0, None):
        assert intercity.city(h.db, bad) is None


# ============================================================================================== the list of cities
def test_the_list_shows_every_city_with_its_id_and_marks_your_own(h):
    join(h, A, CH1)
    out = txt(h.say(A, "شهرها", chat=CH1))
    w1, w2 = intercity.city(h.db, 1), intercity.city(h.db, 2)
    assert w1["name_fa"] in out and w2["name_fa"] in out
    lines = [ln for ln in out.split("\n") if w1["name_fa"] in ln]
    assert lines and "📍" in lines[0] and "شهر تو" in lines[0]                                         # the player's city is marked
    other = [ln for ln in out.split("\n") if w2["name_fa"] in ln]
    assert other and "📍" not in other[0]
    assert "سفر" in out                                                                               # how to travel is on the screen


def test_the_list_is_read_only_and_does_not_create_anything(h):
    join(h, A, CH1)
    before = snap(h.db)
    for typed in ("شهرها", "شهرها 2", "cities", "/cities", "شهرها ۱"):
        h.say(A, typed, chat=CH1)
    assert snap(h.db) == before


def test_the_list_works_in_a_private_chat_too(h):
    join(h, A, CH1)
    out = txt(h.say(A, "شهرها", chat=A))
    assert intercity.city(h.db, 2)["name_fa"] in out


def test_the_list_is_paginated_with_buttons_that_page_in_place(h):
    join(h, A, CH1)
    for i in range(18):
        charkit.fake_world(h.db, -300000 - i, name=f"City{i + 3}")
    assert intercity.city_count(h.db) == 20
    rows, page, pages = intercity.cities_page(h.db, 1)
    assert (len(rows), page, pages) == (8, 1, 3) and [r["id"] for r in rows] == list(range(1, 9))
    rows3, page3, _ = intercity.cities_page(h.db, 3)
    assert [r["id"] for r in rows3] == [17, 18, 19, 20] and page3 == 3
    assert intercity.cities_page(h.db, 99)[1] == 3 and intercity.cities_page(h.db, 0)[1] == 1 and intercity.cities_page(h.db, -5)[1] == 1     # out of range: the nearest real page
    first = h.say(A, "شهرها", chat=CH1)
    out = txt(first)
    assert "City3" in out and "City10" not in out
    assert cbdata(first) == [f"cy:2:{A}"]
    second = h.press(A, f"cy:2:{A}", chat=CH1)
    out2 = txt(second)
    assert "City10" in out2 and "City3" not in out2
    assert cbdata(second) == [f"cy:1:{A}", f"cy:3:{A}"]
    last = txt(h.say(A, "شهرها 3", chat=CH1))
    assert "City20" in last and "City19" in last and "City3" not in last
    assert txt(h.say(A, "شهرها 99", chat=CH1)) == last                                               # clamped to the last page


def test_another_player_cannot_press_my_page_button(h):
    join(h, A, CH1)
    join(h, B, CH1)
    before = snap(h.db)
    calls = h.press(B, f"cy:2:{A}", chat=CH1)
    assert tb.Harness.texts(calls) == t("fa", "not_yours") and snap(h.db) == before                    # a short alert, nothing edited
    assert not any(c.__class__.__name__ in ("EditMessageText", "EditMessageCaption", "SendMessage") for c in calls)


def test_a_malformed_page_is_refused_politely(h):
    join(h, A, CH1)
    assert h.say(A, "شهرها abc", chat=CH1) == []                                                        # not a command in a group: chat
    assert t("fa", "e.ic_bad")[:10] in txt(h.say(A, "/cities abc", chat=CH1))                          # a slash command gets the hint
    before = snap(h.db)
    calls = h.press(A, f"cy:x:{A}", chat=CH1)
    assert calls and snap(h.db) == before                                                              # answered, nothing changed


# ============================================================================================== setting off
def test_setting_off_saves_a_journey_and_does_not_move_the_character_yet(h):
    join(h, A, CH1)
    ch0 = character.get(h.db, A)
    now = core.now()
    out = txt(go(h, A, 2))
    w2 = intercity.city(h.db, 2)
    trip = active(h)
    assert trip["from_world_id"] == 1 and trip["to_world_id"] == 2 and trip["started_at"] == now and trip["arrive_at"] == now + 600 and trip["duration"] == 600
    assert w2["name_fa"] in out and fmt.dur(600, "fa") in out and fmt.clock(now + 600, "fa") in out
    ch = character.get(h.db, A)
    assert ch["current_world_id"] == 1 and ch["birth_world_id"] == ch0["birth_world_id"] == 1         # the source city until the arrival is applied
    assert intercity.in_transit(h.db, A) and not character.in_world(h.db, A, 1) and not character.in_world(h.db, A, 2)


@pytest.mark.parametrize("word,digit", [("سفر", "2"), ("سفر", "۲"), ("سفر", "٢"), ("travel", "2"), ("/travel", "2"), ("/سفر", "۲"), ("سفر", " 2")])
def test_the_command_reads_persian_and_english_words_and_digits(h, word, digit):
    join(h, A, CH1)
    go(h, A, digit.strip(), word=word)
    assert active(h) is not None and active(h)["to_world_id"] == 2


def test_travel_can_be_typed_in_any_group_or_in_private(h):
    join(h, A, CH1)
    go(h, A, 2, chat=CH2)                                                                              # a different group: the same character, the same journey
    assert active(h)["from_world_id"] == 1 and active(h)["to_world_id"] == 2
    assert h.db.q1("SELECT COUNT(*) c FROM intercity_trips")["c"] == 1


def test_travel_in_a_private_chat(h):
    join(h, A, CH1)
    h.say(A, "سفر 2", chat=A)
    assert active(h) is not None


@pytest.mark.parametrize("typed,key", [("سفر 999", "e.ic_unknown"), ("سفر 0", "e.ic_unknown"), ("سفر 1", "e.ic_here"), ("/travel 999999999", "e.ic_unknown")])
def test_a_wrong_destination_is_refused_and_changes_nothing(h, typed, key):
    join(h, A, CH1)
    before = snap(h.db)
    out = txt(h.say(A, typed, chat=CH1))
    assert t("fa", key)[:8] in out, (typed, out)
    assert snap(h.db) == before


@pytest.mark.parametrize("typed", ["سفر 1.5", "سفر -2", "سفر 2x", "سفر ²", "سفر 1 2", "سفر abc", "سفر １", "travel 0x2", "travel 12345678901"])
def test_a_malformed_destination_is_refused_changes_nothing_and_is_chat_in_a_group(h, typed):
    join(h, A, CH1)
    before = snap(h.db)
    assert h.say(A, typed, chat=CH1) == []                                                              # a plain word in a group that is not ID-shaped: ordinary chat
    prefixed = "/" + typed.replace("travel", "travel", 1)
    assert t("fa", "e.ic_bad")[:8] in txt(h.say(A, prefixed, chat=CH1)), prefixed                       # a slash command gets the hint
    assert t("fa", "e.ic_bad")[:8] in txt(h.say(A, typed, chat=A)), typed                               # so does the private chat
    assert snap(h.db) == before


def test_a_second_journey_cannot_start_while_one_is_going(h):
    join(h, A, CH1)
    world.save_world(h.db, CH3, layout(11))
    go(h, A, 2)
    first = tuple(active(h))
    h.advance(100)
    before = snap(h.db)
    out = txt(go(h, A, 3))
    assert t("fa", "e.ic_busy")[:12] in out and fmt.dur(500, "fa") in out                              # says how much of the first one is left
    assert tuple(active(h)) == first and snap(h.db) == before
    out2 = txt(go(h, A, 2))                                                                            # the same destination again: still no second journey
    assert t("fa", "e.ic_busy")[:12] in out2 and h.db.q1("SELECT COUNT(*) c FROM intercity_trips")["c"] == 1


def test_a_duplicated_message_makes_one_journey(h):
    join(h, A, CH1)
    go(h, A, 2)
    go(h, A, 2)
    go(h, A, 2)
    assert h.db.q1("SELECT COUNT(*) c FROM intercity_trips")["c"] == 1


def test_a_finished_character_with_no_city_yet_is_told_to_enter_one_not_crashed(h):
    h.say(B, "/start")                                                                                  # finished (the suite's fixture), but never opened the map: no city yet
    assert character.get(h.db, B)["current_world_id"] is None
    for text in ("سفر", "سفر 2"):
        out = txt(h.say(B, text))
        assert t("fa", "ic.nocity") in out or t("fa", "e.ic_nocity") in out, (text, out)
        assert t("fa", "err") not in out
    with pytest.raises(core.GameError) as e:
        intercity.start(h.db, B, 2)
    assert e.value.code == "ic_nocity" and h.db.q1("SELECT COUNT(*) c FROM intercity_trips")["c"] == 0
    assert t("fa", "err") not in txt(h.say(B, "شهرها"))                                                  # the list itself is fine without a city


def test_an_unfinished_character_cannot_travel(h):
    core.ensure_user(h.db, 555, "x", "X", lang="fa")
    character.ensure_profile(h.db, 555)
    from hypemarket.core import GameError
    with pytest.raises(GameError) as e:
        intercity.start(h.db, 555, 2)
    assert e.value.code == "ic_char"
    with pytest.raises(GameError) as e:
        intercity.start(h.db, 556, 2)                                                                  # no character at all
    assert e.value.code == "char_none"
    assert h.db.q1("SELECT COUNT(*) c FROM intercity_trips")["c"] == 0


def test_a_bot_account_never_travels(h):
    from hypemarket.core import GameError
    with pytest.raises(GameError):
        intercity.start(h.db, -5, 2)


# ---- unsafe departures
def test_an_open_job_blocks_departure(h):
    join(h, A, CH1)
    city = worldplaces.load_city(h.db, intercity.city(h.db, 1))
    out = P.start_trip(h.db, city, A, city.mcd_id)
    h.advance(out.trip["arrive_at"] - out.trip["started_at"] + 1)
    work.mcd_start(h.db, city, A, core.now())                                                          # an 8-hour shift at the McDonald's
    assert work.current(h.db, A) is not None
    before = snap(h.db)
    out = txt(go(h, A, 2))
    assert t("fa", "e.ic_job")[:10] in out and snap(h.db) == before and active(h) is None


def test_a_local_walk_blocks_departure_and_a_finished_walk_does_not(h):
    join(h, A, CH1)
    city = worldplaces.load_city(h.db, intercity.city(h.db, 1))
    out = P.start_trip(h.db, city, A, city.casino_id)
    assert out.status == "started"
    before = snap(h.db)
    assert t("fa", "e.ic_trip")[:10] in txt(go(h, A, 2)) and snap(h.db) == before and active(h) is None
    h.advance(out.trip["arrive_at"] - out.trip["started_at"] + 1)                                      # the walk is over (nobody applied it yet)
    go(h, A, 2)
    assert active(h) is not None and P.get_player(h.db, 1, A)["at_place"] == city.casino_id            # it was applied first; they leave from the casino's door


def test_a_blackjack_hand_blocks_departure(h):
    join(h, A, CH1)
    casino.start(h.db, "s-test", 1, A, 0, core.now(), 1_00)
    row = h.db.q1("SELECT state FROM casino_games WHERE sid='s-test'")
    if row["state"] == "playing":
        assert t("fa", "e.ic_casino")[:10] in txt(go(h, A, 2)) and active(h) is None
        casino.act(h.db, "s-test", 1, A, "stand", h.db.q1("SELECT seq FROM casino_games WHERE sid='s-test'")["seq"], core.now())
    go(h, A, 2)
    assert active(h) is not None                                                                       # once the hand is over they may go


# ============================================================================================== the duration setting
def test_the_default_is_ten_real_minutes_from_one_setting(monkeypatch):
    monkeypatch.delenv("INTERCITY_TRAVEL_MINUTES", raising=False)
    assert C.INTERCITY_TRAVEL_MINUTES_DEFAULT == 10 and C.intercity_travel_secs() == 600


@pytest.mark.parametrize("value,secs", [("2", 120), ("0.5", 30), ("1", 60), ("90", 5400), ("", 600), ("abc", 600), ("0", 1), ("-5", 1), ("100000", 86400), ("0.001", 1)])
def test_the_setting_is_read_safely(monkeypatch, value, secs):
    monkeypatch.setenv("INTERCITY_TRAVEL_MINUTES", value)
    assert C.intercity_travel_secs() == secs


def test_a_journey_keeps_the_duration_it_started_with(h, monkeypatch):
    join(h, A, CH1)
    monkeypatch.setenv("INTERCITY_TRAVEL_MINUTES", "2")
    go(h, A, 2)
    first = active(h)
    assert first["duration"] == 120 and first["arrive_at"] - first["started_at"] == 120
    monkeypatch.setenv("INTERCITY_TRAVEL_MINUTES", "30")
    h.say(A, "سفر", chat=CH1)                                                                          # looking at it does not stretch it
    assert tuple(active(h)) == tuple(first)


# ============================================================================================== the status of a journey
def test_the_status_shows_the_destination_the_time_left_and_never_an_early_arrival(h):
    join(h, A, CH1)
    go(h, A, 2)
    w1, w2 = intercity.city(h.db, 1), intercity.city(h.db, 2)
    for passed in (0, 1, 150, 299, 599):
        tb.T[0] = active(h)["started_at"] + passed
        out = txt(h.say(A, "سفر", chat=CH1))
        assert w2["name_fa"] in out and w1["name_fa"] in out and fmt.dur(600 - passed, "fa") in out, (passed, out)
        assert "رسیدی" not in out                                                                       # never a false arrival
        assert active(h) is not None and character.get(h.db, A)["current_world_id"] == 1


def test_the_status_without_a_journey_says_where_you_are(h):
    join(h, A, CH1)
    out = txt(h.say(A, "سفر", chat=A))
    assert intercity.city(h.db, 1)["name_fa"] in out and "شهرها" in out


def test_the_list_marks_the_destination_while_travelling(h):
    join(h, A, CH1)
    go(h, A, 2)
    out = txt(h.say(A, "شهرها", chat=CH1))
    w2 = intercity.city(h.db, 2)
    line = [ln for ln in out.split("\n") if w2["name_fa"] in ln][0]
    assert "مقصد" in line and "🧳" in out


def test_the_status_is_the_same_in_every_group(h):
    join(h, A, CH1)
    go(h, A, 2)
    h.advance(200)
    a = txt(h.say(A, "سفر", chat=CH1))
    b = txt(h.say(A, "سفر", chat=CH2))
    c = txt(h.say(A, "سفر", chat=A))
    assert a == b == c


# ============================================================================================== arrival
def test_arrival_moves_the_character_exactly_once_and_keeps_the_birthplace(h):
    join(h, A, CH1)
    go(h, A, 2)
    trip = active(h)
    tb.T[0] = trip["arrive_at"] - 1
    assert intercity.settle(h.db, A) is None and character.get(h.db, A)["current_world_id"] == 1      # one second early: nothing
    tb.T[0] = trip["arrive_at"]
    done = intercity.settle(h.db, A)
    assert done is not None and done["state"] == "arrived" and done["arrived_at"] == trip["arrive_at"]
    ch = character.get(h.db, A)
    assert ch["current_world_id"] == 2 and ch["birth_world_id"] == 1 and ch["birth_basis"] == "spawn"
    assert active(h) is None and character.in_world(h.db, A, 2) and not character.in_world(h.db, A, 1)
    stamp = ch["updated_at"]
    tb.T[0] += 500
    assert intercity.settle(h.db, A) is None and intercity.settle(h.db, A) is None                    # a repeat applies nothing
    assert character.get(h.db, A)["updated_at"] == stamp and h.db.q1("SELECT COUNT(*) c FROM intercity_trips WHERE state='arrived'")["c"] == 1


def test_a_player_who_was_offline_arrives_when_they_next_type_anything(h):
    join(h, A, CH1)
    go(h, A, 2)
    h.advance(6 * 3600)                                                                                # six hours later
    out = txt(h.say(A, "سفر", chat=CH1))
    assert intercity.city(h.db, 2)["name_fa"] in out and "رسیدی" in out
    assert character.get(h.db, A)["current_world_id"] == 2 and active(h) is None


def test_the_map_after_arrival_is_the_destination_city(h):
    join(h, A, CH1)
    go(h, A, 2)
    h.advance(601)
    d2 = mapkit.Driver(h, CH1)                                                                         # (typed in the OLD city's group: the character's city decides)
    v = d2.open(A)
    assert intercity.city(h.db, 2)["name_fa"] in v.caption
    assert d2.session(A)["world_id"] == 2
    assert P.get_player(h.db, 2, A) is not None


def test_the_position_in_the_new_city_is_the_deterministic_spawn_spot(h):
    join(h, A, CH1)
    go(h, A, 2)
    h.advance(601)
    asyncio_run(h, intercity.asettle(h.db, A))
    row = P.get_player(h.db, 2, A)
    city2 = worldplaces.load_city(h.db, intercity.city(h.db, 2))
    x, y = P.spawn_point(city2, A)
    assert (row["x"], row["y"]) == (round(x, 2), round(y, 2)) and row["at_place"] is None


def asyncio_run(h, coro):
    return h.run(coro)


def test_coming_back_restores_the_position_they_left(h):
    join(h, A, CH1)
    city1 = worldplaces.load_city(h.db, intercity.city(h.db, 1))
    out = P.start_trip(h.db, city1, A, city1.casino_id)
    h.advance(out.trip["arrive_at"] - out.trip["started_at"] + 1)
    P.stand(h.db, city1, A)
    left_at = tuple(P.get_player(h.db, 1, A))
    assert left_at[4] == city1.casino_id
    go(h, A, 2)
    h.advance(601)
    h.say(A, "سفر", chat=CH1)
    assert character.get(h.db, A)["current_world_id"] == 2
    h.say(A, "سفر 1", chat=CH1)
    h.advance(601)
    h.say(A, "سفر", chat=CH1)
    assert character.get(h.db, A)["current_world_id"] == 1
    assert tuple(P.get_player(h.db, 1, A))[:5] == left_at[:5]                                          # the same spot, the same place, as they left it


def test_the_housekeeping_tick_applies_arrivals_without_the_player(h):
    join(h, A, CH1)
    join(h, B, CH1)
    go(h, A, 2)
    go(h, B, 2)
    assert intercity.sweep(h.db) == 0
    h.advance(601)
    assert intercity.sweep(h.db) == 2
    assert intercity.sweep(h.db) == 0
    assert character.get(h.db, A)["current_world_id"] == character.get(h.db, B)["current_world_id"] == 2


def test_somebody_else_looking_at_the_destination_applies_the_arrival(h):
    join(h, A, CH1)
    join(h, C3, CH2)
    go(h, A, 2)
    city2 = worldplaces.load_city(h.db, intercity.city(h.db, 2))
    pos = P.stand(h.db, city2, C3).pos
    assert all(o.uid != A for o in P.nearby(h.db, city2, C3, pos, radius=10 ** 6))                      # on the road: on nobody's map
    h.advance(601)
    P.stand(h.db, city2, A)
    seen = [o.uid for o in P.nearby(h.db, city2, C3, pos, radius=10 ** 6)]
    assert seen == [A] and character.get(h.db, A)["current_world_id"] == 2                             # applied by the destination's own lookup


def test_travelling_back_right_after_arriving_is_allowed(h):
    join(h, A, CH1)
    go(h, A, 2)
    h.advance(601)
    go(h, A, 1)                                                                                         # «سفر 1»: the arrival is applied first, then the new journey starts
    trip = active(h)
    assert trip["from_world_id"] == 2 and trip["to_world_id"] == 1
    assert h.db.q1("SELECT COUNT(*) c FROM intercity_trips")["c"] == 2 and h.db.q1("SELECT COUNT(*) c FROM intercity_trips WHERE state='arrived'")["c"] == 1


# ============================================================================================== survives a restart
def test_a_journey_survives_a_restart_without_resetting_its_clock(tmp_path):
    path = str(tmp_path / "trip.db")
    worldmap.cache_clear()
    worldplaces.cache_clear()
    h1 = tb.Harness()
    db = Database(path)
    core.set_clock(lambda: tb.T[0])
    world.save_world(db, CH1, layout(7))
    world.save_world(db, CH2, layout(5))
    core.ensure_user(db, A, "a", "A", lang="fa")
    charkit.complete(db, A, world_id=1)
    tb.T[0] = 1_700_000_000
    trip = intercity.start(db, A, 2, tb.T[0])
    saved = tuple(trip)
    db.close()
    tb.T[0] += 300                                                                                      # the bot was down for five minutes
    db2 = Database(path)
    assert tuple(intercity.active(db2, A)) == saved                                                     # same row: same start, same end
    assert intercity.remaining(intercity.active(db2, A), tb.T[0]) == 300
    assert intercity.settle(db2, A) is None
    tb.T[0] += 301
    done = intercity.settle(db2, A)
    assert done is not None and character.get(db2, A)["current_world_id"] == 2
    db2.close()
    db3 = Database(path)
    assert intercity.settle(db3, A) is None and db3.q1("SELECT COUNT(*) c FROM intercity_trips")["c"] == 1 and character.get(db3, A)["current_world_id"] == 2
    del h1


# ============================================================================================== while travelling
def test_the_map_is_closed_while_travelling(h):
    join(h, A, CH1)
    go(h, A, 2)
    before_sessions = h.db.q1("SELECT COUNT(*) c FROM map_sessions")["c"]
    for typed in ("مپ", "map", "/map", "مپ P3", "مپ L5"):
        out = txt(h.say(A, typed, chat=CH1))
        assert t("fa", "ic.guard").split("\n")[0][:14] in out and intercity.city(h.db, 2)["name_fa"] in out, typed
    assert h.db.q1("SELECT COUNT(*) c FROM map_sessions")["c"] == before_sessions                       # no map message, no session
    assert not any(isinstance(c, tb.SendPhoto) for c in h.say(A, "مپ", chat=CH1))


def test_every_button_of_an_old_map_message_is_closed_while_travelling(h):
    join(h, A, CH1)
    d = mapkit.Driver(h, CH1)
    v = d.open(A)
    sid = v.sid
    go(h, A, 2)
    before = snap(h.db)
    for act in ("rf", "nb", "pl:0", "ln:0", "tr", "o:casino", "o:p3", "go:l5", "ce", "bj:o", "bj:b:0:100", "home"):
        out = d.press(A, f"mp:{sid}:{act}", v)
        assert out.alerts == [t("fa", "ic.guard.brief")] and not out.edits, act
    assert snap(h.db) == before


def test_jobs_are_closed_while_travelling(h):
    join(h, A, CH1)
    go(h, A, 2)
    before = snap(h.db)
    for typed in ("مشاغل", "اسنپ", "مک دونالد", "شهرداری", "jobs"):
        out = txt(h.say(A, typed, chat=CH1))
        assert t("fa", "ic.guard").split("\n")[0][:14] in out, (typed, out)
    assert snap(h.db) == before and work.current(h.db, A) is None


def test_a_traveller_cannot_be_greeted_or_hugged_and_cannot_hug(h):
    join(h, A, CH1)
    join(h, B, CH1)
    city1 = worldplaces.load_city(h.db, intercity.city(h.db, 1))
    from hypemarket import interact
    now = core.now()
    pa = P.stand(h.db, city1, A).pos
    h.db.x("UPDATE world_players SET x=?, y=? WHERE world_id=1 AND user_id=?", (pa[0], pa[1], B))        # standing next to each other
    assert interact.near(h.db, city1, A, B, now) is True and interact.near(h.db, city1, B, A, now) is True
    go(h, A, 2)
    assert interact.near(h.db, city1, A, B, now) is False and interact.near(h.db, city1, B, A, now) is False
    sa = P.stand(h.db, city1, B)
    assert all(o.uid != A for o in P.nearby(h.db, city1, B, sa.pos, radius=10 ** 6))


def test_stock_features_still_work_while_travelling(h):
    join(h, A, CH1)
    go(h, A, 2)
    assert "🪙" in txt(h.say(A, "پول", chat=CH1)) or "$" in txt(h.say(A, "پول", chat=CH1))
    assert txt(h.say(A, "سهام بازار", chat=CH1))
    assert txt(h.say(A, "پروفایل", chat=CH1))
    assert txt(h.say(A, "روزانه", chat=CH1))


def test_old_map_sessions_of_the_source_city_are_stale_after_arrival(h):
    join(h, A, CH1)
    d = mapkit.Driver(h, CH1)
    v = d.open(A)
    go(h, A, 2)
    h.advance(601)
    out = d.press(A, f"mp:{v.sid}:rf", v)
    assert out.alerts and out.alerts != [t("fa", "ic.guard.brief")] and not out.edits                            # stale, not «you are travelling»: they are no longer on the road
    assert character.get(h.db, A)["current_world_id"] == 2


# ============================================================================================== the database keeps the rules
def raw_insert(db, **kw):
    row = dict(user_id=A, from_world_id=1, to_world_id=2, started_at=100, arrive_at=700, duration=600, state="travelling", arrived_at=None)
    row.update(kw)
    cols = ",".join(row)
    return db.x(f"INSERT INTO intercity_trips({cols}) VALUES({','.join('?' * len(row))})", tuple(row.values()))


def test_the_database_allows_one_open_journey_per_player(h):
    join(h, A, CH1)
    raw_insert(h.db)
    with pytest.raises(sqlite3.IntegrityError):
        raw_insert(h.db, to_world_id=2, started_at=101, arrive_at=701)


@pytest.mark.parametrize("bad", [dict(to_world_id=1), dict(arrive_at=100, duration=0), dict(arrive_at=50, duration=-50), dict(duration=599), dict(state="gone"),
                                 dict(state="arrived"), dict(arrived_at=700), dict(to_world_id=77), dict(from_world_id=2), dict(user_id=12345)])
def test_the_database_refuses_impossible_journeys(h, bad):
    join(h, A, CH1)
    with pytest.raises(sqlite3.IntegrityError):
        raw_insert(h.db, **bad)
    assert h.db.q1("SELECT COUNT(*) c FROM intercity_trips")["c"] == 0


def test_the_database_refuses_a_journey_for_an_unfinished_character(h):
    core.ensure_user(h.db, 556, "p", "P", lang="fa")
    character.ensure_profile(h.db, 556)
    h.db.x("UPDATE characters SET birth_world_id=1, current_world_id=1 WHERE user_id=556")
    with pytest.raises(sqlite3.IntegrityError):
        raw_insert(h.db, user_id=556)


def test_a_journey_row_cannot_be_rewritten_early_or_deleted(h):
    join(h, A, CH1)
    raw_insert(h.db)
    for sql in ("UPDATE intercity_trips SET to_world_id=1", "UPDATE intercity_trips SET arrive_at=200, duration=100", "UPDATE intercity_trips SET started_at=0",
                "UPDATE intercity_trips SET user_id=1", "UPDATE intercity_trips SET state='arrived', arrived_at=699",        # one second early
                "DELETE FROM intercity_trips"):
        with pytest.raises(sqlite3.IntegrityError):
            h.db.x(sql)
    with pytest.raises(sqlite3.IntegrityError):                                                         # arriving without the character being there
        h.db.x("UPDATE intercity_trips SET state='arrived', arrived_at=700")
    h.db.x("UPDATE characters SET current_world_id=2 WHERE user_id=?", (A,))
    h.db.x("UPDATE intercity_trips SET state='arrived', arrived_at=700")
    for sql in ("UPDATE intercity_trips SET state='travelling', arrived_at=NULL", "UPDATE intercity_trips SET arrived_at=900"):
        with pytest.raises(sqlite3.IntegrityError):
            h.db.x(sql)


# ============================================================================================== concurrency
def test_two_connections_racing_to_set_off_end_with_one_journey(tmp_path):
    path = str(tmp_path / "race.db")
    d0 = Database(path)
    core.set_clock(lambda: 1_700_000_000)
    world.save_world(d0, CH1, layout(7))
    world.save_world(d0, CH2, layout(5))
    world.save_world(d0, CH3, layout(11))
    core.ensure_user(d0, A, "a", "A", lang="fa")
    charkit.complete(d0, A, world_id=1)
    d0.close()
    results, barrier = [], threading.Barrier(4)

    def attempt(dest):
        db = Database(path)
        try:
            barrier.wait()
            results.append(("ok", intercity.start(db, A, dest, 1_700_000_000)["to_world_id"]))
        except Exception as e:                                                                          # noqa: BLE001
            results.append((type(e).__name__, getattr(e, "code", str(e))))
        finally:
            db.close()

    threads = [threading.Thread(target=attempt, args=(d,)) for d in (2, 3, 2, 3)]
    [th.start() for th in threads]
    [th.join() for th in threads]
    assert sum(1 for r in results if r[0] == "ok") == 1, results
    assert all(r[0] in ("ok", "GameError", "OperationalError") for r in results), results
    check = Database(path)
    assert check.q1("SELECT COUNT(*) c FROM intercity_trips")["c"] == 1


def test_two_connections_racing_to_apply_the_arrival_apply_it_once(tmp_path):
    path = str(tmp_path / "race2.db")
    d0 = Database(path)
    core.set_clock(lambda: 1_700_000_000)
    world.save_world(d0, CH1, layout(7))
    world.save_world(d0, CH2, layout(5))
    core.ensure_user(d0, A, "a", "A", lang="fa")
    charkit.complete(d0, A, world_id=1)
    intercity.start(d0, A, 2, 1_700_000_000)
    d0.close()
    results, barrier = [], threading.Barrier(4)

    def attempt():
        db = Database(path)
        try:
            barrier.wait()
            results.append(intercity.settle(db, A, 1_700_000_700) is not None)
        except Exception as e:                                                                          # noqa: BLE001
            results.append(type(e).__name__)
        finally:
            db.close()

    threads = [threading.Thread(target=attempt) for _ in range(4)]
    [th.start() for th in threads]
    [th.join() for th in threads]
    assert results.count(True) == 1 and all(r in (True, False, "OperationalError") for r in results), results
    check = Database(path)
    assert check.q1("SELECT COUNT(*) c FROM intercity_trips WHERE state='arrived'")["c"] == 1 and character.get(check, A)["current_world_id"] == 2


# ============================================================================================== typing in a group: what is and is not a command
def test_a_bare_travel_word_in_a_group_is_ordinary_chat_unless_you_are_travelling(h):
    join(h, A, CH1)
    assert h.say(A, "سفر", chat=CH1) == []                                                              # «سفر» alone in a group: just a word
    assert h.say(A, "سفر خوش", chat=CH1) == [] and h.say(A, "سفر به شمال عالیه", chat=CH1) == []
    go(h, A, 2)
    assert intercity.city(h.db, 2)["name_fa"] in txt(h.say(A, "سفر", chat=CH1))                           # on a journey it shows it
    assert h.say(A, "سفر خوش", chat=CH1) == []
    assert h.say(B, "سفر", chat=CH1) == []


def test_the_city_list_word_in_a_group_only_answers_on_its_own(h):
    join(h, A, CH1)
    assert intercity.city(h.db, 2)["name_fa"] in txt(h.say(A, "شهرها", chat=CH1))
    assert h.say(A, "شهرها خیلی قشنگن", chat=CH1) == []
    assert h.say(A, "شهرها abc", chat=CH1) == []


def test_the_main_menu_offers_the_city_list(h):
    join(h, A, CH1)
    assert f"mn:go:cities:{A}" in cbdata(h.say(A, "منو", chat=CH1))


def test_schema_has_the_journey_table(h):
    assert SCHEMA_VERSION == 11
    names = {r["name"] for r in h.db.qa("SELECT name FROM sqlite_master WHERE type='table'")}
    assert "intercity_trips" in names
