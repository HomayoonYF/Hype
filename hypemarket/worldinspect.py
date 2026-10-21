"""World Interaction v1: inspect any location of a group's city  (`مپ P14`, `مپ L2`, `مپ D3`).

This module only READS a saved layout. It never writes to the database and never touches the layout it is given: the same layout and
the same ID always give the same answer, whatever the language, the picture size, the colours or the restart in between.

IDs are the ones the world engine already stores inside every layout (`p…` parcels, `l…` public places, `d…` districts, plus the
unused `b…` blocks, `r…` roads and `w…` water). They are assigned once, when the city is generated, and live in the saved JSON next to
the geometry, so nothing here invents or renumbers anything; a group's IDs can only ever be looked up in that group's own layout.

What the panels say is only what the layout really contains: the kind of land, the district, whether the lot is built, the nearest road,
where it is, how big it is, what is close by. There is no owner, price, income or permit anywhere: none of that exists yet.
"""
from __future__ import annotations

import math
import re
from dataclasses import dataclass

from . import fmt, textparse, worldgeom as G
from .texts import t

KINDS = {"p": "parcel", "l": "landmark", "d": "district"}      # the ID prefixes a player can inspect
FRONTAGE = 18.0           # a lot whose edge is this close to a road's centre line fronts that road (lots sit ~11 away; the first inner lots ~20+)
NEARBY = 4                # how many neighbouring lots a panel lists
_STEP = 6.0               # sampling step along a lot's edge when measuring the distance to the roads
_ID = re.compile(r"^#?([a-z])[-_.:]?0*(\d{1,6})$")
_TRIM = "<>«»\"'()[]{}.,،;؛:!؟?"


def display_id(ident: str) -> str:
    """How an ID is shown to players: the stored ID in capitals (p14 -> P14). Typing it either way, or as P-014, finds the same object."""
    return str(ident).upper()


# --- turning what the player typed into a stored ID --------------------------------------------------------------------
def normalize(token) -> str | None:
    """«P-014», «p14», «#P14», «پ14»-style digits (۱۴) all become the stored form «p14»; anything that is not ID-shaped gives None."""
    if not isinstance(token, str):
        return None
    m = _ID.match(textparse.key(token.strip().strip(_TRIM)))
    return f"{m.group(1)}{int(m.group(2))}" if m else None


def parse_ref(args) -> str | None:
    """The ID in the words after «مپ»: one word («P14», «P-014») or a letter and a number («P 14»). Anything else is not an ID."""
    if not isinstance(args, (list, tuple)):
        return None
    toks = [a for a in args if isinstance(a, str) and a.strip()]
    if len(toks) == 1:
        return normalize(toks[0])
    if len(toks) == 2 and len(textparse.key(toks[0]).strip(_TRIM)) == 1:
        return normalize(toks[0] + toks[1])
    return None


@dataclass(frozen=True)
class Target:
    kind: str                 # "parcel" | "landmark" | "district"
    obj: dict                 # the layout's own entry (read only)


def resolve(layout: dict, ref: str) -> Target | None:
    """The object `ref` names in THIS layout, or None (unknown ID, or an ID that names no inspectable object such as a road or a block)."""
    kind = KINDS.get(ref[:1]) if isinstance(ref, str) else None
    if not kind:
        return None
    for obj in layout[{"parcel": "parcels", "landmark": "landmarks", "district": "districts"}[kind]]:
        if obj["id"] == ref:
            return Target(kind, obj)
    return None


# --- facts -----------------------------------------------------------------------------------------------------------------
def _tables(layout: dict) -> dict:
    return {"blocks": {b["id"]: b for b in layout["blocks"]}, "districts": {d["id"]: d for d in layout["districts"]},
            "parcels": {p["id"]: p for p in layout["parcels"]}, "by_parcel": {m["parcel"]: m for m in layout["landmarks"]}}


def _edge_samples(poly) -> list:
    out = []
    n = len(poly)
    for i in range(n):
        a, b = poly[i], poly[(i + 1) % n]
        k = max(1, int(G.dist(a, b) / _STEP))
        out += [(a[0] + (b[0] - a[0]) * j / k, a[1] + (b[1] - a[1]) * j / k) for j in range(k)]
    return out


