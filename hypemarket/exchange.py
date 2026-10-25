"""The Rofaqa Exchange: six house-priced companies that nobody can pump or dump.

How it works
============
* Every company is a normal row of `stocks` with kind='listed' and a FLAT price curve (k = 0). The price is `p0`, and `p0`
  is moved only by the oracle below, never by trades. The house is the counterparty: when a price rises the reserve is topped
  up with new coins, when it falls the surplus is burned (core.resync). That is the "house P&L".
* A price is  A * exp(y)  where
    A  is a random walk of hidden, unpredictable noise (zero-mean multiplicative steps, so the price is a martingale), and
    y  is the "heat": a small, fast-fading push from REAL game activity (chat messages, payouts, meme trades, volume, daily
       claims, trust actions). Only the SURPRISE counts: activity is compared with what the model expected (learned level,
       lag-1 correlation removed, and the average surprise of each half hour of the day cancelled), scaled by a floor (so tiny numbers
       never create huge surprises), clipped to +-3 sigma; the heat is capped at +-EX_YCAP and decays with a half-life of a few hours.
* Why that cannot be farmed (all verified by tests/test_exchange.py, which attacks it):
    - past prices carry no information (trend / mean-reversion / time-of-day / ramp strategies all lose);
    - anybody who steers or knows the activity (even an insider with a swarm of accounts) can swing a price by at most
      2 x EX_YCAP (+ a tiny earnings part), which is below the cheapest round trip (the levy on each side, minus the
      most a lone holder can get back of it), and the levy also grows with the size of recent market-wide flow (slippage),
      so alts can't dodge it;
    - nothing here mints coins on a schedule (no yield, no holder bonus): holders can only gain from the random walk and from
      the share of OTHER traders' levies (a seller never shares in the levy of his own sale).
* The price moves are announced AFTER they happen (headlines with a statement of the company itself; never a made-up person) and every week there is an earnings night: a bigger,
  random, zero-mean jump plus a report built from the real numbers of the week.
"""
from __future__ import annotations

import datetime
import json
import math
import random
from dataclasses import dataclass

from . import config as C
from . import engine as E
from .core import (
    add_dividend, add_news, burn, counter, kv_add, kv_get, kv_int, kv_set, notify, now, price, price_ago, record, resync, stock_row,
)
from .db import Database

_SYS = random.SystemRandom()      # hidden randomness: nobody can reproduce or predict it
SLOTS = 48                        # half-hour slots of the day (the bias of each one is learned and cancelled)


@dataclass(frozen=True)
class Company:
    key: str
    ticker: str
    emoji: str
    en_name: str
    p0: int                       # listing price (units)
    drivers: tuple                # ((counter name, weight), ...): what real activity moves this company


COMPANIES = (
    Company("chat", "CHAT", "💬", "Chatterbox", C.coins(68.5), (("chat", 1.0),)),
    Company("bank", "RBNK", "🏦", "Rofaqa Bank", C.coins(131.2), (("payouts", 1.0),)),
    Company("meme", "MEME", "⛏", "Meme Mining Co", C.coins(14.8), (("meme_trades", 1.0),)),
    Company("levi", "LEVI", "🐋", "Leviathan Capital", C.coins(256.8), (("volume", 1.0 / C.coins(100)),)),
    Company("strk", "STRK", "⚡", "Streak Energy", C.coins(42.3), (("dailies", 1.0),)),
    Company("trst", "TRST", "🛡", "Trust Shield", C.coins(96.4), (("votes", 1.0), ("reports", 2.0), ("modact", 3.0))),
)
BY_KEY = {c.key: c for c in COMPANIES}
RHO_Y = 0.5 ** (1.0 / C.EX_Y_HALF_LIFE)


# ---------------------------------------------------------------------------------- lookups
def stock_id(db: Database, key: str) -> int | None:
    v = kv_get(db, f"exid:{key}")
    return int(v) if v is not None else None


def company_of(db: Database, s) -> Company | None:
    if s is None or s["kind"] != "listed":
        return None
    k = kv_get(db, f"exk:{s['id']}")
    return BY_KEY.get(k) if k else None


