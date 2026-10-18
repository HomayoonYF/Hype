"""The required-channel gate: the bot admin names a channel, and every other player must be a member of it to use the bot.

* The setting lives in the database (kv), so it is changed from the admin panel (`/admin`) at runtime: no restart, no .env edit.
  Only the accounts in ADMIN_IDS (.env) can reach the panel; admins themselves are never gated.
* A player's membership is asked of Telegram (`getChatMember`) and remembered for a few minutes, so an active player costs one API
  call per TTL, not one per message. The "I joined" button always asks again.
* The gate never locks the whole game by accident: if Telegram can't answer (the bot was removed from the channel, an outage...)
  players are let through and the admins get one alert an hour. A player Telegram says is not in the channel is always blocked.
"""
from __future__ import annotations

import logging
import re

from . import core
from .db import Database

log = logging.getLogger("hypemarket.gate")

OK_TTL = 600                    # seconds a "yes, a member" answer is trusted
UNKNOWN_TTL = 60                # seconds a "Telegram could not tell" answer is trusted (so an outage is not hammered)
NO_TTL = 10                     # seconds a "not a member" answer is trusted (so a player who keeps typing doesn't cost an API call each time)
NOTICE_EVERY = 90               # in a group, the same player is told to join at most this often
MEMBER_STATUSES = frozenset({"creator", "administrator", "member"})
CHANNEL_TYPES = frozenset({"channel", "supergroup"})
_KEYS = ("gate:chat", "gate:title", "gate:username", "gate:link", "gate:on")


def _word(v) -> str:
    """A Telegram status or chat type as a plain lower-case word, whether the library hands us a str or an enum member."""
    return str(getattr(v, "value", v) or "").split(".")[-1].lower()


class GateError(Exception):
    """code: bad_ref | not_found | not_channel | bot_not_admin | api"""

    def __init__(self, code: str, detail: str = ""):
        super().__init__(code if not detail else f"{code}: {detail}")
        self.code, self.detail = code, detail


# --- the stored setting -------------------------------------------------------------------------------------------------------
def get(db: Database) -> dict | None:
    """The saved channel (whether the gate is on or not), or None."""
    raw = core.kv_get(db, "gate:chat")
    if not raw:
        return None
    try:
        chat = int(raw)
    except ValueError:
        return None
    return {"chat": chat, "title": core.kv_get(db, "gate:title", ""), "username": core.kv_get(db, "gate:username", ""),
            "link": core.kv_get(db, "gate:link", ""), "on": core.kv_get(db, "gate:on", "0") == "1"}


def active(db: Database) -> dict | None:
    """The channel players must have joined right now, or None when the gate is off."""
    s = get(db)
    return s if s and s["on"] else None


def save(db: Database, info: dict, on: bool) -> None:
    with db.tx():
        core.kv_set(db, "gate:chat", info["id"])
        core.kv_set(db, "gate:title", info.get("title") or "")
        core.kv_set(db, "gate:username", info.get("username") or "")
        core.kv_set(db, "gate:link", info.get("link") or "")
        core.kv_set(db, "gate:on", "1" if on else "0")


def set_on(db: Database, on: bool) -> None:
    core.kv_set(db, "gate:on", "1" if on else "0")


def clear(db: Database) -> None:
    with db.tx():
        db.x("DELETE FROM kv WHERE key IN (%s)" % ",".join("?" * len(_KEYS)), _KEYS)


def label(s: dict) -> str:
    """How the channel is named to people: @username when public, else its title."""
    return f"@{s['username']}" if s.get("username") else (s.get("title") or str(s["chat"]))


# --- turning what the admin typed into a channel ----------------------------------------------------------------------------------
_USER = re.compile(r"^[A-Za-z][A-Za-z0-9_]{3,31}$")


def parse_ref(raw: str):
    """«@name», «name», «https://t.me/name», «t.me/name» -> "@name";  «-1001234567890» -> that number;  anything else -> None."""
    words = (raw or "").split() if isinstance(raw, str) else []
    if len(words) != 1:                                   # a channel name is one word: a sentence is not a channel
        return None
    s = words[0].strip("<>«»\"'")
    m = re.match(r"^(?:https?://)?(?:www\.)?(?:t|telegram)\.me/([^/?#]+)/?$", s, re.I)
    if m:
        s = m.group(1)
        if s.startswith("+") or s.lower() in ("joinchat", "c"):
            return None                                   # an invite link names no channel we can look up
    if re.fullmatch(r"-?\d{5,20}", s):
        n = int(s)
        return n if n < 0 else None                      # chat ids of channels and supergroups are negative
    s = s.lstrip("@")
    return "@" + s if _USER.match(s) else None


