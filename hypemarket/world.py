"""World Engine v1: one persistent, procedurally generated city per Telegram group.

Layout format 2 (v0.15): an organic city on a continuous plane, not a grid of cells.
  * neighbourhoods are curved-outline areas (a Voronoi diagram bent by a smooth warp), each with its own street direction and density;
  * roads are polylines of explicit world coordinates, joined at nodes (T-junctions, crossroads, bridges over a river);
  * blocks are the faces between roads; parcels (lots) are small convex polygons inside blocks, each with a stable id;
  * water, parks and a few faint terrain contour lines complete the picture.
The generator is deterministic (seed + GENERATOR_VERSION -> identical layout) and only ever touches its own `random.Random`, so the
stock market's randomness is never disturbed. The COMPLETE layout is stored as JSON, so a future generator can change freely
without silently redrawing cities that groups already know.
"""
from __future__ import annotations

import heapq
import asyncio
import json
import logging
import math
import random
import secrets
from collections import deque

from . import core
from . import worldgeom as G
from .db import Database

log = logging.getLogger(__name__)

GENERATOR_VERSION = 2
LAYOUT_VERSION = 2
MAX_ATTEMPTS = 6
W, H = 1360, 1020                       # the world's first extent in world units; future areas are added OUTSIDE it, nothing moves
MAX_SIZE = 8000

DISTRICTS = ("residential", "commercial", "industrial", "civic", "park")
LANDMARKS = ("city_hall", "hospital", "school", "station")
USES = ("vacant", "building", "green", "landmark")
ROAD_KINDS = ("main", "street")
ROAD_HALF = {"main": 5.5, "street": 3.5}          # half the drawn width of a road, in world units
MIN_VACANT_SHARE = 0.25                           # at least a quarter of the lots stay empty for the future
ROAD_MARGIN = 11.0                                # lots keep this far from the road's centre line (flat space, before the warp)
DEAD_END_SLACK = 6.0                              # a road may only end this close to the city's outline (where it leaves town)
MAX_ROAD_REACH = 160.0                            # every lot's centre lies within this distance of a road

# name parts come in (fa, en) pairs so both languages name the same city
_PREFIX = [("مهر", "Mehr"), ("نو", "Now"), ("سپید", "Sepid"), ("زرین", "Zarrin"), ("آبان", "Aban"), ("خرم", "Khorram"),
           ("شاد", "Shad"), ("بهار", "Bahar"), ("پارس", "Pars"), ("کیان", "Kian"), ("نیک", "Nik"), ("آذر", "Azar"),
           ("گل", "Gol"), ("سرو", "Sarv"), ("فیروز", "Firooz"), ("روشن", "Roshan"), ("دریا", "Darya"), ("سیمین", "Simin"),
           ("ماه", "Mah"), ("تابان", "Taban"), ("ایرا", "Ira"), ("کوهسار", "Kuhsar"), ("نسیم", "Nasim"), ("آرام", "Aram"),
           ("پیروز", "Piruz"), ("رویا", "Roya"), ("لاله", "Laleh"), ("اوج", "Owj"), ("مروارید", "Morvarid"), ("ساحل", "Sahel")]
_SUFFIX = [("آباد", "abad"), ("شهر", "shahr"), ("دژ", "dej"), ("کند", "kand"), ("گرد", "gerd"), ("سار", "sar"),
           ("پور", "pur"), ("وند", "vand"), ("بندر", "bandar"), ("ده", "deh")]


class LayoutError(ValueError):
    """The generated/stored layout does not describe a valid city."""


class WorldError(Exception):
    def __init__(self, code: str):
        super().__init__(code)
        self.code = code


def _r1(v: float) -> float:
    return round(v, 1)


def city_name(rng: random.Random) -> dict:
    p, s = rng.choice(_PREFIX), rng.choice(_SUFFIX)
    return {"fa": p[0] + s[0], "en": p[1] + s[1]}


# =====================================================================================================================
#                                                   generation
# =====================================================================================================================
# street spacing per neighbourhood kind: (across, along) in flat world units; lot (width, depth) targets
SPACING = {("residential", "dense"): ((62, 78), (84, 110)), ("residential", "open"): ((96, 120), (120, 160)),
           ("commercial", "dense"): ((56, 70), (70, 92)), ("civic", "dense"): ((90, 110), (110, 140)),
           ("industrial", "open"): ((130, 160), (160, 220))}
LOT = {("residential", "dense"): (20, 31), ("residential", "open"): (28, 40), ("commercial", "dense"): (19, 28),
       ("civic", "dense"): (34, 44), ("industrial", "open"): (54, 66)}
BUILT = {"residential": 0.60, "commercial": 0.62, "industrial": 0.48, "civic": 0.42}


class _River:
    """A meandering river in flat space: centre line, half widths, and helpers to test distance to it."""

    def __init__(self, pts, hw):
        self.pts, self.hw = pts, hw

    def info(self, p):
        """(distance to the centre line, half width there)"""
        best, bi, bt = 1e18, 0, 0.0
        for i in range(len(self.pts) - 1):
            a, b = self.pts[i], self.pts[i + 1]
            dx, dy = b[0] - a[0], b[1] - a[1]
            L = dx * dx + dy * dy
            t = max(0.0, min(1.0, ((p[0] - a[0]) * dx + (p[1] - a[1]) * dy) / L)) if L > 1e-12 else 0.0
            d = math.hypot(p[0] - (a[0] + t * dx), p[1] - (a[1] + t * dy))
            if d < best:
                best, bi, bt = d, i, t
        return best, self.hw[bi] + (self.hw[bi + 1] - self.hw[bi]) * bt

    def offset(self, extra: float, sign: int):
        out = []
        n = len(self.pts)
        for i, p in enumerate(self.pts):
            a, b = self.pts[max(0, i - 1)], self.pts[min(n - 1, i + 1)]
            dx, dy = b[0] - a[0], b[1] - a[1]
            L = math.hypot(dx, dy) or 1.0
            nx, ny = -dy / L, dx / L
            r = self.hw[i] + extra
            out.append((p[0] + sign * nx * r, p[1] + sign * ny * r))
        return out


