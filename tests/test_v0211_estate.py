"""v0.211, part 2: commercial property, brands, branches, the till and upgrades, on real generated cities.

Covered: which lots become properties and what their attributes are (pure, deterministic, saved, fixed by triggers); prices inside $250-$50,000 in whole steps; physical presence needed to buy; a
purchase charging exactly once, never twice for one lot and never when it fails (a race of two connections included); fitting an empty shell and the dear conversion; the global brand registry (one owner whatever
races, the fee, the limit, the database's own uniqueness); branches (a fitted property and an own brand needed, one open branch per property, numbering); the headquarters (city of the first active
store, moves when it closes, branches never inflate) and the commercial capital (most different brands, tie keeps the current); the till (own money, ledger, never negative, only the owner moves it
out, closing pays it out); permissions of a manager; upgrades (paid from the till, compatibility, slots, real effects, the sign). A real Telegram client was not used."""
import copy
import sqlite3
import threading

import pytest

import charkit
import test_life_jobs as LJ
from hypemarket import biz, brands, cash, config as C, core, estate, presence, upgrades, world, worldplaces
from hypemarket.core import GameError
from hypemarket.db import Database

A, B = LJ.A, LJ.B
CAFE = 51003
T0 = LJ.T0
layout = LJ.layout
layout2 = LJ.layout2


def env(layout_, chat=LJ.CH):
    E = LJ.make_env(layout_, chat=chat)
    for u in (A, B, CAFE):
        core.ensure_user(E.db, u, f"u{u}", f"User{u}", lang="fa")
        charkit.complete(E.db, u)
    estate.ensure(E.db, E.city, T0)
    return E


def rich(E, uid, dollars=100_000):
    cash.ensure_account(E.db, uid)
    cash.apply(E.db, uid, dollars * 100, "grant", f"test-{uid}-{dollars}", now=E.now[0])


def props(E, zone=None, mode=None):
    rows = estate.listing(E.db, E.city.id)
    return [p for p in rows if (zone is None or p["zone"] == zone)]


def stand(E, uid):
    return presence.stand(E.db, E.city, uid, E.now[0])


def at(E, uid, prop):
    E.go_to(uid, prop["parcel_id"])
    return stand(E, uid)


def buy(E, uid, prop, go=True):
    st = at(E, uid, prop) if go else stand(E, uid)
    return estate.buy(E.db, E.city, st, uid, prop["id"], E.now[0])


# ============================================================================================== which lots, what attributes
def test_properties_are_the_vacant_lots_of_the_rated_zones_and_nothing_else(layout):
    E = env(layout)
    rows = props(E)
    lots = {p["id"]: p for p in E.city.layout["parcels"]}
    assert rows and {r["parcel_id"] for r in rows} == {p["id"] for p in estate.eligible(E.city)}
    for r in rows:
        p = lots[r["parcel_id"]]
        assert p["use"] == "vacant" and p["zone"] in C.PROPERTY_ZONE_RATE_CENTS and r["zone"] == p["zone"]
    reserved = {r["parcel_id"] for r in worldplaces.places(E.db, E.city.id)}
    assert not reserved & {r["parcel_id"] for r in rows}                                              # the casino and the McDonald's lots are never for sale
    assert all(lots[x]["use"] != "building" for x in (r["parcel_id"] for r in rows))
    assert estate.ensure(E.db, E.city, T0 + 5) == 0 and len(props(E)) == len(rows)                     # idempotent
    assert E.db.q1("SELECT COUNT(*) c FROM properties WHERE owner_id IS NOT NULL")["c"] == 0           # nothing is owned until somebody buys it


def test_attributes_are_pure_saved_and_inside_the_rules(layout):
    E = env(layout)
    ctx = estate._centre_and_reach(E.city)
    lots = {p["id"]: p for p in E.city.layout["parcels"]}
    for r in props(E):
        a = estate.attributes(E.city, lots[r["parcel_id"]], ctx)
        assert a == estate.attributes(E.city, lots[r["parcel_id"]], ctx)
        assert (r["area_m2"], r["tier"], r["price"], r["warehouse_cap"], r["display_cap"], r["staff_slots"], r["equip_slots"], r["max_quality"], r["modes"]) == (
            a["area_m2"], a["tier"], a["price"], a["warehouse_cap"], a["display_cap"], a["staff_slots"], a["equip_slots"], a["max_quality"], a["modes"])
        assert C.PROPERTY_PRICE_MIN_CENTS <= r["price"] <= C.PROPERTY_PRICE_MAX_CENTS and r["price"] % C.PROPERTY_ROUND_CENTS == 0
        assert 1 <= r["max_quality"] <= C.COMMERCIAL_MAX_QUALITY == 4 and r["staff_slots"] >= 1 and r["equip_slots"] >= 1 and r["warehouse_cap"] > 0
        assert tuple(r["modes"].split(",")) == C.PROPERTY_ZONE_MODES[r["zone"]]
        tier = estate.tier_of(r["area_m2"])
        assert tier[0] == r["tier"]
    assert any(r["display_cap"] == 0 for r in props(E, "industrial")) or not props(E, "industrial")      # a workshop has no shop floor


@pytest.mark.parametrize("m2,tier", [(1, "kiosk"), (89, "kiosk"), (90, "small"), (179, "small"), (180, "medium"), (399, "medium"), (400, "large"), (799, "large"), (800, "flagship"), (5000, "flagship")])
def test_the_tier_follows_the_area(m2, tier):
    assert estate.tier_of(m2)[0] == tier


def test_a_commercial_property_never_makes_more_than_four_stars():
    assert max(row[6] for row in C.PROPERTY_TIERS) <= C.COMMERCIAL_MAX_QUALITY == 4


def test_every_city_has_its_own_properties_and_they_survive_a_reload(layout, layout2):
    E = env(layout)
    w2, _ = world.save_world(E.db, LJ.CH2, copy.deepcopy(layout2))
    city2 = worldplaces.load_city(E.db, w2)
    n1 = len(props(E))
    made = estate.ensure(E.db, city2, T0)
    assert made == len(estate.eligible(city2)) > 0 and len(props(E)) == n1
    assert E.db.q1("SELECT COUNT(*) c FROM properties")["c"] == n1 + made
    again = worldplaces.load_city(E.db, E.db.q1("SELECT * FROM worlds WHERE id=?", (E.city.id,)))
    assert estate.ensure(E.db, again, T0) == 0


