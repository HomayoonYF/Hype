"""Admin gift codes (v0.20): an admin makes a code that gives a reward, players redeem it with `کد هدیه <code>`.

A code is one row of `gift_codes` (the reward, the amount per redeemer, how many different players may use it, who made it and when) and every use is one row of
`gift_redemptions` (UNIQUE per code and player: a player gets a code's reward once). The rows are protected by triggers (db.py): a code's reward, amount and limit never change, only
its use counter moves and only up; nothing is deleted.

Rewards (the amount is in the unit stated):
* `stock_money`: stock-game coins, the same balance (`users.cash`, in 1/100-coin units) an admin's /give and the daily bonus change; the coins are counted as minted (`core.mint`);
* `life_cash`: life cash in cents, through `cash.apply` (its ledger row `gift` / <code> is unique per player: the payout is idempotent by itself);
* `char_xp`: character XP, through `charxp.grant` (source `gift`, reference <code>: idempotent by itself); it needs a FINISHED character.
* `item` (v0.21): a quantity of one catalog item (`gift_codes.item_id` names it; the amount is the quantity per redeemer), put into the player's bag by `inventory.give` (source `gift`,
  reference <code>: idempotent by itself, so a repeated message never gives it twice). A bag with no room for the whole quantity refuses the code and nothing of it is used up.

Redeeming is ONE transaction: the code is read, the player's earlier use and the limit are checked, the use counter goes up (`WHERE used_count < max_uses`, so two players racing for the
last place cannot both get it), the redemption row is written and the reward is paid. If anything fails (the reward cannot be given, a cap is reached, a crash) nothing of it stays: no
use is used up and no coin moves. A repeat (a retried message, a second tap) is answered "already redeemed" and pays nothing.

The code itself is `HM-XXXX-XXXX-XXXX`: twelve characters from a 32-letter alphabet without look-alikes (0/O, 1/I), drawn with `secrets` (60 random bits), so it cannot be guessed;
uniqueness is the table's primary key (a clash is simply drawn again). A code is typed back with any spacing, case or digit script (`normalize`).
"""
from __future__ import annotations

import re
import secrets
import sqlite3

from . import cash, character, charxp, config as C, core, inventory, items
from .core import GameError

REWARDS = ("stock_money", "life_cash", "char_xp", "item")
ALPHABET = "ABCDEFGHJKLMNPQRSTUVWXYZ23456789"
PREFIX = "HM"
GROUPS, GROUP_LEN = 3, 4
_CODE = re.compile(rf"(?:{PREFIX})?([{ALPHABET}]{{{GROUPS * GROUP_LEN}}})")
_DIGITS = str.maketrans("۰۱۲۳۴۵۶۷۸۹٠١٢٣٤٥٦٧٨٩", "01234567890123456789")
_NOISE = re.compile(r"[\s\-_‌‍‎‏⁦-⁩  ]+")
_WHOLE = re.compile(r"\d{1,15}|\d{1,3}(?:[,٬ '  ]\d{3}){1,4}")
MAX_TYPED = 60                                   # a typed code longer than this is not even looked at


def limit(reward: str) -> int:
    """The largest amount an admin may put into a code of this reward, in the unit it is typed in (coins, dollars, XP)."""
    return {"stock_money": C.GIFT_MAX_STOCK_COINS, "life_cash": C.GIFT_MAX_CASH_DOLLARS, "char_xp": C.GIFT_MAX_XP, "item": C.GIFT_MAX_ITEM_QTY}[reward]


def to_units(reward: str, n: int) -> int:
    """What an admin typed (whole coins / dollars / XP) as the stored amount (1/100-coin units / cents / XP)."""
    return n * {"stock_money": C.UNIT, "life_cash": 100, "char_xp": 1, "item": 1}[reward]


def from_units(reward: str, amount: int) -> int:
    return amount // {"stock_money": C.UNIT, "life_cash": 100, "char_xp": 1, "item": 1}[reward]


def parse_whole(token, lo: int, hi: int) -> int | None:
    """A whole number from `lo` to `hi` as an admin types it («500», «۵۰۰», «1,000»), or None («2.5», «-1», «0», «1e3», «12abc», an empty text ...)."""
    if not isinstance(token, str):
        return None
    s = token.strip().translate(_DIGITS).replace("‌", "").replace("‏", "").replace("‎", "")
    if not _WHOLE.fullmatch(s):
        return None
    v = int(re.sub(r"[,٬ '  ]", "", s))
    return v if lo <= v <= hi else None


