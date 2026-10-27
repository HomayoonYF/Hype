"""v0.18.2: explicit speeds and one scale. Walking is 20 km/h and the rental Pride 40 km/h (10 and 20 map units per second, 1800 map units = 1 km), the
screens say so in km/h and show distances in metres / kilometres, and distance, duration and ETA all come from the same saved trip. Trips saved
before the change keep their own speed."""
import math
import re
from pathlib import Path

import pytest

import mapkit
import test_bot as tb
from hypemarket import config as C, core, fmt, presence as P, world, worldmap, worldplaces
from hypemarket.db import Database
from hypemarket.texts import EN, FA, t

CH = -100606
A = 777001
ROOT = Path(__file__).resolve().parent.parent


@pytest.fixture()
def h():
    worldmap.cache_clear()
    worldplaces.cache_clear()
    hh = tb.Harness()
    world.save_world(hh.db, CH, world.build(7))
    return hh


@pytest.fixture()
def d(h):
    return mapkit.Driver(h, CH)


def city_db(db=None, seed=7):
    db = db or Database(":memory:")
    world.save_world(db, CH, world.build(seed))
    core.ensure_user(db, A, "a", "A", lang="fa")
    return db, worldplaces.load_city(db, world.get_world(db, CH))


# ============================================================================================== the scale
def test_walking_is_20_kmh_and_the_pride_is_40_kmh():
    assert C.UNITS_PER_KM == 1800
    assert C.walk_speed() == 10 and C.car_speed() == 20 == C.CAR_SPEED_DEFAULT
    assert C.to_kmh(C.walk_speed()) == pytest.approx(20) and C.to_kmh(C.car_speed()) == pytest.approx(40)
    assert fmt.kmh(C.walk_speed(), "en").strip("‎") == "20 km/h" and fmt.kmh(C.car_speed(), "en").strip("‎") == "40 km/h"
    assert fmt.kmh(C.walk_speed(), "fa").strip("‎") == "۲۰ km/h" and fmt.kmh(C.car_speed(), "fa").strip("‎") == "۴۰ km/h"
    assert C.kmh_to_units(20) == pytest.approx(10) and C.kmh_to_units(40) == pytest.approx(20)
    assert C.to_metres(900) == pytest.approx(500)


def test_distances_are_metres_below_a_kilometre_and_kilometres_above():
    assert fmt.dist(900, "en") == "500 m" and fmt.dist(1800, "en") == "1 km" and fmt.dist(2700, "en") == "1.5 km" and fmt.dist(0, "en") == "0 m"
    assert fmt.dist(900, "fa") == "۵۰۰ متر" and fmt.dist(1800, "fa") == "۱ کیلومتر" and fmt.dist(2700, "fa") == "۱٫۵ کیلومتر"
    assert fmt.dist(1780, "en") == "989 m" and fmt.dist(1795, "en") == "1 km"                             # from 995 m up it reads as the kilometre, never «1000 m»
    assert "واحد" not in fmt.dist(500, "fa") and "unit" not in fmt.dist(500, "en")


def test_areas_use_the_same_scale_in_square_metres():
    assert C.to_square_metres(3240) == pytest.approx(1000)
    assert fmt.area(3240, "en") == "1,000 m²" and fmt.area(3240, "fa") == fmt.loc("1,000", "fa") + " متر مربع"
    assert fmt.area(0, "en") == "0 m²" and "واحد" not in fmt.area(100, "fa")


def test_the_speed_can_still_be_changed_in_env_and_the_screens_follow(monkeypatch):
    monkeypatch.setenv("WALK_SPEED", "15")
    monkeypatch.setenv("CAR_SPEED", "30")
    assert fmt.kmh(C.walk_speed(), "en").strip("‎") == "30 km/h" and fmt.kmh(C.car_speed(), "en").strip("‎") == "60 km/h"


# ============================================================================================== the screens say it
def test_the_map_object_screen_states_distance_speed_and_a_matching_eta(h, d):
    v = d.open(A)
    o = d.press(A, f"mp:{v.sid}:o:casino", v)
    c = worldplaces.load_city(h.db, world.get_world(h.db, CH))
    st = P.stand(h.db, c, A)
    length = c.net.route(st.pos, c.spot(c.casino_id).door.pt).length
    line = [x for x in o.caption.split("\n") if "km/h" in x][0]
    assert fmt.dist(length, "fa") in line and fmt.kmh(C.walk_speed(), "fa") in line
    assert fmt.dur(max(1, math.ceil(length / C.walk_speed() - 1e-9)), "fa") in line                       # the ETA is length ÷ the stated speed
    out = d.go(A, o, t("fa", "mp.b.go"))
    tr = h.db.q1("SELECT * FROM world_trips WHERE user_id=?", (A,))
    assert tr["arrive_at"] - tr["started_at"] == max(1, math.ceil(tr["length"] / tr["speed"] - 1e-9))
    assert fmt.kmh(tr["speed"], "fa") in out.caption and fmt.dist(tr["length"], "fa") not in ("", None)