def _round_corners(poly, passes: int = 2, ratio: float = 0.2, min_turn: float = 0.1):
    """Chamfer every noticeable corner of a convex polygon (it stays convex)."""
    for _ in range(passes):
        out = []
        n = len(poly)
        for i in range(n):
            a, b, c = poly[i - 1], poly[i], poly[(i + 1) % n]
            t1 = math.atan2(b[1] - a[1], b[0] - a[0])
            t2 = math.atan2(c[1] - b[1], c[0] - b[0])
            turn = abs((t2 - t1 + math.pi) % math.tau - math.pi)
            if turn < min_turn:
                out.append(b)
                continue
            la, lc = G.dist(a, b), G.dist(b, c)
            ra, rc = min(ratio, 0.45 * la / max(la, 1e-9)), min(ratio, 0.45 * lc / max(lc, 1e-9))
            out.append((b[0] + (a[0] - b[0]) * ra, b[1] + (a[1] - b[1]) * ra))
            out.append((b[0] + (c[0] - b[0]) * rc, b[1] + (c[1] - b[1]) * rc))
        poly = out
    return poly


def _outline(rng: random.Random, cx, cy, rx, ry) -> list:
    """The city's edge: an ellipse with a few flat sides taken off and the corners softened (always convex)."""
    ring = [(cx + rx * math.cos(math.tau * i / 96), cy + ry * math.sin(math.tau * i / 96)) for i in range(96)]
    for _ in range(rng.randint(3, 5)):
        th = rng.uniform(0, math.tau)
        nx, ny = math.cos(th), math.sin(th)
        reach = 1 / math.sqrt((nx / rx) ** 2 + (ny / ry) ** 2)
        f = rng.uniform(0.80, 0.92)
        px, py = cx + nx * reach * f, cy + ny * reach * f
        cut = G.clip_halfplane(ring, -nx, -ny, nx * px + ny * py)
        if len(cut) >= 8 and G.area(cut) > 0.8 * G.area(ring):
            ring = cut
    return _round_corners(ring)


def _make_river(rng: random.Random, ring, cx: float, cy: float) -> _River:
    for _ in range(14):
        vertical = rng.random() < 0.5
        oa, ob = rng.uniform(-240, 240), rng.uniform(-240, 240)
        a, b = ((cx + oa, -80.0), (cx + ob, H + 80.0)) if vertical else ((-80.0, cy + oa), (W + 80.0, cy + ob))
        if rng.random() < 0.5:
            a, b = b, a
        dx, dy = b[0] - a[0], b[1] - a[1]
        L = math.hypot(dx, dy)
        nx, ny = -dy / L, dx / L
        a1, f1, p1 = rng.uniform(55, 105), rng.uniform(1.1, 1.9), rng.uniform(0, math.tau)
        a2, f2, p2 = rng.uniform(12, 28), rng.uniform(3.2, 4.6), rng.uniform(0, math.tau)
        w0, w1 = rng.uniform(22, 28), rng.uniform(34, 44)
        n = 96
        pts, hw = [], []
        for i in range(n + 1):
            t = i / n
            m = a1 * math.sin(math.tau * f1 * t + p1) + a2 * math.sin(math.tau * f2 * t + p2)
            pts.append((a[0] + dx * t + nx * m, a[1] + dy * t + ny * m))
            hw.append((w0 + (w1 - w0) * t) / 2)
        inside = sum(G.dist(pts[i], pts[i + 1]) for i in range(n)
                     if G.point_in_poly(((pts[i][0] + pts[i + 1][0]) / 2, (pts[i][1] + pts[i + 1][1]) / 2), ring))
        if inside >= 560:
            return _River(pts, hw)
    raise LayoutError("no usable river")


def _neighbourhoods(rng: random.Random, ring, cx, cy, rx, ry) -> list:
    n_cells = rng.randint(8, 11)
    seeds = [(cx + rng.uniform(-35, 35), cy + rng.uniform(-30, 30))]
    tries = 0
    while len(seeds) < n_cells and tries < 4000:
        tries += 1
        a, r = rng.uniform(0, math.tau), math.sqrt(rng.random())
        p = (cx + rx * 0.94 * r * math.cos(a), cy + ry * 0.94 * r * math.sin(a))
        if all(G.dist(p, q) >= 185 for q in seeds) and G.point_in_poly(p, ring):
            seeds.append(p)
    if len(seeds) < 8:
        raise LayoutError("could not place enough neighbourhoods")

    def cells_of(sd):
        out = []
        for i, s in enumerate(sd):
            pts, tags = list(ring), ["E"] * len(ring)
            for j, o in enumerate(sd):
                if j != i:
                    pts, tags = G.clip_tagged(pts, tags, 2 * (s[0] - o[0]), 2 * (s[1] - o[1]), (o[0] ** 2 + o[1] ** 2) - (s[0] ** 2 + s[1] ** 2), ("c", j))
                    if len(pts) < 3:
                        break
            if len(pts) < 3 or G.area(pts) < 8000:
                raise LayoutError("a neighbourhood vanished")
            out.append((pts, tags))
        return out
    cells = cells_of(seeds)
    for _ in range(2):                                              # relax: even out the neighbourhood sizes
        seeds = [seeds[0]] + [G.centroid(pts) for pts, _t in cells[1:]]
        cells = cells_of(seeds)
    return [{"seed": seeds[i], "poly": pts, "tags": tags} for i, (pts, tags) in enumerate(cells)]


def _assign_kinds(rng: random.Random, cells: list, river: _River, cx, cy, rx) -> None:
    n = len(cells)
    for c in cells:
        c["c"] = G.centroid(c["poly"])
        c["area"] = G.area(c["poly"])
    order = sorted(range(1, n), key=lambda i: G.dist(cells[i]["c"], (cx, cy)))
    kinds = {0: "civic"}
    for i in order[:2 if n >= 9 else 1]:
        kinds[i] = "commercial"
    outer = [i for i in order if i not in kinds and G.dist(cells[i]["c"], (cx, cy)) >= 0.3 * rx] or [i for i in order if i not in kinds]
    ind = min(outer, key=lambda i: river.info(cells[i]["c"])[0])
    kinds[ind] = "industrial"
    if n >= 10 and rng.random() < 0.4:
        near = sorted((i for i in outer if i not in kinds), key=lambda i: G.dist(cells[i]["c"], cells[ind]["c"]))
        if near:
            kinds[near[0]] = "industrial"
    rest = sorted((i for i in outer if i not in kinds), key=lambda i: -cells[i]["area"])
    parks = 1 + (1 if n >= 10 and rng.random() < 0.5 else 0)
    for i in rest[:parks]:
        kinds[i] = "park"
    for i in range(n):
        k = kinds.get(i, "residential")
        cells[i]["kind"] = k
        far = G.dist(cells[i]["c"], (cx, cy)) / rx
        cells[i]["density"] = "open" if (k in ("industrial",) or (k == "residential" and far > 0.55)) else "dense"
        cells[i]["id"] = f"d{i + 1}"
    if sum(c["kind"] == "residential" for c in cells) < 3:
        raise LayoutError("not enough room for homes")


