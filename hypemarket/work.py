"""Side jobs (v0.18): ONE framework, three jobs (Snapp, McDonald's, شهرداری / municipal work).

What a job is
    A job is a row of `work_tasks`. A player has AT MOST ONE open task at a time, whatever the job (a partial UNIQUE index, so two overlapping
    shifts, two rides or a ride plus a shift are impossible even if two requests race). The row says who, in which city (`world_id`), which
    job and sub-kind, which STAGE it is in, the server-computed terms (`reward`, `cost`), the stage data (`data`, JSON) and the timestamps
    (`created_at`, `started_at`, `due_at`, `finished_at`). Everything a job needs to continue lives in that row: nothing is held in memory,
    there is no timer or background task whose loss could lose a job or a payment, and a restart changes nothing. Time-dependent things
    (a shift being due, a car having arrived) are evaluated LAZILY from the saved timestamps / the saved trip the next time anybody looks.

Money
    Every payout goes through `_close`: the task is flipped open -> done by a guarded `UPDATE ... WHERE state='open' AND stage=?` and the
    cash ledger rows are written in the SAME transaction (`cash.apply`, UNIQUE per (player, kind, task id)). A repeated or concurrent
    completion matches nothing, so it can never pay twice. Amounts are never read from a button or from typed text: a button carries only
    the task id (and, for municipal offers, the kind and the offer round), and everything else is recomputed from saved state. Jobs pay
    LIFE CASH only (never stock money) and touch no stock level, XP, skill or portfolio.

The jobs
    * Snapp: rental car from the start. Each ride has a rental fee that is shown before the ride, is charged ONCE when the ride ends and is
      netted from the fare in the same transaction; the player must be able to afford it to start. A ride is planned from the saved city
      (pickup and destination are real parcels / public places reachable by road, the fare is base + per-distance on the road distance
      between them), then driven in two stages: to the pickup (the player must really arrive before they can pick the passenger up) and to
      the destination (arrival pays out). The car is the ordinary v0.17 trip with mode «car» (config.car_speed(): 40 km/h by default, walking is 20 km/h; every trip saves its own speed).
    * McDonald's: an 8-hour real-time shift. Applied for at the city's McDonald's (the player must have arrived there); start / due times are
      saved; the wage is collected with a claim that is allowed only once the SERVER clock has passed `due_at`.
    * Municipal tasks (رفتگری، جمع‌آوری زباله، نگهداری فضای سبز): one task at a time, a fixed reward per kind shown before accepting, a target
      the player must travel to and be standing at before the task can be completed.
"""
from __future__ import annotations

import hashlib
import json
import sqlite3
from dataclasses import dataclass

from . import cash, config as C, core, presence
from .core import GameError

SNAPP, MCD, MUNI = "snapp", "mcd", "muni"
JOB_IDS = (SNAPP, MCD, MUNI)
MUNI_KINDS = ("sweep", "waste", "green")
PASSENGERS = 8                       # how many passenger names the texts have (snapp.pax.0 ... 7)
CAR_MODEL = "pride"                  # v0.18.1: the rental car is a Saipa Pride, whatever the ride
CAR_YEARS = (1380, 1395)             # its model year, in the Iranian (Solar Hijri) calendar: 1380 ... 1395
CAR_COLOR_POOL = ("white", "white", "white", "silver", "silver", "black", "blue", "green", "red", "yellow")      # a plain white or silver one is the most common
CAR_COLORS = tuple(dict.fromkeys(CAR_COLOR_POOL))
MIN_RIDE = 120.0                     # a ride shorter than this (road units) is never planned, even in a tiny city
MIN_PICKUP = 60.0


@dataclass(frozen=True)
class JobDef:
    id: str
    needs_city: bool                 # played on the map of the chat's city (vs. collectable anywhere)
    cooldown: int                    # seconds between the end of one task of this job and the start of the next


def defs() -> dict:
    """The job table. Built on demand so the cooldowns follow the configuration."""
    return {SNAPP: JobDef(SNAPP, True, C.SNAPP_COOLDOWN), MCD: JobDef(MCD, True, 0), MUNI: JobDef(MUNI, True, C.MUNI_COOLDOWN)}