def test_a_car_trip_takes_half_the_time_of_a_walk_over_the_same_road():
    db, c = city_db()
    w = P.start_trip(db, c, A, "l5", now=1_700_000_000).trip
    P.cancel_trip(db, c.id, A, now=1_700_000_000)
    db2, c2 = city_db()
    k = P.start_trip(db2, c2, A, "l5", now=1_700_000_000, mode="car").trip
    assert (w["speed"], k["speed"]) == (10.0, 20.0) and w["length"] == pytest.approx(k["length"])
    assert w["arrive_at"] - w["started_at"] == math.ceil(w["length"] / 10 - 1e-9)
    assert k["arrive_at"] - k["started_at"] == math.ceil(k["length"] / 20 - 1e-9)
    assert (w["arrive_at"] - w["started_at"]) >= 2 * (k["arrive_at"] - k["started_at"]) - 2             # (the car's time is half, give or take the rounding up)
    assert k["mode"] == "car" and w["mode"] == "walk"


def test_progress_and_remaining_distance_follow_the_saved_speed():
    db, c = city_db()
    tr = P.start_trip(db, c, A, "l5", now=1_700_000_000, mode="car").trip
    total = tr["arrive_at"] - tr["started_at"]
    half = 1_700_000_000 + total // 2
    st = P.stand(db, c, A, now=half)
    assert st.progress == pytest.approx((total // 2) * 20.0 / tr["length"], abs=0.02)
    left = tr["length"] * (1 - st.progress)
    assert left / 20.0 == pytest.approx(tr["arrive_at"] - half, abs=2)                                   # remaining distance ÷ 40 km/h = the time left


def test_a_trip_saved_before_keeps_its_own_speed_when_the_settings_change(monkeypatch):
    db, c = city_db()
    tr = P.start_trip(db, c, A, "l5", now=1_700_000_000).trip                                           # saved at 10 units/s
    monkeypatch.setenv("WALK_SPEED", "50")                                                              # the owner changes the speed afterwards
    monkeypatch.setenv("CAR_SPEED", "100")
    same = db.q1("SELECT * FROM world_trips WHERE id=?", (tr["id"],))
    assert same["speed"] == 10.0 and same["arrive_at"] == tr["arrive_at"]
    st = P.stand(db, c, A, now=1_700_000_000 + (tr["arrive_at"] - tr["started_at"]) // 2)
    assert st.trip is not None and st.progress == pytest.approx(0.5, abs=0.05)                          # the position still follows the saved route and time
    assert fmt.kmh(st.trip["speed"], "en").strip("‎") == "20 km/h"                                  # and it is still shown as the speed it was saved with


def test_the_snapp_and_job_screens_state_both_speeds(h):
    from test_life_flow import A as UA, B as UB, CH as JCH, press, screen
    h.say(UA, "اسنپ", chat=JCH)
    desk = screen(h.say(UA, "اسنپ", chat=JCH))[0]
    assert "پراید" in desk and fmt.kmh(C.car_speed(), "fa") in desk and fmt.kmh(C.walk_speed(), "fa") in desk
    ride = screen(press(h, UA, f"jb:sr:{UA}"))[0]
    assert fmt.kmh(C.car_speed(), "fa") in ride
    mcd = screen(h.say(UB, "مک دونالد", chat=JCH))[0]                                                     # (the other player: UA is on a ride)
    assert fmt.kmh(C.walk_speed(), "fa") in mcd and "متر" in mcd


# ============================================================================================== no relative wording left
RELATIVE = re.compile(r"2x|2×|twice as fast|twice the speed|double speed|دو ?برابر( سرعت| سریع)|دوبرابر", re.I)


def test_no_text_describes_a_speed_relative_to_another():
    from hypemarket import docs
    bad = []
    for lang, table in (("fa", FA), ("en", EN)):
        for key, value in table.items():
            if RELATIVE.search(value):
                bad.append((lang, key))
    for key, entry in docs.DOCS.items():
        for part in (entry[0], entry[2]):
            if RELATIVE.search(part):
                bad.append(("doc", key))
    assert not bad, bad
    env = (ROOT / ".env.example").read_text(encoding="utf-8")
    assert "twice as fast" not in env and "km/h" in env
    src = (ROOT / "hypemarket" / "work.py").read_text(encoding="utf-8")
    assert "2x walking" not in src and "40 km/h" in src


def test_the_help_text_of_the_map_states_the_walking_speed():
    from hypemarket import docs
    fa, _, en, _ = docs.DOCS["map"]
    assert "۲۰ km/h" in fa and "20 km/h" in en


def test_distance_texts_never_say_units():
    bad = [(lang, k) for lang, table in (("fa", FA), ("en", EN)) for k, v in table.items()
           if k.startswith(("mp.", "mcd.", "muni.", "snapp.", "wi.", "job")) and re.search(r"\bواحد\b|\bunits?\b", v) and "{" in v]
    assert not bad, bad
