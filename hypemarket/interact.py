"""Player-to-player interactions (v0.18; since v0.18.1 they live ONLY behind glass buttons, there are no typed «سلام کن» / «بغل کن» commands).

How a player reaches them: map -> «📍 اطراف من» lists the players around you as buttons (Jack, John, ...); pressing a name opens that player with
two buttons, «👋 سلام» and «🤗 بغل». See mapui.py (screens `nb` / `pp`) and interactui.py (the messages).

A GREETING is instant and needs no answer: one player says hello to another in the group. It is a row of `interactions` (kind 'greet', state
'sent') and the bot's one-line message; the only rule is a short cooldown per sender, taken from that table, so it survives a restart.

A HUG is a REQUEST and nothing happens without the other player's consent. The request is a row (kind 'hug', state 'pending') with an expiry,
shown with buttons that only the TARGET can use to accept or decline (and only the sender to take it back). The state moves with guarded
`UPDATE ... WHERE state='pending'`, so a repeated, late or duplicated press matches nothing; expiry is lazy (a press, or the next request,
finds the row past `expires_at` and marks it expired). Hugs need the two players to be near each other in the city they both live in (config.hug_need_near(),
on by default; the nearness is the v0.17 road distance, checked when asking AND when accepting). v0.19: a city is shared by the players of many groups, and the
message of a greeting or a hug request is posted in the group of the player who sends it, so both need the other player to be in THAT group too (`shares_chat`). Cooldowns, one pending request per sender,
a cap on pending requests per target and a pause after a «no» keep it from being used to pester someone.

Nothing here touches money, stocks, levels or any other part of the game: these are social messages and nothing else.
"""
from __future__ import annotations

import html
import sqlite3

from . import character, config as C, core, presence
from .core import GameError

MAX_INCOMING = 3                     # pending hug requests one player can have waiting at the same time
PAIR_PAUSE = 600                     # seconds before the same sender may ask the same player again after a «no»


def mention(u) -> str:
    """A clickable name (it notifies the player in a group)."""
    return f'<a href="tg://user?id={int(u["id"])}">{html.escape(core.display_name(u, with_badge=False), quote=False)}</a>'


# =====================================================================================================================
#                                                       who is the target
# =====================================================================================================================
def shares_chat(db, chat_id: int, uid: int, now: int | None = None) -> bool:
    """Is player `uid` known to be in group `chat_id`: seen chatting there (`group_members`) or holding a live map message there? A greeting or a hug request is
    a message in ONE group, so it only makes sense for somebody who can see that group."""
    now = core.now() if now is None else now
    return (db.q1("SELECT 1 FROM group_members WHERE group_id=? AND user_id=?", (chat_id, uid)) is not None
            or db.q1("SELECT 1 FROM map_sessions WHERE chat_id=? AND user_id=? AND expires_at>?", (chat_id, uid, now)) is not None)


def target(db, uid: int, to, chat_id: int | None = None) -> object:
    """The row of the player `to` (picked from the «اطراف من» list of the map: a Telegram user id), or a GameError: ia_self (yourself),
    ia_unknown (not a player of the game, a bot, or banned), ia_elsewhere (when `chat_id` is given: not a member of that group). Whether they are really near is
    the caller's job (the map checks the list it just drew; hugs check the road distance again)."""
    if not isinstance(to, int) or isinstance(to, bool):
        raise GameError("ia_unknown")
    if to == uid:
        raise GameError("ia_self")
    u = core.get_user(db, to)
    if u is None or u["bot"] or u["banned"]:
        raise GameError("ia_unknown")
    if chat_id is not None and not shares_chat(db, chat_id, to):
        raise GameError("ia_elsewhere")
    return u


# =====================================================================================================================
#                                                        greetings
# =====================================================================================================================
def _last(db, uid: int, kind: str) -> int:
    r = db.q1("SELECT MAX(created_at) FROM interactions WHERE from_id=? AND kind=?", (uid, kind))
    return r[0] if r and r[0] else 0


def greet(db, chat_id: int, uid: int, to: int, now: int | None = None) -> int:
    """Record a greeting (instant, no answer needed) and return its id. Raises GameError ia_cooldown (carries the seconds left)."""
    now = core.now() if now is None else now
    with db.tx():
        left = _last(db, uid, "greet") + C.GREET_COOLDOWN - now
        if left > 0:
            raise GameError("ia_cooldown", secs=left)
        cur = db.x("INSERT INTO interactions(kind,chat_id,from_id,to_id,state,created_at,answered_at) VALUES('greet',?,?,?,'sent',?,?)", (chat_id, uid, to, now, now))
        return cur.lastrowid


