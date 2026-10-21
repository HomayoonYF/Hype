"""Fixed public places that were added to a city AFTER it was generated (v0.17: the casino), and the one object the map code works with.

A saved layout is never edited: the layout JSON, its seed, roads, river, districts and every parcel ID and coordinate stay byte-for-byte what
the generator wrote. A place is a row of `world_places` that says «this parcel of this city is, for good, a public place of this kind».
Loading a city validates the saved layout, then overlays those rows IN MEMORY: the reserved parcel becomes `use: "landmark"` and the place joins
the landmark list under its own stable ID (`l5`). Every reader of a city (the map picture, the inspection panels, the walking code and, one
day, land sales) goes through `load_city`, so a reserved parcel can never be offered as empty land.

* New cities get their casino in the same transaction that saves them (`world.save_world`); cities saved by an older version get it the first
  time anyone loads them (and in one pass at start-up, `ensure_all`). Both go through `ensure_casino`, which is idempotent: UNIQUE(world, kind)
  and UNIQUE(world, parcel) make a second casino impossible, and a second call only reads.
* Which parcel? A pure function of the saved layout: an empty parcel, preferably in the commercial district, fronting a main road, away from
  the other public places and close to the middle of town. It is chosen once and persisted, so it never moves.
"""
from __future__ import annotations

import asyncio
import logging
from collections import OrderedDict
from dataclasses import dataclass, field

from . import core, world, worldgeom as G, worldinspect, worldnet

log = logging.getLogger("hypemarket")

CASINO = "casino"
PLACE_KINDS = (CASINO,)                       # kinds of places this module adds (the generator's own four stay in world.LANDMARKS)
ZONE_RANK = {"commercial": 0, "civic": 1, "residential": 2, "industrial": 3, "park": 4}
SPACING = 70.0                                # keep this far from the other public places when the city allows it
FRONT = 24.0                                  # a parcel whose centre is this close to a road fronts it


def _num(ident: str) -> int:
    return int(ident[1:]) if isinstance(ident, str) and ident[1:].isdigit() else 0


# --- choosing and saving ------------------------------------------------------------------------------------------------------
def choose_parcel(base: dict, net: worldnet.RoadNet) -> dict:
    """The empty parcel of `base` that becomes the casino. Pure and deterministic: same layout, same parcel."""
    free = [p for p in base["parcels"] if p["use"] == "vacant"]
    if not free:
        raise world.WorldError("no_room")
    centre = G.centroid(base["footprint"])
    spots = [m["at"] for m in base["landmarks"]]

    def score(p):
        door = net.door(p["id"], p["c"])
        seg = net.segs[door.seg]
        return (ZONE_RANK.get(p["zone"], 9),
                1 if spots and min(G.dist(p["c"], q) for q in spots) < SPACING else 0,
                1 if door.off > FRONT else 0,
                0 if seg.kind == "main" else 1,
                round(G.dist(p["c"], centre), 1),
                _num(p["id"]))
    return min(free, key=score)


def get_place(db, world_id: int, kind: str):
    return db.q1("SELECT * FROM world_places WHERE world_id=? AND kind=?", (world_id, kind))


def places(db, world_id: int) -> list:
    return db.qa("SELECT * FROM world_places WHERE world_id=? ORDER BY place_id", (world_id,))


def ensure_casino(db, w, base: dict, net: worldnet.RoadNet | None = None):
    """The world's casino row, created if the city has none yet. Idempotent and safe against two callers at once."""
    row = get_place(db, w["id"], CASINO)
    if row is not None:
        return row
    net = net or worldnet.RoadNet(base)
    parcel = choose_parcel(base, net)
    with db.tx():
        row = get_place(db, w["id"], CASINO)                          # someone may have just made it
        if row is None:
            used = [_num(m["id"]) for m in base["landmarks"]] + [_num(r["place_id"]) for r in places(db, w["id"])]
            db.x("INSERT OR IGNORE INTO world_places(world_id,place_id,kind,parcel_id,created_at) VALUES(?,?,?,?,?)",
                 (w["id"], f"l{max(used) + 1}", CASINO, parcel["id"], core.now()))
            row = get_place(db, w["id"], CASINO)
    if row is None:
        raise world.WorldError("no_room")
    return row


# --- the city object ----------------------------------------------------------------------------------------------------------
def overlay(base: dict, rows) -> dict:
    """`base` plus its places, as a NEW layout (the saved one is left alone): each reserved parcel is a landmark parcel, each place a landmark."""
    parcels = list(base["parcels"])
    index = {p["id"]: i for i, p in enumerate(parcels)}
    landmarks = list(base["landmarks"])
    for r in rows:
        i = index.get(r["parcel_id"])
        if i is None or parcels[i]["use"] != "vacant" or r["kind"] not in PLACE_KINDS:
            log.error("place %s of world %s does not fit its saved city", r["place_id"], r["world_id"])
            raise world.WorldError("corrupt")
        p = dict(parcels[i])
        p["use"] = "landmark"
        parcels[i] = p
        landmarks.append({"id": r["place_id"], "kind": r["kind"], "parcel": p["id"], "at": list(p["c"])})
    out = dict(base)
    out["parcels"], out["landmarks"] = parcels, landmarks
    return out


