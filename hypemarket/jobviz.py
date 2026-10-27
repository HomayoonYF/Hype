"""Map pictures for the job screens (v0.18.1, narrowed in v0.18.2).

The only pictures a job screen carries are MAPS of the city with the route, drawn on the server and sent as the photo of the job message: a Snapp ride (the
way to the passenger in BLUE, the passenger's ride to the destination in YELLOW, a person pin at the pickup, a flag pin at the destination, and the player as a
small CAR instead of the round marker), and the walk to McDonald's or to a municipal work site (the walk drawn like every walk, a bag pin at the workplace).
Since v0.18.2 there are no illustrated scenes of a character doing a job: those screens are text (workui.py).

Nothing here touches the database or the clock: every function takes values that were already read (the task, the standing, the city) and is
pure, so it can run in a worker thread while the bot keeps answering. A picture that cannot be drawn is simply not sent (the screen's text and
buttons are always sent); see `handlers.send`.
"""
from __future__ import annotations

import asyncio
import logging
import math
import threading
from collections import OrderedDict

from . import fmt, presence, work, worldinspect, worldmap, worldnet as N
from .texts import t

log = logging.getLogger("hypemarket")

_ROUTES: OrderedDict = OrderedDict()                  # (seed, spot a, spot b) -> the road points of the saved city between two places
_ROUTES_MAX = 256
_ROUTES_LOCK = threading.Lock()                       # `_between` runs in worker threads (map_pic): the cache is only touched under this lock


# --- the rental car -----------------------------------------------------------------------------------------------------------
def car_label(lang: str, car: dict) -> str:
    """«پراید 1384 (سفید)» / «Pride 1384 (white)»."""
    return t(lang, "snapp.car", year=fmt.loc(str(int(car["y"])), lang), color=t(lang, "snapp.color." + car["c"]))


def car_of_user(db, uid: int) -> str:
    """The body colour of the Snapp car the player is driving right now (white when they are not on a ride)."""
    task = work.current(db, uid)
    if task is not None and task["job"] == work.SNAPP and task["stage"] in ("assigned", "to_pickup", "to_dest"):
        return work.car_of(task)["c"]
    return "white"


# --- routes -------------------------------------------------------------------------------------------------------------------
def _road(city, a, b) -> list:
    """The road points from the point `a` to the door of the spot `b`; [] when there is no road."""
    try:
        r = city.net.route(a, b.door.pt)
    except Exception:
        log.exception("route for a job picture failed")
        return []
    return [tuple(p) for p in r.pts] if r is not None and len(r.pts) >= 2 else []


def _between(city, a, b) -> list:
    """The road between two PLACES (cached: it is the same road every time that ride is looked at)."""
    key = (city.base["seed"], a.id, b.id)
    with _ROUTES_LOCK:
        hit = _ROUTES.get(key)
        if hit is not None:
            _ROUTES.move_to_end(key)
            return hit
    hit = _road(city, a.door.pt, b)                                      # planned outside the lock (read-only on the road network)
    with _ROUTES_LOCK:
        _ROUTES[key] = hit
        while len(_ROUTES) > _ROUTES_MAX:
            _ROUTES.popitem(last=False)
    return hit


def _heading_of(pts) -> float:
    if not pts or len(pts) < 2:
        return 0.0
    a = pts[0]
    for b in pts[1:]:
        if abs(b[0] - a[0]) + abs(b[1] - a[1]) > 1e-6:
            return math.atan2(b[1] - a[1], b[0] - a[0])
    return 0.0


def _cut(trip, progress: float) -> tuple:
    """(driven part, part still ahead) of a saved trip's route, cut where `progress` (0..1) has reached."""
    pts = presence.route_of(trip)
    return N.split_along(pts, max(0.0, min(1.0, progress)) * float(trip["length"]))