def test_a_property_keeps_its_lot_price_and_owner_by_trigger(layout):
    E = env(layout)
    p = props(E)[0]
    for col, val in (("price", p["price"] + 1), ("area_m2", p["area_m2"] + 1), ("parcel_id", "p9999"), ("zone", "park"), ("warehouse_cap", p["warehouse_cap"] + 1),
                     ("max_quality", p["max_quality"] % 4 + 1), ("modes", p["modes"] + ",x"), ("tier", p["tier"] + "x"), ("staff_slots", p["staff_slots"] + 1), ("equip_slots", p["equip_slots"] + 1),
                     ("display_cap", p["display_cap"] + 1), ("world_id", p["world_id"] + 1)):
        with pytest.raises(sqlite3.DatabaseError, match="keeps its lot"):
            E.db.x(f"UPDATE properties SET {col}=? WHERE id=?", (val, p["id"]))
    with pytest.raises(sqlite3.DatabaseError):
        E.db.x("DELETE FROM properties WHERE id=?", (p["id"],))
    rich(E, A)
    q = buy(E, A, p)
    assert q["owner_id"] == A
    with pytest.raises(sqlite3.DatabaseError):
        E.db.x("UPDATE properties SET owner_id=? WHERE id=?", (B, p["id"]))
    with pytest.raises(sqlite3.DatabaseError):
        E.db.x("UPDATE properties SET owner_id=NULL WHERE id=?", (p["id"],))


# ============================================================================================== buying
def cheapest(E, zone="commercial"):
    rows = [p for p in props(E, zone) if p["owner_id"] is None]
    return min(rows, key=lambda p: (p["price"], p["id"]))


def test_buying_needs_the_player_to_stand_at_the_property(layout):
    E = env(layout)
    rich(E, A)
    p = cheapest(E)
    st = stand(E, A)
    spot = E.city.spot(p["parcel_id"])
    assert E.city.net.field(st.pos, cutoff=10 ** 6).to(spot.door) > C.BIZ_NEAR_UNITS, "the player starts away from the cheapest lot"
    bal = cash.balance(E.db, A)
    with pytest.raises(GameError) as e:
        estate.buy(E.db, E.city, st, A, p["id"], E.now[0])
    assert e.value.code == "prop_far" and cash.balance(E.db, A) == bal
    assert estate.get(E.db, p["id"])["owner_id"] is None
    E.go_to(A, p["parcel_id"])
    got = estate.buy(E.db, E.city, stand(E, A), A, p["id"], E.now[0])
    assert got["owner_id"] == A and got["bought_price"] == p["price"] and got["bought_at"] == E.now[0] and got["mode"] is None        # an EMPTY shell
    assert estate.get(E.db, p["id"])["owner_id"] == A


def test_a_player_who_is_walking_is_not_near_anything(layout):
    E = env(layout)
    rich(E, A)
    p = cheapest(E)
    E.go_to(A, E.city.mcd_id)
    out = presence.start_trip(E.db, E.city, A, p["parcel_id"], E.now[0])
    assert out.status == "started"
    st = stand(E, A)
    assert st.trip is not None and not estate.near(E.city, st, p)
    with pytest.raises(GameError) as e:
        estate.buy(E.db, E.city, st, A, p["id"], E.now[0])
    assert e.value.code == "prop_far"


def test_standing_close_to_the_door_counts_as_near(layout):
    E = env(layout)
    p = cheapest(E)
    E.go_to(A, p["parcel_id"])
    st = stand(E, A)
    assert estate.near(E.city, st, p)
    other = max((q for q in props(E) if q["id"] != p["id"]), key=lambda q: G_dist(E, st, q))
    assert not estate.near(E.city, st, other)


def G_dist(E, st, q):
    return E.city.net.field(st.pos, cutoff=10 ** 6).to(E.city.spot(q["parcel_id"]).door)


def test_the_price_is_charged_once_and_the_ledger_says_so(layout):
    E = env(layout)
    rich(E, A, 60_000)
    p = cheapest(E)
    before = cash.balance(E.db, A)
    buy(E, A, p)
    assert cash.balance(E.db, A) == before - p["price"]
    assert E.ledger(A, ("property_buy",)) == [("property_buy", str(p["id"]), -p["price"])]
    with pytest.raises(GameError) as e:
        estate.buy(E.db, E.city, stand(E, A), A, p["id"], E.now[0])
    assert e.value.code == "prop_taken" and cash.balance(E.db, A) == before - p["price"]                 # the same lot twice: nothing more is charged


def test_a_lot_somebody_owns_cannot_be_bought_and_the_second_buyer_pays_nothing(layout):
    E = env(layout)
    rich(E, A)
    rich(E, B)
    p = cheapest(E)
    buy(E, A, p)
    bal = cash.balance(E.db, B)
    with pytest.raises(GameError) as e:
        buy(E, B, p)
    assert e.value.code == "prop_taken" and cash.balance(E.db, B) == bal and estate.get(E.db, p["id"])["owner_id"] == A


def test_a_player_who_cannot_pay_gets_nothing_and_loses_nothing(layout):
    E = env(layout)
    p = cheapest(E)
    bal = cash.balance(E.db, B)
    assert bal < p["price"]
    with pytest.raises(GameError) as e:
        buy(E, B, p)
    assert e.value.code == "cash_low" and cash.balance(E.db, B) == bal and estate.get(E.db, p["id"])["owner_id"] is None


def test_only_finished_human_characters_can_buy(layout):
    E = env(layout)
    p = cheapest(E)
    E.db.x("UPDATE users SET bot=1 WHERE id=?", (CAFE,))
    with pytest.raises(GameError) as e:
        estate.buy(E.db, E.city, stand(E, CAFE), CAFE, p["id"], E.now[0])
    assert e.value.code == "no_user"
    core.ensure_user(E.db, 51099, "z", "Zed", lang="fa")
    with pytest.raises(GameError) as e:
        estate.buy(E.db, E.city, stand(E, A), 51099, p["id"], E.now[0])
    assert e.value.code == "no_character" and estate.get(E.db, p["id"])["owner_id"] is None


