"""The messages of the glass-button interactions (v0.18.1): what the group sees when somebody says hello to, or asks for a hug from, a player
they picked in the map's «📍 اطراف من» list. The rules (cooldowns, consent, nearness) are in interact.py; the buttons are in mapui.py."""
from __future__ import annotations

from . import config as C, core, fmt, interact
from .texts import t
from .views import Reply, kb


def greet_reply(db, lang: str, chat_id: int, uid: int, to_uid: int, toast: str) -> Reply:
    """An instant greeting. The group gets a new message; the map message stays as it is. Raises GameError (ia_self / ia_unknown / ia_cooldown)."""
    to = interact.target(db, uid, to_uid)
    iid = interact.greet(db, chat_id, uid, to["id"])
    me = core.need_user(db, uid)
    return Reply(text=t(lang, f"ia.greet.{iid % 4}", a=interact.mention(me), b=interact.mention(to)), toast=toast)


def hug_reply(db, lang: str, city, chat_id: int, uid: int, to_uid: int, toast: str) -> Reply:
    """A hug REQUEST: the group gets a message with buttons that only the asked player can use to accept or decline (and only the sender to take
    it back). Raises GameError (ia_self / ia_unknown / ia_cooldown / ia_pending / ia_declined / ia_busy / ia_far)."""
    to = interact.target(db, uid, to_uid)
    row = interact.hug_request(db, city, chat_id, uid, to["id"])
    me = core.need_user(db, uid)
    rows = [[(t(lang, "ia.b.yes"), f"ia:y:{row['id']}:{to['id']}"), (t(lang, "ia.b.no"), f"ia:n:{row['id']}:{to['id']}")],
            [(t(lang, "ia.b.cancel"), f"ia:c:{row['id']}:{uid}")]]
    iid = row["id"]
    return Reply(text=t(lang, "ia.hug.ask", a=interact.mention(me), b=interact.mention(to), ttl=fmt.dur(C.HUG_TTL, lang)), kb=kb(rows), toast=toast,
                 after=lambda mid: interact.remember_message(db, iid, mid))
