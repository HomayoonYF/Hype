"""What the player sees of the city list and of travel between cities (v0.20): `شهرها`, `سفر <ID>`, the journey's status, and the answer every city-bound feature
gives a traveller. The rules (IDs, journeys, arrival) are in intercity.py; this module only turns them into messages and buttons."""
from __future__ import annotations

from . import character, characterui, core, fmt, intercity
from .core import GameError
from .texts import t
from .views import Reply, kb

PAGE_CB = "cy"                                                  # cy:<page>:<player id>: the pages of «شهرها» (the last part is always the player's id)


def num(n, lang: str) -> str:
    """A city ID as it is shown: plain digits, no thousands separator (it is something a player types back)."""
    return fmt.loc(str(int(n)), lang)


def _city(db, city_id: int, lang: str) -> tuple:
    """(ID text, escaped name) of a city; a city that is gone shows only its number."""
    w = intercity.city(db, city_id)
    return num(city_id, lang), (fmt.esc(intercity.city_name(w, lang)) if w is not None else "?")


def example_id(db, current) -> str:
    """An ID to show in «سفر ...»: the lowest ID that is not the player's own city (2 when there is no other city yet)."""
    r = db.q1("SELECT MIN(id) m FROM worlds WHERE id<>?", (current if current is not None else -1,))
    return str(r["m"] if r is not None and r["m"] is not None else 2)


def _clock(ts: int, lang: str) -> str:
    return fmt.clock(ts, lang)


# =====================================================================================================================
#                                                   the journey, in words
# =====================================================================================================================
def status_text(db, lang: str, trip, now: int) -> str:
    left = intercity.remaining(trip, now)
    frac = min(1.0, max(0.0, (now - trip["started_at"]) / trip["duration"]))
    fid, fname = _city(db, trip["from_world_id"], lang)
    tid, tname = _city(db, trip["to_world_id"], lang)
    return t(lang, "ic.status", from_id=fid, from_name=fname, to_id=tid, to_name=tname, bar=fmt.bar(frac), pct=fmt.num(int(frac * 100), lang),
             eta=fmt.dur(left, lang), clock=_clock(trip["arrive_at"], lang))


def guard_text(db, lang: str, trip, now: int | None = None) -> str:
    """The message of a city-bound feature that a traveller tried to use: where they are going and how long is left."""
    now = core.now() if now is None else now
    tid, tname = _city(db, trip["to_world_id"], lang)
    return t(lang, "ic.guard", id=tid, name=tname, eta=fmt.dur(intercity.remaining(trip, now), lang))


def guard_reply(c, trip) -> Reply:
    """For a message: the status text. For a button press: a short alert (nothing is edited, started or changed)."""
    if c.cb is not None:
        return Reply(toast=t(c.lang, "ic.guard.brief"), alert=True)
    return Reply(text=guard_text(c.db, c.lang, trip))


def arrived_text(db, lang: str, trip) -> str:
    tid, tname = _city(db, trip["to_world_id"], lang)
    return t(lang, "ic.arrived", id=tid, name=tname)


# =====================================================================================================================
#                                                       commands
# =====================================================================================================================
def _unfinished(c, ch) -> Reply:
    """A player whose character is not finished: the group gets the private-chat button, the private chat gets the set-up screen itself."""
    if c.is_group or c.cb is not None:
        return characterui.need_reply(c, ch)
    return characterui.screen(c.lang, ch, c.uid)


