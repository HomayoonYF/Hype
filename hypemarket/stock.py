"""What a branch holds (v0.211): the WAREHOUSE, the public DISPLAY, the owner's standing instructions and the game's buyback of surplus.

TWO PLACES. Every branch has a warehouse ('wh') and a display ('disp'). Goods in the warehouse are NOT for sale: a customer only ever sees and buys what is on the display. Moving goods from the
warehouse to the display is a separate act (`move`, or the automatic `restock` that follows the owner's instructions). Capacity: the warehouse holds `upgrades.warehouse_cap` pieces; the display
has `display_cap // DISPLAY_SLOT_ITEMS` SLOTS and a slot holds `DISPLAY_SLOT_ITEMS` pieces of ONE product variant (a small shop: 2 slots, a flagship: 24).

A VARIANT is an item id plus its data (quality, brand, production history): two pieces are the same stock only when both are equal, exactly as in a bag.

EVERY CHANGE IS LOGGED. `add` and `remove` write a row of `stock_log` (append-only, unique per branch, kind, reference, place and variant) in the same transaction as the quantity change, so the same
operation twice is a no-op and the log always adds up to the stock (`audit`). A quantity can never go below zero (a CHECK).

WHO MAY DO WHAT. The owner and anybody with the `stock` permission (a manager, a warehouse worker) can deposit goods from their own bag, set prices and instructions, move goods between the two places
and run the restock. Taking goods OUT of the branch into a bag is the OWNER's alone (there is no stealing from a business by an employee). Nobody can touch another player's bag at all.

INSTRUCTIONS (`stock_rules`, one per variant): a price (without one the piece is not for sale), a display TARGET, a warehouse reserve `keep`, whether the warehouse REFILLS the display, and whether the
SURPLUS is sold to the game (`buyback`, which only the OWNER can switch on). The surplus is what the warehouse holds beyond `keep` once the display is at its target. The game pays
`config.BUYBACK_BPS` of ITS OWN value of the piece (`fashion.base_value`), never of a price an owner typed; the owner's price is limited to `config.PRICE_MAX_MULT` times that value. Nothing is ever
sold to the game without that switch, every such sale is logged with its money, and the money goes into the branch's till.

RESTOCK. `restock` applies the instructions once: first the refills (warehouse to display, up to the target and the free slots), then the buyback of the surplus. It is a pure function of the state, so running
it twice in a row does nothing the second time. It runs by itself after every sale of a branch that has a warehouse worker (a player or a bot), and the owner / a manager can run it by hand.
"""
from __future__ import annotations

import secrets
import zlib

from . import biz, config as C, core, fashion, inventory, items, skills, upgrades
from .core import GameError

WH, DISP = "wh", "disp"
LOCS = (WH, DISP)
WAREHOUSE_RUNS_PER_DAY = 10                      # a warehouse worker earns skill from this many runs a day at most


def key(data) -> str:
    """The canonical text of an item's data ('' for none): the same form the bag stores."""
    d = items.parse(data)
    return inventory._data(d or None) or ""


def variant_of(key_: str) -> dict:
    return items.parse(key_)


def _token() -> str:
    return secrets.token_hex(6)


def _vref(prefix: str, data) -> str:
    """A reference that is different for every VARIANT of an item (the bag's log is unique per player, item, source and reference, not per variant)."""
    return f"{prefix}:{zlib.crc32(key(data).encode()):08x}"[:80]


# =====================================================================================================================
#                                                       reading
# =====================================================================================================================
def qty(db, branch_id: int, loc: str, item_id: str, data="") -> int:
    r = db.q1("SELECT qty FROM branch_stock WHERE branch_id=? AND loc=? AND item_id=? AND data=?", (branch_id, loc, item_id, key(data)))
    return r["qty"] if r else 0