def listed_rows(db: Database) -> list:
    """The listed companies in display order (even before anybody holds a share)."""
    out = []
    for co in COMPANIES:
        sid = stock_id(db, co.key)
        s = stock_row(db, sid) if sid else None
        if s and s["status"] == "active":
            out.append((co, s))
    return out


# ---------------------------------------------------------------------------------- the oracle (pure maths)
def new_state(p: int, t: int, totals: dict) -> dict:
    return {"A": float(p), "y": 0.0, "L": 0.0, "V": 0.0, "c1": 0.0, "dprev": 0.0, "B": [0.0] * SLOTS, "n": 0, "ts": t,
            "last": totals, "listing": p, "wk0": dict(totals), "wk_prev": None, "wk_price": p}


def slot_of(t: int) -> int:
    return (t % 86400) // 1800


def _clip(v: float, lim: float) -> float:
    return max(-lim, min(lim, v))


def oracle_step(st: dict, x: float, slot: int, steps: float, rng) -> dict:
    """Advance the state by `steps` ticks (normally 1) given the activity `x` seen per tick. Mutates `st`.
    Returns {"z": surprise, "eps": hidden noise step, "y": new heat}."""
    n = st["n"]
    rho = _clip(st["c1"] / st["V"], 0.8) if st["V"] > 1e-9 and st["c1"] > 0 else 0.0     # lag-1 correlation of surprises (kept >= 0)
    expected = max(0.0, st["L"] + rho * st["dprev"]) if n > 0 else x
    d = x - expected
    sigma = max(math.sqrt(max(st["V"], 0.0)), C.EX_FLOOR_FRAC * expected + C.EX_FLOOR_ABS)
    z = _clip(d / sigma, C.EX_ZCLIP) if n >= C.EX_WARMUP else 0.0
    zc = z - st["B"][slot]
    st["y"] = _clip(st["y"] * RHO_Y ** steps + C.EX_BETA * zc, C.EX_YCAP)
    g = _clip(rng.gauss(0.0, 1.0), 3.5)
    eps = _clip(C.EX_NOISE * math.sqrt(steps) * g, C.EX_MAX_STEP * max(1.0, steps))
    lo, hi = st["listing"] * C.EX_PRICE_FLOOR, st["listing"] * C.EX_PRICE_CEIL
    st["A"] = min(hi, max(lo, st["A"] * (1.0 + eps)))
    # learn from what happened (AFTER the surprise was computed)
    k = 1.0 - (1.0 - C.EX_ALPHA_LEVEL) ** steps
    kv_ = 1.0 - (1.0 - C.EX_ALPHA_VAR) ** steps
    kb = 1.0 - (1.0 - C.EX_ALPHA_BIAS) ** steps
    if n == 0:
        st["L"] = x
    else:
        st["L"] += k * (x - st["L"])
    st["V"] += kv_ * (d * d - st["V"])
    st["c1"] += kv_ * (d * st["dprev"] - st["c1"])
    st["B"][slot] += kb * (z - st["B"][slot])
    st["dprev"] = d
    st["n"] = n + 1
    return {"z": z, "eps": eps, "y": st["y"]}


def state_price(st: dict) -> int:
    return max(1, int(round(st["A"] * math.exp(st["y"]))))


# ---------------------------------------------------------------------------------- state storage
def _load(db: Database, key: str) -> dict | None:
    v = kv_get(db, f"exs:{key}")
    return json.loads(v) if v else None


def _save(db: Database, key: str, st: dict) -> None:
    kv_set(db, f"exs:{key}", json.dumps(st, separators=(",", ":")))


def totals_now(db: Database, co: Company) -> dict:
    return {name: kv_int(db, f"tot:{name}") for name, _w in co.drivers}


def _weighted(co: Company, now_t: dict, then: dict) -> float:
    return sum(w * max(0, now_t.get(n, 0) - then.get(n, 0)) for n, w in co.drivers)


# ---------------------------------------------------------------------------------- price application
def _set_price(db: Database, sid: int, p: int) -> None:
    s = stock_row(db, sid)
    if s["p0"] == p:
        return
    before = s["reserve"]
    db.x("UPDATE stocks SET p0=? WHERE id=?", (p, sid))
    resync(db, sid)
    d = stock_row(db, sid)["reserve"] - before          # > 0: the house paid holders (coins minted); < 0: holders' loss burned
    if d:
        kv_add(db, "ex_pnl", -d)
        counter(db, "ex_mint" if d > 0 else "ex_burn", abs(d))
    record(db, sid)


