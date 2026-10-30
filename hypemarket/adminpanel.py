"""The bot admin's panel (/admin): the required channel and the backups, as screens with buttons.

Only the accounts listed in ADMIN_IDS (.env) can open it or press its buttons; a regular player gets silence for `/admin` and a
dead toast for any button they forge. It only opens in a private chat. The screens are pure functions of the database and the
app; handlers.py wires the command and the `ad:` buttons to them.
"""
from __future__ import annotations

import secrets

from . import backups, config as C, core, fmt, gate, giftui, gifts
from .core import GameError
from .texts import t
from .views import Reply, kb

PROMPT = "adminchan"          # the key of the guided prompt in App.pending: the admin's next message is a channel name
PROMPT_TTL = 300
GIFT_AMOUNT, GIFT_USES = "giftamt", "giftuses"       # the guided prompts of «ساخت گیفت کارت»: the admin's next message is the amount per player / the number of players
GIFT_PROMPTS = (GIFT_AMOUNT, GIFT_USES)
GIFT_TTL = 600                                        # how long a prompt waits for the admin's answer, and how long an unfinished draft lives (nothing is stored before the confirmation)


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
    rows = [[(t(lang, "ad.b.chan"), f"ad:chan:{uid}"), (t(lang, "ad.b.backup"), f"ad:bk:{uid}")],
            [(t(lang, "ad.b.gift"), f"ad:gift:{uid}"), (t(lang, "ad.b.gifts"), f"ad:gl:{uid}")]]
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


# =====================================================================================================================
#                                  «ساخت گیفت کارت»: type -> amount -> players -> confirm -> the code
# =====================================================================================================================
# The draft lives in memory only (App.gift_drafts: uid -> {type, step, amount, uses, nonce, exp}); NOTHING is stored before the confirmation, so a cancel, a timeout or a restart leaves no
# code behind. The confirm button carries the draft's nonce and consumes the draft before it creates the code, so a double tap or a retried press creates at most one code.
def _gift_draft(app, uid: int):
    d = app.gift_drafts.get(uid)
    if d is not None and d["exp"] <= core.now():
        app.gift_drafts.pop(uid, None)
        return None
    return d


def _gift_arm(app, uid: int, key: str) -> None:
    app.pending[uid] = (key, core.now() + GIFT_TTL)


def _gift_end(app, uid: int) -> None:
    app.gift_drafts.pop(uid, None)
    if app.pending.get(uid, ("",))[0] in GIFT_PROMPTS:
        app.pending.pop(uid, None)


def _gift_cancel_row(lang: str, uid: int) -> list:
    return [(t(lang, "gift.b.cancel"), f"ad:gx:{uid}")]


def _gift_stale(lang: str, uid: int, edit: bool) -> Reply:
    return _screen(t(lang, "gift.stale"), [[(t(lang, "gift.b.again"), f"ad:gift:{uid}")], [(t(lang, "ad.b.main"), f"ad:main:{uid}")]], edit)


def gift_type_screen(lang: str, uid: int) -> Reply:
    rows = [[(t(lang, f"gift.b.{r}"), f"ad:gt:{r}:{uid}")] for r in gifts.REWARDS]
    rows.append([(t(lang, "gift.b.cancel"), f"ad:gx:{uid}")])
    return _screen(t(lang, "gift.type"), rows)


def _gift_ask_amount(lang: str, uid: int, d: dict, edit: bool, note: str = "") -> Reply:
    body = t(lang, "gift.ask_amount", reward=t(lang, f"gift.b.{d['type']}"), unit=t(lang, f"gift.unit.{d['type']}"), max=fmt.num(gifts.limit(d["type"]), lang))
    return _screen((note + "\n\n" if note else "") + body, [_gift_cancel_row(lang, uid)], edit)


def _gift_ask_uses(lang: str, uid: int, d: dict, edit: bool, note: str = "") -> Reply:
    what = giftui.what(lang, d["type"], gifts.to_units(d["type"], d["amount"]))
    body = t(lang, "gift.ask_uses", what=what, max=fmt.num(C.GIFT_MAX_USES, lang))
    return _screen((note + "\n\n" if note else "") + body, [_gift_cancel_row(lang, uid)], edit)


def _gift_confirm_screen(lang: str, uid: int, d: dict, edit: bool) -> Reply:
    each = gifts.to_units(d["type"], d["amount"])
    body = t(lang, "gift.confirm", what=giftui.what(lang, d["type"], each), uses=fmt.num(d["uses"], lang), total=giftui.what(lang, d["type"], each * d["uses"]))
    return _screen(body, [[(t(lang, "gift.b.ok"), f"ad:gok:{d['nonce']}:{uid}"), (t(lang, "gift.b.cancel"), f"ad:gx:{uid}")]], edit)