def parse_uses(token) -> int | None:
    return parse_whole(token, 1, C.GIFT_MAX_USES)


# =====================================================================================================================
#                                                       the code
# =====================================================================================================================
def make_code() -> str:
    """A fresh random code, `HM-XXXX-XXXX-XXXX` (60 random bits from the operating system's generator)."""
    raw = "".join(secrets.choice(ALPHABET) for _ in range(GROUPS * GROUP_LEN))
    return PREFIX + "-" + "-".join(raw[i:i + GROUP_LEN] for i in range(0, len(raw), GROUP_LEN))


def normalize(parts) -> str | None:
    """The canonical code for what a player typed after «کد هدیه» (one word or several: «HM-ABCD-EFGH-JKMN», «hm abcd efgh jkmn», «ABCDEFGHJKMN»), or None when it cannot be a code.
    Nothing here looks anything up."""
    if isinstance(parts, str):
        parts = [parts]
    if not parts or not all(isinstance(p, str) for p in parts):
        return None
    joined = "".join(parts)
    if not joined or len(joined) > MAX_TYPED:
        return None
    s = _NOISE.sub("", joined.translate(_DIGITS)).upper()
    m = _CODE.fullmatch(s)
    if not m:
        return None
    raw = m.group(1)
    return PREFIX + "-" + "-".join(raw[i:i + GROUP_LEN] for i in range(0, len(raw), GROUP_LEN))


def looks_like_attempt(parts) -> bool:
    """Whether the words after «کد هدیه» look like somebody typing a code (a single run of Latin letters, digits and dashes, 6 to 60 characters, spaces ignored), right or wrong: in a
    group only that is taken as the command, so «کد هدیه نداری؟» stays ordinary chat. A valid code always does."""
    if isinstance(parts, str):
        parts = [parts]
    if not parts or not all(isinstance(p, str) for p in parts):
        return False
    s = _NOISE.sub("", "".join(parts).translate(_DIGITS))
    return 6 <= len(s) <= MAX_TYPED and re.fullmatch(r"[A-Za-z0-9]+", s) is not None


# =====================================================================================================================
#                                                       making a code
# =====================================================================================================================
def create(db, by: int, reward: str, amount: int, max_uses: int, admins, now: int | None = None, item_id: str | None = None, item_data: dict | None = None):
    """Store a new code and return its row. `amount` is the stored amount (units / cents / XP / the quantity of an item: use `to_units`). An item code names a catalog item (`item_id`);
    no other code may. v0.211: `item_data` (quality `q`, brand `b`) is the instance data every redeemed unit carries; it is stored once beside the code (`gift_item_data`) and never changes.
    The maker must be an admin (checked again HERE, at execution, whatever screen led to it). GameError: gift_denied. ValueError for a reward, amount, item or limit
    outside what an admin may make."""
    if by not in admins:
        raise GameError("gift_denied")
    if reward not in REWARDS:
        raise ValueError("unknown reward")
    if (reward == "item") != (item_id is not None):
        raise ValueError("an item code names an item and no other code does")
    items.ensure(db)
    if reward == "item" and items.get(item_id) is None:
        raise ValueError("unknown item")
    if item_data is not None and reward != "item":
        raise ValueError("only an item code carries item data")
    data_text = None
    if item_data is not None:
        data_text = inventory._data(item_data)                                    # validates the shape (quality 1-5, a brand key)
        inventory._check_data_fits(item_id, data_text)
        if "b" in item_data and item_data["b"] not in C.brand_names():
            raise ValueError("unknown brand")
    if not isinstance(amount, int) or isinstance(amount, bool) or not (1 <= from_units(reward, amount) <= limit(reward)) or amount % to_units(reward, 1):
        raise ValueError("amount out of range")
    if not isinstance(max_uses, int) or isinstance(max_uses, bool) or not 1 <= max_uses <= C.GIFT_MAX_USES:
        raise ValueError("limit out of range")
    now = core.now() if now is None else now
    for _ in range(12):
        code = make_code()
        try:
            with db.tx():
                db.x("INSERT INTO gift_codes(code,reward_type,amount,max_uses,used_count,created_by,created_at,item_id) VALUES(?,?,?,?,0,?,?,?)", (code, reward, amount, max_uses, by, now, item_id))
                if data_text is not None:
                    db.x("INSERT INTO gift_item_data(code,data) VALUES(?,?)", (code, data_text))
            return db.q1("SELECT * FROM gift_codes WHERE code=?", (code,))
        except sqlite3.IntegrityError:                                            # the (astronomically unlikely) clash: draw again
            continue
    raise RuntimeError("could not draw an unused gift code")