def _street_chords(rng: random.Random, c: dict, cx, cy) -> list:
    """Straight street chords of one neighbourhood (flat space): two crossing families, one of them continuous."""
    kind, dens = c["kind"], c["density"]
    (u0, u1), (v0, v1) = SPACING[(kind, dens)]
    k = rng.uniform(0.92, 1.12)
    su, sv = rng.uniform(u0, u1) * k, rng.uniform(v0, v1) * k
    phi = math.atan2(cy - c["seed"][1], cx - c["seed"][0])
    theta = rng.uniform(0, math.pi) if c["id"] == "d1" else phi + rng.choice((0.0, math.pi / 2)) + rng.gauss(0, 0.16) + rng.uniform(-0.3, 0.3)
    nA = (math.cos(theta), math.sin(theta))
    nB = (-nA[1], nA[0])
    poly = c["poly"]

    def lines(n, sp):
        ds = [n[0] * x + n[1] * y for x, y in poly]
        lo, hi = min(ds), max(ds)
        d = lo + sp * rng.uniform(0.35, 0.9)
        out = []
        while d < hi - sp * 0.4:
            out.append(d)
            d += sp * rng.uniform(0.78, 1.28)
        return out
    fam = {"A": (nA, lines(nA, su)), "B": (nB, lines(nB, sv))}
    full = rng.choice("AB")
    other = "B" if full == "A" else "A"
    p_trunc = 0.32 if dens == "dense" else 0.55
    p_drop = 0.05 if dens == "dense" else 0.2
    segs = []
    for name in (full, other):
        n, ds = fam[name]
        nf, dsf = fam["B" if name == "A" else "A"]
        for d in ds:
            ch = G.chord(poly, n[0], n[1], d)
            if not ch or G.dist(*ch) < 24:
                continue
            p, q = ch
            if name == other:
                if rng.random() < p_drop:
                    continue
                sp_, sq_ = nf[0] * p[0] + nf[1] * p[1], nf[0] * q[0] + nf[1] * q[1]
                if sp_ > sq_:
                    p, q, sp_, sq_ = q, p, sq_, sp_
                cross = [x for x in dsf if sp_ + 6 < x < sq_ - 6]
                if len(cross) >= 2 and rng.random() < p_trunc:       # stop at a street instead of crossing the whole area: a T-junction
                    cpos = rng.choice(cross[1:] if rng.random() < 0.5 else cross[:-1])
                    pt = (n[0] * d + nf[0] * cpos, n[1] * d + nf[1] * cpos)
                    if rng.random() < 0.5:
                        q = pt
                    else:
                        p = pt
            segs.append((p, q))
    return segs


def _arterials(arr: G.Arrangement, rng: random.Random, centre_pt, rx, ry, cx, cy, cross_pen: float) -> set:
    """Main roads: shortest, straight-preferring routes from a few gates on the city outline to the centre."""
    nodes = arr.nodes
    adj: dict = {}
    for (a, b), e in arr.edges.items():
        if e["kind"] == "limit":
            continue
        L = G.dist(nodes[a], nodes[b]) + (cross_pen if "cross" in e["tags"] else 0.0)
        adj.setdefault(a, []).append((b, L))
        adj.setdefault(b, []).append((a, L))
    ring_nodes = sorted({n for (a, b), e in arr.edges.items() if "ring" in e["tags"] for n in (a, b) if n in adj})
    if not adj or not ring_nodes:
        raise LayoutError("no roads")
    centre = min(adj, key=lambda n: (G.dist(nodes[n], centre_pt), n))
    base = rng.uniform(0, math.tau)
    gates = []
    for k in range(5):
        ang = base + k * math.tau / 5 + rng.uniform(-0.25, 0.25)
        tgt = (cx + rx * math.cos(ang), cy + ry * math.sin(ang))
        g = min(ring_nodes, key=lambda n: (G.dist(nodes[n], tgt), n))
        if g not in gates:
            gates.append(g)
    main: set = set()
    for g in gates:
        # Dijkstra over directed edges so that turning costs something and straight roads win
        start = (g, -1)
        best = {start: 0.0}
        prev = {}
        heap = [(0.0, g, -1)]
        end = None
        while heap:
            d, u, pu = heapq.heappop(heap)
            if d > best.get((u, pu), 1e18):
                continue
            if u == centre:
                end = (u, pu)
                break
            for v, L in adj.get(u, ()):
                if v == pu:
                    continue
                turn = 0.0
                if pu >= 0:
                    ax, ay = nodes[u][0] - nodes[pu][0], nodes[u][1] - nodes[pu][1]
                    bx, by = nodes[v][0] - nodes[u][0], nodes[v][1] - nodes[u][1]
                    la, lb = math.hypot(ax, ay), math.hypot(bx, by)
                    if la > 1e-9 and lb > 1e-9:
                        turn = 70.0 * (1.0 - (ax * bx + ay * by) / (la * lb))
                nd = d + L + turn
                st = (v, u)
                if nd < best.get(st, 1e18):
                    best[st] = nd
                    prev[st] = (u, pu)
                    heapq.heappush(heap, (nd, v, u))
        if end is None:
            continue
        cur = end
        while cur in prev:
            p = prev[cur]
            a, b = cur[0], p[0]
            main.add((a, b) if a < b else (b, a))
            cur = p
    return main


