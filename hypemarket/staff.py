"""Employees, contracts and payroll (v0.211).

A CONTRACT is one employee in one role at one branch with the terms the OWNER chose: fixed pay (cents a day) or a percentage of the branch's recorded sales, and, for a manager, the list of
permissions. The terms are written once and a trigger keeps them: a different deal is a NEW contract that the employee has to accept (nothing is ever changed behind anybody's back). An offer
waits for the employee's answer (it expires); a BOT employee (accountant, warehouse worker, production worker, sales clerk: never a manager) is hired outright, has fixed performance, earns no skills
and costs a fixed wage a day which the GAME takes (a sink, not a payment to anybody).

ROLES. manager: exactly the permissions named in the contract (never the brand, never closing the branch, never money out to themselves). accountant: makes the sales automatic (store.py) and keeps
the books. warehouse: the `stock` permission (move goods to the display, run the restock; never take goods out). production: the `produce` permission. sales: may approve purchases. A branch has at most
`upgrades.staff_slots` contracts running or offered at a time (the property's slots plus the staff-room upgrades), and one manager.

PAY. Everything owed comes from RECORDED figures: a fixed wage accrues by the second of the server clock from the contract's start; a percentage is that share of the branch's `sale` rows in its till ledger
from the start (sales that really happened, counted once: no projection, no invented income). One contract pays at most `STAFF_PERCENT_MAX_BPS`, all percent contracts of a branch together at most
`STAFF_PERCENT_TOTAL_BPS`. `settle` pays what is owed from the till, as far as the till holds it; what it cannot pay stays owed (it keeps counting as owed and is paid first when the till has money again).
RESERVED: what is owed is held back (`reserved`): the owner cannot take it out of the till, spend it on upgrades or put it into an ad. Closing a branch settles every wage first, the owner making up
what the till lacks from their own cash; a branch with wages it cannot pay does not close.
"""
from __future__ import annotations

from . import biz, brands, cash, character, config as C, core, estate, upgrades
from .core import GameError

DAY = 86400
ROLE_PERMS = {"accountant": ("approve",), "warehouse": ("stock",), "production": ("produce",), "sales": ("approve",)}
MANAGER_DEFAULT = ("stock", "produce", "approve", "ads")


def role_perms(role: str, perms=None) -> tuple:
    if role == "manager":
        got = tuple(p for p in biz.PERMS if p in (perms if perms else MANAGER_DEFAULT))
        return got
    return ROLE_PERMS[role]


# =====================================================================================================================
#                                                       reading
# =====================================================================================================================
def get(db, cid):
    if not isinstance(cid, int) or isinstance(cid, bool):
        return None
    return db.q1("SELECT * FROM contracts WHERE id=?", (cid,))


def of_branch(db, branch_id: int, states=("offered", "active")) -> list:
    q = ",".join("?" * len(states))
    return db.qa(f"SELECT * FROM contracts WHERE branch_id=? AND state IN ({q}) ORDER BY id", (branch_id, *states))


def of_employee(db, uid: int, states=("offered", "active")) -> list:
    q = ",".join("?" * len(states))
    return db.qa(f"SELECT * FROM contracts WHERE employee_id=? AND state IN ({q}) ORDER BY id", (uid, *states))


def perms_of(contract) -> tuple:
    return tuple(p for p in (contract["perms"] or "").split(",") if p)


def active_player(db, branch_id: int, uid: int, role: str | None = None):
    q = "SELECT * FROM contracts WHERE branch_id=? AND employee_id=? AND state='active'" + (" AND role=?" if role else "") + " ORDER BY id"
    return db.qa(q, (branch_id, uid) + ((role,) if role else ()))


def has_role(db, branch_id: int, uid: int, role: str) -> bool:
    return bool(active_player(db, branch_id, uid, role))


def bot_role(db, branch_id: int, role: str):
    """The active BOT contract of a role at the branch, or None."""
    return db.q1("SELECT * FROM contracts WHERE branch_id=? AND role=? AND employee_id IS NULL AND state='active'", (branch_id, role))


