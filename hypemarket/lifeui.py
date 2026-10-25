"""The wallet and the currency exchange screens (v0.18).

`cash` shows the two balances side by side with their own names and symbols (💵 پول نقد in $, 🪙 پول سهام in coins) and the latest cash
transactions. `fx` is the exchange: pick a direction, pick or type an amount, review the preview (what you give, what you get, both
balances after), confirm or cancel. A button never carries an amount that moves money: the amount buttons only ask for a PREVIEW (a stored,
expiring quote); the confirm / cancel buttons carry only the quote's random id. See cash.py for the exact rules and the rounding policy.
"""
from __future__ import annotations

from . import cash, config as C, core, fmt
from .texts import t
from .views import Reply, kb

PRESET_COINS = (50, 250, 1000)                 # 🪙 amounts offered as buttons (only those the player can afford)
PRESET_CENTS = (100, 500, 2000)                # $ amounts offered as buttons


def rate_line(lang: str) -> str:
    """«1,000 🪙 = $5.00» (the documented fixed rate, derived from config.FX_UNITS_PER_CENT)."""
    return t(lang, "cash.rate", coins=fmt.coins_x(1000 * C.UNIT, lang), usd=fmt.usd(C.fx_cents_for_coins(1000), lang))


def _step(lang: str) -> str:
    return fmt.num(C.fx_coin_step(), lang)


def _kind_label(lang: str, kind: str) -> str:
    return t(lang, f"cash.k.{kind}") if kind in ("grant", "fx", "snapp_fare", "snapp_tip", "snapp_fee", "mcd_wage", "muni_sweep", "muni_waste", "muni_green", "casino_bet", "casino_win") else kind


def wallet(db, lang: str, uid: int) -> Reply:
    acc = cash.ensure_account(db, uid)
    u = core.need_user(db, uid)
    lines = [t(lang, "cash.title"), "", t(lang, "cash.body", cash=fmt.usd(acc["balance"], lang), stock=fmt.coins_x(u["cash"], lang), rate=rate_line(lang))]
    rows = cash.history(db, uid)
    lines += ["", t(lang, "cash.last")]
    if rows:
        for r in rows:
            sign = "+" if r["delta"] > 0 else "−"
            lines.append(t(lang, "cash.row", sign=sign, amount=fmt.usd(abs(r["delta"]), lang), what=_kind_label(lang, r["kind"])))
    else:
        lines.append(t(lang, "cash.none"))
    buttons = [[(t(lang, "cash.b.fx"), f"cs:fx:{uid}"), (t(lang, "cash.b.jobs"), f"jb:h:{uid}")],
               [(t(lang, "cash.b.refresh"), f"cs:w:{uid}")]]
    return Reply(text="\n".join(lines), kb=kb(buttons))


def fx_home(db, lang: str, uid: int) -> Reply:
    acc = cash.ensure_account(db, uid)
    u = core.need_user(db, uid)
    text = t(lang, "fx.home", cash=fmt.usd(acc["balance"], lang), stock=fmt.coins_x(u["cash"], lang), rate=rate_line(lang), step=_step(lang))
    rows = [[(t(lang, "fx.b.s2c"), f"cs:d:s2c:{uid}")], [(t(lang, "fx.b.c2s"), f"cs:d:c2s:{uid}")],
            [(t(lang, "fx.b.wallet"), f"cs:w:{uid}")]]
    return Reply(text=text, kb=kb(rows))


def fx_direction(db, lang: str, uid: int, direction: str) -> Reply:
    acc = cash.ensure_account(db, uid)
    u = core.need_user(db, uid)
    cash_s, stock_s = fmt.usd(acc["balance"], lang), fmt.coins_x(u["cash"], lang)
    rows = []
    if direction == cash.S2C:
        have = cash.max_coins(db, uid)
        text = t(lang, "fx.dir.s2c", cash=cash_s, stock=stock_s, step=_step(lang))
        presets = [n for n in PRESET_COINS if n <= have and n % C.fx_coin_step() == 0]
        row = [(fmt.coins_x(n * C.UNIT, lang), f"cs:a:s2c:{n}:{uid}") for n in presets]
        if row:
            rows.append(row)
        if have >= C.fx_coin_step():
            rows.append([(t(lang, "fx.b.all", amount=fmt.coins_x(have * C.UNIT, lang)), f"cs:a:s2c:all:{uid}")])
        else:
            text += t(lang, "fx.dir.nothing_s2c", step=_step(lang))
    else:
        have = acc["balance"]
        text = t(lang, "fx.dir.c2s", cash=cash_s, stock=stock_s, per_usd=fmt.coins_x(C.fx_coins_for_cents(100) * C.UNIT, lang))
        presets = [n for n in PRESET_CENTS if n <= have]
        row = [(fmt.usd(n, lang), f"cs:a:c2s:{n}:{uid}") for n in presets]
        if row:
            rows.append(row)
        if have >= 1:
            rows.append([(t(lang, "fx.b.all", amount=fmt.usd(have, lang)), f"cs:a:c2s:all:{uid}")])
        else:
            text += t(lang, "fx.dir.nothing_c2s")
    rows.append([(t(lang, "fx.b.back"), f"cs:fx:{uid}")])
    return Reply(text=text, kb=kb(rows))