# =====================================================================================================================
#                                                       the framework
# =====================================================================================================================
def get_task(db, tid, uid: int):
    if not isinstance(tid, int) or isinstance(tid, bool):
        return None
    return db.q1("SELECT * FROM work_tasks WHERE id=? AND user_id=?", (tid, uid))


def current(db, uid: int):
    """The player's open task (any job), or None."""
    return db.q1("SELECT * FROM work_tasks WHERE user_id=? AND state='open'", (uid,))


def data_of(task) -> dict:
    try:
        d = json.loads(task["data"])
        return d if isinstance(d, dict) else {}
    except (TypeError, ValueError):
        return {}


def receipt_of(task) -> dict:
    try:
        d = json.loads(task["receipt"] or "{}")
        return d if isinstance(d, dict) else {}
    except (TypeError, ValueError):
        return {}


def history(db, uid: int, limit: int = 5) -> list:
    """The player's latest finished tasks, newest first."""
    return db.qa("SELECT * FROM work_tasks WHERE user_id=? AND state='done' ORDER BY finished_at DESC, id DESC LIMIT ?", (uid, limit))


def cooldown_left(db, uid: int, job: str, now: int | None = None) -> int:
    """Seconds until the player may start a task of `job` again (0 = now)."""
    now = core.now() if now is None else now
    cd = defs()[job].cooldown
    if cd <= 0:
        return 0
    r = db.q1("SELECT MAX(finished_at) FROM work_tasks WHERE user_id=? AND job=? AND finished_at IS NOT NULL", (uid, job))
    last = r[0] if r and r[0] else 0
    return max(0, last + cd - now)


def check_start(db, uid: int, job: str, now: int | None = None) -> None:
    """The rules every job shares. Raises GameError: job_busy (another task is open; carries that job's id) or job_cooldown."""
    now = core.now() if now is None else now
    if job not in defs():
        raise GameError("job_gone")
    cur = current(db, uid)
    if cur is not None:
        raise GameError("job_busy", job=cur["job"])
    left = cooldown_left(db, uid, job, now)
    if left > 0:
        raise GameError("job_cooldown", secs=left)


def _open(db, uid: int, world_id: int, job: str, *, kind: str = "", stage: str, data: dict, reward: int = 0, cost: int = 0, now: int,
          started: int | None = None, due: int | None = None) -> int:
    """Create the player's open task. The partial UNIQUE index is the final word: a second open task cannot exist."""
    try:
        cur = db.x("INSERT INTO work_tasks(user_id,world_id,job,kind,stage,state,data,reward,cost,paid,receipt,created_at,started_at,due_at) VALUES(?,?,?,?,?,'open',?,?,?,0,'',?,?,?)",
                   (uid, world_id, job, kind, stage, json.dumps(data, separators=(",", ":")), int(reward), int(cost), now, started, due))
    except sqlite3.IntegrityError:
        other = current(db, uid)
        raise GameError("job_busy", job=other["job"] if other else job)
    return cur.lastrowid


class _Stale(Exception):
    """The guarded UPDATE matched nothing: the task is no longer in the stage the button was drawn for."""


def _close(db, uid: int, tid: int, stage: str, lines: list, receipt: dict, now: int) -> dict:
    """Finish an open task ONCE and pay it. `lines` = [(ledger kind, signed cents), ...] applied in order. The task flips open -> done first
    (guarded on its stage), then the ledger rows are written; all in one transaction, so either everything happens or nothing does, and a
    second call finds the task done and raises _Stale without touching anything."""
    with db.tx():
        if db.x("UPDATE work_tasks SET state='done', stage='done', paid=1, finished_at=? WHERE id=? AND user_id=? AND state='open' AND stage=?",
                (now, tid, uid, stage)).rowcount != 1:
            raise _Stale()
        for kind, delta in lines:
            if delta and cash.apply(db, uid, delta, kind, str(tid), now=now) is None:
                raise RuntimeError("a job payout was applied twice")          # impossible (the guard above); rolls everything back
        receipt = dict(receipt)
        receipt["balance"] = cash.balance(db, uid)
        db.x("UPDATE work_tasks SET receipt=? WHERE id=?", (json.dumps(receipt, separators=(",", ":")), tid))
    return receipt


