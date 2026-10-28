"""v0.19: a character lives in ONE city, whatever Telegram group the player types in. The map, the position, trips, jobs, the casino, the people nearby and the
map sessions all follow the character's authoritative city (`characters.current_world_id`), never the group."""
import asyncio
import sqlite3

import pytest

import charkit
import mapkit
import test_bot as tb
from hypemarket import character, core, interact, presence as P, world, worldmap, worldplaces
from hypemarket.db import Database
from hypemarket.texts import t
from test_casino import enter

CH1, CH2, CH3 = -100911, -100912, -100913
A, B, C3 = 881001, 881002, 881003
NAMES = {A: "علی", B: "رضا", C3: "سارا"}
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
    """The real way in: the first life-game entry in `chat`, then the private set-up."""
    h.say(uid, "مپ", chat=chat)
    h.say(uid, "/start char")
    h.say(uid, NAMES.get(uid, "نام"))
    h.say(uid, "رضایی")
    h.press(uid, f"cr:g:male:{uid}")
    assert character.is_done(character.get(h.db, uid))


def city_of(h, uid):
    return worldplaces.load_city(h.db, character.current_world(h.db, uid))


def photos(calls):
    return [c for c in calls if isinstance(c, tb.SendPhoto)]


def world_id(h, chat):
    return world.get_world(h.db, chat)["id"]


# ================================================================================================== one city, every group
def test_the_map_in_another_group_is_the_same_city_at_the_same_place(h):
    join(h, A, CH1)
    d1, d2 = mapkit.Driver(h, CH1), mapkit.Driver(h, CH2)
    v1 = d1.open(A)
    pos = P.get_player(h.db, world_id(h, CH1), A)
    v2 = d2.open(A)
    assert mapkit.same_city(v1.photo, v2.photo) and v1.caption == v2.caption
    s1, s2 = d1.session(A), d2.session(A)
    assert s1["world_id"] == s2["world_id"] == world_id(h, CH1) != world_id(h, CH2)
    assert (s1["chat_id"], s2["chat_id"]) == (CH1, CH2)                                          # each session belongs to its own group's message
    assert h.db.qa("SELECT world_id FROM world_players WHERE user_id=?", (A,))[0]["world_id"] == world_id(h, CH1)
    assert h.db.q1("SELECT COUNT(*) c FROM world_players WHERE user_id=?", (A,))["c"] == 1
    again = P.get_player(h.db, world_id(h, CH1), A)
    assert (again["x"], again["y"]) == (pos["x"], pos["y"])


def test_a_group_never_gets_a_city_for_a_player_who_already_has_one(h):
    d = h.db
    d.x("DELETE FROM worlds WHERE chat_id=?", (CH2,))                                            # the second group has no city at all
    join(h, A, CH1)
    n = d.q1("SELECT COUNT(*) c FROM worlds")["c"]
    assert photos(h.say(A, "مپ", chat=CH2)) and photos(h.say(A, "map", chat=CH3))
    assert d.q1("SELECT COUNT(*) c FROM worlds")["c"] == n == 1                                  # asking from new groups built nothing
    assert world.get_world(d, CH2) is None and world.get_world(d, CH3) is None


def test_worlds_chat_id_keeps_meaning_the_group_the_city_was_made_for(h):
    join(h, A, CH1)
    snap = lambda: {r["chat_id"]: tuple(r) for r in h.db.qa("SELECT * FROM worlds")}
    before = snap()
    for chat in (CH2, CH3, CH1):
        h.say(A, "مپ", chat=chat)
        mapkit.Driver(h, chat).open(A)
    assert snap() == before and set(before) == {CH1, CH2}                                        # nothing rewritten, nothing added (CH3 never got a city)
    assert before[CH1][0] == world_id(h, CH1) == character.get(h.db, A)["current_world_id"]


