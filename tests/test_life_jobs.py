"""v0.18: the side-job framework and its three jobs (Snapp, McDonald's, شهرداری), on real generated cities.

What is under test: one open task per player, guarded single payouts, restart safety, rides and walks that need REAL arrival (no teleport), the
8-real-hour shift on the server clock, fixed municipal rewards, stale / foreign / invented buttons, and that the jobs touch life cash only."""
import copy
import json
import sqlite3
import statistics

import pytest

import mapkit
import test_bot as tb
from hypemarket import cash, config as C, core, fmt, presence, work, world, worldplaces
from hypemarket.core import GameError
from hypemarket.db import Database
from hypemarket.texts import t

CH, CH2 = -100606, -100607
A, B = 51001, 51002
T0 = 1_700_000_000


@pytest.fixture(scope="module")
def layout():
    return world.build(7)


@pytest.fixture(scope="module")
def layout2():
    return world.build(42)


class Env:
    """A database with one real city and a clock the test moves."""

    def __init__(self, db, city, now):
        self.db, self.city, self.now = db, city, now

    def adv(self, secs):
        self.now[0] += secs

    def trip(self, uid):
        return presence.walking_trip(self.db, self.city.id, uid)

    def arrive(self, uid):
        """Let the player's trip run to its end by moving the clock to the arrival second; returns where they stand."""
        tr = self.trip(uid)
        if tr is not None:
            self.now[0] = max(self.now[0], tr["arrive_at"])
        return presence.stand(self.db, self.city, uid, self.now[0])

    def go_to(self, uid, ref):
        out = presence.start_trip(self.db, self.city, uid, ref, self.now[0])
        assert out.status in ("started", "here"), out.status
        return self.arrive(uid)

    def bal(self, uid=A):
        return cash.balance(self.db, uid)

    def ledger(self, uid=A, kinds=None):
        rows = [(r["kind"], r["ref"], r["delta"]) for r in self.db.qa("SELECT kind,ref,delta FROM life_ledger WHERE user_id=? ORDER BY id", (uid,))]
        return [r for r in rows if kinds is None or r[0] in kinds]

    def tasks(self, uid=A):
        return [dict(r) for r in self.db.qa("SELECT * FROM work_tasks WHERE user_id=? ORDER BY id", (uid,))]


def make_env(layout, db=None, chat=CH, now=None):
    now = now or [T0]
    core.set_clock(lambda: now[0])
    worldmap_reset()
    db = db or Database(":memory:")
    for u in (A, B):
        core.ensure_user(db, u, f"u{u}", f"User{u}", lang="fa")
    w, _ = world.save_world(db, chat, copy.deepcopy(layout))
    return Env(db, worldplaces.load_city(db, w), now)


def worldmap_reset():
    from hypemarket import worldmap
    worldmap.cache_clear()
    worldplaces.cache_clear()


@pytest.fixture()
def E(layout):
    return make_env(layout)


def ride_to_the_end(E, uid=A):
    """A whole honest ride: request, drive to the passenger, pick them up, drive to the destination, settle. Returns (task row at the start, receipt)."""
    db, city = E.db, E.city
    task = work.snapp_request(db, city, uid, E.now[0])
    work.snapp_go(db, city, uid, task["id"], E.now[0])
    E.arrive(uid)
    work.snapp_pickup(db, city, uid, task["id"], E.now[0])
    E.arrive(uid)
    return task, work.snapp_settle(db, city, uid, task["id"], E.now[0])


# ================================================================================================ the framework
def test_a_player_has_at_most_one_open_task_even_if_the_requests_race(E):
    task = work.snapp_request(E.db, E.city, A, E.now[0])
    with pytest.raises(sqlite3.IntegrityError):                                    # the database itself refuses a second open row
        E.db.x("INSERT INTO work_tasks(user_id,world_id,job,kind,stage,state,data,reward,cost,paid,receipt,created_at) VALUES(?,?,?,?,?,?,?,?,?,?,?,?)",
               (A, E.city.id, "mcd", "", "shift", "open", "{}", 1, 0, 0, "", E.now[0]))
    for fn in (lambda: work.snapp_request(E.db, E.city, A, E.now[0]), lambda: work.mcd_start(E.db, E.city, A, E.now[0]),
               lambda: work.muni_accept(E.db, E.city, A, "sweep", work.muni_round(E.db, A), E.now[0])):
        with pytest.raises(GameError) as e:
            fn()
        assert e.value.code in ("job_busy", "job_far")
    assert [x["id"] for x in E.tasks()] == [task["id"]]
    assert work.snapp_request(E.db, E.city, B, E.now[0])["user_id"] == B                   # another player is not affected


def test_unknown_jobs_and_ids_are_refused_cleanly(E):
    with pytest.raises(GameError) as e:
        work.check_start(E.db, A, "plumber")
    assert e.value.code == "job_gone"
    for bad in (None, "1", 1.5, True, -1, 10 ** 12, [], {}):
        assert work.get_task(E.db, bad, A) is None
    assert work.cancel(E.db, A, 999) is False and work.current(E.db, A) is None


def test_another_players_task_is_invisible_and_untouchable(E):
    task = work.snapp_request(E.db, E.city, A, E.now[0])
    assert work.get_task(E.db, task["id"], B) is None
    for fn in (work.snapp_go, work.snapp_pickup):
        with pytest.raises(GameError) as e:
            fn(E.db, E.city, B, task["id"], E.now[0])
        assert e.value.code == "job_stale"
    assert work.snapp_settle(E.db, E.city, B, task["id"], E.now[0]) is None
    assert work.cancel(E.db, B, task["id"]) is False
    assert work.current(E.db, A)["id"] == task["id"] and work.current(E.db, A)["stage"] == "assigned"


def test_a_task_of_another_city_cannot_be_played_here(E, layout2):
    task = work.snapp_request(E.db, E.city, A, E.now[0])
    w2, _ = world.save_world(E.db, CH2, copy.deepcopy(layout2))
    other = worldplaces.load_city(E.db, w2)
    with pytest.raises(GameError) as e:
        work.snapp_go(E.db, other, A, task["id"], E.now[0])
    assert e.value.code == "job_othercity"
    assert work.sync(E.db, other, A, E.now[0]) is None


