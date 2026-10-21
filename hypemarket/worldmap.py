"""Draws a saved city layout as a PNG. Pure: no database, no generation, no Telegram; the layout is never mutated.

Art direction: an almost-black map with muted forest-green linework, in the spirit of a dark cartographic base map. Roads are the
primary structure (main roads brighter than streets), neighbourhoods are quiet shapes, lots are flat footprints (no 3D, no icons),
empty lots are dashed outlines, water and terrain lines are barely there. The picture is drawn at twice the size and scaled down,
which gives smooth curves without any extra dependency.
"""
from __future__ import annotations

import asyncio
import io
import logging
import math
import random
import re
from collections import OrderedDict

from PIL import Image, ImageDraw

from . import cards
from . import worldgeom as G
from .texts import t

log = logging.getLogger("hypemarket")

RENDER_VERSION = 2                      # bump when the look changes: the in-memory picture cache is keyed by it (2: the casino landmark)
MAX_PNG = 2_500_000
SCALE = 0.92                             # output pixels per world unit
SS = 2                                  # supersampling factor
HEADER = 90
FOOTER = 72

# --- palette ---------------------------------------------------------------------------------------------------------
BG = (8, 11, 9)                         # #080B09
LAND = (16, 24, 18)                     # #101812
STREET = (36, 59, 42)                   # #243B2A
MAIN = (69, 101, 74)                    # #45654A
WATER = (6, 15, 13)
WATER_EDGE = (22, 36, 27)
CONTOUR = (14, 22, 17)
OUTLINE = (27, 43, 32)
DISTRICT_FILL = {"residential": LAND, "commercial": (18, 28, 21), "industrial": (15, 21, 17), "civic": (19, 29, 22), "park": (13, 28, 18)}
BUILDING = {"residential": (30, 47, 35), "commercial": (42, 65, 48), "industrial": (34, 41, 36), "civic": (36, 56, 42)}
VACANT_LINE = (58, 92, 66)
VACANT_FILL = (19, 29, 22)
GREEN_FILL = (13, 30, 19)
TREE = (33, 68, 43)
LANDMARK_FILL = (28, 44, 33)
LANDMARK_LINE = (126, 168, 133)
LABEL = (104, 126, 108)                 # muted gray-green
LABEL_DIM = (74, 94, 79)
LABEL_BRIGHT = (150, 174, 155)
HILITE = (170, 232, 184)                # #AAE8B8: the selected place of an inspection picture (pale mint, still green-dominant)
HILITE_FILL = (118, 196, 136, 84)       # its translucent fill
ROAD_PX = {"main": 6.2, "street": 3.0}  # drawn width of a road in output pixels
CASING = 2.6


MAP_WORD = "نقشه ی"                      # the Persian «map» as the map's own labels spell it: a plain space between «نقشه» and «ی»
_MAP_WORD_VARIANTS = re.compile("نقشه[\u200c\u200d\u200e\u200f]?ی")


def fix_label(text: str) -> str:
    """Every label the renderer draws goes through here: the word «map» written fully joined, or with a zero-width joiner / non-joiner between
    its last two letters, becomes exactly «نقشه ی» (a plain space). Idempotent, and it only touches that one word."""
    return _MAP_WORD_VARIANTS.sub(MAP_WORD, text) if isinstance(text, str) else text


def _text_w(d, s, font, spacing=0.0):
    return d.textlength(s, font=font) + spacing * max(0, len(s) - 1)


def _draw_text(d, xy, text, size, fill, anchor="la", bold=False, spacing=0.0, shape=True):
    """Draw (and measure) text; returns its bounding box (x0, y0, x1, y1). Persian goes through the shaper like every share card."""
    s = cards.shape(fix_label(text)) if shape else text
    f = cards.font(int(size * SS), bold)
    w = _text_w(d, s, f, spacing * SS)
    x, y = xy
    h = size * SS
    if anchor[0] == "r":
        x -= w
    elif anchor[0] == "m":
        x -= w / 2
    if anchor[1] == "m":
        y -= h / 2
    elif anchor[1] == "d":
        y -= h
    if spacing:
        cx = x
        for ch in s:
            d.text((cx, y), ch, font=f, fill=fill)
            cx += d.textlength(ch, font=f) + spacing * SS
    else:
        d.text((x, y), s, font=f, fill=fill)
    return (x, y, x + w, y + h)