def test_the_city_is_decided_once_by_the_first_entry_and_never_by_a_later_group(h):
    join(h, A, CH2)                                                                              # A's first entry is in the SECOND group
    ch = character.get(h.db, A)
    assert ch["birth_world_id"] == ch["current_world_id"] == world_id(h, CH2)
    for chat in (CH1, CH3, CH2):
        assert photos(h.say(A, "مپ", chat=chat))
        assert character.get(h.db, A)["current_world_id"] == world_id(h, CH2)
    assert {r["world_id"] for r in h.db.qa("SELECT world_id FROM map_sessions WHERE user_id=?", (A,))} == {world_id(h, CH2)}


def test_two_players_of_one_group_can_live_in_different_cities(h):
    join(h, A, CH1)
    join(h, B, CH2)
    assert character.get(h.db, A)["current_world_id"] == world_id(h, CH1) and character.get(h.db, B)["current_world_id"] == world_id(h, CH2)
    h.say(A, "مپ", chat=CH2)                                                                     # the players are in the same group now: still different cities
    h.say(B, "مپ", chat=CH2)
    c1 = city_of(h, A)
    assert P.nearby(h.db, c1, A, P.stand(h.db, c1, A).pos, radius=1e9) == []
    assert character.get(h.db, A)["current_world_id"] != character.get(h.db, B)["current_world_id"]


def test_a_trip_started_in_one_group_is_the_same_trip_in_the_next(h):
    join(h, A, CH1)
    c = city_of(h, A)
    out = P.start_trip(h.db, c, A, c.mcd_id)
    assert out.status == "started"
    trip = P.walking_trip(h.db, c.id, A)
    here, there = mapkit.Driver(h, CH1).open(A), mapkit.Driver(h, CH2).open(A)
    assert here.caption == there.caption and mapkit.same_city(here.photo, there.photo)
    assert h.db.q1("SELECT COUNT(*) c FROM world_trips WHERE user_id=?", (A,))["c"] == 1
    assert P.walking_trip(h.db, c.id, A)["id"] == trip["id"]
    h.advance(trip["arrive_at"] - core.now() + 1)
    arrived = mapkit.Driver(h, CH2).open(A)
    assert P.walking_trip(h.db, c.id, A) is None and P.get_player(h.db, c.id, A)["at_place"] == c.mcd_id
    assert arrived.caption != there.caption


def test_the_position_is_the_same_wherever_the_player_looks_from(h):
    join(h, A, CH1)
    d1, d2 = mapkit.Driver(h, CH1), mapkit.Driver(h, CH2)
    d1.open(A)
    c = city_of(h, A)
    P.start_trip(h.db, c, A, c.mcd_id)
    h.advance(10)
    s1 = P.stand(h.db, c, A)
    d2.open(A)
    s2 = P.stand(h.db, c, A)
    assert s1.pos == s2.pos and s1.trip["id"] == s2.trip["id"]                                                 # the same place, seen from either group
    assert h.db.q1("SELECT COUNT(*) c FROM world_players WHERE user_id=?", (A,))["c"] == 1
    assert h.db.q1("SELECT COUNT(*) c FROM world_trips WHERE user_id=?", (A,))["c"] == 1


def test_a_job_started_in_one_group_lives_in_the_characters_city_and_is_seen_from_another(h):
    join(h, A, CH1)
    out = h.press(A, f"jb:sr:{A}", chat=CH2)                                                     # a ride is requested from the OTHER group
    task = h.db.q1("SELECT * FROM work_tasks WHERE user_id=? ORDER BY id DESC LIMIT 1", (A,))
    assert task is not None and task["world_id"] == world_id(h, CH1) and task["state"] == "open"
    assert h.db.q1("SELECT COUNT(*) c FROM work_tasks WHERE user_id=?", (A,))["c"] == 1
    again = mapkit.Out(h.press(A, f"jb:o:snapp:{A}", chat=CH1, photo=True))
    other = mapkit.Out(h.press(A, f"jb:o:snapp:{A}", chat=CH2, photo=True))
    assert again.caption and again.caption == other.caption                                      # the same task screen in both groups
    assert h.db.q1("SELECT COUNT(*) c FROM work_tasks WHERE user_id=?", (A,))["c"] == 1


