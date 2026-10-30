"""The permanent character (v0.19): who a player is in the life game, and which city they are in.

ONE character per Telegram user (`characters.user_id` is the user's Telegram ID, the primary key). It is created once and never duplicated, whatever the
group, the menu, a restart, a changed Telegram name or a new map session. What it holds, and ONLY this (everything else has its own table and module):

* **identity**: a citizen ID, a first and a last name, a gender chosen by the player, the age anchor, the birthplace and the current city.
  Genes and the visible traits are in `traits.py`; character XP is in `charxp.py`; the calendar is `gamecal.py`; the body condition and the portrait are
  future systems that do not exist yet.
* **citizen ID**: zero-based and sequential. It comes from the one-row counter `citizen_seq`, read and advanced inside the same write transaction that creates
  the character (`BEGIN IMMEDIATE`: two creators can never read the same number, a rolled-back creation gives the number back, a restart cannot reset it), and
  `characters.citizen_id` is UNIQUE. Rows are never deleted (a trigger refuses), so an ID is never reused. It is NOT the Telegram ID.
* **set-up** (`setup_state`): first -> last -> gender -> done. Each step is saved the moment it happens, so the profile, its ID and its traits survive a
  restart, a closed chat or a second device; choosing the gender is the last step and finishes the profile exactly once (a guarded UPDATE: a repeated button
  changes nothing), which also starts the age: `age_started_at` = the server's clock at that moment. Until then the character has no age.
* **cities**: `birth_world_id` is the city the character first spawned in and never changes (a trigger refuses). `current_world_id` is the ONE authoritative
  city: the map, the jobs, the casino, the people near you and every saved position / trip are read from it, whatever Telegram group the player happens to be
  typing in. A new character gets it at their first life-game entry (`assign_world`, once); a legacy player gets it from the migration (`legacy_choice`).
  `worlds.chat_id` keeps meaning «the group this city was first created for» and is never rewritten; the old per-group rows of `world_players` / `world_trips`
  stay where they are (nothing is deleted) but only the row of the current city is ever read for a character.

The functions take the `Database` (or the migration's bare stand-in for it) and keep each state change in one transaction. They raise `GameError` with a
player-facing code (`e.<code>` texts). Nothing here touches stock money, stock XP, levels, life cash or any ledger.
"""
from __future__ import annotations

import logging
import random
import re
import unicodedata

from . import charxp, config as C, core, gamecal, traits
from .core import GameError

log = logging.getLogger("hypemarket")

STATES = ("first", "last", "gender", "done")
GENDERS = ("male", "female")
_SEPARATORS = " -'‌"                                   # between the letters of ONE name: space, hyphen, apostrophe, half-space (ZWNJ)
_FIXES = str.maketrans({"ي": "ی", "ك": "ک", "ى": "ی"})   # Arabic forms of Persian letters, unified like the command parser does


# =====================================================================================================================
#                                                      names
# =====================================================================================================================
def _script(ch: str) -> str | None:
    try:
        name = unicodedata.name(ch)
    except ValueError:
        return None
    return "arabic" if name.startswith("ARABIC") else "latin" if name.startswith("LATIN") else None


