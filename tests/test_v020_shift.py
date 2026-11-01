"""v0.20 fix: a worker on a running McDonald's shift stays at work.

Before: the map's «go» button only refused during a Snapp ride, so somebody on an 8-hour shift could walk off to any place in the city. Now `presence.start_trip` (the one place that plans a
local walk) refuses every destination but the shift's own McDonald's while the shift is running by the SERVER clock. A shift that is due (even if its wage is not collected yet), a collected
one, and the other jobs do not hold anybody; travel between cities stays closed for as long as the job is open (that part is v0.20 and is tested with the travel suite)."""
import pytest

import mapkit
import test_bot as tb
from hypemarket import config as C, core, fmt, intercity, mapui, presence as P, work, world, worldmap, worldplaces
from hypemarket.texts import EN, FA, t
from test_v020_travel import layout, snap

CH1, CH2 = -100941, -100942
A, B = 883001, 883002


@pytest.fixture()
def h():
    worldmap.cache_clear()
    worldplaces.cache_clear()
    hh = tb.Harness()
    world.save_world(hh.db, CH1, layout(7))
    world.save_world(hh.db, CH2, layout(5))
    return hh


def city_of(h):
    return worldplaces.load_city(h.db, world.get_world(h.db, CH1))


def at_mcd(h, uid=A):
    """The player has entered the city, walked to its McDonald's and stands there."""
    h.say(uid, "مپ", chat=CH1)
    city = city_of(h)
    out = P.start_trip(h.db, city, uid, city.mcd_id)
    h.advance(out.trip["arrive_at"] - out.trip["started_at"] + 1)
    assert P.stand(h.db, city, uid).row["at_place"] == city.mcd_id
    return city


def shift(h, city, uid=A):
    task, started = work.mcd_start(h.db, city, uid, core.now())
    assert started
    return task


def a_place(h, city):
    """Some public place that is not the McDonald's (found through the city's own map of places)."""
    for ref in ("l1", "l2", "l3", "p1", "p2", "p3"):
        s = city.spot(ref)
        if s is not None and s.id != city.mcd_id:
            return s.id
    raise AssertionError("no other place in this city")


# ---------------------------------------------------------------------------------------------------- the rule, in the game layer
def test_a_running_shift_refuses_every_walk_but_the_one_back_to_the_mcdonalds(h):
    city = at_mcd(h)
    task = shift(h, city)
    far = a_place(h, city)
    before = snap(h.db)
    out = P.start_trip(h.db, city, A, far, core.now())
    assert out.status == "shift" and out.trip is None and out.left == C.MCD_SHIFT_SECONDS
    assert snap(h.db) == before                                                           # nothing was saved: no trip, no moved player
    assert P.stand(h.db, city, A).row["at_place"] == city.mcd_id and P.walking_trip(h.db, city.id, A) is None
    assert P.start_trip(h.db, city, A, city.mcd_id, core.now()).status == "here"          # its own McDonald's is allowed (and the player is already there)


def test_the_hold_lasts_exactly_as_long_as_the_shift(h):
    city = at_mcd(h)
    task = shift(h, city)
    far = a_place(h, city)
    for dt, left in ((0, 28_800), (1, 28_799), (28_799 - 0, 1)):
        now = task["started_at"] + dt
        out = P.start_trip(h.db, city, A, far, now)
        assert out.status == "shift" and out.left == left, (dt, out)
    due = task["due_at"]
    assert work.shift_hold(h.db, A, due - 1) == (city.mcd_id, 1) and work.shift_hold(h.db, A, due) is None            # at the due time the worker is free
    out = P.start_trip(h.db, city, A, far, due)
    assert out.status == "started"                                                       # a finished shift (wage still uncollected) does not hold anybody


def test_a_collected_shift_and_other_jobs_do_not_hold_anybody(h):
    city = at_mcd(h)
    task = shift(h, city)
    h.advance(C.MCD_SHIFT_SECONDS)
    work.mcd_claim(h.db, A, task["id"], core.now())
    assert work.shift_hold(h.db, A) is None
    assert P.start_trip(h.db, city, A, a_place(h, city), core.now()).status == "started"
    h.say(B, "مپ", chat=CH1)
    assert work.shift_hold(h.db, B) is None                                               # nobody else is held by A's shift
    tk = work.snapp_request(h.db, city, B, core.now())                                    # a Snapp ride (its own walks) is unaffected
    assert work.shift_hold(h.db, B) is None and work.snapp_go(h.db, city, B, tk["id"], core.now())


