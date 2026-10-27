"""Life cash and the currency exchange (v0.18).

TWO CURRENCIES, NEVER MIXED
    * stock money: `users.cash`, whole *units* (100 units = 1 🪙). The stock game, as before. Unchanged by this module except through the
      exchange below, which is the ONLY place where the two currencies meet.
    * life cash: `life_accounts.balance`, whole *cents* ($1.00 = 100). Wages, fares, rental fees. Exact integers everywhere, no floats.
Portfolio value, share prices and net worth are not money you can spend; nothing here reads them.

THE LEDGER
    Every change of a cash balance is `apply()`: one row of `life_ledger` (kind, ref, signed cents, balance after) written in the SAME
    transaction as the balance update. (user, kind, ref) is UNIQUE, so the same payout applied twice (a repeated button, a retry, a replayed
    task) hits the unique index and is a no-op that returns None: nothing is paid or charged a second time. A balance can never go below
    zero (CHECK + explicit test) or past CASH_CAP_CENTS.
    Starting cash: the ledger row ('grant','start') is unique per player, so $100 is paid exactly once however often a player is loaded,
    a map is opened, the bot restarts or the database is migrated. Cash belongs to the Telegram account (the player's id), not to a group.

THE EXCHANGE (ONE WAY since v0.18.2: stock money -> life cash. There is no way back.)
        1,000 🪙 = $5.00      <=>      2 🪙 = 1 cent      <=>      1 cent = 200 units (config.FX_UNITS_PER_CENT)      (v0.18 had 1,000 🪙 = $20; the rate is unchanged since v0.18.1)
    Stock money to cash: whole coins in MULTIPLES OF config.fx_coin_step() (2 at this rate: every 2 🪙 is exactly 1 cent, so there is never a remainder).
    Anything else (a fraction of a coin, an odd number of coins, zero, negative, junk, 1e9, NaN, a number with too many digits) is refused
    with the allowed form; nothing is rounded silently.
    WHY ONE WAY: cash -> stock money let every extra account turn its free $100 into stock money and pour it into a main account. Now
    cash can never become stock money: the exchange has no such direction at all (no function, no button, no command, no callback) and a
    preview of the old kind that is still stored in a database (an open v0.18.x "c2s" quote) is refused when someone tries to confirm it.
    The ledger keeps the old rows of the time when both ways existed; nothing in it is rewritten.
    Flow: a QUOTE (stored on the server, with an expiry) shows exactly what will move; only the quote's id travels in the confirm button,
    never an amount. Confirming flips the quote open -> done and moves both balances in ONE transaction; a repeated press finds it done.
    The coins that leave the stock economy are counted as burned (the supply bookkeeping stays exact), and the player's cached net worth /
    level is refreshed through the stock game's own function.
"""
from __future__ import annotations

import re
import secrets
import sqlite3
from dataclasses import dataclass

from . import config as C, core
from .core import GameError

KINDS_SHOWN = 6                         # how many ledger rows the wallet screen lists


# =====================================================================================================================
#                                                  accounts and the ledger
# =====================================================================================================================
def get_account(db, uid: int):
    return db.q1("SELECT * FROM life_accounts WHERE user_id=?", (uid,))


def ensure_account(db, uid: int, now: int | None = None):
    """The player's cash account; created, with the one-time $100 grant, the first time it is needed. Idempotent."""
    row = get_account(db, uid)
    if row is not None:
        return row
    u = core.need_user(db, uid)
    if u["bot"]:
        raise GameError("no_user")
    now = core.now() if now is None else now
    with db.tx():
        db.x("INSERT OR IGNORE INTO life_accounts(user_id,balance,created_at,updated_at) VALUES(?,0,?,?)", (uid, now, now))
        apply(db, uid, C.CASH_START_CENTS, "grant", "start", now=now)          # no-op if some earlier run already paid it
    return get_account(db, uid)


def balance(db, uid: int) -> int:
    """Cents the player can spend (creates the account on first use)."""
    return ensure_account(db, uid)["balance"]


def apply(db, uid: int, delta: int, kind: str, ref: str, *, stock: int = 0, note: str = "", now: int | None = None):
    """Add `delta` cents (negative = spend) to the player's cash, once. Returns the new balance, or None when (uid, kind, ref) was applied
    before (nothing changes then). Raises GameError('cash_low') when a spend is more than the balance, ('cash_cap') when the balance would
    pass the cap. Atomic with the ledger row: either both happen or neither."""
    if not isinstance(delta, int) or isinstance(delta, bool) or delta == 0:
        raise ValueError("cash delta must be a non-zero integer number of cents")
    now = core.now() if now is None else now
    with db.tx():
        row = get_account(db, uid)
        if row is None:
            if kind == "grant":                                                  # creating the account is ensure_account's job
                raise ValueError("no account")
            row = ensure_account(db, uid, now)
        bal = row["balance"]
        new = bal + delta
        if new < 0:
            raise GameError("cash_low", need=-delta, have=bal)
        if new > C.CASH_CAP_CENTS:
            raise GameError("cash_cap")
        try:
            db.x("INSERT INTO life_ledger(user_id,kind,ref,delta,balance_after,stock_delta,note,created_at) VALUES(?,?,?,?,?,?,?,?)",
                 (uid, kind, str(ref), delta, new, int(stock), note[:80], now))
        except sqlite3.IntegrityError:                                           # the same payout again: already done
            return None
        if db.x("UPDATE life_accounts SET balance=?, updated_at=? WHERE user_id=? AND balance=?", (new, now, uid, bal)).rowcount != 1:
            raise RuntimeError("cash balance changed under a transaction")      # cannot happen (one writer); never silently continue
        return new


