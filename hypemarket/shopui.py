"""The business world as the player sees it (v0.211), part 2: stock and display, production orders, the material ads, the shop floor (a customer's purchase) and the approvals of waiting orders.
The conventions (buttons, owner-bound presses, re-reading the saved state, pictures) are bizui.py's; the rules are stock.py, tailor.py, market.py and store.py."""
from __future__ import annotations

import zlib

from . import biz, brands, cash, config as C, core, estate, fashion, fmt, inventory, items, market, presence, rep, staff, stock, store, tailor, textile, upgrades
from .bizui import (NoWhere, R, _cb, _front, _known, branch_text, brand_text, city_name, num, plain, usd, where, who)
from .core import GameError
from .texts import t

PAGE = 6


# =====================================================================================================================
#                                                       variants (the short name of a stock row in a button)
# =====================================================================================================================
def vtok(item_id: str, data) -> str:
    return f"{zlib.crc32((item_id + '|' + stock.key(data)).encode()):08x}"


def variants(db, bid: int) -> list:
    """Every variant the branch holds or has an instruction for: [(item id, canonical data)] in catalog order, best quality first."""
    seen: dict = {}
    for loc in stock.LOCS:
        for r in stock.contents(db, bid, loc):
            seen[(r["item_id"], r["data"])] = True
    for r in stock.rules(db, bid):
        seen[(r["item_id"], r["data"])] = True
    return sorted(seen, key=lambda k: (items.order_of(k[0]), k[0], -(items.quality_of(k[1]) or 0), k[1]))


def variant_of(db, bid: int, tok: str):
    for k in variants(db, bid):
        if vtok(*k) == tok:
            return k
    return None


def vname(lang: str, item_id: str, data) -> str:
    q = items.quality_of(data)
    return f"{items.icon(item_id)} {fmt.esc(items.display_name(lang, item_id, data))}" + (f" {items.stars(q)}" if q else "")


def _stock_branch(db, uid: int, bid: int):
    return biz.need(db, uid, bid, "stock")


