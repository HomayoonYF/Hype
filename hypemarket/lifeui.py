"""The wallet and the currency exchange screens (v0.18).

`cash` shows the two balances side by side with their own names and symbols (💵 پول نقد in $, 🪙 پول سهام in coins) and the latest cash
transactions. `fx` is the exchange, ONE WAY since v0.18.2 (stock money into cash): pick or type an amount, review the preview (what you give,
what you get, both balances after), confirm or cancel. A button never carries an amount that moves money: the amount buttons only ask for a PREVIEW (a stored,
expiring quote); the confirm / cancel buttons carry only the quote's random id. See cash.py for the exact rules and the rounding policy.
"""
from __future__ import annotations

from . import cash, config as C, core, fmt
from .texts import t
from .views import Reply, kb

PRESET_COINS = (50, 250, 1000)                 # 🪙 amounts offered as buttons (only those the player can afford)


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
    """The exchange: stock money into cash (the only direction there is). Balances, the rate, the amounts the player can afford as buttons."""
    acc = cash.ensure_account(db, uid)
    u = core.need_user(db, uid)
    have = cash.max_coins(db, uid)
    step = C.fx_coin_step()
    text = t(lang, "fx.home", cash=fmt.usd(acc["balance"], lang), stock=fmt.coins_x(u["cash"], lang), rate=rate_line(lang), step=_step(lang))
    rows = []
    presets = [n for n in PRESET_COINS if n <= have and n % step == 0]
    if presets:
        rows.append([(fmt.coins_x(n * C.UNIT, lang), f"cs:a:s2c:{n}:{uid}") for n in presets])
    if have >= step:
        rows.append([(t(lang, "fx.b.all", amount=fmt.coins_x(have * C.UNIT, lang)), f"cs:a:s2c:all:{uid}")])
    else:
        text += t(lang, "fx.nothing", step=_step(lang))
    rows.append([(t(lang, "fx.b.wallet"), f"cs:w:{uid}")])
    return Reply(text=text, kb=kb(rows))


def fx_confirm(db, lang: str, uid: int, q: cash.Quote, now: int | None = None) -> Reply:
    """The preview of a stored quote: what you give, what you get, the rate, both balances after, and the two buttons."""
    now = core.now() if now is None else now
    acc = cash.get_account(db, uid)
    u = core.need_user(db, uid)
    text = t(lang, "fx.confirm", give=fmt.coins_x(q.src, lang), get=fmt.usd(q.dst, lang), rate=rate_line(lang),
             stock=fmt.coins_x(u["cash"], lang), cash=fmt.usd(acc["balance"], lang),
             stock_after=fmt.coins_x(u["cash"] - q.src, lang), cash_after=fmt.usd(acc["balance"] + q.dst, lang), ttl=fmt.dur(max(0, q.expires_at - now), lang))
    rows = [[(t(lang, "fx.b.ok"), f"cs:ok:{q.id}:{uid}"), (t(lang, "fx.b.no"), f"cs:no:{q.id}:{uid}")]]
    return Reply(text=text, kb=kb(rows))


def fx_done(lang: str, uid: int, r: cash.Receipt) -> Reply:
    q = r.quote
    text = t(lang, "fx.done", give=fmt.coins_x(q.src, lang), get=fmt.usd(q.dst, lang), stock=fmt.coins_x(r.stock_after, lang), cash=fmt.usd(r.cash_after, lang))
    rows = [[(t(lang, "fx.again"), f"cs:fx:{uid}"), (t(lang, "fx.b.wallet"), f"cs:w:{uid}")]]
    return Reply(text=text, kb=kb(rows))


def fx_cancelled(lang: str, uid: int) -> Reply:
    return Reply(text=t(lang, "fx.cancelled"), kb=kb([[(t(lang, "fx.again"), f"cs:fx:{uid}"), (t(lang, "fx.b.wallet"), f"cs:w:{uid}")]]))


def parse_amount(token: str):
    """What the player typed as the amount: ('all', None), ('n', whole coins) or None when it is not a valid amount."""
    if cash.is_all(token):
        return "all", None
    v = cash.parse_coins(token)
    return ("n", v) if v is not None else None


STOCK_WORDS = {"سهام", "سکه", "stocks", "stock", "coins", "coin", "s2c", "پول-سهام"}                 # «صرافی سهام 250»: the word of the old two-direction days still works
CASH_WORDS = {"نقد", "دلار", "cash", "usd", "dollar", "dollars", "c2s", "پول-نقد"}                    # «صرافی نقد 5»: there is no such direction any more (answered with a refusal)


def lead_of(token: str) -> str | None:
    """'stock' for a leading word that names the stock money, 'cash' for one that names the cash (refused: cash cannot be exchanged), else None."""
    from .textparse import key
    k = key(token)
    if k in {key(w) for w in STOCK_WORDS}:
        return "stock"
    if k in {key(w) for w in CASH_WORDS}:
        return "cash"
    return None