def test_history_lists_only_finished_tasks_newest_first(E):
    t1, rc = ride_to_the_end(E)
    E.adv(C.SNAPP_COOLDOWN)
    work.snapp_request(E.db, E.city, A, E.now[0])                                            # an open ride is not history
    h = work.history(E.db, A)
    assert [x["id"] for x in h] == [t1["id"]] and work.receipt_of(h[0])["net"] == rc["net"]


# ================================================================================================ Snapp
@pytest.mark.parametrize("dist,cents", [(0, 22), (1, 22), (33, 22 + 0), (34, 23), (100, 25), (240, 22 + 7), (900, 22 + 27), (1000.4, 22 + 30)])
def test_the_fare_is_base_plus_a_whole_number_of_cents_per_distance(dist, cents):
    assert (C.SNAPP_BASE_FARE_CENTS, C.SNAPP_PER_100_CENTS) == (22, 3)
    assert work.snapp_fare(dist) == cents and isinstance(work.snapp_fare(dist), int)


def test_a_ride_can_never_lose_the_driver_money():
    assert C.SNAPP_BASE_FARE_CENTS > C.SNAPP_FEE_CENTS > 0
    assert work.snapp_fare(-50) == C.SNAPP_BASE_FARE_CENTS > C.SNAPP_FEE_CENTS


def test_tips_are_bounded_server_side_and_fixed_per_ride():
    tips = [work.snapp_tip(7, A, i) for i in range(1, 4001)]
    assert min(tips) == 0 and 1 <= max(tips) <= C.SNAPP_TIP_MAX_CENTS
    assert all(isinstance(x, int) and 0 <= x <= C.SNAPP_TIP_MAX_CENTS for x in tips)
    share = sum(1 for x in tips if x) / len(tips)
    num, den = C.SNAPP_TIP_ODDS
    assert abs(share - num / den) < 0.04                                                    # about 3 rides in 5 tip, as documented
    assert tips == [work.snapp_tip(7, A, i) for i in range(1, 4001)]                         # the same ride always has the same tip: no re-rolling
    assert work.snapp_tip(7, A, 1) == work.snapp_tip(7, A, 1) and {work.snapp_tip(s, A, 3) for s in range(40)} != {0}


def test_tipping_can_be_switched_off_in_the_configuration(monkeypatch):
    monkeypatch.setattr(C, "SNAPP_TIP_MAX_CENTS", 0)
    assert {work.snapp_tip(7, A, i) for i in range(200)} == {0}


def test_a_ride_is_planned_from_real_places_by_road_distance(E):
    st = presence.stand(E.db, E.city, A, E.now[0])
    plan = work.plan_ride(E.city, A, st.pos, 1)
    assert plan is not None
    pick, dest, d1, d2, pax = plan
    assert pick.id != dest.id and 0 <= pax < work.PASSENGERS
    assert E.city.spot(pick.id) is not None and E.city.spot(dest.id) is not None
    assert pick.parcel["id"] != dest.parcel["id"]
    assert abs(d1 - E.city.net.field(st.pos).to(pick.door)) < 1e-6                              # the distance IS the road distance
    assert abs(d2 - E.city.net.field(pick.door.pt).to(dest.door)) < 1e-6
    assert d1 >= work.MIN_PICKUP and d2 >= work.MIN_RIDE
    assert plan[:4] == work.plan_ride(E.city, A, st.pos, 1)[:4]                                  # pure: the same inputs give the same ride
    assert any(work.plan_ride(E.city, A, st.pos, n)[0].id != pick.id for n in range(2, 12))      # and the next rides differ


def test_requesting_a_ride_saves_the_terms_and_charges_nothing_yet(E):
    task = work.snapp_request(E.db, E.city, A, E.now[0])
    d = work.data_of(task)
    assert task["job"] == "snapp" and task["stage"] == "assigned" and task["state"] == "open"
    assert task["cost"] == C.SNAPP_FEE_CENTS and task["reward"] == work.snapp_fare(d["d2"]) and task["paid"] == 0
    assert E.bal() == 10_000 and E.ledger(kinds=("snapp_fare", "snapp_tip", "snapp_fee")) == []   # the rental fee is only charged when the ride ends
    assert task["world_id"] == E.city.id and task["created_at"] == T0 and task["finished_at"] is None
    assert E.city.spot(d["pickup"]) and E.city.spot(d["dest"])


def test_a_whole_ride_pays_fare_plus_tip_minus_the_rental_fee_once(E):
    task, rc = ride_to_the_end(E)
    d = work.data_of(task)
    fare, tip = work.snapp_fare(d["d2"]), work.snapp_tip(E.city.base["seed"], A, task["id"])
    assert rc["fare"] == fare and rc["tip"] == tip and rc["fee"] == C.SNAPP_FEE_CENTS and rc["net"] == fare + tip - C.SNAPP_FEE_CENTS
    assert rc["balance"] == 10_000 + rc["net"] == E.bal()
    got = E.ledger(kinds=("snapp_fare", "snapp_tip", "snapp_fee"))
    assert got == [("snapp_fare", str(task["id"]), fare)] + ([("snapp_tip", str(task["id"]), tip)] if tip else []) + [("snapp_fee", str(task["id"]), -C.SNAPP_FEE_CENTS)]
    row = E.tasks()[0]
    assert row["state"] == "done" and row["stage"] == "done" and row["paid"] == 1 and row["finished_at"] == E.now[0] and work.receipt_of(row)["net"] == rc["net"]
    assert work.current(E.db, A) is None and not work.in_ride(E.db, A)


def test_the_payout_can_never_be_collected_twice(E):
    task, rc = ride_to_the_end(E)
    bal = E.bal()
    for _ in range(4):
        assert work.snapp_settle(E.db, E.city, A, task["id"], E.now[0]) is None
        assert work.sync(E.db, E.city, A, E.now[0]) is None
    E.adv(10_000)
    assert work.snapp_settle(E.db, E.city, A, task["id"], E.now[0]) is None
    assert E.bal() == bal and len(E.ledger(kinds=("snapp_fare",))) == 1