def test_the_jobs_list_is_the_same_in_every_group_and_builds_no_city(h):
    join(h, A, CH1)
    h.db.x("DELETE FROM worlds WHERE chat_id=?", (CH2,))
    a, b = h.say(A, "مشاغل", chat=CH1), h.say(A, "مشاغل", chat=CH2)
    assert [c.text for c in a if hasattr(c, "text")] == [c.text for c in b if hasattr(c, "text")] and a
    assert world.get_world(h.db, CH2) is None


def test_the_casino_is_the_one_of_the_characters_city_from_any_group(h):
    join(h, A, CH1)
    d2 = mapkit.Driver(h, CH2)
    v, lobby = enter(h, d2, A)                                                                   # walk to the casino and enter, all typed in the second group
    assert lobby.caption.split("\n")[0] == t("fa", "mp.cas.title")
    assert P.get_player(h.db, world_id(h, CH1), A)["at_place"] == "l5"
    assert h.db.q1("SELECT COUNT(*) c FROM world_players WHERE user_id=?", (A,))["c"] == 1
    assert h.db.q1("SELECT world_id FROM map_sessions WHERE user_id=? AND chat_id=?", (A, CH2))["world_id"] == world_id(h, CH1)
    # the same table from the first group
    d1 = mapkit.Driver(h, CH1)
    again = d1.open(A)
    assert again.caption and h.db.q1("SELECT COUNT(*) c FROM world_trips WHERE user_id=?", (A,))["c"] == 1


# ================================================================================================== map sessions
def test_a_session_of_another_group_keeps_every_protection(h):
    join(h, A, CH1)
    join(h, B, CH1)
    d = mapkit.Driver(h, CH2)
    v = d.open(A)
    ok = d.press(A, f"mp:{v.sid}:rf", v)
    assert ok.edit is not None                                                                   # the owner, in that group, on that message: it works
    not_yours = d.press(B, f"mp:{v.sid}:rf", v)
    assert t("fa", "mp.not_yours") in not_yours.toasts and not not_yours.edits
    wrong_chat = h.press(A, f"mp:{v.sid}:rf", chat=CH1, mid=v.mid, photo=True)
    assert t("fa", "mp.stale") in [c.text for c in wrong_chat if isinstance(c, tb.AnswerCallbackQuery)]
    wrong_msg = d.press(A, f"mp:{v.sid}:rf", v, mid=v.mid + 999)
    assert t("fa", "mp.stale") in wrong_msg.toasts and not wrong_msg.edits
    fake = d.press(A, "mp:deadbeef:rf", v)
    assert t("fa", "mp.expired") in fake.toasts
    h.advance(10 ** 6)
    late = d.press(A, f"mp:{v.sid}:rf", v)
    assert t("fa", "mp.expired") in late.toasts and not late.edits


def test_a_session_of_a_city_that_is_no_longer_the_characters_acts_on_nothing(h):
    join(h, A, CH1)
    d = mapkit.Driver(h, CH1)
    v = d.open(A)
    other = world_id(h, CH2)
    h.db.x("UPDATE characters SET current_world_id=? WHERE user_id=?", (other, A))               # (a future «move» would do this; v0.19 has no such feature)
    out = d.press(A, f"mp:{v.sid}:rf", v)
    assert t("fa", "mp.stale") in out.toasts and not out.edits
    assert h.db.q1("SELECT COUNT(*) c FROM world_players WHERE world_id=? AND user_id=?", (other, A))["c"] == 0      # and it created no presence anywhere


def test_sessions_of_a_pending_character_do_nothing(h):
    join(h, A, CH1)
    d = mapkit.Driver(h, CH1)
    v = d.open(A)
    h.db.x("DROP TRIGGER trg_char_update")
    h.db.x("UPDATE characters SET setup_state='gender', age_started_at=NULL")                    # a damaged row
    out = d.press(A, f"mp:{v.sid}:rf", v)
    assert not out.edits and out.toasts


