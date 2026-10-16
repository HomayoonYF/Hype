"""Shared building blocks used by every game module."""
from __future__ import annotations

import json
import re
import time

from . import config as C
from . import engine as E
from .db import Database


class GameError(Exception):
    """A player-facing error. `code` maps to the text key  e.<code>."""

    def __init__(self, code: str, **kw):
        super().__init__(code)
        self.code = code
        self.kw = kw


# --- clock (replaceable in tests) -------------------------------------------
_clock = time.time


def now() -> int:
    return int(_clock())


def set_clock(fn) -> None:
    global _clock
    _clock = fn


# --- key/value store & money accounting ---------------------------------------
def kv_get(db: Database, key: str, default=None):
    r = db.q1("SELECT value FROM kv WHERE key=?", (key,))
    return r["value"] if r else default


def kv_set(db: Database, key: str, value) -> None:
    db.x("INSERT INTO kv(key,value) VALUES(?,?) ON CONFLICT(key) DO UPDATE SET value=excluded.value", (key, str(value)))


def kv_int(db: Database, key: str, default: int = 0) -> int:
    v = kv_get(db, key)
    try:
        return int(v) if v is not None else default
    except ValueError:
        return default


def kv_add(db: Database, key: str, delta: int) -> int:
    v = kv_int(db, key) + delta
    kv_set(db, key, v)
    return v


def counter(db: Database, name: str, n: int = 1) -> None:
    """Per-day analytics counter (kept 60 days) plus a lifetime total `tot:<name>` (the exchange reads deltas of it)."""
    kv_add(db, f"ctr:{now() // 86400}:{name}", n)
    kv_add(db, f"tot:{name}", n)


def total_get(db: Database, name: str) -> int:
    return kv_int(db, f"tot:{name}")


def counter_get(db: Database, day: int, name: str) -> int:
    return kv_int(db, f"ctr:{day}:{name}")


def _mark_day(db: Database) -> None:
    """Remember the money supply at the first mint/burn of each day, so daily totals are exact."""
    day = now() // 86400
    if kv_get(db, f"mint_at:{day}") is None:
        kv_set(db, f"mint_at:{day}", kv_int(db, "minted"))
        kv_set(db, f"burn_at:{day}", kv_int(db, "burned"))


def mint(db: Database, n: int) -> None:
    """Coins created from nothing (bonuses, rewards). Tracked for monitoring."""
    if n > 0:
        _mark_day(db)
        kv_add(db, "minted", n)


def burn(db: Database, n: int) -> None:
    """Coins destroyed (fees, launch costs, taxes)."""
    if n > 0:
        _mark_day(db)
        kv_add(db, "burned", n)


def notify(db: Database, uid: int, key: str, **kw) -> None:
    if uid > 0:   # automated market traders have negative ids and no chat
        db.outbox.append((uid, key, kw))


# --- text safety ----------------------------------------------------------------
_CTRL = re.compile(r"[\x00-\x1f\x7f\u202a-\u202e\u2066-\u2069]")
_BAD = re.compile(r"(https?:|t\.me|telegram\.me|www\.|@|\.com|\.ir|\.org|\.net)", re.I)
TICKER_RE = re.compile(r"^[A-Z][A-Z0-9]{1,9}$")
RESERVED = {"IDX", "BOT", "ADMIN", "TELEGRAM", "NULL", "NONE", "ALL", "MAX", "SELL", "BUY"}


def clean(text: str, maxlen: int) -> str:
    return _CTRL.sub("", text or "").strip()[:maxlen]


def check_text(text: str, minlen: int = 1) -> None:
    if len(text) < minlen or _BAD.search(text):
        raise GameError("bad_text")


def norm_ticker(x: str) -> str:
    x = (x or "").strip().lstrip("$#").upper()
    return x.translate(str.maketrans("۰۱۲۳۴۵۶۷۸۹", "0123456789"))


# --- users ----------------------------------------------------------------------
def get_user(db: Database, uid: int):
    return db.q1("SELECT * FROM users WHERE id=?", (uid,))


def need_user(db: Database, uid: int):
    u = get_user(db, uid)
    if not u:
        raise GameError("no_user")
    return u


def perk(db: Database, uid: int, key: str) -> int:
    r = db.q1("SELECT level FROM perks WHERE user_id=? AND perk=?", (uid, key))
    return r["level"] if r else 0


def start_cash_for(db: Database, uid: int) -> int:
    return C.START_CASH * (100 + 20 * perk(db, uid, "head")) // 100


def ensure_user(db: Database, uid: int, username: str | None, name: str, lang: str = "fa", referrer: int | None = None):
    """Create the player on first contact. Returns (row, created)."""
    name = clean(name, 40) or "player"
    with db.tx():
        u = get_user(db, uid)
        if u:
            if (u["username"], u["name"]) != (username, name):
                db.x("UPDATE users SET username=?, name=? WHERE id=?", (username, name, uid))
                u = get_user(db, uid)
            return u, False
        cash = C.START_CASH
        ref = None
        if referrer and referrer != uid and get_user(db, referrer):
            ref = referrer
            cash += C.REF_BONUS_NEW
        db.x(
            "INSERT INTO users(id,username,name,lang,cash,created_at,rep,season_base,referrer) VALUES(?,?,?,?,?,?,?,?,?)",
            (uid, username, name, lang, cash, now(), C.REP_START, cash, ref),
        )
        mint(db, cash)
        db.x("UPDATE users SET nw=? WHERE id=?", (cash, uid))
        return get_user(db, uid), True


def display_name(u, with_badge: bool = True) -> str:
    base = u["name"] or (("@" + u["username"]) if u["username"] else str(u["id"]))
    return f"{u['badge']} {base}".strip() if with_badge and u["badge"] else base