def role_holder(db, branch_id: int, role: str):
    """The active contract (player or bot) that fills `role` at the branch (the oldest one), or None."""
    return db.q1("SELECT * FROM contracts WHERE branch_id=? AND role=? AND state='active' ORDER BY id LIMIT 1", (branch_id, role))


def slots_taken(db, branch_id: int) -> int:
    return db.q1("SELECT COUNT(*) c FROM contracts WHERE branch_id=? AND state IN ('offered','active')", (branch_id,))["c"]


def percent_taken(db, branch_id: int) -> int:
    return db.q1("SELECT COALESCE(SUM(pay_value),0) s FROM contracts WHERE branch_id=? AND pay_type='percent' AND state IN ('offered','active')", (branch_id,))["s"]


def perm_hook(db, uid: int, branch) -> set:
    out: set = set()
    for c in active_player(db, branch["id"], uid):
        out |= set(perms_of(c))
    return out


biz.register_perm_hook(perm_hook)


# =====================================================================================================================
#                                                       making and answering offers
# =====================================================================================================================
def _check_terms(role: str, pay_type: str, pay_value, *, bot: bool) -> None:
    if role not in C.ROLES or (bot and role not in C.BOT_ROLES):
        raise GameError("staff_role")
    if pay_type not in ("fixed", "percent") or not isinstance(pay_value, int) or isinstance(pay_value, bool) or pay_value < 0:
        raise GameError("staff_terms")
    if pay_type == "fixed" and pay_value > C.STAFF_FIXED_MAX_DAY_CENTS:
        raise GameError("staff_terms")
    if pay_type == "percent" and pay_value > C.STAFF_PERCENT_MAX_BPS:
        raise GameError("staff_terms")


def _owner_branch(db, owner_id: int, branch_id: int):
    b = biz.get(db, branch_id)
    if b is None or b["state"] != "open":
        raise GameError("branch_gone")
    if b["owner_id"] != owner_id:
        raise GameError("branch_denied")
    return b


def expire_offers(db, now: int | None = None, branch_id: int | None = None) -> int:
    """Close every offer nobody answered in time (it becomes 'declined', closed by no one): its slot and its role are free again. Lazy and idempotent; returns how many it closed."""
    now = core.now() if now is None else now
    q = "UPDATE contracts SET state='declined', ended_at=? WHERE state='offered' AND expires_at IS NOT NULL AND expires_at<=?" + (" AND branch_id=?" if branch_id is not None else "")
    return db.x(q, (now, now) + ((branch_id,) if branch_id is not None else ())).rowcount


def _name(db, uid: int) -> str:
    """A person's name as the character card has it (else the account's name): for the private message an offer sends."""
    ch = character.get(db, uid)
    if ch is not None and ch["first_name"]:
        return f"{ch['first_name']} {ch['last_name']}".strip()
    u = core.get_user(db, uid)
    return core.display_name(u, False) if u is not None else str(uid)


