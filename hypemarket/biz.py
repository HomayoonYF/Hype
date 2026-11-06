"""Branches and their money (v0.211): a business of one brand at one property, with its own till, its own permissions and its own history.

A BRANCH is one row of `branches`: the brand, the owner, the property it runs in (at most ONE open branch per property, by a partial unique index) and a number that is unique per brand and city
(«NIKE · Tehran #2»). Opening needs: a property the player owns and has FITTED for a business mode (an empty shell cannot open), and a brand the player owns. Brand, property and branch stay three
separate things: the same owner can have many properties without a branch, a brand without a branch, and several branches under one brand in different cities. A closed branch stays in the books.

THE TILL. Every branch has its own money (`branches.till`, cents). It moves only through `till_apply`, which writes one row of `biz_ledger` (append-only, UNIQUE per branch, kind and reference, so the same
movement twice is a no-op) in the same transaction as the balance change; it can never go below zero. Money enters or leaves the BUSINESS only in pairs with the player's own cash ledger: `fund` (cash.apply
`biz_fund` out of the owner's wallet + till `fund`) and `withdraw` (the reverse). Whatever else the till pays (materials, wages, upgrades) is a till row with the other party's own cash row beside it.
`audit_till` re-adds the ledger and compares it with the balance.

PERMISSIONS. The owner may do everything with their branches. A delegated manager (a contract, staff.py) holds only the permissions named in it (`PERMS`); `can` answers for a player and a branch. Nothing here lets
a manager touch the brand, close the branch or take money out of the till to themselves: those are owner-only functions and check the owner.
"""
from __future__ import annotations

import secrets

from . import brands, cash, config as C, core, estate
from .core import GameError

PERMS = ("stock", "produce", "spend", "hire", "approve", "upgrade", "ads")          # what a manager can be allowed (staff.py stores the list in the contract)
_PERM_HOOKS: list = []                                                              # staff.py registers `fn(db, uid, branch_row) -> set of permissions`
_CLOSE_HOOKS: list = []                                                             # later modules register `fn(db, branch_row, now)`: return what is in stock to the owner, settle wages ...
_RESERVE_HOOKS: list = []                                                           # `fn(db, branch_row, now) -> cents` held back from the owner (wages that are owed)


def register_perm_hook(fn) -> None:
    if fn not in _PERM_HOOKS:
        _PERM_HOOKS.append(fn)


def register_close_hook(fn, first: bool = False) -> None:
    """`first=True` runs the hook before the others (money that must be back in the till before wages are settled and the till is paid out)."""
    if fn not in _CLOSE_HOOKS:
        _CLOSE_HOOKS.insert(0, fn) if first else _CLOSE_HOOKS.append(fn)


def register_reserve_hook(fn) -> None:
    if fn not in _RESERVE_HOOKS:
        _RESERVE_HOOKS.append(fn)


def reserved(db, branch, now: int | None = None) -> int:
    """Cents of the till that are spoken for (wages owed to the staff): the owner cannot withdraw, spend or advertise with them."""
    now = core.now() if now is None else now
    return sum(fn(db, branch, now) for fn in _RESERVE_HOOKS)


def spendable(db, branch, now: int | None = None) -> int:
    """What the owner may actually take out of or spend from the till: the till minus what is reserved."""
    return max(0, branch["till"] - reserved(db, branch, now))


# =====================================================================================================================
#                                                       reading
# =====================================================================================================================
def get(db, branch_id):
    if not isinstance(branch_id, int) or isinstance(branch_id, bool):
        return None
    return db.q1("SELECT * FROM branches WHERE id=?", (branch_id,))


def of_property(db, property_id: int):
    """The OPEN branch of a property, or None."""
    return db.q1("SELECT * FROM branches WHERE property_id=? AND state='open'", (property_id,))


def of_owner(db, uid: int, open_only: bool = False) -> list:
    q = "SELECT * FROM branches WHERE owner_id=?" + (" AND state='open'" if open_only else "") + " ORDER BY opened_at, id"
    return db.qa(q, (uid,))


def in_world(db, world_id: int, open_only: bool = True) -> list:
    q = "SELECT * FROM branches WHERE world_id=?" + (" AND state='open'" if open_only else "") + " ORDER BY opened_at, id"
    return db.qa(q, (world_id,))


def property_of(db, branch):
    return estate.get(db, branch["property_id"])


def label(branch) -> str:
    """«NIKE #2»: the brand's display name and the branch's number in its city."""
    return f"{brands.name_of(branch['brand'])} #{branch['seq']}"


