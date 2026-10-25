"""v0.18: the McDonald's as the second permanent public place of every city, added without regenerating anything.

What is under test: the choice is pure and persisted, every ID / parcel / coordinate that already existed is untouched, old cities get it at start-up or
the first time somebody loads them, a city with no room still loads, it can be walked to, drawn, found, inspected and is never offered as empty land."""
import copy
import json
import sqlite3

import pytest

import mapkit
import test_bot as tb
from hypemarket import cards, config as C, core, presence, world, worldinspect as WI, worldmap, worldplaces
from hypemarket.db import Database
from hypemarket.texts import t

CH, CH2 = -100606, -100777
A, B = 81001, 81002


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


def city(h, chat=CH):
    return mapkit.city_of(h, chat)


def v017_world(db, chat, seed):
    """A city exactly as v0.17 left it: the saved layout and ONLY the casino's place row (no McDonald's)."""
    layout = world.build(seed)
    db.x("INSERT INTO worlds(chat_id,seed,gen_version,name_fa,name_en,cols,rows,layout,created_at) VALUES(?,?,?,?,?,?,?,?,?)",
         (chat, layout["seed"], layout["generator"], layout["name"]["fa"], layout["name"]["en"], layout["w"], layout["h"], world.dumps(layout), 1_650_000_000))
    w = world.get_world(db, chat)
    base = world.load_layout(w)
    worldplaces.ensure_place(db, w, base, None, worldplaces.CASINO)
    return world.get_world(db, chat)


# ================================================================================================ where it goes
@pytest.mark.parametrize("seed", [0, 1, 7, 42, 99, 999, 31337])
def test_a_new_city_gets_one_mcdonalds_on_its_own_empty_parcel(seed):
    db = Database(":memory:")
    w, _ = world.save_world(db, CH, world.build(seed))
    rows = {r["kind"]: r for r in db.qa("SELECT * FROM world_places WHERE world_id=?", (w["id"],))}
    assert set(rows) == {"casino", "mcdonalds"} and rows["casino"]["place_id"] == "l5" and rows["mcdonalds"]["place_id"] == "l6"
    assert rows["casino"]["parcel_id"] != rows["mcdonalds"]["parcel_id"]
    base = world.load_layout(w)
    parcel = next(p for p in base["parcels"] if p["id"] == rows["mcdonalds"]["parcel_id"])
    assert parcel["use"] == "vacant"                                                                  # an empty lot, not a building or a park
    c = worldplaces.load_city(db, w)
    assert c.mcd_id == "l6" and c.casino_id == "l5"
    spot = c.spot("l6")
    assert spot.kind == "landmark" and spot.landmark["kind"] == "mcdonalds" and spot.landmark["parcel"] == parcel["id"]
    assert c.spot(parcel["id"]).id == "l6"                                                            # its parcel ID leads to the same place
    assert spot.door.off < 60 and not c.net.segs[spot.door.seg].bridge                                # a real road in front of it, not a bridge
    changed = sorted(a["id"] for a, b in zip(base["parcels"], c.layout["parcels"]) if a != b)
    assert changed == sorted(r["parcel_id"] for r in rows.values())                                   # exactly the two reserved parcels differ from what the generator saved...
    mine = next(p for p in c.layout["parcels"] if p["id"] == parcel["id"])
    assert mine["use"] == "landmark" and mine["poly"] == parcel["poly"] and mine["c"] == parcel["c"]  # ...only by being a public place; geometry is identical
    assert [m for m in c.layout["landmarks"] if m["kind"] not in ("casino", "mcdonalds")] == base["landmarks"]


def test_the_choice_is_a_pure_function_of_the_saved_city_and_the_casino():
    picks = set()
    for _ in range(3):
        db = Database(":memory:")
        world.save_world(db, CH, world.build(7))
        picks.add(tuple((r["kind"], r["place_id"], r["parcel_id"]) for r in db.qa("SELECT * FROM world_places ORDER BY place_id")))
    assert len(picks) == 1