def fx_confirm(db, lang: str, uid: int, q: cash.Quote, now: int | None = None) -> Reply:
    """The preview of a stored quote: what you give, what you get, the rate, both balances after, and the two buttons."""
    now = core.now() if now is None else now
    acc = cash.get_account(db, uid)
    u = core.need_user(db, uid)
    if q.direction == cash.S2C:
        give, get = fmt.coins_x(q.src, lang), fmt.usd(q.dst, lang)
        gw, tw = t(lang, "fx.give.s2c"), t(lang, "fx.give.c2s")
        stock_after, cash_after = u["cash"] - q.src, acc["balance"] + q.dst
    else:
        give, get = fmt.usd(q.src, lang), fmt.coins_x(q.dst, lang)
        gw, tw = t(lang, "fx.give.c2s"), t(lang, "fx.give.s2c")
        stock_after, cash_after = u["cash"] + q.dst, acc["balance"] - q.src
    text = t(lang, "fx.confirm", give=give, give_what=gw, get=get, get_what=tw, rate=rate_line(lang),
             stock=fmt.coins_x(u["cash"], lang), cash=fmt.usd(acc["balance"], lang),
             stock_after=fmt.coins_x(stock_after, lang), cash_after=fmt.usd(cash_after, lang), ttl=fmt.dur(max(0, q.expires_at - now), lang))
    rows = [[(t(lang, "fx.b.ok"), f"cs:ok:{q.id}:{uid}"), (t(lang, "fx.b.no"), f"cs:no:{q.id}:{uid}")]]
    return Reply(text=text, kb=kb(rows))


def fx_done(lang: str, uid: int, r: cash.Receipt) -> Reply:
    q = r.quote
    if q.direction == cash.S2C:
        give, get = fmt.coins_x(q.src, lang), fmt.usd(q.dst, lang)
    else:
        give, get = fmt.usd(q.src, lang), fmt.coins_x(q.dst, lang)
    text = t(lang, "fx.done", give=give, get=get, stock=fmt.coins_x(r.stock_after, lang), cash=fmt.usd(r.cash_after, lang))
    rows = [[(t(lang, "fx.again"), f"cs:fx:{uid}"), (t(lang, "fx.b.wallet"), f"cs:w:{uid}")]]
    return Reply(text=text, kb=kb(rows))


def fx_cancelled(lang: str, uid: int) -> Reply:
    return Reply(text=t(lang, "fx.cancelled"), kb=kb([[(t(lang, "fx.again"), f"cs:fx:{uid}"), (t(lang, "fx.b.wallet"), f"cs:w:{uid}")]]))


def parse_amount(direction: str, token: str):
    """What the player typed after the direction word: ('all', None), ('n', amount) or None when it is not a valid amount for that direction.
    s2c amounts are whole coins; c2s amounts are dollars with at most two decimals."""
    if cash.is_all(token):
        return "all", None
    v = cash.parse_coins(token) if direction == cash.S2C else cash.parse_cents(token)
    return ("n", v) if v is not None else None


DIRECTION_WORDS = {
    cash.S2C: {"سهام", "سکه", "stocks", "stock", "coins", "coin", "s2c", "پول-سهام"},
    cash.C2S: {"نقد", "دلار", "cash", "usd", "dollar", "dollars", "c2s", "پول-نقد"},
}


def direction_of(token: str) -> str | None:
    from .textparse import key
    k = key(token)
    for d, words in DIRECTION_WORDS.items():
        if k in {key(w) for w in words}:
            return d
    return None
