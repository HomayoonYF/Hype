"""The inventory (v0.21): what a player OWNS, and the only code that changes it. What an item IS lives in items.py; the screens are invui.py.

Three tables (db.py, schema 13):
* `item_inventory`   the BAG, one row per STACK: (id, player, item, quantity >= 1, when and from what the stack was started, optional item-specific data).
                     A stackable item piles up to its max stack size and then starts a new stack; a non-stackable item is one row of quantity 1 each.
* `item_equipment`   what the player WEARS: one row per (player, slot): the primary key is what makes a slot hold ONE item. An equipped item is not in the bag; taking it off puts it back.
* `item_log`         the append-only ledger of every grant and removal with its source and reference. A (player, item, source, reference) can gain once and lose once (unique
                     indexes), so a repeated message, a double tap or a retried request can never give, or take, the same thing twice.

Every change is ONE atomic block (`Database.savepoint`: a transaction, or a savepoint inside the caller's): either all of it happens or none, and nothing is cut in the middle (a bag
that is too full to take a whole grant takes none of it). Quantities are positive whole numbers; a stack never passes the item's max stack size (the code keeps it; the table's CHECK keeps every quantity at 1 or more and a trigger keeps a stack's owner and item fixed);
a player's bag never passes `config.INV_MAX_STACKS` stacks. Ownership is part of every query (`WHERE user_id = ?`): there is no function that reads or changes a stack without the
player it belongs to, so one player can never reach another's items even with a forged button.

Nothing in the game gives a player an item on its own: no migration, no sign-up, no opening of the bag. `give` is the one entry point; the admin's item gift codes (gifts.py) were its first caller and (v0.211)
the cotton job, the fabric market, the stores and the tailoring orders call it with their own source and reference.

v0.211: a stack can carry DATA (item instance data: quality `q`, brand `b`, production history `ph`: see items.parse). Two stacks merge only when their data is equal, so «a 4-star NIKE tank top»
and «a 2-star unbranded one» are different entries of the bag everywhere (the bag groups by item AND data); `count` adds the variants up, `count_variant` / `take_variant` work on one exact variant.

Errors (GameError, all with texts `e.<code>`): no_user, item_unknown, inv_full, inv_few, inv_gone, inv_stale, inv_noequip, inv_nouse, equip_empty. ValueError for programming mistakes
(a bad quantity, source, reference or data).
"""
from __future__ import annotations

import json
import re
import sqlite3
from dataclasses import dataclass

from . import config as C, core, items
from .core import GameError

SOURCE_RE = re.compile(r"[a-z][a-z0-9_:.\-]{0,39}")
BRAND_KEY_RE = re.compile(r"[a-z][a-z0-9_]{2,23}")
MAX_REF = 80
MAX_DATA = 400


class _Duplicate(Exception):
    """The same (source, reference) already gave / took this: nothing is written."""


# =====================================================================================================================
#                                                       checking what a caller passes
# =====================================================================================================================
def _qty(n, hi: int) -> int:
    if not isinstance(n, int) or isinstance(n, bool) or not 1 <= n <= hi:
        raise ValueError(f"a quantity is a whole number from 1 to {hi}")
    return n


def _source(s) -> str:
    if not isinstance(s, str) or not SOURCE_RE.fullmatch(s):
        raise ValueError("a source is a short lower-case code like 'gift'")
    return s


def _ref(r):
    if r is None:
        return None
    if not isinstance(r, str) or not r.strip() or len(r) > MAX_REF:
        raise ValueError("a reference is a non-empty text of at most %d characters" % MAX_REF)
    return r


def _data(d):
    """Item-specific data as the JSON text that is stored (None stays None): a small dict, always written in one canonical form so that equal data is equal text."""
    if d is None:
        return None
    if not isinstance(d, dict) or not d:
        raise ValueError("item data is a non-empty dict or None")
    q = d.get("q")
    if q is not None and (not isinstance(q, int) or isinstance(q, bool) or not items.QUALITY_MIN <= q <= items.QUALITY_MAX):
        raise ValueError("an item's quality is a whole number from 1 to 5")
    b = d.get("b")
    if b is not None and not (isinstance(b, str) and BRAND_KEY_RE.fullmatch(b)):
        raise ValueError("an item's brand is a brand key")
    ph = d.get("ph")
    if ph is not None and (not isinstance(ph, int) or isinstance(ph, bool) or ph < 1):
        raise ValueError("a production history id is a positive whole number")
    s = json.dumps(d, sort_keys=True, ensure_ascii=False, separators=(",", ":"))
    if len(s) > MAX_DATA:
        raise ValueError("item data is too long")
    return s