def test_a_property_of_another_city_or_an_unknown_one_cannot_be_bought(layout, layout2):
    E = env(layout)
    w2, _ = world.save_world(E.db, LJ.CH2, copy.deepcopy(layout2))
    city2 = worldplaces.load_city(E.db, w2)
    estate.ensure(E.db, city2, T0)
    other = estate.listing(E.db, city2.id)[0]
    rich(E, A)
    for pid in (other["id"], 99999, 0, -1, None, "1", True):
        with pytest.raises(GameError) as e:
            estate.buy(E.db, E.city, stand(E, A), A, pid, E.now[0])
        assert e.value.code == "prop_gone", pid


def test_two_connections_racing_for_one_lot_make_one_owner_and_one_charge(layout, tmp_path):
    path = str(tmp_path / "race.db")
    db = Database(path)
    core.set_clock(lambda: T0)
    LJ.worldmap_reset()
    for u in (A, B):
        core.ensure_user(db, u, f"u{u}", f"User{u}", lang="fa")
        charkit.complete(db, u)
        cash.ensure_account(db, u)
        cash.apply(db, u, 100_000_00, "grant", f"r{u}", now=T0)
    w, _ = world.save_world(db, LJ.CH, copy.deepcopy(layout))
    city = worldplaces.load_city(db, w)
    estate.ensure(db, city, T0)
    p = min(estate.listing(db, city.id), key=lambda q: (q["price"], q["id"]))
    for u in (A, B):
        presence.start_trip(db, city, u, p["parcel_id"], T0)
    arrive = max(presence.walking_trip(db, city.id, u)["arrive_at"] for u in (A, B))
    core.set_clock(lambda: arrive)
    db.close()
    outcomes, errors = {}, []
    barrier = threading.Barrier(2)

    def run(u):
        try:
            d = Database(path)
            c = worldplaces.load_city(d, d.q1("SELECT * FROM worlds WHERE id=?", (city.id,)))
            st = presence.stand(d, c, u, arrive)
            barrier.wait()
            try:
                estate.buy(d, c, st, u, p["id"], arrive)
                outcomes[u] = "bought"
            except GameError as e:
                outcomes[u] = e.code
        except Exception as e:                                                                          # noqa: BLE001
            errors.append(repr(e))
    th = [threading.Thread(target=run, args=(u,)) for u in (A, B)]
    [x.start() for x in th]
    [x.join() for x in th]
    assert not errors, errors
    assert sorted(outcomes.values()) == ["bought", "prop_taken"]
    d = Database(path)
    owner = d.q1("SELECT owner_id FROM properties WHERE id=?", (p["id"],))["owner_id"]
    winner, loser = (A, B) if outcomes[A] == "bought" else (B, A)
    assert owner == winner
    assert cash.balance(d, winner) == 100_000_00 + C.CASH_START_CENTS - p["price"] and cash.balance(d, loser) == 100_000_00 + C.CASH_START_CENTS
    assert d.q1("SELECT COUNT(*) c FROM life_ledger WHERE kind='property_buy'")["c"] == 1


# ============================================================================================== fitting and converting
def owned(E, uid=A, zone="commercial"):
    rich(E, uid)
    p = cheapest(E, zone)
    buy(E, uid, p)
    return estate.get(E.db, p["id"])