def _hit(box, boxes, pad=6):
    return any(not (box[2] + pad < o[0] or o[2] + pad < box[0] or box[3] + pad < o[1] or o[3] + pad < box[1]) for o in boxes)


def _dashed(d, pts, fill, width, dash=5.0, gap=3.5):
    """A closed polygon outline made of short dashes (an empty lot)."""
    n = len(pts)
    for i in range(n):
        a, b = pts[i], pts[(i + 1) % n]
        L = math.hypot(b[0] - a[0], b[1] - a[1])
        if L < 1e-6:
            continue
        ux, uy = (b[0] - a[0]) / L, (b[1] - a[1]) / L
        pos = 0.0
        while pos < L:
            e = min(L, pos + dash * SS)
            d.line((a[0] + ux * pos, a[1] + uy * pos, a[0] + ux * e, a[1] + uy * e), fill=fill, width=max(1, int(width * SS)))
            pos += (dash + gap) * SS


def _shrink(pts, f):
    c = G.centroid(pts)
    return [(c[0] + (x - c[0]) * (1 - f), c[1] + (y - c[1]) * (1 - f)) for x, y in pts]


def _road(d, pts, color, width):
    w = max(1, int(round(width * SS)))
    d.line(pts, fill=color, width=w, joint="curve")
    r = w / 2
    for p in (pts[0], pts[-1]):
        d.ellipse((p[0] - r, p[1] - r, p[0] + r, p[1] + r), fill=color)


def _stipple(d, pts, pid, color):
    """Tree dots inside a park polygon: deterministic per parcel, from a private rng."""
    rng = random.Random(f"trees:{pid}")
    x0, y0, x1, y1 = G.bbox(pts)
    n = int(abs(G.area(pts)) / (SS * SS) / 140)
    r = 1.5 * SS
    placed = 0
    for _ in range(n * 4):
        if placed >= n:
            break
        p = (rng.uniform(x0, x1), rng.uniform(y0, y1))
        if G.point_in_poly(p, pts):
            d.ellipse((p[0] - r, p[1] - r, p[0] + r, p[1] + r), fill=color)
            placed += 1


