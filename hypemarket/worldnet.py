"""The walkable road network of a saved city, and deterministic routing on it.

The graph is read straight from the layout the world engine already stores (`roads`: polylines that start and end at junctions; a road with
`bridge: true` is the only thing that crosses the river). Nothing is inferred from a picture and nothing here writes anywhere: the same layout,
origin and destination always give the same route, whatever the language, the colours or the restart in between.

* Every polyline vertex is a graph node (junction vertices are shared by the roads that meet there), every polyline step is an edge whose
  weight is its length in map units. A walker therefore only ever stands on a road and only ever moves along road polylines; a river is
  crossed solely along a bridge road.
* A position is any point on a road. For routing, the origin and the destination are snapped onto their nearest road segment and become
  temporary nodes of that segment, so a route may start and end in the middle of a road.
* Routing is Dijkstra with a binary heap; equal-cost ties resolve by node number, which is fixed by the layout, so results are stable.
"""
from __future__ import annotations

import heapq
import math
from dataclasses import dataclass

from . import worldgeom as G

CELL = 48.0                 # side of the spatial-index cell used to find the road nearest to a point
INF = math.inf


@dataclass(frozen=True)
class Seg:
    a: int                  # node ids of the two ends
    b: int
    length: float
    road: str               # id of the road this step belongs to
    bridge: bool
    kind: str               # "main" | "street"


@dataclass(frozen=True)
class Snap:
    seg: int                # index into RoadNet.segs
    t: float                # 0..1 from the segment's `a` to its `b`
    pt: tuple               # the point on the road
    off: float              # how far the queried point was from the road


@dataclass(frozen=True)
class Route:
    pts: tuple              # ((x, y), ...) from the origin along roads to the destination
    length: float           # total length in map units


def _lerp(p, q, t):
    return (p[0] + (q[0] - p[0]) * t, p[1] + (q[1] - p[1]) * t)


def path_length(pts) -> float:
    return sum(G.dist(a, b) for a, b in zip(pts, pts[1:]))


def point_along(pts, d: float) -> tuple:
    """The point `d` map units along a polyline from its start (clamped to its two ends)."""
    if not pts:
        raise ValueError("empty path")
    if d <= 0 or len(pts) == 1:
        return tuple(pts[0])
    for a, b in zip(pts, pts[1:]):
        L = G.dist(a, b)
        if d <= L:
            return tuple(_lerp(a, b, d / L if L else 0.0))
        d -= L
    return tuple(pts[-1])


def split_along(pts, d: float) -> tuple:
    """(walked, remaining): the polyline cut `d` units from its start; both pieces contain the cut point."""
    here = point_along(pts, d)
    walked, left, acc = [tuple(pts[0])], None, 0.0
    for i, (a, b) in enumerate(zip(pts, pts[1:])):
        L = G.dist(a, b)
        if acc + L >= d - 1e-9:
            walked.append(here)
            left = [here] + [tuple(q) for q in pts[i + 1:]]
            break
        walked.append(tuple(b))
        acc += L
    if left is None:
        left = [here]
    return walked, left