def _check_data_fits(item_id: str, data) -> None:
    """Quality only on an item that has one, a brand only on a brandable item (a programming mistake otherwise)."""
    d = items.get(item_id)
    info = items.parse(data)
    if d is not None and ("q" in info and not d.quality or "b" in info and not d.brandable):
        raise ValueError(f"{item_id}: this item does not carry that data")


def _human(db, uid: int) -> None:
    u = core.get_user(db, uid)
    if u is None or u["bot"]:
        raise GameError("no_user")


def _stack_cap(item_id: str) -> int:
    d = items.get(item_id)
    return d.max_stack if d is not None and d.stackable else 1


# =====================================================================================================================
#                                                       reading
# =====================================================================================================================
@dataclass(frozen=True)
class Entry:
    """All of one item in the bag: the total, how many stacks it takes, and the first (oldest) stack: the one a button acts on."""
    item_id: str
    qty: int
    stacks: int
    stack_id: int
    item: items.ItemDef | None
    data: str | None = None            # v0.211: the variant (canonical json of the instance data, None for plain)

    @property
    def category(self) -> str:
        return items.category_of(self.item_id)

    @property
    def info(self) -> dict:
        return items.parse(self.data)


@dataclass(frozen=True)
class Worn:
    slot: str
    item_id: str
    data: str | None
    acquired_at: int
    source: str
    equipped_at: int
    item: items.ItemDef | None


def bag(db, uid: int) -> list:
    """Every item VARIANT in the player's bag as `Entry` rows, in catalog order (items the catalog no longer knows last); variants of one item sort by quality, best first."""
    items.ensure(db)
    rows = db.qa("SELECT item_id, data, SUM(quantity) q, COUNT(*) n, MIN(id) first FROM item_inventory WHERE user_id=? GROUP BY item_id, data", (uid,))
    out = [Entry(r["item_id"], r["q"], r["n"], r["first"], items.get(r["item_id"]), r["data"]) for r in rows]
    out.sort(key=lambda e: (items.order_of(e.item_id), e.item_id, -(items.quality_of(e.data) or 0), e.data or ""))
    return out


def entries_in(db, uid: int, category: str) -> list:
    return [e for e in bag(db, uid) if e.category == category]


def entry(db, uid: int, item_id: str) -> Entry | None:
    """The first variant of `item_id` in the bag (for a plain item: the only one)."""
    return next((e for e in bag(db, uid) if e.item_id == item_id), None)


def entry_of_stack(db, uid: int, stack_id: int) -> Entry | None:
    """The bag entry (the whole variant) that the stack `stack_id` of THIS player belongs to, or None (gone, or somebody else's)."""
    row = stack(db, uid, stack_id) if isinstance(stack_id, int) and not isinstance(stack_id, bool) else None
    if row is None:
        return None
    return next((e for e in bag(db, uid) if e.item_id == row["item_id"] and e.data == row["data"]), None)


def total(db, uid: int) -> int:
    """The number of items in the bag (every unit, equipped ones not counted)."""
    return db.q1("SELECT COALESCE(SUM(quantity),0) q FROM item_inventory WHERE user_id=?", (uid,))["q"]


def stack_count(db, uid: int) -> int:
    return db.q1("SELECT COUNT(*) n FROM item_inventory WHERE user_id=?", (uid,))["n"]


def count(db, uid: int, item_id: str) -> int:
    """How many of `item_id` are in the bag (not counting a worn one)."""
    return db.q1("SELECT COALESCE(SUM(quantity),0) q FROM item_inventory WHERE user_id=? AND item_id=?", (uid, item_id))["q"]