def test_a_players_open_map_messages_in_several_groups_all_work(h):
    join(h, A, CH1)
    d1, d2 = mapkit.Driver(h, CH1), mapkit.Driver(h, CH2)
    v1, v2 = d1.open(A), d2.open(A)
    assert d1.press(A, f"mp:{v1.sid}:rf", v1).edit is not None
    assert d2.press(A, f"mp:{v2.sid}:rf", v2).edit is not None
    assert h.db.q1("SELECT COUNT(*) c FROM map_sessions WHERE user_id=?", (A,))["c"] == 2


# ================================================================================================== the people around
def meet(h, *uids):
    for u in uids:
        c = city_of(h, u)
        P.start_trip(h.db, c, u, c.mcd_id)
        tr = P.walking_trip(h.db, c.id, u)
        if tr is not None:
            h.advance(tr["arrive_at"] - core.now() + 1)
        P.stand(h.db, c, u)


def test_players_see_each_other_by_city_not_by_group(h):
    join(h, A, CH1)
    join(h, B, CH1)
    join(h, C3, CH2)
    meet(h, A, B)
    c = city_of(h, A)
    me = P.stand(h.db, c, A)
    assert [o.uid for o in P.nearby(h.db, c, A, me.pos)] == [B]                                  # B lives in A's city
    mapkit.Driver(h, CH2).open(A)                                                                # A looks from the second group: C3's group, but not C3's city
    mapkit.Driver(h, CH2).open(B)
    assert [o.uid for o in P.nearby(h.db, c, A, me.pos)] == [B]
    assert C3 not in [o.uid for o in P.nearby(h.db, c, A, me.pos, radius=1e9)]


def test_old_rows_of_another_city_never_make_ghost_markers(h):
    join(h, A, CH1)
    join(h, B, CH1)
    meet(h, A, B)
    c1, c2 = city_of(h, A), worldplaces.load_city(h.db, world.get_world(h.db, CH2))
    # B also has an OLD presence row in the second city (the shape the v0.18 per-group model could leave behind)
    h.db.x("INSERT INTO world_players(world_id,user_id,x,y,at_place,spawn_x,spawn_y,created_at,updated_at) VALUES(?,?,5,5,NULL,5,5,1,1)", (c2.id, B))
    join(h, C3, CH2)
    me = P.stand(h.db, c2, C3)
    assert B not in [o.uid for o in P.nearby(h.db, c2, C3, me.pos, radius=1e9)]                  # B is not a marker in a city they do not live in
    assert B in [o.uid for o in P.nearby(h.db, c1, A, P.stand(h.db, c1, A).pos, radius=1e9)]     # and still one in their own, at their own position
    assert h.db.q1("SELECT COUNT(*) c FROM world_players WHERE user_id=?", (B,))["c"] == 2        # nothing was deleted


def test_a_character_that_is_not_finished_is_not_a_marker(h):
    join(h, A, CH1)
    h.say(B, "مپ", chat=CH1)                                                                     # B: a pending profile, city assigned
    c = city_of(h, A)
    P.ensure_player(h.db, c, B)                                                                  # even with a presence row in the city
    me = P.stand(h.db, c, A)
    assert [o.uid for o in P.nearby(h.db, c, A, me.pos, radius=1e9)] == []


def test_the_nearby_list_shows_the_first_name_of_the_character(h):
    join(h, A, CH1)
    join(h, B, CH1)
    meet(h, A, B)
    d = mapkit.Driver(h, CH1)
    m = d.open(A)
    nb = d.go(A, m, data=f"mp:{m.sid}:nb")
    assert any(NAMES[B] in label for label, _ in nb.buttons)
    h.db.x("UPDATE users SET name='SomeoneElse' WHERE id=?", (B,))                               # the Telegram name is not the in-world name
    nb2 = d.go(A, nb, data=f"mp:{nb.sid}:nb")
    assert any(NAMES[B] in label for label, _ in nb2.buttons) and not any("SomeoneElse" in label for label, _ in nb2.buttons)


