"""The business world as the player sees it (v0.211), part 1: the hub, properties, brands, branches, the till and upgrades. Part 2 (shopui.py) has the stock, production, ads, staff, customers,
reports, reputation, style and the cotton job. Every screen is TEXT with inline buttons; the few pictures are pixel art (pixelart.py): a for-sale lot, a storefront. The rules are the logic modules'
(estate, brands, biz, upgrades ...); this file only asks them and shows the answer.

    کسب‌وکار ─┬─ املاک اطراف ── a lot ── 💵 buy (confirm) / 🚶 walk there
              ├─ املاک من ── a property ── fit / convert (confirm), open a branch of my brand
              ├─ برندها ── register a brand (confirm)
              ├─ شعبه‌های من ── a branch ── till, stock, production, ads, staff, upgrades, report, close
              └─ … shops here, the ad board, cotton and fabric, my contracts, reports, reputation, style (shopui)

Buttons are `bz:<action>[:<arguments>]:<player id>` (at most 64 bytes). `bz` is a BOUND prefix: handlers.on_callback refuses anybody else's press before anything runs. Every action re-reads the
saved state, so a repeated, outdated or hand-made button changes nothing twice: a quote, a price, a stack or a property that is gone answers with a short alert, never an error page. Whatever needs
the player to BE somewhere (buying a lot, a workshop order, the cotton job, a purchase) reads the saved position on the server; the buttons only name the thing.
"""
from __future__ import annotations

import logging
import re

from . import biz, brands, cash, character, config as C, core, estate, fmt, intercity, items, mapui, pixelart, presence, skills, rep, stock, tailor, textile, upgrades, views, work, workui, world, worldgeom as G, worldplaces
from .core import GameError
from .texts import t
from .views import Reply, kb

log = logging.getLogger("hypemarket")

PREFIX = "bz"
_INT = re.compile(r"^\d{1,12}$")
_WORD = re.compile(r"^[a-z][a-z0-9_]{0,23}$")
_TOK = re.compile(r"^[0-9a-f]{8}$")
PAGE = 6


def _cb(*parts, uid: int) -> str:
    s = ":".join((PREFIX, *(str(p) for p in parts), str(uid)))
    if len(s.encode()) > 64:
        raise ValueError(f"a button is too long: {s}")
    return s


def num(n, lang: str) -> str:
    return fmt.num(n, lang)


def usd(cents, lang: str) -> str:
    return fmt.usd(cents, lang)


def R(c, text: str, rows: list, *, photo: bytes | None = None, toast: str | None = None, alert: bool = False) -> Reply:
    """A business screen: replaces the message the button was on (a picture and a text swap places cleanly)."""
    return Reply(text=text, kb=kb(rows), photo=photo, edit=c.cb is not None, toast=toast, alert=alert, fresh=True)


def who(db, uid: int | None) -> str:
    if uid is None:
        return "—"
    ch = character.get(db, uid)
    if ch is not None and ch["first_name"]:
        return fmt.esc(f"{ch['first_name']} {ch['last_name']}".strip())
    u = core.get_user(db, uid)
    return fmt.esc(core.display_name(u, False)) if u is not None else str(uid)


def city_name(db, lang: str, world_id: int | None) -> str:
    return workui._city_name(db, lang, world_id) if world_id is not None else "—"


def brand_text(key: str) -> str:
    return fmt.ltr(fmt.esc(brands.name_of(key)))


def branch_text(b) -> str:
    return fmt.ltr(fmt.esc(f"{brands.name_of(b['brand'])} #{b['seq']}"))


def mode_text(lang: str, mode) -> str:
    return t(lang, f"bz.mode.{mode}") if mode else t(lang, "bz.mode.none")


def tier_text(lang: str, tier: str) -> str:
    return t(lang, f"bz.tier.{tier}")


def plain(text: str) -> str:
    return mapui.plain(text)


def back_hub(lang: str, uid: int) -> list:
    return [(t(lang, "bz.b.hub"), _cb("h", uid=uid))]


# =====================================================================================================================
#                                                       where the player is
# =====================================================================================================================
class NoWhere(Exception):
    """The screen needs the player's position and cannot have it (a journey, an unfinished character, a damaged city): `reply` answers instead."""
    def __init__(self, reply: Reply):
        super().__init__("nowhere")
        self.reply = reply


async def city_of(c):
    """The city of the player's CHARACTER (a group chat may create it, like «مپ»; a private chat uses the saved one), or None when there is none yet. NoWhere for a character in set-up or on a journey."""
    ch = character.ensure_profile(c.db, c.uid)
    if ch is None:
        raise NoWhere(Reply(toast=plain(t(c.lang, "bz.e.account")), alert=True))
    if not character.is_done(ch):
        raise NoWhere(workui.NeedCharacter(ch).reply(c))
    trip = await intercity.aguard(c.db, c.uid)
    if trip is not None:
        raise NoWhere(workui.InTransit(trip).reply(c))
    try:
        if c.is_group:
            w, _created = await world.aworld_for(c.db, c.uid, c.chat.id)
        else:
            w = character.current_world(c.db, c.uid)
        if w is None:
            return None
        return await worldplaces.aload_city(c.db, w)
    except world.WorldError as e:
        log.error("business: the city of user %s is unavailable: %s", c.uid, e.code)
        raise NoWhere(Reply(text=t(c.lang, "map.failed" if e.code == "generation_failed" else "map.corrupt")))