def count_variant(db, uid: int, item_id: str, data=None) -> int:
    """How many of exactly this variant (`data` as a dict, a json text or None) are in the bag."""
    d = _data(items.parse(data) or None) if not isinstance(data, str) else data
    return db.q1("SELECT COALESCE(SUM(quantity),0) q FROM item_inventory WHERE user_id=? AND item_id=? AND data IS ?", (uid, item_id, d))["q"]


def counts_by_category(db, uid: int) -> dict:
    """{category id: number of items} for every registered category (0 for an empty one); unknown items count in the fallback category."""
    out = {cid: 0 for cid in items.CATEGORIES}
    for e in bag(db, uid):
        out[e.category] = out.get(e.category, 0) + e.qty
    return out


def stack(db, uid: int, stack_id: int):
    """The stack row `stack_id` of THIS player, or None (a stack of anybody else is None too)."""
    return db.q1("SELECT * FROM item_inventory WHERE id=? AND user_id=?", (stack_id, uid))


def worn(db, uid: int) -> dict:
    """{slot id: Worn} of the items the player wears right now."""
    items.ensure(db)
    rows = db.qa("SELECT * FROM item_equipment WHERE user_id=?", (uid,))
    return {r["slot"]: Worn(r["slot"], r["item_id"], r["data"], r["acquired_at"], r["source"], r["equipped_at"], items.get(r["item_id"])) for r in rows}


def candidates(db, uid: int, slot: str) -> list:
    """The bag entries that can be worn in `slot`."""
    return [e for e in bag(db, uid) if e.item is not None and items.can_equip(e.item) and e.item.slot == slot]


