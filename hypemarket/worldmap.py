"""Draws a saved city layout as a PNG. Pure: no database, no generation, no Telegram; the layout is never mutated.

Art direction: an almost-black map with muted forest-green linework, in the spirit of a dark cartographic base map. Roads are the
primary structure (main roads brighter than streets), neighbourhoods are quiet shapes, lots are flat footprints (no 3D, no icons),
empty lots are dashed outlines, water and terrain lines are barely there. The picture is drawn at twice the size and scaled down,
which gives smooth curves without any extra dependency.
"""
from __future__ import annotations

import asyncio
import io
import math
import random
from collections import OrderedDict

from PIL import Image, ImageDraw

from . import cards
from . import worldgeom as G
from .texts import t

RENDER_VERSION = 1                      # bump when the look changes: the in-memory picture cache is keyed by it
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
ROAD_PX = {"main": 6.2, "street": 3.0}  # drawn width of a road in output pixels
CASING = 2.6


def _text_w(d, s, font, spacing=0.0):
    return d.textlength(s, font=font) + spacing * max(0, len(s) - 1)


def _draw_text(d, xy, text, size, fill, anchor="la", bold=False, spacing=0.0, shape=True):
    """Draw (and measure) text; returns its bounding box (x0, y0, x1, y1). Persian goes through the shaper like every share card."""
    s = cards.shape(text) if shape else text
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


def render(layout: dict, lang: str = "fa") -> bytes:
    """PNG bytes for a validated layout. Reads the layout only."""
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
        d.ellipse((cx - r, cy - r, cx + r, cy + r), fill=LANDMARK_LINE)
        label = t(lang, f"map.lm.{m['kind']}")
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
        text = t(lang, f"map.zone.{kind}")
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


# --- picture cache: a city never changes, so its picture is drawn once per (world, language, look) ------------------------
CACHE_MAX = 24
_cache: "OrderedDict[tuple, bytes]" = OrderedDict()


def cache_clear() -> None:
    _cache.clear()


async def render_cached(world_id: int, layout: dict, lang: str) -> bytes:
    """The map picture off the event loop (drawing takes about half a second), with a small in-memory cache."""
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


def _legend(d, lang: str, width: int, y: int):
    rtl = lang == "fa"
    items = ["main", "street", "building", "vacant", "park", "landmark"]
    f = cards.font(int(12 * SS))
    labels = [t(lang, f"map.legend.{k}") for k in items]
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
