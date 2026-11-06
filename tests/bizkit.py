"""Shared set-up of the v0.211 business tests: a real generated city with its properties, rich players, and a branch in a few calls."""
import copy

import charkit
import test_life_jobs as LJ
from hypemarket import biz, brands, cash, config as C, core, estate, presence, upgrades, world, worldplaces

A, B = LJ.A, LJ.B
OWNER, WORKER, CUSTOMER, CUST2 = A, B, 51003, 51004
T0 = LJ.T0
BRAND = "nike"


def env(layout, chat=LJ.CH, users=(A, B, 51003, 51004, 51005)):
    E = LJ.make_env(layout, chat=chat)
    for u in users:
        core.ensure_user(E.db, u, f"u{u}", f"User{u}", lang="fa")
        charkit.complete(E.db, u)
    estate.ensure(E.db, E.city, T0)
    return E


def rich(E, uid, dollars=100_000):
    cash.ensure_account(E.db, uid)
    cash.apply(E.db, uid, dollars * 100, "grant", f"kit-{uid}-{dollars}-{E.now[0]}-{len(E.ledger(uid))}", now=E.now[0])


def free_props(E, zone, mode):
    return sorted((p for p in estate.listing(E.db, E.city.id) if p["zone"] == zone and p["owner_id"] is None and mode in C.PROPERTY_ZONE_MODES[zone]), key=lambda p: (p["price"], p["id"]))


def teleport(E, uid, ref):
    """Put the player at a place without a walk (the walking is tested elsewhere): saved position and arrival, like a finished trip."""
    spot = E.city.spot(ref)
    presence.stand(E.db, E.city, uid, E.now[0])                                                      # (makes the player's row on first use)
    E.db.x("UPDATE world_players SET x=?, y=?, at_place=? WHERE world_id=? AND user_id=?", (spot.door.pt[0], spot.door.pt[1], spot.id, E.city.id, uid))
    return presence.stand(E.db, E.city, uid, E.now[0])


def stand(E, uid):
    return presence.stand(E.db, E.city, uid, E.now[0])


def make_branch(E, uid=OWNER, *, zone="commercial", mode="both", brand=BRAND, skip=(), fund=0, level=None, tier=None):
    """Buy a lot, fit it, register `brand` (once) and open a branch; `fund` dollars go into the till. Returns the branch row."""
    rich(E, uid)
    rows = [p for p in free_props(E, zone, mode) if p["id"] not in skip and (tier is None or p["tier"] == tier)]
    p = rows[0]
    teleport(E, uid, p["parcel_id"])
    estate.buy(E.db, E.city, stand(E, uid), uid, p["id"], E.now[0])
    estate.set_mode(E.db, uid, p["id"], mode, E.now[0])
    if brands.get(E.db, brand) is None:
        brands.register(E.db, uid, brand, E.now[0])
    b = biz.open_branch(E.db, uid, p["id"], brand, E.now[0])
    if fund:
        biz.fund(E.db, uid, b["id"], fund * 100, f"kit{E.now[0]}", E.now[0])
    return biz.get(E.db, b["id"])


def install(E, uid, b, kind, times=1):
    for _ in range(times):
        upgrades.install(E.db, uid, b["id"], kind, E.now[0])


def park(E):
    from hypemarket import textile
    return textile.site_id(E.city)
