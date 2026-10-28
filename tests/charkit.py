"""Helpers for the character tests (v0.19)."""
from hypemarket import character, core


def complete(db, uid, first=None, last="Tester", gender="male", world_id=None, now=None):
    """A FINISHED character for `uid` without going through the chat: the profile (ID, traits, XP) is created the normal way, the names are written straight
    into the pending row (so the legacy suites can keep the digits of their `User777001` style names, which the real name rule refuses), and the gender step
    finishes it exactly like the button does. `world_id` also gives it its city. Idempotent: an existing character is returned unchanged."""
    ch = character.ensure_profile(db, uid, now)
    if ch is None:
        return None
    if ch["setup_state"] != "done":
        first = first or core.get_user(db, uid)["name"] or "Player"
        db.x("UPDATE characters SET first_name=?, last_name=?, setup_state='gender' WHERE user_id=? AND setup_state IN ('first','last','gender')", (first, last, uid))
        character.choose_gender(db, uid, gender, now)
    if world_id is not None:
        character.assign_world(db, uid, world_id, now)
    return character.get(db, uid)


def fake_world(db, chat_id, name="Testville"):
    """A bare `worlds` row (no real layout) for tests that only need a city to refer to; returns its id."""
    cur = db.x("INSERT INTO worlds(chat_id,seed,gen_version,name_fa,name_en,cols,rows,layout,created_at) VALUES(?,?,?,?,?,?,?,'{}',?)",
               (chat_id, chat_id & 0xFFFF, 2, name, name, 10, 10, 1_700_000_000))
    return cur.lastrowid
