"""Material purchase ads (v0.211): a business that needs fabric says so, and any producer can fill the order. Player to player, with no auction house.

AN AD says: this branch buys FABRIC of at least `min_q` stars at `unit_cents` each, up to `qty` pieces, until it expires. Creating one RESERVES the whole budget (qty x unit): the money leaves the
branch's till at that moment and sits in the ad (`reserved`), so it is always there when a seller fulfils it. It is refused when the till cannot cover it (wages owed to the staff are not available), when
the branch has no workshop (retail only) or no room in its warehouse for the quantity, or when the branch already has `AD_MAX_OPEN` ads. What is not used goes back to the till when the ad is
cancelled or expires (`sweep`, run lazily by every screen and by the periodic job).

FULFILLING is one transaction: the guarded update of the ad's remaining quantity (two sellers can never fill the same remaining pieces: the second finds less left and is refused), the seller's fabric
leaving THEIR bag, the seller's cash arriving, the fabric entering the BRANCH WAREHOUSE (and its room is checked), one row of `ad_fills` (unique by its reference) and the branch's reputation. Either all of
it happens or none. The owner, the staff and the branch's manager can never fill their own branch's ad (no reputation or money for nothing).
"""
from __future__ import annotations

import secrets
import zlib

from . import biz, cash, config as C, core, estate, items, rep, staff, stock, textile
from .core import GameError

MATERIAL = "fabric"


def get(db, ad_id):
    if not isinstance(ad_id, int) or isinstance(ad_id, bool):
        return None
    return db.q1("SELECT * FROM material_ads WHERE id=?", (ad_id,))


def of_branch(db, branch_id: int, open_only: bool = False) -> list:
    q = "SELECT * FROM material_ads WHERE branch_id=?" + (" AND state='open'" if open_only else "") + " ORDER BY id DESC"
    return db.qa(q, (branch_id,))


def fills(db, ad_id: int) -> list:
    return db.qa("SELECT * FROM ad_fills WHERE ad_id=? ORDER BY id", (ad_id,))


def board(db, now: int | None = None, world_id: int | None = None, limit: int = C.AD_BOARD_PAGE, offset: int = 0, min_q: int | None = None) -> list:
    """Open ads that have not expired, newest first (one city when `world_id`); each row also carries the branch (`brand`, `seq`, `world_id`)."""
    now = core.now() if now is None else now
    sweep(db, now)
    q = ("SELECT a.*, b.brand, b.seq, b.world_id, b.owner_id FROM material_ads a JOIN branches b ON b.id=a.branch_id WHERE a.state='open' AND a.expires_at>?"
         + (" AND b.world_id=?" if world_id is not None else "") + (" AND a.min_q<=?" if min_q is not None else "") + " ORDER BY a.id DESC LIMIT ? OFFSET ?")
    args = [now] + ([world_id] if world_id is not None else []) + ([min_q] if min_q is not None else []) + [limit, offset]
    return db.qa(q, tuple(args))


def count_open(db, now: int | None = None, world_id: int | None = None) -> int:
    now = core.now() if now is None else now
    q = "SELECT COUNT(*) c FROM material_ads a JOIN branches b ON b.id=a.branch_id WHERE a.state='open' AND a.expires_at>?" + (" AND b.world_id=?" if world_id is not None else "")
    return db.q1(q, (now,) + ((world_id,) if world_id is not None else ()))["c"]


