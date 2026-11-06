"""Style (v0.211): a rating from what a player really wears, and the saved outfits.

THE RATING (0 to 100) comes from the worn items only: each of the three main pieces (upper body, lower body, footwear) counts by the quality of the piece (`config.STYLE_Q_POINTS`: a piece without a
quality counts a little, one with five stars the most), a COMPLETE outfit (all three) adds a bonus, and headwear and accessories add a little each (at most two count). It does not depend on money:
a plain complete outfit beats a single expensive piece. Tiers (`STYLE_TIERS`): plain (nothing worn), simple, average, stylish, iconic.

WHAT IT DOES. (1) The player's marker on the city map: the better the outfit, the more decorated the marker (a ring for average, a double ring for stylish, a crown for iconic). Every marker stays fully
visible and the same size at least: nobody is ever hidden or made hard to find for dressing plainly. (2) A small, capped bonus in a customer-facing job: a Snapp passenger's chance to tip rises by up
to `STYLE_TIP_BPS_MAX` basis points at the top tier and never goes DOWN for plain clothes. Technical and production jobs, and McDonald's (it has its own uniform), do not look at it.

SAVED OUTFITS: one per kind (streetwear, casual, formal, work). Saving records what is worn; wearing it puts those pieces on again from the bag (a piece that is no longer there is skipped and
reported). Nothing is created or given by wearing an outfit.
"""
from __future__ import annotations

import json

from . import config as C, core, inventory, items
from .core import GameError

MAIN = ("upper", "lower", "footwear")
EXTRA = ("headwear", "accessory")


def rating(db, uid: int) -> dict:
    """{"points", "tier", "worn": {slot: (item id, data)}, "complete"} for the player."""
    worn = inventory.worn(db, uid)
    pts = 0
    for s in MAIN:
        w = worn.get(s)
        if w is not None:
            pts += C.STYLE_Q_POINTS.get(items.quality_of(w.data) or 0, C.STYLE_Q_POINTS[0])
    complete = all(s in worn for s in MAIN)
    if complete:
        pts += C.STYLE_COMPLETE_BONUS
    pts += C.STYLE_ACCESSORY_BONUS * min(2, sum(1 for s in EXTRA if s in worn))
    pts = max(0, min(100, pts))
    return {"points": pts, "tier": tier_of(pts) if worn else "plain", "worn": {s: (w.item_id, w.data) for s, w in worn.items()}, "complete": complete}


def tier_of(points: int) -> str:
    out = C.STYLE_TIERS[0][1]
    for lo, tid in C.STYLE_TIERS:
        if points >= lo:
            out = tid
    return out


def tier_index(tid: str) -> int:
    return [x[1] for x in C.STYLE_TIERS].index(tid)


def tip_bonus_bps(points: int) -> int:
    """The extra tipping chance (basis points) an outfit of that rating gives a Snapp driver: linear from nothing at 0 to the maximum at 100."""
    return max(0, min(100, points)) * C.STYLE_TIP_BPS_MAX // 100


def marker_tiers(db, uids) -> dict:
    """{player id: tier id} for the markers of a map picture (one pass for everybody on it)."""
    return {u: rating(db, u)["tier"] for u in uids}


# --- saved outfits -------------------------------------------------------------------------------------------------------------------------
def save_outfit(db, uid: int, kind: str, now: int | None = None) -> dict:
    """Save what is worn as the outfit `kind`. GameError: outfit_kind, outfit_empty (nothing worn)."""
    now = core.now() if now is None else now
    if kind not in C.OUTFIT_KINDS:
        raise GameError("outfit_kind")
    worn = inventory.worn(db, uid)
    if not worn:
        raise GameError("outfit_empty")
    slots = {s: [w.item_id, w.data] for s, w in sorted(worn.items())}
    db.x("INSERT INTO outfits(user_id,kind,slots,saved_at) VALUES(?,?,?,?) ON CONFLICT(user_id,kind) DO UPDATE SET slots=excluded.slots, saved_at=excluded.saved_at", (uid, kind, json.dumps(slots, sort_keys=True, ensure_ascii=False, separators=(",", ":")), now))
    return slots


def outfits(db, uid: int) -> dict:
    """{kind: {slot: [item id, data]}} of the saved outfits."""
    out = {}
    for r in db.qa("SELECT kind, slots FROM outfits WHERE user_id=?", (uid,)):
        try:
            out[r["kind"]] = json.loads(r["slots"])
        except ValueError:
            continue
    return out


def wear_outfit(db, uid: int, kind: str, now: int | None = None) -> dict:
    """Put on a saved outfit from the bag. Returns {"worn": [slots put on], "kept": [slots already right], "missing": [slots whose piece is not in the bag]}. GameError: outfit_kind, outfit_none."""
    now = core.now() if now is None else now
    if kind not in C.OUTFIT_KINDS:
        raise GameError("outfit_kind")
    saved = outfits(db, uid).get(kind)
    if saved is None:
        raise GameError("outfit_none")
    res = {"worn": [], "kept": [], "missing": []}
    with db.tx():
        for slot, (item_id, data) in saved.items():
            cur = inventory.worn(db, uid).get(slot)
            if cur is not None and cur.item_id == item_id and cur.data == data:
                res["kept"].append(slot)
                continue
            row = db.q1("SELECT id FROM item_inventory WHERE user_id=? AND item_id=? AND data IS ? ORDER BY id LIMIT 1", (uid, item_id, data))
            if row is None:
                res["missing"].append(slot)
                continue
            inventory.equip(db, uid, row["id"], now)
            res["worn"].append(slot)
    return res