def _frontage_lots(poly, key, rng: random.Random) -> list:
    """Lots along the block's streets: rectangles that face a street, tile its frontage and keep clear of every road.
    Works for any simple block shape (including blocks bent by the river bank): a lot is accepted only if it fits inside."""
    wt, dt = LOT[key]
    margin = ROAD_MARGIN
    simp = G.simplify_closed(poly, 3.0)
    n = len(simp)
    edges = [(poly[i], poly[(i + 1) % len(poly)]) for i in range(len(poly))]

    def clear(p) -> bool:
        return G.point_in_poly(p, poly) and min(G.seg_point_dist(a, b, p) for a, b in edges) >= margin - 0.6
    lots = []
    for i in sorted(range(n), key=lambda i: (-G.dist(simp[i], simp[(i + 1) % n]), i)):
        a, b = simp[i], simp[(i + 1) % n]
        L = G.dist(a, b)
        start, end = margin * 0.8, L - margin * 0.8
        if end - start < wt * 0.8:
            continue
        ux, uy = (b[0] - a[0]) / L, (b[1] - a[1]) / L
        nx, ny = -uy, ux
        k = max(1, round((end - start) / (wt * rng.uniform(0.9, 1.1))))
        ws = [rng.uniform(0.86, 1.14) for _ in range(k)]
        scale = (end - start) / sum(ws)
        pos = start
        for w in ws:
            s0, s1 = pos, pos + w * scale
            pos = s1
            for depth in (dt * rng.uniform(0.92, 1.08), dt * 0.75, dt * 0.55):
                p0 = (a[0] + ux * s0 + nx * margin, a[1] + uy * s0 + ny * margin)
                p1 = (a[0] + ux * s1 + nx * margin, a[1] + uy * s1 + ny * margin)
                rect = [p0, p1, (p1[0] + nx * depth, p1[1] + ny * depth), (p0[0] + nx * depth, p0[1] + ny * depth)]
                probes = rect + [((rect[j][0] + rect[(j + 1) % 4][0]) / 2, (rect[j][1] + rect[(j + 1) % 4][1]) / 2) for j in range(4)] + [G.centroid(rect)]
                if all(clear(q) for q in probes) and not any(G.polys_overlap(rect, o, -0.08) for o in lots):
                    lots.append(rect)
                    break
    out = []
    for rect in lots:
        piece = G.inset_convex(rect, 0.9)
        if piece and G.area(piece) >= 0.4 * wt * dt:
            out.append(piece)
    return out


def _sliced_lots(poly, key, rng: random.Random) -> list:
    """Small convex blocks: the block's inner core cut into a few lots side by side along its long axis."""
    wt, dt = LOT[key]
    core = G.inset_convex(poly, ROAD_MARGIN)
    if not core:
        return []
    target = wt * dt * 1.1
    n = max(1, min(6, round(G.area(core) / target)))
    i = max(range(len(core)), key=lambda i: G.dist(core[i], core[(i + 1) % len(core)]))
    a, b = core[i], core[(i + 1) % len(core)]
    L = G.dist(a, b)
    ux, uy = (b[0] - a[0]) / L, (b[1] - a[1]) / L
    ts = [x * ux + y * uy for x, y in core]
    lo, hi = min(ts), max(ts)
    ws = [rng.uniform(0.82, 1.18) for _ in range(n)]
    total, cum = sum(ws), [0.0]
    for w in ws:
        cum.append(cum[-1] + w / total)
    out = []
    for k in range(n):
        piece = G.clip_halfplane(core, ux, uy, -(lo + (hi - lo) * cum[k]))
        piece = G.clip_halfplane(piece, -ux, -uy, lo + (hi - lo) * cum[k + 1]) if len(piece) >= 3 else []
        if len(piece) >= 3:
            piece = G.inset_convex(piece, 0.9)
        if piece and G.area(piece) >= 0.4 * wt * dt:
            out.append(piece)
    return out


def _lots(poly, key, rng: random.Random) -> list:
    """Lots for one block: small convex blocks are sliced, bigger or bent ones get lots along their frontage."""
    if G.area(poly) < 0:
        poly = poly[::-1]
    wt, dt = LOT[key]
    if G.is_convex(poly) and G.area(poly) < 7 * wt * dt:
        out = _sliced_lots(poly, key, rng)
        if out:
            return out
    out = _frontage_lots(poly, key, rng)
    if not out and G.is_convex(poly):
        out = _sliced_lots(poly, key, rng)
    return out


def _holds_water(poly, river) -> bool:
    """True when the river's centre line passes through this face (a long, bent face can have its centroid on dry land)."""
    xs = [p[0] for p in poly]
    ys = [p[1] for p in poly]
    x0, x1, y0, y1 = min(xs), max(xs), min(ys), max(ys)
    return any(x0 <= p[0] <= x1 and y0 <= p[1] <= y1 and G.point_in_poly(p, poly) for p in river.pts)