def create(db, uid: int, branch_id: int, min_q: int, unit_cents: int, qty: int, ttl: int, now: int | None = None):
    """Publish an ad. Owner or a manager with `ads` (and `spend`). GameError: branch_gone, branch_denied, ad_mode, ad_terms, ad_open (too many), wh_full, till_low, till_reserved."""
    now = core.now() if now is None else now
    ok = lambda v, lo, hi: isinstance(v, int) and not isinstance(v, bool) and lo <= v <= hi             # noqa: E731
    if not (ok(min_q, 1, 5) and ok(unit_cents, 1, C.AD_MAX_UNIT_CENTS) and ok(qty, 1, C.AD_MAX_QTY) and ttl in C.AD_TTL_CHOICES):
        raise GameError("ad_terms")
    with db.tx():
        sweep(db, now)
        b = biz.need(db, uid, branch_id, "ads")
        if not (b["owner_id"] == uid or biz.can(db, uid, b, "spend")):
            raise GameError("branch_denied")
        prop = biz.property_of(db, b)
        if not estate.mode_allows(prop["mode"], "production"):
            raise GameError("ad_mode")
        if db.q1("SELECT COUNT(*) c FROM material_ads WHERE branch_id=? AND state='open'", (branch_id,))["c"] >= C.AD_MAX_OPEN:
            raise GameError("ad_open")
        if qty > stock.free_warehouse(db, b):
            raise GameError("wh_full", free=stock.free_warehouse(db, b))
        budget = unit_cents * qty
        if budget > biz.spendable(db, b, now):
            raise GameError("till_low" if budget > b["till"] else "till_reserved", need=budget, have=b["till"])
        cur = db.x("INSERT INTO material_ads(branch_id,buyer_id,material,min_q,unit_cents,qty_total,qty_left,reserved,state,expires_at,created_at) VALUES(?,?,?,?,?,?,?,?,'open',?,?)",
                   (branch_id, uid, MATERIAL, min_q, unit_cents, qty, qty, budget, now + ttl, now))
        ad_id = cur.lastrowid
        if biz.till_apply(db, branch_id, -budget, "ad_reserve", f"ad{ad_id}", actor=uid, note=f"ad {ad_id}", now=now) is None:
            raise RuntimeError("an ad budget was reserved twice")
        estate.event(db, "ad_created", str(ad_id), uid, {"branch": branch_id, "min_q": min_q, "unit": unit_cents, "qty": qty}, now)
    return get(db, ad_id)


def _close(db, ad, state: str, now: int, actor: int | None) -> int:
    """Close an open ad and return what is left of its budget to the till. Returns the cents returned."""
    back = ad["reserved"]
    if db.x("UPDATE material_ads SET state=?, reserved=0, closed_at=? WHERE id=? AND state='open'", (state, now, ad["id"])).rowcount != 1:
        return 0
    b = biz.get(db, ad["branch_id"])
    if back > 0 and b is not None:
        biz.till_apply(db, ad["branch_id"], back, "ad_refund", f"ad{ad['id']}", actor=actor, note=f"ad {ad['id']}", now=now, allow_closed=True)
    return back


def cancel(db, uid: int, ad_id: int, now: int | None = None) -> int:
    """Take an ad down: the unused budget returns to the till. Owner or a manager with `ads`. Returns the cents returned. GameError: ad_gone, branch_denied."""
    now = core.now() if now is None else now
    with db.tx():
        ad = get(db, ad_id)
        if ad is None or ad["state"] != "open":
            raise GameError("ad_gone")
        b = biz.get(db, ad["branch_id"])
        if b is None or not biz.can(db, uid, b, "ads"):
            raise GameError("branch_denied")
        return _close(db, ad, "cancelled", now, uid)


def sweep(db, now: int | None = None) -> int:
    """Expire every open ad whose time has passed and return its budget. Lazy and idempotent; returns how many ads it closed."""
    now = core.now() if now is None else now
    n = 0
    for ad in db.qa("SELECT * FROM material_ads WHERE state='open' AND expires_at<=?", (now,)):
        with db.tx():
            ad = get(db, ad["id"])
            if ad is not None and ad["state"] == "open":
                _close(db, ad, "expired", now, None)
                n += 1
    return n