def clean_name(raw) -> str:
    """A first or last name as it is saved, or GameError('char_name').

    Normalised: Unicode NFKC, Arabic ي/ك/ى become Persian ی/ک/ی, diacritics and tatweel are dropped, runs of spaces become one, the ends are trimmed. Rejected,
    not repaired: anything that is not text, line breaks and control or invisible formatting characters (bidi marks, zero-width joiners), digits, symbols, emoji,
    links and «@», names shorter than CHAR_NAME_MIN or longer than CHAR_NAME_MAX, a separator (space, hyphen, apostrophe, half-space) at an edge or next to another,
    and a name that mixes the Persian and the Latin alphabet. The saved text keeps the player's own letters and capitals."""
    if not isinstance(raw, str):
        raise GameError("char_name", min=C.CHAR_NAME_MIN, max=C.CHAR_NAME_MAX)
    bad = GameError("char_name", min=C.CHAR_NAME_MIN, max=C.CHAR_NAME_MAX)
    if len(raw) > 4 * C.CHAR_NAME_MAX:
        raise bad
    for ch in raw:
        cat = unicodedata.category(ch)
        if cat == "Cc" or cat in ("Zl", "Zp") or (cat == "Cf" and ch != "‌"):
            raise bad                                         # control characters, line breaks, bidi / zero-width tricks
    text = unicodedata.normalize("NFKC", raw).translate(_FIXES)
    text = "".join(ch for ch in text if unicodedata.category(ch) != "Mn" and ch != "ـ")
    text = "".join(" " if unicodedata.category(ch) == "Zs" else ch for ch in text)
    text = re.sub(r" {2,}", " ", text).strip(" ‌")
    if not C.CHAR_NAME_MIN <= len(text) <= C.CHAR_NAME_MAX:
        raise bad
    scripts, letters, prev_sep = set(), 0, True               # prev_sep starts True: no separator may open the name
    for ch in text:
        if ch in _SEPARATORS:
            if prev_sep:
                raise bad
            prev_sep = True
            continue
        if not unicodedata.category(ch).startswith("L") or _script(ch) is None:
            raise bad                                         # digits, punctuation, symbols, emoji, other alphabets
        scripts.add(_script(ch))
        letters += 1
        prev_sep = False
    if prev_sep or letters < C.CHAR_NAME_MIN or len(scripts) != 1:
        raise bad
    if "‌" in text and scripts != {"arabic"}:
        raise bad                                             # a half-space only has a meaning inside Persian words
    return text


# =====================================================================================================================
#                                                  the profile row
# =====================================================================================================================
def get(db, uid: int):
    return db.q1("SELECT * FROM characters WHERE user_id=?", (uid,))


def by_citizen(db, citizen_id: int):
    return db.q1("SELECT * FROM characters WHERE citizen_id=?", (citizen_id,))


def is_done(ch) -> bool:
    return ch is not None and ch["setup_state"] == "done"


def _next_citizen_id(db) -> int:
    """The next unused citizen ID, taken from the counter. Must run inside a write transaction (every caller is in `db.tx()`): the counter row is
    then locked against every other writer until commit, so no two characters can be given the same number."""
    r = db.q1("SELECT next_id FROM citizen_seq WHERE id=1")
    if r is None:
        raise RuntimeError("the citizen counter is missing")
    n = r["next_id"]
    if db.x("UPDATE citizen_seq SET next_id=? WHERE id=1 AND next_id=?", (n + 1, n)).rowcount != 1:
        raise RuntimeError("the citizen counter changed under a write transaction")
    return n


def _insert(db, uid: int, now: int, rng, *, origin: str = "new", birth: int | None = None, current: int | None = None, basis: str = "spawn") -> None:
    """The one place a character is born: its ID, its empty profile, its 0 XP and its genes / traits, all in the caller's transaction."""
    cid = _next_citizen_id(db)
    db.x("INSERT INTO characters(user_id,citizen_id,origin,birth_basis,birth_world_id,current_world_id,created_at,updated_at) VALUES(?,?,?,?,?,?,?,?)",
         (uid, cid, origin, basis, birth, current, now, now))
    db.x("INSERT INTO character_xp(user_id,xp,updated_at) VALUES(?,0,?)", (uid, now))
    traits.save(db, uid, traits.roll_genes(rng))


def ensure_profile(db, uid: int, now: int | None = None, rng: random.Random | None = None):
    """The player's character, created (once) if they have none: a pending profile with its citizen ID and its traits, no names yet, no age yet, no city yet.
    Idempotent and race-safe. Returns None for a bot account or an unknown user (they never get a character)."""
    ch = get(db, uid)
    if ch is not None:
        return ch
    u = core.get_user(db, uid)
    if u is None or u["bot"]:
        return None
    now = core.now() if now is None else now
    with db.tx():
        ch = get(db, uid)
        if ch is None:
            _insert(db, uid, now, rng)
            log.info("character created for user %s", uid)
        return get(db, uid)


# =====================================================================================================================
#                                                      set-up
# =====================================================================================================================
def submit_name(db, uid: int, raw, now: int | None = None):
    """The player's next typed name: the first name while the set-up is at «first», the last name at «last». Returns the updated row. GameError: char_none (no
    profile), char_state (not waiting for a name), char_name (not an acceptable name)."""
    now = core.now() if now is None else now
    name = clean_name(raw)
    with db.tx():
        ch = get(db, uid)
        if ch is None:
            raise GameError("char_none")
        if ch["setup_state"] == "first":
            ok = db.x("UPDATE characters SET first_name=?, setup_state='last', updated_at=? WHERE user_id=? AND setup_state='first'", (name, now, uid)).rowcount
        elif ch["setup_state"] == "last":
            ok = db.x("UPDATE characters SET last_name=?, setup_state='gender', updated_at=? WHERE user_id=? AND setup_state='last'", (name, now, uid)).rowcount
        else:
            raise GameError("char_state")
        if ok != 1:
            raise GameError("char_state")
        return get(db, uid)