def history(db, uid: int, limit: int = KINDS_SHOWN) -> list:
    return db.qa("SELECT * FROM life_ledger WHERE user_id=? ORDER BY id DESC LIMIT ?", (uid, limit))


# =====================================================================================================================
#                                                  reading what the player typed
# =====================================================================================================================
_DIGITS = str.maketrans("۰۱۲۳۴۵۶۷۸۹٠١٢٣٤٥٦٧٨٩", "01234567890123456789")
_SEP = r"[,٬ '\u00a0\u202f]"                 # separators people type between groups of thousands
_WORDS_ALL = {"all", "max", "همه", "حداکثر", "کامل", "کل"}
_WHOLE = re.compile(rf"(?:\d{{1,15}}|\d{{1,3}}(?:{_SEP}\d{{3}}){{1,4}})")                 # «250», «1000», «1,000», «۱٬۰۰۰»; «1,00» or «5,25» are NOT numbers (a comma only ever groups thousands)


def _clean(token: str) -> str:
    s = str(token).strip().translate(_DIGITS)
    return s.replace("٫", ".").replace("\u200c", "").replace("\u200f", "").replace("\u200e", "").strip()


def _plain_int(s: str) -> str:
    return re.sub(_SEP, "", s)


def is_all(token) -> bool:
    return isinstance(token, str) and token.strip().lower() in _WORDS_ALL


def parse_coins(token) -> int | None:
    """A whole number of 🪙 (1 to FX_MAX_COINS), or None. «1,000», «۱٬۰۰۰», «250», «250 🪙» are fine; «2.5», «1e3», «-5», «0», «NaN», «1,00» are not."""
    if not isinstance(token, str):
        return None
    s = _clean(token)
    if s.endswith("🪙"):
        s = s[:-1].strip()
    if not _WHOLE.fullmatch(s):
        return None
    v = int(_plain_int(s))
    return v if 1 <= v <= C.FX_MAX_COINS else None


# =====================================================================================================================
#                                                       the exchange
# =====================================================================================================================
S2C = "s2c"                                  # stock money -> cash: the ONLY direction of the exchange (a stored "c2s" quote of v0.18.x is refused, see confirm())


@dataclass
class Quote:
    id: str
    uid: int
    direction: str
    src: int                 # what the player gives: units of stock money
    dst: int                 # what the player gets:  cents of cash
    expires_at: int
    state: str = "open"

    @property
    def coins(self) -> int:
        """The coin side of the trade as whole coins (always exact: the exchange moves a multiple of the coin step)."""
        return self.src // C.UNIT

    @property
    def units(self) -> int:
        return self.src

    @property
    def cents(self) -> int:
        return self.dst


def units_to_cents(units: int) -> int:
    """Exact value of whole units in cents (only called with multiples of FX_UNITS_PER_CENT)."""
    return units // C.FX_UNITS_PER_CENT


def _align(coins: int) -> int:
    """Round a coin amount DOWN to a multiple of the coin step (only used for limits like «all», never for something the player typed)."""
    step = C.fx_coin_step()
    return coins - coins % step


