"""The admin's item gift flow (v0.211): «ساخت گیفت کارت» → 🎒 آیتم.

    category → a template of that category (or ➕ a new one: a fashion product type or a custom item) → quantity per player → quality (where the item has one) → brand (where it can carry one)
    → how many different players → review → ✅ the code.

Everything is buttons except the typed answers (a name, a description, the quantity, the number of players). The draft lives in memory only (`App.gift_drafts`): NOTHING is stored before the final
confirmation except a TEMPLATE the admin explicitly finishes making (a template is a catalog entry, not a grant; see itemtpl.py). A cancel, a timeout or a restart leaves no code behind; the
confirm button carries the draft's nonce and consumes the draft first, so a double tap makes at most one code. Every press re-checks that the presser is an admin.

Callback data (after «ad:»): gc <category> · gi <page> · gp <template id> · gn · gb <product id | custom> · gs <slot | none> · gk <1|0> · gq <1-5> · gr <brand key | none>. Each acts only on the
step the draft is in, so an old button of an earlier step is answered «this draft is no longer active» and changes nothing.
"""
from __future__ import annotations

import secrets

from . import config as C, core, fashion, fmt, giftui, gifts, inventory, itemtpl, items
from .core import GameError
from .texts import t
from .views import Reply, kb

GIFT_AMOUNT, GIFT_USES = "giftamt", "giftuses"
GIFT_NAME, GIFT_EN, GIFT_DESC = "giftname", "gifteng", "giftdesc"
GIFT_PROMPTS = (GIFT_AMOUNT, GIFT_USES, GIFT_NAME, GIFT_EN, GIFT_DESC)
TEXT_STEPS = {GIFT_NAME: "nname", GIFT_EN: "nen", GIFT_DESC: "ndesc", GIFT_AMOUNT: "amount", GIFT_USES: "uses"}
TTL = 600
SKIP = {"-", "—", "–", "_", "ندارد", "skip", "no", "none"}


def _screen(text: str, rows: list, edit: bool = True) -> Reply:
    return Reply(text=text, kb=kb(rows), edit=edit)


def draft(app, uid: int):
    d = app.gift_drafts.get(uid)
    if d is not None and d["exp"] <= core.now():
        app.gift_drafts.pop(uid, None)
        return None
    return d


def arm(app, uid: int, key: str) -> None:
    app.pending[uid] = (key, core.now() + TTL)


def end(app, uid: int) -> None:
    app.gift_drafts.pop(uid, None)
    if app.pending.get(uid, ("",))[0] in GIFT_PROMPTS:
        app.pending.pop(uid, None)


def touch(d: dict, step: str) -> None:
    d["step"], d["exp"] = step, core.now() + TTL


def cancel_row(lang: str, uid: int) -> list:
    return [(t(lang, "gift.b.cancel"), f"ad:gx:{uid}")]


def stale(lang: str, uid: int, edit: bool = True) -> Reply:
    return _screen(t(lang, "gift.stale"), [[(t(lang, "gift.b.again"), f"ad:gift:{uid}")], [(t(lang, "ad.b.main"), f"ad:main:{uid}")]], edit)


def new_draft(app, uid: int) -> dict:
    d = {"type": "item", "step": "cat", "cat": None, "tpl": None, "item": None, "data": {}, "amount": None, "uses": None, "new": {}, "nonce": secrets.token_hex(4), "exp": core.now() + TTL}
    app.gift_drafts[uid] = d
    return d


# =====================================================================================================================
#                                                       the screens
# =====================================================================================================================
def cat_screen(lang: str, uid: int) -> Reply:
    cats = [(f"{c.icon} {items.category_name(lang, cid)}", f"ad:gc:{cid}:{uid}") for cid, c in items.CATEGORIES.items()]
    rows = [cats[i:i + 2] for i in range(0, len(cats), 2)]
    rows.append(cancel_row(lang, uid))
    return _screen(t(lang, "gift.cat"), rows)


