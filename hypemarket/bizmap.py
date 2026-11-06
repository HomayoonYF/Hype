"""The business world on the city map (v0.211): which little pixel pictures and style marks the map's overlay paints. Pure reading of the database; the saved city picture is never regenerated (the
overlay paints on a copy, exactly like the player's own marker).

    shops / workshops   every OPEN branch of the city, drawn at its lot with the colours of its sign upgrade
    lots for sale       the unowned properties close to the viewer (all of them would bury the map)
    the cotton site     the park, always
    style               the tier of the viewer and of the players on the picture (a ring, two rings, a spark, a crown); a plain outfit adds nothing
"""
from __future__ import annotations

from . import biz, brands, character, config as C, core, estate, fmt, style, textile, upgrades, worldgeom as G
from .texts import t

MAX_SHOPS = 80
MAX_FORSALE = 14


def _centre(city, parcel_id: str):
    p = city._pidx.get(parcel_id)
    return tuple(p["c"]) if p is not None and p.get("c") else None


def places(db, city, pos) -> list:
    """[{"pt", "kind": shop | work | sale | cotton, "sign"}] for the picture of this city around `pos` (the viewer)."""
    out: list = []
    site = textile.site(city)
    if site is not None and site.get("c"):
        out.append({"pt": tuple(site["c"]), "kind": "cotton", "sign": "plain"})
    for b in biz.in_world(db, city.id, open_only=True)[:MAX_SHOPS]:
        prop = estate.get(db, b["property_id"])
        pt = _centre(city, prop["parcel_id"]) if prop else None
        if pt is None:
            continue
        retail = estate.mode_allows(prop["mode"], "retail")
        out.append({"pt": pt, "kind": "shop" if retail else "work", "sign": upgrades.sign_style(db, b["id"])})
    reach = C.BIZ_NEAR_UNITS * 4
    sale = []
    for p in estate.for_sale(db, city.id):
        pt = _centre(city, p["parcel_id"])
        if pt is not None and G.dist(pt, pos) <= reach:
            sale.append((G.dist(pt, pos), p["id"], pt))
    for _d, _i, pt in sorted(sale)[:MAX_FORSALE]:
        out.append({"pt": pt, "kind": "sale", "sign": "plain"})
    return out


def tiers(db, uids) -> dict:
    return style.marker_tiers(db, [u for u in uids if u is not None])


def _owner_name(db, uid: int) -> str:
    ch = character.get(db, uid)
    if ch is not None and ch["first_name"]:
        return fmt.esc(f"{ch['first_name']} {ch['last_name']}".strip())
    u = core.get_user(db, uid)
    return fmt.esc(core.display_name(u, False)) if u is not None else str(uid)


def inspect_lines(lang: str, db, city, spot) -> list:
    """What the map's «place» panel adds for a spot (v0.211): the cotton park says what it is for, and a lot that is a commercial property says whether it is for sale (and for how much), who owns it, what it
    is fitted for and which branch is open there. Read-only, and nothing is shown for a spot that is neither."""
    out = []
    if spot is None:
        return out
    if textile.site_id(city) == spot.id:
        out.append(t(lang, "bz.ins.cotton"))
    prop = estate.by_parcel(db, city.id, spot.id)
    if prop is not None:
        tier = t(lang, f"bz.tier.{prop['tier']}")
        if prop["owner_id"] is None:
            out.append(t(lang, "bz.ins.free", tier=tier, price=fmt.usd(prop["price"], lang)))
        else:
            mode = t(lang, f"bz.mode.{prop['mode']}") if prop["mode"] else t(lang, "bz.mode.none")
            out.append(t(lang, "bz.ins.owned", tier=tier, owner=_owner_name(db, prop["owner_id"]), mode=mode))
            b = db.q1("SELECT * FROM branches WHERE property_id=? AND state='open'", (prop["id"],))
            if b is not None:
                out.append(t(lang, "bz.ins.branch", branch=fmt.ltr(fmt.esc(f"{brands.name_of(b['brand'])} #{b['seq']}"))))
    return out
