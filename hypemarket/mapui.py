"""The interactive city map (v0.17): the message under `مپ`, its buttons, and who may press them.

One `مپ` makes one MAP SESSION: a row of `map_sessions` that remembers who opened it (Telegram user ID), which city it shows (`world_id`: since v0.19 the city
of the player's CHARACTER, which can have been first created for another group), in which group (`chat_id`) and in which message it lives. Every button of that message carries only the session's random ID (`mp:<sid>:<action>[:<arg>]`, never a user ID);
what a press may do is decided on the server, from the row, BEFORE anything is read or changed (see `authorize`):

    1. the session exists            4. the message the button sits on is the session's message
    2. the presser is its owner      5. it has not expired
    3. the chat is the session's chat
    (then, in `handle`: the session's city is still the presser's character's current city)

Anyone else gets a short alert and nothing happens: no edit, no registration, no state change. Then the action itself is checked against the
session's current screen and the player's current state (a «go» only from the screen of that place, «enter» only when the player has
arrived, a blackjack button only inside the casino and only with the seq of the screen it was drawn for). Stale, repeated and late presses
therefore fail safely, and nothing is applied or charged twice (there is nothing to charge: nothing here touches money, stocks or XP).

The screens: the map with your marker, the people near you and your walk · around me · the public places (paged) · the nearest empty
parcels (paged) · one place (the existing inspection panel, plus «go» / «enter») · my trip · the casino and its blackjack table.
"""
from __future__ import annotations

import logging
import math
import re
import secrets
import sqlite3

from . import casino, cash, character, config as C, core, fmt, interactui, jobviz, presence, views, work, world, worldgeom as G, worldinspect, worldmap, worldnet as N, worldplaces
from .core import GameError
from .texts import t
from .views import Reply, kb

log = logging.getLogger("hypemarket")

ICONS = {"city_hall": "🏛", "hospital": "🏥", "school": "🏫", "station": "🚉", "casino": "🎰", "mcdonalds": "🍔"}
DENY = {"gone": "mp.expired", "expired": "mp.expired", "owner": "mp.not_yours", "chat": "mp.stale", "message": "mp.stale", "world": "mp.stale"}
_SID = re.compile(r"^[0-9a-f]{8}$")
_TAGS = re.compile(r"<[^>]+>")


def plain(text: str, limit: int = 190) -> str:
    import html
    return html.unescape(_TAGS.sub("", text))[:limit]


# =====================================================================================================================
#                                                  sessions and ownership
# =====================================================================================================================
def new_session(db, world_id: int, chat_id: int, uid: int, now: int | None = None) -> str:
    """Register a new map message of player `uid` in city `world_id` / chat `chat_id` and drop what is no longer needed (expired sessions,
    sessions whose message never got sent, a player's oldest sessions beyond the limit and the casino games of every dropped session)."""
    now = core.now() if now is None else now
    with db.tx():
        db.x("DELETE FROM map_sessions WHERE expires_at<=?", (now,))
        db.x("DELETE FROM map_sessions WHERE message_id IS NULL AND created_at<?", (now - 3600,))
        old = db.qa("SELECT sid FROM map_sessions WHERE world_id=? AND user_id=? ORDER BY created_at DESC, rowid DESC LIMIT -1 OFFSET ?",
                    (world_id, uid, C.MAP_SESSIONS_PER_PLAYER - 1))
        for r in old:
            db.x("DELETE FROM map_sessions WHERE sid=?", (r["sid"],))
        casino.sweep(db, now)                                  # a hand whose message is gone is stood and paid first (its chip is never lost with the message)
        db.x("DELETE FROM casino_games WHERE state='done' AND sid NOT IN (SELECT sid FROM map_sessions)")
        for _ in range(12):
            sid = secrets.token_hex(4)
            try:
                db.x("INSERT INTO map_sessions(sid,world_id,chat_id,user_id,message_id,view,page,sel,back,created_at,expires_at) VALUES(?,?,?,?,NULL,'map',0,NULL,'m',?,?)",
                     (sid, world_id, chat_id, uid, now, now + C.map_session_ttl()))
                return sid
            except sqlite3.IntegrityError:
                continue
    raise RuntimeError("could not create a map session")


def set_message(db, sid: str, old_message_id: int | None, message_id: int) -> None:
    """Remember which message shows the session: set once, or moved when a press had to be answered with a new message."""
    db.x("UPDATE map_sessions SET message_id=? WHERE sid=? AND (message_id IS NULL OR message_id=?)", (message_id, sid, old_message_id))