@dataclass(frozen=True)
class Spot:
    """Somewhere a player can walk to: a parcel or a public place, with the point of the road where it is entered."""
    id: str                   # canonical stored ID: the place's own ID for a public place (even when reached by its parcel's ID), else the parcel's
    kind: str                 # "parcel" | "landmark"
    parcel: dict
    landmark: dict | None
    door: worldnet.Snap


@dataclass
class City:
    world: object
    base: dict                # the validated saved layout (never modified)
    layout: dict              # base + places: what pictures, panels and walking use
    net: worldnet.RoadNet
    rows: dict = field(default_factory=dict)          # kind -> world_places row
    _pidx: dict = field(default_factory=dict, repr=False)
    _lidx: dict = field(default_factory=dict, repr=False)

    def __post_init__(self):
        self._pidx = {p["id"]: p for p in self.layout["parcels"]}
        self._lidx = {m["id"]: m for m in self.layout["landmarks"]}
        self._by_parcel = {m["parcel"]: m for m in self.layout["landmarks"]}
        self._district = {b["id"]: b["district"] for b in self.layout["blocks"]}

    @property
    def id(self) -> int:
        return self.world["id"]

    @property
    def casino_id(self) -> str | None:
        r = self.rows.get(CASINO)
        return r["place_id"] if r else None

    def spot(self, ref: str) -> Spot | None:
        """The walkable place `ref` names («p14», «l5») or None (unknown, or a district: you walk to places, not to areas)."""
        if not isinstance(ref, str):
            return None
        if ref in self._lidx:
            lm = self._lidx[ref]
            p = self._pidx[lm["parcel"]]
            return Spot(lm["id"], "landmark", p, lm, self.net.door(p["id"], p["c"]))
        p = self._pidx.get(ref)
        if p is None:
            return None
        lm = self._by_parcel.get(p["id"])
        return Spot(lm["id"] if lm else p["id"], "landmark" if lm else "parcel", p, lm, self.net.door(p["id"], p["c"]))

    def district_of(self, parcel: dict) -> str:
        """ID of the district a parcel lies in."""
        return self._district[parcel["block"]]

    def spots(self, kind: str | None = None):
        """Every public place (kind 'landmark') or every parcel (kind 'parcel') as Spots."""
        if kind == "landmark":
            return [self.spot(m["id"]) for m in self.layout["landmarks"]]
        return [self.spot(p["id"]) for p in self.layout["parcels"]]


# --- loading (the saved layout is validated and parsed once per version of its text, not once per button) -------------------------
_base: "OrderedDict[tuple, tuple]" = OrderedDict()
BASE_MAX = 24


def _key(w) -> tuple:
    return (w["id"], len(w["layout"]), hash(w["layout"]))


def _remember(key, value) -> None:
    _base[key] = value
    _base.move_to_end(key)
    while len(_base) > BASE_MAX:
        _base.popitem(last=False)


def cache_clear() -> None:
    _base.clear()


def _build_base(w) -> tuple:
    layout = world.load_layout(w)                                       # raises WorldError("corrupt"): a broken city is reported, never replaced
    return layout, worldnet.RoadNet(layout)


def _city(db, w, base: dict, net: worldnet.RoadNet) -> City:
    ensure_casino(db, w, base, net)
    rows = places(db, w["id"])
    return City(w, base, overlay(base, rows), net, {r["kind"]: r for r in rows})


def load_city(db, w) -> City:
    """The saved city of world row `w` with its public places. Synchronous: for start-up, tests and code already off the event loop."""
    key = _key(w)
    if key not in _base:
        _remember(key, _build_base(w))
    base, net = _base[key]
    _base.move_to_end(key)
    return _city(db, w, base, net)


async def aload_city(db, w) -> City:
    """`load_city` for the event loop: parsing and indexing a city happens in a worker thread, the database is only touched here."""
    key = _key(w)
    hit = _base.get(key)
    if hit is None:
        hit = await asyncio.to_thread(_build_base, w)
        _remember(key, hit)
    else:
        _base.move_to_end(key)
    return _city(db, w, *hit)


def ensure_all(db) -> int:
    """Give every saved city that has no casino yet its casino (start-up pass; the cities themselves are never regenerated or touched).
    A city that cannot be read is skipped and logged. Returns how many casinos were added."""
    added = 0
    for w in db.qa("SELECT * FROM worlds WHERE id NOT IN (SELECT world_id FROM world_places WHERE kind=?) ORDER BY id", (CASINO,)):
        try:
            load_city(db, w)
            added += 1
        except Exception:
            log.exception("could not add the casino to world %s", w["id"])
    return added