def generate(seed: int, version: int = GENERATOR_VERSION) -> dict:
    """A finite organic city: neighbourhoods with their own street directions, bending roads, a river, parks, lots, civic landmarks."""
    if version != GENERATOR_VERSION:
        raise LayoutError(f"unknown generator version {version}")
    rng = random.Random(f"hype-world:{version}:{seed}")
    warp = G.Warp(rng)
    if not warp.relax(W, H, 0.74):
        raise LayoutError("the warp would fold the map over itself")
    cx, cy = W / 2 + rng.uniform(-40, 40), H / 2 + rng.uniform(-30, 30)
    rx, ry = rng.uniform(535, 585), rng.uniform(395, 430)
    ring = _outline(rng, cx, cy, rx, ry)
    river = _make_river(rng, ring, cx, cy)
    cells = _neighbourhoods(rng, ring, cx, cy, rx, ry)
    _assign_kinds(rng, cells, river, cx, cy, rx)

    # --- the road network, built in flat space ---------------------------------------------------------------------
    arr = G.Arrangement()
    for i in range(len(ring)):
        arr.add(ring[i], ring[(i + 1) % len(ring)], "limit", "ring")
    for i, c in enumerate(cells):
        pts, tags = c["poly"], c["tags"]
        for k in range(len(pts)):
            tg = tags[k]
            if tg != "E" and tg[1] > i:
                arr.add(pts[k], pts[(k + 1) % len(pts)], "street")
        if c["kind"] != "park":
            for p, q in _street_chords(rng, c, cx, cy):
                arr.add(p, q, "street")
    for sign in (1, -1):                                              # the two embankment roads that follow the river
        bank = river.offset(7.0, sign)
        for a, b in zip(bank, bank[1:]):
            seg = G.clip_segment(ring, a, b)
            if seg:
                arr.add(seg[0], seg[1], "street", "bank")
    arr.build()

    # crossings of the river: only bridges (on the main roads, plus a few more) survive
    crossing = []
    for key, e in arr.edges.items():
        if e["kind"] == "limit" or "bank" in e["tags"]:
            continue
        a, b = arr.nodes[key[0]], arr.nodes[key[1]]
        d, hw = river.info(((a[0] + b[0]) / 2, (a[1] + b[1]) / 2))
        if d < hw + 3.5:
            e["tags"].add("cross")
            crossing.append(key)
    main = _arterials(arr, rng, (cx, cy), rx, ry, cx, cy, cross_pen=80.0)
    for key in main:
        arr.edges[key]["kind"] = "main"
    keep_cross = {k for k in crossing if k in main}
    spare = sorted((k for k in crossing if k not in keep_cross), key=lambda k: (arr.nodes[k[0]][0] + arr.nodes[k[0]][1], k))
    for k in spare:
        if len(keep_cross) >= 3:
            break
        mid = ((arr.nodes[k[0]][0] + arr.nodes[k[1]][0]) / 2, (arr.nodes[k[0]][1] + arr.nodes[k[1]][1]) / 2)
        if all(G.dist(mid, ((arr.nodes[o[0]][0] + arr.nodes[o[1]][0]) / 2, (arr.nodes[o[0]][1] + arr.nodes[o[1]][1]) / 2)) > 200 for o in keep_cross):
            keep_cross.add(k)
    for k in crossing:
        if k in keep_cross:
            arr.edges[k]["tags"].add("bridge")
        else:
            del arr.edges[k]
    exits = {n for (a, b), e in arr.edges.items() if "ring" in e["tags"] for n in (a, b)}
    before = sum(e["kind"] != "limit" for e in arr.edges.values())
    arr.prune_dead_ends(keep=exits)
    arr.keep_largest_component()
    arr.prune_dead_ends(keep=exits)
    after = sum(e["kind"] != "limit" for e in arr.edges.values())
    if after < 0.8 * before or after < 80:
        raise LayoutError("the road network fell apart")
    road_edges = {k: e for k, e in arr.edges.items() if e["kind"] != "limit"}
    if not any(e["kind"] == "main" for e in road_edges.values()):
        raise LayoutError("no main road")

    # --- blocks ---------------------------------------------------------------------------------------------------
    nodes = arr.nodes
    seeds = [c["seed"] for c in cells]
    blocks = []
    for face in arr.faces():
        fp = [nodes[i] for i in face]
        cen = G.centroid(fp)
        d, hw = river.info(cen)
        if d < hw + 7.5 or _holds_water(fp, river):
            continue                                                  # the river itself
        di = min(range(len(seeds)), key=lambda i: (G.dist(cen, seeds[i]), i))
        cell = cells[di]
        a = G.area(fp)
        per = sum(G.dist(fp[i], fp[(i + 1) % len(fp)]) for i in range(len(fp)))
        use = "lots" if (cell["kind"] != "park" and a >= 700) else "green"
        blocks.append({"face": face, "poly": fp, "area": a, "cen": cen, "cell": di, "use": use, "convex": G.is_convex(fp),
                       "compact": 4 * math.pi * a / (per * per)})
    if len(blocks) < 40:
        raise LayoutError("too few blocks")
    blocks.sort(key=lambda b: (round(b["cen"][1] / 40), b["cen"][0]))
    for i, b in enumerate(blocks):
        b["id"] = f"b{i + 1}"

    # --- landmarks take whole blocks ---------------------------------------------------------------------------------
    centre_pt = cells[0]["seed"]

    def pick(kinds, lo, hi, avoid=(), near=None):
        cand = [b for b in blocks if b["use"] == "lots" and b["convex"] and b["compact"] >= 0.6 and cells[b["cell"]]["kind"] in kinds and lo <= b["area"] <= hi
                and all(G.dist(b["cen"], o["cen"]) > 150 for o in avoid)]
        if not cand:
            return None
        ref = near or centre_pt
        return min(cand, key=lambda b: (G.dist(b["cen"], ref), b["id"]))

    landmarks = []
    taken = []
    plan = [("city_hall", ("civic", "commercial"), 1800, 9000, None), ("hospital", ("residential", "commercial", "civic"), 2200, 9000, None),
            ("school", ("residential",), 1800, 8000, None), ("station", ("commercial", "industrial", "civic"), 1500, 9000, None)]
    for kind, kinds, lo, hi, near in plan:
        b = pick(kinds, lo, hi, avoid=taken)
        if b is None:
            b = pick(kinds, 1000, 30000, avoid=taken)
        if b is None:
            raise LayoutError(f"no room for {kind}")
        b["use"] = "campus"
        b["landmark"] = kind
        taken.append(b)
        landmarks.append({"kind": kind, "block": b["id"]})

    # --- lots ------------------------------------------------------------------------------------------------------
    hfield = G.Height(random.Random(f"hype-world:{version}:{seed}:vacancy"))
    parcels = []
    for b in blocks:
        cell = cells[b["cell"]]
        key = (cell["kind"], cell["density"]) if (cell["kind"], cell["density"]) in LOT else (cell["kind"], "dense")
        if b["use"] == "lots":
            polys = _lots(b["poly"], key, rng)
            uses = []
            for pl in polys:
                c = G.centroid(pl)
                far = min(1.0, G.dist(c, (cx, cy)) / rx)
                noise = 0.5 + 0.5 * math.tanh(hfield(c[0], c[1]) / 1.6)
                share = BUILT[cell["kind"]] * (1.25 - 0.65 * far)
                score = 0.55 * rng.random() + 0.45 * noise
                uses.append("building" if score < share else "vacant")
            for pl, use in zip(polys, uses):
                parcels.append({"block": b["id"], "zone": cell["kind"], "use": use, "flat": pl})
        elif b["use"] == "campus":
            pl = G.inset_convex(b["poly"], ROAD_MARGIN)
            if not pl:
                raise LayoutError("a landmark block is too small")
            parcels.append({"block": b["id"], "zone": cell["kind"], "use": "landmark", "flat": pl, "landmark": b["landmark"]})
        else:
            parcels.append({"block": b["id"], "zone": cell["kind"], "use": "green", "flat": b["poly"]})
    lots = [p for p in parcels if p["use"] in ("building", "vacant")]
    need = math.ceil(MIN_VACANT_SHARE * len(lots)) - sum(p["use"] == "vacant" for p in lots)
    if need > 0:
        built = sorted((p for p in lots if p["use"] == "building"), key=lambda p: -G.dist(G.centroid(p["flat"]), (cx, cy)))
        for p in built[:need]:
            p["use"] = "vacant"
    if len(lots) < 80:
        raise LayoutError("too few lots")
    for i, p in enumerate(parcels):
        p["id"] = f"p{i + 1}"

    return _assemble(seed, version, rng, warp, ring, river, cells, arr, road_edges, blocks, parcels, landmarks, (cx, cy))