def can(db, uid: int, branch, perm: str | None = None) -> bool:
    """May player `uid` do `perm` at this branch? The owner: everything. Anyone else: only what a registered hook (a manager's contract) grants; `None` asks whether they have any role at all."""
    if branch is None:
        return False
    if branch["owner_id"] == uid:
        return True
    if perm is not None and perm not in PERMS:
        return False
    from . import staff  # noqa: F401  (registers the contract hook the first time, whatever else was imported before)
    for fn in _PERM_HOOKS:
        granted = fn(db, uid, branch)
        if granted and (perm is None or perm in granted):
            return True
    return False


def need(db, uid: int, branch_id: int, perm: str | None = None):
    """The OPEN branch `branch_id` when `uid` may do `perm` there. GameError: branch_gone (unknown or closed), branch_denied."""
    b = get(db, branch_id)
    if b is None or b["state"] != "open":
        raise GameError("branch_gone")
    if not can(db, uid, b, perm):
        raise GameError("branch_denied")
    return b


# =====================================================================================================================
#                                                       opening and closing
# =====================================================================================================================
def open_branch(db, uid: int, property_id: int, brand: str, now: int | None = None):
    """Open a branch of `brand` in property `property_id`. GameError: prop_gone, prop_notyours, branch_nomode (the shell is not fitted), brand_notyours, branch_exists (an open branch is already there),
    branch_cap."""
    now = core.now() if now is None else now
    with db.tx():
        prop = estate.get(db, property_id)
        if prop is None:
            raise GameError("prop_gone")
        if prop["owner_id"] != uid:
            raise GameError("prop_notyours")
        if prop["mode"] is None:
            raise GameError("branch_nomode")
        b = brands.get(db, brand)
        if b is None or b["owner_id"] != uid:
            raise GameError("brand_notyours")
        if of_property(db, property_id) is not None:
            raise GameError("branch_exists")
        seq = (db.q1("SELECT COALESCE(MAX(seq),0) m FROM branches WHERE brand=? AND world_id=?", (brand, prop["world_id"]))["m"]) + 1
        if seq > C.BRAND_SEQ_CAP:
            raise GameError("branch_cap")
        cur = db.x("INSERT INTO branches(brand,owner_id,property_id,world_id,seq,state,sign,till,opened_at,created_at) VALUES(?,?,?,?,?,'open',?,0,?,?)",
                   (brand, uid, property_id, prop["world_id"], seq, C.BRANCH_SIGN_DEFAULT, now, now))
        bid = cur.lastrowid
        estate.event(db, "branch_opened", str(bid), uid, {"brand": brand, "property": property_id, "world": prop["world_id"], "seq": seq}, now)
        brands.refresh_capital(db, now)
    return get(db, bid)


def close_branch(db, uid: int, branch_id: int, now: int | None = None):
    """Close a branch (owner only). The registered close hooks run first (stock goes back to the owner, wages are settled ...), the till is paid out to the owner's cash, then the branch is closed for good
    and the headquarters and the capital are recomputed. All or nothing. GameError: branch_gone, branch_denied, plus whatever a hook raises (inv_full ...)."""
    now = core.now() if now is None else now
    with db.tx():
        b = get(db, branch_id)
        if b is None or b["state"] != "open":
            raise GameError("branch_gone")
        if b["owner_id"] != uid:
            raise GameError("branch_denied")
        for fn in _CLOSE_HOOKS:
            fn(db, b, now)
        b = get(db, branch_id)
        if b["till"] > 0:
            paid = b["till"]
            till_apply(db, branch_id, -paid, "close", "close", actor=uid, note="closed", now=now, allow_closed=False)
            if cash.apply(db, uid, paid, "biz_withdraw", f"close:{branch_id}", now=now) is None:
                raise GameError("branch_gone")
        if db.x("UPDATE branches SET state='closed', closed_at=? WHERE id=? AND state='open'", (now, branch_id)).rowcount != 1:
            raise GameError("branch_gone")
        estate.event(db, "branch_closed", str(branch_id), uid, {"brand": b["brand"], "world": b["world_id"]}, now)
        brands.refresh_capital(db, now)
    return get(db, branch_id)