def test_a_player_can_walk_to_the_mcdonalds_from_where_they_start(h):
    for seed in (7, 42, 999, 5, 11):
        db = Database(":memory:")
        w, _ = world.save_world(db, CH, world.build(seed))
        c = worldplaces.load_city(db, w)
        for uid in (1, 2, 3):
            assert c.net.route(presence.spawn_point(c, uid), c.spot(c.mcd_id).door.pt) is not None


# ================================================================================================ old cities
def test_an_old_city_gets_a_mcdonalds_without_changing_anything_that_existed(h):
    w = v017_world(h.db, CH2, 5)
    casino_before = dict(worldplaces.get_place(h.db, w["id"], "casino"))
    row_before, layout_before = dict(w), copy.deepcopy(world.load_layout(w))
    h.say(A, "مپ", chat=CH2)                                                                          # somebody opens the old city's map
    h.say(B, "شروع")
    now = dict(world.get_world(h.db, CH2))
    assert now == row_before and world.load_layout(now) == layout_before                              # not one byte of the saved city
    assert dict(worldplaces.get_place(h.db, w["id"], "casino")) == casino_before                      # the casino: same ID, parcel and time
    mcd = worldplaces.get_place(h.db, w["id"], "mcdonalds")
    assert mcd["place_id"] == "l6" and mcd["parcel_id"] not in (casino_before["parcel_id"],)
    c = worldplaces.load_city(h.db, now)
    assert c.casino_id == "l5" and c.mcd_id == "l6"
    for key in ("roads", "water", "districts", "nodes", "footprint", "blocks", "terrain", "seed", "name"):
        assert c.layout[key] == layout_before[key]
    assert {p["id"]: (p["poly"], p["c"], p["area"]) for p in c.layout["parcels"]} == {p["id"]: (p["poly"], p["c"], p["area"]) for p in layout_before["parcels"]}


def test_the_start_up_pass_adds_it_once_and_is_then_a_no_op(h):
    w = v017_world(h.db, CH2, 5)
    other = dict(world.get_world(h.db, CH))
    assert worldplaces.ensure_all(h.db) >= 1
    assert worldplaces.get_place(h.db, w["id"], "mcdonalds") is not None
    assert worldplaces.ensure_all(h.db) == 0 and worldplaces.ensure_all(h.db) == 0
    assert dict(world.get_world(h.db, CH)) == other and h.db.q1("SELECT COUNT(*) c FROM world_places")["c"] == 4


def test_players_trips_and_sessions_survive_the_addition(h, d):
    v = d.open(A)
    d.open(B)
    c = city(h)
    o = d.press(A, f"mp:{v.sid}:o:p3", v)
    d.go(A, o, t("fa", "mp.b.go"))
    snap = lambda: {n: [tuple(r) for r in h.db.qa(f"SELECT * FROM {n} ORDER BY 1,2")] for n in ("world_players", "world_trips", "users", "life_accounts", "holdings", "worlds")}
    before = snap()
    mcd_before = dict(worldplaces.get_place(h.db, c.id, "mcdonalds"))
    h.db.x("DELETE FROM world_places WHERE kind='mcdonalds'")                                          # as if this city had been saved by v0.17
    worldplaces.cache_clear()
    d.open(B)
    assert dict(worldplaces.get_place(h.db, c.id, "mcdonalds")) | {"created_at": 0} == mcd_before | {"created_at": 0}      # the same ID and parcel again: stable
    assert snap() == before                                                                            # players, trips, money, holdings and the saved city: untouched


def test_a_city_with_no_room_still_loads_and_just_has_no_mcdonalds(monkeypatch):
    db = Database(":memory:")
    w = v017_world(db, CH, 5)
    real = worldplaces.choose_parcel

    def no_room_for_mcd(base, net, taken=()):
        if taken:
            raise world.WorldError("no_room")
        return real(base, net, taken)
    monkeypatch.setattr(worldplaces, "choose_parcel", no_room_for_mcd)
    c = worldplaces.load_city(db, w)
    assert c.casino_id == "l5" and c.mcd_id is None and worldplaces.get_place(db, w["id"], "mcdonalds") is None
    assert worldplaces.ensure_all(db) >= 0                                                             # the start-up pass does not crash on it
    assert c.spot("l6") is None