def authorize(db, sid, uid: int, chat_id: int, message_id, now: int | None = None) -> tuple:
    """(session row, None) when `uid` may use the buttons of message `message_id` in chat `chat_id` under session `sid`; otherwise
    (None, reason) with reason in gone / owner / chat / message / expired. Reads only."""
    now = core.now() if now is None else now
    if not isinstance(sid, str) or not _SID.match(sid):
        return None, "gone"
    row = db.q1("SELECT * FROM map_sessions WHERE sid=?", (sid,))
    if row is None:
        return None, "gone"
    if row["user_id"] != uid:
        return None, "owner"
    if row["chat_id"] != chat_id:
        return None, "chat"
    if row["message_id"] is None or message_id is None or row["message_id"] != message_id:
        return None, "message"
    if row["expires_at"] <= now:
        return None, "expired"
    return row, None


def pre_check(db, parts: list, uid: int, chat_id: int, message_id) -> str | None:
    """The reason a map button press is refused (None = allowed). Run by the dispatcher before the presser is registered or anything else
    about them is touched."""
    if len(parts) < 3:
        return "gone"
    return authorize(db, parts[1], uid, chat_id, message_id)[1]


def _save(db, sid: str, view: str, page: int = 0, sel: str | None = None, back: str = "m", now: int | None = None) -> None:
    now = core.now() if now is None else now
    db.x("UPDATE map_sessions SET view=?, page=?, sel=?, back=?, expires_at=? WHERE sid=?", (view, page, sel, back, now + C.map_session_ttl(), sid))


def _uint(s, maxlen: int = 15):
    """A button argument as a non-negative int: plain ASCII digits only (str.isdigit() also accepts «²», «①» or Arabic-Indic digits, which int() then
    refuses or reads as another number), at most `maxlen` of them; None otherwise."""
    return int(s) if isinstance(s, str) and s.isascii() and s.isdigit() and len(s) <= maxlen else None


def cd(sid: str, *parts) -> str:
    return ":".join(["mp", sid] + [str(p) for p in parts])


# =====================================================================================================================
#                                                       small helpers
# =====================================================================================================================
def short_name(name, limit: int = 12) -> str:
    """A player's name as a short map label: letters, digits and a few separators only (emoji and symbols would draw as boxes)."""
    keep = "".join(ch for ch in str(name) if ch.isalnum() or ch in " _-.‌")
    return " ".join(keep.split())[:limit].strip()


def _dist(d: float, lang: str) -> str:
    """A road distance as the game-world metres / kilometres players are told (config.UNITS_PER_KM): «167 متر»."""
    return fmt.dist(d, lang)


def _eta(d: float) -> int:
    return max(1, math.ceil(d / C.walk_speed() - 1e-9))


def place_html(lang: str, spot) -> str:
    """«کازینو L5» or «قطعه P14» (the ID in <code>)."""
    ident = f"<code>{worldinspect.display_id(spot.id)}</code>"
    if spot.landmark:
        return f"{t(lang, 'map.lm.' + spot.landmark['kind'])} {ident}"
    return t(lang, "mp.parcel", id=ident)


def place_label(lang: str, spot) -> str:
    """Plain text for a button."""
    if spot.landmark:
        return f"{t(lang, 'map.lm.' + spot.landmark['kind'])} {worldinspect.display_id(spot.id)}"
    return t(lang, "mp.parcel", id=worldinspect.display_id(spot.id))


def _dest_html(lang: str, city, dest_id: str) -> str:
    spot = city.spot(dest_id)
    return place_html(lang, spot) if spot else fmt.esc(worldinspect.display_id(dest_id))


def _nearest_place(city, pos) -> dict:
    return min(city.layout["landmarks"], key=lambda m: (G.dist(pos, m["at"]), m["id"]))


def _pct(st) -> str:
    return fmt.num(round(st.progress * 100), "en")


# =====================================================================================================================
#                                                       building a screen
# =====================================================================================================================
class Screen:
    """What one press ends up showing."""
    def __init__(self, text: str, rows: list, *, view: str, page: int = 0, sel: str | None = None, back: str = "m",
                 hl: dict | None = None, ref: str | None = None, picture: bool = True, toast: str | None = None, alert: bool = False):
        self.text, self.rows, self.view, self.page, self.sel, self.back = text, rows, view, page, sel, back
        self.hl, self.ref, self.picture, self.toast, self.alert = hl, ref, picture, toast, alert