async def resolve(bot, raw) -> dict:
    """Look the channel up and make sure the gate can really work: it must be a channel (or supergroup) and the bot must be one of its
    administrators (Telegram only lets an administrator list a channel's members). Returns {id, title, username, link}."""
    if isinstance(raw, bool):
        ref = None
    elif isinstance(raw, int):
        ref = raw if raw < 0 else None
    elif isinstance(raw, str):
        ref = parse_ref(raw)
    else:
        ref = None
    if ref is None:
        raise GateError("bad_ref")
    try:
        chat = await bot.get_chat(ref)
    except Exception as e:
        text = str(e).lower()
        if "not found" in text or "invalid" in text or "username" in text:
            raise GateError("not_found", str(e)) from e
        raise GateError("api", str(e)) from e
    if _word(getattr(chat, "type", None)) not in CHANNEL_TYPES:
        raise GateError("not_channel")
    try:
        me = await bot.get_chat_member(chat.id, bot.id)
    except Exception as e:
        raise GateError("bot_not_admin", str(e)) from e
    if _word(getattr(me, "status", None)) not in ("administrator", "creator"):
        raise GateError("bot_not_admin")
    username = getattr(chat, "username", None) or ""
    link = f"https://t.me/{username}" if username else (getattr(chat, "invite_link", None) or "")
    if not link:
        try:
            link = await bot.export_chat_invite_link(chat.id) or ""
        except Exception:
            log.info("no invite link for channel %s (the bot may lack the invite right)", chat.id)
            link = ""
    return {"id": chat.id, "title": getattr(chat, "title", "") or "", "username": username, "link": link}


# --- who may pass ---------------------------------------------------------------------------------------------------------------
def _prune(d: dict, now: int, limit: int = 20000) -> None:
    if len(d) > limit:
        for k in [k for k, v in d.items() if v <= now]:
            del d[k]
        if len(d) > limit:
            d.clear()


async def membership(app, uid: int, fresh: bool = False) -> str:
    """"yes" | "no" | "unknown" for one player. Admins and an inactive gate are always "yes"."""
    s = active(app.db)
    if not s or uid in app.settings.admins:
        return "yes"
    now = core.now()
    if not fresh:
        if app.gate_ok.get(uid, 0) > now:
            return "yes"
        if app.gate_no.get(uid, 0) > now:
            return "no"
    try:
        m = await app.bot.get_chat_member(s["chat"], uid)
    except Exception as e:
        text = str(e).lower()
        if "user not found" in text or "participant" in text:       # Telegram says this person is not part of the channel
            app.gate_ok.pop(uid, None)
            app.gate_no[uid] = now + NO_TTL
            return "no"
        log.warning("channel gate could not check player %s in %s: %s", uid, s["chat"], e)
        await app.alert_admins(f"Channel gate: I can't check membership of «{label(s)}» ({type(e).__name__}: {e}). "
                               "Players are being let in until this is fixed. Make sure the bot is still an administrator of the channel (/admin → Channel).",
                               key="gate-api", every=3600)
        app.gate_ok[uid] = now + UNKNOWN_TTL
        app.gate_no.pop(uid, None)
        _prune(app.gate_ok, now)
        return "unknown"
    status = _word(getattr(m, "status", ""))
    if status in MEMBER_STATUSES or (status == "restricted" and bool(getattr(m, "is_member", False))):
        app.gate_ok[uid] = now + OK_TTL
        app.gate_no.pop(uid, None)
        _prune(app.gate_ok, now)
        return "yes"
    app.gate_ok.pop(uid, None)
    app.gate_no[uid] = now + NO_TTL
    _prune(app.gate_no, now)
    return "no"


def forget(app) -> None:
    """Drop every remembered answer (the channel changed, or the gate was switched)."""
    app.gate_ok.clear()
    app.gate_no.clear()


def may_notify(app, chat_id: int, uid: int, group: bool) -> bool:
    """Private chats are always told; in a group the same player is told once every NOTICE_EVERY seconds (no spam)."""
    if not group:
        return True
    now = core.now()
    key = (chat_id, uid)
    if app.gate_told.get(key, 0) > now:
        return False
    app.gate_told[key] = now + NOTICE_EVERY
    _prune(app.gate_told, now)
    return True