# ---------------------------------------------------------------------------------- setup
def _free_ticker(db: Database, base: str) -> str:
    cand, i = base, 0
    while db.q1("SELECT 1 FROM stocks WHERE ticker=?", (cand,)):
        i += 1
        cand = base + ("X" if i == 1 else str(i))
    return cand


def _backfill(db: Database, sid: int, p: int, t: int, rng) -> None:
    """24 hours of plausible history so the very first chart is not empty (pure noise, no coins involved)."""
    pts, cur = [], float(p)
    for i in range(288):
        pts.append((t - i * 300, int(round(cur))))
        cur = max(1.0, cur / (1.0 + _clip(rng.gauss(0, C.EX_NOISE * math.sqrt(5)), 0.02)))
    db.conn.executemany("INSERT INTO history(stock_id,ts,price,supply) VALUES(?,?,?,0)", [(sid, ts, pr) for ts, pr in reversed(pts)])


def ensure_listed(db: Database, rng=None) -> int:
    """Create the companies that don't exist yet (idempotent). Returns how many were created."""
    rng = rng or _SYS
    created = 0
    t = now()
    with db.tx():
        for co in COMPANIES:
            sid = stock_id(db, co.key)
            if sid and stock_row(db, sid):
                continue
            tk = _free_ticker(db, co.ticker)
            cur = db.x(
                "INSERT INTO stocks(ticker,name,descr,kind,p0,k,created_at) VALUES(?,?,?,?,?,?,?)",
                (tk, co.en_name, "", "listed", co.p0, 0, t),
            )
            sid = cur.lastrowid
            kv_set(db, f"exid:{co.key}", sid)
            kv_set(db, f"exk:{sid}", co.key)
            kv_set(db, f"exn:{co.key}", t)                   # no headline about the (synthetic) backfilled past
            _save(db, co.key, new_state(co.p0, t, totals_now(db, co)))
            _backfill(db, sid, co.p0, t, rng)
            created += 1
        if created and kv_get(db, "ex_open") is None:
            kv_set(db, "ex_open", t)
            add_news(db, "ex_open", None)
        if kv_get(db, "ex_earn_week") is None and t >= sched_ts(t):
            kv_set(db, "ex_earn_week", week_id(t))           # this week's earnings night already passed: the first one is next week
    return created


# ---------------------------------------------------------------------------------- the tick
def tick(db: Database, rng=None) -> int:
    """Advance every company by the time that has passed (call every ~60 s). Returns the number of price changes."""
    rng = rng or _SYS
    t = now()
    changed = 0
    with db.tx():
        for co in COMPANIES:
            sid = stock_id(db, co.key)
            st = _load(db, co.key) if sid else None
            if not st:
                continue
            elapsed = t - st["ts"]
            if elapsed < C.EX_TICK * 0.8:
                continue
            steps = min(10.0, elapsed / C.EX_TICK)
            tot = totals_now(db, co)
            x = _weighted(co, tot, st["last"]) / steps
            oracle_step(st, x, slot_of(t), steps, rng)
            st["ts"], st["last"] = t, tot
            old = stock_row(db, sid)["p0"]
            p = state_price(st)
            _set_price(db, sid, p)
            if p != old:
                changed += 1
            _save(db, co.key, st)
            _maybe_news(db, co, sid, rng)
    return changed


def _maybe_news(db: Database, co: Company, sid: int, rng) -> None:
    s = stock_row(db, sid)
    if now() - kv_int(db, f"exn:{co.key}") < C.EX_NEWS_COOLDOWN:
        return
    for secs, thr in ((3600, C.EX_NEWS_1H), (6 * 3600, C.EX_NEWS_6H)):
        p_ago = price_ago(db, sid, secs)
        if not p_ago:
            continue
        chg = (price(s) - p_ago) / p_ago
        if abs(chg) >= thr:
            kv_set(db, f"exn:{co.key}", now())
            add_news(db, "ex_up" if chg > 0 else "ex_down", sid, key=co.key, ticker=s["ticker"], pct=round(abs(chg) * 100, 1), q=rng.choice((1, 2)))
            return