def _hello(h, d, who, to):
    m = d.open(who)
    nb = d.go(who, m, data=f"mp:{m.sid}:nb")
    pp = d.go(who, nb, data=f"mp:{nb.sid}:pp:{to}")
    return d.go(who, pp, data=f"mp:{pp.sid}:gr:{to}")


def test_a_greeting_across_groups_needs_the_other_player_to_be_in_this_group(h):
    join(h, A, CH1)
    join(h, B, CH1)
    meet(h, A, B)
    d2 = mapkit.Driver(h, CH2)
    out = _hello(h, d2, A, B)                                                                    # A types in the second group; B has never been seen there
    assert t("fa", "e.ia_elsewhere") in out.toasts and h.db.q1("SELECT COUNT(*) c FROM interactions")["c"] == 0
    h.db.x("INSERT INTO group_members(group_id,user_id,last_seen) VALUES(?,?,?)", (CH2, B, core.now()))
    out = _hello(h, d2, A, B)
    assert h.db.q1("SELECT COUNT(*) c FROM interactions WHERE chat_id=? AND from_id=? AND to_id=?", (CH2, A, B))["c"] == 1


def test_nearness_needs_two_finished_characters_of_the_same_city(h):
    join(h, A, CH1)
    join(h, C3, CH2)
    h.say(B, "مپ", chat=CH1)                                                                     # B: pending, in A's city
    c = city_of(h, A)
    now = core.now()
    for u in (B, C3):
        P.ensure_player(h.db, c, u)                                                              # both have a presence row in A's city...
    assert interact.near(h.db, c, A, B, now) is False                                            # ...but one is unfinished,
    assert interact.near(h.db, c, A, C3, now) is False                                           # and one lives in another city
    charkit.complete(h.db, B)
    assert interact.near(h.db, c, A, B, now) is True


# ================================================================================================== concurrency and integrity
def test_two_simultaneous_first_entries_from_two_groups_make_one_city(h):
    h.db.x("DELETE FROM worlds")
    core.ensure_user(h.db, A, "a", "A")
    character.ensure_profile(h.db, A)

    async def go():
        return await asyncio.gather(world.aworld_for(h.db, A, CH1), world.aworld_for(h.db, A, CH2), world.aworld_for(h.db, A, CH3))

    got = asyncio.run(go())
    assert len({row["id"] for row, _ in got}) == 1
    assert h.db.q1("SELECT COUNT(*) c FROM worlds")["c"] == 1                                    # not one per group
    ch = character.get(h.db, A)
    assert ch["birth_world_id"] == ch["current_world_id"] == got[0][0]["id"]
    assert sum(1 for _, created in got if created) == 1


def test_a_character_needs_a_profile_before_a_city(h):
    core.ensure_user(h.db, A, "a", "A")
    with pytest.raises(core.GameError) as e:
        asyncio.run(world.aworld_for(h.db, A, CH1))
    assert e.value.code == "char_none"
    assert h.db.q1("SELECT COUNT(*) c FROM worlds WHERE chat_id NOT IN (?,?)", (CH1, CH2))["c"] == 0


def test_a_missing_city_is_reported_not_replaced(h):
    join(h, A, CH1)
    wid = world_id(h, CH1)
    h.db.x("DELETE FROM worlds WHERE id=?", (wid,))
    with pytest.raises(world.WorldError):
        asyncio.run(world.aworld_for(h.db, A, CH2))
    assert character.get(h.db, A)["current_world_id"] == wid and world.get_world(h.db, CH2) is not None