def restart_names(db, uid: int, now: int | None = None):
    """«Change the name»: back to typing the first name (the old names stay until they are replaced). Only while the set-up is not finished; a finished character
    keeps its names (the row is returned unchanged)."""
    now = core.now() if now is None else now
    with db.tx():
        ch = get(db, uid)
        if ch is None:
            raise GameError("char_none")
        db.x("UPDATE characters SET setup_state='first', updated_at=? WHERE user_id=? AND setup_state IN ('last','gender')", (now, uid))
        return get(db, uid)


def choose_gender(db, uid: int, gender, now: int | None = None) -> tuple:
    """The last step: record the gender and FINISH the profile, exactly once. Returns (row, finished_now): a repeated press, or a press by a player whose
    character is already finished, changes nothing and returns (row, False). GameError: char_none, char_gender (not male / female), char_state (the names are
    not both given yet)."""
    now = core.now() if now is None else now
    if gender not in GENDERS:
        raise GameError("char_gender")
    with db.tx():
        ch = get(db, uid)
        if ch is None:
            raise GameError("char_none")
        if ch["setup_state"] == "done":
            return ch, False
        if ch["setup_state"] != "gender":
            raise GameError("char_state")
        n = db.x("UPDATE characters SET gender=?, setup_state='done', age_started_at=?, updated_at=? WHERE user_id=? AND setup_state='gender'",
                 (gender, now, now, uid)).rowcount
        return get(db, uid), n == 1


# =====================================================================================================================
#                                                       age
# =====================================================================================================================
def age(ch, now: int | None = None) -> int | None:
    """The character's age as of `now` (the server clock by default): 15 + one year per four complete real days since the profile was finished. None while the
    set-up is not finished (nobody ages before they exist)."""
    if ch is None or ch["age_started_at"] is None:
        return None
    return gamecal.age_at(ch["age_started_at"], core.now() if now is None else now)


# =====================================================================================================================
#                                                  the current city
# =====================================================================================================================
def assign_world(db, uid: int, world_id: int, now: int | None = None):
    """Give a character without a city its first one: it becomes both the birthplace and the current city, once (this happens at the player's first life-game entry in
    a group, even before the set-up is finished, so an abandoned set-up resumes with the same birthplace). A character that already has a city keeps it (the row is
    returned unchanged), so no later group can replace it. GameError: char_none, char_world (no such city)."""
    now = core.now() if now is None else now
    with db.tx():
        ch = get(db, uid)
        if ch is None:
            raise GameError("char_none")
        if ch["current_world_id"] is not None:
            return ch
        if db.q1("SELECT 1 FROM worlds WHERE id=?", (world_id,)) is None:
            raise GameError("char_world")
        db.x("UPDATE characters SET birth_world_id=?, current_world_id=?, updated_at=? WHERE user_id=? AND current_world_id IS NULL AND birth_world_id IS NULL",
             (world_id, world_id, now, uid))
        return get(db, uid)


def current_world(db, uid: int):
    """The `worlds` row of the character's current city, or None (no character, no city yet, or the row is gone)."""
    return db.q1("SELECT w.* FROM characters c JOIN worlds w ON w.id=c.current_world_id WHERE c.user_id=?", (uid,))


def in_world(db, uid: int, world_id: int) -> bool:
    """Is `uid` a finished character whose authoritative city is `world_id`, and who is not on a journey between cities? (The only players that are present in a city: v0.20, a
    traveller is on the road, in no city, until the arrival is applied.)"""
    return db.q1("SELECT 1 FROM characters WHERE user_id=? AND setup_state='done' AND current_world_id=? "
                 "AND NOT EXISTS(SELECT 1 FROM intercity_trips WHERE user_id=? AND state='travelling')", (uid, world_id, uid)) is not None


def first_name(db, uid: int) -> str | None:
    """The in-world display name: the first name of a finished character."""
    r = db.q1("SELECT first_name FROM characters WHERE user_id=? AND setup_state='done'", (uid,))
    return r["first_name"] if r else None