# ---------------------------------------------------------------------------------- earnings nights
def week_id(t: int) -> int:
    y, w, _d = datetime.datetime.fromtimestamp(t, datetime.timezone.utc).isocalendar()
    return y * 100 + w


def sched_ts(t: int) -> int:
    """When this ISO week's earnings night happens (UTC)."""
    dt = datetime.datetime.fromtimestamp(t, datetime.timezone.utc)
    monday = (dt - datetime.timedelta(days=dt.weekday())).replace(hour=0, minute=0, second=0, microsecond=0)
    return int((monday + datetime.timedelta(days=C.EX_EARN_WEEKDAY, hours=C.EX_EARN_HOUR)).timestamp())


def earnings_due(db: Database, t: int | None = None) -> bool:
    t = now() if t is None else t
    return t >= sched_ts(t) and str(kv_get(db, "ex_earn_week")) != str(week_id(t))


def next_earnings(db: Database, t: int | None = None) -> int:
    t = now() if t is None else t
    if earnings_due(db, t):
        return t
    cur = sched_ts(t)
    if t < cur and str(kv_get(db, "ex_earn_week")) != str(week_id(t)):
        return cur
    return sched_ts(t + 7 * 86400)


def run_earnings(db: Database, rng=None) -> dict | None:
    """Earnings night: every company gets a random, zero-mean jump (plus a tiny activity part) and a report of the week."""
    rng = rng or _SYS
    t = now()
    if not earnings_due(db, t):
        return None
    rows = []
    with db.tx():
        wk = week_id(t)
        for co in COMPANIES:
            sid = stock_id(db, co.key)
            st = _load(db, co.key) if sid else None
            if not st:
                continue
            tot = totals_now(db, co)
            cur = _weighted(co, tot, st["wk0"])
            prev = st["wk_prev"]
            z_week = _clip((cur - prev) / max(prev * 0.25, 1.0), C.EX_ZCLIP) if prev else 0.0
            jump = _clip(C.EX_EARN_BETA * z_week + rng.gauss(0.0, C.EX_EARN_SD), C.EX_EARN_MAX)
            lo, hi = st["listing"] * C.EX_PRICE_FLOOR, st["listing"] * C.EX_PRICE_CEIL
            st["A"] = min(hi, max(lo, st["A"] * (1.0 + jump)))
            p = state_price(st)
            _set_price(db, sid, p)
            wk_pct = (p - st["wk_price"]) / st["wk_price"] if st["wk_price"] else 0.0
            act = (1 if cur >= prev else -1) if prev else (1 if jump >= 0 else -1)      # did the REAL activity of the week grow? (the company's statement talks about this)
            row = {"key": co.key, "ticker": stock_row(db, sid)["ticker"], "price": p, "jump": round(jump * 100, 2), "wk_pct": round(wk_pct * 100, 2),
                   "metric": round(cur, 1), "prev": round(prev, 1) if prev is not None else None, "act": act, "q": rng.choice((1, 2))}
            rows.append(row)
            st["wk0"], st["wk_prev"], st["wk_price"], st["last"], st["ts"] = dict(tot), cur, p, tot, t
            _save(db, co.key, st)
            add_news(db, "ex_earn", sid, key=co.key, ticker=row["ticker"], pct=row["wk_pct"], jump=row["jump"], act=act, q=row["q"])
        kv_set(db, "ex_earn_week", wk)
        rep = {"ts": t, "week": wk, "rows": rows}
        kv_set(db, "ex_rep", json.dumps(rep, separators=(",", ":")))
        counter(db, "ex_earnings")
    return rep


def last_report(db: Database) -> dict | None:
    v = kv_get(db, "ex_rep")
    return json.loads(v) if v else None


# ---------------------------------------------------------------------------------- trading costs (used by trading.buy/sell)
def flow_now(db: Database, sid: int, t: int | None = None) -> float:
    t = now() if t is None else t
    r = db.q1("SELECT flow, ts FROM exflow WHERE stock_id=?", (sid,))
    if not r:
        return 0.0
    return r["flow"] * 0.5 ** (max(0, t - r["ts"]) / C.LISTED_FLOW_HALF_LIFE)