def offer(db, owner_id: int, branch_id: int, employee_id: int, role: str, pay_type: str, pay_value: int, perms=None, now: int | None = None):
    """The owner offers a player a contract. The employee must accept. GameError: branch_gone, branch_denied, staff_role, staff_terms, staff_self, staff_noone, staff_full, staff_manager (one already),
    staff_percent (over the caps), staff_dup (they already hold or were offered this role here)."""
    now = core.now() if now is None else now
    _check_terms(role, pay_type, pay_value, bot=False)
    with db.tx():
        b = _owner_branch(db, owner_id, branch_id)
        expire_offers(db, now, branch_id)                                                            # an offer nobody answered must not keep a slot or a role
        if employee_id == owner_id:
            raise GameError("staff_self")
        u = core.get_user(db, employee_id)
        ch = character.get(db, employee_id) if u is not None else None
        if u is None or u["bot"] or ch is None or not character.is_done(ch):
            raise GameError("staff_noone")
        if slots_taken(db, branch_id) >= upgrades.staff_slots(db, b):
            raise GameError("staff_full")
        if role == "manager" and db.q1("SELECT 1 FROM contracts WHERE branch_id=? AND role='manager' AND state IN ('offered','active')", (branch_id,)):
            raise GameError("staff_manager")
        if pay_type == "percent" and percent_taken(db, branch_id) + pay_value > C.STAFF_PERCENT_TOTAL_BPS:
            raise GameError("staff_percent")
        if role == "manager" and perms:
            bad = [p for p in perms if p not in biz.PERMS]
            if bad:
                raise GameError("staff_terms")
        granted = ",".join(role_perms(role, perms))
        try:
            cur = db.x("INSERT INTO contracts(branch_id,employee_id,role,pay_type,pay_value,perms,state,offered_by,offered_at,expires_at) VALUES(?,?,?,?,?,?,'offered',?,?,?)",
                       (branch_id, employee_id, role, pay_type, pay_value, granted, owner_id, now, now + C.STAFF_OFFER_TTL))
        except Exception as e:                                                                       # the partial unique index: already offered / employed in this role
            if "UNIQUE" in str(e):
                raise GameError("staff_dup") from None
            raise
        cid = cur.lastrowid
        estate.event(db, "contract_offered", str(cid), owner_id, {"branch": branch_id, "employee": employee_id, "role": role}, now)
        core.notify(db, employee_id, "staff_offer", who=_name(db, owner_id), role=role, brand=b["brand"], seq=b["seq"], pay_type=pay_type, pay_value=pay_value)
    return get(db, cid)


def hire_bot(db, owner_id: int, branch_id: int, role: str, now: int | None = None):
    """Hire an automated employee for `role` (never a manager): fixed performance, a fixed daily wage the game takes, no skills. Active at once. GameError: staff_role, staff_full, staff_dup."""
    now = core.now() if now is None else now
    _check_terms(role, "fixed", C.BOT_WORKER_WAGE_DAY_CENTS.get(role, 0), bot=True)
    with db.tx():
        b = _owner_branch(db, owner_id, branch_id)
        if bot_role(db, branch_id, role) is not None:
            raise GameError("staff_dup")
        if slots_taken(db, branch_id) >= upgrades.staff_slots(db, b):
            raise GameError("staff_full")
        cur = db.x("INSERT INTO contracts(branch_id,employee_id,role,pay_type,pay_value,perms,state,offered_by,offered_at,starts_at) VALUES(?,NULL,?,'fixed',?,?,'active',?,?,?)",
                   (branch_id, role, C.BOT_WORKER_WAGE_DAY_CENTS[role], ",".join(role_perms(role)), owner_id, now, now))
        estate.event(db, "bot_hired", str(cur.lastrowid), owner_id, {"branch": branch_id, "role": role}, now)
    return get(db, cur.lastrowid)


def accept(db, uid: int, cid: int, now: int | None = None):
    """The employee accepts an offer (once): it becomes active and the pay starts counting NOW. GameError: staff_gone (not theirs / not open / expired), branch_gone, staff_full, staff_percent."""
    now = core.now() if now is None else now
    with db.tx():
        c = get(db, cid)
        if c is None or c["employee_id"] != uid or c["state"] != "offered":
            raise GameError("staff_gone")
        if c["expires_at"] is not None and c["expires_at"] <= now:
            raise GameError("staff_gone")
        b = biz.get(db, c["branch_id"])
        if b is None or b["state"] != "open":
            raise GameError("branch_gone")
        if db.x("UPDATE contracts SET state='active', starts_at=? WHERE id=? AND state='offered' AND employee_id=?", (now, cid, uid)).rowcount != 1:
            raise GameError("staff_gone")
        estate.event(db, "contract_accepted", str(cid), uid, {"branch": c["branch_id"], "role": c["role"]}, now)
    return get(db, cid)