async def where(c):
    """(city, standing) for a screen that needs the player's position (the city's property rows are made first). NoWhere when there is no city."""
    city = await city_of(c)
    if city is None:
        raise NoWhere(Reply(toast=plain(t(c.lang, "bz.e.nocity")), alert=True))
    estate.ensure(c.db, city, core.now())
    return city, presence.stand(c.db, city, c.uid, core.now())


def _ints(*vals):
    return all(v is not None for v in vals)


# =====================================================================================================================
#                                                       the hub
# =====================================================================================================================
def hub(c, toast: str | None = None) -> Reply:
    from . import staff, store
    lang, db, uid = c.lang, c.db, c.uid
    props = estate.of_owner(db, uid)
    mine = brands.of_player(db, uid)
    branches = biz.of_owner(db, uid, open_only=True)
    cap = brands.capital(db)
    offers = [x for x in staff.of_employee(db, uid, states=("offered",)) if x["expires_at"] is None or x["expires_at"] > core.now()]
    waiting = 0
    for b in biz.of_owner(db, uid, open_only=True):
        waiting += len(store.pending(db, b["id"]))
    lines = [t(lang, "bz.hub", name=who(db, uid), cash=usd(cash.balance(db, uid), lang), props=num(len(props), lang), brands=("، ".join(brand_text(b["brand"]) for b in mine) if lang == "fa" else ", ".join(brand_text(b["brand"]) for b in mine)) or "—",
             branches=num(len(branches), lang), capital=city_name(db, lang, cap) if cap else t(lang, "bz.capital.none"))]
    if offers:
        lines.append(t(lang, "bz.hub.offers", n=num(len(offers), lang)))
    if waiting:
        lines.append(t(lang, "bz.hub.waiting", n=num(waiting, lang)))
    rows = [[(t(lang, "bz.b.near"), _cb("n", 1, uid=uid)), (t(lang, "bz.b.props"), _cb("p", 1, uid=uid))],
            [(t(lang, "bz.b.brands"), _cb("b", uid=uid)), (t(lang, "bz.b.branches"), _cb("l", uid=uid))],
            [(t(lang, "bz.b.shops"), _cb("sh", uid=uid)), (t(lang, "bz.b.ads"), _cb("ab", 1, uid=uid))],
            [(t(lang, "bz.b.cotton"), f"jb:o:cotton:{uid}"), (t(lang, "bz.b.contracts"), _cb("k", uid=uid))],
            [(t(lang, "bz.b.reports"), _cb("ro", uid=uid)), (t(lang, "bz.b.rep"), _cb("t", uid=uid))],
            [(t(lang, "bz.b.style"), _cb("y", uid=uid)), (t(lang, "menu.back"), f"mn:menu:{uid}")]]
    return R(c, "\n".join(lines), rows, toast=toast)