def render(layout: dict, lang: str = "fa", highlight: dict | None = None) -> bytes:
    """PNG bytes for a validated layout. Reads the layout only. `highlight` (see worldinspect.highlight) marks one place on the SAME map;
    without it the picture is exactly the plain map."""
    W, H = layout["w"], layout["h"]
    mw, mh = int(W * SCALE), int(H * SCALE)
    width, height = mw, HEADER + mh + FOOTER
    img = Image.new("RGB", (width * SS, height * SS), BG)
    d = ImageDraw.Draw(img)
    rtl = lang == "fa"
    pad_side = 17

    def P(p):
        return (p[0] * SCALE * SS, (HEADER + p[1] * SCALE) * SS)

    def PP(pts):
        return [P(p) for p in pts]

    # 1. faint terrain lines in the countryside
    for pl in layout["terrain"]["contours"]:
        d.line(PP(pl), fill=CONTOUR, width=SS, joint="curve")
    # 2. the land and its neighbourhoods
    d.polygon(PP(layout["footprint"]), fill=LAND)
    for dist in layout["districts"]:
        d.polygon(PP(dist["poly"]), fill=DISTRICT_FILL[dist["kind"]])
    # 3. water
    for wt in layout["water"]:
        d.polygon(PP(wt["poly"]), fill=WATER)
    # 4. parks and woods, then lots
    for p in layout["parcels"]:
        pts = PP(p["poly"])
        zone = p["zone"]
        if p["use"] == "green":
            d.polygon(pts, fill=GREEN_FILL)
            _stipple(d, pts, p["id"], TREE)
        elif p["use"] == "vacant":
            d.polygon(pts, fill=VACANT_FILL)
            _dashed(d, pts, VACANT_LINE, 1.0)
        elif p["use"] == "building":
            d.polygon(_shrink(pts, 0.2), fill=BUILDING[zone])
        else:
            d.polygon(pts, fill=LANDMARK_FILL, outline=LANDMARK_LINE, width=max(1, int(1.4 * SS)))
    # 5. the city outline, then water edges
    d.line(PP(layout["footprint"]) + [P(layout["footprint"][0])], fill=OUTLINE, width=SS, joint="curve")
    # 6. roads: dark casings first, then streets, then main roads on top
    roads = [(r, PP(r["pts"])) for r in layout["roads"]]
    for r, pts in roads:
        _road(d, pts, BG, ROAD_PX[r["kind"]] + CASING)
    for kind, color in (("street", STREET), ("main", MAIN)):
        for r, pts in roads:
            if r["kind"] == kind:
                _road(d, pts, color, ROAD_PX[kind])
    # 7. labels
    boxes: list = []
    wet = [PP(wt["poly"]) for wt in layout["water"]]
    mains = [pts for r, pts in roads if r["kind"] == "main"]

    def _crossed(box) -> bool:
        """Does a main road run through this label box?"""
        for pts in mains:
            for a, b in zip(pts, pts[1:]):
                n = max(1, int(G.dist(a, b) / (5 * SS)))
                for i in range(n + 1):
                    x, y = a[0] + (b[0] - a[0]) * i / n, a[1] + (b[1] - a[1]) * i / n
                    if box[0] - 3 * SS <= x <= box[2] + 3 * SS and box[1] - 3 * SS <= y <= box[3] + 3 * SS:
                        return True
        return False

    def _wet(box) -> bool:
        pts = [(box[0], box[1]), (box[2], box[1]), (box[0], box[3]), (box[2], box[3]), ((box[0] + box[2]) / 2, (box[1] + box[3]) / 2)]
        return any(G.point_in_poly(q, poly) for poly in wet for q in pts)
    lms = {m["parcel"]: m for m in layout["landmarks"]}
    for p in layout["parcels"]:
        m = lms.get(p["id"])
        if not m:
            continue
        cx, cy = P(m["at"])
        r = 3.2 * SS
        if m["kind"] == "casino":                                        # the casino: a small diamond with a dark core instead of the plain dot
            q = 5.2 * SS
            d.polygon([(cx, cy - q), (cx + q, cy), (cx, cy + q), (cx - q, cy)], fill=LANDMARK_LINE)
            q2 = 2.0 * SS
            d.polygon([(cx, cy - q2), (cx + q2, cy), (cx, cy + q2), (cx - q2, cy)], fill=LANDMARK_FILL)
        else:
            d.ellipse((cx - r, cy - r, cx + r, cy + r), fill=LANDMARK_LINE)
        label = fix_label(t(lang, f"map.lm.{m['kind']}"))
        size = 12.5
        f = cards.font(int(size * SS), True)
        wlab = d.textlength(cards.shape(label), font=f)
        off = 8 * SS
        spots = [("lm", cx + off, cy), ("rm", cx - off, cy), ("md", cx, cy - off), ("ma", cx, cy + off)]
        if rtl:
            spots = [("rm", cx - off, cy), ("lm", cx + off, cy)] + spots[2:]
        for strict in (True, False):
            done = False
            for anc, x, y in spots:
                x0 = x if anc[0] == "l" else x - wlab if anc[0] == "r" else x - wlab / 2
                y0 = y - size * SS / 2 if anc[1] == "m" else y - size * SS if anc[1] == "d" else y
                box = (x0, y0, x0 + wlab, y0 + size * SS)
                if _hit(box, boxes, 3 * SS) or (strict and (_wet(box) or _crossed(box))):
                    continue
                _draw_text(d, (x, y), label, size, LABEL_BRIGHT, anchor=anc if anc[1] != "a" else anc[0] + "a", bold=True)
                boxes.append(box)
                done = True
                break
            if done:
                break
    best: dict = {}
    for dist in layout["districts"]:
        a = abs(G.area(dist["poly"]))
        if dist["kind"] not in best or a > best[dist["kind"]][0]:
            best[dist["kind"]] = (a, dist)
    for kind in ("civic", "commercial", "industrial", "residential", "park"):
        if kind not in best:
            continue
        dist = best[kind][1]
        text = fix_label(t(lang, f"map.zone.{kind}"))
        text = text.upper() if not rtl else text
        cx, cy = P(dist["label"])
        sp = 2.4 if not rtl else 0.0
        f = cards.font(int(12 * SS), True)
        s = cards.shape(text)
        wlab = _text_w(d, s, f, sp * SS)
        for dy, dx in ((0, 0), (-22, 0), (22, 0), (-44, 0), (44, 0), (0, -70), (0, 70), (-22, -70), (22, 70)):
            box = (cx + dx * SS - wlab / 2, cy + dy * SS - 7 * SS, cx + dx * SS + wlab / 2, cy + dy * SS + 7 * SS)
            if not _hit(box, boxes, 4 * SS) and not _wet(box) and not _crossed(box):
                _draw_text(d, (cx + dx * SS, cy + dy * SS), text, 12, LABEL, anchor="mm", bold=True, spacing=sp)
                boxes.append(box)
                break
    # the world is cropped to its frame: the river and the contours run off the edge of the map, not into the title or the legend
    top, bottom = (HEADER - 6) * SS, (HEADER + mh + 6) * SS
    d.rectangle((0, 0, width * SS, top - 1), fill=BG)
    d.rectangle((0, bottom + 1, width * SS, height * SS), fill=BG)
    d.rectangle((0, 0, pad_side * SS, height * SS), fill=BG)
    d.rectangle(((width - pad_side) * SS, 0, width * SS, height * SS), fill=BG)
    if highlight:
        _highlight(img, d, P, highlight, (pad_side + 6, HEADER, width - pad_side - 6, HEADER + mh), boxes)
    # 8. title and legend
    name = layout["name"].get(lang) or layout["name"]["en"]
    pad = 34
    if rtl:
        _draw_text(d, ((width - pad) * SS, 20 * SS), name, 30, LABEL_BRIGHT, anchor="ra", bold=True)
        _draw_text(d, ((width - pad) * SS, 56 * SS), t(lang, "map.subtitle"), 13, LABEL_DIM, anchor="ra")
    else:
        _draw_text(d, (pad * SS, 20 * SS), name.upper(), 30, LABEL_BRIGHT, anchor="la", bold=True, spacing=3.0)
        _draw_text(d, (pad * SS, 56 * SS), t(lang, "map.subtitle"), 13, LABEL_DIM, anchor="la")
    _legend(d, lang, width, HEADER + mh + 26)
    # frame
    d.rectangle((pad_side * SS, (HEADER - 6) * SS, (width - pad_side) * SS, (HEADER + mh + 6) * SS), outline=(20, 32, 24), width=SS)
    out = img.resize((width, height), Image.BOX)                  # an exact 2x2 average: no ringing halos around text and lines
    buf = io.BytesIO()
    out.save(buf, "PNG", optimize=True)
    data = buf.getvalue()
    if len(data) > MAX_PNG:                                           # never happens with this palette, but a photo must stay small
        buf = io.BytesIO()
        out.quantize(colors=48).save(buf, "PNG", optimize=True)
        data = buf.getvalue()
        if len(data) > MAX_PNG:
            raise ValueError("the map picture is too large")
    return data