def _assemble(seed, version, rng, warp, ring, river, cells, arr, road_edges, blocks, parcels, landmarks, centre) -> dict:
    """Bend everything with the warp and write the stable, explicit, JSON-friendly layout."""
    def wp(p):
        x, y = warp(p[0], p[1])
        return [_r1(x), _r1(y)]

    def line(a, b, step):
        return [wp(a)] + [wp(p) for p in G.densify(a, b, step)]

    def loop(pts, step):
        out = []
        for i in range(len(pts)):
            out += line(pts[i], pts[(i + 1) % len(pts)], step)
        return out

    nodes = arr.nodes
    # roads: chains of edges through 2-way nodes that share the same class
    adj: dict = {}
    for (a, b), e in road_edges.items():
        adj.setdefault(a, []).append(b)
        adj.setdefault(b, []).append(a)

    def cls(a, b):
        e = road_edges[(a, b) if a < b else (b, a)]
        return (e["kind"], "bridge" in e["tags"])
    used = set()
    chains = []
    for a, b in sorted(road_edges):
        if (a, b) in used:
            continue
        used.add((a, b))
        chain = [a, b]
        c0 = cls(a, b)
        for end in (1, 0):
            while True:
                tip, back = (chain[-1], chain[-2]) if end == 1 else (chain[0], chain[1])
                if len(adj[tip]) != 2:
                    break
                nxt = adj[tip][0] if adj[tip][0] != back else adj[tip][1]
                key = (tip, nxt) if tip < nxt else (nxt, tip)
                if key in used or cls(tip, nxt) != c0:
                    break
                used.add(key)
                if end == 1:
                    chain.append(nxt)
                else:
                    chain.insert(0, nxt)
        chains.append((chain, c0))
    chains.sort(key=lambda c: (round(nodes[c[0][0]][1] / 30), nodes[c[0][0]][0], c[0][0]))
    roads, junction = [], {}
    for i, (chain, (kind, bridge)) in enumerate(chains):
        pts = []
        for u, v in zip(chain, chain[1:]):
            seg = line(nodes[u], nodes[v], 18.0)
            pts += seg if not pts else seg
        pts.append(wp(nodes[chain[-1]]))
        roads.append({"id": f"r{i + 1}", "kind": kind, "bridge": bridge, "pts": pts})
        for n in (chain[0], chain[-1]):
            junction[n] = wp(nodes[n])
    node_list = [list(q) for q in sorted({tuple(junction[n]) for n in junction})]

    # water
    left, right = river.offset(0.0, 1), river.offset(0.0, -1)
    water_poly = [wp(p) for p in left] + [wp(p) for p in reversed(right)]
    water = [{"id": "w1", "kind": "river", "pts": [wp(p) for p in river.pts], "hw": [_r1(h) for h in river.hw], "poly": water_poly}]

    # neighbourhoods
    districts = []
    for c in cells:
        districts.append({"id": c["id"], "kind": c["kind"], "density": c["density"], "poly": loop(c["poly"], 36.0), "label": wp(G.pole_of_poly(c["poly"]))})
    foot = loop(ring, 24.0)

    blist = []
    for b in blocks:
        entry = {"id": b["id"], "district": cells[b["cell"]]["id"], "use": b["use"], "poly": loop(b["poly"], 36.0)}
        if b["use"] == "campus":
            entry["landmark"] = b["landmark"]
        blist.append(entry)
    plist = []
    byid = {}
    for p in parcels:
        poly = [wp(q) for q in p["flat"]]
        e = {"id": p["id"], "block": p["block"], "zone": p["zone"], "use": p["use"], "poly": poly}
        c = G.centroid(poly)
        e["c"] = [_r1(c[0]), _r1(c[1])]
        e["area"] = int(round(abs(G.area(poly))))
        plist.append(e)
        byid[p["block"], p["use"]] = e
    lmk = []
    for i, lm in enumerate(landmarks):
        e = next(q for q in plist if q["block"] == lm["block"] and q["use"] == "landmark")
        lmk.append({"id": f"l{i + 1}", "kind": lm["kind"], "parcel": e["id"], "at": e["c"]})

    # faint terrain lines in the countryside only
    height = G.Height(random.Random(f"hype-world:{version}:{seed}:terrain"))
    fcx, fcy = G.centroid(foot)
    wide = [(fcx + (x - fcx) * 1.07, fcy + (y - fcy) * 1.07) for x, y in foot]
    wcl = [tuple(p) for p in water[0]["pts"]]

    def keep(x, y):
        if not (6 < x < W - 6 and 6 < y < H - 6):
            return False
        if G.point_in_poly((x, y), wide):
            return False
        return G.polyline_dist(wcl, (x, y)) > 34
    contours = G.contour_polylines(height, W, H, 32.0, [-2.0, -1.5, -1.0, -0.5, 0.0, 0.5, 1.0, 1.5, 2.0], keep)
    contours.sort(key=lambda pl: -len(pl))
    budget, kept = 4500, []
    for pl in contours:
        if budget < len(pl):
            continue
        budget -= len(pl)
        kept.append([[x, y] for x, y in pl])

    return {"version": LAYOUT_VERSION, "generator": version, "seed": seed, "w": W, "h": H, "name": city_name(rng),
            "footprint": foot, "water": water, "nodes": node_list, "roads": roads, "districts": districts, "blocks": blist,
            "parcels": plist, "landmarks": lmk, "terrain": {"contours": kept}}


# =====================================================================================================================
#                                                    validation
# =====================================================================================================================
def _check(cond, msg: str):
    if not cond:
        raise LayoutError(msg)


def _pt_ok(p, w, h, margin=0.0) -> bool:
    return (isinstance(p, (list, tuple)) and len(p) == 2 and all(isinstance(v, (int, float)) and not isinstance(v, bool) and math.isfinite(v) for v in p)
            and -margin <= p[0] <= w + margin and -margin <= p[1] <= h + margin)


