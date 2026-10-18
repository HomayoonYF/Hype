"""Plane geometry for the city generator (World Engine v2). Pure functions: no database, no Telegram, no global random state.

Everything the generator builds is straight-edged in a "flat" plane (convex Voronoi neighbourhoods, street chords, lots),
so exact clipping and an exact planar graph are possible. A smooth displacement field (`Warp`) then bends the whole plane at
once, which is what makes the finished streets curve gently without ever breaking the topology that was built in flat space.
"""
from __future__ import annotations

import math
import random

Point = tuple


# --- small vector helpers -------------------------------------------------------------------------------------------
def dist(a, b) -> float:
    return math.hypot(a[0] - b[0], a[1] - b[1])


def area(poly) -> float:
    """Signed area (counter-clockwise polygons are positive)."""
    s = 0.0
    n = len(poly)
    for i in range(n):
        x1, y1 = poly[i]
        x2, y2 = poly[(i + 1) % n]
        s += x1 * y2 - x2 * y1
    return s / 2.0


def centroid(poly) -> Point:
    a = area(poly)
    if abs(a) < 1e-9:
        return (sum(p[0] for p in poly) / len(poly), sum(p[1] for p in poly) / len(poly))
    cx = cy = 0.0
    n = len(poly)
    for i in range(n):
        x1, y1 = poly[i]
        x2, y2 = poly[(i + 1) % n]
        f = x1 * y2 - x2 * y1
        cx += (x1 + x2) * f
        cy += (y1 + y2) * f
    return (cx / (6 * a), cy / (6 * a))


def bbox(poly):
    xs = [p[0] for p in poly]
    ys = [p[1] for p in poly]
    return min(xs), min(ys), max(xs), max(ys)


def point_in_poly(pt, poly) -> bool:
    x, y = pt
    inside = False
    n = len(poly)
    j = n - 1
    for i in range(n):
        xi, yi = poly[i]
        xj, yj = poly[j]
        if (yi > y) != (yj > y) and x < (xj - xi) * (y - yi) / (yj - yi) + xi:
            inside = not inside
        j = i
    return inside


def is_convex(poly, tol: float = 1e-6) -> bool:
    n = len(poly)
    sign = 0
    for i in range(n):
        ax, ay = poly[i]
        bx, by = poly[(i + 1) % n]
        cx, cy = poly[(i + 2) % n]
        cr = (bx - ax) * (cy - by) - (by - ay) * (cx - bx)
        if abs(cr) <= tol:
            continue
        s = 1 if cr > 0 else -1
        if sign and s != sign:
            return False
        sign = s
    return True


def seg_point_dist(a, b, p) -> float:
    dx, dy = b[0] - a[0], b[1] - a[1]
    L = dx * dx + dy * dy
    if L < 1e-12:
        return dist(a, p)
    t = max(0.0, min(1.0, ((p[0] - a[0]) * dx + (p[1] - a[1]) * dy) / L))
    return math.hypot(p[0] - (a[0] + t * dx), p[1] - (a[1] + t * dy))


def polyline_dist(pts, p) -> float:
    return min(seg_point_dist(pts[i], pts[i + 1], p) for i in range(len(pts) - 1))


def seg_cross(a, b, c, d) -> bool:
    """Do the open segments ab and cd properly cross?"""
    def o(p, q, r):
        return (q[0] - p[0]) * (r[1] - p[1]) - (q[1] - p[1]) * (r[0] - p[0])
    o1, o2, o3, o4 = o(a, b, c), o(a, b, d), o(c, d, a), o(c, d, b)
    return (o1 > 1e-9 and o2 < -1e-9 or o1 < -1e-9 and o2 > 1e-9) and (o3 > 1e-9 and o4 < -1e-9 or o3 < -1e-9 and o4 > 1e-9)