def _marks(lang: str, city, st, others, tag_dest: bool, db=None) -> dict:
    """The markers of a map picture. v0.18.1: someone who is driving a Snapp car is drawn as a small car (yours in your own colour, the others
    in theirs) instead of the round marker; needs `db` to know who is driving (without it every marker is the plain one)."""
    me: dict = {"pt": st.pos, "label": t(lang, "mp.me")}
    if db is not None:
        task = work.current(db, st.row["user_id"])
        if task is not None and task["job"] == work.SNAPP and task["world_id"] == city.id and task["stage"] in ("assigned", "to_pickup", "to_dest"):
            heading = presence.trip_heading(st.trip, st.now) if st.trip is not None else 0.0
            me["vehicle"] = {"kind": "car", "heading": heading, "color": work.car_of(task)["c"]}
    marks: dict = {"me": me, "others": []}
    for o in others:
        entry = {"pt": o.pos, "label": short_name(o.name)}
        if o.mode == "car" and db is not None:
            entry["vehicle"] = {"kind": "car", "heading": o.heading, "color": jobviz.car_of_user(db, o.uid)}
        marks["others"].append(entry)
    if st.trip is not None:
        try:
            pts = presence.route_of(st.trip)
            walked, left = N.split_along(pts, st.progress * st.trip["length"])
            marks["walked"], marks["route"] = walked, left
            marks["dest"] = {"pt": pts[-1], "tag": worldinspect.display_id(st.trip["dest_id"]) if tag_dest else None}
        except (ValueError, TypeError):
            pass
    return marks


def _status(lang: str, city, st, others) -> str:
    lines = []
    if st.arrived is not None:
        lines.append(t(lang, "mp.arrived", dest=_dest_html(lang, city, st.arrived["dest_id"])))
    if st.trip is not None:
        lines.append(t(lang, "mp.drive" if st.trip["mode"] == "car" else "mp.walk", dest=_dest_html(lang, city, st.trip["dest_id"]), pct=fmt.loc(_pct(st), lang), eta=fmt.dur(st.eta, lang),
                       speed=fmt.kmh(float(st.trip["speed"]), lang)))
    place = st.row["at_place"]
    spot = city.spot(place) if place and st.trip is None else None
    if spot is not None:
        lines.append(t(lang, "mp.pos.at", place=place_html(lang, spot)))
    else:
        lm = _nearest_place(city, st.pos)
        lines.append(t(lang, "mp.pos.near", place=place_html(lang, city.spot(lm["id"]))))
    if others:
        names = [fmt.esc(short_name(o.name) or "…") for o in others[:3]]
        shown = "، ".join(names) if lang == "fa" else ", ".join(names)
        if len(others) > 3:
            shown = t(lang, "mp.others.more", names=shown, n=fmt.num(len(others) - 3, lang))
        lines.append(t(lang, "mp.others", names=shown))
    else:
        lines.append(t(lang, "mp.others.none"))
    return "\n".join(lines)


def _main_rows(lang: str, sid: str, st) -> list:
    rows = [[(t(lang, "mp.b.near"), cd(sid, "nb")), (t(lang, "mp.b.places"), cd(sid, "pl", 0))],
            [(t(lang, "mp.b.lots"), cd(sid, "ln", 0)), (t(lang, "mp.b.casino"), cd(sid, "o", "casino"))]]
    jobs = (t(lang, "mp.b.jobs"), f"jb:m:{st.row['user_id']}")                        # v0.18: the job board, in a new message under the map (owner-bound like every jb: button)
    if st.trip is None:
        rows.append([(t(lang, "mp.b.refresh"), cd(sid, "rf")), jobs])                  # still three compact rows when standing still
    else:
        rows.append([(t(lang, "mp.b.refresh"), cd(sid, "rf")), (t(lang, "mp.b.trip"), cd(sid, "tr"))])
        rows.append([jobs])
    return rows


def _back_row(lang: str, sid: str, back: str = "m") -> list:
    """The way back: to the list the place was opened from, or the map."""
    if back.startswith(("pl:", "ln:")):
        return [(t(lang, "mp.b.back"), cd(sid, *back.split(":"))), (t(lang, "mp.b.map"), cd(sid, "rf"))]
    if back == "nb":
        return [(t(lang, "mp.b.back"), cd(sid, "nb")), (t(lang, "mp.b.map"), cd(sid, "rf"))]
    return [(t(lang, "mp.b.map"), cd(sid, "rf"))]


def screen_main(lang: str, w, city, sid: str, st, others) -> Screen:
    text = views.map_caption(lang, w, city.layout, _status(lang, city, st, others))
    return Screen(text, _main_rows(lang, sid, st), view="map")


def _pager(lang: str, sid: str, act: str, page: int, pages: int) -> list:
    row = []
    if page > 0:
        row.append((t(lang, "mp.b.prev"), cd(sid, act, page - 1)))
    if page < pages - 1:
        row.append((t(lang, "mp.b.next"), cd(sid, act, page + 1)))
    return row


