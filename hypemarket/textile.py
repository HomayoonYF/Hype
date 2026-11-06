"""Cotton processing (v0.211): the side job that turns time into FABRIC, a real inventory item with a quality.

WHERE. Every city has one park (the single parcel of the park zone). That parcel is the cotton site: a pure function of the saved layout (`site`), so no map is regenerated and no layout is edited; a
player walks there through the ordinary map (the park's own id, like any place), and the picture shows a cotton pin on it. The job works only while the player STANDS at the site.

THE WORK. The player picks a GRADE, the quality of the fabric: 1 to 5 stars. Each grade needs a Textile skill (the better the grade, the higher), takes a fixed time and gives a fixed number of units
(`config.COTTON_GRADES`): the better the grade, the much longer it takes. The skill of the player at the START changes the time (up to `COTTON_SPEED_BPS_MAX` faster) and the yield (up to
`COTTON_YIELD_BPS_MAX` more, rounded down): these numbers are fixed then and saved in the task, so the confirmation screen is the truth and a level-up in the middle changes nothing. While the batch
runs the worker stays at the site (a batch can be given up, for nothing). The fabric exists only when the server clock says the batch is done AND the player collects it: collecting is one guarded
update (once), puts the fabric in the bag (a full bag leaves the batch waiting), gives character XP and the Textile skill's work for the whole batch (separate, each once per batch) and nothing else.
Pressing buttons gives nothing: no batch, no fabric, no skill.

THE FABRIC. `fabric` is an item with data {"q": stars, "src": "cotton"}; the source also sits in the bag's own log (`job:cotton`, the task). A player can keep it, sell it to the game at the fixed
price of its quality (`sell_to_game`: `config.FABRIC_GAME_CENTS`), hand it to another business through a purchase ad (market.py) or use it in tailoring (tailor.py).
"""
from __future__ import annotations

import secrets
import zlib

from . import cash, charxp, config as C, core, inventory, items, presence, skills, work
from .core import GameError

SRC = "cotton"
JOB = work.COTTON


# =====================================================================================================================
#                                                       the site
# =====================================================================================================================
def site(city) -> dict | None:
    """The park parcel of the city (the biggest park parcel; the lowest id on a tie), or None for a city with no park."""
    parks = [p for p in city.layout["parcels"] if p["zone"] == "park"]
    if not parks:
        return None
    return min(parks, key=lambda p: (-p["area"], int(p["id"][1:]) if p["id"][1:].isdigit() else 0))


def site_id(city) -> str | None:
    p = site(city)
    return p["id"] if p else None


# =====================================================================================================================
#                                                       what a batch is worth now
# =====================================================================================================================
def grades() -> tuple:
    return tuple(sorted(C.COTTON_GRADES))


def unlocked(db, uid: int) -> tuple:
    lvl = skills.level_of(db, uid, "textile")
    return tuple(g for g in grades() if lvl >= C.COTTON_GRADES[g][0])