def polys_overlap(p, q, shrink: float = 0.0) -> bool:
    """Do two simple polygons share interior area? (touching edges do not count.) Convex or not."""
    if shrink:
        cp, cq = centroid(p), centroid(q)
        p = [(cp[0] + (x - cp[0]) * (1 - shrink), cp[1] + (y - cp[1]) * (1 - shrink)) for x, y in p]
        q = [(cq[0] + (x - cq[0]) * (1 - shrink), cq[1] + (y - cq[1]) * (1 - shrink)) for x, y in q]
    pb, qb = bbox(p), bbox(q)
    if pb[2] <= qb[0] or qb[2] <= pb[0] or pb[3] <= qb[1] or qb[3] <= pb[1]:
        return False
    np_, nq = len(p), len(q)
    for i in range(np_):
        for j in range(nq):
            if seg_cross(p[i], p[(i + 1) % np_], q[j], q[(j + 1) % nq]):
                return True
    cq, cp = centroid(q), centroid(p)
    return point_in_poly(cq, p) or point_in_poly(cp, q) or point_in_poly(p[0], q) or point_in_poly(q[0], p)


def poly_seg_clearance(poly, a, b) -> float:
    """Smallest distance between the polygon (as a filled shape) and the segment ab; 0 when they touch or overlap."""
    n = len(poly)
    for i in range(n):
        if seg_cross(poly[i], poly[(i + 1) % n], a, b):
            return 0.0
    if point_in_poly(a, poly) or point_in_poly(b, poly):
        return 0.0
    best = min(min(seg_point_dist(a, b, poly[i]) for i in range(n)),
               min(seg_point_dist(poly[i], poly[(i + 1) % n], a) for i in range(n)),
               min(seg_point_dist(poly[i], poly[(i + 1) % n], b) for i in range(n)))
    return best


# --- convex clipping ------------------------------------------------------------------------------------------------
def clip_halfplane(poly, a, b, c):
    """Keep the part of a polygon where a*x + b*y + c >= 0 (Sutherland-Hodgman; exact for convex polygons)."""
    out = []
    n = len(poly)
    for i in range(n):
        p, q = poly[i], poly[(i + 1) % n]
        dp, dq = a * p[0] + b * p[1] + c, a * q[0] + b * q[1] + c
        if dp >= 0:
            out.append(p)
        if (dp >= 0) != (dq >= 0):
            t = dp / (dp - dq)
            out.append((p[0] + t * (q[0] - p[0]), p[1] + t * (q[1] - p[1])))
    return out


def clip_tagged(pts, tags, a, b, c, new_tag):
    """Like clip_halfplane, but every edge keeps a tag (tags[i] belongs to the edge pts[i] -> pts[i+1]); the edge created on
    the clip line gets `new_tag`. Used to know which cell border belongs to which neighbour."""
    out_p, out_t = [], []
    n = len(pts)
    d = [a * x + b * y + c for x, y in pts]
    for i in range(n):
        j = (i + 1) % n
        pi, pj, di, dj = pts[i], pts[j], d[i], d[j]
        if di >= 0:
            out_p.append(pi)
            out_t.append(tags[i])
            if dj < 0:
                t = di / (di - dj)
                out_p.append((pi[0] + t * (pj[0] - pi[0]), pi[1] + t * (pj[1] - pi[1])))
                out_t.append(new_tag)
        elif dj >= 0:
            t = di / (di - dj)
            out_p.append((pi[0] + t * (pj[0] - pi[0]), pi[1] + t * (pj[1] - pi[1])))
            out_t.append(tags[i])
    return out_p, out_t


def inset_convex(poly, d: float):
    """Shrink a convex counter-clockwise polygon by d on every side. None when nothing is left."""
    out = list(poly)
    n = len(poly)
    for i in range(n):
        a, b = poly[i], poly[(i + 1) % n]
        dx, dy = b[0] - a[0], b[1] - a[1]
        L = math.hypot(dx, dy)
        if L < 1e-9:
            continue
        nx, ny = -dy / L, dx / L                                  # left normal = inside for counter-clockwise polygons
        out = clip_halfplane(out, nx, ny, -(nx * a[0] + ny * a[1]) - d)
        if len(out) < 3:
            return None
    return out if area(out) > 1.0 else None