def road_access(layout: dict, poly) -> tuple:
    """(distance from the polygon's edge to the nearest road's centre line, that road's kind). Pure geometry on the saved roads."""
    pts = _edge_samples(poly)
    x0, y0, x1, y1 = G.bbox(poly)
    best, kind = math.inf, "street"
    for r in layout["roads"]:
        rp = r["pts"]
        if best < math.inf:
            rx0, rx1 = min(p[0] for p in rp), max(p[0] for p in rp)
            ry0, ry1 = min(p[1] for p in rp), max(p[1] for p in rp)
            if rx0 - x1 > best or x0 - rx1 > best or ry0 - y1 > best or y0 - ry1 > best:
                continue
        d = min(G.polyline_dist(rp, s) for s in pts)
        if d < best:
            best, kind = d, r["kind"]
    return best, kind


def _near_place(layout: dict, at) -> dict | None:
    best = None
    for m in layout["landmarks"]:
        d = G.dist(at, m["at"])
        if best is None or d < best[0]:
            best = (d, m)
    return {"id": best[1]["id"], "kind": best[1]["kind"], "dist": best[0]} if best else None


def _id_num(ident: str) -> int:
    return int(ident[1:]) if ident[1:].isdigit() else 0


def _neighbours(layout: dict, me: dict) -> list:
    """The closest other lots (empty, built or public places; parks are whole blocks and say little about where you are), nearest first."""
    c = me["c"]
    cand = [(G.dist(c, p["c"]), _id_num(p["id"]), p["id"]) for p in layout["parcels"] if p["id"] != me["id"] and p["use"] != "green"]
    cand.sort()
    return [i for _, _, i in cand[:NEARBY]]


def facts(layout: dict, target: Target) -> dict:
    """Everything a panel states, as plain language-free data."""
    tab = _tables(layout)
    if target.kind == "district":
        d = target.obj
        inside = [p for p in layout["parcels"] if tab["blocks"][p["block"]]["district"] == d["id"]]
        use = {u: sum(p["use"] == u for p in inside) for u in ("vacant", "building", "green", "landmark")}
        places = [m for m in layout["landmarks"] if tab["blocks"][tab["parcels"][m["parcel"]]["block"]]["district"] == d["id"]]
        return {"kind": "district", "id": d["id"], "zone": d["kind"], "density": d["density"], "at": d["label"], "use": use,
                "places": [{"id": m["id"], "kind": m["kind"]} for m in places], "lots": use["vacant"] + use["building"] + use["landmark"]}
    if target.kind == "landmark":
        lm = target.obj
        p = tab["parcels"][lm["parcel"]]
        name = lm["id"]
    else:
        p = target.obj
        lm = tab["by_parcel"].get(p["id"])
        name = p["id"]
    dist_, road = road_access(layout, p["poly"])
    block = tab["blocks"][p["block"]]
    out = {"kind": target.kind, "id": name, "parcel": p["id"], "use": p["use"], "zone": p["zone"], "district": block["district"],
           "district_zone": tab["districts"][block["district"]]["kind"], "at": list(p["c"]), "area": p["area"],
           "direct": dist_ <= FRONTAGE, "road": road, "road_dist": dist_,
           "landmark": ({"id": lm["id"], "kind": lm["kind"]} if lm else None), "near": _neighbours(layout, p)}
    out["near_place"] = None if lm else _near_place(layout, p["c"])
    return out


def highlight(layout: dict, target: Target) -> dict:
    """What the picture should mark, as plain geometry for worldmap.render (the layout itself is never modified)."""
    if target.kind == "district":
        d = target.obj
        return {"polys": [d["poly"]], "center": d["label"], "tag": display_id(d["id"]), "ring": False}
    pid = target.obj["parcel"] if target.kind == "landmark" else target.obj["id"]
    p = next(q for q in layout["parcels"] if q["id"] == pid)
    x0, y0, x1, y1 = G.bbox(p["poly"])
    return {"polys": [p["poly"]], "center": p["c"], "tag": display_id(target.obj["id"]), "ring": max(x1 - x0, y1 - y0) < 110}


# --- the words ---------------------------------------------------------------------------------------------------------
def _row(lang: str, label: str, value: str) -> str:
    return f"• {t(lang, 'wi.f.' + label)}: {value}"


def _ids(ids) -> str:
    return " · ".join(f"<code>{display_id(i)}</code>" for i in ids)


def _n(v, lang: str) -> str:
    """A coordinate: plain digits, no thousands separator (so «1068, 566» can't be misread), localized."""
    return fmt.loc(str(int(round(v))), lang)


def _where(lang: str, f: dict) -> str:
    x, y = _n(f["at"][0], lang), _n(f["at"][1], lang)
    near = f.get("near_place")
    if near:
        return t(lang, "wi.where.near", x=x, y=y, name=t(lang, f"map.lm.{near['kind']}"), id=display_id(near["id"]))
    return t(lang, "wi.where", x=x, y=y)


