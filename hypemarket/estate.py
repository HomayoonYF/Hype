"""Commercial property (v0.211): the lots of a saved city that can hold a business, how they are priced and described, and how a player comes to own one.

WHERE PROPERTIES COME FROM. A city's layout is never edited. A property is a row of `properties` that says «this vacant lot of this city is, for good, a commercial property with these attributes».
`ensure` makes the rows of a city the first time anybody looks (idempotent: UNIQUE(world, lot), INSERT OR IGNORE, so two callers at once make each row once). A lot is eligible when it is vacant, is in a
zone that has a rate in `config.PROPERTY_ZONE_RATE_CENTS` and is not already a public place (the loaded city has those as landmarks). The attributes are a pure function of the lot's geometry and
zone and of the tables in `config`: area in m², price ($250 to $50,000, from the area, the zone, the closeness to the middle of the city and a main-road frontage), the tier (kiosk ... flagship)
with its warehouse and display capacities, employee and equipment slots and highest product quality, and the business modes the zone permits. They are SAVED in the row, and a trigger keeps them fixed.

OWNING. A property starts as an EMPTY SHELL: no business mode, no brand, no branch. To inspect or buy one the player must be PHYSICALLY NEAR it (standing still within `config.BIZ_NEAR_UNITS` of its door:
decided on the server from the saved position, never from a button). `buy` charges the price in ONE transaction with the guarded change of owner (`WHERE owner_id IS NULL`), so two buyers racing for one
lot cannot both get it and a failed purchase charges nothing. The owner then FITS the shell for a business mode (retail only, production only, or both: only the modes the lot permits) for a fee,
and converting it to another mode later costs a lot more. Everything is paid from life cash through `cash.apply` (unique per player, kind and property: a repeated press pays once).
"""
from __future__ import annotations

import json
import sqlite3

from . import cash, character, config as C, core, worldgeom as G
from .core import GameError

NO_SIGN = "plain"


# =====================================================================================================================
#                                                       attributes (pure)
# =====================================================================================================================
def area_m2(parcel: dict) -> int:
    return max(1, int(round(C.to_square_metres(float(parcel["area"])))))


def tier_of(m2: int) -> tuple:
    """The tier of a lot of `m2` square metres: the LAST tier of `config.PROPERTY_TIERS` whose smallest area it reaches."""
    out = C.PROPERTY_TIERS[0]
    for row in C.PROPERTY_TIERS:
        if m2 >= row[1]:
            out = row
    return out


def modes_of(zone: str) -> tuple:
    return tuple(C.PROPERTY_ZONE_MODES.get(zone, ()))


def eligible(city) -> list:
    """The lots of the loaded city that can be a property: vacant (public places are landmarks in the loaded city), in a zone with a rate."""
    return [p for p in city.layout["parcels"] if p["use"] == "vacant" and p["zone"] in C.PROPERTY_ZONE_RATE_CENTS and p["zone"] in C.PROPERTY_ZONE_MODES]


def _centre_and_reach(city) -> tuple:
    centre = G.centroid(city.base["footprint"])
    reach = max((G.dist(p["c"], centre) for p in city.layout["parcels"]), default=1.0) or 1.0
    return centre, reach