def chord(poly, nx: float, ny: float, d: float):
    """The part of the line nx*x + ny*y = d (unit normal) inside a convex counter-clockwise polygon, as (p, q), or None."""
    px, py = nx * d, ny * d
    dx, dy = -ny, nx
    lo, hi = -1e9, 1e9
    n = len(poly)
    for i in range(n):
        a, b = poly[i], poly[(i + 1) % n]
        ex, ey = b[0] - a[0], b[1] - a[1]
        # inside: cross(e, X - a) >= 0 with X = P + t*dir
        num = ex * (py - a[1]) - ey * (px - a[0])
        den = ex * dy - ey * dx
        if abs(den) < 1e-12:
            if num < 0:
                return None
            continue
        t = -num / den
        if den > 0:
            lo = max(lo, t)
        else:
            hi = min(hi, t)
        if lo > hi:
            return None
    if hi - lo < 1e-6:
        return None
    return (px + lo * dx, py + lo * dy), (px + hi * dx, py + hi * dy)


def simplify_closed(poly, tol: float):
    """Douglas-Peucker for a closed polygon: drops vertices that lie within `tol` of the straight line between their neighbours."""
    n = len(poly)
    if n <= 3:
        return list(poly)
    far = max(range(n), key=lambda i: dist(poly[0], poly[i]))

    def rec(i, j, out):
        # points strictly between i and j (indices modulo n, walking forward)
        k, best, bi = (i + 1) % n, -1.0, -1
        while k != j:
            d = seg_point_dist(poly[i], poly[j], poly[k])
            if d > best:
                best, bi = d, k
            k = (k + 1) % n
        if bi >= 0 and best > tol:
            rec(i, bi, out)
            out.append(bi)
            rec(bi, j, out)
    keep = [0]
    rec(0, far, keep)
    keep.append(far)
    rec(far, 0, keep)
    return [poly[i] for i in keep]


def clip_segment(poly, p, q):
    """The part of segment pq inside a convex counter-clockwise polygon, or None."""
    lo, hi = 0.0, 1.0
    dx, dy = q[0] - p[0], q[1] - p[1]
    n = len(poly)
    for i in range(n):
        a, b = poly[i], poly[(i + 1) % n]
        ex, ey = b[0] - a[0], b[1] - a[1]
        num = ex * (p[1] - a[1]) - ey * (p[0] - a[0])            # inside when num + t*den >= 0
        den = ex * dy - ey * dx
        if abs(den) < 1e-12:
            if num < 0:
                return None
            continue
        t = -num / den
        if den > 0:
            lo = max(lo, t)
        else:
            hi = min(hi, t)
        if lo >= hi:
            return None
    return (p[0] + lo * dx, p[1] + lo * dy), (p[0] + hi * dx, p[1] + hi * dy)


def poly_inradius_hint(poly) -> float:
    """A cheap 'how thick is this shape' number: 2 * area / perimeter."""
    per = sum(dist(poly[i], poly[(i + 1) % len(poly)]) for i in range(len(poly)))
    return 2 * abs(area(poly)) / per if per else 0.0


def pole_of_poly(poly, step: float = 12.0) -> Point:
    """The point of a polygon farthest from its border (a good spot for a label), found on a coarse grid."""
    x0, y0, x1, y1 = bbox(poly)
    best, best_d = centroid(poly), -1.0
    n = len(poly)
    y = y0 + step / 2
    while y < y1:
        x = x0 + step / 2
        while x < x1:
            if point_in_poly((x, y), poly):
                d = min(seg_point_dist(poly[i], poly[(i + 1) % n], (x, y)) for i in range(n))
                if d > best_d:
                    best, best_d = (x, y), d
            x += step
        y += step
    return best