def test_the_ride_pays_when_somebody_simply_looks_later(E):
    task = work.snapp_request(E.db, E.city, A, E.now[0])
    work.snapp_go(E.db, E.city, A, task["id"], E.now[0])
    E.arrive(A)
    work.snapp_pickup(E.db, E.city, A, task["id"], E.now[0])
    tr = E.trip(A)
    E.now[0] = tr["arrive_at"] + 3600                                                          # the player left Telegram for an hour; nothing ran meanwhile
    assert E.bal() == 10_000
    rc = work.sync(E.db, E.city, A, E.now[0])                                                  # the first look applies the arrival and pays, once
    assert rc is not None and E.bal() == 10_000 + rc["net"] and work.sync(E.db, E.city, A, E.now[0]) is None


def test_the_passenger_cannot_be_picked_up_before_arriving(E):
    task = work.snapp_request(E.db, E.city, A, E.now[0])
    d = work.data_of(task)
    with pytest.raises(GameError) as e:
        work.snapp_pickup(E.db, E.city, A, task["id"], E.now[0])                                # assigned, never drove there
    assert e.value.code == "job_stale"
    work.snapp_go(E.db, E.city, A, task["id"], E.now[0])
    tr = E.trip(A)
    assert tr is not None and tr["mode"] == "car" and tr["dest_id"] == d["pickup"] and tr["speed"] == C.car_speed()
    for secs in (0, 1, (tr["arrive_at"] - tr["started_at"]) // 2, tr["arrive_at"] - tr["started_at"] - 1):
        E.now[0] = tr["started_at"] + secs
        with pytest.raises(GameError) as e:
            work.snapp_pickup(E.db, E.city, A, task["id"], E.now[0])
        assert e.value.code == "job_far"
        assert work.snapp_settle(E.db, E.city, A, task["id"], E.now[0]) is None
    assert work.current(E.db, A)["stage"] == "to_pickup" and E.bal() == 10_000
    E.arrive(A)
    assert work.snapp_pickup(E.db, E.city, A, task["id"], E.now[0])["stage"] == "to_dest"


def test_there_is_no_teleporting_the_car_moves_along_the_road(E):
    task = work.snapp_request(E.db, E.city, A, E.now[0])
    d = work.data_of(task)
    start = presence.stand(E.db, E.city, A, E.now[0]).pos
    work.snapp_go(E.db, E.city, A, task["id"], E.now[0])
    tr = E.trip(A)
    span = tr["arrive_at"] - tr["started_at"]
    assert span >= 2 and abs(span - tr["length"] / C.car_speed()) <= 1                           # the time is the road length at the car's speed
    mid = presence.stand(E.db, E.city, A, tr["started_at"] + span // 2)
    assert mid.row["at_place"] is None and mid.pos != start and mid.trip is not None
    end = E.arrive(A)
    assert end.row["at_place"] == d["pickup"] and end.trip is None
    work.snapp_pickup(E.db, E.city, A, task["id"], E.now[0])
    tr2 = E.trip(A)
    assert tr2["dest_id"] == d["dest"] and tr2["mode"] == "car" and (tr2["length"] > 0)
    assert abs((tr2["arrive_at"] - tr2["started_at"]) - tr2["length"] / C.car_speed()) <= 1
    assert E.arrive(A).row["at_place"] == d["dest"]


def test_the_car_is_faster_than_walking_by_the_documented_factor(E):
    out = presence.start_trip(E.db, E.city, B, E.city.mcd_id, E.now[0])
    walk = out.trip
    car = presence.start_trip(E.db, E.city, A, E.city.mcd_id, E.now[0], mode="car").trip
    assert (walk["mode"], car["mode"]) == ("walk", "car") and walk["speed"] == C.walk_speed() and car["speed"] == C.car_speed() == 2 * C.walk_speed()


def test_driving_again_while_already_driving_changes_nothing(E):
    task = work.snapp_request(E.db, E.city, A, E.now[0])
    work.snapp_go(E.db, E.city, A, task["id"], E.now[0])
    before = (dict(E.trip(A)), E.tasks())
    again = work.snapp_go(E.db, E.city, A, task["id"], E.now[0])
    assert again["stage"] == "to_pickup" and (dict(E.trip(A)), E.tasks()) == before and E.db.q1("SELECT COUNT(*) FROM world_trips")[0] == 1


def test_a_ride_needs_enough_cash_for_the_rental_fee(E):
    E.db.x("UPDATE life_accounts SET balance=? WHERE user_id=?", (C.SNAPP_FEE_CENTS - 1, A))
    with pytest.raises(GameError) as e:
        work.snapp_request(E.db, E.city, A, E.now[0])
    assert e.value.code == "cash_low" and e.value.kw == {"need": C.SNAPP_FEE_CENTS, "have": C.SNAPP_FEE_CENTS - 1}
    assert E.tasks() == []
    E.db.x("UPDATE life_accounts SET balance=? WHERE user_id=?", (C.SNAPP_FEE_CENTS, A))
    task, rc = ride_to_the_end(E)
    assert rc["net"] >= 1 and E.bal() == C.SNAPP_FEE_CENTS + rc["net"]                          # a ride with exactly the fee in the pocket works and pays


def test_a_ride_cannot_start_in_the_middle_of_a_walk(E):
    presence.start_trip(E.db, E.city, A, E.city.mcd_id, E.now[0])
    with pytest.raises(GameError) as e:
        work.snapp_request(E.db, E.city, A, E.now[0])
    assert e.value.code == "job_walking" and E.tasks() == []


def test_a_city_with_no_possible_ride_says_so(E, monkeypatch):
    monkeypatch.setattr(work, "plan_ride", lambda *a, **k: None)
    with pytest.raises(GameError) as e:
        work.snapp_request(E.db, E.city, A, E.now[0])
    assert e.value.code == "job_noride" and E.tasks() == []


def test_cancelling_a_ride_costs_nothing_pays_nothing_and_leaves_you_on_the_road(E):
    task = work.snapp_request(E.db, E.city, A, E.now[0])
    work.snapp_go(E.db, E.city, A, task["id"], E.now[0])
    tr = E.trip(A)
    E.now[0] = tr["started_at"] + (tr["arrive_at"] - tr["started_at"]) // 2
    assert work.in_ride(E.db, A)
    assert work.cancel(E.db, A, task["id"], E.now[0]) is True and work.cancel(E.db, A, task["id"], E.now[0]) is False
    assert E.bal() == 10_000 and E.ledger(kinds=("snapp_fare", "snapp_tip", "snapp_fee")) == []
    st = presence.stand(E.db, E.city, A, E.now[0])
    assert st.trip is None and st.row["at_place"] is None and E.trip(A) is None                  # standing on the road where the car was, not teleported anywhere
    assert E.tasks()[0]["state"] == "cancelled" and not work.in_ride(E.db, A)
    with pytest.raises(GameError) as e:                                                         # a cancelled ride cannot be re-rolled for free
        work.snapp_request(E.db, E.city, A, E.now[0])
    assert e.value.code == "job_cooldown" and 0 < e.value.kw["secs"] <= C.SNAPP_COOLDOWN
    E.adv(C.SNAPP_COOLDOWN)
    assert work.snapp_request(E.db, E.city, A, E.now[0])["state"] == "open"


def test_the_cooldown_follows_a_finished_ride(E):
    ride_to_the_end(E)
    with pytest.raises(GameError) as e:
        work.snapp_request(E.db, E.city, A, E.now[0])
    assert e.value.code == "job_cooldown" and e.value.kw["secs"] == C.SNAPP_COOLDOWN
    E.adv(C.SNAPP_COOLDOWN - 1)
    with pytest.raises(GameError):
        work.snapp_request(E.db, E.city, A, E.now[0])
    E.adv(1)
    assert work.cooldown_left(E.db, A, "snapp", E.now[0]) == 0 and work.snapp_request(E.db, E.city, A, E.now[0])


def test_every_ride_of_a_long_session_is_valid_and_pays_the_documented_amount(E):
    total = 0
    for i in range(12):
        E.adv(C.SNAPP_COOLDOWN)
        task, rc = ride_to_the_end(E)
        d = work.data_of(task)
        assert rc["net"] == work.snapp_fare(d["d2"]) + rc["tip"] - C.SNAPP_FEE_CENTS > 0
        assert d["pickup"] != d["dest"] and E.city.spot(d["pickup"]) and E.city.spot(d["dest"])
        total += rc["net"]
    assert E.bal() == 10_000 + total and len({x["id"] for x in E.tasks()}) == 12
    assert [r[1] for r in E.ledger(kinds=("snapp_fare",))] == [str(x["id"]) for x in E.tasks()]        # exactly one fare row per ride, keyed by the ride


def test_an_unreachable_stop_cancels_the_ride_without_charging(E, monkeypatch):
    task = work.snapp_request(E.db, E.city, A, E.now[0])
    real = presence.start_trip
    monkeypatch.setattr(presence, "start_trip", lambda *a, **k: presence.Outcome("unreachable"))
    with pytest.raises(GameError) as e:
        work.snapp_go(E.db, E.city, A, task["id"], E.now[0])
    monkeypatch.setattr(presence, "start_trip", real)
    assert e.value.code == "job_unreachable" and E.tasks()[0]["state"] == "cancelled" and E.bal() == 10_000


def test_a_failure_while_paying_rolls_the_whole_ride_back(E, monkeypatch):
    task = work.snapp_request(E.db, E.city, A, E.now[0])
    work.snapp_go(E.db, E.city, A, task["id"], E.now[0])
    E.arrive(A)
    work.snapp_pickup(E.db, E.city, A, task["id"], E.now[0])
    E.arrive(A)
    real = cash.apply

    def flaky(db, uid, delta, kind, *a, **k):
        if kind == "snapp_fee":
            raise RuntimeError("boom")
        return real(db, uid, delta, kind, *a, **k)
    monkeypatch.setattr(cash, "apply", flaky)
    with pytest.raises(RuntimeError):
        work.snapp_settle(E.db, E.city, A, task["id"], E.now[0])
    monkeypatch.setattr(cash, "apply", real)
    assert E.bal() == 10_000 and E.ledger(kinds=("snapp_fare", "snapp_tip", "snapp_fee")) == []       # no half payout: not even the fare
    assert work.current(E.db, A)["stage"] == "to_dest"
    rc = work.snapp_settle(E.db, E.city, A, task["id"], E.now[0])                                   # and the ride is still there to be paid properly
    assert rc and E.bal() == 10_000 + rc["net"]


def test_a_stale_button_of_an_old_ride_does_nothing(E):
    t1, _ = ride_to_the_end(E)
    for fn in (work.snapp_go, work.snapp_pickup):
        with pytest.raises(GameError) as e:
            fn(E.db, E.city, A, t1["id"], E.now[0])
        assert e.value.code == "job_stale"
    E.adv(C.SNAPP_COOLDOWN)
    t2 = work.snapp_request(E.db, E.city, A, E.now[0])
    with pytest.raises(GameError):
        work.snapp_go(E.db, E.city, A, t1["id"], E.now[0])                                          # the old ride's id cannot drive the new ride
    assert work.current(E.db, A)["id"] == t2["id"] and work.current(E.db, A)["stage"] == "assigned"


# ================================================================================================ McDonald's
def test_every_city_has_one_mcdonalds_that_can_be_walked_to(E):
    mid = E.city.mcd_id
    assert mid is not None and E.city.spot(mid).landmark["kind"] == "mcdonalds"
    assert presence.start_trip(E.db, E.city, A, mid, E.now[0]).status == "started"


def test_applying_requires_standing_at_the_mcdonalds(E):
    with pytest.raises(GameError) as e:
        work.mcd_start(E.db, E.city, A, E.now[0])
    assert e.value.code == "job_far" and E.tasks() == []
    presence.start_trip(E.db, E.city, A, E.city.mcd_id, E.now[0])
    with pytest.raises(GameError) as e:                                                               # still walking
        work.mcd_start(E.db, E.city, A, E.now[0])
    assert e.value.code == "job_far"
    E.arrive(A)
    task, started = work.mcd_start(E.db, E.city, A, E.now[0])
    assert started and task["job"] == "mcd" and task["state"] == "open"


def test_the_shift_is_8_real_hours_and_saves_its_start_and_end(E):
    E.go_to(A, E.city.mcd_id)
    now = E.now[0]
    task, started = work.mcd_start(E.db, E.city, A, now)
    assert C.MCD_SHIFT_SECONDS == 8 * 3600 == 28_800
    assert (task["started_at"], task["due_at"]) == (now, now + 28_800) and task["reward"] == C.MCD_WAGE_CENTS == 1200 and task["paid"] == 0
    assert work.shift_state(task, now) == "working" and work.shift_state(task, now + 28_799) == "working" and work.shift_state(task, now + 28_800) == "ready"
    assert work.data_of(task) == {"place": E.city.mcd_id}


def test_a_second_press_never_creates_a_second_shift_or_moves_the_times(E):
    E.go_to(A, E.city.mcd_id)
    t1, s1 = work.mcd_start(E.db, E.city, A, E.now[0])
    E.adv(600)
    t2, s2 = work.mcd_start(E.db, E.city, A, E.now[0])
    assert (s1, s2) == (True, False) and t1["id"] == t2["id"] and (t2["started_at"], t2["due_at"]) == (t1["started_at"], t1["due_at"])
    assert len(E.tasks()) == 1


def test_the_wage_is_collected_only_when_the_server_clock_says_so_and_once(E):
    E.go_to(A, E.city.mcd_id)
    task, _ = work.mcd_start(E.db, E.city, A, E.now[0])
    for early in (0, 1, 3600, 28_799):
        with pytest.raises(GameError) as e:
            work.mcd_claim(E.db, A, task["id"], T0 + early + (E.now[0] - T0))
        assert e.value.code == "job_notready" and e.value.kw["secs"] == 28_800 - early
    assert E.bal() == 10_000 and E.ledger(kinds=("mcd_wage",)) == []
    rc = work.mcd_claim(E.db, A, task["id"], E.now[0] + 28_800)
    assert rc["wage"] == 1200 and rc["balance"] == 11_200 and E.bal() == 11_200
    assert E.ledger(kinds=("mcd_wage",)) == [("mcd_wage", str(task["id"]), 1200)]
    for _ in range(3):
        with pytest.raises(GameError) as e:
            work.mcd_claim(E.db, A, task["id"], E.now[0] + 99_999)
        assert e.value.code == "job_claimed"
    assert E.bal() == 11_200 and work.shift_state(work.get_task(E.db, task["id"], A), E.now[0]) == "collected"


def test_the_player_clock_or_a_forged_time_cannot_shorten_a_shift(E):
    E.go_to(A, E.city.mcd_id)
    task, _ = work.mcd_start(E.db, E.city, A, E.now[0])
    E.adv(3600)                                                                                         # the SERVER clock is what the handlers use
    with pytest.raises(GameError) as e:
        work.mcd_claim(E.db, A, task["id"])
    assert e.value.code == "job_notready" and e.value.kw["secs"] == 28_800 - 3600


def test_a_shift_cannot_be_cancelled_and_blocks_every_other_job(E):
    E.go_to(A, E.city.mcd_id)
    task, _ = work.mcd_start(E.db, E.city, A, E.now[0])
    with pytest.raises(GameError) as e:
        work.cancel(E.db, A, task["id"], E.now[0])
    assert e.value.code == "job_nocancel" and work.current(E.db, A)["id"] == task["id"]
    for fn in (lambda: work.snapp_request(E.db, E.city, A, E.now[0]), lambda: work.muni_accept(E.db, E.city, A, "sweep", work.muni_round(E.db, A), E.now[0])):
        with pytest.raises(GameError) as e:
            fn()
        assert e.value.code == "job_busy" and e.value.kw == {"job": "mcd"}
    E.adv(28_800)
    with pytest.raises(GameError) as e:                                                                  # a finished but uncollected shift still occupies the slot
        work.snapp_request(E.db, E.city, A, E.now[0])
    assert e.value.code == "job_busy"
    work.mcd_claim(E.db, A, task["id"], E.now[0])
    assert work.snapp_request(E.db, E.city, A, E.now[0])


def test_a_new_shift_can_follow_a_collected_one(E):
    E.go_to(A, E.city.mcd_id)
    t1, _ = work.mcd_start(E.db, E.city, A, E.now[0])
    E.adv(28_800)
    work.mcd_claim(E.db, A, t1["id"], E.now[0])
    t2, started = work.mcd_start(E.db, E.city, A, E.now[0])
    assert started and t2["id"] != t1["id"] and t2["due_at"] == E.now[0] + 28_800 and E.bal() == 11_200


def test_nobody_else_can_collect_my_wage_or_a_task_that_is_not_a_shift(E):
    E.go_to(A, E.city.mcd_id)
    task, _ = work.mcd_start(E.db, E.city, A, E.now[0])
    E.adv(28_800)
    for bad in (task["id"] + 1, 0, -1, None, "1"):
        with pytest.raises(GameError) as e:
            work.mcd_claim(E.db, A, bad, E.now[0])
        assert e.value.code == "job_stale"
    with pytest.raises(GameError) as e:
        work.mcd_claim(E.db, B, task["id"], E.now[0])
    assert e.value.code == "job_stale" and cash.balance(E.db, B) == 10_000
    ride = work.snapp_request(E.db, E.city, B, E.now[0])
    with pytest.raises(GameError) as e:
        work.mcd_claim(E.db, B, ride["id"], E.now[0] + 10 ** 6)
    assert e.value.code == "job_stale"


def test_a_city_without_a_mcdonalds_cannot_start_a_shift(E, monkeypatch):
    E.city.rows.pop(worldplaces.MCDONALDS)
    assert E.city.mcd_id is None
    with pytest.raises(GameError) as e:
        work.mcd_start(E.db, E.city, A, E.now[0])
    assert e.value.code == "job_nomcd"


def test_the_shift_survives_a_restart_and_pays_after_it(layout, tmp_path):
    path = str(tmp_path / "game.db")
    E1 = make_env(layout, Database(path))
    E1.go_to(A, E1.city.mcd_id)
    task, _ = work.mcd_start(E1.db, E1.city, A, E1.now[0])
    saved = (task["started_at"], task["due_at"])
    E1.db.close()
    now = [saved[0] + 30_000]
    core.set_clock(lambda: now[0])
    worldmap_reset()
    db2 = Database(path)                                                                                # "the bot restarted a few hours later"
    again = work.current(db2, A)
    assert again["id"] == task["id"] and (again["started_at"], again["due_at"]) == saved and work.shift_state(again, now[0]) == "ready"
    rc = work.mcd_claim(db2, A, task["id"], now[0])
    assert rc["wage"] == 1200 and cash.balance(db2, A) == 11_200
    db2.close()
    db3 = Database(path)
    with pytest.raises(GameError):
        work.mcd_claim(db3, A, task["id"], now[0])                                                      # a second restart cannot pay it again
    assert cash.balance(db3, A) == 11_200 and len(db3.qa("SELECT * FROM life_ledger WHERE kind='mcd_wage'")) == 1


def test_a_finished_shift_is_announced_once_and_only_once(E):
    E.go_to(A, E.city.mcd_id)
    work.mcd_start(E.db, E.city, A, E.now[0])
    assert work.notify_ready(E.db, E.now[0] + 28_799) == 0
    E.db.outbox.clear()
    assert work.notify_ready(E.db, E.now[0] + 28_800) == 1
    assert [(u, k, kw) for u, k, kw in E.db.outbox] == [(A, "shift_ready", {"cents": 1200})]
    assert work.notify_ready(E.db, E.now[0] + 99_999) == 0 and len(E.db.outbox) == 1                    # the flag lives in the row: repeated sweeps (or restarts) say nothing more
    from hypemarket import views
    assert "$۱۲٫۰۰" in views.dm_text("fa", "shift_ready", {"cents": 1200})


# ================================================================================================ municipal work
def test_municipal_targets_follow_the_kind_of_work(E):
    sweep, waste, green = (work.muni_candidates(E.city, k) for k in work.MUNI_KINDS)
    assert sweep and green and waste
    assert all(s.landmark is None and s.parcel["use"] in ("building", "vacant") for s in sweep)
    assert all(s.landmark is None and s.parcel["use"] == "green" for s in green)
    assert all(s.landmark is not None for s in waste)
    assert work.muni_candidates(E.city, "nonsense") == []


def test_offers_are_real_reachable_fixed_priced_and_stable(E):
    st = presence.stand(E.db, E.city, A, E.now[0])
    offers = work.muni_offers(E.db, E.city, A, st.pos, E.now[0])
    assert offers and [o.kind for o in offers] == [k for k in work.MUNI_KINDS if k in {o.kind for o in offers}]
    for o in offers:
        assert o.reward == C.MUNI_TASKS[o.kind] and o.round == 0 and E.city.spot(o.spot.id) is not None
        assert abs(o.dist - E.city.net.field(st.pos).to(o.spot.door)) < 1e-6 and o.dist >= work.MIN_PICKUP
        assert o.spot.id in {s.id for s in work.muni_candidates(E.city, o.kind)}
    assert [(o.kind, o.spot.id, o.reward) for o in offers] == [(o.kind, o.spot.id, o.reward) for o in work.muni_offers(E.db, E.city, A, st.pos, E.now[0] + 500)]


def test_accepting_saves_the_fixed_reward_and_nothing_is_paid_yet(E):
    o = work.muni_offers(E.db, E.city, A, presence.stand(E.db, E.city, A, E.now[0]).pos)[0]
    task = work.muni_accept(E.db, E.city, A, o.kind, 0, E.now[0])
    assert (task["job"], task["kind"], task["stage"], task["reward"], task["paid"]) == ("muni", o.kind, "travel", C.MUNI_TASKS[o.kind], 0)
    assert work.data_of(task)["target"] == o.spot.id and E.bal() == 10_000


def test_the_task_is_done_only_after_really_arriving_and_pays_once(E):
    st = presence.stand(E.db, E.city, A, E.now[0])
    o = work.muni_offers(E.db, E.city, A, st.pos)[0]
    task = work.muni_accept(E.db, E.city, A, o.kind, 0, E.now[0])
    with pytest.raises(GameError) as e:
        work.muni_complete(E.db, E.city, A, task["id"], E.now[0])                                       # claiming "done" from where you stand is refused
    assert e.value.code == "job_far"
    assert work.muni_go(E.db, E.city, A, task["id"], E.now[0]).status == "started"
    tr = E.trip(A)
    assert tr["mode"] == "walk" and tr["dest_id"] == o.spot.id
    E.now[0] = tr["arrive_at"] - 1
    with pytest.raises(GameError) as e:
        work.muni_complete(E.db, E.city, A, task["id"], E.now[0])
    assert e.value.code == "job_far" and E.bal() == 10_000
    E.arrive(A)
    rc = work.muni_complete(E.db, E.city, A, task["id"], E.now[0])
    assert rc["reward"] == C.MUNI_TASKS[o.kind] and rc["balance"] == E.bal() == 10_000 + C.MUNI_TASKS[o.kind]
    assert E.ledger(kinds=("muni_" + o.kind,)) == [("muni_" + o.kind, str(task["id"]), C.MUNI_TASKS[o.kind])]
    for _ in range(3):
        with pytest.raises(GameError) as e:
            work.muni_complete(E.db, E.city, A, task["id"], E.now[0])
        assert e.value.code == "job_stale"
    assert E.bal() == 10_000 + C.MUNI_TASKS[o.kind] and E.tasks()[0]["state"] == "done"


def test_walking_away_from_the_site_takes_the_completion_away(E):
    o = work.muni_offers(E.db, E.city, A, presence.stand(E.db, E.city, A, E.now[0]).pos)[0]
    task = work.muni_accept(E.db, E.city, A, o.kind, 0, E.now[0])
    work.muni_go(E.db, E.city, A, task["id"], E.now[0])
    E.arrive(A)
    other = next(s.id for s in E.city.spots("landmark") if s.id != o.spot.id)
    presence.start_trip(E.db, E.city, A, other, E.now[0])
    with pytest.raises(GameError) as e:
        work.muni_complete(E.db, E.city, A, task["id"], E.now[0])
    assert e.value.code == "job_far"
    E.arrive(A)
    with pytest.raises(GameError) as e:
        work.muni_complete(E.db, E.city, A, task["id"], E.now[0])
    assert e.value.code == "job_far" and E.bal() == 10_000


def test_one_task_at_a_time_and_old_offer_buttons_go_stale(E):
    pos = presence.stand(E.db, E.city, A, E.now[0]).pos
    offers = work.muni_offers(E.db, E.city, A, pos)
    task = work.muni_accept(E.db, E.city, A, offers[0].kind, 0, E.now[0])
    with pytest.raises(GameError) as e:
        work.muni_accept(E.db, E.city, A, offers[-1].kind, 0, E.now[0])
    assert e.value.code == "job_busy"
    work.cancel(E.db, A, task["id"], E.now[0])
    E.adv(C.MUNI_COOLDOWN)
    with pytest.raises(GameError) as e:                                                                  # the screen the player was looking at (round 0) is out of date now
        work.muni_accept(E.db, E.city, A, offers[0].kind, 0, E.now[0])
    assert e.value.code == "job_stale"
    assert work.muni_round(E.db, A) == 1
    assert work.muni_accept(E.db, E.city, A, offers[0].kind, 1, E.now[0])["state"] == "open"


@pytest.mark.parametrize("kind,rnd", [("nonsense", 0), ("", 0), ("sweep", None), ("sweep", "0"), ("sweep", -1), ("sweep", 1.5), ("sweep", True), ("sweep", 7), (None, 0)])
def test_forged_offer_buttons_are_refused(E, kind, rnd):
    with pytest.raises(GameError) as e:
        work.muni_accept(E.db, E.city, A, kind, rnd, E.now[0])
    assert e.value.code == "job_stale" and E.tasks() == [] and E.bal() == 10_000


def test_the_cooldown_after_a_municipal_task(E):
    o = work.muni_offers(E.db, E.city, A, presence.stand(E.db, E.city, A, E.now[0]).pos)[0]
    task = work.muni_accept(E.db, E.city, A, o.kind, 0, E.now[0])
    work.muni_go(E.db, E.city, A, task["id"], E.now[0])
    E.arrive(A)
    work.muni_complete(E.db, E.city, A, task["id"], E.now[0])
    with pytest.raises(GameError) as e:
        work.muni_accept(E.db, E.city, A, o.kind, 1, E.now[0])
    assert e.value.code == "job_cooldown" and e.value.kw["secs"] == C.MUNI_COOLDOWN
    E.adv(C.MUNI_COOLDOWN)
    st = presence.stand(E.db, E.city, A, E.now[0])
    assert work.muni_offers(E.db, E.city, A, st.pos) and work.muni_accept(E.db, E.city, A, work.muni_offers(E.db, E.city, A, st.pos)[0].kind, 1, E.now[0])


def test_municipal_work_repeats_with_new_offers_each_time(E):
    seen, earned = [], 0
    for n in range(6):
        E.adv(C.MUNI_COOLDOWN)
        offers = work.muni_offers(E.db, E.city, A, presence.stand(E.db, E.city, A, E.now[0]).pos)
        o = offers[n % len(offers)]
        task = work.muni_accept(E.db, E.city, A, o.kind, n, E.now[0])
        work.muni_go(E.db, E.city, A, task["id"], E.now[0])
        E.arrive(A)
        earned += work.muni_complete(E.db, E.city, A, task["id"], E.now[0])["reward"]
        seen.append((o.kind, o.spot.id))
    assert E.bal() == 10_000 + earned and len(set(seen)) >= 4
    assert {k for k, _ in seen} >= {"sweep", "waste"} or len({k for k, _ in seen}) >= 2


def test_unreachable_municipal_site_cancels_the_task_for_free(E, monkeypatch):
    o = work.muni_offers(E.db, E.city, A, presence.stand(E.db, E.city, A, E.now[0]).pos)[0]
    task = work.muni_accept(E.db, E.city, A, o.kind, 0, E.now[0])
    monkeypatch.setattr(presence, "start_trip", lambda *a, **k: presence.Outcome("unreachable"))
    with pytest.raises(GameError) as e:
        work.muni_go(E.db, E.city, A, task["id"], E.now[0])
    assert e.value.code == "job_unreachable" and E.tasks()[0]["state"] == "cancelled" and E.bal() == 10_000


# ================================================================================================ all jobs together
def test_the_jobs_only_ever_touch_life_cash(E):
    snap = lambda: ([tuple(r) for r in E.db.qa("SELECT * FROM users ORDER BY id")],
                    {n: [tuple(r) for r in E.db.qa(f"SELECT * FROM {n}")] for n in ("stocks", "holdings", "perks", "quests", "achievements", "history", "news")},
                    {r["key"]: r["value"] for r in E.db.qa("SELECT key,value FROM kv WHERE key IN ('minted','burned')")})
    before = snap()
    ride_to_the_end(E)
    E.adv(C.SNAPP_COOLDOWN)
    E.go_to(A, E.city.mcd_id)
    task, _ = work.mcd_start(E.db, E.city, A, E.now[0])
    E.adv(28_800)
    work.mcd_claim(E.db, A, task["id"], E.now[0])
    o = work.muni_offers(E.db, E.city, A, presence.stand(E.db, E.city, A, E.now[0]).pos)[0]
    mt = work.muni_accept(E.db, E.city, A, o.kind, 0, E.now[0])
    work.muni_go(E.db, E.city, A, mt["id"], E.now[0])
    E.arrive(A)
    work.muni_complete(E.db, E.city, A, mt["id"], E.now[0])
    assert snap() == before                                                                              # no stock cash, level, XP, skill, perk, supply or portfolio changed
    kinds = {r[0] for r in E.ledger()}
    assert kinds <= {"grant", "snapp_fare", "snapp_tip", "snapp_fee", "mcd_wage", "muni_sweep", "muni_waste", "muni_green"}
    assert sum(r[2] for r in E.ledger()) == E.bal()


def test_the_ledger_kinds_of_the_jobs_all_have_a_name_in_the_wallet():
    from hypemarket import lifeui
    for k in ("snapp_fare", "snapp_tip", "snapp_fee", "mcd_wage", "muni_sweep", "muni_waste", "muni_green", "fx", "grant"):
        assert t("fa", f"cash.k.{k}") != f"cash.k.{k}" and t("en", f"cash.k.{k}") != f"cash.k.{k}" and lifeui._kind_label("fa", k) == t("fa", f"cash.k.{k}")


def test_the_rides_and_tasks_of_two_players_do_not_interfere(E):
    ta = work.snapp_request(E.db, E.city, A, E.now[0])
    tb_ = work.snapp_request(E.db, E.city, B, E.now[0])
    for uid, task in ((A, ta), (B, tb_)):
        work.snapp_go(E.db, E.city, uid, task["id"], E.now[0])
    E.arrive(A)
    E.arrive(B)
    for uid, task in ((A, ta), (B, tb_)):
        work.snapp_pickup(E.db, E.city, uid, task["id"], E.now[0])
    E.arrive(A)
    E.arrive(B)
    ra = work.snapp_settle(E.db, E.city, A, ta["id"], E.now[0])
    rb = work.snapp_settle(E.db, E.city, B, tb_["id"], E.now[0])
    assert ra and rb and E.bal(A) == 10_000 + ra["net"] and E.bal(B) == 10_000 + rb["net"]


def test_the_ride_survives_a_restart_in_every_stage(layout, tmp_path):
    path = str(tmp_path / "ride.db")
    E1 = make_env(layout, Database(path))
    task = work.snapp_request(E1.db, E1.city, A, E1.now[0])
    work.snapp_go(E1.db, E1.city, A, task["id"], E1.now[0])
    E1.db.close()                                                                                      # restart while driving to the passenger
    now = [T0 + 10]
    core.set_clock(lambda: now[0])
    worldmap_reset()
    db2 = Database(path)
    city2 = worldplaces.load_city(db2, world.get_world(db2, CH))
    tr = presence.walking_trip(db2, city2.id, A)
    assert tr is not None and work.current(db2, A)["stage"] == "to_pickup"
    now[0] = tr["arrive_at"]
    work.snapp_pickup(db2, city2, A, task["id"], now[0])
    db2.close()                                                                                        # restart while driving to the destination
    db3 = Database(path)
    city3 = worldplaces.load_city(db3, world.get_world(db3, CH))
    tr = presence.walking_trip(db3, city3.id, A)
    now[0] = tr["arrive_at"] + 500
    rc = work.sync(db3, city3, A, now[0])
    assert rc and cash.balance(db3, A) == 10_000 + rc["net"]
    db3.close()
    db4 = Database(path)
    city4 = worldplaces.load_city(db4, world.get_world(db4, CH))
    assert work.sync(db4, city4, A, now[0] + 10) is None and cash.balance(db4, A) == 10_000 + rc["net"]


# ================================================================================================ the economy stays modest
def _hourly(seeds, overhead=25.0):
    """Cents per ACTIVE hour of each job on the generated cities (the same method the configuration comment documents): Snapp = average net of a
    ride / (average road time at car speed + `overhead` seconds of reading and tapping), municipal = reward / (walk time + 0.7 x overhead)."""
    snapp, muni = [], {k: [] for k in work.MUNI_KINDS}
    tip_avg = (C.SNAPP_TIP_ODDS[0] / C.SNAPP_TIP_ODDS[1]) * (1 + C.SNAPP_TIP_MAX_CENTS) / 2
    for s in seeds:
        core.set_clock(lambda: T0)
        db = Database(":memory:")
        core.ensure_user(db, 7, "a", "A", lang="fa")
        w, _ = world.save_world(db, -2000 - s, world.build(s))
        city = worldplaces.load_city(db, w)
        pos0 = presence.stand(db, city, 7).pos
        pos, rides = pos0, []
        for serial in range(1, 17):
            plan = work.plan_ride(city, 7, pos, serial)
            if plan is None:
                continue
            _p, dest, d1, d2, _x = plan
            rides.append(((d1 + d2) / C.car_speed(), work.snapp_fare(d2) - C.SNAPP_FEE_CENTS + tip_avg))
            pos = dest.door.pt
        assert rides, f"seed {s}: no ride could be planned"
        snapp.append(statistics.mean(n for _, n in rides) / (statistics.mean(x for x, _ in rides) + overhead) * 3600)
        for kind in work.MUNI_KINDS:
            cand = work._ring(city, pos0, work.muni_candidates(city, kind), *C.MUNI_RANGE, work.MIN_PICKUP)
            if cand:
                muni[kind].append(C.MUNI_TASKS[kind] / (statistics.mean(d for d, _ in cand) / C.walk_speed() + 0.7 * overhead) * 3600)
    return statistics.mean(snapp), {k: statistics.mean(v) for k, v in muni.items() if v}


def test_job_earnings_stay_modest_and_ordered():
    """The calibration promised in config.py: per ACTIVE hour Snapp is about $10, municipal work about $7-9, McDonald's $1.50 (passive); all
    far below what the stock game itself pays (1,000 🪙 a day of play = $20 at the fixed rate), and Snapp never runs away from municipal work."""
    snapp_h, muni_h = _hourly([1, 2, 3, 7])
    mcd_h = C.MCD_WAGE_CENTS / (C.MCD_SHIFT_SECONDS / 3600)
    assert 800 <= snapp_h <= 1300, snapp_h
    assert set(muni_h) == set(work.MUNI_KINDS) and all(500 <= v <= 1100 for v in muni_h.values()), muni_h
    assert mcd_h == 150
    assert mcd_h < min(muni_h.values()) < snapp_h                                                     # passive < walking errands < driving
    assert snapp_h <= 1.8 * statistics.mean(muni_h.values())
    day_of_stock_play = 2000                                                                          # cents: 1,000 🪙 at the fixed rate
    assert snapp_h <= 0.65 * day_of_stock_play                                                         # an active hour of the best job is worth well under a day of stock income
    assert 3 * C.MCD_WAGE_CENTS <= 2 * day_of_stock_play                                              # even three shifts back to back (24 h) stay under twice a stock day
    assert C.MCD_WAGE_CENTS / (C.MCD_SHIFT_SECONDS / 3600) <= 200
    assert all(1 <= v <= 50 for v in C.MUNI_TASKS.values()) and C.SNAPP_TIP_MAX_CENTS <= C.SNAPP_BASE_FARE_CENTS