def max_coins(db, uid: int) -> int:
    """The most coins the player could exchange right now (whole coins, a multiple of the coin step)."""
    u = core.need_user(db, uid)
    return _align(max(0, u["cash"]) // C.UNIT)


def make_quote(db, uid: int, amount: int | None, now: int | None = None) -> Quote:
    """Prepare an exchange of STOCK MONEY INTO CASH and store it. `amount` is whole coins (a multiple of the coin step); None = everything possible
    (only whole steps move, so a fraction stays; the amount is also kept inside the exchange limit and what the cash cap still allows).
    Raises GameError: fx_amount (not a positive whole number inside the limits), fx_step (not a multiple of the coin step), fx_nothing («everything»
    was asked and there is nothing to move), fx_low_stock (not enough stock money), cash_cap (the dollars would pass the balance cap)."""
    if amount is not None and (not isinstance(amount, int) or isinstance(amount, bool)):
        raise GameError("fx_amount")                                              # floats, strings, booleans: never converted, never rounded
    now = core.now() if now is None else now
    with db.tx():
        acc = ensure_account(db, uid, now)
        u = core.need_user(db, uid)
        step = C.fx_coin_step()
        have = max(0, u["cash"]) // C.UNIT                                                          # whole coins (the fraction of a coin stays)
        room = C.fx_coins_for_cents(max(0, C.CASH_CAP_CENTS - acc["balance"]))                      # coins whose dollars still fit under the cap
        if amount is None:
            coins = _align(min(have, C.FX_MAX_COINS, room))
            if coins < step:
                raise GameError("fx_nothing" if have < step else "cash_cap")
        else:
            coins = amount
            if coins < 1 or coins > C.FX_MAX_COINS:
                raise GameError("fx_amount")
            if coins % step:                                                                        # an odd number of coins would be half a cent
                low = coins - coins % step
                raise GameError("fx_step", step=step, near=low if low >= step else step)
            if coins > have:
                raise GameError("fx_low_stock", need=coins * C.UNIT, have=u["cash"])
            if coins > room:
                raise GameError("cash_cap")
        src, dst = coins * C.UNIT, units_to_cents(coins * C.UNIT)
        db.x("UPDATE fx_quotes SET state='cancelled' WHERE user_id=? AND state='open'", (uid,))     # only the newest preview can be confirmed
        db.x("DELETE FROM fx_quotes WHERE created_at<?", (now - 86400,))
        for _ in range(12):
            qid = secrets.token_hex(4)
            try:
                db.x("INSERT INTO fx_quotes(id,user_id,direction,src,dst,state,created_at,expires_at) VALUES(?,?,?,?,?,'open',?,?)",
                     (qid, uid, S2C, src, dst, now, now + C.FX_QUOTE_TTL))
                return Quote(qid, uid, S2C, src, dst, now + C.FX_QUOTE_TTL)
            except sqlite3.IntegrityError:
                continue
    raise RuntimeError("could not create an exchange quote")


def get_quote(db, qid: str, uid: int) -> Quote | None:
    if not isinstance(qid, str) or not re.fullmatch(r"[0-9a-f]{8}", qid):
        return None
    r = db.q1("SELECT * FROM fx_quotes WHERE id=? AND user_id=?", (qid, uid))
    if r is None:
        return None
    return Quote(r["id"], r["user_id"], r["direction"], r["src"], r["dst"], r["expires_at"], r["state"])


@dataclass
class Receipt:
    quote: Quote
    stock_before: int
    stock_after: int
    cash_before: int
    cash_after: int


def confirm(db, uid: int, qid: str, now: int | None = None) -> Receipt:
    """Carry out a stored quote of this player, once. Raises GameError: fx_gone (unknown / not theirs / cancelled), fx_done (already done),
    fx_expired, fx_oneway (a quote of the removed cash -> stock direction: it is never carried out), or a shortage if the balance changed
    since the preview; in every error case nothing at all has changed."""
    now = core.now() if now is None else now
    with db.tx():
        old_kind = get_quote(db, qid, uid)
        voided = old_kind is not None and old_kind.direction != S2C
        if voided:                                                                 # only stock money -> cash exists; an old or forged other-direction row never moves a cent
            db.x("UPDATE fx_quotes SET state='cancelled' WHERE id=? AND user_id=? AND state='open'", (qid, uid))
    if voided:
        raise GameError("fx_oneway")                                               # raised AFTER the transaction, so voiding the stale row is kept
    with db.tx():
        q = get_quote(db, qid, uid)
        if q is None or q.state == "cancelled":
            raise GameError("fx_gone")
        if q.state == "done":
            raise GameError("fx_done")
        if q.expires_at <= now:
            raise GameError("fx_expired")
        if db.x("UPDATE fx_quotes SET state='done' WHERE id=? AND user_id=? AND state='open' AND direction=?", (qid, uid, S2C)).rowcount != 1:
            raise GameError("fx_done")
        acc = ensure_account(db, uid, now)
        u = core.need_user(db, uid)
        stock_before, cash_before = u["cash"], acc["balance"]
        if stock_before < q.src:
            raise GameError("fx_low_stock", need=q.src, have=stock_before)
        if db.x("UPDATE users SET cash=cash-? WHERE id=? AND cash>=?", (q.src, uid, q.src)).rowcount != 1:
            raise GameError("fx_low_stock", need=q.src, have=stock_before)
        core.burn(db, q.src)                                                       # the coins leave the stock economy
        if apply(db, uid, q.dst, "fx", q.id, stock=-q.src, note="stock->cash", now=now) is None:
            raise RuntimeError("exchange applied twice")                          # impossible (the quote is single-use); never pay silently twice
        core.check_level(db, uid)                                                  # net worth / level follow the new balance, like after any trade
        q.state = "done"
        after_stock = core.need_user(db, uid)["cash"]
        after_cash = get_account(db, uid)["balance"]
        return Receipt(q, stock_before, after_stock, cash_before, after_cash)


def cancel(db, uid: int, qid: str) -> bool:
    """Drop a preview. True if it was open and is now cancelled; anything else (done, unknown, someone else's) changes nothing."""
    with db.tx():
        return db.x("UPDATE fx_quotes SET state='cancelled' WHERE id=? AND user_id=? AND state='open'", (qid, uid)).rowcount == 1
