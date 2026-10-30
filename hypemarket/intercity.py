"""Travel between cities (v0.20): city IDs, the list of cities, journeys and their arrival.

CITY IDs. A city's ID is `worlds.id`: it is already unique, permanent and sequential (AUTOINCREMENT: SQLite never hands the same number out twice, even if a row were ever
removed) and a database trigger refuses to change it, so nothing had to be migrated. IDs start at 1 in the order the cities were created. `parse_city_id` reads what a player
types (English, Persian or Arabic-Indic digits only).

A JOURNEY is one row of `intercity_trips`: who, from which city, to which, when it started, when it ends (`arrive_at`) and the duration it was given. The duration is
`config.intercity_travel_secs()` (10 real minutes unless INTERCITY_TRAVEL_MINUTES says otherwise) read ONCE, when the journey starts, and saved in the row, so changing the setting
later never moves a journey that is already going, and a restart can never reset a timer: the end time is an absolute timestamp in the row.

* `start` checks everything first and writes the row in one transaction. The character stays in the SOURCE city until the arrival is applied (`characters.current_world_id` is not
  touched at departure). Refused, with a player-facing GameError: no finished character (`char_none`), unknown city (`ic_unknown`), the city they are in (`ic_here`), a journey
  already going (`ic_busy`), an open job (`ic_job`), a local walk or ride in progress (`ic_trip`), a blackjack hand on the table (`ic_casino`).
* While a journey is open the character is IN TRANSIT: `in_transit` is true, `character.in_world` is false in every city (they are on no map, in nobody's «nearby» list, and cannot be greeted
  or hugged), and every city-bound entry point (the map and all its buttons, jobs, the casino, local walking and hugs) answers with the journey's status instead (see travelui.guard_reply).
  Stock-market features have nothing to do with a city and keep working.
* ARRIVAL is lazy and idempotent. `settle` applies a due arrival in one transaction: the character's `current_world_id` becomes the destination (the birthplace is never touched),
  the journey row flips `travelling` -> `arrived` with a guarded UPDATE (a repeated or concurrent call matches nothing the second time), any dormant local walk the character still has
  in the destination city is stopped where its clock says they are, and the character's position there is initialised (spawn spot of that city: deterministic) or restored (the row they
  left there, if they have lived there before). It is applied whenever anybody looks: the traveller's next command, anybody's «nearby» in the destination city (`settle_arrivals`),
  the 5-minute housekeeping tick (`sweep`). Nothing is ever shown as «arrived» before the row says so.
Nothing here touches stock money, life cash, XP or any ledger.
"""
from __future__ import annotations

import logging
import re
import sqlite3

from . import character, config as C, core, presence
from .core import GameError

log = logging.getLogger("hypemarket")

PER_PAGE = 8                                                   # cities on one page of «شهرها»
_DIGITS = str.maketrans("۰۱۲۳۴۵۶۷۸۹٠١٢٣٤٥٦٧٨٩", "01234567890123456789")
_ID = re.compile(r"^[0-9]{1,9}$")


# =====================================================================================================================
#                                                       city IDs
# =====================================================================================================================
def parse_city_id(raw) -> int | None:
    """A city ID typed by a player: only the digits 0-9, ۰-۹ and ٠-٩ (nothing else: no sign, decimal point, separators, spaces, letters, other kinds of digit), at most 9 of them.
    Returns the number, or None when it is not that. (A well-formed number may still name no city.)"""
    if not isinstance(raw, str):
        return None
    s = raw.strip().translate(_DIGITS)
    return int(s) if _ID.fullmatch(s) else None


def city(db, city_id):
    """The saved city `city_id` (a `worlds` row), or None."""
    if not isinstance(city_id, int) or isinstance(city_id, bool) or city_id < 1:
        return None
    return db.q1("SELECT * FROM worlds WHERE id=?", (city_id,))


def city_count(db) -> int:
    return db.q1("SELECT COUNT(*) c FROM worlds")["c"]