def decline(db, uid: int, cid: int, now: int | None = None):
    now = core.now() if now is None else now
    with db.tx():
        c = get(db, cid)
        if c is None or c["employee_id"] != uid or c["state"] != "offered":
            raise GameError("staff_gone")
        db.x("UPDATE contracts SET state='declined', ended_at=?, ended_by=? WHERE id=? AND state='offered'", (now, uid, cid))
    return get(db, cid)


def withdraw_offer(db, owner_id: int, cid: int, now: int | None = None):
    """The owner takes back an offer nobody has accepted yet."""
    now = core.now() if now is None else now
    with db.tx():
        c = get(db, cid)
        if c is None or c["state"] != "offered":
            raise GameError("staff_gone")
        b = biz.get(db, c["branch_id"])
        if b is None or b["owner_id"] != owner_id:
            raise GameError("branch_denied")
        db.x("UPDATE contracts SET state='declined', ended_at=?, ended_by=? WHERE id=? AND state='offered'", (now, owner_id, cid))
    return get(db, cid)


def end(db, actor: int, cid: int, now: int | None = None):
    """Either side ends an active contract. What is owed up to this moment is settled (as far as the till holds it) and the rest stays owed. Owner or the employee. GameError: staff_gone, branch_denied."""
    now = core.now() if now is None else now
    with db.tx():
        c = get(db, cid)
        if c is None or c["state"] != "active":
            raise GameError("staff_gone")
        b = biz.get(db, c["branch_id"])
        if actor != c["employee_id"] and (b is None or actor != b["owner_id"]):
            raise GameError("branch_denied")
        if db.x("UPDATE contracts SET state='ended', ended_at=?, ended_by=? WHERE id=? AND state='active'", (now, actor, cid)).rowcount != 1:
            raise GameError("staff_gone")
        estate.event(db, "contract_ended", str(cid), actor, {"branch": c["branch_id"], "role": c["role"]}, now)
        if b is not None and b["state"] == "open":
            settle(db, c["branch_id"], now)
    return get(db, cid)


