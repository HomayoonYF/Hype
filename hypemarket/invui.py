"""What the player sees and presses in the inventory (v0.21). Every screen is TEXT with inline buttons (no picture, no web page); the rules are inventory.py's, the item names items.py's.

    کوله‌پشتی ─┬─ one button per CATEGORY (with its count) ── the items of the category, six to a page ── an item: details, 🎽 wear, ✨ use
               └─ 🎽 تجهیزات ── one button per SLOT ── the worn item (↩️ take off) and what can be worn there

The way in: the command `کوله‌پشتی` (inventory), a button of the main menu and a button of the character hub. Buttons are `iv:<action>:<arguments>:<player id>`; the player id is the opener's and
`handlers.on_callback` refuses anybody else's press before anything runs (`iv` is a BOUND prefix); the stacks a button names are looked up WITH the presser's id as well, so a forged button
cannot reach another player's items either. A screen replaces the message the button was on (one message per click). Pressing the same button again only draws the same screen, or finds
the thing already done and says so: wearing, taking off and using are each applied to the CURRENT state of the bag, so no tap or retry can do them twice by accident (using also carries the
quantity the button was drawn with, so a second press of the same button is refused).

    action  meaning                                       arguments
    m       the bag                                       -
    c       a category                                    category id, page
    o       an item                                       item id, page of its category
    e       wear one item                                 stack id, page (of the screen it was pressed on)
    u       take off                                      slot id
    q       the equipment                                 -
    s       a slot                                        slot id, page
    x       use one item                                  stack id, quantity drawn, page
"""
from __future__ import annotations

from . import character, config as C, fmt, inventory as I, items
from .core import GameError
from .texts import t
from .views import Reply, kb

PREFIX = "iv"
STALE = ("inv_gone", "inv_stale", "equip_empty")                 # a button of an old screen: the screen is drawn again, with a note
SOURCES = ("gift", "system", "use")                              # acquisition sources that have a name of their own (any other shows as the plain code)
MAX_INT_LEN = 12


def _cb(*parts, uid: int) -> str:
    return ":".join((PREFIX, *(str(p) for p in parts), str(uid)))


def _num(n: int, lang: str) -> str:
    return fmt.num(n, lang)


def _int(s) -> int | None:
    return int(s) if isinstance(s, str) and s.isascii() and s.isdigit() and len(s) <= MAX_INT_LEN else None


def _owner(db, uid: int) -> str:
    ch = character.get(db, uid)
    return fmt.esc(f"{ch['first_name']} {ch['last_name']}") if ch is not None and ch["first_name"] else ""


def _item(lang: str, item_id: str, data=None) -> str:
    """The name of one item instance (a brand in front, as items.display_name writes it), escaped for HTML."""
    return fmt.esc(items.display_name(lang, item_id, data))


def _stars(data) -> str:
    q = items.quality_of(data)
    return items.stars(q) if q else ""


def _label(lang: str, e) -> str:
    """A button label: icon, name (brand), stars, and ×quantity when there is more than one."""
    base = f"{items.icon(e.item_id)} {items.display_name(lang, e.item_id, e.data)}"
    stars = _stars(e.data)
    return base + (f" {stars}" if stars else "") + (f" ×{_num(e.qty, lang)}" if e.qty > 1 else "")


def _with_note(note: str, body: str) -> str:
    return (note + "\n\n" + body) if note else body


def _pager(lang: str, kind: str, key, page: int, pages: int, uid: int) -> list:
    row = []
    if page > 1:
        row.append((t(lang, "iv.b.prev"), _cb(kind, key, page - 1, uid=uid)))
    if page < pages:
        row.append((t(lang, "iv.b.next"), _cb(kind, key, page + 1, uid=uid)))
    return row


