"""Where the players of a city are, how they walk, and who is near whom (v0.17).

Everything a player's marker shows comes from three tables: `world_players` (one row per player per city: the last place they stood still),
`world_trips` (a walk: the saved route, its length, the walking speed it was planned with, when it started and when it ends) and the saved
city itself. Nothing is stored about «where the walker is right now»: that is worked out from the route and the clock whenever somebody looks.

* Spawn: the first time a player opens the map in a city they are put on a road near the city's station, on a spot chosen by a hash of
  (the city's seed, their Telegram ID): the same player always gets the same spot in the same city, whatever the stock market, their
  rebirths or the number of other players. The row is written once (INSERT OR IGNORE on the primary key); opening the map again only reads.
* Walking: `start_trip` plans the shortest road route, stores it with started_at / arrive_at = started_at + ceil(length / speed) and returns.
  The player is not moved. While the walk lasts their position is the point of the route that the elapsed share of the duration reaches.
  Arrival is applied lazily by `settle` (the next time that player acts, or anybody looks at the saved row), in one transaction that also
  marks the trip arrived and writes the destination into the player's row. No timers, no background task; a restart loses nothing.
* One walk at a time per player per city: a partial UNIQUE index on (world, player) WHERE state = 'walking'.
* Nearby: other players of the SAME city whose road distance to you is at most the configured radius (straight lines are only used to skip
  the obviously far ones: a road is never shorter than the straight line).
No function here touches money, stocks, XP or anything outside the five map tables.
"""
from __future__ import annotations

import hashlib
import json
import math
import sqlite3
from dataclasses import dataclass

from . import config as C, core, worldgeom as G, worldnet as N

SPAWN_RADIUS = 140.0          # road distance from the station door within which players spawn
MIN_SPAWN_SPOTS = 8


# --- rows ---------------------------------------------------------------------------------------------------------------------
def get_player(db, world_id: int, uid: int):
    return db.q1("SELECT * FROM world_players WHERE world_id=? AND user_id=?", (world_id, uid))


def walking_trip(db, world_id: int, uid: int):
    return db.q1("SELECT * FROM world_trips WHERE world_id=? AND user_id=? AND state='walking'", (world_id, uid))


def route_of(trip) -> list:
    """The saved route as a list of (x, y); raises ValueError if the row is damaged."""
    pts = json.loads(trip["route"])
    if not (isinstance(pts, list) and len(pts) >= 2 and all(isinstance(p, list) and len(p) == 2 and all(isinstance(v, (int, float)) for v in p) for p in pts)):
        raise ValueError("bad route")
    return [(float(p[0]), float(p[1])) for p in pts]


def trip_position(trip, now: int) -> tuple:
    """Where a walker is at `now`, from the saved route and the clock only."""
    pts = route_of(trip)
    span = trip["arrive_at"] - trip["started_at"]
    frac = 0.0 if now <= trip["started_at"] else 1.0 if now >= trip["arrive_at"] else (now - trip["started_at"]) / span
    return N.point_along(pts, frac * trip["length"])


def trip_heading(trip, now: int) -> float:
    """The direction a traveller is going at `now` (radians, 0 = east, map y points down), from the saved route and the clock only; 0.0 when it
    cannot be told (a damaged route, a route of no length)."""
    try:
        pts = route_of(trip)
        span = trip["arrive_at"] - trip["started_at"]
        frac = 0.0 if now <= trip["started_at"] else 1.0 if now >= trip["arrive_at"] else (now - trip["started_at"]) / span
        d = frac * trip["length"]
        total = N.path_length(pts)
        a, b = N.point_along(pts, max(0.0, min(d, total) - 4.0)), N.point_along(pts, min(total, max(d, 0.0) + 4.0))
        if abs(b[0] - a[0]) + abs(b[1] - a[1]) < 1e-6:
            a, b = pts[0], pts[-1]
        return math.atan2(b[1] - a[1], b[0] - a[0])
    except (ValueError, TypeError, ZeroDivisionError, json.JSONDecodeError, KeyError):
        return 0.0


def trip_progress(trip, now: int) -> float:
    span = trip["arrive_at"] - trip["started_at"]
    return 0.0 if now <= trip["started_at"] else 1.0 if now >= trip["arrive_at"] else (now - trip["started_at"]) / span


# --- spawn --------------------------------------------------------------------------------------------------------------------
def spawn_candidates(city) -> list:
    """Road points near the station that are on dry land, sorted (so the list only depends on the saved city)."""
    net = city.net
    st = next(m for m in city.base["landmarks"] if m["kind"] == "station")
    door = city.spot(st["id"]).door
    radius = SPAWN_RADIUS
    while True:
        field = net.field(door.pt, cutoff=radius)
        cands = sorted(net.pts[i] for i in field.dist if i < net.n and net.land[i])
        if len(cands) >= MIN_SPAWN_SPOTS or radius > 4000:
            return cands
        radius *= 1.6