def _stop(db, uid: int, tid: int, stage: str | None, now: int) -> bool:
    """Cancel an open task (guarded). True when this call cancelled it."""
    q = "UPDATE work_tasks SET state='cancelled', stage='cancelled', finished_at=? WHERE id=? AND user_id=? AND state='open'"
    args = [now, tid, uid]
    if stage is not None:
        q += " AND stage=?"
        args.append(stage)
    return db.x(q, tuple(args)).rowcount == 1


def cancel(db, uid: int, tid: int, now: int | None = None) -> bool:
    """Give up the open task. Snapp rides (and a car trip in progress) can be cancelled at no cost and no pay (the cooldown applies, so a ride
    cannot be re-rolled for free); municipal tasks too. A McDonald's shift cannot be cancelled: it is paid when it is due."""
    now = core.now() if now is None else now
    with db.tx():
        t = get_task(db, tid, uid)
        if t is None or t["state"] != "open":
            return False
        if t["job"] == MCD:
            raise GameError("job_nocancel")
        if not _stop(db, uid, tid, None, now):
            return False
        if t["job"] == SNAPP and t["stage"] in ("to_pickup", "to_dest"):
            trip = presence.walking_trip(db, t["world_id"], uid)
            if trip is not None and trip["mode"] == "car":
                presence.cancel_trip(db, t["world_id"], uid, now)
        return True


def _h(*parts) -> int:
    return int.from_bytes(hashlib.sha256("|".join(str(p) for p in parts).encode()).digest()[:8], "big")


def _ring(city, pos, spots, lo: float, hi: float, fallback_min: float) -> list:
    """[(road distance, spot)] of the spots a walker/driver at `pos` can reach with lo <= distance <= hi, sorted by (distance, id). When the
    city has none in that band the nearest reachable ones farther than `fallback_min` are used instead (a tiny city still gets work)."""
    field = city.net.field(pos, cutoff=max(hi, fallback_min) * 3 + 1)
    seen, out = set(), []
    for s in spots:
        if s.id in seen:
            continue
        seen.add(s.id)
        d = field.to(s.door)
        if d != float("inf"):
            out.append((d, s))
    out.sort(key=lambda x: (round(x[0], 3), x[1].id))
    band = [x for x in out if lo <= x[0] <= hi]
    if band:
        return band
    return [x for x in out if x[0] >= fallback_min][:8]


# =====================================================================================================================
#                                                        Snapp
# =====================================================================================================================
def snapp_fare(dist: float) -> int:
    """The base fare in cents for a ride of `dist` road units: base + per-100-units, in integers (the distance is rounded to a whole unit,
    the per-distance part rounds DOWN to a whole cent)."""
    d = max(0, int(round(dist)))
    return C.SNAPP_BASE_FARE_CENTS + (C.SNAPP_PER_100_CENTS * d) // 100