def terms(db, uid: int, grade: int) -> dict:
    """What a batch of `grade` is worth to THIS player right now: {"grade", "need", "level", "open", "seconds", "units", "xp", "base_seconds", "base_units", "price"}.
    Time and units follow the skill linearly in whole basis points (the time rounded up, the units down), so the skill never gives more than the configured maximum."""
    if grade not in C.COTTON_GRADES:
        raise GameError("cotton_grade")
    need, secs, units = C.COTTON_GRADES[grade]
    lvl = skills.level_of(db, uid, "textile")
    speed = skills.bonus_bps(lvl, C.COTTON_SPEED_BPS_MAX)
    extra = skills.bonus_bps(lvl, C.COTTON_YIELD_BPS_MAX)
    return {"grade": grade, "need": need, "level": lvl, "open": lvl >= need, "base_seconds": secs, "base_units": units,
            "seconds": max(60, -(-secs * (10000 - speed) // 10000)), "units": max(units, units * (10000 + extra) // 10000), "xp": C.COTTON_XP[grade], "price": C.FABRIC_GAME_CENTS[grade]}


def current(db, uid: int):
    t = work.current(db, uid)
    return t if t is not None and t["job"] == JOB else None


def state(task, now: int) -> str:
    """'working' | 'ready' (due, not collected) | 'collected' | 'none', from the SERVER clock and the saved due time only."""
    if task is None or task["job"] != JOB:
        return "none"
    if task["state"] == "done":
        return "collected"
    if task["state"] != "open":
        return "none"
    return "ready" if task["due_at"] is not None and task["due_at"] <= now else "working"


# =====================================================================================================================
#                                                       starting and collecting
# =====================================================================================================================
def start(db, city, uid: int, grade: int, now: int | None = None) -> tuple:
    """Start a batch of `grade`. (task, True) when started; (task, False) when the player already has a batch (a repeated press changes nothing). The player must be standing at the park.
    GameError: cotton_grade, cotton_nosite, job_busy, job_far, cotton_locked (the skill is too low)."""
    now = core.now() if now is None else now
    with db.tx():
        cur = current(db, uid)
        if cur is not None:
            return cur, False
        if grade not in C.COTTON_GRADES:
            raise GameError("cotton_grade")
        sid = site_id(city)
        if sid is None:
            raise GameError("cotton_nosite")
        work.check_start(db, uid, JOB, now)
        st = presence.stand(db, city, uid, now)
        if st.trip is not None or st.row["at_place"] != sid:
            raise GameError("job_far")
        tm = terms(db, uid, grade)
        if not tm["open"]:
            raise GameError("cotton_locked", need=tm["need"], have=tm["level"])
        tid = work._open(db, uid, city.id, JOB, kind=str(grade), stage="working", data={"place": sid, "grade": grade, "units": tm["units"], "secs": tm["seconds"], "lvl": tm["level"], "xp": tm["xp"]},
                         reward=tm["units"], now=now, started=now, due=now + tm["seconds"])
        return work.get_task(db, tid, uid), True


def claim(db, uid: int, tid: int, now: int | None = None) -> dict:
    """Collect a finished batch, exactly once: the fabric goes into the bag, the XP and the skill's work are recorded. GameError: job_stale, job_claimed, job_notready (carries the seconds left), inv_full
    (the bag has no room: the batch stays waiting and nothing changes)."""
    now = core.now() if now is None else now
    with db.tx():
        t = work.get_task(db, tid, uid)
        if t is None or t["job"] != JOB or t["state"] == "cancelled":
            raise GameError("job_stale")
        if t["state"] == "done":
            raise GameError("job_claimed")
        if t["due_at"] is None or t["due_at"] > now:
            raise GameError("job_notready", secs=max(1, (t["due_at"] or now) - now))
        d = work.data_of(t)
        grade, units, secs = int(d.get("grade", 1)), int(t["reward"]), int(d.get("secs", 0))
        try:
            rc = work._close(db, uid, tid, "working", [], {"job": JOB, "grade": grade, "units": units, "started": t["started_at"], "due": t["due_at"]}, now)
        except work._Stale:
            raise GameError("job_claimed")
        inventory.give(db, uid, "fabric", units, f"job:{JOB}", str(tid), {"q": grade, "src": SRC}, now)
        out = {"xp": 0, "level_from": skills.level_of(db, uid, "textile"), "level_to": skills.level_of(db, uid, "textile")}
        xp = int(d.get("xp", C.COTTON_XP.get(grade, 0)))
        if charxp.get(db, uid) is not None and xp > 0 and charxp.grant(db, uid, xp, f"job:{JOB}", str(tid), now) is not None:
            out["xp"] = xp
        moved = skills.grant_work(db, uid, "textile", skills.milli(secs), f"job:{JOB}", str(tid), now) if secs > 0 else None
        if moved is not None:
            out["level_from"], out["level_to"] = moved
        rc.update(out)
        rc["fabric"] = fabric_count(db, uid)
        return rc


def give_up(db, uid: int, tid: int, now: int | None = None) -> bool:
    """Give up a batch in progress: nothing is made, nothing is earned. True when this call ended it."""
    return work.cancel(db, uid, tid, now)


# =====================================================================================================================
#                                                       the fabric in the bag
# =====================================================================================================================
def fabric_variants(db, uid: int) -> list:
    """The player's fabric by variant: [{"data": canonical json, "q": stars (0 = none), "qty": pieces}] best quality first."""
    return [{"data": e.data, "q": items.quality_of(e.data) or 0, "qty": e.qty} for e in inventory.bag(db, uid) if e.item_id == "fabric"]


def fabric_count(db, uid: int) -> int:
    return inventory.count(db, uid, "fabric")


def fabric_by_quality(db, uid: int) -> dict:
    out: dict = {}
    for v in fabric_variants(db, uid):
        out[v["q"]] = out.get(v["q"], 0) + v["qty"]
    return out


def take_quality(db, uid: int, quality: int, n: int, source: str, ref: str, now: int) -> list:
    """Take `n` pieces of fabric of exactly `quality` stars out of the bag, from however many variants (best-stocked first, then by their data). Returns [(data text, pieces)]. GameError: inv_few.
    Each variant has its own reference (the bag's log is unique per item, source and reference)."""
    have = [v for v in fabric_variants(db, uid) if v["q"] == quality]
    if sum(v["qty"] for v in have) < n:
        raise GameError("inv_few", have=sum(v["qty"] for v in have), need=n)
    taken, left = [], n
    for v in sorted(have, key=lambda v: (-v["qty"], v["data"] or "")):
        if left <= 0:
            break
        k = min(left, v["qty"])
        if inventory.take_variant(db, uid, "fabric", v["data"], k, source, f"{ref}:{zlib.crc32((v['data'] or '').encode()):08x}", now) is None:
            raise GameError("inv_few", have=0, need=k)
        taken.append((v["data"], k))
        left -= k
    return taken


def sell_to_game(db, uid: int, quality: int, n: int, now: int | None = None) -> dict:
    """Sell `n` pieces of `quality`-star fabric to the game at the fixed price of that quality. One transaction: the fabric leaves the bag and the money arrives, or nothing happens.
    Returns {"qty", "cents", "balance"}. GameError: bad_amount, cotton_grade, inv_few, cash_cap."""
    now = core.now() if now is None else now
    if quality not in C.FABRIC_GAME_CENTS:
        raise GameError("cotton_grade")
    if not isinstance(n, int) or isinstance(n, bool) or n <= 0:
        raise GameError("bad_amount")
    ref = secrets.token_hex(6)
    with db.tx():
        take_quality(db, uid, quality, n, "fabric_sale", ref, now)
        cents = C.FABRIC_GAME_CENTS[quality] * n
        bal = cash.apply(db, uid, cents, "fabric_sale", ref, note=f"{quality}*", now=now)
        if bal is None:
            raise RuntimeError("a fabric sale was paid twice")
    return {"qty": n, "cents": cents, "balance": bal}