def _highlight(img, d, P, hl: dict, frame: tuple, boxes: list) -> None:
    """Mark one place for an inspection picture: a translucent mint fill and outline on the real geometry, a ring that is easy to spot on
    a phone around small places, and the ID in a small tag. Flat colours only (no glow, no gradient); nothing else on the map changes.
    The tag looks for a spot that covers no label (`boxes` are the labels already drawn) and stays inside the map frame."""
    ov = Image.new("RGBA", img.size, (0, 0, 0, 0))
    od = ImageDraw.Draw(ov)
    outlines = []
    for poly in hl["polys"]:
        pts = [P(p) for p in poly]
        od.polygon(pts, fill=HILITE_FILL)
        outlines.append(pts)
    img.paste(ov.convert("RGB"), mask=ov.getchannel("A"))
    for pts in outlines:
        d.line(pts + [pts[0]], fill=HILITE, width=int(2.6 * SS), joint="curve")
    cx, cy = P(hl["center"])
    reach = 0.0
    if hl.get("ring"):
        xs = [p[0] for pts in outlines for p in pts]
        ys = [p[1] for pts in outlines for p in pts]
        reach = max(26 * SS, math.hypot(max(xs) - min(xs), max(ys) - min(ys)) / 2 + 12 * SS)
        d.ellipse((cx - reach, cy - reach, cx + reach, cy + reach), outline=HILITE, width=int(3.0 * SS))
    size = 15
    f = cards.font(int(size * SS), True)
    w = d.textlength(hl["tag"], font=f)
    padx, pady = 6 * SS, 3 * SS
    bw, bh = w + 2 * padx, size * SS + 2 * pady
    fx0, fy0, fx1, fy1 = (v * SS for v in frame)
    gap = 4 * SS
    spots = []
    if reach:
        for deg in (45, 0, 135, 180, 90, 270, 315, 225):               # around the ring, upper right first
            c, s_ = math.cos(math.radians(deg)), math.sin(math.radians(deg))
            ax, ay = cx + (reach + gap) * c, cy - (reach + gap) * s_
            spots.append((ax if c > 0.3 else ax - bw if c < -0.3 else ax - bw / 2, ay - bh if s_ > 0.3 else ay if s_ < -0.3 else ay - bh / 2))
    else:                                                              # a district: on its label point, nudged off the zone name
        spots = [(cx - bw / 2, cy + dy * SS - bh / 2) for dy in (-26, 26, -52, 52, 0)]
    poly = outlines[0] if outlines and not reach else None
    pick = None
    for strict in (True, False):
        for sx, sy in spots:
            box = (sx, sy, sx + bw, sy + bh)
            if not (fx0 <= sx and sx + bw <= fx1 and fy0 <= sy and sy + bh <= fy1):
                continue
            if poly is not None and not G.point_in_poly((sx + bw / 2, sy + bh / 2), poly):
                continue
            if strict and _hit(box, boxes, 2 * SS):
                continue
            pick = (sx, sy)
            break
        if pick:
            break
    if pick is None:
        pick = (min(max(spots[0][0], fx0), fx1 - bw), min(max(spots[0][1], fy0), fy1 - bh))
    x0, y0 = pick
    d.rounded_rectangle((x0, y0, x0 + bw, y0 + bh), radius=3 * SS, fill=BG, outline=HILITE, width=SS)
    d.text((x0 + padx, y0 + pady), hl["tag"], font=f, fill=HILITE)


