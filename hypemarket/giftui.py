"""What the player and the admin see of gift codes (v0.20): the amount of a reward in words, and the answer to `کد هدیه <code>`. The rules are in gifts.py."""
from __future__ import annotations

from . import charxp, core, fmt, gifts
from .core import GameError
from .texts import t
from .views import Reply


def amount_text(lang: str, reward: str, amount: int) -> str:
    """The stored amount of a reward as a short text: «500 🪙», «$25.00», «50 XP»."""
    if reward == "stock_money":
        return fmt.coins_x(amount, lang)
    if reward == "life_cash":
        return fmt.usd(amount, lang)
    return f"{fmt.num(amount, lang)} XP"


def what(lang: str, reward: str, amount: int) -> str:
    """«500 🪙 پول سهام» / «$25.00 پول زندگی» / «50 XP شخصیت»: the reward as the admin's screens name it."""
    return t(lang, f"gift.what.{reward}", n=amount_text(lang, reward, amount))


def redeemed(lang: str, r: dict) -> str:
    n = amount_text(lang, r["reward"], r["amount"])
    if r["reward"] == "stock_money":
        return t(lang, "gift.ok.stock_money", n=n, balance=fmt.coins_x(r["balance"], lang))
    if r["reward"] == "life_cash":
        return t(lang, "gift.ok.life_cash", n=n, balance=fmt.usd(r["balance"], lang))
    return t(lang, "gift.ok.char_xp", n=n, xp=fmt.num(r["xp"], lang), level=fmt.num(charxp.level_for(r["xp"]), lang))


async def redeem(c) -> Reply | None:
    """`کد هدیه <code>`: the player redeems a code for themselves, once. The code is never repeated in the answer (in a group everybody can read it anyway). GameError for every
    refusal (the generic error path says it in Persian / English); None for an account that cannot play (a bot)."""
    u = core.get_user(c.db, c.uid)
    if u is None or u["bot"]:
        return None
    if not c.args:
        return Reply(text=t(c.lang, "gift.usage"))
    code = gifts.normalize(c.args)
    if code is None:
        raise GameError("gift_bad")
    r = gifts.redeem(c.db, c.uid, code)
    return Reply(text=redeemed(c.lang, r))