# =====================================================================================================================
#                                                       properties
# =====================================================================================================================
def _prop_lines(db, lang: str, prop, city=None, st=None) -> list:
    lines = [t(lang, "bz.p.shape", m2=num(prop["area_m2"], lang), tier=tier_text(lang, prop["tier"]), zone=t(lang, f"bz.zone.{prop['zone']}")),
             t(lang, "bz.p.caps", wh=num(prop["warehouse_cap"], lang), disp=num(prop["display_cap"], lang), slots=num(prop["display_cap"] // C.DISPLAY_SLOT_ITEMS, lang), staff=num(prop["staff_slots"], lang),
               equip=num(prop["equip_slots"], lang)),
             t(lang, "bz.p.quality", stars=items.stars(prop["max_quality"])),
             t(lang, "bz.p.modes", modes=("، " if lang == "fa" else ", ").join(mode_text(lang, m) for m in estate.modes(prop)))]
    return lines


def _near_text(c, city, st, prop) -> str:
    return t(c.lang, "bz.p.here") if estate.near(city, st, prop) else t(c.lang, "bz.p.away", d=fmt.dist(_dist(city, st, prop), c.lang))


def _dist(city, st, prop) -> float:
    spot = city.spot(prop["parcel_id"])
    if spot is None:
        return 0.0
    d = city.net.field(st.pos).to(spot.door)
    return float(d) if d != float("inf") else 0.0


def _parcel_label(lang: str, prop) -> str:
    return t(lang, "mp.parcel", id=f"<code>{_display_id(prop['parcel_id'])}</code>")


def _display_id(ref: str) -> str:
    from . import worldinspect
    return worldinspect.display_id(ref)


async def near_list(c, page: int) -> Reply:
    """The lots for sale closest to the player (straight-line first, then by the road): those within reach are marked and can be bought on the spot."""
    lang, db, uid = c.lang, c.db, c.uid
    city, st = await where(c)
    sale = []
    for p in estate.for_sale(db, city.id):
        spot = city.spot(p["parcel_id"])
        if spot is None:
            continue
        cen = city._pidx[p["parcel_id"]].get("c")
        sale.append((G.dist(tuple(cen), st.pos) if cen else 1e9, p["id"], p))
    sale.sort(key=lambda r: (r[0], r[1]))
    pages = max(1, -(-min(len(sale), PAGE * 4) // PAGE))
    page = max(1, min(page, pages))
    shown = sale[(page - 1) * PAGE:page * PAGE]
    lines = [t(lang, "bz.n.head", city=city_name(db, lang, city.id), n=num(len(sale), lang))]
    rows = []
    for _d, _i, p in shown:
        here = estate.near(city, st, p)
        lines.append(t(lang, "bz.n.line", mark="📍" if here else "▫️", parcel=_parcel_label(lang, p), tier=tier_text(lang, p["tier"]), price=usd(p["price"], lang), zone=t(lang, f"bz.zone.{p['zone']}")))
        rows.append([(t(lang, "bz.n.btn", mark="📍" if here else "▫️", parcel=_display_id(p["parcel_id"]), price=usd(p["price"], lang)), _cb("pi", p["id"], uid=uid))])
    if not sale:
        lines.append(t(lang, "bz.n.none"))
    else:
        lines.append(t(lang, "bz.n.hint"))
    nav = []
    if page > 1:
        nav.append((t(lang, "iv.b.prev"), _cb("n", page - 1, uid=uid)))
    if page < pages:
        nav.append((t(lang, "iv.b.next"), _cb("n", page + 1, uid=uid)))
    if nav:
        rows.append(nav)
    rows.append(back_hub(lang, uid))
    return R(c, "\n".join(lines), rows)


async def prop_inspect(c, pid: int) -> Reply:
    """One lot: its picture, attributes and price; 💵 buy when it is for sale and the player is next to it, 🚶 walk there otherwise, 🛠 manage when it is theirs."""
    lang, db, uid = c.lang, c.db, c.uid
    city, st = await where(c)
    prop = estate.get(db, pid)
    if prop is None or prop["world_id"] != city.id:
        raise GameError("prop_gone")
    br = biz.of_property(db, pid)
    head = t(lang, "bz.p.head", parcel=_parcel_label(lang, prop), city=city_name(db, lang, city.id))
    lines = [head] + _prop_lines(db, lang, prop)
    rows = []
    photo = None
    tier_i = upgrades.tier_index(prop["tier"])
    if prop["owner_id"] is None:
        lines.append(t(lang, "bz.p.price", price=usd(prop["price"], lang), cash=usd(cash.balance(db, uid), lang)))
        lines.append(_near_text(c, city, st, prop))
        photo = pixelart.for_sale(tier_i, prop["zone"])
        if estate.near(city, st, prop):
            rows.append([(t(lang, "bz.b.buy", price=usd(prop["price"], lang)), _cb("pb", pid, uid=uid))])
        else:
            rows.append([(t(lang, "bz.b.walk"), _cb("pg", pid, uid=uid))])
    else:
        lines.append(t(lang, "bz.p.owner", owner=who(db, prop["owner_id"]), mode=mode_text(lang, prop["mode"])))
        if br is not None:
            lines.append(t(lang, "bz.p.branch", branch=branch_text(br)))
            photo = _front(db, br, prop)
            rows.append([(t(lang, "bz.b.shopfront"), _cb("sb", br["id"], uid=uid))]) if estate.mode_allows(prop["mode"], "retail") else None
        else:
            photo = pixelart.for_sale(tier_i, prop["zone"]) if prop["mode"] is None else None
        if prop["owner_id"] == uid:
            rows.append([(t(lang, "bz.b.manage"), _cb("po", pid, uid=uid))])
        elif not estate.near(city, st, prop):
            rows.append([(t(lang, "bz.b.walk"), _cb("pg", pid, uid=uid))])
    rows = [r for r in rows if r]
    rows.append([(t(lang, "bz.b.nearlist"), _cb("n", 1, uid=uid)), back_hub(lang, uid)[0]])
    return R(c, "\n".join(lines), rows, photo=photo)


def _front(db, br, prop) -> bytes:
    """The storefront picture of an open branch, from what is really installed and stocked."""
    fx = upgrades.installed(db, br["id"])
    return pixelart.storefront(brands.name_of(br["brand"]), br["seq"], upgrades.sign_style(db, br["id"]), prop["mode"] or "retail", open_now=br["state"] == "open",
                               shown=len(stock.contents(db, br["id"], stock.DISP)), machines=fx.get("machines", 0), cameras=fx.get("cameras", 0) > 0)


async def prop_walk(c, pid: int) -> Reply:
    """🚶 the ordinary walk to the lot (the same server-side trip the map starts)."""
    lang, db, uid = c.lang, c.db, c.uid
    city, st = await where(c)
    prop = estate.get(db, pid)
    if prop is None or prop["world_id"] != city.id:
        raise GameError("prop_gone")
    out = presence.start_trip(db, city, uid, prop["parcel_id"], core.now())
    if out.status == "busy" and not out.same:
        raise GameError("job_walking")
    if out.status == "unreachable":
        raise GameError("job_unreachable")
    r = await prop_inspect(c, pid)
    r.toast = plain(t(lang, "bz.walk.started"))
    return r


async def prop_buy_ask(c, pid: int) -> Reply:
    lang, db, uid = c.lang, c.db, c.uid
    city, st = await where(c)
    prop = estate.get(db, pid)
    if prop is None or prop["world_id"] != city.id:
        raise GameError("prop_gone")
    if prop["owner_id"] is not None:
        raise GameError("prop_taken")
    if not estate.near(city, st, prop):
        raise GameError("prop_far")
    text = t(lang, "bz.buy.ask", parcel=_parcel_label(lang, prop), tier=tier_text(lang, prop["tier"]), m2=num(prop["area_m2"], lang), price=usd(prop["price"], lang), cash=usd(cash.balance(db, uid), lang),
             fit=usd(estate.fit_cost(prop), lang))
    return R(c, text, [[(t(lang, "bz.b.confirm_buy", price=usd(prop["price"], lang)), _cb("pd", pid, uid=uid))], [(t(lang, "bz.b.cancel"), _cb("pi", pid, uid=uid))]])


async def prop_buy(c, pid: int) -> Reply:
    lang, db, uid = c.lang, c.db, c.uid
    city, st = await where(c)
    prop = estate.buy(db, city, st, uid, pid, core.now())
    text = t(lang, "bz.buy.done", parcel=_parcel_label(lang, prop), price=usd(prop["price"], lang), cash=usd(cash.balance(db, uid), lang))
    return R(c, text, [[(t(lang, "bz.b.manage"), _cb("po", pid, uid=uid))], back_hub(lang, uid)], toast=plain(t(lang, "bz.buy.toast")))


def _my_prop(db, uid: int, pid: int):
    prop = estate.get(db, pid)
    if prop is None:
        raise GameError("prop_gone")
    if prop["owner_id"] != uid:
        raise GameError("prop_notyours")
    return prop


def props_list(c, page: int) -> Reply:
    lang, db, uid = c.lang, c.db, c.uid
    mine = estate.of_owner(db, uid)
    pages = max(1, -(-len(mine) // PAGE))
    page = max(1, min(page, pages))
    shown = mine[(page - 1) * PAGE:page * PAGE]
    lines = [t(lang, "bz.pl.head", n=num(len(mine), lang))]
    rows = []
    for p in shown:
        br = biz.of_property(db, p["id"])
        lines.append(t(lang, "bz.pl.line", parcel=_parcel_label(lang, p), city=city_name(db, lang, p["world_id"]), tier=tier_text(lang, p["tier"]), mode=mode_text(lang, p["mode"]),
                       branch=branch_text(br) if br else t(lang, "bz.pl.nobranch")))
        rows.append([(t(lang, "bz.pl.btn", parcel=_display_id(p["parcel_id"]), mode=mode_text(lang, p["mode"])), _cb("po", p["id"], uid=uid))])
    if not mine:
        lines.append(t(lang, "bz.pl.none"))
    nav = []
    if page > 1:
        nav.append((t(lang, "iv.b.prev"), _cb("p", page - 1, uid=uid)))
    if page < pages:
        nav.append((t(lang, "iv.b.next"), _cb("p", page + 1, uid=uid)))
    if nav:
        rows.append(nav)
    rows.append(back_hub(lang, uid))
    return R(c, "\n".join(lines), rows)


def prop_manage(c, pid: int, toast: str | None = None) -> Reply:
    """My property: its attributes, its mode (fit an empty shell / convert, both with a confirmation that names the price) and a branch of my brand."""
    lang, db, uid = c.lang, c.db, c.uid
    prop = _my_prop(db, uid, pid)
    br = biz.of_property(db, pid)
    lines = [t(lang, "bz.p.head", parcel=_parcel_label(lang, prop), city=city_name(db, lang, prop["world_id"]))] + _prop_lines(db, lang, prop)
    lines.append(t(lang, "bz.p.bought", price=usd(prop["bought_price"], lang), date=fmt.date(prop["bought_at"], lang)))
    rows = []
    photo = None
    if prop["mode"] is None:
        lines.append(t(lang, "bz.p.shell", fit=usd(estate.fit_cost(prop), lang)))
        for m in estate.allowed_choices(prop):
            rows.append([(t(lang, "bz.b.fit", mode=mode_text(lang, m), cost=usd(estate.fit_cost(prop), lang)), _cb("pm", pid, m, uid=uid))])
        photo = pixelart.for_sale(upgrades.tier_index(prop["tier"]), prop["zone"])
    else:
        lines.append(t(lang, "bz.p.fitted", mode=mode_text(lang, prop["mode"])))
        if br is None:
            lines.append(t(lang, "bz.p.nobranch"))
            rows.append([(t(lang, "bz.b.open"), _cb("ob", pid, uid=uid))])
            photo = pixelart.for_sale(upgrades.tier_index(prop["tier"]), prop["zone"])
        else:
            lines.append(t(lang, "bz.p.branch", branch=branch_text(br)))
            rows.append([(t(lang, "bz.b.branch", branch=plain(branch_text(br))), _cb("bo", br["id"], uid=uid))])
            photo = _front(db, br, prop)
        others = [m for m in estate.allowed_choices(prop) if m != prop["mode"]]
        if others:
            why = estate.convert_blockers(db, prop)
            if why:
                lines.append(t(lang, "bz.p.noconvert", why=t(lang, f"bz.block.{why[0]}") if f"bz.block.{why[0]}" in _known(lang) else why[0]))
            else:
                for m in others:
                    rows.append([(t(lang, "bz.b.convert", mode=mode_text(lang, m), cost=usd(estate.convert_cost(prop), lang)), _cb("pm", pid, m, uid=uid))])
    rows.append([(t(lang, "bz.b.props"), _cb("p", 1, uid=uid)), back_hub(lang, uid)[0]])
    return R(c, "\n".join(lines), rows, photo=photo, toast=toast)


def _known(lang: str) -> dict:
    from .texts import STR
    return STR.get(lang, {})


def mode_ask(c, pid: int, mode: str) -> Reply:
    lang, db, uid = c.lang, c.db, c.uid
    prop = _my_prop(db, uid, pid)
    if mode not in C.BUSINESS_MODES or mode not in estate.allowed_choices(prop):
        raise GameError("mode_bad")
    if prop["mode"] == mode:
        raise GameError("mode_same")
    first = prop["mode"] is None
    if not first and estate.convert_blockers(db, prop):
        raise GameError("mode_busy", why=estate.convert_blockers(db, prop)[0])
    cost = estate.fit_cost(prop) if first else estate.convert_cost(prop)
    text = t(lang, "bz.mode.ask.fit" if first else "bz.mode.ask.convert", parcel=_parcel_label(lang, prop), mode=mode_text(lang, mode), old=mode_text(lang, prop["mode"]), cost=usd(cost, lang),
             cash=usd(cash.balance(db, uid), lang), hint=t(lang, f"bz.mode.hint.{mode}"))
    return R(c, text, [[(t(lang, "bz.b.confirm", cost=usd(cost, lang)), _cb("pM", pid, mode, uid=uid))], [(t(lang, "bz.b.cancel"), _cb("po", pid, uid=uid))]])


def mode_do(c, pid: int, mode: str) -> Reply:
    lang, db, uid = c.lang, c.db, c.uid
    prop = estate.set_mode(db, uid, pid, mode, core.now())
    return prop_manage(c, pid, toast=plain(t(lang, "bz.mode.done", mode=mode_text(lang, prop["mode"]))))


# =====================================================================================================================
#                                                       brands
# =====================================================================================================================
def brands_screen(c, toast: str | None = None) -> Reply:
    lang, db, uid = c.lang, c.db, c.uid
    cat = brands.catalog()
    reg = brands.registered(db)
    mine = brands.of_player(db, uid)
    hq = brands.headquarters(db)
    cap = brands.capital(db)
    lines = [t(lang, "bz.br.head", fee=usd(C.BRAND_FEE_CENTS, lang), cap=city_name(db, lang, cap) if cap else t(lang, "bz.capital.none"))]
    rows = []
    full = C.BRAND_MAX_PER_PLAYER and len(mine) >= C.BRAND_MAX_PER_PLAYER
    for key, name in cat.items():
        owner = reg.get(key)
        if owner is None:
            lines.append(t(lang, "bz.br.free", brand=fmt.ltr(fmt.esc(name))))
            if not full:
                rows.append([(t(lang, "bz.br.btn", brand=name, fee=usd(C.BRAND_FEE_CENTS, lang)), _cb("br", _tok(key), uid=uid))])
        else:
            h = hq.get(key)
            lines.append(t(lang, "bz.br.taken" if owner != uid else "bz.br.mine", brand=fmt.ltr(fmt.esc(name)), owner=who(db, owner), hq=city_name(db, lang, h) if h else t(lang, "bz.br.nohq"),
                           n=num(len(brands.stores(db, key)), lang)))
    if full:
        lines.append(t(lang, "bz.br.limit", n=num(C.BRAND_MAX_PER_PLAYER, lang)))
    rows.append(back_hub(lang, uid))
    return R(c, "\n".join(lines), rows, toast=toast)


def _tok(key: str) -> str:
    """A brand key as a short button token (8 hex digits of its crc), resolved against the catalog when the button is pressed."""
    import zlib
    return f"{zlib.crc32(key.encode()):08x}"


def _brand_of(tok: str):
    for key in brands.catalog():
        if _tok(key) == tok:
            return key
    return None


def brand_ask(c, tok: str) -> Reply:
    lang, db, uid = c.lang, c.db, c.uid
    key = _brand_of(tok)
    if key is None:
        raise GameError("brand_unknown")
    if brands.get(db, key) is not None:
        raise GameError("brand_taken")
    text = t(lang, "bz.br.ask", brand=fmt.ltr(fmt.esc(brands.name_of(key))), fee=usd(C.BRAND_FEE_CENTS, lang), cash=usd(cash.balance(db, uid), lang))
    return R(c, text, [[(t(lang, "bz.b.confirm", cost=usd(C.BRAND_FEE_CENTS, lang)), _cb("bR", tok, uid=uid))], [(t(lang, "bz.b.cancel"), _cb("b", uid=uid))]])


def brand_do(c, tok: str) -> Reply:
    lang, db, uid = c.lang, c.db, c.uid
    key = _brand_of(tok)
    if key is None:
        raise GameError("brand_unknown")
    brands.register(db, uid, key, core.now())
    return brands_screen(c, toast=plain(t(lang, "bz.br.done", brand=brands.name_of(key))))


# =====================================================================================================================
#                                                       opening a branch
# =====================================================================================================================
def open_ask(c, pid: int) -> Reply:
    lang, db, uid = c.lang, c.db, c.uid
    prop = _my_prop(db, uid, pid)
    if prop["mode"] is None:
        raise GameError("branch_nomode")
    if biz.of_property(db, pid) is not None:
        raise GameError("branch_exists")
    mine = brands.of_player(db, uid)
    if not mine:
        return R(c, t(lang, "bz.open.nobrand"), [[(t(lang, "bz.b.brands"), _cb("b", uid=uid))], [(t(lang, "bz.b.cancel"), _cb("po", pid, uid=uid))]])
    b = mine[0]
    cap = brands.capital(db)
    hq_note = t(lang, "bz.open.hq") if brands.hq(db, b["brand"]) is None else t(lang, "bz.open.branchof", city=city_name(db, lang, brands.hq(db, b["brand"])))
    text = t(lang, "bz.open.ask", brand=brand_text(b["brand"]), parcel=_parcel_label(lang, prop), mode=mode_text(lang, prop["mode"]), note=hq_note)
    return R(c, text, [[(t(lang, "bz.b.confirm_open"), _cb("oB", pid, _tok(b["brand"]), uid=uid))], [(t(lang, "bz.b.cancel"), _cb("po", pid, uid=uid))]])


def open_do(c, pid: int, tok: str) -> Reply:
    lang, db, uid = c.lang, c.db, c.uid
    key = _brand_of(tok)
    if key is None:
        raise GameError("brand_unknown")
    b = biz.open_branch(db, uid, pid, key, core.now())
    return branch_screen(c, b["id"], toast=plain(t(lang, "bz.open.done", branch=branch_text(b))))


# =====================================================================================================================
#                                                       branches
# =====================================================================================================================
def branches_list(c) -> Reply:
    from . import staff
    lang, db, uid = c.lang, c.db, c.uid
    mine = biz.of_owner(db, uid, open_only=True)
    lines = [t(lang, "bz.bl.head", n=num(len(mine), lang))]
    rows = []
    for b in mine:
        hq = " 🏛" if biz.is_hq(db, b) else ""
        lines.append(t(lang, "bz.bl.line", branch=branch_text(b), city=city_name(db, lang, b["world_id"]), till=usd(b["till"], lang), hq=hq))
        rows.append([(t(lang, "bz.bl.btn", branch=plain(branch_text(b)), hq=hq), _cb("bo", b["id"], uid=uid))])
    # branches where I work as a manager
    for ct in staff.of_employee(db, uid, states=("active",)):
        b = biz.get(db, ct["branch_id"])
        if b is not None and b["state"] == "open" and b["owner_id"] != uid:
            rows.append([(t(lang, "bz.bl.job", branch=plain(branch_text(b)), role=t(lang, f"bz.role.{ct['role']}")), _cb("bo", b["id"], uid=uid))])
    if not mine:
        lines.append(t(lang, "bz.bl.none"))
    rows.append(back_hub(lang, uid))
    return R(c, "\n".join(lines), rows)


def _branch_for(db, uid: int, bid: int, perm: str | None = None):
    """The open branch `bid` for a player who may act on it (owner, or staff with `perm`). GameError: branch_gone, branch_denied."""
    return biz.need(db, uid, bid, perm)


def branch_screen(c, bid: int, toast: str | None = None) -> Reply:
    from . import staff, store
    lang, db, uid = c.lang, c.db, c.uid
    b = _branch_for(db, uid, bid)
    prop = biz.property_of(db, b)
    owner = b["owner_id"] == uid
    retail = estate.mode_allows(prop["mode"], "retail")
    prod = estate.mode_allows(prop["mode"], "production")
    now = core.now()
    lines = [t(lang, "bz.bo.head", branch=branch_text(b), city=city_name(db, lang, b["world_id"]), hq=t(lang, "bz.bo.hq") if biz.is_hq(db, b) else "", mode=mode_text(lang, prop["mode"]))]
    res = biz.reserved(db, b, now)
    lines.append(t(lang, "bz.bo.till", till=usd(b["till"], lang), reserved=usd(res, lang) if res else "—"))
    lines.append(t(lang, "bz.bo.stock", wh=num(stock.total(db, bid, stock.WH), lang), cap=num(upgrades.warehouse_cap(db, b), lang), disp=num(stock.total(db, bid, stock.DISP), lang),
                   slots=num(stock.slots_used(db, bid), lang), slotcap=num(stock.display_slots(db, b), lang)))
    r = rep.get(db, bid)
    lines.append(t(lang, "bz.bo.rep", score=num(r["score"], lang), title=t(lang, f"bz.rep.{rep.title_id(r['score'])}"), sales=num(r["sales"], lang)))
    o = tailor.working(db, bid)
    if o is not None:
        lines.append(t(lang, "bz.bo.order", product=items.display_name(lang, o["product"], None), n=num(o["pieces"], lang),
                       left=fmt.dur(max(0, o["due_at"] - now), lang) if o["due_at"] > now else t(lang, "bz.bo.ready")))
    pend = store.pending(db, bid, now)
    if pend:
        lines.append(t(lang, "bz.bo.pending", n=num(len(pend), lang)))
    cts = staff.of_branch(db, bid, states=("active",))
    lines.append(t(lang, "bz.bo.staff", n=num(len(cts), lang), cap=num(upgrades.staff_slots(db, b), lang)))
    lines.append(t(lang, "bz.bo.sign", style=t(lang, f"bz.sign.{upgrades.sign_style(db, bid)}")))
    rows = []
    first = []
    if owner or biz.can(db, uid, b, "spend"):
        first.append((t(lang, "bz.b.till", till=usd(b["till"], lang)), _cb("bf", bid, uid=uid)))
    if owner or biz.can(db, uid, b, "stock"):
        first.append((t(lang, "bz.b.stock"), _cb("s", bid, uid=uid)))
    if first:
        rows.append(first)
    second = []
    if prod and (owner or biz.can(db, uid, b, "produce")):
        second.append((t(lang, "bz.b.prod"), _cb("pr", bid, uid=uid)))
    if prod and (owner or biz.can(db, uid, b, "ads")):                                          # material ads are a workshop's: a shop has nothing to buy fabric for
        second.append((t(lang, "bz.b.myads"), _cb("am", bid, uid=uid)))
    if second:
        rows.append(second)
    third = []
    if owner:                                                                                    # contracts are the owner's alone
        third.append((t(lang, "bz.b.staff"), _cb("sf", bid, uid=uid)))
    if owner or biz.can(db, uid, b, "upgrade"):
        third.append((t(lang, "bz.b.upg"), _cb("u", bid, uid=uid)))
    if third:
        rows.append(third)
    fourth = [(t(lang, "bz.b.report"), _cb("rp", bid, uid=uid))]
    if retail and (owner or biz.can(db, uid, b, "approve")):
        fourth.append((t(lang, "bz.b.approvals", n=num(len(pend), lang)), _cb("ap", bid, uid=uid)))
    rows.append(fourth)
    if owner:
        rows.append([(t(lang, "bz.b.close"), _cb("cl", bid, uid=uid))])
    rows.append([(t(lang, "bz.b.branches"), _cb("l", uid=uid)), back_hub(lang, uid)[0]])
    return R(c, "\n".join(lines), rows, photo=_front(db, b, prop), toast=toast)


# --- the till ------------------------------------------------------------------------------------------------------
FUND = (100_00, 500_00, 2000_00, 10000_00)


def till_screen(c, bid: int, toast: str | None = None) -> Reply:
    lang, db, uid = c.lang, c.db, c.uid
    b = _branch_for(db, uid, bid, "spend")
    owner = b["owner_id"] == uid
    now = core.now()
    res = biz.reserved(db, b, now)
    lines = [t(lang, "bz.tl.head", branch=branch_text(b)), t(lang, "bz.tl.till", till=usd(b["till"], lang), reserved=usd(res, lang), free=usd(biz.spendable(db, b, now), lang)),
             t(lang, "bz.tl.cash", cash=usd(cash.balance(db, uid), lang))]
    led = biz.ledger(db, bid, 6)
    if led:
        lines.append(t(lang, "bz.tl.ledger"))
        for r in led:
            lines.append(t(lang, "bz.tl.row", date=fmt.date(r["created_at"], lang), kind=t(lang, f"bz.ledger.{r['kind']}") if f"bz.ledger.{r['kind']}" in _known(lang) else fmt.esc(r["kind"]),
                           delta=usd(r["delta"], lang)))
    rows = []
    if owner:
        rows.append([(t(lang, "bz.b.fund", amount=usd(a, lang)), _cb("fm", bid, a, uid=uid)) for a in FUND[:2]])
        rows.append([(t(lang, "bz.b.fund", amount=usd(a, lang)), _cb("fm", bid, a, uid=uid)) for a in FUND[2:]])
        rows.append([(t(lang, "bz.b.wd", amount=usd(a, lang)), _cb("wd", bid, a, uid=uid)) for a in FUND[:2]])
        rows.append([(t(lang, "bz.b.wd", amount=usd(a, lang)), _cb("wd", bid, a, uid=uid)) for a in FUND[2:]])
        rows.append([(t(lang, "bz.b.wdall"), _cb("wd", bid, 0, uid=uid))])
    rows.append([(t(lang, "bz.b.branch", branch=plain(branch_text(b))), _cb("bo", bid, uid=uid))])
    return R(c, "\n".join(lines), rows, toast=toast)


def fund_do(c, bid: int, cents: int) -> Reply:
    lang, db, uid = c.lang, c.db, c.uid
    if cents not in FUND:
        raise GameError("bad_amount")
    biz.fund(db, uid, bid, cents, None, core.now())
    return till_screen(c, bid, toast=plain(t(lang, "bz.tl.funded", amount=usd(cents, lang))))


def withdraw_do(c, bid: int, cents: int) -> Reply:
    lang, db, uid = c.lang, c.db, c.uid
    b = _branch_for(db, uid, bid, "spend")
    if cents == 0:                                                                # «everything that is free»
        cents = biz.spendable(db, b, core.now())
        if cents <= 0:
            raise GameError("till_low")
    elif cents not in FUND:
        raise GameError("bad_amount")
    biz.withdraw(db, uid, bid, cents, None, core.now())
    return till_screen(c, bid, toast=plain(t(lang, "bz.tl.withdrawn", amount=usd(cents, lang))))


# --- upgrades ------------------------------------------------------------------------------------------------------
def _effect_text(lang: str, u, level: int) -> str:
    val = u.levels[level - 1][1]
    if u.effect == "speed":
        return t(lang, "bz.upg.fx.speed", speed=num(val[0] // 100, lang), quality=num(val[1], lang))
    if u.effect == "unlock":
        return t(lang, "bz.upg.fx.unlock")
    if u.effect == "selfcheck":
        return t(lang, "bz.upg.fx.selfcheck", amount=usd(val, lang))
    if u.effect in ("slots_staff", "slots_equip"):
        return t(lang, "bz.upg.fx." + u.effect, n=num(val, lang))
    return t(lang, "bz.upg.fx." + u.effect, pct=num(val // 100, lang))


def upgrades_screen(c, bid: int, toast: str | None = None) -> Reply:
    lang, db, uid = c.lang, c.db, c.uid
    b = _branch_for(db, uid, bid, "upgrade")
    prop = biz.property_of(db, b)
    have = upgrades.installed(db, bid)
    lines = [t(lang, "bz.upg.head", branch=branch_text(b), till=usd(b["till"], lang), slots=t(lang, "bz.upg.slots", used=num(upgrades.equipment_used(db, bid), lang), cap=num(upgrades.equipment_slots(db, b), lang)))]
    rows = []
    for kind, u in upgrades.UPGRADES.items():
        if u.needs and not estate.mode_allows(prop["mode"], u.needs):
            continue
        lvl = have.get(kind, 0)
        nxt = upgrades.next_cost(db, bid, kind)
        lines.append(t(lang, "bz.upg.line", icon=u.icon, name=t(lang, f"bz.upg.{kind}"), lvl=num(lvl, lang), max=num(len(u.levels), lang), now=_effect_text(lang, u, lvl) if lvl else "—"))
        rows.append([(t(lang, "bz.upg.btn", icon=u.icon, name=t(lang, f"bz.upg.{kind}"), lvl=num(lvl, lang), max=num(len(u.levels), lang)), _cb("ui", bid, kind, uid=uid))])
    rows.append([(t(lang, "bz.b.branch", branch=plain(branch_text(b))), _cb("bo", bid, uid=uid))])
    return R(c, "\n".join(lines), rows, toast=toast)


def upgrade_detail(c, bid: int, kind: str) -> Reply:
    lang, db, uid = c.lang, c.db, c.uid
    b = _branch_for(db, uid, bid, "upgrade")
    u = upgrades.UPGRADES.get(kind)
    if u is None:
        raise GameError("upg_unknown")
    lvl = upgrades.level_of(db, bid, kind)
    nxt = upgrades.next_cost(db, bid, kind)
    lines = [t(lang, "bz.upg.d.head", icon=u.icon, name=t(lang, f"bz.upg.{kind}"), lvl=num(lvl, lang), max=num(len(u.levels), lang)), f"<i>{t(lang, f'bz.upg.{kind}.d')}</i>"]
    if lvl:
        lines.append(t(lang, "bz.upg.d.now", fx=_effect_text(lang, u, lvl)))
    rows = []
    if nxt is None:
        lines.append(t(lang, "bz.upg.d.max"))
    else:
        lines.append(t(lang, "bz.upg.d.next", fx=_effect_text(lang, u, lvl + 1), cost=usd(nxt, lang), till=usd(b["till"], lang)))
        why = upgrades.problem(db, b, kind)
        if why is not None:
            lines.append(t(lang, "bz.upg.d.why", why=t(lang, f"e.upg_{why}")))
        else:
            rows.append([(t(lang, "bz.b.install", cost=usd(nxt, lang)), _cb("uI", bid, kind, lvl + 1, uid=uid))])       # the level it promises: a second tap is stale
    rows.append([(t(lang, "bz.b.upg"), _cb("u", bid, uid=uid))])
    return R(c, "\n".join(lines), rows)


def upgrade_do(c, bid: int, kind: str, to: int) -> Reply:
    lang, db, uid = c.lang, c.db, c.uid
    if kind in upgrades.UPGRADES and upgrades.level_of(db, bid, kind) + 1 != to:
        biz.need(db, uid, bid, "upgrade")                                                        # (a stranger still hears "not yours", not "stale")
        raise GameError("biz_stale")
    lvl = upgrades.install(db, uid, bid, kind, core.now())
    r = upgrade_detail(c, bid, kind)
    r.toast = plain(t(lang, "bz.upg.done", name=t(lang, f"bz.upg.{kind}"), lvl=num(lvl, lang)))
    return r


# --- closing -------------------------------------------------------------------------------------------------------
def close_ask(c, bid: int) -> Reply:
    lang, db, uid = c.lang, c.db, c.uid
    b = biz.get(db, bid)
    if b is None or b["state"] != "open":
        raise GameError("branch_gone")
    if b["owner_id"] != uid:
        raise GameError("branch_denied")
    text = t(lang, "bz.cl.ask", branch=branch_text(b), till=usd(b["till"], lang), goods=num(stock.total(db, bid, stock.WH) + stock.total(db, bid, stock.DISP), lang),
             hq=t(lang, "bz.cl.hq") if biz.is_hq(db, b) else "")
    return R(c, text, [[(t(lang, "bz.b.confirm_close"), _cb("cL", bid, uid=uid))], [(t(lang, "bz.b.cancel"), _cb("bo", bid, uid=uid))]])


def close_do(c, bid: int) -> Reply:
    lang, db, uid = c.lang, c.db, c.uid
    b = biz.close_branch(db, uid, bid, core.now())
    return R(c, t(lang, "bz.cl.done", branch=branch_text(b), cash=usd(cash.balance(db, uid), lang)), [[(t(lang, "bz.b.branches"), _cb("l", uid=uid))], back_hub(lang, uid)], toast=plain(t(lang, "bz.cl.toast")))