def add_flow(db: Database, sid: int, notional: int, t: int | None = None) -> None:
    t = now() if t is None else t
    f = flow_now(db, sid, t) + notional
    db.x("INSERT INTO exflow(stock_id,flow,ts) VALUES(?,?,?) ON CONFLICT(stock_id) DO UPDATE SET flow=excluded.flow, ts=excluded.ts", (sid, f, t))


def slip_bps_x100(flow: float, notional: int) -> int:
    """Slippage in hundredths of a basis point: it grows with the market-wide flow of the last minutes (all players together)."""
    v = C.LISTED_SLIP_COEF_BPS * (flow + notional / 2.0) / C.LISTED_DEPTH
    return int(min(C.LISTED_SLIP_MAX_BPS, v) * 100)


def levy(db: Database, s, notional: int, spread_bps: int | None = None, slippage: bool = True) -> int:
    """Total cost (units, rounded up) of trading `notional` of this stock: fixed spread + size/flow-based slippage."""
    if notional <= 0:
        return 0
    x100 = (C.LISTED_SPREAD_BPS if spread_bps is None else spread_bps) * 100
    if slippage:
        x100 += slip_bps_x100(flow_now(db, s["id"]), notional)
    return -(-notional * x100 // (C.BPS * 100))


def max_affordable(db: Database, s, budget: int) -> int:
    """Largest number of shares whose cost plus levy fits the budget."""
    p0, supply, eff = s["p0"], s["supply"], s["base_bps"] * s["event_bps"] // C.BPS

    def total(n: int) -> int:
        c = E.quote_buy(supply, n, p0, 0, eff)
        return c + levy(db, s, c)

    pr = max(1, p0 * eff // C.BPS)
    hi = min(C.MAX_SHARES_PER_TRADE, budget // pr)
    if hi <= 0 or total(1) > budget:
        return 0
    lo = 1
    while lo < hi:
        mid = (lo + hi + 1) // 2
        if total(mid) <= budget:
            lo = mid
        else:
            hi = mid - 1
    return lo


def distribute_levy(db: Database, sid: int, fee: int) -> int:
    """Part of every levy goes to the holders as a dividend (never more than half), the rest is burned. Returns the dividend."""
    if fee <= 0:
        return 0
    div = fee * C.LISTED_HOLDER_SHARE_BPS // C.BPS
    if div and not add_dividend(db, sid, div):
        div = 0                                          # nobody holds shares yet: it burns
    burn(db, fee - div)
    counter(db, "ex_levy", fee)
    if div:
        counter(db, "ex_levy_div", div)
    return div


# ---------------------------------------------------------------------------------- info for the views and the admin dashboard
def house_stats(db: Database) -> dict:
    day = now() // 86400
    from .core import counter_get
    ctr = lambda name, d=day: counter_get(db, d, name)
    rows = listed_rows(db)
    mcap = sum(price(s) * s["supply"] for _co, s in rows)
    holders = db.q1("SELECT COUNT(DISTINCT user_id) c FROM holdings h JOIN stocks s ON s.id=h.stock_id WHERE s.kind='listed'")["c"]
    return {"pnl": kv_int(db, "ex_pnl"), "mcap": mcap, "holders": holders,
            "minted_today": ctr("ex_mint"), "burned_today": ctr("ex_burn"), "levy_today": ctr("ex_levy"), "div_today": ctr("ex_levy_div"),
            "trades_today": ctr("ex_trades"), "volume_today": ctr("ex_volume"), "levy_total": kv_int(db, "tot:ex_levy"),
            "minted_total": kv_int(db, "tot:ex_mint"), "burned_total": kv_int(db, "tot:ex_burn")}


def announce_migration(db: Database) -> int:
    """One-time DMs for players affected by the removal of short selling (written by the schema migration)."""
    n = 0
    with db.tx():
        for r in db.qa("SELECT key, value FROM kv WHERE key LIKE 'mig5:%'"):
            try:
                uid = int(r["key"].split(":")[1])
                refund, pts = (int(x) for x in str(r["value"]).split(","))
            except ValueError:
                db.x("DELETE FROM kv WHERE key=?", (r["key"],))
                continue
            notify(db, uid, "short_removed", amount=refund, points=pts)
            db.x("DELETE FROM kv WHERE key=?", (r["key"],))
            n += 1
    return n