def validate(layout: dict) -> dict:
    """Raise LayoutError unless `layout` is a complete, consistent, serializable city. Returns the layout untouched."""
    _check(isinstance(layout, dict), "layout must be a mapping")
    for k in ("version", "generator", "seed", "w", "h", "name", "footprint", "water", "nodes", "roads", "districts", "blocks", "parcels", "landmarks", "terrain"):
        _check(k in layout, f"missing {k}")
    _check(layout["version"] == LAYOUT_VERSION, f"unsupported layout version {layout['version']!r}")
    w, h = layout["w"], layout["h"]
    _check(all(isinstance(v, int) and not isinstance(v, bool) and 0 < v <= MAX_SIZE for v in (w, h)), "bad dimensions")
    _check(isinstance(layout["name"], dict) and all(isinstance(layout["name"].get(l), str) and layout["name"][l] for l in ("fa", "en")), "bad name")
    foot = layout["footprint"]
    _check(len(foot) >= 8 and all(_pt_ok(p, w, h) for p in foot) and G.area(foot) > 1000, "bad footprint")

    # water
    water = layout["water"]
    _check(isinstance(water, list) and len({x["id"] for x in water}) == len(water), "water needs unique ids")
    for x in water:
        _check(len(x["pts"]) >= 2 and len(x["hw"]) == len(x["pts"]) and all(_pt_ok(p, w, h, 240) for p in x["pts"]) and all(v > 0 for v in x["hw"]), f"bad water {x.get('id')}")
        _check(len(x["poly"]) >= 4 and all(_pt_ok(p, w, h, 300) for p in x["poly"]), f"bad water outline {x['id']}")

    # roads and their junctions
    roads = layout["roads"]
    _check(roads and len({r["id"] for r in roads}) == len(roads), "roads need unique ids")
    node_set = set()
    for n in layout["nodes"]:
        _check(_pt_ok(n, w, h), "bad node")
        node_set.add((n[0], n[1]))
    _check(len(node_set) == len(layout["nodes"]), "duplicate nodes")
    adj: dict = {}
    for r in roads:
        _check(r["kind"] in ROAD_KINDS and isinstance(r.get("bridge"), bool) and len(r["pts"]) >= 2 and all(_pt_ok(p, w, h) for p in r["pts"]), f"bad road {r.get('id')}")
        a, b = (r["pts"][0][0], r["pts"][0][1]), (r["pts"][-1][0], r["pts"][-1][1])
        _check(a in node_set and b in node_set, f"road {r['id']} does not end at a junction")
        adj.setdefault(a, set()).add(b)
        adj.setdefault(b, set()).add(a)
    _check(any(r["kind"] == "main" for r in roads), "no main road")
    start = next(iter(adj))
    seen, todo = {start}, deque([start])
    while todo:
        n = todo.popleft()
        for m in adj[n]:
            if m not in seen:
                seen.add(m)
                todo.append(m)
    _check(seen == set(adj), "road network is not connected")
    ends = {}
    for r in roads:
        for q in (r["pts"][0], r["pts"][-1]):
            ends[(q[0], q[1])] = ends.get((q[0], q[1]), 0) + 1
    rim = [tuple(p) for p in foot] + [tuple(foot[0])]
    for q, n in ends.items():
        _check(n > 1 or G.polyline_dist(rim, q) <= DEAD_END_SLACK, "a street ends in the middle of nowhere")       # roads leave the city or lead somewhere

    # neighbourhoods and blocks
    dist_ = layout["districts"]
    _check(dist_ and len({d["id"] for d in dist_}) == len(dist_), "districts need unique ids")
    for d in dist_:
        _check(d["kind"] in DISTRICTS and len(d["poly"]) >= 3 and G.area(d["poly"]) > 1000 and _pt_ok(d["label"], w, h), f"bad district {d.get('id')}")
    _check({d["kind"] for d in dist_} == set(DISTRICTS), "every district kind must exist")
    dkind = {d["id"]: d["kind"] for d in dist_}
    blocks = {b["id"]: b for b in layout["blocks"]}
    _check(blocks and len(blocks) == len(layout["blocks"]), "blocks need unique ids")
    for b in blocks.values():
        _check(b["district"] in dkind and b["use"] in ("lots", "green", "campus") and len(b["poly"]) >= 3 and all(_pt_ok(p, w, h) for p in b["poly"]), f"bad block {b['id']}")

    # parcels
    parcels = layout["parcels"]
    _check(parcels and len({p["id"] for p in parcels}) == len(parcels), "parcels need unique ids")
    cell = 70.0
    grid: dict = {}
    boxes = {}
    for p in parcels:
        _check(p["block"] in blocks and p["zone"] == dkind[blocks[p["block"]]["district"]] and p["use"] in USES, f"bad parcel {p['id']}")
        _check(len(p["poly"]) >= 3 and all(_pt_ok(q, w, h) for q in p["poly"]) and G.area(p["poly"]) > 0 and _pt_ok(p["c"], w, h) and p["area"] > 0, f"parcel {p['id']} has a bad shape")
        bx = G.bbox(p["poly"])
        boxes[p["id"]] = bx
        for gx in range(int(bx[0] // cell), int(bx[2] // cell) + 1):
            for gy in range(int(bx[1] // cell), int(bx[3] // cell) + 1):
                grid.setdefault((gx, gy), []).append(p)
    pairs = set()
    for lst in grid.values():
        for i in range(len(lst)):
            for j in range(i + 1, len(lst)):
                a, b = lst[i], lst[j]
                key = (a["id"], b["id"])
                if key in pairs:
                    continue
                pairs.add(key)
                _check(not G.polys_overlap(a["poly"], b["poly"], 0.02), f"parcels {a['id']} and {b['id']} overlap")
    # lots stay clear of roads and of the water
    seg_grid: dict = {}
    for r in roads:
        half = ROAD_HALF[r["kind"]]
        for a, b in zip(r["pts"], r["pts"][1:]):
            for gx in range(int(min(a[0], b[0]) // cell), int(max(a[0], b[0]) // cell) + 1):
                for gy in range(int(min(a[1], b[1]) // cell), int(max(a[1], b[1]) // cell) + 1):
                    seg_grid.setdefault((gx, gy), []).append((a, b, half))
    wl = [(x["pts"], x["hw"]) for x in water]
    for p in parcels:
        if p["use"] == "green":
            continue
        bx = boxes[p["id"]]
        near = {}
        for gx in range(int((bx[0] - 8) // cell), int((bx[2] + 8) // cell) + 1):
            for gy in range(int((bx[1] - 8) // cell), int((bx[3] + 8) // cell) + 1):
                for a, b, half in seg_grid.get((gx, gy), ()):
                    near[(a[0], a[1], b[0], b[1])] = (a, b, half)
        for a, b, half in near.values():
            _check(G.poly_seg_clearance(p["poly"], a, b) >= half + 0.5, f"parcel {p['id']} touches a road")
        for pts, hws in wl:
            _check(G.polyline_dist(pts, p["c"]) > max(hws) + 4, f"parcel {p['id']} is in the water")
        gx, gy = int(p["c"][0] // cell), int(p["c"][1] // cell)                  # and no lot is cut off from every road
        nearest = min((G.seg_point_dist(a, b, p["c"]) for dx in range(-3, 4) for dy in range(-3, 4) for a, b, _h in seg_grid.get((gx + dx, gy + dy), ())),
                      default=math.inf)
        _check(nearest <= MAX_ROAD_REACH, f"parcel {p['id']} has no road nearby")

    # landmarks and vacancy
    lms = layout["landmarks"]
    pid = {p["id"]: p for p in parcels}
    _check(len({m["id"] for m in lms}) == len(lms), "landmarks need unique ids")
    for m in lms:
        _check(m["kind"] in LANDMARKS and m["parcel"] in pid and pid[m["parcel"]]["use"] == "landmark" and _pt_ok(m["at"], w, h), f"bad landmark {m.get('id')}")
    _check({m["kind"] for m in lms} == set(LANDMARKS), "a city needs all four kinds of public places")
    _check(sum(p["use"] == "landmark" for p in parcels) == len(lms), "every landmark parcel needs a landmark")
    _check(any(p["use"] == "vacant" for p in parcels), "no undeveloped land")
    t = layout["terrain"]
    _check(isinstance(t, dict) and isinstance(t.get("contours"), list) and all(len(c) >= 2 and all(_pt_ok(q, w, h) for q in c) for c in t["contours"]), "bad terrain")
    _check(json.loads(dumps(layout)) == layout, "layout does not survive JSON")
    return layout


def dumps(layout: dict) -> str:
    return json.dumps(layout, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def build(seed: int) -> dict:
    """Generate and validate, trying a handful of derived seeds before giving up (never loops forever)."""
    for i in range(MAX_ATTEMPTS):
        try:
            return validate(generate(seed + i))
        except LayoutError:
            log.warning("world generation attempt %s failed for seed %s", i + 1, seed, exc_info=True)
    raise WorldError("generation_failed")


# =====================================================================================================================
#                                                    persistence
# =====================================================================================================================
def get_world(db: Database, chat_id: int):
    return db.q1("SELECT * FROM worlds WHERE chat_id=?", (chat_id,))


def save_world(db: Database, chat_id: int, layout: dict) -> tuple:
    """Store a finished layout for the chat unless one is already there. Returns (row, created): when two requests race, one wins
    (UNIQUE(chat_id) + INSERT OR IGNORE) and both get the same saved world back."""
    from . import worldplaces                                          # imported here: worldplaces itself builds on this module
    with db.tx():
        cur = db.x("INSERT OR IGNORE INTO worlds(chat_id,seed,gen_version,name_fa,name_en,cols,rows,layout,created_at) VALUES(?,?,?,?,?,?,?,?,?)",
                   (chat_id, layout["seed"], layout["generator"], layout["name"]["fa"], layout["name"]["en"], layout["w"], layout["h"], dumps(layout), core.now()))
        row = get_world(db, chat_id)
        if cur.rowcount == 1:                                           # a new city gets its permanent public places (casino, McDonald's) in the same transaction
            worldplaces.ensure_places(db, row, layout)
        return row, cur.rowcount == 1


def ensure_world(db: Database, chat_id: int) -> tuple:
    """The group's world, created on first use. (row, created). One per chat: enforced by UNIQUE(chat_id).
    The (slow) generation happens before the write transaction, so the database is never locked while a city is being drawn up."""
    w = get_world(db, chat_id)
    if w:
        return w, False
    layout = build(secrets.randbits(48))                             # never touches the module-level `random`
    return save_world(db, chat_id, layout)


_locks: dict = {}


async def aensure_world(db: Database, chat_id: int) -> tuple:
    """`ensure_world` for the event loop: the slow, pure part (drawing up a city) runs in a worker thread, the database is only ever
    touched here on the loop's thread, and one chat never builds two cities at once."""
    w = get_world(db, chat_id)
    if w:
        return w, False
    lock = _locks.setdefault(chat_id, asyncio.Lock())
    try:
        async with lock:
            w = get_world(db, chat_id)
            if w:
                return w, False
            layout = await asyncio.to_thread(build, secrets.randbits(48))
            return save_world(db, chat_id, layout)
    finally:
        if not lock.locked():
            _locks.pop(chat_id, None)


async def aload_layout(world) -> dict:
    """`load_layout` in a worker thread (parsing and checking a whole city is pure work)."""
    return await asyncio.to_thread(load_layout, world)


def load_layout(world) -> dict:
    """The saved layout, validated. A broken row raises WorldError('corrupt') and is NEVER silently replaced."""
    try:
        layout = json.loads(world["layout"])
        validate(layout)
        if layout["seed"] != world["seed"] or layout["w"] != world["cols"] or layout["h"] != world["rows"]:
            raise LayoutError("row metadata does not match the layout")
        return layout
    except (TypeError, ValueError, KeyError, IndexError, AttributeError) as e:
        log.error("world %s of chat %s is corrupt: %s", world["id"], world["chat_id"], e)
        raise WorldError("corrupt") from e


def stats(layout: dict) -> dict:
    uses = [p["use"] for p in layout["parcels"]]
    lots = uses.count("vacant") + uses.count("building") + uses.count("landmark")
    return {"parcels": lots, "vacant": uses.count("vacant"), "built": uses.count("building"), "green": uses.count("green"),
            "blocks": len(layout["blocks"]), "landmarks": len(layout["landmarks"]), "roads": len(layout["roads"])}