async def travel(c) -> Reply | None:
    """`سفر <ID>`: set off for a city; `سفر` alone: the journey in progress (or where you are and how to travel). None for an account that has no character (a bot)."""
    db, lang, uid = c.db, c.lang, c.uid
    ch = character.ensure_profile(db, uid)
    if ch is None:
        return None
    if not character.is_done(ch):
        return _unfinished(c, ch)
    now = core.now()
    done = await intercity.asettle(db, uid, now)                        # a journey that has ended is applied first
    note = arrived_text(db, lang, done) + "\n\n" if done is not None else ""
    trip = intercity.active(db, uid)
    if not c.args:
        if trip is not None:
            return Reply(text=note + status_text(db, lang, trip, now))
        ch = character.get(db, uid)
        if ch["current_world_id"] is None:                                  # finished the set-up in the private chat but never entered a city
            return Reply(text=note + t(lang, "ic.nocity"))
        cid, name = _city(db, ch["current_world_id"], lang)
        return Reply(text=note + t(lang, "ic.idle", id=cid, name=name, example=example_id(db, ch["current_world_id"])))
    dest = intercity.parse_city_id(c.args[0]) if len(c.args) == 1 else None
    if dest is None:
        raise GameError("ic_bad")
    try:
        new = intercity.start(db, uid, dest, now)
    except GameError as e:
        if e.code == "ic_busy":
            busy = intercity.active(db, uid)
            if busy is not None:
                return Reply(text=note + t(lang, "e.ic_busy") + "\n\n" + status_text(db, lang, busy, now))
        raise
    tid, tname = _city(db, new["to_world_id"], lang)
    return Reply(text=note + t(lang, "ic.started", id=tid, name=tname, dur=fmt.dur(new["duration"], lang), clock=_clock(new["arrive_at"], lang)))


def _status_line(db, lang: str, uid: int, ch, trip, now: int) -> str:
    if trip is not None:
        tid, tname = _city(db, trip["to_world_id"], lang)
        return t(lang, "ic.on_road", id=tid, name=tname, eta=fmt.dur(intercity.remaining(trip, now), lang))
    if ch is not None and ch["current_world_id"] is not None:
        cid, name = _city(db, ch["current_world_id"], lang)
        return t(lang, "ic.here", id=cid, name=name)
    return ""


def cities_reply(db, lang: str, uid: int, page: int = 1, now: int | None = None) -> Reply:
    """One page of the list of cities: ID and name, the player's own city and (on a journey) their destination marked. Read-only: nothing is created or changed."""
    now = core.now() if now is None else now
    rows, page, pages = intercity.cities_page(db, page)
    if not rows:
        return Reply(text=t(lang, "ic.empty"))
    ch = character.get(db, uid)
    trip = intercity.active(db, uid)
    here = ch["current_world_id"] if ch is not None else None
    lines = []
    for r in rows:
        name = fmt.esc(intercity.city_name(r, lang))
        if trip is not None and r["id"] == trip["to_world_id"]:
            lines.append(t(lang, "ic.line.dest", id=num(r["id"], lang), name=name))
        elif r["id"] == here:
            lines.append(t(lang, "ic.line.here", id=num(r["id"], lang), name=name))
        else:
            lines.append(t(lang, "ic.line", mark="▫️", id=num(r["id"], lang), name=name))
    text = t(lang, "ic.list", page=fmt.num(page, lang), pages=fmt.num(pages, lang), lines="\n".join(lines), status=_status_line(db, lang, uid, ch, trip, now),
             example=example_id(db, here))
    nav = []
    if page > 1:
        nav.append((t(lang, "ic.b.prev"), f"{PAGE_CB}:{page - 1}:{uid}"))
    if page < pages:
        nav.append((t(lang, "ic.b.next"), f"{PAGE_CB}:{page + 1}:{uid}"))
    return Reply(text=text.replace("\n\n\n", "\n\n"), kb=kb([nav]) if nav else None)


async def cities(c) -> Reply | None:
    """`شهرها` / `شهرها <page>`: the list of cities. Anybody with a character profile may look (even before it is finished: it only reads). Read-only."""
    page = 1
    if c.args:
        n = intercity.parse_city_id(c.args[0]) if len(c.args) == 1 else None
        if n is None:
            raise GameError("ic_bad")
        page = max(1, n)
    if character.ensure_profile(c.db, c.uid) is None:
        return None
    await intercity.asettle(c.db, c.uid)
    return cities_reply(c.db, c.lang, c.uid, page)


async def handle_page(c) -> Reply:
    """cy:<page>:<player id>: another page of the list, in place."""
    rest = c.args[:-1]
    page = intercity.parse_city_id(rest[0]) if len(rest) == 1 else None
    if page is None:
        return Reply(toast=t(c.lang, "e.bad_usage"))
    await intercity.asettle(c.db, c.uid)
    r = cities_reply(c.db, c.lang, c.uid, page)
    r.edit = True
    return r