# --- the Snapp map ------------------------------------------------------------------------------------------------------------
def snapp_marks(lang: str, city, task, st, car: dict) -> dict | None:
    """The markers of a Snapp ride (see worldmap.overlay): the car, the pins and the blue / yellow route pieces. None when the ride's places are
    gone from the city (the screen then goes without a picture). `st` is the player's presence.Standing."""
    d = work.data_of(task)
    pick, dest = city.spot(d.get("pickup")), city.spot(d.get("dest"))
    if pick is None or dest is None:
        return None
    stage = task["stage"]
    trip = st.trip
    want = dest.id if stage == "to_dest" else pick.id
    if trip is not None and trip["dest_id"] != want:                    # a trip that is not this ride's own leg: draw the ride as if standing
        trip = None
    legs: list = []
    heading = 0.0

    def leg(pts, color, style="dash"):
        if pts and len(pts) >= 2 and N.path_length(pts) > 0.5:             # a piece of no length (nothing driven yet) is not drawn
            legs.append({"pts": [list(p) for p in pts], "color": color, "style": style})

    if stage == "to_dest":
        if trip is not None:
            try:
                walked, left = _cut(trip, st.progress)
                leg(walked, "yellow_done", "solid")
                leg(left, "yellow")
                heading = presence.trip_heading(trip, st.now)
            except (ValueError, TypeError):
                trip = None
        if trip is None:
            ahead = _road(city, st.pos, dest)
            leg(ahead, "yellow")
            heading = _heading_of(ahead)
    else:
        if trip is not None:
            try:
                walked, left = _cut(trip, st.progress)
                leg(walked, "blue_done", "solid")
                leg(left, "blue")
                heading = presence.trip_heading(trip, st.now)
            except (ValueError, TypeError):
                trip = None
        if trip is None and st.row["at_place"] != pick.id:
            ahead = _road(city, st.pos, pick)
            leg(ahead, "blue")
            heading = _heading_of(ahead)
        leg(_between(city, pick, dest), "yellow")
        if trip is None and st.row["at_place"] == pick.id:
            heading = _heading_of(_between(city, pick, dest))
    stops = []
    if stage != "to_dest":
        stops.append({"pt": list(pick.door.pt), "kind": "pickup", "color": "blue", "tag": worldinspect.display_id(pick.id)})
    stops.append({"pt": list(dest.door.pt), "kind": "dest", "color": "yellow", "tag": worldinspect.display_id(dest.id)})
    return {"me": {"pt": st.pos, "label": t(lang, "mp.me"), "vehicle": {"kind": "car", "heading": heading, "color": car["c"]}},
            "legs": legs, "stops": stops}


def walk_marks(lang: str, city, st, target_id: str) -> dict | None:
    """The markers of a walk to a workplace (McDonald's, a municipal site): you, the walk so far / still ahead, and a bag pin at the workplace.
    When the player is not walking there yet, the road they would take is drawn dashed. None when the place is not in the city."""
    spot = city.spot(target_id)
    if spot is None:
        return None
    marks: dict = {"me": {"pt": st.pos, "label": t(lang, "mp.me")},
                   "stops": [{"pt": list(spot.door.pt), "kind": "work", "color": "mint", "tag": worldinspect.display_id(spot.id)}]}
    trip = st.trip
    if trip is not None and trip["dest_id"] == spot.id:
        try:
            marks["walked"], marks["route"] = _cut(trip, st.progress)
            return marks
        except (ValueError, TypeError):
            pass
    if st.trip is None and st.row["at_place"] != spot.id:
        ahead = _road(city, st.pos, spot)
        if ahead:
            marks["legs"] = [{"pts": [list(p) for p in ahead], "color": "mint", "style": "dash"}]
    return marks


# --- turning them into pictures (what a Reply's `pic` runs) ---------------------------------------------------------------------
def map_pic(city, lang: str, build):
    """An async function that draws the city with the markers `build()` returns (None: no picture). `build` is pure and runs in a worker thread,
    so planning a road does not hold up the bot."""
    async def draw():
        marks = await asyncio.to_thread(build)
        if not marks:
            return None
        return await worldmap.render_marked(city.id, city.layout, lang, marks)
    return draw