def test_somebody_else_on_the_map_is_free_while_a_worker_is_held(h):
    city = at_mcd(h)
    shift(h, city)
    h.say(B, "مپ", chat=CH1)
    assert P.start_trip(h.db, city, B, a_place(h, city), core.now()).status == "started"


def test_a_worker_who_is_already_away_can_only_walk_back_to_the_mcdonalds(h):
    """Shifts that were started before this fix: the player may already be somewhere else. They are not stranded: the McDonald's is always a legal destination."""
    city = at_mcd(h)
    shift(h, city)
    h.db.x("UPDATE world_players SET at_place=? WHERE world_id=? AND user_id=?", (a_place(h, city), city.id, A))      # (as the bug left them)
    other = next(r for r in ("l1", "l2", "l3", "p1", "p2", "p3", "l4", "p4") if city.spot(r) is not None and city.spot(r).id not in (city.mcd_id, a_place(h, city)))
    assert P.start_trip(h.db, city, A, other, core.now()).status == "shift"
    assert P.start_trip(h.db, city, A, city.mcd_id, core.now()).status == "started"


def test_the_hold_is_the_server_clock_not_a_forged_time(h):
    city = at_mcd(h)
    task = shift(h, city)
    assert P.start_trip(h.db, city, A, a_place(h, city), task["started_at"] + 5).status == "shift"
    assert P.start_trip(h.db, city, A, a_place(h, city)).status == "shift"               # without a time it uses the clock
    assert core.now() < task["due_at"]


# ---------------------------------------------------------------------------------------------------- the map button
def test_the_map_go_button_says_so_and_changes_nothing(h):
    city = at_mcd(h)
    shift(h, city)
    d = mapkit.Driver(h, CH1)
    far = a_place(h, city)
    v = d.open(A, f"مپ {far.upper()}")
    assert any(data.split(":")[2] == "go" for _, data in v.buttons)
    before = snap(h.db)
    for _ in range(3):                                                                    # a repeated press does nothing more than the first
        o = d.press(A, v.find(t("fa", "mp.b.go")), v)
        assert o.alerts == [mapui.plain(t("fa", "mp.on_shift", left=fmt.dur(C.MCD_SHIFT_SECONDS, "fa")))]
        assert not o.edits
    assert "مک‌دونالد" in o.alerts[0] and "شیفت" in o.alerts[0]
    assert snap(h.db)["world_trips"] == before["world_trips"] and snap(h.db)["world_players"] == before["world_players"]
    assert P.walking_trip(h.db, city.id, A) is None


def test_the_map_go_button_works_again_when_the_shift_is_over(h):
    city = at_mcd(h)
    task = shift(h, city)
    d = mapkit.Driver(h, CH1)
    far = a_place(h, city)
    v = d.open(A, f"مپ {far.upper()}")
    h.advance(C.MCD_SHIFT_SECONDS)
    o = d.press(A, v.find(t("fa", "mp.b.go")), v)
    assert not o.alerts and P.walking_trip(h.db, city.id, A) is not None


def test_english_text_and_the_remaining_time_are_shown(h):
    city = at_mcd(h)
    shift(h, city)
    h.advance(3 * 3600 + 25 * 60)
    out = P.start_trip(h.db, city, A, a_place(h, city), core.now())
    assert out.status == "shift" and out.left == C.MCD_SHIFT_SECONDS - (3 * 3600 + 25 * 60)
    assert "{left}" in FA["mp.on_shift"] and "{left}" in EN["mp.on_shift"]
    en = t("en", "mp.on_shift", left=fmt.dur(out.left, "en"))
    assert "shift" in en and fmt.dur(out.left, "en") in en and fmt.dur(out.left, "fa") in t("fa", "mp.on_shift", left=fmt.dur(out.left, "fa"))


# ---------------------------------------------------------------------------------------------------- travel between cities stays closed
def test_travel_between_cities_stays_closed_until_the_wage_is_collected(h):
    city = at_mcd(h)
    task = shift(h, city)
    from hypemarket.core import GameError
    for _ in range(2):
        with pytest.raises(GameError) as e:
            intercity.start(h.db, A, 2)
        assert e.value.code == "ic_job"
    h.advance(C.MCD_SHIFT_SECONDS)
    with pytest.raises(GameError) as e:                                                   # due but not collected: the job is still open
        intercity.start(h.db, A, 2)
    assert e.value.code == "ic_job"
    work.mcd_claim(h.db, A, task["id"], core.now())
    assert intercity.start(h.db, A, 2) is not None