# --- stocks ---------------------------------------------------------------------
def stock_row(db: Database, sid: int):
    return db.q1("SELECT * FROM stocks WHERE id=?", (sid,))


def need_stock(db: Database, ticker: str, active: bool = True):
    t = norm_ticker(ticker)
    s = db.q1("SELECT * FROM stocks WHERE ticker=?", (t,))
    if not s:
        raise GameError("no_stock", ticker=t)
    if active and s["status"] != "active":
        raise GameError("delisted", ticker=t)
    return s


def eff(s) -> int:
    return s["base_bps"] * s["event_bps"] // C.BPS


def price(s) -> int:
    return E.spot(s["supply"], s["p0"], s["k"], eff(s))


def mcap(s) -> int:
    return price(s) * s["supply"]


def liq(s, n: int) -> int:
    """What selling n shares right now pays, before fees."""
    return E.quote_sell(s["supply"], n, s["p0"], s["k"], eff(s))


def resync(db: Database, sid: int) -> None:
    """Make reserve == F(supply) after the price multiplier changed (mint or burn the difference)."""
    s = stock_row(db, sid)
    target = E.f_eff(s["supply"], s["p0"], s["k"], eff(s))
    d = target - s["reserve"]
    if d:
        (mint if d > 0 else burn)(db, abs(d))
        db.x("UPDATE stocks SET reserve=? WHERE id=?", (target, sid))


def set_event(db: Database, sid: int, bps: int, until: int, kind: str) -> None:
    db.x("UPDATE stocks SET event_bps=?, event_until=?, event_kind=? WHERE id=?", (bps, until, kind, sid))
    resync(db, sid)
    record(db, sid)   # so charts show the jump


def clear_event(db: Database, sid: int) -> None:
    set_event(db, sid, C.BPS, 0, "")


def record(db: Database, sid: int) -> None:
    s = stock_row(db, sid)
    db.x("INSERT INTO history(stock_id,ts,price,supply) VALUES(?,?,?,?)", (sid, now(), price(s), s["supply"]))


def record_stale(db: Database, secs: int = 300) -> int:
    """Add a price point for every live stock that has none in the last `secs` (keeps charts continuous)."""
    t = now()
    rows = db.qa(
        "SELECT s.id FROM stocks s WHERE s.status='active' AND s.supply>0 AND "
        "COALESCE((SELECT MAX(ts) FROM history h WHERE h.stock_id=s.id),0) < ?", (t - secs,))
    for r in rows:
        record(db, r["id"])
    return len(rows)


def price_ago(db: Database, sid: int, secs: int) -> int | None:
    r = db.q1("SELECT price FROM history WHERE stock_id=? AND ts<=? ORDER BY ts DESC LIMIT 1", (sid, now() - secs))
    if r:
        return r["price"]
    r = db.q1("SELECT price FROM history WHERE stock_id=? ORDER BY ts ASC LIMIT 1", (sid,))
    return r["price"] if r else None


def change_pct(db: Database, s, secs: int = 86400) -> float:
    p = price_ago(db, s["id"], secs)
    if not p:
        return 0.0
    return (price(s) - p) * 100.0 / p


def add_news(db: Database, kind: str, sid: int | None = None, **params) -> None:
    db.x("INSERT INTO news(ts,kind,stock_id,params) VALUES(?,?,?,?)", (now(), kind, sid, json.dumps(params, ensure_ascii=False)))


# --- dividends ------------------------------------------------------------------
def add_dividend(db: Database, sid: int, amount: int) -> bool:
    """Spread `amount` units over all current holders of the stock. False when nobody holds it."""
    if amount <= 0:
        return True
    s = stock_row(db, sid)
    if not s or s["supply"] <= 0 or s["status"] != "active":
        return False
    db.x("UPDATE stocks SET acc_div=acc_div+? WHERE id=?", (amount * C.ACC // s["supply"], sid))
    return True


def settle(db: Database, uid: int, h, s) -> int:
    """Move a holding's accrued dividends into the player's pending_div."""
    owed = h["shares"] * s["acc_div"] // C.ACC - h["debt"]
    if owed > 0:
        db.x("UPDATE users SET pending_div=pending_div+? WHERE id=?", (owed, uid))
    return max(owed, 0)


# --- net worth ------------------------------------------------------------------
def holdings_rows(db: Database, uid: int) -> list:
    return db.qa(
        "SELECT h.shares, h.cost, h.debt, h.last_buy, h.last_bonus, s.* FROM holdings h "
        "JOIN stocks s ON s.id=h.stock_id WHERE h.user_id=? AND s.status='active' ORDER BY h.shares DESC",
        (uid,),
    )


def net_worth(db: Database, uid: int) -> int:
    u = need_user(db, uid)
    total = u["cash"] + u["pending_div"] + u["pending_inc"]
    for r in holdings_rows(db, uid):
        total += liq(r, r["shares"])
    return total


def touch_nw(db: Database, uid: int) -> int:
    nw = net_worth(db, uid)
    db.x("UPDATE users SET nw=? WHERE id=?", (nw, uid))
    return nw


def check_level(db: Database, uid: int):
    """Refresh the cached net worth and raise max_level if earned. Returns (net_worth, new_level or None)."""
    nw = touch_nw(db, uid)
    u = need_user(db, uid)
    lvl = E.level_for(nw)
    if lvl > u["max_level"]:
        db.x("UPDATE users SET max_level=? WHERE id=?", (lvl, uid))
        from . import achievements
        achievements.check(db, uid, "level")
        return nw, lvl
    return nw, None