def tpl_screen(app, lang: str, uid: int, d: dict, page: int) -> Reply:
    cid = d["cat"]
    rows_all = itemtpl.listing(app.db, cid)
    shown, page, pages = inventory.paginate(rows_all, page)
    rows = [[(f"{r['icon']} {items.name(lang, r['item_id'])}", f"ad:gp:{r['id']}:{uid}")] for r in shown]
    nav = []
    if page > 1:
        nav.append((t(lang, "gift.b.prev"), f"ad:gi:{page - 1}:{uid}"))
    if page < pages:
        nav.append((t(lang, "gift.b.next"), f"ad:gi:{page + 1}:{uid}"))
    if nav:
        rows.append(nav)
    rows.append([(t(lang, "gift.b.newtpl"), f"ad:gn:{uid}")])
    rows.append([(t(lang, "gift.b.cats"), f"ad:gift:{uid}"), cancel_row(lang, uid)[0]])
    pages_text = t(lang, "gift.pick.pages", page=fmt.num(page, lang), pages=fmt.num(pages, lang)) if pages > 1 else ""
    body = t(lang, "gift.tpl", cat=fmt.esc(items.category_name(lang, cid)), pages=pages_text) if rows_all else t(lang, "gift.tpl.none", cat=fmt.esc(items.category_name(lang, cid)))
    return _screen(body, rows)


def newbase_screen(lang: str, uid: int, d: dict) -> Reply:
    cid = d["cat"]
    prods = [(f"{fashion.PRODUCTS[p].icon} {items.name(lang, p)}", f"ad:gb:{p}:{uid}") for p in fashion.product_ids(cid)]
    rows = [prods[i:i + 2] for i in range(0, len(prods), 2)]
    rows.append([(t(lang, "gift.b.custom"), f"ad:gb:custom:{uid}")])
    rows.append([(t(lang, "gift.b.back"), f"ad:gc:{cid}:{uid}"), cancel_row(lang, uid)[0]])
    return _screen(t(lang, "gift.newbase", cat=fmt.esc(items.category_name(lang, cid))), rows)


def ask_text(lang: str, uid: int, key: str, note: str = "", edit: bool = True) -> Reply:
    body = t(lang, {GIFT_NAME: "gift.ask_name", GIFT_EN: "gift.ask_en", GIFT_DESC: "gift.ask_desc"}[key])
    return _screen((note + "\n\n" if note else "") + body, [cancel_row(lang, uid)], edit)


def slot_screen(lang: str, uid: int, d: dict) -> Reply:
    slots = [(f"{items.SLOTS[s].icon} {items.slot_name(lang, s)}", f"ad:gs:{s}:{uid}") for s in itemtpl.slots_for(d["cat"]) if s in items.SLOTS]
    rows = [slots[i:i + 2] for i in range(0, len(slots), 2)]
    rows.append([(t(lang, "gift.slot.none"), f"ad:gs:none:{uid}")])
    rows.append(cancel_row(lang, uid))
    return _screen(t(lang, "gift.ask_slot"), rows)


def stack_screen(lang: str, uid: int) -> Reply:
    return _screen(t(lang, "gift.ask_stack"), [[(t(lang, "gift.b.stack"), f"ad:gk:1:{uid}"), (t(lang, "gift.b.single"), f"ad:gk:0:{uid}")], cancel_row(lang, uid)])


def amount_screen(lang: str, uid: int, d: dict, edit: bool = True, note: str = "") -> Reply:
    body = t(lang, "gift.ask_amount", reward=reward_label(lang, d), unit=t(lang, "gift.unit.item"), max=fmt.num(gifts.limit("item"), lang))
    return _screen((note + "\n\n" if note else "") + body, [cancel_row(lang, uid)], edit)


def quality_screen(lang: str, uid: int, d: dict) -> Reply:
    row = [(items.stars(q), f"ad:gq:{q}:{uid}") for q in range(items.QUALITY_MIN, items.QUALITY_MAX + 1)]
    rows = [row[:3], row[3:]]
    rows.append(cancel_row(lang, uid))
    return _screen(t(lang, "gift.ask_quality", item=fmt.esc(items.name(lang, d["item"]))), rows)


def brand_screen(lang: str, uid: int, d: dict) -> Reply:
    brands = [(items.brand_label(k), f"ad:gr:{k}:{uid}") for k in C.brand_names()]
    rows = [brands[i:i + 2] for i in range(0, len(brands), 2)]
    rows.append([(t(lang, "gift.b.nobrand"), f"ad:gr:none:{uid}")])
    rows.append(cancel_row(lang, uid))
    return _screen(t(lang, "gift.ask_brand", item=fmt.esc(items.name(lang, d["item"]))), rows)


def reward_label(lang: str, d: dict) -> str:
    base = t(lang, "gift.b.item")
    if not d.get("item"):
        return base
    return base + " • " + fmt.esc(items.display_name(lang, d["item"], d.get("data") or None)) + (" " + items.stars(d["data"]["q"]) if d.get("data", {}).get("q") else "")


