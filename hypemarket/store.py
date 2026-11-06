"""Customers and sales (v0.211): a player walks up to a branch, looks at its DISPLAY and buys. Atomic, once, and honest about who completes it.

WHAT A CUSTOMER SEES AND CAN DO. Only a customer who is PHYSICALLY NEAR the property (`estate.near`: standing still within `BIZ_NEAR_UNITS` of its door, decided on the server) sees the shop and its prices
and can buy. Only the DISPLAY is on sale (never the warehouse), only a variant that has a price in the owner's instructions, only the pieces actually on display. The owner, the manager and every member of
the branch's staff can never be its customers (no revenue, reputation or skill from selling to yourself).

A PURCHASE IS TWO STEPS, so a repeated or stale button can never buy twice. Opening the confirm screen creates a QUOTE (`store_orders`, state 'quote': the item, the quantity and the price, fixed, valid for
`QUOTE_TTL` seconds). Confirming moves that one row from 'quote' (or 'pending') to 'done' with a guarded update, and in the SAME transaction: the display stock is checked and reduced, the item goes into the
buyer's bag (a full bag stops it), the price leaves the buyer's life cash, the same amount enters the branch's till as a `sale` row of its ledger, the reputation and the accountant's progress are
recorded and the restock instructions run. A second confirm finds the order 'done' and changes nothing. If the owner changed the price in the meantime the quote is void.

WHO COMPLETES IT. With an ACCOUNTANT (a player or a bot) the sale completes automatically, whether or not anybody else is online, up to that accountant's limit (`staff.auto_limit`: fixed for a bot, growing
with the Accounting skill of a player). A checkout upgrade lets a small purchase complete without an accountant. Otherwise the order waits ('pending', `APPROVAL_TTL`) for the owner or somebody with
`approve`; the shop never stops working because its owner is away: the customer is told it is waiting, and nothing is taken from them until it is approved (the cash and the stock are checked again then).
An order bigger than the accountant's limit waits for approval the same way.
"""
from __future__ import annotations

import datetime

from . import biz, cash, charxp, config as C, core, estate, fashion, inventory, items, rep, skills, staff, stock, upgrades
from .core import GameError


def get(db, order_id):
    if not isinstance(order_id, int) or isinstance(order_id, bool):
        return None
    return db.q1("SELECT * FROM store_orders WHERE id=?", (order_id,))


def is_staff(db, branch, uid: int) -> bool:
    return uid == branch["owner_id"] or any(c["branch_id"] == branch["id"] for c in staff.of_employee(db, uid, states=("offered", "active")))


def shelf(db, branch_id: int) -> list:
    """What is on sale: [{item_id, data, qty (on display), price}] for every displayed variant that has a price, in catalog order."""
    out = []
    for r in stock.contents(db, branch_id, stock.DISP):
        ru = stock.rule(db, branch_id, r["item_id"], r["data"])
        if ru is not None and ru["price"]:
            out.append({"item_id": r["item_id"], "data": r["data"], "qty": r["qty"], "price": ru["price"]})
    return out


def check_customer(db, city, st, uid: int, branch):
    """The checks every customer step shares. GameError: no_user, no_character, branch_gone, store_closed (no shop floor), store_self, store_far."""
    estate._human(db, uid)
    if branch is None or branch["state"] != "open":
        raise GameError("branch_gone")
    prop = biz.property_of(db, branch)
    if not estate.mode_allows(prop["mode"], "retail"):
        raise GameError("store_closed")
    if is_staff(db, branch, uid):
        raise GameError("store_self")
    if not estate.near(city, st, prop):
        raise GameError("store_far")
    return prop


def quote(db, city, st, uid: int, branch_id: int, item_id: str, data, n: int, now: int | None = None):
    """Make the price quote for `n` pieces of one displayed variant. GameError: the customer checks, store_none (not on sale / not enough on display), bad_amount, cash_cap."""
    now = core.now() if now is None else now
    if not isinstance(n, int) or isinstance(n, bool) or not 1 <= n <= 99:
        raise GameError("bad_amount")
    with db.tx():
        expire(db, now)
        b = biz.get(db, branch_id)
        check_customer(db, city, st, uid, b)
        d = stock.key(data)
        ru = stock.rule(db, branch_id, item_id, d)
        if ru is None or not ru["price"] or stock.qty(db, branch_id, stock.DISP, item_id, d) < n:
            raise GameError("store_none")
        total = ru["price"] * n
        if total > C.CASH_CAP_CENTS:
            raise GameError("cash_cap")
        db.x("UPDATE store_orders SET state='cancelled' WHERE buyer_id=? AND branch_id=? AND state='quote'", (uid, branch_id))
        cur = db.x("INSERT INTO store_orders(branch_id,buyer_id,item_id,data,qty,unit_cents,total_cents,state,created_at,expires_at) VALUES(?,?,?,?,?,?,?,'quote',?,?)",
                   (branch_id, uid, item_id, d, n, ru["price"], total, now, now + C.QUOTE_TTL))
    return get(db, cur.lastrowid)