# --- smooth warp ----------------------------------------------------------------------------------------------------
class Warp:
    """A smooth, bounded displacement field: sums of a few sine waves per axis (no tables, fully determined by the rng)."""

    def __init__(self, rng: random.Random, octaves=((700.0, 30.0), (330.0, 10.0), (150.0, 3.2))):
        self.gain = 1.0
        self.waves = []
        for wavelength, amp in octaves:
            for _ in range(2):
                th = rng.uniform(0, math.pi * 2)
                k = 2 * math.pi / wavelength * rng.uniform(0.8, 1.25)
                self.waves.append((k * math.cos(th), k * math.sin(th), rng.uniform(0, math.tau), amp * rng.uniform(0.6, 1.0) * 0.7,
                                   rng.uniform(0, math.tau)))

    def __call__(self, x: float, y: float) -> Point:
        dx = dy = 0.0
        for kx, ky, ph, amp, ang in self.waves:
            s = amp * math.sin(kx * x + ky * y + ph)
            dx += s * math.cos(ang)
            dy += s * math.sin(ang)
        return (x + dx * self.gain, y + dy * self.gain)

    def relax(self, w: float, h: float, floor: float) -> bool:
        """Weaken the field step by step until it no longer folds the map over itself; False if even a faint field does."""
        for g in (1.0, 0.85, 0.7, 0.55, 0.4, 0.3):
            self.gain = g
            if self.min_jacobian(w, h) >= floor:
                return True
        return False

    def min_jacobian(self, w: float, h: float, step: float = 40.0) -> float:
        """Weakest stretch of the warp over the canvas (its smallest singular value, negative where the map would fold over itself).
        Distances never shrink by more than this factor, so road clearances measured before the warp still hold after it."""
        best = 9.0
        e = 1.0
        y = 0.0
        while y <= h:
            x = 0.0
            while x <= w:
                a = self(x + e, y)
                b = self(x - e, y)
                c = self(x, y + e)
                d = self(x, y - e)
                j11, j21 = (a[0] - b[0]) / (2 * e), (a[1] - b[1]) / (2 * e)
                j12, j22 = (c[0] - d[0]) / (2 * e), (c[1] - d[1]) / (2 * e)
                det = j11 * j22 - j12 * j21
                tr = j11 * j11 + j12 * j12 + j21 * j21 + j22 * j22
                lam = (tr - math.sqrt(max(0.0, tr * tr - 4 * det * det))) / 2
                sv = math.sqrt(max(0.0, lam))
                best = min(best, sv if det > 0 else -sv - 1e-9)
                x += step
            y += step
        return best


def densify(a, b, max_len: float):
    """Points strictly between a and b so that no piece is longer than max_len (the same points for ab and ba)."""
    n = max(1, int(math.ceil(dist(a, b) / max_len)))
    return [(a[0] + (b[0] - a[0]) * i / n, a[1] + (b[1] - a[1]) * i / n) for i in range(1, n)]