def uses_screen(lang: str, uid: int, d: dict, edit: bool = True, note: str = "") -> Reply:
    what = giftui.what(lang, "item", d["amount"], d["item"], d.get("data") or None)
    body = t(lang, "gift.ask_uses", what=what, max=fmt.num(C.GIFT_MAX_USES, lang))
    return _screen((note + "\n\n" if note else "") + body, [cancel_row(lang, uid)], edit)


def confirm_screen(lang: str, uid: int, d: dict, edit: bool = True) -> Reply:
    data = d.get("data") or None
    each = d["amount"]
    body = t(lang, "gift.confirm", what=giftui.what(lang, "item", each, d["item"], data), uses=fmt.num(d["uses"], lang), total=giftui.what(lang, "item", each * d["uses"], d["item"], data))
    return _screen(body, [[(t(lang, "gift.b.ok"), f"ad:gok:{d['nonce']}:{uid}"), (t(lang, "gift.b.cancel"), f"ad:gx:{uid}")]], edit)


# =====================================================================================================================
#                                                       moving through the steps
# =====================================================================================================================
def _item_chosen(app, lang: str, uid: int, d: dict, item_id: str) -> Reply:
    d["item"], d["data"] = item_id, {}
    touch(d, "amount")
    arm(app, uid, GIFT_AMOUNT)
    return amount_screen(lang, uid, d)


def after_amount(app, lang: str, uid: int, d: dict, edit: bool) -> Reply:
    """The quantity is known: the next question is the quality (an item that has one), then the brand (one that can carry one), else the number of players."""
    if items.has_quality(d["item"]):
        touch(d, "quality")
        return quality_screen(lang, uid, d)
    return _after_quality(app, lang, uid, d)


def _after_quality(app, lang: str, uid: int, d: dict) -> Reply:
    if items.is_brandable(d["item"]) and C.brand_names():
        touch(d, "brand")
        return brand_screen(lang, uid, d)
    touch(d, "uses")
    arm(app, uid, GIFT_USES)
    return uses_screen(lang, uid, d)


def _finish_template(app, lang: str, uid: int, d: dict) -> Reply:
    n = d["new"]
    try:
        row = itemtpl.create_custom(app.db, uid, n.get("fa"), n.get("en"), d["cat"], n.get("desc") or "", n.get("slot"), bool(n.get("stack")))
    except (ValueError, GameError):
        end(app, uid)
        return _screen(t(lang, "gift.failed"), [[(t(lang, "gift.b.again"), f"ad:gift:{uid}")], [(t(lang, "ad.b.main"), f"ad:main:{uid}")]])
    d["new"] = {}
    r = _item_chosen(app, lang, uid, d, row["item_id"])
    r.text = t(lang, "gift.tpl.made", name=fmt.esc(row["name_fa"] if lang != "en" else (row["name_en"] or row["name_fa"]))) + "\n\n" + r.text
    return r


def start(app, lang: str, uid: int) -> Reply:
    new_draft(app, uid)
    return cat_screen(lang, uid)