def screen_nearby(lang: str, w, city, sid: str, st, others) -> Screen:
    field = city.net.field(st.pos)
    places = sorted(((field.to(s.door), _num(s.id), s) for s in city.spots("landmark")), key=lambda x: (x[0], x[1]))
    lines = [t(lang, "mp.nb.title"), ""]
    if others:
        lines.append(t(lang, "mp.nb.people"))
        for o in others:
            lines.append(t(lang, "mp.nb.person", name=fmt.esc(short_name(o.name) or "…"), d=_dist(o.dist, lang)))
    else:
        lines.append(t(lang, "mp.nb.nobody", r=fmt.dist(C.nearby_radius(), lang)))
    lines += ["", t(lang, "mp.nb.places")]
    here = st.row["at_place"] if st.trip is None else None
    near = places[:4]
    for d, _n_, s in near:
        lines.append(t(lang, "mp.nb.place", name=t(lang, "map.lm." + s.landmark["kind"]), id=worldinspect.display_id(s.id), d=_dist(d, lang)) if s.id != here
                     else f"• {place_html(lang, s)} — {t(lang, 'mp.b.here')}")
    lines += ["", t(lang, "mp.nb.hint")]
    people = [(t(lang, "mp.nb.pbtn", name=short_name(o.name) or "…"), cd(sid, "pp", o.uid)) for o in others]          # v0.18.1: the players around you are buttons (hello / hug)
    rows = [people[i:i + 2] for i in range(0, len(people), 2)]
    if others:
        lines.insert(lines.index(t(lang, "mp.nb.places")) - 1, t(lang, "mp.nb.hint_people"))
    btns = [(ICONS.get(s.landmark["kind"], "📍") + " " + place_label(lang, s), cd(sid, "o", s.id)) for _d, _n_, s in near]
    rows += [btns[i:i + 2] for i in range(0, len(btns), 2)]
    rows.append([(t(lang, "mp.b.refresh"), cd(sid, "nb")), (t(lang, "mp.b.map"), cd(sid, "rf"))])
    return Screen("\n".join(lines), rows, view="nb")


def screen_person(lang: str, sid: str, o) -> Screen:
    """One player of the «اطراف من» list: say hello (instant) or ask for a hug (they must accept)."""
    text = t(lang, "mp.pp.title", name=fmt.esc(short_name(o.name) or "…"), d=_dist(o.dist, lang))
    rows = [[(t(lang, "mp.pp.b.greet"), cd(sid, "gr", o.uid)), (t(lang, "mp.pp.b.hug"), cd(sid, "hg", o.uid))],
            [(t(lang, "mp.b.back"), cd(sid, "nb")), (t(lang, "mp.b.map"), cd(sid, "rf"))]]
    return Screen(text, rows, view="pp", sel=str(o.uid), back="nb")


def _num(ident: str) -> int:
    return int(ident[1:]) if ident[1:].isascii() and ident[1:].isdigit() else 0


def _clamp(page, pages: int) -> int:
    try:
        page = int(page)
    except (TypeError, ValueError):
        page = 0
    return max(0, min(page, max(0, pages - 1)))


def screen_places(lang: str, w, city, sid: str, st, page) -> Screen:
    field = city.net.field(st.pos)
    items = sorted(((field.to(s.door), _num(s.id), s) for s in city.spots("landmark")), key=lambda x: (x[0], x[1]))
    pages = max(1, math.ceil(len(items) / C.MAP_PAGE))
    page = _clamp(page, pages)
    here = st.row["at_place"] if st.trip is None else None
    rows = []
    for d, _n_, s in items[page * C.MAP_PAGE:(page + 1) * C.MAP_PAGE]:
        name, ident, icon = t(lang, "map.lm." + s.landmark["kind"]), worldinspect.display_id(s.id), ICONS.get(s.landmark["kind"], "📍")
        label = t(lang, "mp.item.here", icon=icon, name=name, id=ident) if s.id == here else t(lang, "mp.item.place", icon=icon, name=name, id=ident, d=_dist(d, lang))
        rows.append([(label, cd(sid, "o", s.id))])
    nav = _pager(lang, sid, "pl", page, pages)
    if nav:
        rows.append(nav)
    rows.append([(t(lang, "mp.b.map"), cd(sid, "rf"))])
    text = t(lang, "mp.pl.title", p=fmt.num(page + 1, lang), n=fmt.num(pages, lang)) + "\n\n" + t(lang, "mp.pl.hint")
    return Screen(text, rows, view="pl", page=page)