# =====================================================================================================================
#                                                       stock and display
# =====================================================================================================================
def stock_screen(c, bid: int, page: int = 1, toast: str | None = None):
    lang, db, uid = c.lang, c.db, c.uid
    b = _stock_branch(db, uid, bid)
    prop = biz.property_of(db, b)
    owner = b["owner_id"] == uid
    vs = variants(db, bid)
    pages = max(1, -(-len(vs) // PAGE))
    page = max(1, min(page, pages))
    lines = [t(lang, "bz.st.head", branch=branch_text(b), wh=num(stock.total(db, bid, stock.WH), lang), cap=num(upgrades.warehouse_cap(db, b), lang), slots=num(stock.slots_used(db, bid), lang),
               slotcap=num(stock.display_slots(db, b), lang), size=num(C.DISPLAY_SLOT_ITEMS, lang))]
    rows = []
    for item_id, data in vs[(page - 1) * PAGE:page * PAGE]:
        wh, dp = stock.qty(db, bid, stock.WH, item_id, data), stock.qty(db, bid, stock.DISP, item_id, data)
        ru = stock.rule(db, bid, item_id, data)
        price = usd(ru["price"], lang) if ru and ru["price"] else t(lang, "bz.st.notsale")
        lines.append(t(lang, "bz.st.line", name=vname(lang, item_id, data), wh=num(wh, lang), disp=num(dp, lang), price=price))
        rows.append([(t(lang, "bz.st.btn", name=plain(vname(lang, item_id, data)), wh=num(wh, lang), disp=num(dp, lang)), _cb("sv", bid, vtok(item_id, data), uid=uid))])
    if not vs:
        lines.append(t(lang, "bz.st.empty"))
    nav = []
    if page > 1:
        nav.append((t(lang, "iv.b.prev"), _cb("s", bid, page - 1, uid=uid)))
    if page < pages:
        nav.append((t(lang, "iv.b.next"), _cb("s", bid, page + 1, uid=uid)))
    if nav:
        rows.append(nav)
    rows.append([(t(lang, "bz.b.deposit"), _cb("sd", bid, 1, uid=uid))] + ([(t(lang, "bz.b.restock"), _cb("sR", bid, uid=uid))] if estate.mode_allows(prop["mode"], "retail") else []))
    rows.append([(t(lang, "bz.b.branch", branch=plain(branch_text(b))), _cb("bo", bid, uid=uid))])
    return R(c, "\n".join(lines), rows, toast=toast)


def _presets(item_id: str, data) -> list:
    """Price buttons for a variant: multiples of the game's value, within the ceiling, deduplicated (cents)."""
    v, top = stock.value_of(item_id, data), stock.max_price(item_id, data)
    out = []
    for bps in (10000, 12500, 15000, 20000, 30000):
        p = min(top, max(1, v * bps // 10000)) if v else 0
        if p and p not in out:
            out.append(p)
    return out


def variant_screen(c, bid: int, tok: str, toast: str | None = None):
    from . import tailor as _t
    lang, db, uid = c.lang, c.db, c.uid
    b = _stock_branch(db, uid, bid)
    owner = b["owner_id"] == uid
    k = variant_of(db, bid, tok)
    if k is None:
        raise GameError("stock_gone")
    item_id, data = k
    wh, dp = stock.qty(db, bid, stock.WH, item_id, data), stock.qty(db, bid, stock.DISP, item_id, data)
    ru = stock.rule(db, bid, item_id, data)
    value, top = stock.value_of(item_id, data), stock.max_price(item_id, data)
    lines = [t(lang, "bz.sv.head", name=vname(lang, item_id, data)), t(lang, "bz.sv.qty", wh=num(wh, lang), disp=num(dp, lang))]
    if ru and ru["price"]:
        lines.append(t(lang, "bz.sv.price", price=usd(ru["price"], lang)))
    else:
        lines.append(t(lang, "bz.sv.noprice"))
    if value:
        lines.append(t(lang, "bz.sv.value", value=usd(value, lang), top=usd(top, lang)))
    r = ru or {"target": 0, "keep": 0, "refill": 0, "buyback": 0}
    lines.append(t(lang, "bz.sv.rule", target=num(r["target"], lang), keep=num(r["keep"], lang), refill=t(lang, "bz.on" if r["refill"] else "bz.off"), buyback=t(lang, "bz.on" if r["buyback"] else "bz.off")))
    if r["buyback"]:
        lines.append(t(lang, "bz.sv.buyback", price=usd(stock.buyback_price(item_id, data), lang)))
    lines += _t.history_lines(db, lang, data)
    rows = []
    if wh:
        rows.append([(t(lang, "bz.b.todisp", n=num(n, lang)), _cb("sm", bid, tok, "wh", n, uid=uid)) for n in (1, 5)] + [(t(lang, "bz.b.todisp_all"), _cb("sm", bid, tok, "wh", 0, uid=uid))])
    if dp:
        rows.append([(t(lang, "bz.b.towh", n=num(n, lang)), _cb("sm", bid, tok, "disp", n, uid=uid)) for n in (1, 5)] + [(t(lang, "bz.b.towh_all"), _cb("sm", bid, tok, "disp", 0, uid=uid))])
    pre = _presets(item_id, data)
    if pre:
        rows.append([(usd(p, lang), _cb("sp", bid, tok, p, uid=uid)) for p in pre[:3]])
        if len(pre) > 3:
            rows.append([(usd(p, lang), _cb("sp", bid, tok, p, uid=uid)) for p in pre[3:]])
    rows.append([(t(lang, "bz.b.offsale"), _cb("sp", bid, tok, 0, uid=uid)), (t(lang, "bz.b.refill", state=t(lang, "bz.on" if r["refill"] else "bz.off")), _cb("sr", bid, tok, uid=uid))])
    rows.append([(t(lang, "bz.b.target", n=num(n, lang)), _cb("st", bid, tok, n, uid=uid)) for n in (0, 5, 10, 20)])
    rows.append([(t(lang, "bz.b.keep", n=num(n, lang)), _cb("sk", bid, tok, n, uid=uid)) for n in (0, 5, 10, 20)])
    if owner:
        if stock.buyback_price(item_id, data) > 0:
            rows.append([(t(lang, "bz.b.buyback", state=t(lang, "bz.on" if r["buyback"] else "bz.off")), _cb("sbb", bid, tok, uid=uid))])
        rows.append([(t(lang, "bz.b.take", n=num(n, lang)), _cb("sw", bid, tok, "wh" if wh else "disp", n, uid=uid)) for n in (1, 5)] + [(t(lang, "bz.b.take_all"), _cb("sw", bid, tok, "wh" if wh else "disp", 0, uid=uid))])
        if wh and len([x for x in biz.of_owner(db, uid, open_only=True) if x["world_id"] == b["world_id"] and x["id"] != bid]) > 0:
            rows.append([(t(lang, "bz.b.transfer"), _cb("sx", bid, tok, uid=uid))])
    rows.append([(t(lang, "bz.b.stock"), _cb("s", bid, 1, uid=uid))])
    return R(c, "\n".join(lines), rows, toast=toast)


def stock_move(c, bid: int, tok: str, frm: str, n: int):
    lang, db, uid = c.lang, c.db, c.uid
    k = variant_of(db, bid, tok)
    if k is None or frm not in stock.LOCS:
        raise GameError("stock_gone")
    have = stock.qty(db, bid, frm, k[0], k[1])
    n = have if n == 0 else n
    if n <= 0:
        raise GameError("stock_low")
    if frm == stock.WH:                                                                # the display is limited by its slots: move as many as fit, never fail half-way
        b = biz.get(db, bid)
        while n > 0 and stock.slots_used(db, bid, (k[0], k[1], n)) > stock.display_slots(db, b):
            n -= 1
        if n <= 0:
            raise GameError("disp_full")
    stock.move(db, uid, bid, k[0], k[1], min(n, have), frm, core.now())
    return variant_screen(c, bid, tok, toast=plain(t(lang, "bz.sv.moved", n=num(min(n, have), lang))))


def stock_withdraw(c, bid: int, tok: str, loc: str, n: int):
    lang, db, uid = c.lang, c.db, c.uid
    k = variant_of(db, bid, tok)
    if k is None or loc not in stock.LOCS:
        raise GameError("stock_gone")
    have = stock.qty(db, bid, loc, k[0], k[1])
    n = have if n == 0 else n
    if n <= 0:
        raise GameError("stock_low")
    stock.withdraw(db, uid, bid, loc, k[0], k[1], min(n, have), core.now())
    r = stock_screen(c, bid, 1) if variant_of(db, bid, tok) is None else variant_screen(c, bid, tok)
    r.toast = plain(t(lang, "bz.sv.taken", n=num(min(n, have), lang)))
    return r


def stock_price(c, bid: int, tok: str, cents: int):
    lang, db, uid = c.lang, c.db, c.uid
    k = variant_of(db, bid, tok)
    if k is None:
        raise GameError("stock_gone")
    if cents != 0 and cents not in _presets(*k):
        raise GameError("bad_amount")
    stock.set_rule(db, uid, bid, k[0], k[1], price=(cents or None), now=core.now())
    return variant_screen(c, bid, tok, toast=plain(t(lang, "bz.sv.priced", price=usd(cents, lang) if cents else t(lang, "bz.st.notsale"))))


def stock_number(c, bid: int, tok: str, field: str, n: int):
    lang, db, uid = c.lang, c.db, c.uid
    k = variant_of(db, bid, tok)
    if k is None:
        raise GameError("stock_gone")
    if n not in (0, 5, 10, 20):
        raise GameError("bad_amount")
    stock.set_rule(db, uid, bid, k[0], k[1], now=core.now(), **{field: n})
    return variant_screen(c, bid, tok, toast=plain(t(lang, "bz.sv.set", n=num(n, lang))))


def stock_toggle(c, bid: int, tok: str, field: str):
    lang, db, uid = c.lang, c.db, c.uid
    k = variant_of(db, bid, tok)
    if k is None:
        raise GameError("stock_gone")
    ru = stock.rule(db, bid, k[0], k[1])
    now_on = bool(ru and ru[field])
    stock.set_rule(db, uid, bid, k[0], k[1], now=core.now(), **{field: not now_on})
    return variant_screen(c, bid, tok, toast=plain(t(lang, "bz.on" if not now_on else "bz.off")))


def stock_restock(c, bid: int):
    lang, db, uid = c.lang, c.db, c.uid
    _stock_branch(db, uid, bid)
    out = stock.restock(db, bid, core.now(), actor=uid)
    return stock_screen(c, bid, 1, toast=plain(t(lang, "bz.st.restocked", moved=num(out["moved"], lang), sold=num(out["sold"], lang), cents=usd(out["cents"], lang))))


def transfer_list(c, bid: int, tok: str):
    lang, db, uid = c.lang, c.db, c.uid
    b = biz.get(db, bid)
    if b is None or b["state"] != "open" or b["owner_id"] != uid:
        raise GameError("branch_denied")
    k = variant_of(db, bid, tok)
    if k is None:
        raise GameError("stock_gone")
    lines = [t(lang, "bz.tx.head", name=vname(lang, k[0], k[1]), wh=num(stock.qty(db, bid, stock.WH, k[0], k[1]), lang))]
    rows = []
    for o in biz.of_owner(db, uid, open_only=True):
        if o["id"] == bid or o["world_id"] != b["world_id"]:
            continue
        lines.append(t(lang, "bz.tx.line", branch=branch_text(o)))
        rows.append([(t(lang, "bz.tx.btn", branch=plain(branch_text(o)), n=num(n, lang)), _cb("sX", bid, tok, o["id"], n, uid=uid)) for n in (1, 5)] + [(t(lang, "bz.tx.all", branch=""), _cb("sX", bid, tok, o["id"], 0, uid=uid))])
    rows.append([(t(lang, "bz.b.back"), _cb("sv", bid, tok, uid=uid))])
    return R(c, "\n".join(lines), rows)


def transfer_do(c, bid: int, tok: str, to: int, n: int):
    lang, db, uid = c.lang, c.db, c.uid
    k = variant_of(db, bid, tok)
    if k is None:
        raise GameError("stock_gone")
    have = stock.qty(db, bid, stock.WH, k[0], k[1])
    n = have if n == 0 else n
    if n <= 0:
        raise GameError("stock_low")
    stock.transfer(db, uid, bid, to, k[0], k[1], min(n, have), core.now())
    r = stock_screen(c, bid, 1) if variant_of(db, bid, tok) is None else variant_screen(c, bid, tok)
    r.toast = plain(t(lang, "bz.tx.done", n=num(min(n, have), lang), branch=plain(branch_text(biz.get(db, to)))))
    return r


# --- deposit from the bag --------------------------------------------------------------------------------------------
def deposit_list(c, bid: int, page: int):
    lang, db, uid = c.lang, c.db, c.uid
    b = _stock_branch(db, uid, bid)
    es = [e for e in inventory.bag(db, uid) if stock.stockable(e.item_id)]
    pages = max(1, -(-len(es) // PAGE))
    page = max(1, min(page, pages))
    lines = [t(lang, "bz.dp.head", branch=branch_text(b), free=num(stock.free_warehouse(db, b), lang))]
    rows = []
    for e in es[(page - 1) * PAGE:page * PAGE]:
        lines.append(t(lang, "bz.dp.line", name=vname(lang, e.item_id, e.data), n=num(e.qty, lang)))
        rows.append([(t(lang, "bz.dp.btn", name=plain(vname(lang, e.item_id, e.data)), n=num(e.qty, lang)), _cb("sdi", bid, e.stack_id, uid=uid))])
    if not es:
        lines.append(t(lang, "bz.dp.none"))
    nav = []
    if page > 1:
        nav.append((t(lang, "iv.b.prev"), _cb("sd", bid, page - 1, uid=uid)))
    if page < pages:
        nav.append((t(lang, "iv.b.next"), _cb("sd", bid, page + 1, uid=uid)))
    if nav:
        rows.append(nav)
    rows.append([(t(lang, "bz.b.stock"), _cb("s", bid, 1, uid=uid))])
    return R(c, "\n".join(lines), rows)


def deposit_pick(c, bid: int, stack_id: int):
    lang, db, uid = c.lang, c.db, c.uid
    b = _stock_branch(db, uid, bid)
    e = inventory.entry_of_stack(db, uid, stack_id)
    if e is None or not stock.stockable(e.item_id):
        raise GameError("inv_gone")
    text = t(lang, "bz.dp.pick", name=vname(lang, e.item_id, e.data), n=num(e.qty, lang), free=num(stock.free_warehouse(db, b), lang))
    rows = [[(t(lang, "bz.dp.n", n=num(n, lang)), _cb("sdn", bid, stack_id, n, uid=uid)) for n in (1, 5, 10) if n < e.qty] + [(t(lang, "bz.dp.all", n=num(e.qty, lang)), _cb("sdn", bid, stack_id, 0, uid=uid))],
            [(t(lang, "bz.b.back"), _cb("sd", bid, 1, uid=uid))]]
    return R(c, text, rows)


def deposit_do(c, bid: int, stack_id: int, n: int):
    lang, db, uid = c.lang, c.db, c.uid
    e = inventory.entry_of_stack(db, uid, stack_id)
    if e is None or not stock.stockable(e.item_id):
        raise GameError("inv_gone")
    n = e.qty if n == 0 else n
    stock.deposit(db, uid, bid, e.item_id, e.data, min(n, e.qty), core.now())
    r = deposit_list(c, bid, 1)
    r.toast = plain(t(lang, "bz.dp.done", n=num(min(n, e.qty), lang)))
    return r


# =====================================================================================================================
#                                                       production
# =====================================================================================================================
def prod_screen(c, bid: int, toast: str | None = None):
    lang, db, uid = c.lang, c.db, c.uid
    b = biz.need(db, uid, bid, "produce")
    prop = biz.property_of(db, b)
    if not estate.mode_allows(prop["mode"], "production"):
        raise GameError("recipe_mode")
    now = core.now()
    o = tailor.working(db, bid)
    rows = []
    lines = [t(lang, "bz.pr.head", branch=branch_text(b), machines=num(upgrades.level_of(db, bid, "machines"), lang), q=items.stars(max(1, min(prop["max_quality"], C.COMMERCIAL_MAX_QUALITY))))]
    if o is not None:
        st = tailor.state(o, now)
        lines.append(t(lang, "bz.pr.order", product=fmt.esc(items.display_name(lang, o["product"], None)), n=num(o["pieces"], lang), stars=items.stars(o["quality"]),
                       who=t(lang, "bz.pr.bot") if o["bot"] else who(db, o["worker_id"]),
                       left=t(lang, "bz.bo.ready") if st == "ready" else fmt.dur(o["due_at"] - now, lang)))
        if o["worker_id"] == uid and st == "ready":
            rows.append([(t(lang, "bz.b.collect"), _cb("pc", bid, o["id"], uid=uid))])
        if st == "working" and o["worker_id"] == uid:
            lines.append(t(lang, "bz.pr.stay"))
        rows.append([(t(lang, "bz.b.refresh"), _cb("pr", bid, uid=uid)), (t(lang, "bz.b.cancelorder"), _cb("pXa", bid, o["id"], uid=uid))])
    else:
        lines.append(t(lang, "bz.pr.idle"))
        level = skills_level(db, uid)
        for pid, r in fashion.RECIPES.items():
            if not r.craftable:
                continue
            ok = level >= r.min_skill
            need = "" if ok else t(lang, "bz.pr.needskill", n=num(r.min_skill, lang))
            eq = (" " + t(lang, "bz.pr.needs", eq=", ".join(t(lang, f"bz.upg.{n}") for n in r.needs))) if r.needs else ""
            lines.append(t(lang, "bz.pr.recipe", name=vname(lang, pid, None).split(" ⭐")[0], units=num(r.materials[0][1], lang), mins=fmt.dur(r.seconds, lang), need=need, eq=eq))
            rows.append([(t(lang, "bz.pr.btn", name=plain(vname(lang, pid, None)), lock="" if ok else "🔒"), _cb("pp", bid, pid, uid=uid))])
        if staff.bot_role(db, bid, "production") is not None:
            lines.append(t(lang, "bz.pr.hasbot"))
    last = tailor.recent(db, bid, 3)
    if last:
        lines.append(t(lang, "bz.pr.recent"))
        for x in last:
            lines.append(t(lang, "bz.pr.row", id=num(x["id"], lang), product=fmt.esc(items.display_name(lang, x["product"], None)), n=num(x["pieces"], lang), stars=items.stars(x["quality"]), state=t(lang, f"bz.ostate.{x['state']}")))
    rows.append([(t(lang, "bz.b.branch", branch=plain(branch_text(b))), _cb("bo", bid, uid=uid))])
    return R(c, "\n".join(lines), rows, toast=toast)


def skills_level(db, uid: int) -> int:
    from . import skills
    return skills.level_of(db, uid, "tailoring")


def prod_fabric(c, bid: int, product: str):
    """Step 1: which fabric quality (from the warehouse)."""
    lang, db, uid = c.lang, c.db, c.uid
    b = biz.need(db, uid, bid, "produce")
    r = fashion.recipe_of(product)
    if r is None or not r.craftable:
        raise GameError("recipe_none")
    have = tailor.fabric_qualities(db, bid)
    lines = [t(lang, "bz.pp.head", name=vname(lang, product, None).split(" ⭐")[0], units=num(r.materials[0][1], lang))]
    rows = []
    for q in sorted(have, reverse=True):
        lines.append(t(lang, "bz.pp.fab", stars=items.stars(q), n=num(have[q], lang)))
        rows.append([(t(lang, "bz.pp.fabbtn", stars=items.stars(q), n=num(have[q], lang)), _cb("pq", bid, product, q, uid=uid))])
    if not have:
        lines.append(t(lang, "bz.pp.nofabric"))
        rows.append([(t(lang, "bz.b.ads"), _cb("am", bid, uid=uid)), (t(lang, "bz.b.deposit"), _cb("sd", bid, 1, uid=uid))])
    rows.append([(t(lang, "bz.b.prod"), _cb("pr", bid, uid=uid))])
    return R(c, "\n".join(lines), rows)


def prod_pieces(c, bid: int, product: str, q: int):
    lang, db, uid = c.lang, c.db, c.uid
    biz.need(db, uid, bid, "produce")
    if fashion.recipe_of(product) is None or q not in range(1, 6):
        raise GameError("recipe_none")
    text = t(lang, "bz.pq.head", name=vname(lang, product, None).split(" ⭐")[0], stars=items.stars(q), max=num(C.TAILOR_MAX_BATCH, lang))
    ns = [1, 3, 5, 10, C.TAILOR_MAX_BATCH]
    rows = [[(num(n, lang), _cb("pn", bid, product, q, n, uid=uid)) for n in ns[:3]], [(num(n, lang), _cb("pn", bid, product, q, n, uid=uid)) for n in ns[3:]], [(t(lang, "bz.b.back"), _cb("pp", bid, product, uid=uid))]]
    return R(c, text, rows)


def prod_plan(c, bid: int, product: str, q: int, n: int):
    lang, db, uid = c.lang, c.db, c.uid
    b = biz.need(db, uid, bid, "produce")
    r = fashion.recipe_of(product)
    if r is None or not r.craftable or q not in range(1, 6):
        raise GameError("recipe_none")
    pl = tailor.plan(db, b, product, n, q, uid)
    lines = [t(lang, "bz.pn.head", name=vname(lang, product, None).split(" ⭐")[0], n=num(n, lang), stars=items.stars(q))]
    if "quality" in pl:
        f = pl["factors"]
        lines.append(t(lang, "bz.pn.result", stars=items.stars(pl["quality"]), limit=t(lang, f"bz.limit.{pl['limit']}")))
        lines.append(t(lang, "bz.pn.factors", m=items.stars(f["material"]), s=items.stars(f["skill"]), e=items.stars(f["equipment"]), p=items.stars(f["property"])))
        lines.append(t(lang, "bz.pn.cost", units=num(pl["units"], lang), have=num(pl["have"], lang), secs=fmt.dur(pl["seconds"], lang)))
    if pl["problems"]:
        lines.append(t(lang, "bz.pn.problems", list=" • ".join(t(lang, f"bz.prob.{p}") for p in pl["problems"])))
    rows = []
    if pl["ok"]:
        rows.append([(t(lang, "bz.b.startme"), _cb("pS", bid, product, q, n, 0, uid=uid))])
    if staff.bot_role(db, bid, "production") is not None:
        bp = tailor.plan(db, b, product, n, q, None)
        if bp["ok"]:
            rows.append([(t(lang, "bz.b.startbot", q=items.stars(bp["quality"])), _cb("pS", bid, product, q, n, 1, uid=uid))])
    rows.append([(t(lang, "bz.b.back"), _cb("pq", bid, product, q, uid=uid))])
    return R(c, "\n".join(lines), rows)


async def prod_start(c, bid: int, product: str, q: int, n: int, bot: int):
    lang, db, uid = c.lang, c.db, c.uid
    city, st = await where(c)
    o = tailor.start(db, city, uid, bid, product, n, q, core.now(), bot=bool(bot))
    return prod_screen(c, bid, toast=plain(t(lang, "bz.pr.started", n=num(o["pieces"], lang), stars=items.stars(o["quality"]))))


def prod_claim(c, bid: int, oid: int):
    lang, db, uid = c.lang, c.db, c.uid
    out = tailor.claim(db, uid, oid, core.now())
    text = t(lang, "bz.pr.claimed", n=num(out["pieces"], lang), name=fmt.esc(items.display_name(lang, out["product"], None)), stars=items.stars(out["quality"]), xp=num(out["xp"], lang))
    if out.get("level_to") and out["level_to"] > (out.get("level_from") or 0):
        from . import skills
        text += "\n" + t(lang, "bz.pr.levelup", level=num(out["level_to"], lang), title=skills.title(lang, out["level_to"]))
    return R(c, text, [[(t(lang, "bz.b.stock"), _cb("s", bid, 1, uid=uid))], [(t(lang, "bz.b.prod"), _cb("pr", bid, uid=uid))]])


def prod_cancel_ask(c, bid: int, oid: int):
    lang, db, uid = c.lang, c.db, c.uid
    biz.need(db, uid, bid, "produce")
    o = tailor.get(db, oid)
    if o is None or o["branch_id"] != bid or o["state"] != "working":
        raise GameError("biz_stale")
    return R(c, t(lang, "bz.pr.cancelask", n=num(o["pieces"], lang), name=fmt.esc(items.display_name(lang, o["product"], None))),
             [[(t(lang, "bz.b.confirm_cancelorder"), _cb("pX", bid, oid, uid=uid))], [(t(lang, "bz.b.back"), _cb("pr", bid, uid=uid))]])


def prod_cancel(c, bid: int, oid: int):
    lang, db, uid = c.lang, c.db, c.uid
    biz.need(db, uid, bid, "produce")
    o = tailor.get(db, oid)
    if o is None or o["branch_id"] != bid:
        raise GameError("biz_stale")
    if not tailor.cancel_order(db, uid, oid, core.now()):
        raise GameError("biz_stale")
    return prod_screen(c, bid, toast=plain(t(lang, "bz.pr.cancelled")))


# =====================================================================================================================
#                                                       material ads
# =====================================================================================================================
def _ad_line(db, lang: str, a) -> str:
    return t(lang, "bz.ad.line", branch=fmt.ltr(fmt.esc(f"{brands.name_of(a['brand'])} #{a['seq']}")), q=items.stars(a["min_q"]), unit=usd(a["unit_cents"], lang), left=num(a["qty_left"], lang),
             total=num(a["qty_total"], lang), time=fmt.dur(max(0, a["expires_at"] - core.now()), lang))


async def board_screen(c, page: int):
    """The ad board of the player's city (the same for everybody): what businesses want to buy, newest first."""
    lang, db, uid = c.lang, c.db, c.uid
    try:
        city, st = await where(c)
        wid = city.id
    except NoWhere as e:
        if e.reply.text is None and e.reply.toast:
            wid = None
        else:
            raise
    now = core.now()
    total = market.count_open(db, now, wid)
    pages = max(1, -(-total // C.AD_BOARD_PAGE))
    page = max(1, min(page, pages))
    ads = market.board(db, now, wid, C.AD_BOARD_PAGE, (page - 1) * C.AD_BOARD_PAGE)
    lines = [t(lang, "bz.ab.head", city=city_name(db, lang, wid) if wid else "—", n=num(total, lang))]
    rows = []
    mine = textile.fabric_by_quality(db, uid)
    for a in ads:
        lines.append(_ad_line(db, lang, a))
        rows.append([(t(lang, "bz.ab.btn", branch=plain(fmt.esc(f"{brands.name_of(a['brand'])} #{a['seq']}")), q=items.stars(a["min_q"]), unit=usd(a["unit_cents"], lang)), _cb("af", a["id"], uid=uid))])
    if not ads:
        lines.append(t(lang, "bz.ab.none"))
    lines.append(t(lang, "bz.ab.mine", n=num(sum(mine.values()), lang)))
    nav = []
    if page > 1:
        nav.append((t(lang, "iv.b.prev"), _cb("ab", page - 1, uid=uid)))
    if page < pages:
        nav.append((t(lang, "iv.b.next"), _cb("ab", page + 1, uid=uid)))
    if nav:
        rows.append(nav)
    rows.append([(t(lang, "bz.b.cotton"), f"jb:o:cotton:{uid}"), (t(lang, "bz.b.hub"), _cb("h", uid=uid))])
    return R(c, "\n".join(lines), rows)


def ad_screen(c, adid: int, toast: str | None = None):
    lang, db, uid = c.lang, c.db, c.uid
    a = market.get(db, adid)
    if a is None or a["state"] != "open" or a["expires_at"] <= core.now():
        raise GameError("ad_gone")
    b = biz.get(db, a["branch_id"])
    row = dict(a)
    row.update({"brand": b["brand"], "seq": b["seq"]})
    lines = [t(lang, "bz.af.head"), _ad_line(db, lang, row), t(lang, "bz.af.owner", owner=who(db, b["owner_id"]))]
    mine = textile.fabric_by_quality(db, uid)
    rows = []
    elig = {q: n for q, n in mine.items() if q >= a["min_q"] and n > 0}
    if not elig:
        lines.append(t(lang, "bz.af.nofabric", q=items.stars(a["min_q"])))
    for q in sorted(elig):
        n = min(elig[q], a["qty_left"])
        lines.append(t(lang, "bz.af.have", stars=items.stars(q), n=num(elig[q], lang), pay=usd(a["unit_cents"] * n, lang)))
        row = [(t(lang, "bz.af.sell", stars=items.stars(q), n=num(k, lang)), _cb("afn", adid, q, k, uid=uid)) for k in (1, 10) if k < n]
        row.append((t(lang, "bz.af.sellall", stars=items.stars(q), n=num(n, lang)), _cb("afn", adid, q, 0, uid=uid)))
        rows.append(row)
    rows.append([(t(lang, "bz.b.ads"), _cb("ab", 1, uid=uid)), (t(lang, "bz.b.cotton"), f"jb:o:cotton:{uid}")])
    return R(c, "\n".join(lines), rows, toast=toast)


def ad_fill(c, adid: int, q: int, n: int):
    lang, db, uid = c.lang, c.db, c.uid
    a = market.get(db, adid)
    if a is None:
        raise GameError("ad_gone")
    have = textile.fabric_by_quality(db, uid).get(q, 0)
    n = min(have, a["qty_left"]) if n == 0 else n
    if n <= 0:
        raise GameError("inv_few")
    out = market.fill(db, uid, adid, q, n, core.now())
    text = t(lang, "bz.af.done", n=num(out["qty"], lang), cents=usd(out["cents"], lang), balance=usd(out["balance"], lang))
    rows = [[(t(lang, "bz.b.ads"), _cb("ab", 1, uid=uid))]]
    if out["left"] > 0:
        rows.insert(0, [(t(lang, "bz.af.again"), _cb("af", adid, uid=uid))])
    return R(c, text, rows, toast=plain(t(lang, "bz.af.toast", cents=usd(out["cents"], lang))))


def _ads_branch(db, uid: int, bid: int):
    """The branch when `uid` may advertise for it AND it is a workshop (a shop has nothing to buy fabric for): the same rule market.create enforces, said before the first step of the wizard."""
    b = biz.need(db, uid, bid, "ads")
    if not estate.mode_allows(biz.property_of(db, b)["mode"], "production"):
        raise GameError("ad_mode")
    return b


def myads_screen(c, bid: int, toast: str | None = None):
    lang, db, uid = c.lang, c.db, c.uid
    b = _ads_branch(db, uid, bid)
    ads = market.of_branch(db, bid, open_only=True)
    lines = [t(lang, "bz.am.head", branch=branch_text(b), reserved=usd(sum(a["reserved"] for a in ads), lang), n=num(len(ads), lang), cap=num(C.AD_MAX_OPEN, lang), free=num(stock.free_warehouse(db, b), lang))]
    rows = []
    for a in ads:
        row = dict(a)
        row.update({"brand": b["brand"], "seq": b["seq"]})
        lines.append(_ad_line(db, lang, row))
        rows.append([(t(lang, "bz.am.cancel", q=items.stars(a["min_q"]), left=num(a["qty_left"], lang)), _cb("ax", bid, a["id"], uid=uid))])
    if not ads:
        lines.append(t(lang, "bz.am.none"))
    rows.append([(t(lang, "bz.am.new"), _cb("ac1", bid, uid=uid))])
    rows.append([(t(lang, "bz.b.branch", branch=plain(branch_text(b))), _cb("bo", bid, uid=uid))])
    return R(c, "\n".join(lines), rows, toast=toast)


def ad_new_q(c, bid: int):
    lang, db, uid = c.lang, c.db, c.uid
    _ads_branch(db, uid, bid)
    rows = [[(items.stars(q), _cb("ac2", bid, q, uid=uid)) for q in (1, 2, 3)], [(items.stars(q), _cb("ac2", bid, q, uid=uid)) for q in (4, 5)], [(t(lang, "bz.b.back"), _cb("am", bid, uid=uid))]]
    return R(c, t(lang, "bz.ac1.head"), rows)


def ad_new_price(c, bid: int, q: int):
    lang, db, uid = c.lang, c.db, c.uid
    _ads_branch(db, uid, bid)
    if q not in C.FABRIC_GAME_CENTS:
        raise GameError("ad_terms")
    base = C.FABRIC_GAME_CENTS[q]
    prices = []
    for bps in (10000, 11000, 12500, 15000, 20000):
        p = min(C.AD_MAX_UNIT_CENTS, base * bps // 10000)
        if p not in prices:
            prices.append(p)
    text = t(lang, "bz.ac2.head", stars=items.stars(q), game=usd(base, lang))
    rows = [[(usd(p, lang), _cb("ac3", bid, q, p, uid=uid)) for p in prices[:3]], [(usd(p, lang), _cb("ac3", bid, q, p, uid=uid)) for p in prices[3:]], [(t(lang, "bz.b.back"), _cb("ac1", bid, uid=uid))]]
    return R(c, text, rows)


def ad_new_qty(c, bid: int, q: int, unit: int):
    lang, db, uid = c.lang, c.db, c.uid
    b = _ads_branch(db, uid, bid)
    free = stock.free_warehouse(db, b)
    qtys = [n for n in (10, 50, 100, 250, C.AD_MAX_QTY) if n <= C.AD_MAX_QTY]
    text = t(lang, "bz.ac3.head", stars=items.stars(q), unit=usd(unit, lang), free=num(free, lang), spend=usd(biz.spendable(db, b, core.now()), lang))
    rows = [[(t(lang, "bz.ac3.btn", n=num(n, lang), cost=usd(n * unit, lang)), _cb("ac4", bid, q, unit, n, uid=uid)) for n in qtys[:2]],
            [(t(lang, "bz.ac3.btn", n=num(n, lang), cost=usd(n * unit, lang)), _cb("ac4", bid, q, unit, n, uid=uid)) for n in qtys[2:4]],
            [(t(lang, "bz.ac3.btn", n=num(n, lang), cost=usd(n * unit, lang)), _cb("ac4", bid, q, unit, n, uid=uid)) for n in qtys[4:]],
            [(t(lang, "bz.b.back"), _cb("ac2", bid, q, uid=uid))]]
    return R(c, text, rows)


def ad_new_ttl(c, bid: int, q: int, unit: int, qty: int):
    lang, db, uid = c.lang, c.db, c.uid
    _ads_branch(db, uid, bid)
    text = t(lang, "bz.ac4.head", stars=items.stars(q), unit=usd(unit, lang), n=num(qty, lang), total=usd(unit * qty, lang))
    ver = _ads_made(db, bid)                                                                  # the button promises THIS number of ads: after one is made, an old copy of it is stale
    rows = [[(fmt.dur(ttl, lang), _cb("acD", bid, q, unit, qty, ttl, ver, uid=uid)) for ttl in C.AD_TTL_CHOICES[:2]], [(fmt.dur(ttl, lang), _cb("acD", bid, q, unit, qty, ttl, ver, uid=uid)) for ttl in C.AD_TTL_CHOICES[2:]],
            [(t(lang, "bz.b.back"), _cb("ac3", bid, q, unit, uid=uid))]]
    return R(c, text, rows)


def _ads_made(db, bid: int) -> int:
    """How many ads the branch has ever published (open or not): the wizard's last button carries it, so a double tap or an old copy of the button can never publish a second ad."""
    return db.q1("SELECT COUNT(*) c FROM material_ads WHERE branch_id=?", (bid,))["c"]


def ad_create(c, bid: int, q: int, unit: int, qty: int, ttl: int, ver: int):
    lang, db, uid = c.lang, c.db, c.uid
    if ver != _ads_made(db, bid):
        raise GameError("biz_stale")
    market.create(db, uid, bid, q, unit, qty, ttl, core.now())
    return myads_screen(c, bid, toast=plain(t(lang, "bz.am.created", total=usd(unit * qty, lang))))


def ad_cancel(c, bid: int, adid: int):
    lang, db, uid = c.lang, c.db, c.uid
    biz.need(db, uid, bid, "ads")
    a = market.get(db, adid)
    if a is None or a["branch_id"] != bid:
        raise GameError("ad_gone")
    back = market.cancel(db, uid, adid, core.now())
    return myads_screen(c, bid, toast=plain(t(lang, "bz.am.cancelled", cents=usd(back, lang))))


# =====================================================================================================================
#                                                       the shop floor (a customer)
# =====================================================================================================================
async def shops_screen(c):
    """The open shops: those within reach first (a purchase needs the player at the door), then the nearest others with a walk button."""
    lang, db, uid = c.lang, c.db, c.uid
    city, st = await where(c)
    cands = []
    for b in biz.in_world(db, city.id, open_only=True):
        prop = biz.property_of(db, b)
        if not estate.mode_allows(prop["mode"], "retail"):
            continue
        cen = city._pidx.get(prop["parcel_id"], {}).get("c")
        from . import worldgeom as G
        cands.append((0 if estate.near(city, st, prop) else 1, G.dist(tuple(cen), st.pos) if cen else 1e9, b["id"], b))
    cands.sort(key=lambda r: (r[0], r[1], r[2]))
    lines = [t(lang, "bz.sh.head", city=city_name(db, lang, city.id), n=num(len(cands), lang))]
    rows = []
    for here, _d, _i, b in cands[:8]:
        r = rep.get(db, b["id"])
        lines.append(t(lang, "bz.sh.line", mark="📍" if here == 0 else "▫️", branch=branch_text(b), title=t(lang, f"bz.rep.{rep.title_id(r['score'])}"), score=num(r["score"], lang)))
        rows.append([(t(lang, "bz.sh.btn", mark="📍" if here == 0 else "▫️", branch=plain(branch_text(b))), _cb("sb", b["id"], uid=uid))])
    if not cands:
        lines.append(t(lang, "bz.sh.none"))
    else:
        lines.append(t(lang, "bz.sh.hint"))
    rows.append([(t(lang, "bz.b.hub"), _cb("h", uid=uid))])
    return R(c, "\n".join(lines), rows)


async def shop_front(c, bid: int, toast: str | None = None):
    lang, db, uid = c.lang, c.db, c.uid
    city, st = await where(c)
    b = biz.get(db, bid)
    if b is None or b["state"] != "open" or b["world_id"] != city.id:
        raise GameError("branch_gone")
    prop = biz.property_of(db, b)
    if not estate.mode_allows(prop["mode"], "retail"):
        raise GameError("store_closed")
    here = estate.near(city, st, prop)
    own = store.is_staff(db, b, uid)
    r = rep.get(db, bid)
    lines = [t(lang, "bz.sf.head", branch=branch_text(b), owner=who(db, b["owner_id"]), title=t(lang, f"bz.rep.{rep.title_id(r['score'])}"), score=num(r["score"], lang))]
    rows = []
    shelf = store.shelf(db, bid)
    if not shelf:
        lines.append(t(lang, "bz.sf.empty"))
    for x in shelf[:8]:
        d = x["data"]
        lines.append(t(lang, "bz.sf.line", name=vname(lang, x["item_id"], d), price=usd(x["price"], lang), n=num(x["qty"], lang)))
        if here and not own:
            rows.append([(t(lang, "bz.sf.btn", name=plain(vname(lang, x["item_id"], d)), price=usd(x["price"], lang)), _cb("sq", bid, vtok(x["item_id"], d), uid=uid))])
    if own:
        lines.append(t(lang, "bz.sf.own"))
    elif not here:
        lines.append(t(lang, "bz.sf.far"))
        rows.append([(t(lang, "bz.b.walkshop"), _cb("pg", prop["id"], uid=uid))])
    else:
        lines.append(t(lang, "bz.sf.hint", cash=usd(cash.balance(db, uid), lang)))
    rows.append([(t(lang, "bz.b.refresh"), _cb("sb", bid, uid=uid)), (t(lang, "bz.b.shops"), _cb("sh", uid=uid))])
    return R(c, "\n".join(lines), rows, photo=_front(db, b, prop), toast=toast)


def _shelf_variant(db, bid: int, tok: str):
    for x in store.shelf(db, bid):
        if vtok(x["item_id"], x["data"]) == tok:
            return x
    return None


async def shop_item(c, bid: int, tok: str):
    lang, db, uid = c.lang, c.db, c.uid
    city, st = await where(c)
    b = biz.get(db, bid)
    store.check_customer(db, city, st, uid, b)
    x = _shelf_variant(db, bid, tok)
    if x is None:
        raise GameError("store_none")
    lines = [t(lang, "bz.si.head", name=vname(lang, x["item_id"], x["data"]), price=usd(x["price"], lang), n=num(x["qty"], lang), cash=usd(cash.balance(db, uid), lang))]
    br = items.parse(x["data"]).get("b")
    if br:
        lines.append(t(lang, "bz.si.brand", brand=fmt.ltr(fmt.esc(brands.name_of(br)))))
    lines += tailor.history_lines(db, lang, x["data"])
    ns = [n for n in (1, 2, 5) if n <= x["qty"]]
    rows = [[(t(lang, "bz.si.buy", n=num(n, lang), total=usd(n * x["price"], lang)), _cb("sQ", bid, tok, n, uid=uid)) for n in ns], [(t(lang, "bz.b.back"), _cb("sb", bid, uid=uid))]]
    return R(c, "\n".join(lines), rows)


async def shop_quote(c, bid: int, tok: str, n: int):
    lang, db, uid = c.lang, c.db, c.uid
    city, st = await where(c)
    x = _shelf_variant(db, bid, tok)
    if x is None:
        raise GameError("store_none")
    q = store.quote(db, city, st, uid, bid, x["item_id"], x["data"], n, core.now())
    text = t(lang, "bz.sq.head", name=vname(lang, x["item_id"], x["data"]), n=num(n, lang), total=usd(q["total_cents"], lang), cash=usd(cash.balance(db, uid), lang), ttl=fmt.dur(C.QUOTE_TTL, lang))
    return R(c, text, [[(t(lang, "bz.sq.pay", total=usd(q["total_cents"], lang)), _cb("sC", q["id"], uid=uid))], [(t(lang, "bz.b.cancel"), _cb("sK", q["id"], uid=uid))]])


async def shop_confirm(c, oid: int):
    lang, db, uid = c.lang, c.db, c.uid
    city, st = await where(c)
    out = store.confirm(db, city, st, uid, oid, core.now())
    o = out["order"]
    if out["state"] == "pending":
        return R(c, t(lang, "bz.sq.pending", total=usd(o["total_cents"], lang)), [[(t(lang, "bz.b.refresh"), _cb("sC", oid, uid=uid)), (t(lang, "bz.b.cancel"), _cb("sK", oid, uid=uid))]], toast=plain(t(lang, "bz.sq.pending.toast")))
    text = t(lang, "bz.sq.done", name=vname(lang, o["item_id"], o["data"]), n=num(o["qty"], lang), total=usd(o["total_cents"], lang), balance=usd(out["balance"], lang))
    return R(c, text, [[(t(lang, "bz.sq.again"), _cb("sb", o["branch_id"], uid=uid))], [(t(lang, "iv.b.back"), f"iv:m:{uid}")]], toast=plain(t(lang, "bz.sq.toast")))


def shop_cancel(c, oid: int):
    lang, db, uid = c.lang, c.db, c.uid
    o = store.get(db, oid)
    store.cancel(db, uid, oid)
    return R(c, t(lang, "bz.sq.cancelled"), [[(t(lang, "bz.sq.again"), _cb("sb", o["branch_id"], uid=uid))] if o else [], [(t(lang, "bz.b.shops"), _cb("sh", uid=uid))]])


# --- approvals -------------------------------------------------------------------------------------------------------
def approvals_screen(c, bid: int, toast: str | None = None):
    lang, db, uid = c.lang, c.db, c.uid
    b = biz.need(db, uid, bid, "approve")
    pend = store.pending(db, bid, core.now())
    lines = [t(lang, "bz.ap.head", branch=branch_text(b), n=num(len(pend), lang))]
    rows = []
    for o in pend[:6]:
        lines.append(t(lang, "bz.ap.line", id=num(o["id"], lang), buyer=who(db, o["buyer_id"]), name=vname(lang, o["item_id"], o["data"]), n=num(o["qty"], lang), total=usd(o["total_cents"], lang),
                       left=fmt.dur(max(0, o["expires_at"] - core.now()), lang)))
        rows.append([(t(lang, "bz.ap.yes", id=num(o["id"], lang), total=usd(o["total_cents"], lang)), _cb("aa", o["id"], uid=uid)), (t(lang, "bz.ap.no", id=num(o["id"], lang)), _cb("an", o["id"], uid=uid))])
    if not pend:
        lines.append(t(lang, "bz.ap.none"))
    rows.append([(t(lang, "bz.b.branch", branch=plain(branch_text(b))), _cb("bo", bid, uid=uid))])
    return R(c, "\n".join(lines), rows, toast=toast)


def approve_do(c, oid: int, yes: bool):
    lang, db, uid = c.lang, c.db, c.uid
    o = store.get(db, oid)
    if o is None:
        raise GameError("store_gone")
    bid = o["branch_id"]
    if yes:
        store.approve(db, uid, oid, core.now())
        return approvals_screen(c, bid, toast=plain(t(lang, "bz.ap.approved")))
    store.decline(db, uid, oid, core.now())
    return approvals_screen(c, bid, toast=plain(t(lang, "bz.ap.declined")))