def test_the_database_forbids_a_second_mcdonalds_or_a_shared_parcel(h):
    w = world.get_world(h.db, CH)
    rows = {r["kind"]: r for r in worldplaces.places(h.db, w["id"])}
    free = next(p["id"] for p in world.load_layout(w)["parcels"] if p["use"] == "vacant" and p["id"] not in {r["parcel_id"] for r in rows.values()})
    for args in ((w["id"], "l9", "mcdonalds", free), (w["id"], "l6", "other", free), (w["id"], "l9", "other", rows["mcdonalds"]["parcel_id"])):
        with pytest.raises(sqlite3.IntegrityError):
            h.db.x("INSERT INTO world_places(world_id,place_id,kind,parcel_id,created_at) VALUES(?,?,?,?,0)", args)
    assert h.db.q1("SELECT COUNT(*) c FROM world_places")["c"] == 2


# ================================================================================================ seen, found, inspected
def test_the_mcdonalds_is_drawn_labelled_and_the_city_picture_changed_only_by_it(h, monkeypatch):
    c = city(h)
    seen = []
    real = cards.shape
    monkeypatch.setattr(cards, "shape", lambda text: seen.append(text) or real(text))
    with_mcd = worldmap.render(c.layout, "fa")
    assert "مک‌دونالد" in seen or any("مک" in s and "دونالد" in s for s in seen)
    without = worldmap.render(c.base, "fa")
    assert with_mcd != without
    lm = next(m for m in c.layout["landmarks"] if m["kind"] == "mcdonalds")
    px = worldmap._px(lm["at"])
    img = mapkit.png(with_mcd)
    near = [img.getpixel((int(px[0]) + dx, int(px[1]) + dy)) for dx in range(-8, 9) for dy in range(-8, 9)]
    assert len({p for p in near}) >= 3                                                                 # a marker and its outline, not flat background
    assert worldmap.RENDER_VERSION >= 3                                                                # an older cached picture (without it) is never reused


def test_the_mcdonalds_is_selectable_in_every_way(h, d):
    v = d.open(A)
    c = city(h)
    parcel = c.rows["mcdonalds"]["parcel_id"]
    pl = d.go(A, v, t("fa", "mp.b.places"))
    assert any(dd.endswith(":o:l6") for _x, dd in pl.buttons)
    for typed in ("مپ L6", "مپ l6", f"مپ {parcel.upper()}", "map L6"):
        tv = d.open(A, typed)
        assert "L6" in tv.caption and "مک" in tv.caption, typed
    assert WI.resolve(c.layout, "l6").kind == "landmark" and WI.resolve(c.layout, "l6").obj["kind"] == "mcdonalds"
    o = d.press(A, f"mp:{v.sid}:o:l6", v)
    assert "L6" in o.caption and o.photo and t("fa", "mp.b.go") in [x for x, _ in o.buttons]


def test_the_mcdonalds_is_never_offered_as_empty_land(h, d):
    v = d.open(A)
    c = city(h)
    parcel = c.rows["mcdonalds"]["parcel_id"]
    ids = []
    page = d.go(A, v, t("fa", "mp.b.lots"))
    while True:
        ids += [dd.split(":")[3] for _x, dd in page.buttons if dd.split(":")[2] == "o"]
        nxt = [dd for x, dd in page.buttons if x == t("fa", "mp.b.next")]
        if not nxt:
            break
        page = d.press(A, nxt[0], v)
    assert parcel not in ids and ids
    assert WI.inspect("fa", world.get_world(h.db, CH), c.layout, parcel).text.count(t("fa", "wi.status.landmark")) >= 1


def test_the_map_caption_counts_both_reserved_parcels_as_used(h):
    calls = h.say(A, "مپ", chat=CH)
    cap = [c for c in calls if getattr(c, "caption", None)][0].caption
    c = city(h)
    base_vacant = world.stats(c.base)["vacant"]
    assert world.stats(c.layout)["vacant"] == base_vacant - 2
    from hypemarket import fmt
    assert fmt.num(base_vacant - 2, "fa") in cap and "L6" in cap