def screen_lots(lang: str, w, city, sid: str, st, page) -> Screen:
    field = city.net.field(st.pos)
    items = []
    for p in city.layout["parcels"]:
        if p["use"] == "vacant":
            items.append((field.to(city.net.door(p["id"], p["c"])), _num(p["id"]), p))
    items.sort(key=lambda x: (x[0], x[1]))
    items = items[:C.MAP_MAX_LOTS]
    if not items:
        return Screen(t(lang, "mp.ln.none"), [[(t(lang, "mp.b.map"), cd(sid, "rf"))]], view="ln")
    pages = max(1, math.ceil(len(items) / C.MAP_PAGE))
    page = _clamp(page, pages)
    rows = []
    for d, _n_, p in items[page * C.MAP_PAGE:(page + 1) * C.MAP_PAGE]:
        label = t(lang, "mp.item.lot", id=worldinspect.display_id(p["id"]), zone=t(lang, "map.zone." + p["zone"]),
                  district=worldinspect.display_id(city.district_of(p)), d=_dist(d, lang))
        rows.append([("🌿 " + label, cd(sid, "o", p["id"]))])
    nav = _pager(lang, sid, "ln", page, pages)
    if nav:
        rows.append(nav)
    rows.append([(t(lang, "mp.b.map"), cd(sid, "rf"))])
    text = t(lang, "mp.ln.title", p=fmt.num(page + 1, lang), n=fmt.num(pages, lang)) + "\n\n" + t(lang, "mp.ln.hint")
    return Screen(text, rows, view="ln", page=page)


def screen_object(lang: str, w, city, sid: str, st, ref: str, back: str) -> Screen | None:
    """One place: the existing inspection panel for exactly this ID, then what walking there means for this player, and the buttons."""
    res = worldinspect.inspect(lang, w, city.layout, ref, notes=False)
    if res is None:
        return None
    spot = city.spot(res.id) if res.kind != "district" else None
    lines = [res.text]
    rows: list = []
    sel = res.id
    if spot is not None:
        sel = spot.id
        casino_here = spot.id == city.casino_id
        field = city.net.field(st.pos)
        d = field.to(spot.door)
        arrived_here = st.trip is None and st.row["at_place"] == spot.id
        lines.append("")
        if arrived_here:
            lines.append(t(lang, "mp.obj.here"))
        elif st.trip is not None and st.trip["dest_id"] == spot.id:
            lines.append(t(lang, "mp.obj.walking", pct=fmt.loc(_pct(st), lang), eta=fmt.dur(st.eta, lang)))
        elif st.trip is not None:
            lines.append(t(lang, "mp.obj.busy", dest=_dest_html(lang, city, st.trip["dest_id"])))
        else:
            lines.append(t(lang, "mp.obj.dist", d=_dist(d, lang), eta=fmt.dur(_eta(d), lang), speed=fmt.kmh(C.walk_speed(), lang)))
        if casino_here:
            lines.append(t(lang, "mp.obj.casino"))
            if not arrived_here:
                lines.append(t(lang, "mp.obj.casino_far"))
        if arrived_here and casino_here:
            rows.append([(t(lang, "mp.b.enter"), cd(sid, "ce"))])
        elif st.trip is not None:
            rows.append([(t(lang, "mp.b.trip"), cd(sid, "tr"))])
        elif not arrived_here:
            rows.append([(t(lang, "mp.b.go"), cd(sid, "go", spot.id))])
    lines += ["", t(lang, "mp.obj.foot")]
    rows.append(_back_row(lang, sid, back)[0:2])
    return Screen("\n".join(lines), rows, view="obj", sel=sel, back=back, hl=res.highlight, ref=res.id)


def screen_trip(lang: str, w, city, sid: str, st, others) -> Screen:
    if st.trip is None:
        return screen_main(lang, w, city, sid, st, others)
    left = float(st.trip["length"]) * (1.0 - st.progress)                      # from the saved trip: what is still to walk, at the speed the trip was saved with
    text = t(lang, "mp.tr.title") + "\n\n" + t(lang, "mp.tr.line", dest=_dest_html(lang, city, st.trip["dest_id"]), pct=fmt.loc(_pct(st), lang),
                                                left=fmt.dist(left, lang), speed=fmt.kmh(float(st.trip["speed"]), lang), eta=fmt.dur(st.eta, lang))
    rows = [[(t(lang, "mp.b.refresh_trip"), cd(sid, "tr")), (t(lang, "mp.b.map"), cd(sid, "rf"))]]
    return Screen(text, rows, view="tr")


# --- the casino ----------------------------------------------------------------------------------------------------------------------
def _cards(hand) -> str:
    return " ".join(hand)


def _tot(lang: str, hand) -> str:
    n, soft = casino.value(hand)
    return t(lang, "bj.soft", total=fmt.num(n, lang)) if soft and n < 21 else fmt.num(n, lang)


def _chips(lang: str, sid: str, seq: int, balance: int) -> list:
    """The chip buttons of the table: every chip the player can pay, three to a row (the press carries the game's seq and the chip's value)."""
    chips = [(t(lang, "bj.b.chip", amount=fmt.usd(b, lang)), cd(sid, "bj", "b", seq, b)) for b in C.CASINO_BETS_CENTS if b <= balance]
    return [chips[i:i + 3] for i in range(0, len(chips), 3)]