# =====================================================================================================================
#                                                       the till
# =====================================================================================================================
def till_apply(db, branch_id: int, delta: int, kind: str, ref: str, *, actor: int | None = None, note: str = "", now: int | None = None, allow_closed: bool = False) -> int | None:
    """Move `delta` cents (negative = out) in or out of the branch's till, once per (branch, kind, ref). Returns the new balance, or None when this movement was already made.
    GameError: branch_gone (unknown, or closed unless `allow_closed`), till_low (it would go below zero)."""
    if not isinstance(delta, int) or isinstance(delta, bool) or delta == 0:
        raise ValueError("a till movement is a non-zero whole number of cents")
    if not (isinstance(kind, str) and kind and len(kind) <= 24 and isinstance(ref, str) and ref and len(ref) <= 80):
        raise ValueError("a till movement needs a kind and a reference")
    now = core.now() if now is None else now
    with db.tx():
        b = get(db, branch_id)
        if b is None or (b["state"] != "open" and not allow_closed):
            raise GameError("branch_gone")
        new = b["till"] + delta
        if new < 0:
            raise GameError("till_low", need=-delta, have=b["till"])
        if new > C.CASH_CAP_CENTS:
            raise GameError("cash_cap")
        if db.q1("SELECT 1 FROM biz_ledger WHERE branch_id=? AND kind=? AND ref=?", (branch_id, kind, ref)) is not None:
            return None
        db.x("INSERT INTO biz_ledger(branch_id,kind,ref,delta,balance_after,actor,note,created_at) VALUES(?,?,?,?,?,?,?,?)", (branch_id, kind, ref, delta, new, actor, note[:80], now))
        if db.x("UPDATE branches SET till=? WHERE id=? AND till=?", (new, branch_id, b["till"])).rowcount != 1:
            raise RuntimeError("a till changed under a transaction")
        return new


def fund(db, uid: int, branch_id: int, cents: int, ref: str | None = None, now: int | None = None) -> int:
    """Move `cents` from the owner's wallet into the branch's till (owner only). Returns the till's new balance. GameError: branch_gone, branch_denied, cash_low."""
    if not isinstance(cents, int) or isinstance(cents, bool) or cents <= 0:
        raise ValueError("an amount is a positive whole number of cents")
    now = core.now() if now is None else now
    ref = ref or secrets.token_hex(6)
    with db.tx():
        b = get(db, branch_id)
        if b is None or b["state"] != "open":
            raise GameError("branch_gone")
        if b["owner_id"] != uid:
            raise GameError("branch_denied")
        if cash.apply(db, uid, -cents, "biz_fund", f"{branch_id}:{ref}", now=now) is None:
            return b["till"]
        new = till_apply(db, branch_id, cents, "fund", ref, actor=uid, note="from the owner", now=now)
        return new if new is not None else b["till"]


def withdraw(db, uid: int, branch_id: int, cents: int, ref: str | None = None, now: int | None = None) -> int:
    """Move `cents` from the till to the owner's wallet (owner only). Returns the till's new balance. GameError: branch_gone, branch_denied, till_low, cash_cap."""
    if not isinstance(cents, int) or isinstance(cents, bool) or cents <= 0:
        raise ValueError("an amount is a positive whole number of cents")
    now = core.now() if now is None else now
    ref = ref or secrets.token_hex(6)
    with db.tx():
        b = get(db, branch_id)
        if b is None or b["state"] != "open":
            raise GameError("branch_gone")
        if b["owner_id"] != uid:
            raise GameError("branch_denied")
        if cents > spendable(db, b, now):
            raise GameError("till_low" if cents > b["till"] else "till_reserved", need=cents, have=b["till"], free=spendable(db, b, now))
        new = till_apply(db, branch_id, -cents, "withdraw", ref, actor=uid, note="to the owner", now=now)
        if new is None:
            return b["till"]
        if cash.apply(db, uid, cents, "biz_withdraw", f"{branch_id}:{ref}", now=now) is None:
            raise GameError("branch_gone")
        return new


def ledger(db, branch_id: int, limit: int = 8) -> list:
    return db.qa("SELECT * FROM biz_ledger WHERE branch_id=? ORDER BY id DESC LIMIT ?", (branch_id, limit))


def audit_till(db, branch_id: int) -> list:
    """Problems found in a branch's money (empty = the books are right): the ledger must add up to the till, every balance_after must follow the one before, nothing may be negative."""
    problems = []
    b = get(db, branch_id)
    if b is None:
        return ["no such branch"]
    total = db.q1("SELECT COALESCE(SUM(delta),0) s FROM biz_ledger WHERE branch_id=?", (branch_id,))["s"]
    if total != b["till"]:
        problems.append(f"ledger adds up to {total} but the till holds {b['till']}")
    run = 0
    for r in db.qa("SELECT delta, balance_after FROM biz_ledger WHERE branch_id=? ORDER BY id", (branch_id,)):
        run += r["delta"]
        if run != r["balance_after"] or run < 0:
            problems.append(f"the running balance broke at {r['balance_after']}")
            break
    return problems


# =====================================================================================================================
#                                                       the owner's overview
# =====================================================================================================================
def overview(db, uid: int) -> list:
    """Every branch of the owner with its property and brand, newest last: [{branch, property, hq}] (closed ones too)."""
    out = []
    for b in of_owner(db, uid):
        out.append({"branch": b, "property": estate.get(db, b["property_id"]), "hq": b["state"] == "open" and is_hq(db, b)})
    return out


def is_hq(db, branch) -> bool:
    h = brands.hq_branch(db, branch["brand"])
    return h is not None and h["id"] == branch["id"]