def contents(db, branch_id: int, loc: str) -> list:
    """The rows of one place, in catalog order, best quality first."""
    items.ensure(db)
    rows = db.qa("SELECT item_id, data, qty FROM branch_stock WHERE branch_id=? AND loc=? AND qty>0", (branch_id, loc))
    return sorted(rows, key=lambda r: (items.order_of(r["item_id"]), r["item_id"], -(items.quality_of(r["data"]) or 0), r["data"]))


def total(db, branch_id: int, loc: str) -> int:
    return db.q1("SELECT COALESCE(SUM(qty),0) s FROM branch_stock WHERE branch_id=? AND loc=?", (branch_id, loc))["s"]


def slots_of(n: int) -> int:
    return -(-n // C.DISPLAY_SLOT_ITEMS) if n > 0 else 0


def display_slots(db, branch) -> int:
    return upgrades.display_cap(db, branch) // C.DISPLAY_SLOT_ITEMS


def slots_used(db, branch_id: int, plus: tuple | None = None) -> int:
    """Display slots in use (a variant uses ceil(pieces / slot size) of them); `plus` = (item id, data key, extra pieces) counts a planned addition."""
    n = 0
    seen = False
    for r in db.qa("SELECT item_id, data, qty FROM branch_stock WHERE branch_id=? AND loc='disp' AND qty>0", (branch_id,)):
        extra = 0
        if plus is not None and (r["item_id"], r["data"]) == (plus[0], plus[1]):
            extra, seen = plus[2], True
        n += slots_of(r["qty"] + extra)
    if plus is not None and not seen:
        n += slots_of(plus[2])
    return n


def free_warehouse(db, branch) -> int:
    return max(0, upgrades.warehouse_cap(db, branch) - total(db, branch["id"], WH))


def rule(db, branch_id: int, item_id: str, data=""):
    return db.q1("SELECT * FROM stock_rules WHERE branch_id=? AND item_id=? AND data=?", (branch_id, item_id, key(data)))


def rules(db, branch_id: int) -> list:
    items.ensure(db)
    return sorted(db.qa("SELECT * FROM stock_rules WHERE branch_id=?", (branch_id,)), key=lambda r: (items.order_of(r["item_id"]), r["item_id"], -(items.quality_of(r["data"]) or 0), r["data"]))


def value_of(item_id: str, data="") -> int:
    """The game's own value of one piece (cents): fabric by its quality, a product by its recipe and quality, anything else 0 (it has no game value, so it cannot be bought back)."""
    q = items.quality_of(data) or 0
    if item_id == "fabric":
        return C.FABRIC_GAME_CENTS.get(q, 0)
    return fashion.base_value(item_id, q) if q else 0


def buyback_price(item_id: str, data="") -> int:
    """What the game pays for one surplus piece: a share of its own value, always less than the value (0 = the game does not buy it)."""
    v = value_of(item_id, data)
    return v * C.BUYBACK_BPS // 10000 if v else 0


def max_price(item_id: str, data="") -> int:
    v = value_of(item_id, data)
    return max(100, v * C.PRICE_MAX_MULT) if v else 1000_00


def stock_value(db, branch_id: int) -> int:
    """The game's value of everything the branch holds (both places), in cents."""
    return sum(value_of(r["item_id"], r["data"]) * r["qty"] for r in db.qa("SELECT item_id, data, qty FROM branch_stock WHERE branch_id=? AND qty>0", (branch_id,)))


def audit(db, branch_id: int) -> list:
    """Problems (empty = right): every place's quantity must equal the sum of its log."""
    out = []
    logged = {(r["loc"], r["item_id"], r["data"]): r["s"] for r in db.qa("SELECT loc, item_id, data, SUM(qty) s FROM stock_log WHERE branch_id=? GROUP BY loc, item_id, data", (branch_id,))}
    held = {(r["loc"], r["item_id"], r["data"]): r["qty"] for r in db.qa("SELECT loc, item_id, data, qty FROM branch_stock WHERE branch_id=?", (branch_id,))}
    for k in set(logged) | set(held):
        if logged.get(k, 0) != held.get(k, 0):
            out.append(f"{k}: the log says {logged.get(k, 0)}, the stock holds {held.get(k, 0)}")
    return out


# =====================================================================================================================
#                                                       the two primitives
# =====================================================================================================================
def add(db, branch, loc: str, item_id: str, data, n: int, kind: str, ref: str, *, actor: int | None = None, now: int | None = None, cents: int = 0) -> bool:
    """Put `n` pieces into a place of the branch. True when done, False when this very (kind, ref) was already done. GameError: wh_full, disp_full."""
    if loc not in LOCS or not isinstance(n, int) or isinstance(n, bool) or n <= 0:
        raise ValueError("stock is added in whole positive numbers to the warehouse or the display")
    now = core.now() if now is None else now
    d = key(data)
    with db.savepoint():
        if db.q1("SELECT 1 FROM stock_log WHERE branch_id=? AND kind=? AND ref=? AND loc=? AND item_id=? AND data=?", (branch["id"], kind, ref, loc, item_id, d)) is not None:
            return False
        if loc == WH and n > free_warehouse(db, branch):
            raise GameError("wh_full", free=free_warehouse(db, branch))
        if loc == DISP and slots_used(db, branch["id"], (item_id, d, n)) > display_slots(db, branch):
            raise GameError("disp_full")
        db.x("INSERT INTO stock_log(branch_id,kind,loc,item_id,data,qty,ref,actor,cents,created_at) VALUES(?,?,?,?,?,?,?,?,?,?)", (branch["id"], kind, loc, item_id, d, n, ref, actor, cents, now))
        db.x("INSERT INTO branch_stock(branch_id,loc,item_id,data,qty) VALUES(?,?,?,?,?) ON CONFLICT(branch_id,loc,item_id,data) DO UPDATE SET qty=qty+excluded.qty", (branch["id"], loc, item_id, d, n))
    return True


def remove(db, branch_id: int, loc: str, item_id: str, data, n: int, kind: str, ref: str, *, actor: int | None = None, now: int | None = None, cents: int = 0) -> bool:
    """Take `n` pieces out of a place. True when done, False when this (kind, ref) was already done. GameError: stock_low."""
    if loc not in LOCS or not isinstance(n, int) or isinstance(n, bool) or n <= 0:
        raise ValueError("stock is removed in whole positive numbers")
    now = core.now() if now is None else now
    d = key(data)
    with db.savepoint():
        if db.q1("SELECT 1 FROM stock_log WHERE branch_id=? AND kind=? AND ref=? AND loc=? AND item_id=? AND data=?", (branch_id, kind, ref, loc, item_id, d)) is not None:
            return False
        if db.x("UPDATE branch_stock SET qty=qty-? WHERE branch_id=? AND loc=? AND item_id=? AND data=? AND qty>=?", (n, branch_id, loc, item_id, d, n)).rowcount != 1:
            raise GameError("stock_low")
        db.x("INSERT INTO stock_log(branch_id,kind,loc,item_id,data,qty,ref,actor,cents,created_at) VALUES(?,?,?,?,?,?,?,?,?,?)", (branch_id, kind, loc, item_id, d, -n, ref, actor, cents, now))
    return True


# =====================================================================================================================
#                                                       what people do
# =====================================================================================================================
def stockable(item_id: str) -> bool:
    """Only fabric and the product types of the clothing business are business stock (a bag's food or a keepsake is not)."""
    return item_id == "fabric" or item_id in fashion.PRODUCTS


def deposit(db, uid: int, branch_id: int, item_id: str, data, n: int, now: int | None = None, ref: str | None = None) -> int:
    """Move `n` pieces of one variant from the player's OWN bag into the branch's warehouse. Owner or `stock` permission. Returns how many pieces the warehouse holds of it now.
    GameError: branch_gone, branch_denied, stock_item, bad_amount, inv_few (the bag has fewer), wh_full."""
    now = core.now() if now is None else now
    ref = ref or _token()
    with db.tx():
        b = biz.need(db, uid, branch_id, "stock")
        if not stockable(item_id):
            raise GameError("stock_item")
        if not isinstance(n, int) or isinstance(n, bool) or n <= 0:
            raise GameError("bad_amount")
        d = key(data)
        if db.q1("SELECT 1 FROM stock_log WHERE branch_id=? AND kind='deposit' AND ref=? AND loc=? AND item_id=? AND data=?", (branch_id, ref, WH, item_id, d)) is not None:
            return qty(db, branch_id, WH, item_id, d)                           # the same instruction again (same reference): already done, nothing is taken twice
        if inventory.take_variant(db, uid, item_id, items.parse(d) or None, n, "branch", _vref(f"{branch_id}:{ref}", d), now) is None:
            raise GameError("bag_low")
        add(db, b, WH, item_id, d, n, "deposit", ref, actor=uid, now=now)
        return qty(db, branch_id, WH, item_id, d)


def withdraw(db, uid: int, branch_id: int, loc: str, item_id: str, data, n: int, now: int | None = None, ref: str | None = None) -> int:
    """Take `n` pieces out of a place into the OWNER's bag. OWNER ONLY. Returns the pieces left there. GameError: branch_gone, branch_denied, stock_low, inv_full, bad_amount."""
    now = core.now() if now is None else now
    ref = ref or _token()
    with db.tx():
        b = biz.get(db, branch_id)
        if b is None or b["state"] != "open":
            raise GameError("branch_gone")
        if b["owner_id"] != uid:
            raise GameError("branch_denied")
        if not isinstance(n, int) or isinstance(n, bool) or n <= 0 or loc not in LOCS:
            raise GameError("bad_amount")
        d = key(data)
        remove(db, branch_id, loc, item_id, d, n, "withdraw", ref, actor=uid, now=now)
        if inventory.give(db, uid, item_id, n, "branch", _vref(f"{branch_id}:{ref}", d), items.parse(d) or None, now) is None:
            raise GameError("bad_amount")
        return qty(db, branch_id, loc, item_id, d)


def move(db, uid: int, branch_id: int, item_id: str, data, n: int, frm: str, now: int | None = None, ref: str | None = None) -> int:
    """Move `n` pieces between the two places (warehouse -> display or back). `stock` permission. Returns the pieces now in the destination. GameError: stock_low, disp_full, wh_full, bad_amount."""
    now = core.now() if now is None else now
    ref = ref or _token()
    if frm not in LOCS or not isinstance(n, int) or isinstance(n, bool) or n <= 0:
        raise GameError("bad_amount")
    to = DISP if frm == WH else WH
    with db.tx():
        b = biz.need(db, uid, branch_id, "stock")
        d = key(data)
        remove(db, branch_id, frm, item_id, d, n, "move", ref, actor=uid, now=now)
        add(db, b, to, item_id, d, n, "move", ref, actor=uid, now=now)
        _worker_run(db, b, uid, now, ref)
        return qty(db, branch_id, to, item_id, d)


def transfer(db, uid: int, from_branch: int, to_branch: int, item_id: str, data, n: int, now: int | None = None, ref: str | None = None) -> int:
    """Send `n` pieces from the WAREHOUSE of one branch to the warehouse of another branch of the SAME OWNER in the SAME CITY (a delivery: a workshop that only produces sends its pieces to a shop).
    Needs the `stock` permission at both ends. Returns the pieces now in the destination warehouse. GameError: branch_gone, branch_denied, stock_low, wh_full, stock_item, bad_amount, transfer_far."""
    now = core.now() if now is None else now
    ref = ref or _token()
    if not isinstance(n, int) or isinstance(n, bool) or n <= 0:
        raise GameError("bad_amount")
    with db.tx():
        a = biz.need(db, uid, from_branch, "stock")
        b = biz.need(db, uid, to_branch, "stock")
        if a["id"] == b["id"] or a["owner_id"] != b["owner_id"] or a["world_id"] != b["world_id"]:
            raise GameError("transfer_far")
        if not stockable(item_id):
            raise GameError("stock_item")
        d = key(data)
        remove(db, from_branch, WH, item_id, d, n, "transfer_out", f"{ref}>{to_branch}", actor=uid, now=now)
        add(db, b, WH, item_id, d, n, "transfer_in", f"{ref}<{from_branch}", actor=uid, now=now)
        return qty(db, to_branch, WH, item_id, d)


def set_rule(db, uid: int, branch_id: int, item_id: str, data, *, price=..., target=None, keep=None, refill=None, buyback=None, now: int | None = None):
    """Set (part of) the instruction for one variant: only the named fields change. `price=None` takes the piece off sale. Owner or `stock`; `buyback` only the OWNER.
    GameError: branch_gone, branch_denied, stock_item, bad_amount, price_range, buyback_none (the game does not buy this piece)."""
    now = core.now() if now is None else now
    with db.tx():
        b = biz.need(db, uid, branch_id, "stock")
        if not stockable(item_id):
            raise GameError("stock_item")
        d = key(data)
        cur = rule(db, branch_id, item_id, d)
        vals = {"price": cur["price"] if cur else None, "target": cur["target"] if cur else 0, "keep": cur["keep"] if cur else 0, "refill": cur["refill"] if cur else 0, "buyback": cur["buyback"] if cur else 0}
        if price is not ...:
            if price is not None:
                if not isinstance(price, int) or isinstance(price, bool) or price <= 0:
                    raise GameError("bad_amount")
                if price > max_price(item_id, d):
                    raise GameError("price_range", max=max_price(item_id, d))
            vals["price"] = price
        for name, v, hi in (("target", target, 10 ** 6), ("keep", keep, 10 ** 6)):
            if v is not None:
                if not isinstance(v, int) or isinstance(v, bool) or not 0 <= v <= hi:
                    raise GameError("bad_amount")
                vals[name] = v
        for name, v in (("refill", refill), ("buyback", buyback)):
            if v is not None:
                vals[name] = 1 if v else 0
        if buyback is not None:
            on, was = bool(buyback), bool(cur and cur["buyback"])
            if on != was and b["owner_id"] != uid:
                raise GameError("branch_denied")                                   # only the OWNER switches the game's buyback on or off
            if on and not was and buyback_price(item_id, d) <= 0:
                raise GameError("buyback_none")
        db.x("INSERT INTO stock_rules(branch_id,item_id,data,price,target,keep,refill,buyback,updated_at) VALUES(?,?,?,?,?,?,?,?,?) "
             "ON CONFLICT(branch_id,item_id,data) DO UPDATE SET price=excluded.price, target=excluded.target, keep=excluded.keep, refill=excluded.refill, buyback=excluded.buyback, updated_at=excluded.updated_at",
             (branch_id, item_id, d, vals["price"], vals["target"], vals["keep"], vals["refill"], vals["buyback"], now))
        return rule(db, branch_id, item_id, d)


# =====================================================================================================================
#                                                       the restock run
# =====================================================================================================================
def restock(db, branch_id: int, now: int | None = None, actor: int | None = None) -> dict:
    """Apply the owner's instructions once. Returns {"moved": pieces moved to the display, "sold": pieces sold to the game, "cents": the money the buyback paid into the till}.
    Refills first (each rule with `refill`: up to its target, the free slots and what the warehouse holds), then the buyback (each rule with `buyback`, only once its display is at the target: the warehouse beyond `keep`)."""
    now = core.now() if now is None else now
    out = {"moved": 0, "sold": 0, "cents": 0}
    run = _token()
    with db.tx():
        b = biz.get(db, branch_id)
        if b is None or b["state"] != "open":
            raise GameError("branch_gone")
        prop = biz.property_of(db, b)
        if not _has(prop["mode"], "retail"):
            return out
        n_moves = 0
        for r in rules(db, branch_id):
            if n_moves >= C.STOCK_RUN_MAX_MOVES:
                break
            have, wh = qty(db, branch_id, DISP, r["item_id"], r["data"]), qty(db, branch_id, WH, r["item_id"], r["data"])
            if r["refill"] and have < r["target"] and wh > 0:
                want = min(r["target"] - have, wh)
                while want > 0 and slots_used(db, branch_id, (r["item_id"], r["data"], want)) > display_slots(db, b):
                    want -= 1
                if want > 0:
                    ref = f"{run}:{r['item_id']}:{r['data']}"[:80]
                    remove(db, branch_id, WH, r["item_id"], r["data"], want, "refill", ref, actor=actor, now=now)
                    add(db, b, DISP, r["item_id"], r["data"], want, "refill", ref, actor=actor, now=now)
                    out["moved"] += want
                    n_moves += 1
        for r in rules(db, branch_id):
            if not r["buyback"]:
                continue
            price = buyback_price(r["item_id"], r["data"])
            if price <= 0 or qty(db, branch_id, DISP, r["item_id"], r["data"]) < r["target"]:
                continue
            surplus = qty(db, branch_id, WH, r["item_id"], r["data"]) - r["keep"]
            if surplus <= 0:
                continue
            cents = price * surplus
            ref = f"{run}:{r['item_id']}:{r['data']}"[:80]
            remove(db, branch_id, WH, r["item_id"], r["data"], surplus, "buyback", ref, actor=actor, now=now, cents=cents)
            if biz.till_apply(db, branch_id, cents, "buyback", ref, actor=actor, note=r["item_id"], now=now) is None:
                raise RuntimeError("a buyback was paid twice")
            out["sold"] += surplus
            out["cents"] += cents
        if out["moved"] or out["sold"]:
            _worker_run(db, b, actor, now, run)
    return out


def _has(mode, need: str) -> bool:
    return mode in ("both", need)


def _worker_run(db, branch, uid, now: int, run: str) -> None:
    """A stock run by a player warehouse WORKER (not the owner or a manager) earns one unit of Warehouse skill, at most `WAREHOUSE_RUNS_PER_DAY` a day."""
    if uid is None or uid == branch["owner_id"]:
        return
    from . import staff
    if not staff.has_role(db, branch["id"], uid, "warehouse"):
        return
    day = now - now % 86400
    done = db.q1("SELECT COUNT(*) c FROM skill_log WHERE user_id=? AND skill='warehouse' AND source='job:wh' AND created_at>=?", (uid, day))["c"]
    if done < WAREHOUSE_RUNS_PER_DAY:
        skills.grant_work(db, uid, "warehouse", 1000, "job:wh", f"{branch['id']}:{run}", now)


# =====================================================================================================================
#                                                       hooks into the rest of the business world
# =====================================================================================================================
def _blockers(db, prop) -> list:
    out = []
    b = db.q1("SELECT id FROM branches WHERE property_id=? AND state='open'", (prop["id"],))
    if b is not None and db.q1("SELECT 1 FROM branch_stock WHERE branch_id=? AND qty>0 LIMIT 1", (b["id"],)) is not None:
        out.append("stock")
    return out


def _on_close(db, branch, now: int) -> None:
    """A closing branch gives every piece it holds back to its owner's bag (all or nothing: a full bag stops the closing, GameError inv_full)."""
    for loc in LOCS:
        for r in contents(db, branch["id"], loc):
            remove(db, branch["id"], loc, r["item_id"], r["data"], r["qty"], "close", f"close:{branch['id']}", actor=branch["owner_id"], now=now)
            if inventory.give(db, branch["owner_id"], r["item_id"], r["qty"], "branch", _vref(f"close:{branch['id']}:{loc}", r["data"]), items.parse(r["data"]) or None, now) is None:
                raise GameError("bad_amount")


from . import estate  # noqa: E402

estate.register_blocker_hook(_blockers)
biz.register_close_hook(_on_close)