def price_of(city, parcel: dict, m2: int, ctx: tuple | None = None) -> int:
    """The price in cents: area x the zone's rate, scaled up towards the middle of the city and for a main-road frontage, rounded to `PROPERTY_ROUND_CENTS` and kept within the legal range."""
    centre, reach = ctx or _centre_and_reach(city)
    lo, hi = C.PROPERTY_CENTRE_BPS
    closeness = 1.0 - min(1.0, G.dist(parcel["c"], centre) / reach)
    centre_bps = lo + int(round((hi - lo) * closeness))
    price = m2 * C.PROPERTY_ZONE_RATE_CENTS[parcel["zone"]] * centre_bps // 10000
    door = city.net.door(parcel["id"], parcel["c"])
    if city.net.segs[door.seg].kind == "main" and door.off <= 24.0:
        price = price * C.PROPERTY_FRONT_BPS // 10000
    step = C.PROPERTY_ROUND_CENTS
    price = (price + step // 2) // step * step
    return max(C.PROPERTY_PRICE_MIN_CENTS, min(C.PROPERTY_PRICE_MAX_CENTS, price))


def attributes(city, parcel: dict, ctx: tuple | None = None) -> dict:
    """Everything a property row holds, worked out from the lot (pure: the same lot always gives the same dict)."""
    m2 = area_m2(parcel)
    tier = tier_of(m2)
    mult = C.PROPERTY_ZONE_MULT_BPS.get(parcel["zone"], {})
    wh = tier[2] * mult.get("warehouse", 10000) // 10000
    disp = tier[3] * mult.get("display", 10000) // 10000
    return {"zone": parcel["zone"], "area_m2": m2, "tier": tier[0], "price": price_of(city, parcel, m2, ctx), "warehouse_cap": wh, "display_cap": disp,
            "staff_slots": tier[4], "equip_slots": tier[5], "max_quality": min(tier[6], C.COMMERCIAL_MAX_QUALITY), "modes": ",".join(modes_of(parcel["zone"]))}


# =====================================================================================================================
#                                                       reading
# =====================================================================================================================
def get(db, property_id):
    if not isinstance(property_id, int) or isinstance(property_id, bool):
        return None
    return db.q1("SELECT * FROM properties WHERE id=?", (property_id,))


def by_parcel(db, world_id: int, parcel_id: str):
    return db.q1("SELECT * FROM properties WHERE world_id=? AND parcel_id=?", (world_id, parcel_id))


def of_owner(db, uid: int) -> list:
    return db.qa("SELECT * FROM properties WHERE owner_id=? ORDER BY bought_at, id", (uid,))


def for_sale(db, world_id: int) -> list:
    return db.qa("SELECT * FROM properties WHERE world_id=? AND owner_id IS NULL ORDER BY price, id", (world_id,))


def listing(db, world_id: int) -> list:
    return db.qa("SELECT * FROM properties WHERE world_id=? ORDER BY id", (world_id,))


def modes(prop) -> tuple:
    return tuple(m for m in prop["modes"].split(",") if m)


def mode_allows(prop_mode: str | None, need: str) -> bool:
    """Does a property fitted for `prop_mode` allow the activity `need` ('retail' or 'production')? An unfitted shell allows neither."""
    return prop_mode in ("both", need)


def allowed_choices(prop) -> tuple:
    """The business modes an owner may fit the lot for: the zone's own list (each of retail / production / both it permits)."""
    return modes(prop)


def ensure(db, city, now: int | None = None) -> int:
    """Make the property rows this city is missing (idempotent). Returns how many rows were created now."""
    now = core.now() if now is None else now
    todo = [p for p in eligible(city)]
    have = {r["parcel_id"] for r in db.qa("SELECT parcel_id FROM properties WHERE world_id=?", (city.id,))}
    todo = [p for p in todo if p["id"] not in have]
    if not todo:
        return 0
    ctx = _centre_and_reach(city)
    made = 0
    with db.tx():
        for p in sorted(todo, key=lambda q: int(q["id"][1:]) if q["id"][1:].isdigit() else 0):
            a = attributes(city, p, ctx)
            cur = db.x("INSERT OR IGNORE INTO properties(world_id,parcel_id,zone,area_m2,tier,price,warehouse_cap,display_cap,staff_slots,equip_slots,max_quality,modes,created_at) "
                       "VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)", (city.id, p["id"], a["zone"], a["area_m2"], a["tier"], a["price"], a["warehouse_cap"], a["display_cap"],
                                                             a["staff_slots"], a["equip_slots"], a["max_quality"], a["modes"], now))
            made += cur.rowcount
    return made


def near(city, st, prop) -> bool:
    """Is the player (the `presence.Standing` `st`) physically near this property? Standing still (not on a walk) and either arrived at it or within `BIZ_NEAR_UNITS` of its door along the roads.
    A property of another city is never near (the standing is in the player's own city)."""
    if prop["world_id"] != city.id or st.trip is not None:
        return False
    spot = city.spot(prop["parcel_id"])
    if spot is None:
        return False
    if st.row["at_place"] == spot.id:
        return True
    d = city.net.field(st.pos, cutoff=C.BIZ_NEAR_UNITS * 3 + 1).to(spot.door)
    return d <= C.BIZ_NEAR_UNITS


def event(db, kind: str, subject: str, actor: int | None, data: dict | None, now: int) -> None:
    db.x("INSERT INTO biz_events(kind,subject,actor,data,created_at) VALUES(?,?,?,?,?)", (kind, subject, actor, json.dumps(data or {}, sort_keys=True, separators=(",", ":")), now))


def _human(db, uid: int):
    u = core.get_user(db, uid)
    if u is None or u["bot"]:
        raise GameError("no_user")
    ch = character.get(db, uid)
    if ch is None or not character.is_done(ch):
        raise GameError("no_character")
    return u


# =====================================================================================================================
#                                                       owning
# =====================================================================================================================
def buy(db, city, st, uid: int, property_id: int, now: int | None = None):
    """Buy an unowned property. The player must be a finished human character, physically near it, and able to pay. All in one transaction: the price is charged and the owner set together or not at all.
    GameError: no_user, no_character, prop_gone (unknown / not this city's), prop_far, prop_taken (someone owns it), cash_low."""
    now = core.now() if now is None else now
    with db.tx():
        _human(db, uid)
        prop = get(db, property_id)
        if prop is None or prop["world_id"] != city.id:
            raise GameError("prop_gone")
        if prop["owner_id"] is not None:
            raise GameError("prop_taken")
        if not near(city, st, prop):
            raise GameError("prop_far")
        if cash.apply(db, uid, -prop["price"], "property_buy", str(prop["id"]), now=now) is None:
            raise GameError("prop_taken")                                          # this very purchase was already made
        if db.x("UPDATE properties SET owner_id=?, bought_at=?, bought_price=? WHERE id=? AND owner_id IS NULL", (uid, now, prop["price"], prop["id"])).rowcount != 1:
            raise GameError("prop_taken")                                          # lost the race: the transaction rolls the charge back
        event(db, "property_bought", str(prop["id"]), uid, {"price": prop["price"], "parcel": prop["parcel_id"], "world": prop["world_id"]}, now)
    return get(db, property_id)


def fit_cost(prop) -> int:
    return max(C.FITOUT_MIN_CENTS, prop["price"] * C.FITOUT_BPS // 10000)


def convert_cost(prop) -> int:
    return max(C.MODE_CHANGE_MIN_CENTS, prop["price"] * C.MODE_CHANGE_BPS // 10000)


def set_mode(db, uid: int, property_id: int, mode: str, now: int | None = None):
    """Fit an empty shell for `mode` (the first choice, the fit-out fee), or convert a fitted property to another mode (the conversion fee, and nothing may be in the way: see `convert_blockers`).
    Owner only; the mode must be one the lot permits. GameError: prop_gone, prop_notyours, mode_bad, mode_same, mode_busy, cash_low."""
    now = core.now() if now is None else now
    with db.tx():
        prop = get(db, property_id)
        if prop is None:
            raise GameError("prop_gone")
        if prop["owner_id"] != uid:
            raise GameError("prop_notyours")
        if mode not in C.BUSINESS_MODES or mode not in allowed_choices(prop):
            raise GameError("mode_bad")
        if prop["mode"] == mode:
            raise GameError("mode_same")
        first = prop["mode"] is None
        if not first:
            blockers = convert_blockers(db, prop)
            if blockers:
                raise GameError("mode_busy", why=blockers[0])
        fee = fit_cost(prop) if first else convert_cost(prop)
        kind = "property_fit" if first else "property_convert"
        ref = f"{prop['id']}:{mode}:{now}" if not first else str(prop["id"])
        if cash.apply(db, uid, -fee, kind, ref, now=now) is None:
            raise GameError("mode_same")
        if db.x("UPDATE properties SET mode=?, fitted_at=? WHERE id=? AND owner_id=? AND mode IS ?", (mode, now, prop["id"], uid, prop["mode"])).rowcount != 1:
            raise GameError("mode_same")
        event(db, "property_fitted" if first else "property_converted", str(prop["id"]), uid, {"mode": mode, "from": prop["mode"], "fee": fee}, now)
    return get(db, property_id)


_BLOCKERS: list = []


def register_blocker_hook(fn) -> None:
    """Later modules (stock, production, staff) register a function `fn(db, property_row) -> [reason, ...]` that says what keeps a property from being converted to another mode."""
    if fn not in _BLOCKERS:
        _BLOCKERS.append(fn)


def convert_blockers(db, prop) -> list:
    """Why a fitted property cannot be converted right now (empty = it can): anything a branch still holds that the new mode would strand (open production, goods in stock, owed wages ...)."""
    from . import market, stock, tailor  # noqa: F401  (importing them registers their hooks: a property can never be converted just because a module was not loaded yet)
    out = []
    for fn in _BLOCKERS:
        out += list(fn(db, prop))
    return out


def summary(prop) -> dict:
    """A plain dict of a property's attributes for the screens."""
    return {k: prop[k] for k in ("id", "world_id", "parcel_id", "zone", "area_m2", "tier", "price", "warehouse_cap", "display_cap", "staff_slots", "equip_slots", "max_quality", "modes", "mode", "owner_id")}
