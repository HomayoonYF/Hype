"""Character XP and level (v0.19): infrastructure only.

This is the character's OWN progression and has nothing to do with the stock game: the stock XP, the stock levels (`users.max_level`), rebirths, skills,
quests, balances and every ledger stay exactly where they are and are never read or written here. Only two tables are touched:
`character_xp` (the running total, one row per character) and `character_xp_log` (one row per grant, UNIQUE per (character, source, reference)).

* XP is a non-negative integer (a CHECK in the table). A new character has 0.
* The level is NOT stored. It is derived from the XP by ONE curve (`level_for`), so the two can never drift apart; change the curve here and every level follows.
  Level 1 is the start; level n begins at `CHAR_XP_LEVEL_STEP * (n - 1) ** 2` XP (100, 400, 900, ...).
* `grant` is the one way XP goes up, atomic and idempotent: the same (source, ref) pays once, whatever repeats or races. There is no way to spend or
  remove XP.

Nothing in v0.19 grants XP: no job, task, casino hand, greeting or login calls `grant` (a test searches the code for that). Later systems call it with a
stable source («job:mcd») and reference (the task id) and get the safety for free.
"""
from __future__ import annotations

import math
import sqlite3

from . import config as C, core
from .core import GameError

MAX_GRANT = 10 ** 9                       # one grant can never be larger than this (so no absurd value can ever overflow a total)
MAX_XP = 10 ** 15


def level_for(xp: int) -> int:
    """The level that `xp` reaches (1 for 0 XP). The only level curve."""
    if not isinstance(xp, int) or isinstance(xp, bool) or xp < 0:
        raise ValueError("xp must be a non-negative integer")
    return math.isqrt(xp // C.CHAR_XP_LEVEL_STEP) + 1


def xp_for_level(level: int) -> int:
    """The XP at which `level` begins (level 1 begins at 0)."""
    if not isinstance(level, int) or isinstance(level, bool) or level < 1:
        raise ValueError("level starts at 1")
    return C.CHAR_XP_LEVEL_STEP * (level - 1) ** 2


def get(db, uid: int) -> int | None:
    """The character's XP, or None when the player has no character."""
    r = db.q1("SELECT xp FROM character_xp WHERE user_id=?", (uid,))
    return r["xp"] if r else None


def level_of(db, uid: int) -> int | None:
    xp = get(db, uid)
    return None if xp is None else level_for(xp)


def grant(db, uid: int, amount: int, source: str, ref: str, now: int | None = None) -> int | None:
    """Add `amount` (> 0) XP to the character, once per (source, ref). Returns the new total, or None when this (source, ref) was already granted (nothing changes
    then). Raises GameError('no_character') for a player without a character and ValueError for a bad amount, source or reference."""
    if not isinstance(amount, int) or isinstance(amount, bool) or not 0 < amount <= MAX_GRANT:
        raise ValueError("an XP grant is a positive whole number")
    if not (isinstance(source, str) and isinstance(ref, str) and source.strip() and ref.strip() and len(source) <= 40 and len(ref) <= 80):
        raise ValueError("a grant needs a source and a reference")
    now = core.now() if now is None else now
    with db.tx():
        row = db.q1("SELECT xp FROM character_xp WHERE user_id=?", (uid,))
        if row is None:
            raise GameError("no_character")
        new = row["xp"] + amount
        if new > MAX_XP:
            new = MAX_XP
        try:
            db.x("INSERT INTO character_xp_log(user_id,source,ref,delta,xp_after,created_at) VALUES(?,?,?,?,?,?)", (uid, source, ref, new - row["xp"], new, now))
        except sqlite3.IntegrityError:                                   # the same grant again: already applied
            return None
        db.x("UPDATE character_xp SET xp=?, updated_at=? WHERE user_id=?", (new, now, uid))
        return new