def cities_page(db, page: int = 1, per_page: int = PER_PAGE) -> tuple:
    """(rows, page, pages): the cities by ascending ID, `per_page` to a page; an out-of-range page is moved to the nearest real one. Read-only."""
    total = city_count(db)
    pages = max(1, -(-total // per_page))
    page = min(max(1, int(page)), pages)
    rows = db.qa("SELECT id, name_fa, name_en FROM worlds ORDER BY id LIMIT ? OFFSET ?", (per_page, (page - 1) * per_page))
    return rows, page, pages


def city_name(row, lang: str) -> str:
    return row["name_fa"] if lang == "fa" else row["name_en"]


# =====================================================================================================================
#                                                       journeys
# =====================================================================================================================
def active(db, uid: int):
    """The open journey (`travelling`) of player `uid`, or None. A journey whose time has passed is still open until `settle` applies it."""
    return db.q1("SELECT * FROM intercity_trips WHERE user_id=? AND state='travelling'", (uid,))


def in_transit(db, uid: int) -> bool:
    return db.q1("SELECT 1 FROM intercity_trips WHERE user_id=? AND state='travelling'", (uid,)) is not None


def remaining(trip, now: int) -> int:
    """Seconds left of an open journey (never negative)."""
    return max(0, trip["arrive_at"] - now)


def last_arrival(db, uid: int):
    return db.q1("SELECT * FROM intercity_trips WHERE user_id=? AND state='arrived' ORDER BY id DESC LIMIT 1", (uid,))


def _blocker(db, uid: int, world_id: int, now: int) -> str | None:
    """Why the character cannot set off right now, as an error code, or None. A walk whose time has come is applied first (a finished walk blocks nothing)."""
    from . import work                                          # (work builds on presence; imported here to keep this module's import graph one-way)
    if work.current(db, uid) is not None:
        return "ic_job"
    presence.settle(db, world_id, uid, now)
    if presence.walking_trip(db, world_id, uid) is not None:
        return "ic_trip"
    if db.q1("SELECT 1 FROM casino_games WHERE user_id=? AND state='playing'", (uid,)) is not None:
        return "ic_casino"
    return None


def start(db, uid: int, dest_id, now: int | None = None):
    """Set off for city `dest_id`. Returns the new journey row. Raises GameError: char_none (no character), ic_char (not finished), ic_nocity (finished but no city yet), ic_unknown, ic_here, ic_busy (a journey is
    already going; `secs` and `dest` say how long it has left and where to), ic_job, ic_trip, ic_casino. Nothing changes when it raises; the character does not move until
    the arrival is applied."""
    now = core.now() if now is None else now
    with db.tx():
        ch = character.get(db, uid)
        if ch is None:
            raise GameError("char_none")
        if not character.is_done(ch):
            raise GameError("ic_char")
        if ch["current_world_id"] is None:                      # finished in the private chat, no city yet (the first «مپ» in a group gives one)
            raise GameError("ic_nocity")
        settle(db, uid, now)                                    # a journey that already ended is applied first: the traveller may set off again at once
        ch = character.get(db, uid)
        open_trip = active(db, uid)
        if open_trip is not None:
            raise GameError("ic_busy", dest=open_trip["to_world_id"], secs=remaining(open_trip, now))
        dest = city(db, dest_id)
        if dest is None:
            raise GameError("ic_unknown")
        if dest["id"] == ch["current_world_id"]:
            raise GameError("ic_here")
        why = _blocker(db, uid, ch["current_world_id"], now)
        if why:
            raise GameError(why)
        secs = C.intercity_travel_secs()
        try:
            cur = db.x("INSERT INTO intercity_trips(user_id,from_world_id,to_world_id,started_at,arrive_at,duration,state) VALUES(?,?,?,?,?,?,'travelling')",
                       (uid, ch["current_world_id"], dest["id"], now, now + secs, secs))
        except sqlite3.IntegrityError:                          # the partial unique index: another request of the same player got there first
            again = active(db, uid)
            raise GameError("ic_busy", dest=again["to_world_id"] if again else dest["id"], secs=remaining(again, now) if again else secs)
        return db.q1("SELECT * FROM intercity_trips WHERE id=?", (cur.lastrowid,))


def settle(db, uid: int, now: int | None = None, city_obj=None):
    """Apply the arrival of `uid`'s journey if its time has come. Returns the journey row that was applied by THIS call, else None (no journey, not due yet, or somebody
    applied it already). Idempotent and race-safe: the character and the journey change in one transaction, and the journey is flipped with a guarded UPDATE.
    `city_obj` (the destination as a worldplaces.City, when the caller has it loaded) lets the arrival also initialise the player's position there; without it that happens the
    first time they stand in the city (the same deterministic spawn)."""
    now = core.now() if now is None else now
    trip = active(db, uid)
    if trip is None or trip["arrive_at"] > now:
        return None
    with db.tx():
        trip = active(db, uid)
        if trip is None or trip["arrive_at"] > now:
            return None
        if db.x("UPDATE characters SET current_world_id=?, updated_at=? WHERE user_id=?", (trip["to_world_id"], now, uid)).rowcount != 1:
            raise RuntimeError("a journey belongs to a character")
        if db.x("UPDATE intercity_trips SET state='arrived', arrived_at=? WHERE id=? AND state='travelling' AND arrive_at<=?", (now, trip["id"], now)).rowcount != 1:
            raise RuntimeError("a journey was applied twice")
        presence.cancel_trip(db, trip["to_world_id"], uid, now)          # an old local walk of theirs in that city is not resumed: they stand where it had got to
        if city_obj is not None and city_obj.id == trip["to_world_id"]:
            presence.ensure_player(db, city_obj, uid, now)               # position: the row they left there, or the spawn spot of that city
        log.info("user %s arrived in city %s from %s", uid, trip["to_world_id"], trip["from_world_id"])
        return db.q1("SELECT * FROM intercity_trips WHERE id=?", (trip["id"],))


async def asettle(db, uid: int, now: int | None = None):
    """`settle` for the event loop: the destination is loaded (cached; drawn once, in a worker thread) first so that the position can be set up at once. A destination that cannot be
    loaded does not hold the traveller back: the arrival is applied without it."""
    from . import worldplaces
    now = core.now() if now is None else now
    trip = active(db, uid)
    if trip is None or trip["arrive_at"] > now:
        return None
    obj = None
    w = city(db, trip["to_world_id"])
    if w is not None:
        try:
            obj = await worldplaces.aload_city(db, w)
        except Exception:                                                      # noqa: BLE001 (a broken city is reported by the screens that need it, not here)
            log.warning("arrival of user %s: city %s could not be loaded", uid, trip["to_world_id"], exc_info=True)
    return settle(db, uid, now, obj)


def settle_arrivals(db, city_obj, now: int | None = None) -> int:
    """Apply every due arrival whose destination is `city_obj` (so travellers who have reached it are on its map even if they have not typed anything since). Returns how many."""
    now = core.now() if now is None else now
    n = 0
    for r in db.qa("SELECT user_id FROM intercity_trips WHERE to_world_id=? AND state='travelling' AND arrive_at<=? ORDER BY id LIMIT 500", (city_obj.id, now)):
        if settle(db, r["user_id"], now, city_obj) is not None:
            n += 1
    return n


def sweep(db, now: int | None = None) -> int:
    """The housekeeping tick: apply every due arrival (no city needed; positions are set up when the player first stands in the city). Returns how many were applied."""
    now = core.now() if now is None else now
    n = 0
    for r in db.qa("SELECT user_id FROM intercity_trips WHERE state='travelling' AND arrive_at<=? ORDER BY id LIMIT 500", (now,)):
        try:
            if settle(db, r["user_id"], now) is not None:
                n += 1
        except Exception:                                                      # noqa: BLE001 (one damaged row never blocks the others)
            log.exception("could not apply the arrival of user %s", r["user_id"])
    return n


# =====================================================================================================================
#                                                the guard of city-bound features
# =====================================================================================================================
def guard(db, uid: int, now: int | None = None):
    """The open journey of `uid` if they are still travelling NOW (a due arrival is applied first, without a city), else None. City-bound entry points call this before they do
    anything in a city and answer `guard_text(...)` instead when it returns a row."""
    now = core.now() if now is None else now
    if active(db, uid) is None:
        return None
    settle(db, uid, now)
    return active(db, uid)


async def aguard(db, uid: int, now: int | None = None):
    """`guard` for the event loop (a due arrival also sets the position up in the destination)."""
    now = core.now() if now is None else now
    if active(db, uid) is None:
        return None
    await asettle(db, uid, now)
    return active(db, uid)