# =====================================================================================================================
#                                       legacy players (the v0.19 migration)
# =====================================================================================================================
def legacy_choice(db, uid: int) -> dict | None:
    """Where a player of the old per-group model belongs, from what the old tables hold. Pure read; None when no saved city of theirs still exists.

    CURRENT city, first rule that applies:
      1. the city of their open work task (a job in progress continues where it began; there is at most one open task per player);
      2. else the city of their active (state «walking») trip: the most recently started one, ties to the lower city ID (a trip whose arrival time has passed
         but was never applied still counts: arrival is applied lazily, the next time the player acts);
      3. else the city of their oldest saved presence (`world_players.created_at`), ties to the lower city ID; if they have no presence row at all, the oldest
         other saved trace (trip, task, casino hand or map session) by the same ordering.
    BIRTH city: the oldest saved presence by the same ordering (basis `legacy_presence`), else the oldest other trace (`legacy_evidence`), else the current city
    (`legacy_current`). The old database never recorded a real birthplace, so for legacy players this is an APPROXIMATION and `characters.birth_basis` says so.
    Nothing is read about money, stocks or XP and nothing is written."""
    valid = {r["id"] for r in db.qa("SELECT id FROM worlds")}

    def oldest(rows):
        rows = [(r[1], r[0]) for r in rows if r[0] in valid]
        return min(rows)[1] if rows else None

    presence = db.qa("SELECT world_id, created_at FROM world_players WHERE user_id=?", (uid,))
    traces = (db.qa("SELECT world_id, started_at FROM world_trips WHERE user_id=?", (uid,)) + db.qa("SELECT world_id, created_at FROM work_tasks WHERE user_id=?", (uid,))
              + db.qa("SELECT world_id, created_at FROM casino_games WHERE user_id=?", (uid,)) + db.qa("SELECT world_id, created_at FROM map_sessions WHERE user_id=?", (uid,)))
    first_seen = oldest(presence)
    first_trace = oldest(presence + traces)
    task = [r["world_id"] for r in db.qa("SELECT world_id FROM work_tasks WHERE user_id=? AND state='open' ORDER BY id", (uid,)) if r["world_id"] in valid]
    walking = sorted((-r["started_at"], r["world_id"]) for r in db.qa("SELECT world_id, started_at FROM world_trips WHERE user_id=? AND state='walking'", (uid,))
                     if r["world_id"] in valid)
    if task:
        current, rule = task[0], "task"
    elif walking:
        current, rule = walking[0][1], "trip"
    elif first_seen is not None:
        current, rule = first_seen, "presence"
    elif first_trace is not None:
        current, rule = first_trace, "evidence"
    else:
        return None
    if first_seen is not None:
        birth, basis = first_seen, "legacy_presence"
    elif first_trace is not None:
        birth, basis = first_trace, "legacy_evidence"
    else:
        birth, basis = current, "legacy_current"
    return {"current": current, "birth": birth, "basis": basis, "rule": rule}


def migrate_legacy(db, rng: random.Random | None = None) -> int:
    """The v0.19 migration step: a PENDING character (citizen ID, traits, 0 XP, birthplace and current city by `legacy_choice`, names and gender still to be chosen) for
    every human player who has saved world activity and no character yet, in ascending Telegram user ID, so the IDs are 0, 1, 2, ... in that order without gaps.
    Players with no activity get theirs at their first life-game entry; bots never get one. Writes only new rows. Safe to repeat: a player who has a character is
    skipped. Returns how many characters were created."""
    rng = rng or random.SystemRandom()
    now = core.now()
    ids = [r["id"] for r in db.qa(
        "SELECT id FROM users WHERE bot=0 AND id NOT IN (SELECT user_id FROM characters) AND id IN ("
        "SELECT user_id FROM world_players UNION SELECT user_id FROM world_trips UNION SELECT user_id FROM work_tasks "
        "UNION SELECT user_id FROM casino_games UNION SELECT user_id FROM map_sessions) ORDER BY id")]
    made = 0
    for uid in ids:
        choice = legacy_choice(db, uid)
        if choice is None:
            continue
        _insert(db, uid, now, rng, origin="legacy", birth=choice["birth"], current=choice["current"], basis=choice["basis"])
        made += 1
    return made