def get(db, code: str):
    return db.q1("SELECT * FROM gift_codes WHERE code=?", (code,))


def item_data(db, code: str) -> str | None:
    """The instance data (canonical json) an item code gives each unit, or None for a plain item / another reward."""
    r = db.q1("SELECT data FROM gift_item_data WHERE code=?", (code,))
    return r["data"] if r else None


def recent(db, limit_: int = 10) -> list:
    return db.qa("SELECT * FROM gift_codes ORDER BY created_at DESC, rowid DESC LIMIT ?", (limit_,))


# =====================================================================================================================
#                                                       redeeming a code
# =====================================================================================================================
def redeem(db, uid: int, code: str, now: int | None = None) -> dict:
    """Redeem the canonical `code` for player `uid`, once, atomically. Returns {reward, amount, balance|xp|level, code}. GameError: no_user (unknown or bot account), gift_bad (not a code),
    gift_unknown, gift_used (this player already redeemed it), gift_exhausted (every place is taken), gift_char (an XP code needs a finished character), gift_unavailable (the reward
    cannot be given now: a cap, or an item that left the catalog), gift_full (the bag has no room for an item reward). Nothing at all changes when it raises."""
    if normalize(code) != code:
        raise GameError("gift_bad")
    now = core.now() if now is None else now
    with db.tx():
        u = core.get_user(db, uid)
        if u is None or u["bot"]:
            raise GameError("no_user")
        row = get(db, code)
        if row is None:
            raise GameError("gift_unknown")
        if db.q1("SELECT 1 FROM gift_redemptions WHERE code=? AND user_id=?", (code, uid)) is not None:
            raise GameError("gift_used")
        if row["used_count"] >= row["max_uses"]:
            raise GameError("gift_exhausted")
        reward, amount = row["reward_type"], row["amount"]
        if reward == "char_xp" and not character.is_done(character.get(db, uid)):
            raise GameError("gift_char")
        if db.x("UPDATE gift_codes SET used_count=used_count+1 WHERE code=? AND used_count<max_uses", (code,)).rowcount != 1:
            raise GameError("gift_exhausted")
        db.x("INSERT INTO gift_redemptions(code,user_id,reward_type,amount,redeemed_at) VALUES(?,?,?,?,?)", (code, uid, reward, amount, now))     # UNIQUE(code, user_id)
        out = {"reward": reward, "amount": amount, "code": code}
        if reward == "item":
            item_id = row["item_id"]
            data = item_data(db, code)
            try:
                given = inventory.give(db, uid, item_id, amount, "gift", code, data=items.parse(data) or None, now=now)
            except GameError as e:
                if e.code in ("inv_full", "item_unknown"):                          # no room for the whole quantity, or the item left the catalog: nothing is used up
                    raise GameError("gift_full" if e.code == "inv_full" else "gift_unavailable") from None
                raise
            if given is None:                                                        # its log row exists without a redemption: never give twice
                raise GameError("gift_used")
            out["item"], out["qty"], out["data"] = item_id, given, data
        elif reward == "stock_money":
            db.x("UPDATE users SET cash=cash+? WHERE id=?", (amount, uid))
            core.mint(db, amount)
            out["balance"] = core.get_user(db, uid)["cash"]
        elif reward == "life_cash":
            try:
                new = cash.apply(db, uid, amount, "gift", code, note="gift code", now=now)
            except GameError as e:
                if e.code == "cash_cap":
                    raise GameError("gift_unavailable") from None
                raise
            if new is None:                                                          # its ledger row exists without a redemption: never pay twice
                raise GameError("gift_used")
            out["balance"] = new
        else:
            new = charxp.grant(db, uid, amount, "gift", code, now)
            if new is None:
                raise GameError("gift_used")
            out["xp"], out["level"] = new, charxp.level_for(new)
        return out
