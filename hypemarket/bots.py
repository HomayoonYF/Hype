"""Automated market traders: clearly labelled 🤖 accounts that keep prices moving like a real market.

Safety rules (so they can't be farmed or break the economy):
* they use the normal buy/sell code, so every trade follows the same curve and the economy stays solvent;
* they pay the normal fee, but NOBODY earns it (it is burned): owners can't farm them with throw-away stocks;
* they only trade established stocks: >= 30 minutes old, >= 30 shares and >= 2 independent real (non-bot, non-owner) holders, not recently rugged;
* small orders only, capped position size (value and % of supply), and a capped daily top-up of their cash;
* they never appear in leaderboards, seasons, quests, referrals or whale news.
"""
from __future__ import annotations

import random

from . import config as C
from . import core, engine as E, trading
from .core import GameError
from .db import Database

PERSONAS = [
    (-1, "🤖 Atlas", "momentum"),
    (-2, "🤖 Nova", "contrarian"),
    (-3, "🤖 Orion", "noise"),
    (-4, "🤖 Hedge", "holder"),
    (-5, "🤖 Bull", "momentum"),
    (-6, "🤖 Bear", "contrarian"),
]


def ensure_bots(db: Database) -> None:
    with db.tx():
        for bid, name, _ in PERSONAS:
            if core.get_user(db, bid):
                continue
            db.x(
                "INSERT INTO users(id,username,name,lang,cash,created_at,rep,season_base,bot,activated) VALUES(?,?,?,?,?,?,?,?,1,1)",
                (bid, None, name, "fa", C.BOT_CASH_TARGET, core.now(), C.REP_START, C.BOT_CASH_TARGET),
            )
            core.mint(db, C.BOT_CASH_TARGET)


def eligible_stocks(db: Database) -> list:
    t = core.now()
    out = []
    for s in db.qa(
        "SELECT * FROM stocks WHERE status='active' AND kind IN ('personal','meme','project','bluechip','company') AND supply>=? AND created_at<=?",
        (C.BOT_MIN_SUPPLY, t - C.BOT_MIN_AGE),
    ):
        if s["last_rug"] and t - s["last_rug"] < C.RUG_COOLDOWN:
            continue
        real = db.q1(
            "SELECT COUNT(*) c FROM holdings h JOIN users u ON u.id=h.user_id WHERE h.stock_id=? AND u.bot=0 AND h.user_id!=?",
            (s["id"], s["owner_id"] or 0),
        )["c"]                                                   # independent investors: the owner doesn't count
        if real >= C.BOT_MIN_REAL_HOLDERS:
            out.append(s)
    return out


def _topup(db: Database, bid: int) -> None:
    with db.tx():
        u = core.get_user(db, bid)
        if u["cash"] >= C.BOT_CASH_TARGET // 2:
            return
        key = f"bottop:{bid}:{core.now() // 86400}"
        used = core.kv_int(db, key)
        amount = min(C.BOT_CASH_TARGET - u["cash"], C.BOT_DAILY_TOPUP - used)
        if amount <= 0:
            return
        db.x("UPDATE users SET cash=cash+? WHERE id=?", (amount, bid))
        core.mint(db, amount)
        core.kv_set(db, key, used + amount)


def _gain_pct(h, s) -> float:
    return (core.liq(s, h["shares"]) - h["cost"]) * 100.0 / h["cost"] if h["cost"] else 0.0


def _choose(db: Database, style: str, stocks: list, held: dict, rng):
    """Returns ("buy"|"sell", stock_row) or None."""
    sig = {s["id"]: core.change_pct(db, s, 3600) for s in stocks}
    held_stocks = [core.stock_row(db, sid) for sid in held]
    held_stocks = [s for s in held_stocks if s and s["status"] == "active"]
    if style == "momentum":
        losers = [s for s in held_stocks if core.change_pct(db, s, 3600) < -3]
        if losers and rng.random() < 0.6:
            return "sell", rng.choice(losers)
        up = [s for s in stocks if sig[s["id"]] > 0.5]
        if up and rng.random() < 0.8:
            return "buy", max(up, key=lambda s: sig[s["id"]])
    elif style == "contrarian":
        rich = [s for s in held_stocks if _gain_pct(held[s["id"]], s) > 8]
        if rich and rng.random() < 0.7:
            return "sell", rng.choice(rich)
        dips = [s for s in stocks if sig[s["id"]] < -3]
        if dips and rng.random() < 0.8:
            return "buy", min(dips, key=lambda s: sig[s["id"]])
    elif style == "holder":
        rich = [s for s in held_stocks if _gain_pct(held[s["id"]], s) > 12]
        if rich and rng.random() < 0.6:
            return "sell", rng.choice(rich)
        if stocks and rng.random() < 0.35:
            return "buy", max(stocks, key=core.mcap)
        return None
    if not stocks:
        return None
    s = rng.choice(stocks)           # "noise" style, and fallback for the others
    if s["id"] in held and rng.random() < 0.5:
        return "sell", s
    return "buy", s


def _execute(db: Database, bid: int, action: str, s, held: dict, rng):
    s = core.stock_row(db, s["id"])
    h = held.get(s["id"])
    if action == "buy":
        have = h["shares"] if h else 0
        if have * 100 >= s["supply"] * C.BOT_MAX_SUPPLY_PCT:
            return None
        if have and core.liq(s, have) >= C.BOT_MAX_POSITION:
            return None
        u = core.get_user(db, bid)
        budget = int(min(u["cash"], u["cash"] * 0.08, max(C.coins(5), s["reserve"] * 0.015)))
        n = E.max_affordable(s["supply"], budget, s["p0"], s["k"], core.eff(s), trading.trade_fee_bps(db, bid))
        n = min(n, max(0, s["supply"] * C.BOT_MAX_SUPPLY_PCT // 100 - have))
        if n <= 0:
            return None
        return trading.buy(db, bid, s["ticker"], n=n)
    if not h:
        return None
    n = max(1, int(h["shares"] * rng.uniform(0.2, 0.6)))
    return trading.sell(db, bid, s["ticker"], n=n, force=True)


def tick(db: Database, rng=None) -> int:
    """One 30-second heartbeat of the market traders. Returns the number of trades made."""
    rng = rng or random
    trades = 0
    stocks = eligible_stocks(db)
    for bid, _name, style in PERSONAS:
        if rng.random() > C.BOT_ACT_PROB:
            continue
        try:
            _topup(db, bid)
            u = core.get_user(db, bid)
            if u and u["pending_div"] >= C.coins(1):
                trading.claim_dividends(db, bid)
            held = {r["stock_id"]: r for r in db.qa("SELECT * FROM holdings WHERE user_id=?", (bid,))}
            pick = _choose(db, style, stocks, held, rng)
            if pick and _execute(db, bid, pick[0], pick[1], held, rng):
                trades += 1
        except GameError:
            continue
    return trades