def expire(db, now: int | None = None) -> int:
    """Void every quote and waiting order whose time is up. Returns how many."""
    now = core.now() if now is None else now
    return db.x("UPDATE store_orders SET state='expired' WHERE state IN ('quote','pending') AND expires_at<=?", (now,)).rowcount


def confirm(db, city, st, uid: int, order_id: int, now: int | None = None) -> dict:
    """The buyer confirms their quote. Returns {"state": 'done' | 'pending', "order", ...}. GameError: store_gone (not theirs / not a quote), order_done (already bought), store_expired,
    store_changed (the price moved), the customer checks, store_none, cash_low, inv_full. An expired or changed quote is voided for good (that write is kept) and THEN the error is raised."""
    now = core.now() if now is None else now
    void = None
    with db.tx():
        o = get(db, order_id)
        if o is None or o["buyer_id"] != uid:
            raise GameError("store_gone")
        if o["state"] == "done":
            raise GameError("order_done")
        if o["state"] == "pending":
            return {"state": "pending", "order": o}
        if o["state"] != "quote":
            raise GameError("store_gone")
        b = biz.get(db, o["branch_id"])
        if o["expires_at"] <= now:
            void = ("expired", "store_expired")
        else:
            check_customer(db, city, st, uid, b)
            ru = stock.rule(db, o["branch_id"], o["item_id"], o["data"])
            if ru is None or ru["price"] != o["unit_cents"]:
                void = ("cancelled", "store_changed")
        if void is not None:
            db.x("UPDATE store_orders SET state=? WHERE id=? AND state='quote'", (void[0], order_id))
        else:
            how = settler(db, b, o["total_cents"])
            if how is None:
                if db.x("UPDATE store_orders SET state='pending', expires_at=? WHERE id=? AND state='quote'", (now + C.APPROVAL_TTL, order_id)).rowcount != 1:
                    raise GameError("store_gone")
                for uid2 in approvers(db, b):
                    core.notify(db, uid2, "store_pending", brand=b["brand"], seq=b["seq"], item_id=o["item_id"], data=o["data"] or "", qty=o["qty"], cents=o["total_cents"])
                return {"state": "pending", "order": get(db, order_id)}
            res = complete(db, o, how, now)
            return {"state": "done", **res}
    raise GameError(void[1])


def settler(db, branch, total: int) -> str | None:
    """How a purchase of `total` cents completes by itself: 'accountant' (a player's or a bot's limit covers it), 'checkout' (the checkout upgrade), or None (it needs approval)."""
    a = staff.accountant(db, branch["id"])
    if a is not None and total <= staff.auto_limit(db, a):
        return "accountant"
    if total <= upgrades.effects(db, branch["id"])["selfcheck"]:
        return "checkout"
    return None


def approvers(db, branch) -> list:
    out = [branch["owner_id"]]
    for c in staff.of_branch(db, branch["id"], states=("active",)):
        if c["employee_id"] is not None and "approve" in staff.perms_of(c) and c["employee_id"] not in out:
            out.append(c["employee_id"])
    return out


def pending(db, branch_id: int, now: int | None = None) -> list:
    now = core.now() if now is None else now
    expire(db, now)
    return db.qa("SELECT * FROM store_orders WHERE branch_id=? AND state='pending' ORDER BY id", (branch_id,))