def snapp_tip(seed, uid: int, tid: int) -> int:
    """The passenger's tip in cents, 0..SNAPP_TIP_MAX_CENTS: a hash of the city, the driver and the ride, so it is fixed by the saved ride
    (repeating the completion can never re-roll it) and cannot depend on anything the player sends."""
    num, den = C.SNAPP_TIP_ODDS
    if C.SNAPP_TIP_MAX_CENTS < 1 or den < 1:
        return 0
    h = _h("snapp-tip", seed, uid, tid)
    if h % den >= num:
        return 0
    return 1 + (h // den) % C.SNAPP_TIP_MAX_CENTS


def plan_ride(city, uid: int, pos, serial: int):
    """(pickup spot, destination spot, road distance to the pickup, road distance of the ride, passenger index) or None. Pure: the same city,
    driver, position and ride number give the same ride. Pickup and destination are different real parcels / public places connected by roads
    (bridges included, as everywhere), the ride is never meaninglessly short."""
    spots = city.spots("parcel")
    lo, hi = C.SNAPP_PICKUP_RANGE
    cand = _ring(city, pos, spots, lo, hi, MIN_PICKUP)
    if not cand:
        return None
    seed = city.base["seed"]
    d1, pick = cand[_h("snapp-pickup", seed, uid, serial) % len(cand)]
    rlo, rhi = C.SNAPP_RIDE_RANGE
    cand2 = [(d, s) for d, s in _ring(city, pick.door.pt, spots, rlo, rhi, MIN_RIDE) if s.id != pick.id]
    if not cand2:
        return None
    d2, dest = cand2[_h("snapp-dest", seed, uid, serial) % len(cand2)]
    return pick, dest, d1, d2, _h("snapp-pax", seed, uid, serial) % PASSENGERS


def car_for(seed, uid: int, serial: int) -> dict:
    """The rental car of a ride: {"y": model year (1380...1395), "c": colour}. A hash of the city, the driver and the ride number, so it is fixed
    by the ride and nothing a player sends can change it."""
    lo, hi = CAR_YEARS
    h = _h("snapp-car", seed, uid, serial)
    return {"y": lo + h % (hi - lo + 1), "c": CAR_COLOR_POOL[(h // 97) % len(CAR_COLOR_POOL)]}


def car_of(task) -> dict:
    """The car of a Snapp task as {"y", "c"}: the one saved with the ride, or (for a ride saved before v0.18.1, which had none) one worked out from
    the driver and the task id. Always a valid model year and colour."""
    d = data_of(task)
    car = d.get("car")
    lo, hi = CAR_YEARS
    if isinstance(car, dict) and isinstance(car.get("y"), int) and lo <= car["y"] <= hi and car.get("c") in CAR_COLORS:
        return {"y": car["y"], "c": car["c"]}
    return car_for(0, task["user_id"], task["id"])


def snapp_request(db, city, uid: int, now: int | None = None):
    """Take a ride: plan it from the player's position and save it as an open task in stage «assigned». Nothing is charged yet; the fee is
    shown with the ride and charged once, when it ends. Raises GameError: job_busy, job_cooldown, job_walking (a trip is in progress),
    cash_low (cannot afford the rental fee), job_noride."""
    now = core.now() if now is None else now
    with db.tx():
        check_start(db, uid, SNAPP, now)
        st = presence.stand(db, city, uid, now)
        if st.trip is not None:
            raise GameError("job_walking")
        fee, bal = C.SNAPP_FEE_CENTS, cash.balance(db, uid)
        if bal < fee:
            raise GameError("cash_low", need=fee, have=bal)
        serial = db.q1("SELECT COUNT(*) FROM work_tasks WHERE user_id=? AND job=?", (uid, SNAPP))[0] + 1
        plan = plan_ride(city, uid, st.pos, serial)
        if plan is None:
            raise GameError("job_noride")
        pick, dest, d1, d2, pax = plan
        data = {"pickup": pick.id, "dest": dest.id, "d1": int(round(d1)), "d2": int(round(d2)), "pax": pax, "car": car_for(city.base["seed"], uid, serial)}
        tid = _open(db, uid, city.id, SNAPP, stage="assigned", data=data, reward=snapp_fare(d2), cost=fee, now=now)
        return get_task(db, tid, uid)


def _mine(db, city, uid: int, tid, job: str, stages: tuple):
    """The player's open task `tid` of `job` in one of `stages` in THIS city; GameError job_stale otherwise (an old button, a task that is
    over, another player's id, another city's task)."""
    t = get_task(db, tid, uid)
    if t is None or t["state"] != "open" or t["job"] != job or t["stage"] not in stages:
        raise GameError("job_stale")
    if t["world_id"] != city.id:
        raise GameError("job_othercity")
    return t


def _set_stage(db, tid: int, frm: str, to: str, now: int) -> None:
    if db.x("UPDATE work_tasks SET stage=?, started_at=COALESCE(started_at,?) WHERE id=? AND state='open' AND stage=?", (to, now, tid, frm)).rowcount != 1:
        raise _Stale()


def snapp_go(db, city, uid: int, tid: int, now: int | None = None):
    """Drive towards the next stop of the ride: the passenger («assigned» -> «to_pickup») or, once the passenger is aboard, the destination.
    The trip and the stage change are saved together. Pressed again while already driving there, nothing changes. A stop that turns out to
    be unreachable cancels the ride (no fee, no pay) and says so."""
    now = core.now() if now is None else now
    failed = None
    try:
        with db.tx():
            t = _mine(db, city, uid, tid, SNAPP, ("assigned", "to_pickup", "to_dest"))
            d = data_of(t)
            out = presence.start_trip(db, city, uid, d["dest"] if t["stage"] == "to_dest" else d["pickup"], now, mode="car")
            if out.status == "busy" and not out.same:
                raise GameError("job_walking")
            if out.status in ("unknown", "unreachable"):
                _stop(db, uid, tid, t["stage"], now)
                failed = "job_unreachable"
            elif t["stage"] == "assigned":
                _set_stage(db, tid, "assigned", "to_pickup", now)            # status "here": already standing at the pickup, no trip needed
            task = get_task(db, tid, uid)
    except _Stale:
        raise GameError("job_stale")
    if failed:
        raise GameError(failed)
    return task


def snapp_pickup(db, city, uid: int, tid: int, now: int | None = None):
    """Pick the passenger up («to_pickup» -> «to_dest») and start driving to the destination. Only possible once the player is REALLY standing
    at the pickup (the arrival is applied from the saved trip and the clock, never from the button)."""
    now = core.now() if now is None else now
    try:
        with db.tx():
            t = _mine(db, city, uid, tid, SNAPP, ("to_pickup",))
            d = data_of(t)
            st = presence.stand(db, city, uid, now)
            if st.trip is not None or st.row["at_place"] != d["pickup"]:
                raise GameError("job_far")
            out = presence.start_trip(db, city, uid, d["dest"], now, mode="car")
            if out.status in ("busy", "here", "unknown", "unreachable"):
                raise GameError("job_walking" if out.status == "busy" else "job_unreachable")
            _set_stage(db, tid, "to_pickup", "to_dest", now)
            return get_task(db, tid, uid)
    except _Stale:
        raise GameError("job_stale")


def snapp_settle(db, city, uid: int, tid: int, now: int | None = None):
    """The ride's end: if the car has really arrived at the destination, pay base fare + tip and charge the rental fee, once, in one
    transaction. Returns the receipt dict, or None when there is nothing to pay (not at the destination yet / already paid)."""
    now = core.now() if now is None else now
    t = get_task(db, tid, uid)
    if t is None or t["state"] != "open" or t["job"] != SNAPP or t["stage"] != "to_dest" or t["world_id"] != city.id:
        return None
    d = data_of(t)
    st = presence.stand(db, city, uid, now)
    if st.trip is not None or st.row["at_place"] != d.get("dest"):
        return None
    fare, tip = int(t["reward"]), snapp_tip(city.base["seed"], uid, tid)
    fee = min(int(t["cost"]), fare + tip)                                       # a ride never costs the driver money (see config invariants)
    receipt = {"job": SNAPP, "fare": fare, "tip": tip, "fee": fee, "net": fare + tip - fee, "d2": d.get("d2"), "pax": d.get("pax"),
               "pickup": d.get("pickup"), "dest": d.get("dest"), "car": car_of(t)}
    try:
        return _close(db, uid, tid, "to_dest", [("snapp_fare", fare), ("snapp_tip", tip), ("snapp_fee", -fee)], receipt, now)
    except _Stale:
        return None


# =====================================================================================================================
#                                                     McDonald's
# =====================================================================================================================
def mcd_start(db, city, uid: int, now: int | None = None) -> tuple:
    """Start the 8-hour shift. (task, True) when started; (task, False) when the player already has THIS shift (a repeated press changes
    nothing). The player must be standing at the city's McDonald's. Raises GameError: job_nomcd, job_busy, job_far."""
    now = core.now() if now is None else now
    with db.tx():
        mid = city.mcd_id
        if mid is None:
            raise GameError("job_nomcd")
        cur = current(db, uid)
        if cur is not None and cur["job"] == MCD:
            return cur, False
        check_start(db, uid, MCD, now)
        st = presence.stand(db, city, uid, now)
        if st.trip is not None or st.row["at_place"] != mid:
            raise GameError("job_far")
        tid = _open(db, uid, city.id, MCD, stage="shift", data={"place": mid}, reward=C.MCD_WAGE_CENTS, now=now, started=now, due=now + C.MCD_SHIFT_SECONDS)
        return get_task(db, tid, uid), True


def shift_state(task, now: int) -> str:
    """'working' | 'ready' (due, not collected) | 'collected' | 'none'. Decided by the SERVER clock and the saved due time only."""
    if task is None or task["job"] != MCD:
        return "none"
    if task["state"] == "done":
        return "collected"
    if task["state"] != "open":
        return "none"
    return "ready" if task["due_at"] is not None and task["due_at"] <= now else "working"


def mcd_claim(db, uid: int, tid: int, now: int | None = None) -> dict:
    """Collect the wage of a finished shift, exactly once. Raises GameError: job_stale (not yours / not a shift), job_claimed (already
    collected), job_notready (carries the seconds left)."""
    now = core.now() if now is None else now
    with db.tx():
        t = get_task(db, tid, uid)
        if t is None or t["job"] != MCD or t["state"] == "cancelled":
            raise GameError("job_stale")
        if t["state"] == "done":
            raise GameError("job_claimed")
        if t["due_at"] is None or t["due_at"] > now:
            raise GameError("job_notready", secs=max(1, (t["due_at"] or now) - now))
        wage = int(t["reward"])
        try:
            return _close(db, uid, tid, "shift", [("mcd_wage", wage)], {"job": MCD, "wage": wage, "started": t["started_at"], "due": t["due_at"]}, now)
        except _Stale:
            raise GameError("job_claimed")


def notify_ready(db, now: int | None = None) -> int:
    """Queue a one-time DM «your shift is over» for every shift that is due and was not announced yet. Lazy and restart-safe: the flag lives
    in the task row, set with a guarded UPDATE, so a restart neither loses nor repeats a notice. Returns how many were queued."""
    now = core.now() if now is None else now
    n = 0
    for t in db.qa("SELECT * FROM work_tasks WHERE job=? AND state='open' AND due_at IS NOT NULL AND due_at<=? LIMIT 200", (MCD, now)):
        d = data_of(t)
        if d.get("dm"):
            continue
        d2 = dict(d, dm=1)
        if db.x("UPDATE work_tasks SET data=? WHERE id=? AND data=? AND state='open'", (json.dumps(d2, separators=(",", ":")), t["id"], t["data"])).rowcount == 1:
            core.notify(db, t["user_id"], "shift_ready", cents=int(t["reward"]))
            n += 1
    return n


# =====================================================================================================================
#                                                  municipal tasks
# =====================================================================================================================
@dataclass(frozen=True)
class Offer:
    kind: str
    spot: object
    dist: float
    reward: int
    round: int


def muni_round(db, uid: int) -> int:
    """How many municipal tasks the player has accepted so far: the number of the offers they are looking at (it moves on with every accept,
    so a button of an old screen can never accept a newer offer)."""
    return db.q1("SELECT COUNT(*) FROM work_tasks WHERE user_id=? AND job=?", (uid, MUNI))[0]


def muni_candidates(city, kind: str) -> list:
    """The places a municipal task of `kind` may be at: street/pavement work at built or empty lots (never at public places or green space),
    waste at public places, green-space care at green parcels."""
    if kind == "waste":
        return city.spots("landmark")
    out = []
    for s in city.spots("parcel"):
        p = s.parcel
        if s.landmark is not None:
            continue
        if kind == "green" and p["use"] == "green":
            out.append(s)
        elif kind == "sweep" and p["use"] in ("building", "vacant"):
            out.append(s)
    return out


def muni_offers(db, city, uid: int, pos, now: int | None = None) -> list:
    """The player's current offers: at most one per kind, each a REAL reachable target 100-520 road units away, with its fixed reward. A pure
    function of the saved city, the player, their position and their offer round, so the same screen always shows the same offers."""
    rnd = muni_round(db, uid)
    lo, hi = C.MUNI_RANGE
    seed = city.base["seed"]
    out = []
    for kind in MUNI_KINDS:
        reward = C.MUNI_TASKS.get(kind)
        if not reward:
            continue
        cand = _ring(city, pos, muni_candidates(city, kind), lo, hi, MIN_PICKUP)
        if cand:
            d, s = cand[_h("muni", seed, uid, rnd, kind) % len(cand)]
            out.append(Offer(kind, s, d, int(reward), rnd))
    return out


def muni_accept(db, city, uid: int, kind: str, rnd, now: int | None = None):
    """Accept the offer of `kind` in round `rnd` (both from the button; everything else is recomputed here). One task at a time."""
    now = core.now() if now is None else now
    if kind not in MUNI_KINDS or not isinstance(rnd, int) or isinstance(rnd, bool):
        raise GameError("job_stale")
    with db.tx():
        check_start(db, uid, MUNI, now)
        st = presence.stand(db, city, uid, now)
        if st.trip is not None:
            raise GameError("job_walking")
        if rnd != muni_round(db, uid):
            raise GameError("job_stale")
        offer = next((o for o in muni_offers(db, city, uid, st.pos, now) if o.kind == kind), None)
        if offer is None:
            raise GameError("job_none")
        tid = _open(db, uid, city.id, MUNI, kind=kind, stage="travel", data={"target": offer.spot.id, "d": int(round(offer.dist))},
                    reward=offer.reward, now=now, started=now)
        return get_task(db, tid, uid)


def muni_go(db, city, uid: int, tid: int, now: int | None = None):
    """Start walking to the task's target (the ordinary v0.17 walk). Returns the presence Outcome. A target that cannot be reached cancels
    the task (no pay) and says so."""
    now = core.now() if now is None else now
    failed = None
    with db.tx():
        t = _mine(db, city, uid, tid, MUNI, ("travel",))
        out = presence.start_trip(db, city, uid, data_of(t)["target"], now)
        if out.status in ("unknown", "unreachable"):
            _stop(db, uid, tid, "travel", now)
            failed = "job_unreachable"
    if failed:
        raise GameError(failed)
    return out


def muni_complete(db, city, uid: int, tid: int, now: int | None = None) -> dict:
    """Do the task and get paid, once. Only possible while the player is standing at the target (arrival comes from the saved trip and the
    clock). Raises GameError: job_stale (an old button / a task that is over), job_far."""
    now = core.now() if now is None else now
    try:
        with db.tx():
            t = _mine(db, city, uid, tid, MUNI, ("travel",))
            d = data_of(t)
            st = presence.stand(db, city, uid, now)
            if st.trip is not None or st.row["at_place"] != d.get("target"):
                raise GameError("job_far")
            return _close(db, uid, tid, "travel", [("muni_" + t["kind"], int(t["reward"]))],
                          {"job": MUNI, "kind": t["kind"], "reward": int(t["reward"]), "target": d.get("target")}, now)
    except _Stale:
        raise GameError("job_stale")


# =====================================================================================================================
#                                      housekeeping every job screen runs first
# =====================================================================================================================
def sync(db, city, uid: int, now: int | None = None) -> dict | None:
    """Apply what is due, lazily: a Snapp ride whose car has arrived at the destination is paid out. Returns the receipt of a ride paid by THIS
    call (so the screen can show it), else None. Safe to call as often as you like."""
    now = core.now() if now is None else now
    t = current(db, uid)
    if t is not None and t["job"] == SNAPP and t["stage"] == "to_dest" and city is not None and t["world_id"] == city.id:
        return snapp_settle(db, city, uid, t["id"], now)
    return None


def in_ride(db, uid: int) -> bool:
    """Is the player in the middle of a Snapp ride (driving to the passenger or to the destination)? Then the map's «go» is not available to
    them: the ride owns their one trip until it is finished or cancelled."""
    t = current(db, uid)
    return t is not None and t["job"] == SNAPP and t["stage"] in ("to_pickup", "to_dest")


def car_trip(db, world_id: int, uid: int):
    """The player's car trip in progress (a Snapp leg), or None."""
    trip = presence.walking_trip(db, world_id, uid)
    return trip if trip is not None and trip["mode"] == "car" else None