# --- picture cache: a city never changes, so its picture is drawn once per (world, language, look) ------------------------
CACHE_MAX = 24
HCACHE_MAX = 16                          # inspection pictures (one per place looked at) live in their own small cache
_cache: "OrderedDict[tuple, bytes]" = OrderedDict()
_hcache: "OrderedDict[tuple, bytes]" = OrderedDict()


def cache_clear() -> None:
    _cache.clear()
    _hcache.clear()


async def render_cached(world_id: int, layout: dict, lang: str, highlight: dict | None = None, ref: str | None = None) -> bytes:
    """The map picture off the event loop (drawing takes about half a second), with a small in-memory cache. With `highlight` (and the
    `ref` that names the place) it draws the inspection version of the same saved map."""
    if highlight is not None:
        hkey = (world_id, lang, RENDER_VERSION, ref)
        hit = _hcache.get(hkey)
        if hit is not None:
            _hcache.move_to_end(hkey)
            return hit
        data = await asyncio.to_thread(render, layout, lang, highlight)
        _hcache[hkey] = data
        while len(_hcache) > HCACHE_MAX:
            _hcache.popitem(last=False)
        return data
    key = (world_id, lang, RENDER_VERSION)
    hit = _cache.get(key)
    if hit is not None:
        _cache.move_to_end(key)
        return hit
    data = await asyncio.to_thread(render, layout, lang)
    _cache[key] = data
    while len(_cache) > CACHE_MAX:
        _cache.popitem(last=False)
    return data


# --- people and walks on top of a finished picture ----------------------------------------------------------------------------
# The saved map picture is drawn (and cached) once; each viewer's own marker, the players near them and their walk are painted on a copy of
# it. Anti-aliased by drawing each shape on a small mask at 4x and shrinking it. Muted mint only: nothing here is brighter than the labels.
ME = (146, 224, 170)                    # the viewer's own marker: a solid mint disc
PEER = (128, 182, 142)                  # other players: small mint rings
ROUTE_DONE = (46, 76, 54)
ROUTE_LEFT = HILITE
MARK_SS = 4


def _px(p) -> tuple:
    return (p[0] * SCALE, HEADER + p[1] * SCALE)


def _paint(img, box, color, fn) -> None:
    x0, y0, x1, y1 = (int(math.floor(box[0])), int(math.floor(box[1])), int(math.ceil(box[2])), int(math.ceil(box[3])))
    x0, y0, x1, y1 = max(0, x0), max(0, y0), min(img.width, x1), min(img.height, y1)
    if x1 <= x0 or y1 <= y0:
        return
    ss = MARK_SS
    mask = Image.new("L", ((x1 - x0) * ss, (y1 - y0) * ss), 0)
    fn(ImageDraw.Draw(mask), lambda q: ((q[0] - x0) * ss, (q[1] - y0) * ss), ss)
    img.paste(color, (x0, y0, x1, y1), mask.resize((x1 - x0, y1 - y0), Image.BOX))