def approve(db, uid: int, order_id: int, now: int | None = None) -> dict:
    """The owner / a manager / a sales clerk / an accountant approves a waiting order: it completes now (stock and the buyer's cash are checked again).
    GameError: store_gone, branch_denied, store_none, cash_low, inv_full (the buyer's bag): the order then stays waiting."""
    now = core.now() if now is None else now
    gone = False
    with db.tx():
        o = get(db, order_id)
        if o is None or o["state"] != "pending":
            raise GameError("store_gone")
        if o["expires_at"] <= now:
            db.x("UPDATE store_orders SET state='expired' WHERE id=? AND state='pending'", (order_id,))
            gone = True
        else:
            gone = False
            b = biz.get(db, o["branch_id"])
            if b is None or b["state"] != "open" or not biz.can(db, uid, b, "approve"):
                raise GameError("branch_denied")
            return complete(db, o, f"approved:{uid}", now)
    if gone:
        raise GameError("store_gone")


def decline(db, uid: int, order_id: int, now: int | None = None) -> bool:
    now = core.now() if now is None else now
    with db.tx():
        o = get(db, order_id)
        if o is None or o["state"] != "pending":
            raise GameError("store_gone")
        b = biz.get(db, o["branch_id"])
        if b is None or not biz.can(db, uid, b, "approve"):
            raise GameError("branch_denied")
        return db.x("UPDATE store_orders SET state='declined', done_at=? WHERE id=? AND state='pending'", (now, order_id)).rowcount == 1


def cancel(db, uid: int, order_id: int) -> bool:
    """The buyer withdraws their own quote or waiting order."""
    with db.tx():
        o = get(db, order_id)
        if o is None or o["buyer_id"] != uid:
            raise GameError("store_gone")
        return db.x("UPDATE store_orders SET state='cancelled' WHERE id=? AND state IN ('quote','pending')", (order_id,)).rowcount == 1


def complete(db, o, how: str, now: int) -> dict:
    """Complete an order (inside the caller's transaction): the single place a sale happens. Everything here is one unit: any failure undoes all of it."""
    b = biz.get(db, o["branch_id"])
    if db.x("UPDATE store_orders SET state='done', settled_by=?, done_at=? WHERE id=? AND state IN ('quote','pending')", (how, now, o["id"])).rowcount != 1:
        raise GameError("order_done")
    ref = f"o{o['id']}"
    stock.remove(db, o["branch_id"], stock.DISP, o["item_id"], o["data"], o["qty"], "sale", ref, actor=o["buyer_id"], now=now, cents=o["total_cents"])
    if inventory.give(db, o["buyer_id"], o["item_id"], o["qty"], "store", ref, items.parse(o["data"]) or None, now) is None:
        raise RuntimeError("a sold item was given twice")
    bal = cash.apply(db, o["buyer_id"], -o["total_cents"], "store_buy", ref, note=f"{b['brand']} #{b['seq']}", now=now)
    if bal is None:
        raise RuntimeError("a sale was charged twice")
    biz.till_apply(db, o["branch_id"], o["total_cents"], "sale", ref, actor=o["buyer_id"], note=o["item_id"], now=now)
    stars = max(1, items.quality_of(o["data"]) or 1)
    if o["total_cents"] >= C.ACCOUNTING_MIN_SALE_CENTS:
        points = C.REP_SALE_PER_STAR * stars
        gain = upgrades.effects(db, o["branch_id"])["rep_gain"]
        rep.add(db, o["branch_id"], "sale", ref, -(-points * (10000 + gain) // 10000), o["buyer_id"], now, sale=True)
        a = staff.accountant(db, o["branch_id"])
        if a is not None and a["employee_id"] is not None and how == "accountant":
            day = datetime.datetime.fromtimestamp(now, datetime.timezone.utc).strftime("%Y%m%d")
            r2 = f"{o['branch_id']}:{o['buyer_id']}:{day}"
            skills.grant_work(db, a["employee_id"], "accounting", 1000, "job:acct", r2, now)
            if charxp.get(db, a["employee_id"]) is not None:
                charxp.grant(db, a["employee_id"], C.ACCOUNTING_XP, "job:acct", r2, now)
    estate.event(db, "sale", ref, o["buyer_id"], {"branch": o["branch_id"], "item": o["item_id"], "qty": o["qty"], "cents": o["total_cents"], "by": how}, now)
    w = staff.role_holder(db, o["branch_id"], "warehouse")
    if w is not None:
        stock.restock(db, o["branch_id"], now, actor=w["employee_id"])
    return {"order": get(db, o["id"]), "balance": bal, "how": how}


def history(db, branch_id: int, limit: int = 8) -> list:
    return db.qa("SELECT * FROM store_orders WHERE branch_id=? AND state='done' ORDER BY id DESC LIMIT ?", (branch_id, limit))