# --- planar arrangement -----------------------------------------------------------------------------------------------
class Arrangement:
    """Turns a pile of straight segments into a planar graph: every crossing and every T-junction becomes a node.

    `add(p, q, kind)` collects segments; `build()` splits them at all intersections and merges coincident points.
    Afterwards `nodes` is a list of points, `edges` maps (a, b) with a < b to {"kind": ..., "tags": set}.
    """

    SNAP = 0.02

    def __init__(self):
        self.segs: list = []
        self.nodes: list = []
        self._grid: dict = {}
        self.edges: dict = {}

    # nodes ------------------------------------------------------------------------------------------------------------
    def node(self, p) -> int:
        gx, gy = int(math.floor(p[0] / 0.1)), int(math.floor(p[1] / 0.1))
        for ix in (gx - 1, gx, gx + 1):
            for iy in (gy - 1, gy, gy + 1):
                for i in self._grid.get((ix, iy), ()):
                    q = self.nodes[i]
                    if abs(q[0] - p[0]) <= self.SNAP and abs(q[1] - p[1]) <= self.SNAP:
                        return i
        self.nodes.append((p[0], p[1]))
        self._grid.setdefault((gx, gy), []).append(len(self.nodes) - 1)
        return len(self.nodes) - 1

    def add(self, p, q, kind: str = "street", tag: str = "") -> None:
        """kind: "main" | "street" | "limit" (the city's outline, not a road); tag: "" | "bank" | "ring"."""
        if dist(p, q) > 1e-6:
            self.segs.append((p, q, kind, tag))

    # build ------------------------------------------------------------------------------------------------------------
    def build(self) -> None:
        segs = self.segs
        cell = 90.0
        index: dict = {}
        for i, (p, q, _k, _b) in enumerate(segs):
            x0, x1 = sorted((p[0], q[0]))
            y0, y1 = sorted((p[1], q[1]))
            for gx in range(int(x0 // cell), int(x1 // cell) + 1):
                for gy in range(int(y0 // cell), int(y1 // cell) + 1):
                    index.setdefault((gx, gy), []).append(i)
        splits: list = [[] for _ in segs]                       # per segment: (t, point)
        done = set()
        tol = self.SNAP
        for lst in index.values():
            for ii in range(len(lst)):
                for jj in range(ii + 1, len(lst)):
                    i, j = lst[ii], lst[jj]
                    if (i, j) in done:
                        continue
                    done.add((i, j))
                    p, q = segs[i][0], segs[i][1]
                    r, s = segs[j][0], segs[j][1]
                    rx, ry, sx, sy = q[0] - p[0], q[1] - p[1], s[0] - r[0], s[1] - r[1]
                    den = rx * sy - ry * sx
                    la, lb = math.hypot(rx, ry), math.hypot(sx, sy)
                    if abs(den) < 1e-9 * la * lb:
                        continue                                  # parallel: the generator never builds overlapping collinear segments
                    qpx, qpy = r[0] - p[0], r[1] - p[1]
                    t = (qpx * sy - qpy * sx) / den
                    u = (qpx * ry - qpy * rx) / den
                    ta, tb = tol / la, tol / lb
                    if t < -ta or t > 1 + ta or u < -tb or u > 1 + tb:
                        continue
                    x = (p[0] + t * rx, p[1] + t * ry)
                    if t <= ta:
                        x = p
                    elif t >= 1 - ta:
                        x = q
                    elif u <= tb:
                        x = r
                    elif u >= 1 - tb:
                        x = s
                    if ta < t < 1 - ta:
                        splits[i].append((t, x))
                    if tb < u < 1 - tb:
                        splits[j].append((u, x))
        rank = {"main": 3, "street": 2, "limit": 1}
        for i, (p, q, kind, tag) in enumerate(segs):
            chain = [(0.0, p)] + sorted(splits[i], key=lambda z: z[0]) + [(1.0, q)]
            ids = [self.node(pt) for _t, pt in chain]
            for a, b in zip(ids, ids[1:]):
                if a == b:
                    continue
                key = (a, b) if a < b else (b, a)
                cur = self.edges.get(key)
                if cur is None:
                    self.edges[key] = {"kind": kind, "tags": {tag} if tag else set()}
                else:                                              # the same piece twice: the more important kind wins
                    if rank[kind] > rank[cur["kind"]]:
                        cur["kind"] = kind
                    if tag:
                        cur["tags"].add(tag)

    # graph queries ----------------------------------------------------------------------------------------------------
    def adjacency(self, roads_only: bool = False) -> dict:
        adj: dict = {}
        for (a, b), e in self.edges.items():
            if roads_only and e["kind"] == "limit":
                continue
            adj.setdefault(a, set()).add(b)
            adj.setdefault(b, set()).add(a)
        return adj

    def prune_dead_ends(self, keep=frozenset()) -> int:
        """Drop streets that lead nowhere (a node with a single road), repeatedly. `keep` nodes (the city's exits) may end a street.
        Only roads count: the outline is never a road. Returns how many edges were removed."""
        removed = 0
        adj = self.adjacency(roads_only=True)
        todo = [n for n, s in adj.items() if len(s) == 1 and n not in keep]
        while todo:
            n = todo.pop()
            if n not in adj or len(adj[n]) != 1:
                continue
            (m,) = adj[n]
            key = (n, m) if n < m else (m, n)
            self.edges.pop(key, None)
            removed += 1
            adj[m].discard(n)
            del adj[n]
            if len(adj[m]) == 1 and m not in keep:
                todo.append(m)
            elif not adj[m]:
                del adj[m]
        return removed

    def components(self) -> list:
        adj = self.adjacency(roads_only=True)
        seen, comps = set(), []
        for s in sorted(adj):
            if s in seen:
                continue
            comp, stack = [], [s]
            seen.add(s)
            while stack:
                n = stack.pop()
                comp.append(n)
                for m in adj[n]:
                    if m not in seen:
                        seen.add(m)
                        stack.append(m)
            comps.append(comp)
        return comps

    def keep_largest_component(self) -> None:
        comps = self.components()
        if len(comps) <= 1:
            return
        best = set(max(comps, key=len))
        self.edges = {k: v for k, v in self.edges.items() if v["kind"] == "limit" or k[0] in best}

    def faces(self) -> list:
        """All bounded faces as lists of node ids (counter-clockwise), found by walking half-edges. The outer face is dropped."""
        adj = self.adjacency()
        order = {}
        for n, ms in adj.items():
            px, py = self.nodes[n]
            order[n] = sorted(ms, key=lambda m: math.atan2(self.nodes[m][1] - py, self.nodes[m][0] - px))
        seen = set()
        out = []
        for a, b in sorted(self.edges):
            for u, v in ((a, b), (b, a)):
                if (u, v) in seen:
                    continue
                face, cu, cv = [], u, v
                while (cu, cv) not in seen:
                    seen.add((cu, cv))
                    face.append(cu)
                    ring = order[cv]
                    k = ring.index(cu)
                    nxt = ring[k - 1]                              # next edge clockwise from the one we came in on: the face stays on our left
                    cu, cv = cv, nxt
                pts = [self.nodes[i] for i in face]
                if len(face) >= 3 and area(pts) > 1.0:
                    out.append(face)
        return out


# --- value-noise style height field (for the faint terrain lines) --------------------------------------------------------
class Height:
    def __init__(self, rng: random.Random):
        self.waves = []
        for wavelength, amp in ((900.0, 1.0), (420.0, 0.55), (210.0, 0.25)):
            for _ in range(3):
                th = rng.uniform(0, math.tau)
                k = 2 * math.pi / wavelength * rng.uniform(0.85, 1.2)
                self.waves.append((k * math.cos(th), k * math.sin(th), rng.uniform(0, math.tau), amp * rng.uniform(0.6, 1.0)))

    def __call__(self, x: float, y: float) -> float:
        return sum(a * math.sin(kx * x + ky * y + ph) for kx, ky, ph, a in self.waves)


def contour_polylines(fn, w: float, h: float, step: float, levels, keep) -> list:
    """Marching squares on fn over [0,w]x[0,h]. `keep(x, y)` filters out places that must stay empty. Returns polylines."""
    nx, ny = int(w // step) + 1, int(h // step) + 1
    vals = [[fn(i * step, j * step) for i in range(nx)] for j in range(ny)]
    out = []
    for lv in levels:
        segs = {}

        def pt(i0, j0, i1, j1):
            v0, v1 = vals[j0][i0], vals[j1][i1]
            t = (lv - v0) / (v1 - v0) if v1 != v0 else 0.5
            return (round((i0 + (i1 - i0) * t) * step, 1), round((j0 + (j1 - j0) * t) * step, 1))
        pairs = []
        for j in range(ny - 1):
            for i in range(nx - 1):
                c = [vals[j][i] > lv, vals[j][i + 1] > lv, vals[j + 1][i + 1] > lv, vals[j + 1][i] > lv]
                code = c[0] * 8 + c[1] * 4 + c[2] * 2 + c[3]
                if code in (0, 15):
                    continue
                top, right, bottom, left = pt(i, j, i + 1, j), pt(i + 1, j, i + 1, j + 1), pt(i, j + 1, i + 1, j + 1), pt(i, j, i, j + 1)
                table = {1: [(left, bottom)], 2: [(bottom, right)], 3: [(left, right)], 4: [(top, right)], 5: [(top, right), (left, bottom)],
                         6: [(top, bottom)], 7: [(left, top)], 8: [(left, top)], 9: [(top, bottom)], 10: [(left, top), (bottom, right)],
                         11: [(top, right)], 12: [(left, right)], 13: [(bottom, right)], 14: [(left, bottom)]}
                for a, b in table[code]:
                    pairs.append((a, b))
        # stitch the little segments into polylines
        ends: dict = {}
        for k, (a, b) in enumerate(pairs):
            ends.setdefault(a, []).append(k)
            ends.setdefault(b, []).append(k)
        used = [False] * len(pairs)
        for k0 in range(len(pairs)):
            if used[k0]:
                continue
            used[k0] = True
            line = [pairs[k0][0], pairs[k0][1]]
            for direction in (0, 1):
                while True:
                    tip = line[-1] if direction == 0 else line[0]
                    nxt = next((k for k in ends.get(tip, ()) if not used[k]), None)
                    if nxt is None:
                        break
                    used[nxt] = True
                    a, b = pairs[nxt]
                    other = b if a == tip else a
                    if direction == 0:
                        line.append(other)
                    else:
                        line.insert(0, other)
            run = []
            for p in line:
                if keep(*p):
                    run.append(p)
                else:
                    if len(run) >= 3:
                        out.append(run)
                    run = []
            if len(run) >= 3:
                out.append(run)
    return out