def test_an_empty_shell_is_fitted_for_a_mode_the_lot_permits_for_a_fee(layout):
    E = env(layout)
    p = owned(E)
    bal = cash.balance(E.db, A)
    fee = estate.fit_cost(p)
    assert fee == max(C.FITOUT_MIN_CENTS, p["price"] * C.FITOUT_BPS // 10000)
    q = estate.set_mode(E.db, A, p["id"], "both", E.now[0])
    assert q["mode"] == "both" and q["fitted_at"] == E.now[0] and cash.balance(E.db, A) == bal - fee
    assert E.ledger(A, ("property_fit",)) == [("property_fit", str(p["id"]), -fee)]
    with pytest.raises(GameError) as e:
        estate.set_mode(E.db, A, p["id"], "both", E.now[0])
    assert e.value.code == "mode_same" and cash.balance(E.db, A) == bal - fee


def test_a_mode_the_zone_does_not_permit_is_refused(layout):
    E = env(layout)
    res = [p for p in props(E, "residential") if p["owner_id"] is None]
    assert res, "the test city has residential lots"
    rich(E, A)
    p = min(res, key=lambda q: (q["price"], q["id"]))
    buy(E, A, p)
    for bad in ("production", "both", "nonsense", None, ""):
        with pytest.raises(GameError) as e:
            estate.set_mode(E.db, A, p["id"], bad, E.now[0])
        assert e.value.code == "mode_bad", bad
    assert estate.set_mode(E.db, A, p["id"], "retail", E.now[0])["mode"] == "retail"


def test_only_the_owner_fits_or_converts(layout):
    E = env(layout)
    p = owned(E)
    rich(E, B)
    for who in (B, CAFE):
        with pytest.raises(GameError) as e:
            estate.set_mode(E.db, who, p["id"], "retail", E.now[0])
        assert e.value.code == "prop_notyours"
    with pytest.raises(GameError) as e:
        estate.set_mode(E.db, A, 424242, "retail", E.now[0])
    assert e.value.code == "prop_gone"


def test_converting_costs_much_more_and_can_be_blocked(layout):
    E = env(layout)
    p = owned(E)
    estate.set_mode(E.db, A, p["id"], "retail", E.now[0])
    bal = cash.balance(E.db, A)
    assert estate.convert_cost(p) > estate.fit_cost(p) * 2 or estate.convert_cost(p) == C.MODE_CHANGE_MIN_CENTS
    estate.register_blocker_hook(lambda db, prop: ["stock"] if prop["id"] == p["id"] else [])
    try:
        with pytest.raises(GameError) as e:
            estate.set_mode(E.db, A, p["id"], "production", E.now[0])
        assert e.value.code == "mode_busy" and cash.balance(E.db, A) == bal and estate.get(E.db, p["id"])["mode"] == "retail"
    finally:
        estate._BLOCKERS.clear()
    q = estate.set_mode(E.db, A, p["id"], "production", E.now[0] + 1)
    assert q["mode"] == "production" and cash.balance(E.db, A) == bal - estate.convert_cost(p)
    assert estate.mode_allows("both", "retail") and estate.mode_allows("retail", "retail") and not estate.mode_allows("retail", "production") and not estate.mode_allows(None, "retail")


# ============================================================================================== brands
def test_registering_a_brand_charges_the_fee_once_and_gives_nothing_else(layout):
    E = env(layout)
    rich(E, A)
    bal = cash.balance(E.db, A)
    row = brands.register(E.db, A, "nike", E.now[0])
    assert (row["brand"], row["owner_id"], row["fee"]) == ("nike", A, C.BRAND_FEE_CENTS) and cash.balance(E.db, A) == bal - C.BRAND_FEE_CENTS
    assert E.ledger(A, ("brand_fee",)) == [("brand_fee", "nike", -C.BRAND_FEE_CENTS)]
    assert E.db.q1("SELECT COUNT(*) c FROM properties WHERE owner_id IS NOT NULL")["c"] == 0 and E.db.q1("SELECT COUNT(*) c FROM branches")["c"] == 0 and E.db.q1("SELECT COUNT(*) c FROM item_inventory")["c"] == 0
    assert brands.registered(E.db) == {"nike": A} and brands.of_player(E.db, A)[0]["brand"] == "nike"


def test_a_brand_has_one_owner_in_the_whole_game(layout):
    E = env(layout)
    rich(E, A)
    rich(E, B)
    brands.register(E.db, A, "nike", E.now[0])
    bal = cash.balance(E.db, B)
    with pytest.raises(GameError) as e:
        brands.register(E.db, B, "nike", E.now[0])
    assert e.value.code == "brand_taken" and cash.balance(E.db, B) == bal
    with pytest.raises(sqlite3.IntegrityError):                                                         # and the table itself says no, whatever the code does
        E.db.x("INSERT INTO brands(brand,owner_id,fee,registered_at) VALUES('nike',?,0,?)", (B, T0))
    for sql in ("UPDATE brands SET owner_id=%d" % B, "DELETE FROM brands"):
        with pytest.raises(sqlite3.DatabaseError):
            E.db.x(sql)
    assert brands.get(E.db, "nike")["owner_id"] == A


def test_a_brand_not_on_the_list_or_beyond_the_limit_or_without_cash_is_refused(layout, monkeypatch):
    E = env(layout)
    rich(E, A)
    for bad in ("adidas", "NIKE", "", None, "nike ", "louis vuitton"):
        with pytest.raises(GameError) as e:
            brands.register(E.db, A, bad, E.now[0])
        assert e.value.code == "brand_unknown", bad
    brands.register(E.db, A, "givenchy", E.now[0])
    with pytest.raises(GameError) as e:
        brands.register(E.db, A, "balenciaga", E.now[0])
    assert e.value.code == "brand_limit" and brands.get(E.db, "balenciaga") is None
    monkeypatch.setattr(C, "BRAND_MAX_PER_PLAYER", 0)                                                  # 0 = no limit
    assert brands.register(E.db, A, "balenciaga", E.now[0])["owner_id"] == A
    with pytest.raises(GameError) as e:                                                                 # B has only the starting $100
        brands.register(E.db, B, "louis_vuitton", E.now[0])
    assert e.value.code == "cash_low" and brands.get(E.db, "louis_vuitton") is None


def test_the_brand_list_is_configurable_without_code(monkeypatch):
    monkeypatch.setenv("BRAND_NAMES", "Acme Coats, nike ,Acme Coats, X, bad name!")
    assert list(C.brand_names()) == ["acme_coats", "nike", "bad_name"] and C.brand_names()["acme_coats"] == "ACME COATS"
    monkeypatch.delenv("BRAND_NAMES")
    assert list(C.brand_names()) == ["nike", "balenciaga", "givenchy", "louis_vuitton"]


def test_only_finished_human_characters_register_brands(layout):
    E = env(layout)
    core.ensure_user(E.db, 51098, "q", "Quin", lang="fa")
    with pytest.raises(GameError) as e:
        brands.register(E.db, 51098, "nike", E.now[0])
    assert e.value.code == "no_character"
    E.db.x("UPDATE users SET bot=1 WHERE id=?", (CAFE,))
    with pytest.raises(GameError) as e:
        brands.register(E.db, CAFE, "nike", E.now[0])
    assert e.value.code == "no_user" and brands.get(E.db, "nike") is None


def test_two_connections_claiming_one_brand_make_one_owner_and_one_charge(tmp_path):
    path = str(tmp_path / "brand.db")
    db = Database(path)
    core.set_clock(lambda: T0)
    for u in (A, B):
        core.ensure_user(db, u, f"u{u}", f"User{u}", lang="fa")
        charkit.complete(db, u)
        cash.ensure_account(db, u)
        cash.apply(db, u, 10_000_00, "grant", f"r{u}", now=T0)
    db.close()
    outcomes, errors = {}, []
    barrier = threading.Barrier(2)

    def run(u):
        try:
            d = Database(path)
            barrier.wait()
            try:
                brands.register(d, u, "givenchy", T0)
                outcomes[u] = "won"
            except GameError as e:
                outcomes[u] = e.code
        except Exception as e:                                                                          # noqa: BLE001
            errors.append(repr(e))
    th = [threading.Thread(target=run, args=(u,)) for u in (A, B)]
    [x.start() for x in th]
    [x.join() for x in th]
    assert not errors, errors
    assert sorted(outcomes.values()) == ["brand_taken", "won"]
    d = Database(path)
    winner = brands.get(d, "givenchy")["owner_id"]
    loser = B if winner == A else A
    assert cash.balance(d, winner) == 10_000_00 + C.CASH_START_CENTS - C.BRAND_FEE_CENTS and cash.balance(d, loser) == 10_000_00 + C.CASH_START_CENTS
    assert d.q1("SELECT COUNT(*) c FROM life_ledger WHERE kind='brand_fee'")["c"] == 1


# ============================================================================================== branches
def shop(E, uid=A, brand="nike", zone="commercial", mode="retail", skip=()):
    """A fitted property of `uid` (a new one each call) and the owner's brand (registered once)."""
    rich(E, uid)
    rows = [p for p in props(E, zone) if p["owner_id"] is None and p["id"] not in skip and mode in C.PROPERTY_ZONE_MODES[zone]]
    p = min(rows, key=lambda q: (q["price"], q["id"]))
    buy(E, uid, p)
    estate.set_mode(E.db, uid, p["id"], mode, E.now[0])
    if brands.get(E.db, brand) is None:
        brands.register(E.db, uid, brand, E.now[0])
    return estate.get(E.db, p["id"])


def test_a_branch_needs_a_fitted_property_and_an_own_brand(layout):
    E = env(layout)
    rich(E, A)
    rich(E, B)
    p = owned(E)
    with pytest.raises(GameError) as e:
        biz.open_branch(E.db, A, p["id"], "nike", E.now[0])
    assert e.value.code == "branch_nomode"                                                              # an empty shell cannot open
    estate.set_mode(E.db, A, p["id"], "retail", E.now[0])
    with pytest.raises(GameError) as e:
        biz.open_branch(E.db, A, p["id"], "nike", E.now[0])
    assert e.value.code == "brand_notyours"                                                             # unregistered
    brands.register(E.db, B, "nike", E.now[0])
    with pytest.raises(GameError) as e:
        biz.open_branch(E.db, A, p["id"], "nike", E.now[0])
    assert e.value.code == "brand_notyours"                                                             # somebody else's brand
    brands.register(E.db, A, "givenchy", E.now[0])
    with pytest.raises(GameError) as e:
        biz.open_branch(E.db, B, p["id"], "nike", E.now[0])
    assert e.value.code == "prop_notyours"                                                              # somebody else's property
    b = biz.open_branch(E.db, A, p["id"], "givenchy", E.now[0])
    assert (b["brand"], b["owner_id"], b["property_id"], b["world_id"], b["seq"], b["state"], b["till"], b["sign"]) == ("givenchy", A, p["id"], E.city.id, 1, "open", 0, "plain")
    with pytest.raises(GameError) as e:
        biz.open_branch(E.db, A, p["id"], "givenchy", E.now[0])
    assert e.value.code == "branch_exists" and E.db.q1("SELECT COUNT(*) c FROM branches")["c"] == 1


def test_brand_property_and_branch_are_separate_things(layout):
    E = env(layout)
    p = owned(E)
    brands.register(E.db, A, "nike", E.now[0])
    assert biz.of_owner(E.db, A) == [] and estate.get(E.db, p["id"])["mode"] is None and brands.hq(E.db, "nike") is None
    estate.set_mode(E.db, A, p["id"], "both", E.now[0])
    assert biz.of_owner(E.db, A) == [] and brands.hq(E.db, "nike") is None                              # a fitted property and a brand still make no business


def test_branches_are_numbered_per_brand_and_city_and_only_one_may_be_open_per_property(layout):
    E = env(layout)
    p1 = shop(E)
    p2 = shop(E, skip=(p1["id"],))
    b1 = biz.open_branch(E.db, A, p1["id"], "nike", E.now[0])
    b2 = biz.open_branch(E.db, A, p2["id"], "nike", E.now[0] + 1)
    assert (b1["seq"], b2["seq"]) == (1, 2) and biz.label(b2) == "NIKE #2"
    with pytest.raises(sqlite3.IntegrityError):                                                         # the index itself allows one open branch per property
        E.db.x("INSERT INTO branches(brand,owner_id,property_id,world_id,seq,state,sign,till,opened_at,created_at) VALUES('nike',?,?,?,9,'open','plain',0,?,?)", (A, p1["id"], E.city.id, T0, T0))
    biz.close_branch(E.db, A, b1["id"], E.now[0] + 2)
    b3 = biz.open_branch(E.db, A, p1["id"], "nike", E.now[0] + 3)                                       # a closed branch frees the property; the number is never reused
    assert b3["seq"] == 3 and [r["state"] for r in biz.of_owner(E.db, A)] == ["closed", "open", "open"]
    with pytest.raises(sqlite3.DatabaseError):
        E.db.x("UPDATE branches SET state='open' WHERE id=?", (b1["id"],))                              # a closed branch stays closed
    with pytest.raises(sqlite3.DatabaseError):
        E.db.x("UPDATE branches SET owner_id=? WHERE id=?", (B, b2["id"]))


# ============================================================================================== headquarters and the capital
def second_city(E, layout2):
    w2, _ = world.save_world(E.db, LJ.CH2, copy.deepcopy(layout2))
    c2 = worldplaces.load_city(E.db, w2)
    estate.ensure(E.db, c2, T0)
    return c2


def buy_in(E, city, uid, mode="retail", zone="commercial", skip=()):
    rich(E, uid)
    rows = [p for p in estate.listing(E.db, city.id) if p["owner_id"] is None and p["zone"] == zone and p["id"] not in skip and mode in C.PROPERTY_ZONE_MODES[zone]]
    p = min(rows, key=lambda q: (q["price"], q["id"]))
    E.db.x("UPDATE life_accounts SET balance=balance WHERE user_id=?", (uid,))
    spot = city.spot(p["parcel_id"])
    st = presence.stand(E.db, city, uid, E.now[0])
    # (the player is moved next to the lot directly: the walking itself is covered by the other tests)
    E.db.x("UPDATE world_players SET x=?, y=?, at_place=? WHERE world_id=? AND user_id=?", (spot.door.pt[0], spot.door.pt[1], spot.id, city.id, uid))
    st = presence.stand(E.db, city, uid, E.now[0])
    estate.buy(E.db, city, st, uid, p["id"], E.now[0])
    estate.set_mode(E.db, uid, p["id"], mode, E.now[0])
    return estate.get(E.db, p["id"])


def test_the_headquarters_is_the_city_of_the_first_active_store_and_moves_when_it_closes(layout, layout2):
    E = env(layout)
    c2 = second_city(E, layout2)
    rich(E, A)
    brands.register(E.db, A, "nike", E.now[0])
    pa = buy_in(E, E.city, A)
    pb = buy_in(E, c2, A)
    first = biz.open_branch(E.db, A, pa["id"], "nike", E.now[0] + 10)
    assert brands.hq(E.db, "nike") == E.city.id and biz.is_hq(E.db, first)
    second = biz.open_branch(E.db, A, pb["id"], "nike", E.now[0] + 20)
    assert brands.hq(E.db, "nike") == E.city.id and not biz.is_hq(E.db, second)                         # a later branch elsewhere changes nothing
    biz.close_branch(E.db, A, first["id"], E.now[0] + 30)
    assert brands.hq(E.db, "nike") == c2.id and biz.is_hq(E.db, biz.get(E.db, second["id"]))            # the next oldest open store takes over
    biz.close_branch(E.db, A, second["id"], E.now[0] + 40)
    assert brands.hq(E.db, "nike") is None and brands.headquarters(E.db) == {}


def test_a_workshop_is_not_a_store_and_makes_no_headquarters(layout):
    E = env(layout)
    rich(E, A)
    brands.register(E.db, A, "nike", E.now[0])
    p = buy_in(E, E.city, A, mode="production", zone="commercial")
    biz.open_branch(E.db, A, p["id"], "nike", E.now[0])
    assert brands.hq(E.db, "nike") is None and brands.stores(E.db, "nike") == []
    q = buy_in(E, E.city, A, mode="both", skip=(p["id"],))
    biz.open_branch(E.db, A, q["id"], "nike", E.now[0] + 1)
    assert brands.hq(E.db, "nike") == E.city.id


def test_the_commercial_capital_is_the_city_with_the_most_different_brands(layout, layout2):
    E = env(layout)
    c2 = second_city(E, layout2)
    assert brands.refresh_capital(E.db, E.now[0]) is None and brands.capital(E.db) is None
    for uid, brand in ((A, "nike"), (B, "givenchy"), (CAFE, "balenciaga")):
        rich(E, uid)
        brands.register(E.db, uid, brand, E.now[0])
    a1 = buy_in(E, E.city, A)
    biz.open_branch(E.db, A, a1["id"], "nike", E.now[0] + 1)
    assert brands.capital(E.db) == E.city.id                                                            # one brand, one city: that city
    b2 = buy_in(E, c2, B)
    biz.open_branch(E.db, B, b2["id"], "givenchy", E.now[0] + 2)
    assert brands.counts(E.db) == {E.city.id: 1, c2.id: 1} and brands.capital(E.db) == E.city.id        # a tie keeps the current capital
    c3 = buy_in(E, c2, CAFE)
    biz.open_branch(E.db, CAFE, c3["id"], "balenciaga", E.now[0] + 3)
    assert brands.counts(E.db) == {E.city.id: 1, c2.id: 2} and brands.capital(E.db) == c2.id            # the second city now has more
    a_second = buy_in(E, E.city, A, skip=(a1["id"],))
    biz.open_branch(E.db, A, a_second["id"], "nike", E.now[0] + 4)
    assert brands.counts(E.db) == {E.city.id: 1, c2.id: 2} and brands.capital(E.db) == c2.id            # more branches of the same brand never count more
    biz.close_branch(E.db, CAFE, biz.of_owner(E.db, CAFE)[0]["id"], E.now[0] + 5)
    assert brands.counts(E.db) == {E.city.id: 1, c2.id: 1} and brands.capital(E.db) == c2.id            # a tie again: the current one stays
    biz.close_branch(E.db, B, biz.of_owner(E.db, B)[0]["id"], E.now[0] + 6)
    assert brands.capital(E.db) == E.city.id
    for who in (A,):
        for b in biz.of_owner(E.db, who, open_only=True):
            biz.close_branch(E.db, who, b["id"], E.now[0] + 7)
    assert brands.capital(E.db) is None and E.db.q1("SELECT COUNT(*) c FROM kv WHERE key='commercial_capital'")["c"] == 0
    assert [r["kind"] for r in E.db.qa("SELECT kind FROM biz_events WHERE kind='capital_changed' ORDER BY id")][0] == "capital_changed"


# ============================================================================================== the till
def branch(E, uid=A, **kw):
    p = shop(E, uid, **kw)
    return biz.open_branch(E.db, uid, p["id"], kw.get("brand", "nike"), E.now[0])


def test_the_till_is_separate_money_that_moves_in_pairs_with_the_owners_wallet(layout):
    E = env(layout)
    b = branch(E)
    bal = cash.balance(E.db, A)
    assert biz.fund(E.db, A, b["id"], 500_00, "f1", E.now[0]) == 500_00 and cash.balance(E.db, A) == bal - 500_00
    assert biz.fund(E.db, A, b["id"], 500_00, "f1", E.now[0]) == 500_00 and cash.balance(E.db, A) == bal - 500_00           # the same reference twice: once
    assert biz.withdraw(E.db, A, b["id"], 200_00, "w1", E.now[0]) == 300_00 and cash.balance(E.db, A) == bal - 300_00
    with pytest.raises(GameError) as e:
        biz.withdraw(E.db, A, b["id"], 300_01, "w2", E.now[0])
    assert e.value.code == "till_low" and cash.balance(E.db, A) == bal - 300_00 and biz.get(E.db, b["id"])["till"] == 300_00
    assert biz.audit_till(E.db, b["id"]) == []
    assert [(r["kind"], r["delta"]) for r in reversed(biz.ledger(E.db, b["id"]))] == [("fund", 500_00), ("withdraw", -200_00)]
    for bad in (0, -5, 1.5, True, None, "5"):
        with pytest.raises(ValueError):
            biz.fund(E.db, A, b["id"], bad, None, E.now[0])


def test_a_player_without_cash_cannot_fund_and_nothing_moves(layout):
    E = env(layout)
    b = branch(E)
    with pytest.raises(GameError) as e:
        biz.fund(E.db, A, b["id"], 10 ** 9, "big", E.now[0])
    assert e.value.code == "cash_low" and biz.get(E.db, b["id"])["till"] == 0 and E.db.q1("SELECT COUNT(*) c FROM biz_ledger")["c"] == 0


def test_only_the_owner_moves_money_in_or_out_and_closes(layout):
    E = env(layout)
    b = branch(E)
    biz.fund(E.db, A, b["id"], 100_00, "f", E.now[0])
    rich(E, B)
    for fn in (lambda: biz.fund(E.db, B, b["id"], 100, "x", E.now[0]), lambda: biz.withdraw(E.db, B, b["id"], 100, "x", E.now[0]), lambda: biz.close_branch(E.db, B, b["id"], E.now[0])):
        with pytest.raises(GameError) as e:
            fn()
        assert e.value.code == "branch_denied"
    assert biz.get(E.db, b["id"])["till"] == 100_00 and biz.get(E.db, b["id"])["state"] == "open"
    with pytest.raises(GameError) as e:
        biz.fund(E.db, A, 424242, 100, "x", E.now[0])
    assert e.value.code == "branch_gone"


def test_closing_pays_the_till_to_the_owner_once_and_the_books_stay(layout):
    E = env(layout)
    b = branch(E)
    biz.fund(E.db, A, b["id"], 250_00, "f", E.now[0])
    bal = cash.balance(E.db, A)
    c = biz.close_branch(E.db, A, b["id"], E.now[0] + 5)
    assert c["state"] == "closed" and c["till"] == 0 and c["closed_at"] == E.now[0] + 5 and cash.balance(E.db, A) == bal + 250_00
    assert biz.audit_till(E.db, b["id"]) == [] and E.db.q1("SELECT COUNT(*) c FROM biz_ledger WHERE branch_id=?", (b["id"],))["c"] == 2          # the funding and the payout on closing
    with pytest.raises(GameError) as e:
        biz.close_branch(E.db, A, b["id"], E.now[0] + 6)
    assert e.value.code == "branch_gone" and cash.balance(E.db, A) == bal + 250_00
    with pytest.raises(GameError) as e:
        biz.fund(E.db, A, b["id"], 100, "again", E.now[0] + 7)
    assert e.value.code == "branch_gone"
    for sql in ("UPDATE biz_ledger SET delta=5", "DELETE FROM biz_ledger"):
        with pytest.raises(sqlite3.DatabaseError):
            E.db.x(sql)


def test_the_till_ledger_catches_a_forged_balance(layout):
    E = env(layout)
    b = branch(E)
    biz.fund(E.db, A, b["id"], 100_00, "f", E.now[0])
    assert biz.audit_till(E.db, b["id"]) == []
    E.db.x("UPDATE branches SET till=till+1 WHERE id=?", (b["id"],))
    assert biz.audit_till(E.db, b["id"])


def test_the_till_never_goes_negative_by_any_route(layout):
    E = env(layout)
    b = branch(E)
    with pytest.raises(GameError):
        biz.till_apply(E.db, b["id"], -1, "x", "r", now=E.now[0])
    with pytest.raises(sqlite3.IntegrityError):
        E.db.x("UPDATE branches SET till=-5 WHERE id=?", (b["id"],))


# ============================================================================================== permissions
def test_a_manager_gets_only_the_permissions_a_hook_grants(layout):
    E = env(layout)
    b = branch(E)
    rich(E, B)
    assert biz.can(E.db, A, b) and biz.can(E.db, A, b, "stock") and not biz.can(E.db, B, b) and not biz.can(E.db, B, b, "stock")
    biz.register_perm_hook(lambda db, uid, br: {"stock", "ads"} if uid == B and br["id"] == b["id"] else set())
    try:
        assert biz.can(E.db, B, b, "stock") and biz.can(E.db, B, b, "ads") and not biz.can(E.db, B, b, "spend") and not biz.can(E.db, B, b, "hire") and not biz.can(E.db, B, b, "everything")
        assert biz.can(E.db, B, b) and not biz.can(E.db, CAFE, b)
        assert biz.need(E.db, B, b["id"], "stock")["id"] == b["id"]
        with pytest.raises(GameError) as e:
            biz.need(E.db, B, b["id"], "hire")
        assert e.value.code == "branch_denied"
        for fn in (lambda: biz.fund(E.db, B, b["id"], 100, "m", E.now[0]), lambda: biz.withdraw(E.db, B, b["id"], 100, "m", E.now[0]), lambda: biz.close_branch(E.db, B, b["id"], E.now[0])):
            with pytest.raises(GameError) as e:
                fn()
            assert e.value.code == "branch_denied"                                                      # never the owner's own powers
    finally:
        biz._PERM_HOOKS.clear()


# ============================================================================================== upgrades
def test_an_upgrade_is_paid_from_the_branchs_till_level_by_level(layout):
    E = env(layout)
    b = branch(E)
    with pytest.raises(GameError) as e:
        upgrades.install(E.db, A, b["id"], "shelving", E.now[0])
    assert e.value.code == "till_low" and upgrades.level_of(E.db, b["id"], "shelving") == 0
    biz.fund(E.db, A, b["id"], 2000_00, "f", E.now[0])
    prop = estate.get(E.db, b["property_id"])
    base = upgrades.warehouse_cap(E.db, b)
    assert base == prop["warehouse_cap"]
    assert upgrades.install(E.db, A, b["id"], "shelving", E.now[0]) == 1 and biz.get(E.db, b["id"])["till"] == 2000_00 - 150_00
    assert upgrades.warehouse_cap(E.db, b) == prop["warehouse_cap"] * 12500 // 10000
    assert upgrades.install(E.db, A, b["id"], "shelving", E.now[0]) == 2 and upgrades.install(E.db, A, b["id"], "shelving", E.now[0]) == 3
    assert upgrades.warehouse_cap(E.db, b) == prop["warehouse_cap"] * 22000 // 10000 and biz.get(E.db, b["id"])["till"] == 2000_00 - 150_00 - 400_00 - 1000_00
    with pytest.raises(GameError) as e:
        upgrades.install(E.db, A, b["id"], "shelving", E.now[0])
    assert e.value.code == "upg_maxed" and upgrades.level_of(E.db, b["id"], "shelving") == 3
    assert biz.audit_till(E.db, b["id"]) == [] and E.db.q1("SELECT COUNT(*) c FROM biz_ledger WHERE kind='upgrade'")["c"] == 3
    with pytest.raises(sqlite3.DatabaseError):
        E.db.x("UPDATE branch_upgrades SET level=1 WHERE branch_id=? AND kind='shelving'", (b["id"],))


def test_upgrades_must_fit_the_properties_mode_and_tier(layout):
    E = env(layout)
    b = branch(E, mode="retail")
    biz.fund(E.db, A, b["id"], 5000_00, "f", E.now[0])
    for kind in ("machines", "cutting_table"):
        with pytest.raises(GameError) as e:
            upgrades.install(E.db, A, b["id"], kind, E.now[0])
        assert e.value.code in ("upg_mode",), kind                                                      # a shop has no workshop machines
    with pytest.raises(GameError) as e:
        upgrades.install(E.db, A, b["id"], "nonsense", E.now[0])
    assert e.value.code == "upg_unknown"
    assert upgrades.problem(E.db, b, "racks") is None and upgrades.problem(E.db, b, "shelving") is None and upgrades.problem(E.db, b, "sign") is None
    E2 = env(layout)
    w = branch(E2, mode="production")
    biz.fund(E2.db, A, w["id"], 5000_00, "f", E2.now[0])
    for kind in ("racks", "sign", "checkout", "presentation"):
        with pytest.raises(GameError) as e:
            upgrades.install(E2.db, A, w["id"], kind, E2.now[0])
        assert e.value.code == "upg_mode", kind                                                         # a workshop has no shop floor to fit
    tiers = {r[0]: i for i, r in enumerate(C.PROPERTY_TIERS)}
    assert upgrades.tier_index("kiosk") == 0 and upgrades.tier_index("flagship") == tiers["flagship"] == 4


def test_equipment_takes_a_slot_and_the_workbench_makes_room(layout, monkeypatch):
    E = env(layout)
    b = branch(E, mode="production")
    biz.fund(E.db, A, b["id"], 20_000_00, "f", E.now[0])
    prop = estate.get(E.db, b["property_id"])
    small = upgrades.tier_index(prop["tier"]) >= upgrades.tier_index("small")
    if not small:
        pytest.skip("the cheapest workshop of this test city is a kiosk")
    slots = upgrades.equipment_slots(E.db, b)
    assert slots == prop["equip_slots"] >= 2
    assert upgrades.install(E.db, A, b["id"], "machines", E.now[0]) == 1 and upgrades.equipment_used(E.db, b["id"]) == 1
    assert upgrades.install(E.db, A, b["id"], "machines", E.now[0]) == 2 and upgrades.equipment_used(E.db, b["id"]) == 1          # a better level of the same machine takes no more room
    monkeypatch.setattr(upgrades, "equipment_slots", lambda db, br: 1)
    with pytest.raises(GameError) as e:
        upgrades.install(E.db, A, b["id"], "cutting_table", E.now[0])
    assert e.value.code == "upg_slots"
    eff = upgrades.effects(E.db, b["id"])
    assert eff["speed"] == 2200 and eff["quality"] == 3 and eff["unlock"] == set()


def test_effects_add_up_and_the_sign_changes_the_look(layout):
    E = env(layout)
    b = branch(E)
    biz.fund(E.db, A, b["id"], 20_000_00, "f", E.now[0])
    assert upgrades.sign_style(E.db, b["id"]) == "plain" and upgrades.effects(E.db, b["id"])["attract"] == 0
    for lvl, style in ((1, "green"), (2, "neon"), (3, "two")):
        upgrades.install(E.db, A, b["id"], "sign", E.now[0])
        assert upgrades.sign_style(E.db, b["id"]) == style == biz.get(E.db, b["id"])["sign"]
    upgrades.install(E.db, A, b["id"], "presentation", E.now[0])
    upgrades.install(E.db, A, b["id"], "cameras", E.now[0])
    upgrades.install(E.db, A, b["id"], "checkout", E.now[0])
    upgrades.install(E.db, A, b["id"], "racks", E.now[0])
    upgrades.install(E.db, A, b["id"], "staffroom", E.now[0])
    eff = upgrades.effects(E.db, b["id"])
    assert eff["attract"] == 1500 + 300 and eff["rep_gain"] == 1000 and eff["selfcheck"] == 50_00 and eff["cap_disp"] == 2000 and eff["slots_staff"] == 1
    prop = estate.get(E.db, b["property_id"])
    assert upgrades.staff_slots(E.db, b) == prop["staff_slots"] + 1 and upgrades.display_cap(E.db, b) == prop["display_cap"] * 12000 // 10000


def test_a_managers_upgrade_needs_both_upgrade_and_spend(layout):
    E = env(layout)
    b = branch(E)
    biz.fund(E.db, A, b["id"], 2000_00, "f", E.now[0])
    rich(E, B)
    biz.register_perm_hook(lambda db, uid, br: {"upgrade"} if uid == B else set())
    try:
        with pytest.raises(GameError) as e:
            upgrades.install(E.db, B, b["id"], "shelving", E.now[0])
        assert e.value.code == "branch_denied"
        biz._PERM_HOOKS.clear()
        biz.register_perm_hook(lambda db, uid, br: {"upgrade", "spend"} if uid == B else set())
        assert upgrades.install(E.db, B, b["id"], "shelving", E.now[0]) == 1
    finally:
        biz._PERM_HOOKS.clear()


def test_the_catalog_is_valid_and_registering_twice_is_refused():
    assert upgrades.ORDER == tuple(upgrades.UPGRADES) and len(upgrades.UPGRADES) >= 10
    for u in upgrades.UPGRADES.values():
        assert u.id and u.effect in ("cap_wh", "cap_disp", "speed", "unlock", "attract", "rep_gain", "selfcheck", "slots_staff", "slots_equip")
        costs = [c for c, _ in u.levels]
        assert costs == sorted(costs) and all(c >= 100 for c in costs)
    with pytest.raises(ValueError):
        upgrades.register(upgrades.Upgrade("shelving", "cap_wh", ((100, 1),)))
    with pytest.raises(ValueError):
        upgrades.register(upgrades.Upgrade("x_bad", "cap_wh", ()))
    with pytest.raises(ValueError):
        upgrades.register(upgrades.Upgrade("x_bad2", "cap_wh", ((100, 1),), needs="space"))