def gift_list_screen(app, lang: str, uid: int) -> Reply:
    """The latest codes with their reward and use counter: the way back to a code whose message was lost (admins only; the codes are secrets worth a few coins, never shown to players)."""
    rows = gifts.recent(app.db, 10)
    if not rows:
        body = t(lang, "gift.list_empty")
    else:
        lines = [t(lang, "gift.line", code=r["code"], what=giftui.what(lang, r["reward_type"], r["amount"]), used=fmt.num(r["used_count"], lang), max=fmt.num(r["max_uses"], lang)) for r in rows]
        body = t(lang, "gift.list", lines="\n".join(lines))
    return _screen(body, [[(t(lang, "gift.b.again"), f"ad:gift:{uid}")], [(t(lang, "ad.b.main"), f"ad:main:{uid}")]])


def _gift_confirm(app, lang: str, uid: int, nonce: str) -> Reply:
    """The ✅ button: the draft is consumed FIRST (a second press finds nothing), the maker is checked again, then the code is created and stored in one transaction."""
    d = _gift_draft(app, uid)
    if d is None or d["nonce"] != nonce or d["step"] != "confirm":
        return _gift_stale(lang, uid, True)
    app.gift_drafts.pop(uid, None)
    if not allowed(app, uid):                                           # re-checked at execution, whatever screen led here
        return Reply(toast=t(lang, "ad.denied"), alert=True)
    try:
        row = gifts.create(app.db, uid, d["type"], gifts.to_units(d["type"], d["amount"]), d["uses"], app.settings.admins)
    except (GameError, ValueError, RuntimeError):
        return _screen(t(lang, "gift.failed"), [[(t(lang, "gift.b.again"), f"ad:gift:{uid}")], [(t(lang, "ad.b.main"), f"ad:main:{uid}")]])
    body = t(lang, "gift.made", code=row["code"], what=giftui.what(lang, row["reward_type"], row["amount"]), uses=fmt.num(row["max_uses"], lang))
    return _screen(body, [[(t(lang, "gift.b.list"), f"ad:gl:{uid}"), (t(lang, "gift.b.again"), f"ad:gift:{uid}")], [(t(lang, "ad.b.main"), f"ad:main:{uid}")]])


async def gift_text(app, lang: str, uid: int, key: str, text: str) -> Reply:
    """The admin's plain answer to a gift prompt: the amount per player (key giftamt) or the number of players (giftuses). A wrong answer repeats the question; a draft that is gone says so."""
    if not allowed(app, uid):
        return Reply(toast=t(lang, "ad.denied"))
    d = _gift_draft(app, uid)
    if d is None or d["step"] != ("amount" if key == GIFT_AMOUNT else "uses"):
        return _gift_stale(lang, uid, False)
    if key == GIFT_AMOUNT:
        n = gifts.parse_whole(text, 1, gifts.limit(d["type"]))
        if n is None:
            _gift_arm(app, uid, GIFT_AMOUNT)
            return _gift_ask_amount(lang, uid, d, False, t(lang, "gift.bad_amount", max=fmt.num(gifts.limit(d["type"]), lang)))
        d["amount"], d["step"], d["exp"] = n, "uses", core.now() + GIFT_TTL
        _gift_arm(app, uid, GIFT_USES)
        return _gift_ask_uses(lang, uid, d, False)
    n = gifts.parse_uses(text)
    if n is None:
        _gift_arm(app, uid, GIFT_USES)
        return _gift_ask_uses(lang, uid, d, False, t(lang, "gift.bad_uses", max=fmt.num(C.GIFT_MAX_USES, lang)))
    d["uses"], d["step"], d["exp"] = n, "confirm", core.now() + GIFT_TTL
    return _gift_confirm_screen(lang, uid, d, False)


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
    if sub == "gift":                                                   # «🎁 ساخت گیفت کارت»: a fresh start (an older unfinished draft is dropped)
        _gift_end(app, uid)
        return gift_type_screen(lang, uid)
    if sub == "gt":
        reward = parts[1] if len(parts) > 1 else ""
        if reward not in gifts.REWARDS:
            return Reply(toast=t(lang, "e.bad_usage"))
        d = {"type": reward, "step": "amount", "amount": None, "uses": None, "nonce": secrets.token_hex(4), "exp": core.now() + GIFT_TTL}
        app.gift_drafts[uid] = d
        _gift_arm(app, uid, GIFT_AMOUNT)
        return _gift_ask_amount(lang, uid, d, True)
    if sub == "gx":                                                     # cancel: nothing was stored, so nothing needs undoing
        _gift_end(app, uid)
        return main(app, lang, uid, t(lang, "gift.cancelled"), edit=True)
    if sub == "gok":
        return _gift_confirm(app, lang, uid, parts[1] if len(parts) > 1 else "")
    if sub == "gl":
        return gift_list_screen(app, lang, uid)
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
