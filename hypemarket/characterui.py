"""The messages and buttons of the character set-up (v0.19), and the rules for who may do what in it.

The set-up lives in the database (`characters.setup_state`: first -> last -> gender -> done, see character.py), never in memory, so it survives a restart, a
closed chat and a second device. It is done in the PRIVATE chat with the bot: a player's chosen names are never typed into a group. Three ways in:

* a group: the first life-game command there (`مپ`, a job) creates the pending profile and answers with a short message and a button that opens the private chat
  (`https://t.me/<bot>?start=char`); the group never sees a name;
* the private chat: `/start char` (that button), and any later `/start`, shows the step the player is at;
* in the private chat, while the step is «first» or «last», the player's next PLAIN message is the name (a message that is a command still runs as a command).
  Messages in groups are never taken as a name, whatever the state of anybody's set-up.

Buttons are `cr:<action>[:<arg>]:<player id>`: the dispatcher refuses a press whose trailing id is not the presser's (handlers.BOUND), and every action then
works on the PRESSER's own character only, so nobody can change, advance or finish another player's profile. Every action re-reads the saved state, so a stale,
repeated or hand-made button changes nothing. Names and gender go into the database through character.py only; a name is HTML-escaped wherever it is shown and
never appears in callback data.
"""
from __future__ import annotations

from . import character, config as C, fmt
from .core import GameError
from .texts import t
from .views import Reply, kb

ACTIONS = {"c": 0, "n": 0, "g": 1}              # continue / change the name / choose a gender (male|female): the number of arguments each takes


def _cd(action: str, uid: int, *arg) -> str:
    return ":".join(["cr", action, *arg, str(uid)])


def _names(ch) -> dict:
    return {"first": fmt.esc(ch["first_name"]), "last": fmt.esc(ch["last_name"])}


def summary(lang: str, ch, key: str, now: int | None = None) -> str:
    return t(lang, key, cid=fmt.loc(str(ch["citizen_id"]), lang), age=fmt.num(character.age(ch, now) or C.CHARACTER_START_AGE, lang), **_names(ch))


def screen(lang: str, ch, uid: int, now: int | None = None) -> Reply:
    """The set-up step the character is at (the finished screen for a finished character). Private chat."""
    state = ch["setup_state"]
    if state == "first":
        return Reply(text=t(lang, "char.s.first", min=C.CHAR_NAME_MIN, max=C.CHAR_NAME_MAX))
    if state == "last":
        return Reply(text=t(lang, "char.s.last", **_names(ch)), kb=kb([[(t(lang, "char.b.rename"), _cd("n", uid))]]))
    if state == "gender":
        rows = [[(t(lang, "char.b.male"), _cd("g", uid, "male")), (t(lang, "char.b.female"), _cd("g", uid, "female"))],
                [(t(lang, "char.b.rename"), _cd("n", uid))]]
        return Reply(text=t(lang, "char.s.gender", **_names(ch)), kb=kb(rows))
    return Reply(text=summary(lang, ch, "char.s.exists", now))


def done_screen(lang: str, ch, now: int | None = None) -> Reply:
    return Reply(text=summary(lang, ch, "char.s.done", now))


def start_link(botname: str) -> str:
    return f"https://t.me/{botname}?start=char"


def need_reply(c, ch) -> Reply:
    """What a life-game command in a GROUP answers a player whose character is not finished: a short message with the button that opens the private chat (or, for a
    button press, a toast: no message is added to the group by somebody else's old button)."""
    if c.cb is not None:
        return Reply(toast=t(c.lang, "char.cb.need"), alert=True)
    resumed = ch is not None and (ch["setup_state"] != "first" or bool(ch["first_name"]))
    label = t(c.lang, "char.b.resume" if resumed else "char.b.start")
    return Reply(text=t(c.lang, "char.need.resume" if resumed else "char.need"), kb=kb([[(label, start_link(c.app.botname), "url")]]))


def handle_name(c, text: str) -> Reply:
    """The player's plain private message while their set-up waits for a name. GameError('char_name') for a name that is not acceptable (the step stays)."""
    ch = character.submit_name(c.db, c.uid, text)
    return screen(c.lang, ch, c.uid)


def handle_button(c) -> Reply:
    """`cr:<action>[:<arg>]:<player id>` in the private chat."""
    lang, uid = c.lang, c.uid
    rest = c.args[:-1]
    if c.is_group:
        return Reply(toast=t(lang, "char.private"), alert=True)
    if not rest or ACTIONS.get(rest[0]) != len(rest) - 1:
        return Reply(toast=t(lang, "e.bad_usage"))
    action, arg = rest[0], rest[1:]
    ch = character.get(c.db, uid)
    if ch is None:
        raise GameError("char_none")
    if action == "c":
        r = screen(lang, ch, uid)
    elif action == "n":
        r = screen(lang, character.restart_names(c.db, uid), uid)
    else:
        row, finished = character.choose_gender(c.db, uid, arg[0])
        r = done_screen(lang, row) if finished else screen(lang, row, uid)
    r.edit = True
    return r