def test_the_birthplace_never_changes_but_the_current_city_can(h):
    join(h, A, CH1)
    ch = character.get(h.db, A)
    other = world_id(h, CH2)
    with pytest.raises(sqlite3.IntegrityError):
        h.db.x("UPDATE characters SET birth_world_id=? WHERE user_id=?", (other, A))
    with pytest.raises(sqlite3.IntegrityError):
        h.db.x("UPDATE characters SET birth_world_id=NULL, current_world_id=NULL WHERE user_id=?", (A,))
    with pytest.raises(sqlite3.IntegrityError):
        h.db.x("UPDATE characters SET current_world_id=NULL WHERE user_id=?", (A,))
    with pytest.raises(sqlite3.IntegrityError):
        h.db.x("UPDATE characters SET current_world_id=999999 WHERE user_id=?", (A,))
    h.db.x("UPDATE characters SET current_world_id=? WHERE user_id=?", (other, A))               # the current city is the changeable one (future travel)
    now = character.get(h.db, A)
    assert now["birth_world_id"] == ch["birth_world_id"] and now["current_world_id"] == other
    assert character.current_world(h.db, A)["id"] == other and character.in_world(h.db, A, other) and not character.in_world(h.db, A, ch["birth_world_id"])


def test_assign_world_is_once_only_and_validated(h):
    core.ensure_user(h.db, A, "a", "A")
    character.ensure_profile(h.db, A)
    with pytest.raises(core.GameError) as e:
        character.assign_world(h.db, A, 424242)
    assert e.value.code == "char_world" and character.get(h.db, A)["current_world_id"] is None
    first = character.assign_world(h.db, A, world_id(h, CH1))
    again = character.assign_world(h.db, A, world_id(h, CH2))
    assert first["current_world_id"] == again["current_world_id"] == world_id(h, CH1) and again["birth_world_id"] == world_id(h, CH1)
    with pytest.raises(core.GameError):
        character.assign_world(h.db, 404, world_id(h, CH1))


# ================================================================================================== legacy players, end to end
def test_a_legacy_player_keeps_the_old_city_in_every_group_and_finishes_the_set_up(tmp_path):
    from test_v019_character import build_v9, legacy_plan
    path = str(tmp_path / "old.db")

    def plan(d):
        legacy_plan(d)
        for wid, seed in ((1, 7), (2, 5), (3, 8)):                                                # give the stand-in cities their real layouts (and their places)
            lay = layout(seed)
            d.x("UPDATE worlds SET seed=?, gen_version=?, name_fa=?, name_en=?, cols=?, rows=?, layout=? WHERE id=?",
                (lay["seed"], lay["generator"], lay["name"]["fa"], lay["name"]["en"], lay["w"], lay["h"], world.dumps(lay), wid))
            worldplaces.ensure_places(d, d.q1("SELECT * FROM worlds WHERE id=?", (wid,)), lay)
    build_v9(path, plan)
    worldmap.cache_clear()
    worldplaces.cache_clear()
    hh = tb.Harness()
    hh.db.close()
    hh.db = hh.app.db = Database(path)
    legacy = character.get(hh.db, 12)                                                            # player 12: oldest presence is city 2
    assert legacy["origin"] == "legacy" and legacy["current_world_id"] == 2 and legacy["setup_state"] == "first"
    before = [tuple(r) for r in hh.db.qa("SELECT * FROM world_players WHERE user_id=12")]
    out = hh.say(12, "مپ", chat=-100555)                                                         # a group that is nobody's city
    assert not photos(out) and t("fa", "char.need") in " ".join(getattr(c, "text", "") or "" for c in out)
    assert world.get_world(hh.db, -100555) is None                                               # and it built nothing
    hh.say(12, "/start char")
    hh.say(12, "حسین")
    hh.say(12, "کریمی")
    hh.press(12, "cr:g:male:12")
    out = hh.say(12, "مپ", chat=-100555)
    assert photos(out) and world.get_world(hh.db, -100555) is None
    assert hh.db.q1("SELECT world_id FROM map_sessions WHERE user_id=12")["world_id"] == 2
    assert [tuple(r) for r in hh.db.qa("SELECT * FROM world_players WHERE user_id=12")] == before          # the saved position was kept as it was
    assert character.get(hh.db, 12)["citizen_id"] == legacy["citizen_id"]