# =====================================================================================================================
#                                                       pay
# =====================================================================================================================
def owed(db, c, now: int | None = None) -> tuple:
    """(fixed owed, percent owed) in cents for contract row `c` up to `now` (or its end): from the server clock and the recorded sales only."""
    now = core.now() if now is None else now
    if c["state"] not in ("active", "ended") or c["starts_at"] is None:
        return 0, 0
    end_at = min(now, c["ended_at"]) if c["ended_at"] is not None else now
    if c["pay_type"] == "fixed":
        return max(0, (max(0, end_at - c["starts_at"]) * c["pay_value"]) // DAY - c["paid_fixed"]), 0
    base = db.q1("SELECT COALESCE(SUM(delta),0) s FROM biz_ledger WHERE branch_id=? AND kind='sale' AND created_at>=? AND created_at<=?", (c["branch_id"], c["starts_at"], end_at))["s"]
    return 0, max(0, base * c["pay_value"] // 10000 - c["paid_pct"])


def owed_total(db, c, now: int | None = None) -> int:
    return sum(owed(db, c, now))


def reserved(db, branch_id: int, now: int | None = None) -> int:
    """Every cent the branch owes its staff right now (held back from the owner)."""
    return sum(owed_total(db, c, now) for c in db.qa("SELECT * FROM contracts WHERE branch_id=? AND state IN ('active','ended')", (branch_id,)))


biz.register_reserve_hook(lambda db, branch, now: reserved(db, branch["id"], now))


def settle(db, branch_id: int, now: int | None = None) -> dict:
    """Pay what is owed from the till, oldest contract first, as far as the till holds it. Returns {"paid": cents, "left": cents still owed}. Safe to call any time and as often as you like:
    the reference of a payment is derived from what the contract has been paid so far, so the same payment cannot be made twice."""
    now = core.now() if now is None else now
    paid = left = 0
    with db.tx():
        b = biz.get(db, branch_id)
        if b is None:
            return {"paid": 0, "left": 0}
        for c in db.qa("SELECT * FROM contracts WHERE branch_id=? AND state IN ('active','ended') ORDER BY id", (branch_id,)):
            fx, pc = owed(db, c, now)
            want = fx + pc
            if want <= 0:
                continue
            pay = min(want, b["till"] if b["state"] == "open" else 0)
            if pay <= 0:
                left += want
                continue
            pay_fx = min(fx, pay)
            pay_pc = pay - pay_fx
            ref = f"c{c['id']}:{c['paid_fixed'] + c['paid_pct'] + pay}"
            if biz.till_apply(db, branch_id, -pay, "wage", ref, actor=c["employee_id"], note=c["role"], now=now) is None:
                continue
            if c["employee_id"] is not None and cash.apply(db, c["employee_id"], pay, "wage_in", f"{branch_id}:{ref}", now=now) is None:
                raise RuntimeError("a wage was paid twice")
            if db.x("UPDATE contracts SET paid_fixed=paid_fixed+?, paid_pct=paid_pct+? WHERE id=?", (pay_fx, pay_pc, c["id"])).rowcount != 1:
                raise RuntimeError("a contract vanished")
            b = biz.get(db, branch_id)
            paid += pay
            left += want - pay
        if paid and not left:
            from . import rep
            rep.add(db, branch_id, "payroll", f"d{now // 86400}", C.REP_WAGE_RUN, None, now)                  # once per branch and (server) day: asking for the wage often cannot farm a reputation
    return {"paid": paid, "left": left}


def _on_close(db, branch, now: int) -> None:
    """Closing: every wage is settled (the till first, then the owner's own cash), then every contract ends."""
    res = settle(db, branch["id"], now)
    if res["left"] > 0:
        for c in db.qa("SELECT * FROM contracts WHERE branch_id=? AND state IN ('active','ended') ORDER BY id", (branch["id"],)):
            want = owed_total(db, c, now)
            if want <= 0:
                continue
            ref = f"c{c['id']}:close:{c['paid_fixed'] + c['paid_pct'] + want}"
            try:
                if cash.apply(db, branch["owner_id"], -want, "wage_out", ref, now=now) is None:
                    raise GameError("owed_wages")
            except GameError as e:
                if e.code == "cash_low":
                    raise GameError("owed_wages", need=want) from None
                raise
            if c["employee_id"] is not None:
                cash.apply(db, c["employee_id"], want, "wage_in", f"{branch['id']}:{ref}", now=now)
            fx, pc = owed(db, c, now)
            db.x("UPDATE contracts SET paid_fixed=paid_fixed+?, paid_pct=paid_pct+? WHERE id=?", (fx, pc, c["id"]))
    for c in db.qa("SELECT id, state FROM contracts WHERE branch_id=? AND state IN ('active','offered')", (branch["id"],)):
        if c["state"] == "offered":
            db.x("UPDATE contracts SET state='declined', ended_at=?, ended_by=? WHERE id=?", (now, branch["owner_id"], c["id"]))
        else:
            db.x("UPDATE contracts SET state='ended', ended_at=?, ended_by=? WHERE id=?", (now, branch["owner_id"], c["id"]))


biz.register_close_hook(_on_close)


# =====================================================================================================================
#                                                       the accountant
# =====================================================================================================================
def accountant(db, branch_id: int):
    """The accountant that handles the branch's sales (the oldest active one, a player or a bot), or None."""
    return role_holder(db, branch_id, "accountant")


def auto_limit(db, contract) -> int:
    """The biggest purchase (cents) this accountant completes without anybody's approval: a bot has a fixed limit, a player one grows with their Accounting skill."""
    from . import skills
    if contract["employee_id"] is None:
        return C.BOT_AUTO_LIMIT_CENTS
    return C.ACCOUNTANT_AUTO_BASE_CENTS + C.ACCOUNTANT_AUTO_PER_LEVEL_CENTS * skills.level_of(db, contract["employee_id"], "accounting")