def _pick(lang: str, balance: int) -> str:
    """«Choose your bet.», or the one line saying the smallest chip is out of reach."""
    low = min(C.CASINO_BETS_CENTS)
    return t(lang, "bj.pick") if balance >= low else t(lang, "bj.poor", min=fmt.usd(low, lang))


def _bet_rows(lang: str, sid: str, seq: int, balance: int) -> list:
    """What is offered when no hand is running: the chips, and a way to read the rules or leave."""
    return _chips(lang, sid, seq, balance) + [[(t(lang, "bj.b.rules"), cd(sid, "bj", "i")), (t(lang, "mp.b.exit"), cd(sid, "rf"))]]


def _hand_lines(lang: str, h) -> list:
    if h.over:
        dealer = t(lang, "bj.dealer", cards=_cards(h.dealer), total=t(lang, "bj.total", total=_tot(lang, h.dealer)))
    else:
        dealer = t(lang, "bj.dealer", cards=f"{h.dealer[0]} {t(lang, 'bj.hidden')}", total="")
    return [dealer, t(lang, "bj.you", cards=_cards(h.player), total=_tot(lang, h.player))]


def screen_table(lang: str, sid: str, city, game, balance: int) -> Screen:
    """The casino, one screen for every state of the table (v0.18.2: concise, no rules on the screen, no separate lobby):
    * no hand yet, or the last one finished: the title, the cash balance and the chips (a finished hand shows its result first);
    * a hand running: the cards, whose move it is, and Hit / Stand only.
    `game` is the session's game row, or None before the first hand. The chips the player cannot pay are not offered."""
    if game is None:
        text = "\n".join([t(lang, "mp.cas.title"), t(lang, "bj.game"), "", t(lang, "bj.cash", cash=fmt.usd(balance, lang)), "", _pick(lang, balance)])
        return Screen(text, _bet_rows(lang, sid, 0, balance), view="bj", sel=city.casino_id, picture=False)
    h = casino.hand_of(game)
    bet = game["bet"]
    lines = [t(lang, "bj.head", bet=fmt.usd(bet, lang)), ""] + _hand_lines(lang, h) + [""]
    if not h.over:
        lines.append(t(lang, "bj.turn"))
        rows = [[(t(lang, "mp.b.hit"), cd(sid, "bj", "h", game["seq"])), (t(lang, "mp.b.stand"), cd(sid, "bj", "s", game["seq"]))]]
        return Screen("\n".join(lines), rows, view="bj", sel=city.casino_id, picture=False)
    lines.append(t(lang, "bj.r." + h.result))
    net = game["paid"] - bet
    lines.append(t(lang, "bj.net.win" if net > 0 else "bj.net.push" if net == 0 else "bj.net.lose", amount=fmt.usd(abs(net), lang)))
    lines += [t(lang, "bj.cash", cash=fmt.usd(balance, lang)), "", _pick(lang, balance)]
    return Screen("\n".join(lines), _bet_rows(lang, sid, game["seq"], balance), view="bj", sel=city.casino_id, picture=False)