# =====================================================================================================================
#                                                       the screens
# =====================================================================================================================
def main_reply(db, lang: str, uid: int, note: str = "") -> Reply:
    """The bag: the totals, one line and one button per category, the equipment button. An empty bag with nothing worn says so (and only offers the way back)."""
    name = _owner(db, uid)
    back = [(t(lang, "iv.b.hub"), f"cg:hub:{uid}")]
    entries = I.bag(db, uid)
    total = sum(e.qty for e in entries)
    worn = I.worn(db, uid)
    if total == 0 and not worn:
        return Reply(text=_with_note(note, t(lang, "iv.empty", name=name)), kb=kb([back]))
    counts = I.counts_by_category(db, uid)
    lines = "\n".join(t(lang, "iv.main.line", icon=c.icon, cat=fmt.esc(items.category_name(lang, cid)), n=_num(counts.get(cid, 0), lang)) for cid, c in items.CATEGORIES.items())
    text = t(lang, "iv.main", name=name, total=_num(total, lang), kinds=_num(len(entries), lang), stacks=_num(I.stack_count(db, uid), lang), cap=_num(C.INV_MAX_STACKS, lang),
             worn=_num(len(worn), lang), slots=_num(len(items.SLOTS), lang), lines=lines)
    cats = [(f"{c.icon} {items.category_name(lang, cid)}" + (f" ({_num(counts.get(cid, 0), lang)})" if counts.get(cid) else ""), _cb("c", cid, 1, uid=uid)) for cid, c in items.CATEGORIES.items()]
    rows = [cats[i:i + 2] for i in range(0, len(cats), 2)]
    rows.append([(t(lang, "iv.b.eq"), _cb("q", uid=uid))])
    rows.append(back)
    return Reply(text=_with_note(note, text), kb=kb(rows))


def cat_reply(db, lang: str, uid: int, cid: str, page: int, note: str = "") -> Reply:
    c = items.category(cid)
    if c is None:
        return main_reply(db, lang, uid, t(lang, "iv.stale"))
    entries = I.entries_in(db, uid, cid)
    shown, page, pages = I.paginate(entries, page)
    pages_text = t(lang, "iv.cat.pages", page=_num(page, lang), pages=_num(pages, lang)) if pages > 1 else ""
    body = t(lang, "iv.cat.body") if entries else t(lang, "iv.cat.none")
    text = t(lang, "iv.cat", icon=c.icon, cat=fmt.esc(items.category_name(lang, cid)), name=_owner(db, uid), n=_num(sum(e.qty for e in entries), lang), pages=pages_text, body=body)
    rows = [[(_label(lang, e), _cb("o", e.stack_id, page, uid=uid))] for e in shown]
    nav = _pager(lang, "c", cid, page, pages, uid)
    if nav:
        rows.append(nav)
    rows.append([(t(lang, "iv.b.back"), _cb("m", uid=uid))])
    return Reply(text=_with_note(note, text), kb=kb(rows))


def _source_name(lang: str, source: str) -> str:
    return t(lang, f"iv.src.{source}") if source in SOURCES else fmt.esc(source)


def item_reply(db, lang: str, uid: int, stack_id: int, page: int, note: str = "") -> Reply:
    """One item (one VARIANT: its quality and brand are part of what it is): its name, category, description, how many are in the bag, what it can do, and the buttons for the actions that really exist for it."""
    e = I.entry_of_stack(db, uid, stack_id)
    if e is None:                                                       # it left the bag since the list was drawn
        return cat_reply(db, lang, uid, _last_cat(db, uid, stack_id), page, t(lang, "iv.stale"))
    item_id = e.item_id
    d = e.item
    first = I.stack(db, uid, e.stack_id)
    cid = e.category
    cat = items.category(cid)
    lines = [t(lang, "iv.l.qty", n=_num(e.qty, lang))]
    q = items.quality_of(e.data)
    if q:
        lines.append(t(lang, "iv.l.quality", stars=items.stars(q)))
    brand = e.info.get("b")
    if brand:
        lines.append(t(lang, "iv.l.brand", brand=fmt.ltr(fmt.esc(items.brand_label(brand)))))
    if d is not None and d.stackable:
        lines.append(t(lang, "iv.l.stack", n=_num(d.max_stack, lang)))
    if d is not None and items.can_equip(d):
        lines.append(t(lang, "iv.l.slot", slot=fmt.esc(items.slot_name(lang, d.slot))))
        w = I.worn(db, uid).get(d.slot)
        if w is not None and w.item_id == item_id and w.data == e.data:
            lines.append(t(lang, "iv.l.worn"))
    if first is not None:
        lines.append(t(lang, "iv.l.got", date=fmt.date(first["acquired_at"], lang), source=_source_name(lang, first["source"])))
    english = ""
    if lang == "fa" and d is not None and d.name_en:
        english = t(lang, "iv.item.en", en=fmt.ltr(fmt.esc(d.name_en)))
    text = t(lang, "iv.item", icon=items.icon(item_id), name=_item(lang, item_id, e.data), en=english, cat_icon=cat.icon if cat else "📦", cat=fmt.esc(items.category_name(lang, cid)),
             desc=fmt.esc(items.description(lang, item_id)), lines="\n".join(lines))
    acts = []
    if items.can_equip(d):
        acts.append((t(lang, "iv.b.equip"), _cb("e", e.stack_id, page, uid=uid)))
    if items.can_use(d) and first is not None:
        acts.append((t(lang, "iv.b.use"), _cb("x", e.stack_id, first["quantity"], page, uid=uid)))
    rows = [acts] if acts else []
    rows.append([(t(lang, "iv.b.cat", cat=items.category_name(lang, cid)), _cb("c", cid, page, uid=uid))])
    return Reply(text=_with_note(note, text), kb=kb(rows))