# =====================================================================================================================
#                                                        hugs
# =====================================================================================================================
def get(db, iid) -> object | None:
    if not isinstance(iid, int) or isinstance(iid, bool):
        return None
    return db.q1("SELECT * FROM interactions WHERE id=?", (iid,))


def _expire_due(db, now: int, uid: int | None = None) -> None:
    q, a = "UPDATE interactions SET state='expired', answered_at=? WHERE kind='hug' AND state='pending' AND expires_at<=?", [now, now]
    if uid is not None:
        q += " AND from_id=?"
        a.append(uid)
    db.x(q, tuple(a))


def near(db, city, a: int, b: int, now: int) -> bool:
    """Are players `a` and `b` within the map's nearby radius of each other (road distance) in this city? Both must be finished characters whose current city
    this is (a character is present in one city only); players who never opened the map are put at their fixed spawn spot first, exactly as opening the map would."""
    if not (character.in_world(db, a, city.id) and character.in_world(db, b, city.id)):
        return False
    sa, sb = presence.stand(db, city, a, now), presence.stand(db, city, b, now)
    return city.net.field(sa.pos, cutoff=C.nearby_radius()).to_point(sb.pos) <= C.nearby_radius()


def hug_request(db, city, chat_id: int, uid: int, to: int, now: int | None = None):
    """Ask player `to` for a hug. Returns the new pending row. Raises GameError: ia_cooldown, ia_pending (you already have a request open),
    ia_declined (they said no a moment ago), ia_busy (they already have several waiting), ia_far (not near each other)."""
    now = core.now() if now is None else now
    with db.tx():
        _expire_due(db, now)
        left = _last(db, uid, "hug") + C.HUG_COOLDOWN - now
        if left > 0:
            raise GameError("ia_cooldown", secs=left)
        if db.q1("SELECT 1 FROM interactions WHERE kind='hug' AND from_id=? AND state='pending'", (uid,)) is not None:
            raise GameError("ia_pending")
        no = db.q1("SELECT MAX(answered_at) FROM interactions WHERE kind='hug' AND from_id=? AND to_id=? AND state='declined'", (uid, to))
        if no and no[0] and no[0] + PAIR_PAUSE > now:
            raise GameError("ia_declined")
        if db.q1("SELECT COUNT(*) FROM interactions WHERE kind='hug' AND to_id=? AND state='pending'", (to,))[0] >= MAX_INCOMING:
            raise GameError("ia_busy")
        if C.hug_need_near() and not near(db, city, uid, to, now):
            raise GameError("ia_far")
        try:
            cur = db.x("INSERT INTO interactions(kind,chat_id,from_id,to_id,state,created_at,expires_at) VALUES('hug',?,?,?,'pending',?,?)", (chat_id, uid, to, now, now + C.HUG_TTL))
        except sqlite3.IntegrityError:                                    # the partial unique index: the same pair already has a pending request
            raise GameError("ia_pending")
        return get(db, cur.lastrowid)


def remember_message(db, iid: int, message_id: int) -> None:
    db.x("UPDATE interactions SET message_id=? WHERE id=? AND message_id IS NULL", (message_id, iid))


NEW_STATE = {"y": "accepted", "n": "declined", "c": "cancelled"}


def hug_answer(db, city, chat_id: int, iid, presser: int, action: str, now: int | None = None) -> tuple:
    """Apply a button press to a hug request. Returns (row, changed): `changed` is True only when THIS call moved it out of «pending».
    A repeated, late or stale press returns the row as it really is, with changed False, and changes nothing. Raises GameError: ia_gone (no such
    request here), ia_notyours (only the target may accept / decline, only the sender may take it back), ia_far (accepting while not near)."""
    now = core.now() if now is None else now
    if action not in NEW_STATE:
        raise GameError("ia_gone")
    with db.tx():
        r = get(db, iid)
        if r is None or r["kind"] != "hug" or r["chat_id"] != chat_id:
            raise GameError("ia_gone")
        if r["from_id" if action == "c" else "to_id"] != presser:
            raise GameError("ia_notyours")
        if r["state"] != "pending":
            return r, False
        if r["expires_at"] is not None and r["expires_at"] <= now:
            db.x("UPDATE interactions SET state='expired', answered_at=? WHERE id=? AND state='pending'", (now, iid))
            return get(db, iid), False
        if action == "y" and C.hug_need_near() and not near(db, city, r["from_id"], r["to_id"], now):
            raise GameError("ia_far")
        if db.x("UPDATE interactions SET state=?, answered_at=? WHERE id=? AND state='pending' AND expires_at>?", (NEW_STATE[action], now, iid, now)).rowcount != 1:
            return get(db, iid), False
        return get(db, iid), True