def paginate(seq: list, page: int, size: int | None = None) -> tuple:
    """(the items of that page, the page actually shown, the number of pages). A page past the end (a button of an old screen after items left) shows the last page; at least 1 page."""
    size = size or C.INV_PAGE_SIZE
    pages = max(1, -(-len(seq) // size))
    page = min(max(int(page), 1), pages)
    return seq[(page - 1) * size: page * size], page, pages


# =====================================================================================================================
#                                                       putting items in
# =====================================================================================================================
def _plan_rows(db, uid: int, item_id: str, qty: int, data) -> tuple:
    """(rows that still have room, how many NEW rows the rest needs): nothing is written."""
    cap = _stack_cap(item_id)
    if cap == 1:
        return [], qty
    room = db.qa("SELECT id, quantity FROM item_inventory WHERE user_id=? AND item_id=? AND quantity<? AND data IS ? ORDER BY id", (uid, item_id, cap, data))
    left = qty
    for r in room:
        left -= min(left, cap - r["quantity"])
        if left <= 0:
            return room, 0
    return room, -(-left // cap)


def _add(db, uid: int, item_id: str, qty: int, data, acquired_at: int, source: str) -> None:
    """Put `qty` of `item_id` into the bag: first into the stacks that have room, then into new stacks. GameError('inv_full') (nothing written) when the new stacks would pass the cap."""
    cap = _stack_cap(item_id)
    room, new_rows = _plan_rows(db, uid, item_id, qty, data)
    if stack_count(db, uid) + new_rows > C.INV_MAX_STACKS:
        raise GameError("inv_full", max=C.INV_MAX_STACKS)
    left = qty
    for r in room:
        add = min(left, cap - r["quantity"])
        if add > 0:
            db.x("UPDATE item_inventory SET quantity=quantity+? WHERE id=?", (add, r["id"]))
            left -= add
    while left > 0:
        n = min(cap, left)
        db.x("INSERT INTO item_inventory(user_id,item_id,quantity,acquired_at,source,data) VALUES(?,?,?,?,?,?)", (uid, item_id, n, acquired_at, source, data))
        left -= n


def fits(db, uid: int, item_id: str, qty: int, data: dict | None = None) -> bool:
    """Whether `give` would accept `qty` of `item_id` right now (the same check, nothing written)."""
    items.ensure(db)
    _, new_rows = _plan_rows(db, uid, item_id, qty, _data(data))
    return stack_count(db, uid) + new_rows <= C.INV_MAX_STACKS


def give(db, uid: int, item_id: str, qty: int = 1, source: str = "system", ref: str | None = None, data: dict | None = None, now: int | None = None) -> int | None:
    """Put `qty` of `item_id` into the player's bag. Returns how many were put in, or None when this (source, ref) already gave this item to this player (nothing changes then).
    With no `ref` every call gives (use a ref whenever the same event could be delivered twice). GameError: no_user (unknown player or a bot account), item_unknown,
    inv_full (the whole grant does not fit: none of it is given)."""
    qty = _qty(qty, C.INV_MAX_GIVE)
    source, ref, data = _source(source), _ref(ref), _data(data)
    now = core.now() if now is None else now
    items.ensure(db)
    try:
        with db.savepoint():
            _human(db, uid)
            if items.get(item_id) is None:
                raise GameError("item_unknown")
            _check_data_fits(item_id, data)
            if ref is not None and db.q1("SELECT 1 FROM item_log WHERE user_id=? AND item_id=? AND source=? AND ref=? AND delta>0", (uid, item_id, source, ref)):
                raise _Duplicate
            _, new_rows = _plan_rows(db, uid, item_id, qty, data)
            if stack_count(db, uid) + new_rows > C.INV_MAX_STACKS:
                raise GameError("inv_full", max=C.INV_MAX_STACKS)
            try:
                db.x("INSERT INTO item_log(user_id,item_id,delta,source,ref,created_at) VALUES(?,?,?,?,?,?)", (uid, item_id, qty, source, ref, now))
            except sqlite3.IntegrityError:
                raise _Duplicate from None
            _add(db, uid, item_id, qty, data, now, source)
            return qty
    except _Duplicate:
        return None


# =====================================================================================================================
#                                                       taking items out
# =====================================================================================================================
def _remove_units(db, uid: int, item_id: str, qty: int) -> None:
    """Remove `qty` units of `item_id` (the caller has checked there are enough): from the stacks with the fewest first, so full stacks stay full."""
    left = qty
    for r in db.qa("SELECT id, quantity FROM item_inventory WHERE user_id=? AND item_id=? ORDER BY quantity ASC, id DESC", (uid, item_id)):
        if left <= 0:
            break
        n = min(left, r["quantity"])
        if n == r["quantity"]:
            db.x("DELETE FROM item_inventory WHERE id=? AND user_id=?", (r["id"], uid))
        else:
            db.x("UPDATE item_inventory SET quantity=quantity-? WHERE id=? AND user_id=?", (n, r["id"], uid))
        left -= n
    if left > 0:
        raise RuntimeError("inventory: removal passed the stock")                      # cannot happen after the check; never leave a half removal


def take(db, uid: int, item_id: str, qty: int = 1, source: str = "system", ref: str | None = None, now: int | None = None) -> int | None:
    """Remove `qty` of `item_id` from the bag (consuming, spending: a worn item is not in the bag and is not touched). Returns how many were removed, or None when this
    (source, ref) already took this item from this player. GameError: no_user, inv_few (the bag holds fewer: nothing is taken)."""
    qty = _qty(qty, C.INV_MAX_GIVE)
    source, ref = _source(source), _ref(ref)
    now = core.now() if now is None else now
    try:
        with db.savepoint():
            _human(db, uid)
            if ref is not None and db.q1("SELECT 1 FROM item_log WHERE user_id=? AND item_id=? AND source=? AND ref=? AND delta<0", (uid, item_id, source, ref)):
                raise _Duplicate
            have = count(db, uid, item_id)
            if have < qty:
                raise GameError("inv_few", have=have, need=qty)
            try:
                db.x("INSERT INTO item_log(user_id,item_id,delta,source,ref,created_at) VALUES(?,?,?,?,?,?)", (uid, item_id, -qty, source, ref, now))
            except sqlite3.IntegrityError:
                raise _Duplicate from None
            _remove_units(db, uid, item_id, qty)
            return qty
    except _Duplicate:
        return None


def take_variant(db, uid: int, item_id: str, data, qty: int, source: str, ref: str | None = None, now: int | None = None) -> int | None:
    """Remove `qty` of exactly one VARIANT (`data` as a dict / canonical json / None) from the bag: the same book-keeping as `take`, for the systems that consume or hand over a specific
    quality (fabric of a given star, a branded product). Returns how many were removed, or None when this (source, ref) already took this item (nothing changes). GameError: no_user, inv_few."""
    qty = _qty(qty, C.INV_MAX_GIVE)
    source, ref = _source(source), _ref(ref)
    d = data if isinstance(data, str) or data is None else _data(data)
    now = core.now() if now is None else now
    try:
        with db.savepoint():
            _human(db, uid)
            if ref is not None and db.q1("SELECT 1 FROM item_log WHERE user_id=? AND item_id=? AND source=? AND ref=? AND delta<0", (uid, item_id, source, ref)):
                raise _Duplicate
            have = count_variant(db, uid, item_id, d)
            if have < qty:
                raise GameError("inv_few", have=have, need=qty)
            try:
                db.x("INSERT INTO item_log(user_id,item_id,delta,source,ref,created_at) VALUES(?,?,?,?,?,?)", (uid, item_id, -qty, source, ref, now))
            except sqlite3.IntegrityError:
                raise _Duplicate from None
            left = qty
            for r in db.qa("SELECT id, quantity FROM item_inventory WHERE user_id=? AND item_id=? AND data IS ? ORDER BY quantity ASC, id DESC", (uid, item_id, d)):
                if left <= 0:
                    break
                n = min(left, r["quantity"])
                if n == r["quantity"]:
                    db.x("DELETE FROM item_inventory WHERE id=? AND user_id=?", (r["id"], uid))
                else:
                    db.x("UPDATE item_inventory SET quantity=quantity-? WHERE id=? AND user_id=?", (n, r["id"], uid))
                left -= n
            if left > 0:
                raise RuntimeError("inventory: removal passed the stock")
            return qty
    except _Duplicate:
        return None


# =====================================================================================================================
#                                                       wearing
# =====================================================================================================================
def _take_one_from_stack(db, row) -> None:
    if row["quantity"] == 1:
        db.x("DELETE FROM item_inventory WHERE id=? AND user_id=?", (row["id"], row["user_id"]))
    else:
        db.x("UPDATE item_inventory SET quantity=quantity-1 WHERE id=? AND user_id=?", (row["id"], row["user_id"]))


def equip(db, uid: int, stack_id: int, now: int | None = None) -> dict:
    """Wear one item of the stack `stack_id` (of THIS player). The slot is the item's; an item already worn there goes back into the bag, with the acquisition record it came
    from. Returns {"item": item id, "slot": slot id, "replaced": item id or None}. GameError: inv_gone (no such stack of this player: a stale or forged button),
    inv_noequip (the item cannot be worn), inv_full (the replaced item has no room: nothing changes at all)."""
    if not isinstance(stack_id, int) or isinstance(stack_id, bool):
        raise GameError("inv_gone")
    now = core.now() if now is None else now
    items.ensure(db)
    with db.savepoint():
        row = stack(db, uid, stack_id)
        if row is None:
            raise GameError("inv_gone")
        d = items.get(row["item_id"])
        if not items.can_equip(d):
            raise GameError("inv_noequip")
        cur = db.q1("SELECT * FROM item_equipment WHERE user_id=? AND slot=?", (uid, d.slot))
        _take_one_from_stack(db, row)
        replaced, replaced_data = None, None
        if cur is not None:
            db.x("DELETE FROM item_equipment WHERE user_id=? AND slot=?", (uid, d.slot))
            _add(db, uid, cur["item_id"], 1, cur["data"], cur["acquired_at"], cur["source"])
            replaced, replaced_data = cur["item_id"], cur["data"]
        db.x("INSERT INTO item_equipment(user_id,slot,item_id,acquired_at,source,data,equipped_at) VALUES(?,?,?,?,?,?,?)",
             (uid, d.slot, row["item_id"], row["acquired_at"], row["source"], row["data"], now))
        return {"item": row["item_id"], "slot": d.slot, "replaced": replaced, "data": row["data"], "replaced_data": replaced_data}


def unequip(db, uid: int, slot: str) -> str:
    """Take off what the player wears in `slot`: it returns to the bag (with its own acquisition record). Returns the item id. GameError: equip_empty (nothing worn there: a stale
    button), inv_full (no room in the bag: it stays worn)."""
    with db.savepoint():
        cur = db.q1("SELECT * FROM item_equipment WHERE user_id=? AND slot=?", (uid, slot))
        if cur is None:
            raise GameError("equip_empty")
        db.x("DELETE FROM item_equipment WHERE user_id=? AND slot=?", (uid, slot))
        _add(db, uid, cur["item_id"], 1, cur["data"], cur["acquired_at"], cur["source"])
        return cur["item_id"]


# =====================================================================================================================
#                                                       using
# =====================================================================================================================
def use(db, uid: int, stack_id: int, expected_qty: int, now: int | None = None) -> dict:
    """Use one unit of the stack `stack_id`: the registered effect handler runs and ONE unit is consumed, in one atomic block (an effect that raises consumes nothing).
    `expected_qty` is the quantity the pressed button was drawn with: a stack that has changed since (the same button pressed twice, an item that left) is refused with
    inv_stale, so a repeated callback can never consume a second unit. Returns {"item": item id, "message": the handler's {"fa", "en"} or None}.
    GameError: inv_gone, inv_stale, inv_nouse (no registered effect: there is nothing to do), or whatever the effect raises."""
    if not isinstance(stack_id, int) or isinstance(stack_id, bool) or not isinstance(expected_qty, int) or isinstance(expected_qty, bool):
        raise GameError("inv_gone")
    now = core.now() if now is None else now
    items.ensure(db)
    with db.savepoint():
        row = stack(db, uid, stack_id)
        if row is None:
            raise GameError("inv_gone")
        if row["quantity"] != expected_qty:
            raise GameError("inv_stale")
        d = items.get(row["item_id"])
        if not items.can_use(d):
            raise GameError("inv_nouse")
        message = items.EFFECTS[d.effect](db, uid, d, now)
        _take_one_from_stack(db, row)
        db.x("INSERT INTO item_log(user_id,item_id,delta,source,ref,created_at) VALUES(?,?,?,?,?,?)", (uid, row["item_id"], -1, "use", None, now))
        return {"item": row["item_id"], "message": message, "data": row["data"]}


# =====================================================================================================================
#                                                       checking the books
# =====================================================================================================================
def audit(db, uid: int) -> list:
    """The problems found in one player's items (an empty list when every rule holds): the same checks the tests make, available to an admin tool or a future repair script.
    Reads only. It does not use the catalog's current list to call a stack wrong (an item that left the catalog is not a problem)."""
    items.ensure(db)
    out = []
    for r in db.qa("SELECT id, item_id, quantity FROM item_inventory WHERE user_id=?", (uid,)):
        cap = _stack_cap(r["item_id"])
        if r["quantity"] < 1:
            out.append(f"stack {r['id']}: quantity {r['quantity']}")
        if r["quantity"] > cap and items.get(r["item_id"]) is not None:
            out.append(f"stack {r['id']}: {r['quantity']} of {r['item_id']} is over the stack size {cap}")
    for slot, w in worn(db, uid).items():
        if w.item is not None and (not w.item.equippable or w.item.slot != slot):
            out.append(f"slot {slot}: {w.item_id} cannot be worn there")
        if slot not in items.SLOTS:
            out.append(f"slot {slot}: not a registered slot")
    owned = {}
    for e in bag(db, uid):
        owned[e.item_id] = owned.get(e.item_id, 0) + e.qty
    for slot, w in worn(db, uid).items():
        owned[w.item_id] = owned.get(w.item_id, 0) + 1
    logged = {r["item_id"]: r["s"] for r in db.qa("SELECT item_id, SUM(delta) s FROM item_log WHERE user_id=? GROUP BY item_id", (uid,))}
    for item_id in set(owned) | set(logged):
        if owned.get(item_id, 0) != logged.get(item_id, 0):
            out.append(f"{item_id}: owns {owned.get(item_id, 0)} but the log adds up to {logged.get(item_id, 0)}")
    return out