def _last_cat(db, uid: int, stack_id: int) -> str:
    """The category to go back to when the stack of an old button is gone: the fallback one (the screen it came from is not stored in the button)."""
    return items.FALLBACK_CATEGORY


def eq_reply(db, lang: str, uid: int, note: str = "") -> Reply:
    """The equipment: every slot with what is worn in it (or «empty»), and a button per slot."""
    worn = I.worn(db, uid)
    lines = []
    for sid, s in items.SLOTS.items():
        w = worn.get(sid)
        lines.append(t(lang, "iv.eq.line", icon=s.icon, slot=fmt.esc(items.slot_name(lang, sid)), item=_item(lang, w.item_id, w.data) + (" " + _stars(w.data) if _stars(w.data) else "")) if w else t(lang, "iv.eq.empty", icon=s.icon, slot=fmt.esc(items.slot_name(lang, sid))))
    slots = [(f"{s.icon} {items.slot_name(lang, sid)}", _cb("s", sid, 1, uid=uid)) for sid, s in items.SLOTS.items()]
    rows = [slots[i:i + 2] for i in range(0, len(slots), 2)]
    rows.append([(t(lang, "iv.b.back"), _cb("m", uid=uid))])
    return Reply(text=_with_note(note, t(lang, "iv.eq", name=_owner(db, uid), lines="\n".join(lines))), kb=kb(rows))


def slot_reply(db, lang: str, uid: int, sid: str, page: int, note: str = "") -> Reply:
    """One slot: the item worn there (with a button to take it off) and the items of the bag that can be worn there (a button each)."""
    s = items.slot(sid)
    if s is None:
        return eq_reply(db, lang, uid, t(lang, "iv.stale"))
    w = I.worn(db, uid).get(sid)
    worn_text = t(lang, "iv.slot.worn", icon=items.icon(w.item_id), item=_item(lang, w.item_id, w.data) + (" " + _stars(w.data) if _stars(w.data) else ""), desc=fmt.esc(items.description(lang, w.item_id))) if w else t(lang, "iv.slot.none")
    cands = I.candidates(db, uid, sid)
    shown, page, pages = I.paginate(cands, page)
    pages_text = t(lang, "iv.cat.pages", page=_num(page, lang), pages=_num(pages, lang)) if pages > 1 else ""
    body = t(lang, "iv.slot.pick", pages=pages_text) if cands else t(lang, "iv.slot.nothing")
    rows = []
    if w:
        rows.append([(t(lang, "iv.b.unequip"), _cb("u", sid, uid=uid))])
    rows += [[(t(lang, "iv.b.wear", item=_label(lang, e)), _cb("e", e.stack_id, page, uid=uid))] for e in shown]
    nav = _pager(lang, "s", sid, page, pages, uid)
    if nav:
        rows.append(nav)
    rows.append([(t(lang, "iv.b.eq_back"), _cb("q", uid=uid))])
    text = t(lang, "iv.slot", icon=s.icon, slot=fmt.esc(items.slot_name(lang, sid)), name=_owner(db, uid), worn=worn_text, body=body)
    return Reply(text=_with_note(note, text), kb=kb(rows))