def spawn_point(city, uid: int) -> tuple:
    """The player's fixed starting spot in this city (pure: same city and Telegram ID, same point)."""
    cands = spawn_candidates(city)
    h = hashlib.sha256(f"hype-spawn:{city.base['seed']}:{int(uid)}".encode()).digest()
    return cands[int.from_bytes(h[:8], "big") % len(cands)]


def ensure_player(db, city, uid: int, now: int | None = None):
    """The player's row in this city; created (once, at their spawn spot) on first use."""
    row = get_player(db, city.id, uid)
    if row is not None:
        return row
    x, y = spawn_point(city, uid)
    now = core.now() if now is None else now
    with db.tx():
        db.x("INSERT OR IGNORE INTO world_players(world_id,user_id,x,y,at_place,spawn_x,spawn_y,created_at,updated_at) VALUES(?,?,?,?,NULL,?,?,?,?)",
             (city.id, uid, round(x, 2), round(y, 2), round(x, 2), round(y, 2), now, now))
    return get_player(db, city.id, uid)


# --- arrival ------------------------------------------------------------------------------------------------------------------
def settle(db, world_id: int, uid: int, now: int | None = None) -> bool:
    """Apply an arrival whose time has come. Idempotent: the trip is flipped walking -> arrived with a guarded UPDATE, so a repeated or
    concurrent call changes nothing the second time. A damaged route falls back to the destination, never to an invented place."""
    now = core.now() if now is None else now
    trip = walking_trip(db, world_id, uid)
    if trip is None or trip["arrive_at"] > now:
        return False
    try:
        x, y = route_of(trip)[-1]
    except (ValueError, TypeError, json.JSONDecodeError):
        x = y = None
    with db.tx():
        if db.x("UPDATE world_trips SET state='arrived' WHERE id=? AND state='walking'", (trip["id"],)).rowcount != 1:
            return False
        if x is None:
            db.x("UPDATE world_players SET at_place=?, updated_at=? WHERE world_id=? AND user_id=?", (trip["dest_id"], now, world_id, uid))
        else:
            db.x("UPDATE world_players SET x=?, y=?, at_place=?, updated_at=? WHERE world_id=? AND user_id=?", (round(x, 2), round(y, 2), trip["dest_id"], now, world_id, uid))
    return True


@dataclass
class Standing:
    row: object                  # the player's world_players row (after settling)
    trip: object | None          # the walk in progress (None when standing still)
    pos: tuple                   # (x, y) now
    now: int
    arrived: object | None = None    # the walk that ended and was applied by THIS call (so the first look after arrival can say so)

    @property
    def walking(self) -> bool:
        return self.trip is not None

    @property
    def progress(self) -> float:
        return trip_progress(self.trip, self.now) if self.trip is not None else 0.0

    @property
    def eta(self) -> int:
        """Seconds until arrival (0 when standing still)."""
        return max(0, self.trip["arrive_at"] - self.now) if self.trip is not None else 0


def stand(db, city, uid: int, now: int | None = None) -> Standing:
    """Settle any due arrival, make sure the player exists, and say where they are right now."""
    now = core.now() if now is None else now
    with db.tx():
        ensure_player(db, city, uid, now)
        due = walking_trip(db, city.id, uid)
        arrived = due if due is not None and due["arrive_at"] <= now and settle(db, city.id, uid, now) else None
        row = get_player(db, city.id, uid)
        trip = walking_trip(db, city.id, uid)
    if trip is not None:
        try:
            pos = trip_position(trip, now)
        except (ValueError, TypeError, json.JSONDecodeError):
            pos = (row["x"], row["y"])
    else:
        pos = (row["x"], row["y"])
    return Standing(row, trip, pos, now, arrived)


# --- walking ------------------------------------------------------------------------------------------------------------------
@dataclass
class Outcome:
    status: str                  # "started" | "busy" | "here" | "unknown" | "unreachable"
    trip: object | None = None
    same: bool = False           # busy: the walk in progress already goes to the requested place


