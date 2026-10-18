"""The bot admin's panel (/admin): the required channel and the backups, as screens with buttons.

Only the accounts listed in ADMIN_IDS (.env) can open it or press its buttons; a regular player gets silence for `/admin` and a
dead toast for any button they forge. It only opens in a private chat. The screens are pure functions of the database and the
app; handlers.py wires the command and the `ad:` buttons to them.
"""
from __future__ import annotations

from . import backups, core, fmt, gate
from .texts import t
from .views import Reply, kb

PROMPT = "adminchan"          # the key of the guided prompt in App.pending: the admin's next message is a channel name
PROMPT_TTL = 300


def allowed(app, uid: int) -> bool:
    return uid in app.settings.admins


def _chan_text(lang: str, s: dict | None) -> str:
    if not s:
        return t(lang, "ad.chan_none")
    return f"<b>{fmt.esc(gate.label(s))}</b> — {t(lang, 'ad.on' if s['on'] else 'ad.off')}"


def _last(lang: str, db) -> str:
    ts = backups.last_done(db)
    return backups.when(ts) if ts else t(lang, "ad.never")


def _next(lang: str, app) -> str:
    nxt = backups.next_due(app.db, backups.interval(app.settings))
    return t(lang, "ad.now") if nxt <= core.now() else backups.when(nxt)


def _screen(text: str, rows: list, edit: bool = True) -> Reply:
    return Reply(text=text, kb=kb(rows), edit=edit)


def main(app, lang: str, uid: int, note: str = "", edit: bool = False) -> Reply:
    text = t(lang, "ad.main", chan=_chan_text(lang, gate.get(app.db)), every=fmt.dur(backups.interval(app.settings), lang), last=_last(lang, app.db))
    rows = [[(t(lang, "ad.b.chan"), f"ad:chan:{uid}"), (t(lang, "ad.b.backup"), f"ad:bk:{uid}")]]
    return _screen((note + "\n\n" if note else "") + text, rows, edit)


def channel_screen(app, lang: str, uid: int, note: str = "", edit: bool = True) -> Reply:
    s = gate.get(app.db)
    link = fmt.esc(s["link"]) if s and s["link"] else t(lang, "ad.nolink")
    body = t(lang, "ad.chan", chan=_chan_text(lang, s), state=t(lang, "ad.on" if s and s["on"] else "ad.off"), link=link,
             hint=t(lang, "ad.hint_set" if s else "ad.hint_none"))
    rows = [[(t(lang, "ad.b.set"), f"ad:ask:{uid}")]]
    if s:
        rows[0].append((t(lang, "ad.b.off") if s["on"] else t(lang, "ad.b.on"), f"ad:{'off' if s['on'] else 'on'}:{uid}"))
        rows.append([(t(lang, "ad.b.check"), f"ad:check:{uid}"), (t(lang, "ad.b.rm"), f"ad:rm:{uid}")])
    rows.append([(t(lang, "ad.b.main"), f"ad:main:{uid}")])
    return _screen((note + "\n\n" if note else "") + body, rows, edit)


def backup_screen(app, lang: str, uid: int, note: str = "", edit: bool = True) -> Reply:
    body = t(lang, "ad.backup", every=fmt.dur(backups.interval(app.settings), lang), last=_last(lang, app.db), next=_next(lang, app),
             n=fmt.num(len(app.settings.admins), lang))
    rows = [[(t(lang, "ad.b.bknow"), f"ad:bknow:{uid}")], [(t(lang, "ad.b.main"), f"ad:main:{uid}")]]
    return _screen((note + "\n\n" if note else "") + body, rows, edit)


def _gate_error(lang: str, e: gate.GateError) -> str:
    return t(lang, f"ad.err.{e.code}") if e.code in ("bad_ref", "not_found", "not_channel", "bot_not_admin", "api") else t(lang, "ad.err.api")


def _arm(app, uid: int) -> None:
    app.pending[uid] = (PROMPT, core.now() + PROMPT_TTL)


async def press(app, lang: str, uid: int, parts: list) -> Reply:
    """One button of the panel. `parts` is the callback data without the leading «ad» and without the trailing player id."""
    if not allowed(app, uid):
        return Reply(toast=t(lang, "ad.denied"))
    sub = parts[0] if parts else "main"
    if sub == "main":
        return main(app, lang, uid, edit=True)
    if sub == "chan":
        return channel_screen(app, lang, uid)
    if sub == "bk":
        return backup_screen(app, lang, uid)
    if sub == "ask":
        _arm(app, uid)
        return _screen(t(lang, "ad.ask"), [[(t(lang, "ad.b.cancel"), f"ad:chan:{uid}")]])
    s = gate.get(app.db)
    if sub in ("on", "check"):
        if not s:
            return Reply(toast=t(lang, "ad.need_chan"), alert=True)
        try:
            info = await gate.resolve(app.bot, s["chat"])
        except gate.GateError as e:
            return channel_screen(app, lang, uid, _gate_error(lang, e))
        gate.save(app.db, info, on=s["on"] or sub == "on")             # refreshes the title and join link too
        gate.forget(app)
        if sub == "on":
            return channel_screen(app, lang, uid, t(lang, "ad.turned_on"))
        return channel_screen(app, lang, uid, t(lang, "ad.check_ok", chan=fmt.esc(gate.label(gate.get(app.db)))))
    if sub == "off":
        if s:
            gate.set_on(app.db, False)
            gate.forget(app)
        return channel_screen(app, lang, uid, t(lang, "ad.turned_off"))
    if sub == "rm":
        if not s:
            return channel_screen(app, lang, uid)
        return _screen(t(lang, "ad.rm_ask", chan=fmt.esc(gate.label(s))), [[(t(lang, "ad.b.rmok"), f"ad:rmok:{uid}"), (t(lang, "ad.b.cancel"), f"ad:chan:{uid}")]])
    if sub == "rmok":
        gate.clear(app.db)
        gate.forget(app)
        return channel_screen(app, lang, uid, t(lang, "ad.removed"))
    if sub == "bknow":
        res = await backups.send_backup(app, "manual")
        note = t(lang, "ad.bk_done", n=fmt.num(res["sent"], lang)) if res["ok"] else t(lang, "backup.failed")
        return backup_screen(app, lang, uid, note)
    return Reply(toast=t(lang, "e.bad_usage"))


async def channel_text(app, lang: str, uid: int, text: str) -> Reply:
    """The admin's answer to the «Set channel» prompt."""
    if not allowed(app, uid):
        return Reply(toast=t(lang, "ad.denied"))
    try:
        info = await gate.resolve(app.bot, text)
    except gate.GateError as e:
        _arm(app, uid)                                                   # let them try again without pressing the button again
        return _screen(_gate_error(lang, e) + "\n\n" + t(lang, "ad.retry"), [[(t(lang, "ad.b.cancel"), f"ad:chan:{uid}")]], edit=False)
    gate.save(app.db, info, on=True)
    gate.forget(app)
    return channel_screen(app, lang, uid, t(lang, "ad.set_ok", chan=fmt.esc(gate.label(gate.get(app.db)))), edit=False)