# =====================================================================================================================
#                                                       the buttons
# =====================================================================================================================
def _do_equip(db, lang: str, uid: int, stack_id: int) -> Reply:
    r = I.equip(db, uid, stack_id)
    note = t(lang, "iv.ok.equip", item=_item(lang, r["item"], r.get("data")))
    if r["replaced"]:
        note += t(lang, "iv.ok.replaced", old=_item(lang, r["replaced"], r.get("replaced_data")))
    return eq_reply(db, lang, uid, note)


def _do_unequip(db, lang: str, uid: int, slot: str) -> Reply:
    w = I.worn(db, uid).get(slot)
    item_id = I.unequip(db, uid, slot)
    return eq_reply(db, lang, uid, t(lang, "iv.ok.unequip", item=_item(lang, item_id, w.data if w else None)))


def _do_use(db, lang: str, uid: int, stack_id: int, qty: int, page: int) -> Reply:
    r = I.use(db, uid, stack_id, qty)
    note = t(lang, "iv.ok.use", item=_item(lang, r["item"], r.get("data")))
    msg = r["message"]
    if isinstance(msg, dict) and msg.get(lang):
        note += "\n" + fmt.esc(msg[lang])
    left = next((e for e in I.bag(db, uid) if e.item_id == r["item"] and e.data == r.get("data")), None)
    if left is None:
        return cat_reply(db, lang, uid, items.category_of(r["item"]), page, note)
    return item_reply(db, lang, uid, left.stack_id, page, note)


def press(db, lang: str, uid: int, rest: list) -> Reply:
    """One button (`rest` is the callback data without the leading «iv» and the trailing player id). A hand-made or outdated button answers with a short note, never an error page."""
    act = rest[0] if rest else "m"
    a = rest[1:]
    bad = Reply(toast=t(lang, "iv.bad"))
    if act == "m" and not a:
        return main_reply(db, lang, uid)
    if act == "q" and not a:
        return eq_reply(db, lang, uid)
    if act == "c" and len(a) == 2 and _int(a[1]) is not None:
        return cat_reply(db, lang, uid, a[0], _int(a[1]))
    if act == "o" and len(a) == 2 and _int(a[0]) is not None and _int(a[1]) is not None:
        return item_reply(db, lang, uid, _int(a[0]), _int(a[1]))
    if act == "s" and len(a) == 2 and items.ID_RE.fullmatch(a[0]) and _int(a[1]) is not None:
        return slot_reply(db, lang, uid, a[0], _int(a[1]))
    try:
        if act == "e" and len(a) == 2 and _int(a[0]) is not None and _int(a[1]) is not None:
            return _do_equip(db, lang, uid, _int(a[0]))
        if act == "u" and len(a) == 1 and items.ID_RE.fullmatch(a[0]):
            return _do_unequip(db, lang, uid, a[0])
        if act == "x" and len(a) == 3 and all(_int(x) is not None for x in a):
            return _do_use(db, lang, uid, _int(a[0]), _int(a[1]), _int(a[2]))
    except GameError as e:
        if e.code in STALE:                                             # an old button: nothing changed; show the real state with a note
            r = main_reply(db, lang, uid, t(lang, "iv.stale"))
            r.toast = _plain(t(lang, "e." + e.code))
            return r
        raise
    return bad


def _plain(text: str) -> str:
    import html
    import re
    return html.unescape(re.sub(r"<[^>]+>", "", text))[:190]


# =====================================================================================================================
#                                                       opening it
# =====================================================================================================================
def _gate(c, unfinished):
    """(None, None) when the player may open the inventory, else (reply or None, True): the set-up for an unfinished character, nothing for an account that has none (a bot)."""
    ch = character.ensure_profile(c.db, c.uid)
    if ch is None:
        return None, True
    if not character.is_done(ch):
        return unfinished(c, ch), True
    return None, None


async def command(c, unfinished) -> Reply | None:
    """`کوله‌پشتی` typed as a command: the bag, as a new message. None for an account that has no character (a bot)."""
    reply, stop = _gate(c, unfinished)
    if stop:
        return reply
    return main_reply(c.db, c.lang, c.uid)


async def callback(c, unfinished) -> Reply | None:
    """`iv:<action>:...:<player id>` (the player id, the last part, was already checked against the presser by the dispatcher): the screen replaces the message the button was on."""
    reply, stop = _gate(c, unfinished)
    if stop:
        return reply
    r = press(c.db, c.lang, c.uid, c.args[:-1])
    r.edit = r.edit or r.text is not None
    r.fresh = True
    return r