def start_trip(db, city, uid: int, ref: str, now: int | None = None, *, mode: str = "walk", speed: float | None = None) -> Outcome:
    """Plan the walk to `ref` and save it. The saved row is the authority: if this returns "started" the trip exists and cannot be started
    a second time (one walk at a time per player per city). Nothing but the five map tables is touched.
    v0.18: `mode` is how the trip is travelled ("walk" by default; the Snapp job drives a rental car: "car", at the configured car speed).
    It is stored with the trip and changes nothing but the speed and the wording; routes, arrival and one-trip-at-a-time are the same."""
    now = core.now() if now is None else now
    if mode not in ("walk", "car"):
        raise ValueError("unknown travel mode")
    spot = city.spot(ref)
    if spot is None:
        return Outcome("unknown")
    with db.tx():
        st = stand(db, city, uid, now)
        if st.trip is not None:
            return Outcome("busy", st.trip, st.trip["dest_id"] == spot.id)
        if st.row["at_place"] == spot.id:
            return Outcome("here")
        route = city.net.route(st.pos, spot.door.pt)
        if route is None:
            return Outcome("unreachable")
        pts = [[round(x, 2), round(y, 2)] for x, y in route.pts]
        length = round(N.path_length(pts), 2)
        speed = (C.walk_speed() if mode == "walk" else C.car_speed()) if speed is None else float(speed)
        arrive = now + max(1, math.ceil(length / speed - 1e-9))
        try:
            db.x("INSERT INTO world_trips(world_id,user_id,dest_id,dest_kind,route,length,speed,started_at,arrive_at,state,mode) VALUES(?,?,?,?,?,?,?,?,?,'walking',?)",
                 (city.id, uid, spot.id, spot.kind, json.dumps(pts, separators=(",", ":")), length, speed, now, arrive, mode))
        except sqlite3.IntegrityError:                                  # the partial unique index: another walk is already saved
            return Outcome("busy", walking_trip(db, city.id, uid), False)
        db.x("UPDATE world_players SET at_place=NULL, updated_at=? WHERE world_id=? AND user_id=?", (now, city.id, uid))
        return Outcome("started", walking_trip(db, city.id, uid))


def cancel_trip(db, world_id: int, uid: int, now: int | None = None) -> bool:
    """Stop the trip in progress where the traveller is RIGHT NOW (v0.18: a Snapp ride that is cancelled mid-way). Nothing teleports: the player's
    row takes the point of the route the clock has reached and they are standing on the road, at no place. A trip whose time has come is applied
    as an arrival instead. Idempotent: True only when this call stopped a trip that was still going."""
    now = core.now() if now is None else now
    trip = walking_trip(db, world_id, uid)
    if trip is None:
        return False
    if trip["arrive_at"] <= now:
        settle(db, world_id, uid, now)
        return False
    try:
        x, y = trip_position(trip, now)
    except (ValueError, TypeError, json.JSONDecodeError):
        row = get_player(db, world_id, uid)
        x, y = row["x"], row["y"]
    with db.tx():
        if db.x("UPDATE world_trips SET state='cancelled' WHERE id=? AND state='walking'", (trip["id"],)).rowcount != 1:
            return False
        db.x("UPDATE world_players SET x=?, y=?, at_place=NULL, updated_at=? WHERE world_id=? AND user_id=?", (round(x, 2), round(y, 2), now, world_id, uid))
    return True


# --- who is near ----------------------------------------------------------------------------------------------------------------
@dataclass(frozen=True)
class Other:
    uid: int
    name: str
    pos: tuple
    dist: float                  # road distance from the viewer
    walking: bool
    mode: str = "walk"           # v0.18.1: "car" while the player drives (a Snapp ride), so the map can draw a car instead of a ring
    heading: float = 0.0         # v0.18.1: the direction of travel in radians (0 = east, y points down), for a car marker


def nearby(db, city, uid: int, pos, now: int | None = None, radius: float | None = None, limit: int | None = None) -> list:
    """Initialized players of THIS city within `radius` road units of `pos`, nearest first (ties: lower Telegram ID)."""
    now = core.now() if now is None else now
    radius = C.nearby_radius() if radius is None else radius
    limit = C.MAP_MAX_OTHERS if limit is None else limit
    trips = {r["user_id"]: r for r in db.qa("SELECT * FROM world_trips WHERE world_id=? AND state='walking'", (city.id,))}
    cand = []
    for r in db.qa("SELECT p.user_id, p.x, p.y, u.name, u.username, u.badge, u.id FROM world_players p JOIN users u ON u.id=p.user_id "
                   "WHERE p.world_id=? AND p.user_id<>? AND u.banned=0 AND u.bot=0", (city.id, uid)):
        here = (r["x"], r["y"])
        tr = trips.get(r["user_id"])
        if tr is not None:
            try:
                here = trip_position(tr, now)
            except (ValueError, TypeError, json.JSONDecodeError):
                pass
        if G.dist(pos, here) <= radius:                                  # a road is never shorter than the straight line
            cand.append((r, here, tr is not None and tr["arrive_at"] > now))
    if not cand:
        return []
    field = city.net.field(pos, cutoff=radius)
    out = []
    for r, here, walking in cand:
        d = field.to_point(here)
        if d <= radius:
            tr = trips.get(r["user_id"])
            mode = tr["mode"] if tr is not None and walking and tr["mode"] else "walk"
            out.append(Other(r["user_id"], core.display_name(r, with_badge=False), here, d, walking, mode,
                             trip_heading(tr, now) if mode == "car" else 0.0))
    out.sort(key=lambda o: (o.dist, o.uid))
    return out[:limit]


def road_distance(city, a, b) -> float:
    """Road distance between two points of the city (inf if unreachable)."""
    return city.net.field(a).to_point(b)