def fill(db, uid: int, ad_id: int, quality: int, qty: int, now: int | None = None) -> dict:
    """Fulfil `qty` pieces of an ad with `quality`-star fabric from the player's own bag. Returns {"qty", "cents", "left", "balance"}.
    GameError: no_user, no_character, ad_gone (unknown, closed or expired), ad_self (the branch's own people), ad_quality (below the minimum), ad_left (more than is wanted),
    bad_amount, inv_few, wh_full (the warehouse has no room), cash_cap."""
    now = core.now() if now is None else now
    if not isinstance(qty, int) or isinstance(qty, bool) or qty <= 0 or not isinstance(quality, int) or isinstance(quality, bool):
        raise GameError("bad_amount")
    ref = secrets.token_hex(6)
    with db.tx():
        sweep(db, now)
        ad = get(db, ad_id)
        if ad is None or ad["state"] != "open" or ad["expires_at"] <= now:
            raise GameError("ad_gone")
        b = biz.get(db, ad["branch_id"])
        if b is None or b["state"] != "open":
            raise GameError("ad_gone")
        estate._human(db, uid)
        if uid == b["owner_id"] or staff.of_employee(db, uid) and any(c["branch_id"] == b["id"] for c in staff.of_employee(db, uid)):
            raise GameError("ad_self")
        if not ad["min_q"] <= quality <= 5:
            raise GameError("ad_quality")
        if qty > ad["qty_left"]:
            raise GameError("ad_left", left=ad["qty_left"])
        if qty > stock.free_warehouse(db, b):
            raise GameError("wh_full", free=stock.free_warehouse(db, b))
        total = ad["unit_cents"] * qty
        if db.x("UPDATE material_ads SET qty_left=qty_left-?, reserved=reserved-? WHERE id=? AND state='open' AND qty_left>=? AND reserved>=?", (qty, total, ad_id, qty, total)).rowcount != 1:
            raise GameError("ad_left", left=get(db, ad_id)["qty_left"])
        taken = textile.take_quality(db, uid, quality, qty, "ad_sale", f"{ad_id}:{ref}", now)
        bal = cash.apply(db, uid, total, "ad_sale", f"{ad_id}:{ref}", note=f"ad {ad_id}", now=now)
        if bal is None:
            raise RuntimeError("an ad was paid twice")
        db.x("INSERT INTO ad_fills(ad_id,seller_id,qty,unit_cents,total_cents,quality,ref,created_at) VALUES(?,?,?,?,?,?,?,?)", (ad_id, uid, qty, ad["unit_cents"], total, quality, f"{ad_id}:{ref}", now))
        fill_id = db.q1("SELECT id FROM ad_fills WHERE ref=?", (f"{ad_id}:{ref}",))["id"]
        for data, k in taken:
            stock.add(db, b, stock.WH, "fabric", data, k, "ad_in", f"{fill_id}:{zlib.crc32((data or '').encode()):08x}", actor=uid, now=now)
        left = ad["qty_left"] - qty
        if left == 0:
            db.x("UPDATE material_ads SET state='filled', closed_at=? WHERE id=? AND state='open'", (now, ad_id))
        rep.add(db, b["id"], "ad_fill", str(fill_id), C.REP_FILL, uid, now)
        estate.event(db, "ad_filled", str(fill_id), uid, {"ad": ad_id, "qty": qty, "cents": total}, now)
    return {"qty": qty, "cents": total, "left": left, "balance": bal}


def _on_close(db, branch, now: int) -> None:
    for ad in db.qa("SELECT * FROM material_ads WHERE branch_id=? AND state='open'", (branch["id"],)):
        _close(db, ad, "cancelled", now, branch["owner_id"])


biz.register_close_hook(_on_close, first=True)


def _blockers(db, prop) -> list:
    b = db.q1("SELECT id FROM branches WHERE property_id=? AND state='open'", (prop["id"],))
    return ["ads"] if b is not None and db.q1("SELECT 1 FROM material_ads WHERE branch_id=? AND state='open' LIMIT 1", (b["id"],)) else []


estate.register_blocker_hook(_blockers)