def screen_rules(lang: str, sid: str, city) -> Screen:
    """The rules, only when the player asks for them (the button next to the chips); one button back to the table."""
    text = t(lang, "bj.rules", mins=fmt.num(max(1, C.CASINO_IDLE_SECS // 60), lang))
    return Screen(text, [[(t(lang, "bj.b.back"), cd(sid, "bj", "o"))]], view="bj", sel=city.casino_id, picture=False)


# =====================================================================================================================
#                                           turning a screen into a message (and the entry points)
# =====================================================================================================================
async def _reply(c, w, city, sid: str, sess_mid, st, others, scr: Screen, *, first: bool = False) -> Reply:
    db = c.db
    with db.tx():
        _save(db, sid, scr.view, scr.page, scr.sel, scr.back)

    def after(mid):
        set_message(db, sid, sess_mid, mid)
    toast = scr.toast
    if st.arrived is not None and not toast:
        toast = plain(t(c.lang, "mp.arrived", dest=_dest_html(c.lang, city, st.arrived["dest_id"])))
    if not scr.picture:
        return Reply(text=scr.text, kb=kb(scr.rows), edit=not first, toast=toast, alert=scr.alert, after=after)
    marks = _marks(c.lang, city, st, others, tag_dest=scr.view not in ("obj",), db=db)
    try:
        png = await worldmap.render_marked(city.id, city.layout, c.lang, marks, scr.hl, scr.ref)
    except Exception:
        if scr.hl is None:                                                    # the plain map itself cannot be drawn: the caller reports it
            raise
        log.exception("picture of %s in world %s failed", scr.ref, city.id)   # a place's panel is still worth sending without its picture
        return Reply(text=scr.text, kb=kb(scr.rows), edit=not first, toast=toast, alert=scr.alert, after=after)
    return Reply(photo=png, text=scr.text, kb=kb(scr.rows), edit=not first, toast=toast, alert=scr.alert, after=after)


def _others(db, city, st):
    return presence.nearby(db, city, st.row["user_id"], st.pos, st.now)


async def open_map(c, w, city, ref: str | None):
    """`مپ` (or `مپ P14`) typed in a group, or the menu's map button: a fresh session and its first message."""
    st = presence.stand(c.db, city, c.uid)
    others = _others(c.db, city, st)
    sid = new_session(c.db, city.id, c.chat.id, c.uid)
    if ref is None:
        scr = screen_main(c.lang, w, city, sid, st, others)
    else:
        scr = screen_object(c.lang, w, city, sid, st, ref, "m")
        if scr is None:
            return Reply(text=worldinspect.unknown_text(c.lang, city.layout, ref))
    return await _reply(c, w, city, sid, None, st, others, scr, first=True)


def _deny(c, why: str) -> Reply:
    return Reply(toast=plain(t(c.lang, DENY.get(why, "mp.stale"))), alert=True)


def _stale(c, key: str = "mp.stale") -> Reply:
    return Reply(toast=plain(t(c.lang, key)), alert=True)


async def handle(c):
    """Every map button. `c.args` = [sid, action, ...]; the session and its owner are verified first, always."""
    args = c.args
    msg = c.msg
    if len(args) < 2 or msg is None:
        return _deny(c, "gone")
    sid, act, rest = args[0], args[1], args[2:]
    sess, why = authorize(c.db, sid, c.uid, c.chat.id, msg.message_id)
    if why:
        return _deny(c, why)
    # v0.19: the session is bound to a city, and that city must still be the CHARACTER's current city. Which group the message lives in is the session's `chat_id`
    # (checked above); where the city was first created (`worlds.chat_id`) does not matter any more. A session of another city (a stale one) acts on nothing.
    ch = character.get(c.db, c.uid)
    if not character.is_done(ch) or ch["current_world_id"] != sess["world_id"]:
        return _deny(c, "world")
    w = c.db.q1("SELECT * FROM worlds WHERE id=?", (sess["world_id"],))
    if w is None:
        return _deny(c, "world")
    try:
        city = await worldplaces.aload_city(c.db, w)
    except world.WorldError:
        return Reply(toast=plain(t(c.lang, "map.corrupt")), alert=True)
    sess, why = authorize(c.db, sid, c.uid, c.chat.id, msg.message_id)           # nothing below runs on a session that vanished while the city loaded
    if why:
        return _deny(c, why)
    mid = msg.message_id
    st = presence.stand(c.db, city, c.uid)
    others = _others(c.db, city, st)
    lang = c.lang

    async def show(scr: Screen | None, **kw):
        if scr is None:
            return _stale(c)
        if kw.get("toast"):
            scr.toast, scr.alert = kw["toast"], kw.get("alert", False)
        return await _reply(c, w, city, sid, mid, st, others, scr)

    try:
        if act in ("rf", "home"):
            return await show(screen_main(lang, w, city, sid, st, others))
        if act == "nb":
            return await show(screen_nearby(lang, w, city, sid, st, others))
        if act in ("pp", "gr", "hg"):
            who = _uint(rest[0]) if len(rest) == 1 else None
            o = next((x for x in others if x.uid == who), None) if who is not None else None
            if o is None:                                                     # not in the list any more (walked away, hand-made button): show the real list
                return await show(screen_nearby(lang, w, city, sid, st, others), toast=plain(t(lang, "mp.pp.gone")))
            if act == "pp":
                return await show(screen_person(lang, sid, o))
            if sess["view"] != "pp" or sess["sel"] != str(o.uid):
                return _stale(c)                                              # the button of a screen that is no longer the current one
            if act == "gr":
                return interactui.greet_reply(c.db, lang, c.chat.id, c.uid, o.uid, plain(t(lang, "mp.pp.sent_greet")))
            return interactui.hug_reply(c.db, lang, city, c.chat.id, c.uid, o.uid, plain(t(lang, "mp.pp.sent_hug")))
        if act == "pl":
            return await show(screen_places(lang, w, city, sid, st, rest[0] if rest else 0))
        if act == "ln":
            return await show(screen_lots(lang, w, city, sid, st, rest[0] if rest else 0))
        if act == "tr":
            return await show(screen_trip(lang, w, city, sid, st, others))
        if act == "o":
            ref = (rest[0] if rest else "")
            if ref == "casino":
                ref = city.casino_id
            spot = city.spot(ref)
            if spot is None:
                return _stale(c, "mp.unknown")
            if sess["view"] in ("pl", "ln"):
                back = f"{sess['view']}:{sess['page']}"
            elif sess["view"] == "nb":
                back = "nb"
            elif sess["view"] == "obj":
                back = sess["back"]
            else:
                back = "m"
            return await show(screen_object(lang, w, city, sid, st, spot.id, back))
        if act == "go":
            ref = rest[0] if rest else ""
            if sess["view"] != "obj" or sess["sel"] != ref:
                return _stale(c)                                              # the button of a screen that is no longer the current one
            if work.in_ride(c.db, c.uid):                                     # a Snapp ride drives the player's one trip: no walking away from it
                return _stale(c, "mp.in_ride")
            out = presence.start_trip(c.db, city, c.uid, ref)
            if out.status == "unknown":
                return _stale(c, "mp.unknown")
            st = presence.stand(c.db, city, c.uid)                             # the saved trip is the authority: show what was saved
            others = _others(c.db, city, st)
            spot = city.spot(ref)
            if out.status == "started":
                eta = fmt.dur(out.trip["arrive_at"] - out.trip["started_at"], lang)
                msg_ = plain(t(lang, "mp.started", dest=place_html(lang, spot), eta=eta))
                return await show(screen_trip(lang, w, city, sid, st, others), toast=msg_)
            if out.status == "busy":
                msg_ = plain(t(lang, "mp.busy", dest=_dest_html(lang, city, st.trip["dest_id"] if st.trip else ref)))
                return await show(screen_trip(lang, w, city, sid, st, others) if st.trip is not None else screen_main(lang, w, city, sid, st, others), toast=msg_)
            if out.status == "here":
                return await show(screen_object(lang, w, city, sid, st, ref, sess["back"]), toast=plain(t(lang, "mp.here")))
            return await show(screen_object(lang, w, city, sid, st, ref, sess["back"]), toast=plain(t(lang, "mp.unreachable")), alert=True)
        if act == "ce":
            cid = city.casino_id
            if cid is None or sess["sel"] != cid or sess["view"] not in ("obj", "cas", "bj"):
                return _stale(c)
            if st.trip is not None:
                return _stale(c, "mp.cas.moving")
            if st.row["at_place"] != cid:
                return _stale(c, "mp.cas.need")                               # refreshing never replaces walking there
            game = casino.get(c.db, sid)
            if game is not None and (game["user_id"] != c.uid or game["world_id"] != city.id):
                return _stale(c)
            return await show(screen_table(lang, sid, city, game, cash.balance(c.db, c.uid)))     # the table itself: the chips, or the hand that was left running
        if act == "bj":
            return await _blackjack(c, w, city, sid, sess, st, others, rest, show)
    except GameError:
        raise                                                                 # a rule of the game («wait 20 s»): the dispatcher shows it as a toast
    except Exception:
        log.exception("map button %s failed", act)
        return Reply(toast=plain(t(lang, "mp.err")), alert=True)
    return _stale(c)


async def _blackjack(c, w, city, sid, sess, st, others, rest, show):
    lang, db = c.lang, c.db
    cid = city.casino_id
    if cid is None or sess["view"] not in ("cas", "bj") or sess["sel"] != cid:
        return _stale(c)
    if st.trip is not None or st.row["at_place"] != cid:                      # every press needs the player to be standing at the casino door
        return _stale(c, "mp.cas.left")
    what = rest[0] if rest else ""
    now = core.now()
    game = casino.get(db, sid)
    if game is not None and (game["user_id"] != c.uid or game["world_id"] != city.id):
        return _stale(c)

    def table(g, **kw):
        return show(screen_table(lang, sid, city, g, cash.balance(db, c.uid)), **kw)

    if what in ("o", "n"):                                                     # open the table: the hand in progress, or the chips for the next one ("n" is the old «again» button of v0.18)
        return await table(game)
    if what == "i":                                                            # the rules, on request (changes nothing)
        return await show(screen_rules(lang, sid, city))
    seq = _uint(rest[1]) if len(rest) >= 2 else None
    if seq is None:
        return _stale(c)
    if what in ("h", "s"):
        new = casino.act(db, sid, city.id, c.uid, "hit" if what == "h" else "stand", seq, now)
        if new is None:                                                        # stale or repeated press: nothing was applied; show the real state
            return await table(casino.get(db, sid), toast=plain(t(lang, "bj.stale")))
        return await table(new)
    if what == "b":
        bet = _uint(rest[2]) if len(rest) == 3 else None
        if bet not in C.CASINO_BETS_CENTS:                                     # a hand-made button: not a chip of this table
            return _stale(c)
        new = casino.start(db, sid, city.id, c.uid, seq, now, bet)             # GameError('cash_low'): the chip was not taken and no hand was dealt
        if new is None:
            return await table(casino.get(db, sid), toast=plain(t(lang, "bj.stale")))
        return await table(new)
    return _stale(c)