def _bounds(pts, pad):
    xs, ys = [q[0] for q in pts], [q[1] for q in pts]
    return (min(xs) - pad, min(ys) - pad, max(xs) + pad, max(ys) + pad)


def _dashes(md, pts, ss, width, dash=7.0, gap=5.0):
    pos, on = 0.0, True
    for a, b in zip(pts, pts[1:]):
        L = math.hypot(b[0] - a[0], b[1] - a[1])
        if L < 1e-9:
            continue
        ux, uy = (b[0] - a[0]) / L, (b[1] - a[1]) / L
        at = 0.0
        while at < L:
            span = (dash if on else gap) * ss - pos
            e = min(L, at + span)
            if on:
                md.line((a[0] + ux * at, a[1] + uy * at, a[0] + ux * e, a[1] + uy * e), fill=255, width=max(1, int(width * ss)))
            pos += e - at
            at = e
            if pos >= (dash if on else gap) * ss - 1e-9:
                on, pos = not on, 0.0


def _mark_label(img, d, text, at, boxes, color=LABEL_BRIGHT, size=12):
    """A short name next to a marker: the first of four spots that stays inside the picture and covers no other label; none if all are taken."""
    if not text:
        return
    s = cards.shape(fix_label(str(text))[:14])
    f = cards.font(size, True)
    w = d.textlength(s, font=f)
    h = size + 2
    x, y = at
    for ox, oy in ((9, -h / 2), (-9 - w, -h / 2), (-w / 2, -9 - h), (-w / 2, 8)):
        box = (x + ox, y + oy, x + ox + w, y + oy + h)
        if box[0] < 4 or box[2] > img.width - 4 or box[1] < 4 or box[3] > img.height - 4 or _hit(box, boxes, 2):
            continue
        d.text((box[0], box[1]), s, font=f, fill=color, stroke_width=2, stroke_fill=BG)
        boxes.append(box)
        return


def overlay(png: bytes, lang: str, marks: dict) -> bytes:
    """`png` (a finished city picture) with: marks["walked"] / marks["route"] (the part of a walk already done / still ahead, lists of world
    points), marks["dest"] ({"pt", "tag"}), marks["others"] (list of {"pt", "label"}) and marks["me"] ({"pt", "label"}). Pure: nothing is stored."""
    img = Image.open(io.BytesIO(png)).convert("RGB")
    d = ImageDraw.Draw(img)
    walked, left = marks.get("walked"), marks.get("route")
    if walked and len(walked) >= 2:
        pts = [_px(p) for p in walked]
        _paint(img, _bounds(pts, 4), ROUTE_DONE, lambda md, T, ss: md.line([T(q) for q in pts], fill=255, width=int(2.0 * ss), joint="curve"))
    if left and len(left) >= 2:
        pts = [_px(p) for p in left]
        _paint(img, _bounds(pts, 4), ROUTE_LEFT, lambda md, T, ss: _dashes(md, [T(q) for q in pts], ss, 2.2))
    boxes: list = []
    dest = marks.get("dest")
    if dest:
        cx, cy = _px(dest["pt"])
        _paint(img, (cx - 14, cy - 14, cx + 14, cy + 14), HILITE, lambda md, T, ss: md.ellipse([*(v - 9.5 * ss for v in T((cx, cy))), *(v + 9.5 * ss for v in T((cx, cy)))], outline=255, width=int(2.0 * ss)))
        if dest.get("tag"):
            _mark_label(img, d, dest["tag"], (cx, cy), boxes, HILITE, 13)
    for o in marks.get("others", ()):
        cx, cy = _px(o["pt"])

        def ring(md, T, ss, cx=cx, cy=cy):
            tx, ty = T((cx, cy))
            md.ellipse((tx - 5.2 * ss, ty - 5.2 * ss, tx + 5.2 * ss, ty + 5.2 * ss), fill=255)
        _paint(img, (cx - 8, cy - 8, cx + 8, cy + 8), BG, ring)                      # a dark disc so the ring reads on any road
        _paint(img, (cx - 8, cy - 8, cx + 8, cy + 8), PEER, lambda md, T, ss, cx=cx, cy=cy: md.ellipse([*(v - 4.4 * ss for v in T((cx, cy))), *(v + 4.4 * ss for v in T((cx, cy)))], outline=255, width=int(1.7 * ss)))
    me = marks.get("me")
    if me:
        cx, cy = _px(me["pt"])
        _paint(img, (cx - 13, cy - 13, cx + 13, cy + 13), BG, lambda md, T, ss: md.ellipse([*(v - 8.4 * ss for v in T((cx, cy))), *(v + 8.4 * ss for v in T((cx, cy)))], fill=255))
        _paint(img, (cx - 13, cy - 13, cx + 13, cy + 13), ME, lambda md, T, ss: md.ellipse([*(v - 7.4 * ss for v in T((cx, cy))), *(v + 7.4 * ss for v in T((cx, cy)))], outline=255, width=int(1.6 * ss)))
        _paint(img, (cx - 13, cy - 13, cx + 13, cy + 13), ME, lambda md, T, ss: md.ellipse([*(v - 4.4 * ss for v in T((cx, cy))), *(v + 4.4 * ss for v in T((cx, cy)))], fill=255))
    for o in marks.get("others", ()):
        _mark_label(img, d, o.get("label"), _px(o["pt"]), boxes, LABEL, 11)
    if me:
        _mark_label(img, d, me.get("label"), _px(me["pt"]), boxes, ME, 12)
    buf = io.BytesIO()
    img.save(buf, "PNG", compress_level=6)
    data = buf.getvalue()
    if len(data) > MAX_PNG:
        raise ValueError("the map picture is too large")
    return data