def _type(lang: str, f: dict) -> str:
    if f["use"] == "landmark":
        return t(lang, "wi.type.landmark", name=t(lang, f"map.lm.{f['landmark']['kind']}"))
    if f["use"] == "green":
        return t(lang, "wi.type.green.park" if f["zone"] == "park" else "wi.type.green.other")
    return t(lang, f"wi.type.lot.{f['zone']}")


def _road(lang: str, f: dict) -> str:
    if f["direct"]:
        return t(lang, "wi.road.direct", road=t(lang, f"map.legend.{f['road']}"))
    return t(lang, "wi.road.inner", d=fmt.num(f["road_dist"], lang))


def panel(lang: str, world, layout: dict, target: Target, notes: bool = True) -> str:
    """The localized information panel (HTML, short enough for a photo caption: the notes at the end are what gets dropped first).
    `notes=False` leaves the two closing lines out (the interactive map says its own)."""
    f = facts(layout, target)
    city = fmt.esc(world["name_fa"] if lang == "fa" else world["name_en"])
    ident = display_id(f["id"])
    zone_name = lambda z: t(lang, f"map.zone.{z}")
    if f["kind"] == "district":
        rows = [t(lang, "wi.title.district", id=ident, city=city),
                "",
                _row(lang, "type", zone_name(f["zone"])),
                _row(lang, "density", t(lang, f"wi.density.{f['density']}")),
                _row(lang, "lots", t(lang, "wi.lots", n=fmt.num(f["lots"], lang), vacant=fmt.num(f["use"]["vacant"], lang), built=fmt.num(f["use"]["building"], lang)))]
        if f["use"]["green"]:
            rows.append(_row(lang, "green", t(lang, "wi.green", n=fmt.num(f["use"]["green"], lang))))
        if f["places"]:
            rows.append(_row(lang, "places", " · ".join(f"{t(lang, 'map.lm.' + m['kind'])} <code>{display_id(m['id'])}</code>" for m in f["places"])))
        rows.append(_row(lang, "centre", t(lang, "wi.xy", x=_n(f["at"][0], lang), y=_n(f["at"][1], lang))))
    else:
        if f["kind"] == "landmark":
            title = t(lang, "wi.title.landmark", name=t(lang, f"map.lm.{f['landmark']['kind']}"), id=ident, city=city)
        else:
            title = t(lang, "wi.title.parcel", id=ident, city=city)
        status = t(lang, f"wi.status.{f['use']}")
        rows = [title, "", _row(lang, "type", _type(lang, f)), _row(lang, "district", f"{zone_name(f['district_zone'])} (<code>{display_id(f['district'])}</code>)")]
        if f["kind"] == "landmark":
            rows.append(_row(lang, "parcel", f"<code>{display_id(f['parcel'])}</code>"))
        elif f["landmark"]:
            rows.append(_row(lang, "place", f"{t(lang, 'map.lm.' + f['landmark']['kind'])} <code>{display_id(f['landmark']['id'])}</code>"))
        rows += [_row(lang, "status", status), _row(lang, "road", _road(lang, f)), _row(lang, "where", _where(lang, f)),
                 _row(lang, "size", t(lang, "wi.size", n=fmt.num(f["area"], lang)))]
        if f["near"]:
            rows.append(_row(lang, "near", _ids(f["near"])))
    if notes:
        rows += ["", t(lang, "wi.foot"), t(lang, "wi.back")]
    return "\n".join(rows)


@dataclass(frozen=True)
class Result:
    id: str                    # the stored ID that was found
    kind: str
    text: str
    highlight: dict


def inspect(lang: str, world, layout: dict, ref: str, notes: bool = True) -> Result | None:
    """Resolve `ref` inside this world's saved layout and describe it (None = no such inspectable object here). Pure: reads only."""
    target = resolve(layout, ref)
    if target is None:
        return None
    return Result(target.obj["id"], target.kind, panel(lang, world, layout, target, notes), highlight(layout, target))


# --- hints for the plain map -------------------------------------------------------------------------------------------
def example_id(layout: dict) -> str:
    """A real ID of this city to use in examples (the first public place)."""
    return display_id(layout["landmarks"][0]["id"])


def places_line(lang: str, layout: dict) -> str:
    """«شهرداری L1 · بیمارستان L2 …»: the public places and their IDs, the easiest way in."""
    return " · ".join(f"{t(lang, 'map.lm.' + m['kind'])} <code>{display_id(m['id'])}</code>" for m in layout["landmarks"])


def unknown_text(lang: str, layout: dict, ref: str) -> str:
    return t(lang, "wi.unknown", id=fmt.esc(display_id(ref)), example=example_id(layout))