async def press(app, lang: str, uid: int, sub: str, parts: list) -> Reply:
    """One button of the item flow (`sub` is the first part of the callback data, `parts` all of them). Admin only (the caller checked)."""
    d = draft(app, uid)
    arg = parts[1] if len(parts) > 1 else ""
    if d is None or d.get("type") != "item":
        return stale(lang, uid)
    step = d["step"]
    if sub == "gc":
        if step not in ("cat", "tpl", "newbase") or arg not in items.CATEGORIES:
            return stale(lang, uid)
        d["cat"], d["tpl"], d["item"] = arg, None, None
        touch(d, "tpl")
        return tpl_screen(app, lang, uid, d, 1)
    if sub == "gi":
        if step != "tpl":
            return stale(lang, uid)
        return tpl_screen(app, lang, uid, d, int(arg) if arg.isascii() and arg.isdigit() and len(arg) < 6 else 1)
    if sub == "gp":
        if step != "tpl":
            return stale(lang, uid)
        row = itemtpl.get(app.db, int(arg)) if arg.isascii() and arg.isdigit() and len(arg) < 10 else None
        if row is None or row["category"] != d["cat"]:
            return Reply(toast=t(lang, "e.bad_usage"))
        items.ensure(app.db)
        if items.get(row["item_id"]) is None:
            return Reply(toast=t(lang, "e.item_unknown"), alert=True)
        d["tpl"] = row["id"]
        return _item_chosen(app, lang, uid, d, row["item_id"])
    if sub == "gn":
        if step != "tpl":
            return stale(lang, uid)
        touch(d, "newbase")
        if d["cat"] in itemtpl.QUALITY_CATEGORIES and fashion.product_ids(d["cat"]):
            return newbase_screen(lang, uid, d)
        d["new"] = {}
        touch(d, "nname")
        arm(app, uid, GIFT_NAME)
        return ask_text(lang, uid, GIFT_NAME)
    if sub == "gb":
        if step != "newbase":
            return stale(lang, uid)
        if arg == "custom":
            d["new"] = {}
            touch(d, "nname")
            arm(app, uid, GIFT_NAME)
            return ask_text(lang, uid, GIFT_NAME)
        p = fashion.PRODUCTS.get(arg)
        if p is None or p.category != d["cat"]:
            return Reply(toast=t(lang, "e.bad_usage"))
        row = itemtpl.create_product(app.db, uid, arg)
        d["tpl"] = row["id"]
        r = _item_chosen(app, lang, uid, d, row["item_id"])
        return r
    if sub == "gs":
        if step != "nslot":
            return stale(lang, uid)
        if arg != "none" and arg not in itemtpl.slots_for(d["cat"]):
            return Reply(toast=t(lang, "e.bad_usage"))
        d["new"]["slot"] = None if arg == "none" else arg
        touch(d, "nstack")
        return stack_screen(lang, uid)
    if sub == "gk":
        if step != "nstack" or arg not in ("0", "1"):
            return stale(lang, uid) if step != "nstack" else Reply(toast=t(lang, "e.bad_usage"))
        d["new"]["stack"] = arg == "1"
        return _finish_template(app, lang, uid, d)
    if sub == "gq":
        if step != "quality":
            return stale(lang, uid)
        if not (arg.isascii() and arg.isdigit() and items.QUALITY_MIN <= int(arg) <= items.QUALITY_MAX):
            return Reply(toast=t(lang, "e.bad_usage"))
        d["data"]["q"] = int(arg)
        return _after_quality(app, lang, uid, d)
    if sub == "gr":
        if step != "brand":
            return stale(lang, uid)
        if arg != "none":
            if arg not in C.brand_names():
                return Reply(toast=t(lang, "e.bad_usage"))
            d["data"]["b"] = arg
        touch(d, "uses")
        arm(app, uid, GIFT_USES)
        return uses_screen(lang, uid, d)
    return Reply(toast=t(lang, "e.bad_usage"))


async def text(app, lang: str, uid: int, key: str, text_: str) -> Reply:
    """The admin's typed answer to one of the flow's questions. A wrong answer repeats the question and keeps the draft."""
    d = draft(app, uid)
    if d is None or d["step"] != TEXT_STEPS.get(key):
        return stale(lang, uid, False)
    if key in (GIFT_NAME, GIFT_EN, GIFT_DESC):
        skip = text_.strip().lower() in SKIP
        if key == GIFT_NAME and skip:
            arm(app, uid, key)
            return ask_text(lang, uid, key, t(lang, "gift.bad_text"), False)
        limit = itemtpl.MAX_DESC if key == GIFT_DESC else itemtpl.MAX_NAME
        val = None if skip else itemtpl.clean_text(text_, limit)
        if not skip and val is None:
            arm(app, uid, key)
            return ask_text(lang, uid, key, t(lang, "gift.bad_text"), False)
        d["new"][{GIFT_NAME: "fa", GIFT_EN: "en", GIFT_DESC: "desc"}[key]] = val
        nxt = {GIFT_NAME: GIFT_EN, GIFT_EN: GIFT_DESC}.get(key)
        if nxt is not None:
            touch(d, TEXT_STEPS[nxt])
            arm(app, uid, nxt)
            return ask_text(lang, uid, nxt, edit=False)
        app.pending.pop(uid, None)
        if itemtpl.slots_for(d["cat"]):
            touch(d, "nslot")
            return slot_screen(lang, uid, d)
        touch(d, "nstack")
        return stack_screen(lang, uid)
    if key == GIFT_AMOUNT:
        n = gifts.parse_whole(text_, 1, gifts.limit("item"))
        if n is None:
            arm(app, uid, GIFT_AMOUNT)
            return amount_screen(lang, uid, d, False, t(lang, "gift.bad_amount", max=fmt.num(gifts.limit("item"), lang)))
        d["amount"] = n
        app.pending.pop(uid, None)
        return after_amount(app, lang, uid, d, False)
    n = gifts.parse_uses(text_)
    if n is None:
        arm(app, uid, GIFT_USES)
        return uses_screen(lang, uid, d, False, t(lang, "gift.bad_uses", max=fmt.num(C.GIFT_MAX_USES, lang)))
    d["uses"] = n
    touch(d, "confirm")
    app.pending.pop(uid, None)
    return confirm_screen(lang, uid, d, False)