async def render_marked(world_id: int, layout: dict, lang: str, marks: dict | None, highlight: dict | None = None, ref: str | None = None) -> bytes:
    """The (cached) city picture with a viewer's markers painted on a copy. If the markers cannot be drawn the plain picture is returned."""
    base = await render_cached(world_id, layout, lang, highlight, ref)
    if not marks:
        return base
    try:
        return await asyncio.to_thread(overlay, base, lang, marks)
    except Exception:
        log.exception("markers on the picture of world %s failed", world_id)
        return base


def _legend(d, lang: str, width: int, y: int):
    rtl = lang == "fa"
    items = ["main", "street", "building", "vacant", "park", "landmark"]
    f = cards.font(int(12 * SS))
    labels = [fix_label(t(lang, f"map.legend.{k}")) for k in items]
    sw = 26
    widths = [sw + 8 + d.textlength(cards.shape(s), font=f) / SS + 22 for s in labels]
    total = sum(widths) - 22
    x = (width - total) / 2
    order = list(zip(items, labels, widths))
    if rtl:
        order.reverse()
    for k, label, w in order:
        sx0, cy = x * SS, y * SS
        if k == "main":
            d.line((sx0, cy, sx0 + sw * SS, cy), fill=MAIN, width=int(ROAD_PX["main"] * SS))
        elif k == "street":
            d.line((sx0, cy, sx0 + sw * SS, cy), fill=STREET, width=int(ROAD_PX["street"] * SS))
        elif k == "building":
            d.rectangle((sx0 + 5 * SS, cy - 5 * SS, sx0 + (sw - 5) * SS, cy + 5 * SS), fill=BUILDING["commercial"])
        elif k == "vacant":
            _dashed(d, [(sx0 + 4 * SS, cy - 5 * SS), (sx0 + (sw - 4) * SS, cy - 5 * SS), (sx0 + (sw - 4) * SS, cy + 5 * SS), (sx0 + 4 * SS, cy + 5 * SS)], VACANT_LINE, 1.0, 3.0, 2.5)
        elif k == "park":
            d.rectangle((sx0 + 3 * SS, cy - 6 * SS, sx0 + (sw - 3) * SS, cy + 6 * SS), fill=GREEN_FILL)
            for dx, dy in ((8, -2), (14, 2), (19, -2)):
                r = 1.5 * SS
                d.ellipse((sx0 + dx * SS - r, cy + dy * SS - r, sx0 + dx * SS + r, cy + dy * SS + r), fill=TREE)
        else:
            r = 3.4 * SS
            d.ellipse((sx0 + sw / 2 * SS - r, cy - r, sx0 + sw / 2 * SS + r, cy + r), fill=LANDMARK_LINE)
        tx = (x + sw + 8) * SS
        _draw_text(d, (tx, cy), label, 12, LABEL, anchor="lm")
        x += w