class RoadNet:
    """Immutable routing graph of one saved layout."""

    def __init__(self, layout: dict):
        self.index: dict = {}
        self.pts: list = []
        self.adj: list = []
        self.segs: list = []
        for r in layout["roads"]:
            ids = [self._node((float(p[0]), float(p[1]))) for p in r["pts"]]
            for a, b in zip(ids, ids[1:]):
                if a == b:
                    continue
                L = G.dist(self.pts[a], self.pts[b])
                if L <= 1e-9:
                    continue
                self.segs.append(Seg(a, b, L, r["id"], bool(r["bridge"]), r["kind"]))
                self.adj[a].append((b, L))
                self.adj[b].append((a, L))
        self.n = len(self.pts)
        self.w, self.h = layout["w"], layout["h"]
        self.land = [True] * self.n                                     # False for every node a bridge touches: nobody spawns on the water
        for s in self.segs:
            if s.bridge:
                self.land[s.a] = self.land[s.b] = False
        self._grid: dict = {}
        for i, s in enumerate(self.segs):
            (x0, y0), (x1, y1) = self.pts[s.a], self.pts[s.b]
            for gx in range(int(min(x0, x1) // CELL), int(max(x0, x1) // CELL) + 1):
                for gy in range(int(min(y0, y1) // CELL), int(max(y0, y1) // CELL) + 1):
                    self._grid.setdefault((gx, gy), []).append(i)
        self._doors: dict = {}
        self._rings = int(max(self.w, self.h) // CELL) + 3

    def _node(self, p) -> int:
        i = self.index.get(p)
        if i is None:
            i = self.index[p] = len(self.pts)
            self.pts.append(p)
            self.adj.append([])
        return i

    # --- finding the road under / next to a point ----------------------------------------------------------------------
    def snap(self, pt, avoid_bridges: bool = False) -> Snap:
        """The nearest point of the road network to `pt` (ties: lower segment number). With `avoid_bridges` a bridge is only used when no
        other road exists."""
        px, py = float(pt[0]), float(pt[1])
        cx, cy = int(px // CELL), int(py // CELL)
        best = None
        seen: set = set()
        for k in range(self._rings):
            for gx in range(cx - k, cx + k + 1):
                for gy in range(cy - k, cy + k + 1):
                    if max(abs(gx - cx), abs(gy - cy)) != k:
                        continue
                    for i in self._grid.get((gx, gy), ()):
                        if i in seen:
                            continue
                        seen.add(i)
                        s = self.segs[i]
                        if avoid_bridges and s.bridge:
                            continue
                        a, b = self.pts[s.a], self.pts[s.b]
                        dx, dy = b[0] - a[0], b[1] - a[1]
                        t = max(0.0, min(1.0, ((px - a[0]) * dx + (py - a[1]) * dy) / (dx * dx + dy * dy)))
                        q = (a[0] + dx * t, a[1] + dy * t)
                        off = math.hypot(px - q[0], py - q[1])
                        if best is None or off < best.off - 1e-12 or (abs(off - best.off) <= 1e-12 and i < best.seg):
                            best = Snap(i, t, q, off)
            if best is not None and best.off <= k * CELL:
                break
        if best is None:
            if avoid_bridges:
                return self.snap(pt)
            raise ValueError("the city has no roads")
        return best

    def door(self, parcel_id: str, centre) -> Snap:
        """Where a parcel is entered: the point of the nearest non-bridge road to its centre (memoized; a layout never changes)."""
        s = self._doors.get(parcel_id)
        if s is None:
            s = self._doors[parcel_id] = self.snap(centre, avoid_bridges=True)
        return s

    # --- shortest paths ---------------------------------------------------------------------------------------------------
    def _virtual(self, snaps) -> tuple:
        """Temporary nodes for points that lie inside a segment. Returns (their node ids, extra adjacency)."""
        ids, extra, by_seg = [], {}, {}
        for k, s in enumerate(snaps):
            seg = self.segs[s.seg]
            if s.t <= 1e-9:
                ids.append(seg.a)
                continue
            if s.t >= 1 - 1e-9:
                ids.append(seg.b)
                continue
            v = self.n + k
            ids.append(v)
            extra.setdefault(v, []).extend([(seg.a, s.t * seg.length), (seg.b, (1 - s.t) * seg.length)])
            extra.setdefault(seg.a, []).append((v, s.t * seg.length))
            extra.setdefault(seg.b, []).append((v, (1 - s.t) * seg.length))
            by_seg.setdefault(s.seg, []).append((s.t, v))
        for si, lst in by_seg.items():                                  # two temporary nodes on one segment also see each other directly
            L = self.segs[si].length
            for i in range(len(lst)):
                for j in range(i + 1, len(lst)):
                    w = abs(lst[i][0] - lst[j][0]) * L
                    extra[lst[i][1]].append((lst[j][1], w))
                    extra[lst[j][1]].append((lst[i][1], w))
        return ids, extra

    def _dijkstra(self, src: int, extra: dict, targets=None, cutoff: float = INF):
        dist = {src: 0.0}
        prev: dict = {}
        heap = [(0.0, src)]
        todo = set(targets) if targets else None
        while heap:
            d, u = heapq.heappop(heap)
            if d > dist.get(u, INF):
                continue
            if todo is not None:
                todo.discard(u)
                if not todo:
                    break
            for v, w in (self.adj[u] if u < self.n else ()):
                nd = d + w
                if nd < dist.get(v, INF) and nd <= cutoff:
                    dist[v] = nd
                    prev[v] = u
                    heapq.heappush(heap, (nd, v))
            for v, w in extra.get(u, ()):
                nd = d + w
                if nd < dist.get(v, INF) and nd <= cutoff:
                    dist[v] = nd
                    prev[v] = u
                    heapq.heappush(heap, (nd, v))
        return dist, prev

    def route(self, a, b) -> Route | None:
        """The shortest walk along roads from point `a` to point `b` (both are snapped onto the network first). None if unreachable."""
        sa, sb = self.snap(a), self.snap(b)
        snaps = [sa, sb]
        (ia, ib), extra = self._virtual(snaps)
        if ia == ib:
            return Route((sa.pt, sb.pt), G.dist(sa.pt, sb.pt))
        dist, prev = self._dijkstra(ia, extra, targets=[ib])
        if ib not in dist:
            return None
        chain = [ib]
        while chain[-1] != ia:
            chain.append(prev[chain[-1]])
        chain.reverse()
        pts = [sa.pt]
        for node in chain:
            q = self.pts[node] if node < self.n else snaps[node - self.n].pt
            if G.dist(q, pts[-1]) > 1e-9:
                pts.append(q)
        if G.dist(pts[-1], sb.pt) > 1e-9:
            pts.append(sb.pt)
        if len(pts) == 1:
            pts.append(pts[0])
        return Route(tuple(pts), path_length(pts))

    def field(self, origin, cutoff: float = INF) -> "DistField":
        """Road distances from `origin` to everywhere (up to `cutoff`): ask it for the distance to any snapped point."""
        so = self.snap(origin)
        (io,), extra = self._virtual([so])
        dist, _prev = self._dijkstra(io, extra, cutoff=cutoff)
        return DistField(self, so, dist)


class DistField:
    """Road distances from one origin (see RoadNet.field)."""

    def __init__(self, net: RoadNet, origin: Snap, dist: dict):
        self.net, self.origin, self.dist = net, origin, dist

    def to(self, s: Snap) -> float:
        """Road distance from the origin to a snapped point; inf if farther than the field's cutoff."""
        net = self.net
        seg = net.segs[s.seg]
        d = min(self.dist.get(seg.a, INF) + s.t * seg.length, self.dist.get(seg.b, INF) + (1 - s.t) * seg.length)
        if s.seg == self.origin.seg:
            d = min(d, abs(s.t - self.origin.t) * seg.length)
        return d

    def to_point(self, pt, avoid_bridges: bool = False) -> float:
        return self.to(self.net.snap(pt, avoid_bridges))
