"""Chaos test: random players do random things; money must be conserved to the unit.

Conservation law:  everything that exists  ==  coins minted - coins burned
  everything = player cash + pending income/dividends + short margins + stock reserves
               + company treasuries + dividends accrued but not yet settled
"""
import random

import pytest

from hypemarket import config as C
from hypemarket import bots, core, events, progress, quests, social, trading
from hypemarket import engine as E
from hypemarket.core import GameError
from hypemarket.db import Database

T = [1_700_000_000]


def total_money(db):
    users = db.q1("SELECT COALESCE(SUM(cash+pending_div+pending_inc),0) v FROM users")["v"]
    margins = db.q1("SELECT COALESCE(SUM(margin),0) v FROM shorts")["v"]
    reserves = db.q1("SELECT COALESCE(SUM(reserve),0) v FROM stocks")["v"]
    treasuries = db.q1("SELECT COALESCE(SUM(treasury),0) v FROM companies")["v"]
    owed = 0
    for r in db.qa("SELECT h.shares, h.debt, s.acc_div FROM holdings h JOIN stocks s ON s.id=h.stock_id WHERE s.status='active'"):
        owed += r["shares"] * r["acc_div"] // C.ACC - r["debt"]
    return users + margins + reserves + treasuries + owed


def check(db):
    for s in db.qa("SELECT * FROM stocks WHERE status='active'"):
        assert s["reserve"] == core.E.f_eff(s["supply"], s["p0"], s["k"], core.eff(s)), s["ticker"]
        held = db.q1("SELECT COALESCE(SUM(shares),0) n FROM holdings WHERE stock_id=?", (s["id"],))["n"]
        assert held == s["supply"], s["ticker"]
    for s in db.qa("SELECT * FROM stocks WHERE status!='active'"):
        assert s["supply"] == 0 and s["reserve"] == 0
    expected = core.kv_int(db, "minted") - core.kv_int(db, "burned")
    got = total_money(db)
    assert abs(got - expected) <= 2000, (got, expected, got - expected)   # a few coins of rounding dust at most
    assert db.q1("SELECT COUNT(*) c FROM users WHERE cash<0 OR pending_div<0 OR pending_inc<0")["c"] == 0


@pytest.mark.parametrize("seed", [1, 2, 3])
def test_money_is_conserved_under_chaos(seed):
    rnd = random.Random(seed)
    random.seed(seed)
    T[0] = 1_700_000_000
    core.set_clock(lambda: T[0])
    db = Database(":memory:")
    progress.ensure_season(db)
    N = 14
    for i in range(1, N + 1):
        core.ensure_user(db, i, f"u{i}", f"User{i}", referrer=1 if i > 3 else None)
        db.x("UPDATE users SET cash=cash+? WHERE id=?", (C.coins(rnd.choice([0, 2000, 30_000])), i))
        core.mint(db, 0)
    # fix accounting for the cash we injected directly
    core.mint(db, db.q1("SELECT SUM(cash) s FROM users")["s"] - (core.kv_int(db, "minted") - core.kv_int(db, "burned")))
    for i in range(1, N + 1):
        db.x("INSERT INTO perks(user_id,perk,level) VALUES(?,?,1)", (i, "short"))
    bots.ensure_bots(db)
    C.BOT_ACT_PROB = 0.7
    social.ensure_group(db, -1, "G1")
    social.ensure_group(db, -2, "G2")
    tickers = []
    levels_given = {}
    errors = {}

    def attempt(fn, *a, **k):
        try:
            return fn(*a, **k)
        except GameError as e:
            errors[e.code] = errors.get(e.code, 0) + 1

    for step in range(3000):
        T[0] += rnd.randint(1, 900)
        uid = rnd.randint(1, N)
        roll = rnd.random()
        # everything below can happen on top of the main action, like in a live market
        if rnd.random() < 0.30:
            bots.tick(db, rnd)
        if rnd.random() < 0.03:
            progress.refresh_all_networth(db)
        if rnd.random() < 0.03:
            core.record_stale(db)
        if rnd.random() < 0.03:
            quests.listing(db, uid)
        if tickers and rnd.random() < 0.03:       # whale-sized order: up to a quarter of the reserve
            tk = rnd.choice(tickers)
            s = core.stock_row(db, db.q1("SELECT id FROM stocks WHERE ticker=?", (tk,))["id"])
            if s["status"] == "active":
                n = E.max_affordable(s["supply"], min(db.q1("SELECT cash FROM users WHERE id=?", (uid,))["cash"], s["reserve"] // 4), s["p0"], s["k"], core.eff(s), 200)
                if n:
                    attempt(trading.buy, db, uid, tk, n)
        if roll < 0.04 and len(tickers) < 6:
            tk = f"T{step}"
            kind = rnd.choice(["personal", "personal", "meme", "project"])
            if attempt(trading.launch, db, uid, tk, f"Name{step}", kind=kind, goal=C.coins(6000)):
                tickers.append(tk)
        elif roll < 0.46 and tickers:
            attempt(trading.buy, db, uid, rnd.choice(tickers), rnd.randint(1, 400), spend_all=rnd.random() < 0.1)
        elif roll < 0.62 and tickers:
            attempt(trading.sell, db, uid, rnd.choice(tickers), rnd.randint(1, 200), sell_all=rnd.random() < 0.3)
        elif roll < 0.67:
            attempt(progress.daily, db, uid)
        elif roll < 0.72:
            attempt(trading.claim_dividends, db, uid)
        elif roll < 0.77:
            attempt(trading.claim_income, db, uid)
        elif roll < 0.79 and tickers:
            attempt(trading.set_dividend, db, uid, rnd.randint(0, 50))
        elif roll < 0.81 and tickers:
            db.x("UPDATE users SET max_level=MAX(max_level,3) WHERE id=?", (uid,))
            attempt(progress.upgrade, db, uid, rnd.choice(["short", "fee", "daily", "bonus", "alerts"]))
        elif roll < 0.85 and tickers:
            attempt(trading.open_short, db, uid, rnd.choice(tickers), rnd.randint(1, 60))
        elif roll < 0.865 and tickers:
            attempt(trading.close_short, db, uid, rnd.choice(tickers))
        elif roll < 0.88:
            attempt(trading.rug, db, uid, None, rnd.choice([25, 50, 70]))
        elif roll < 0.90:
            events.fire_event(db, rnd.choice(["pump", "crash", "boom", "special", "holiday", "mania"]))
        elif roll < 0.92:
            social.flush_activity(db, {(uid, -1): rnd.randint(1, 30), (rnd.randint(1, N), -2): rnd.randint(1, 30)}, {(-1, uid), (-2, uid)})
        elif roll < 0.935:
            db.x("UPDATE users SET max_level=MAX(max_level,3), cash=cash+? WHERE id=?", (C.coins(20_000), uid))
            core.mint(db, C.coins(20_000))
            attempt(social.create_company, db, uid, f"Co{step}", f"C{step}")
        elif roll < 0.95:
            attempt(social.join_company, db, uid, rnd.choice(["Co", "co"]) + str(rnd.randint(0, 1500)))
        elif roll < 0.96:
            attempt(social.leave_company, db, uid)
        elif roll < 0.965:
            attempt(social.payout_company, db, uid)
        elif roll < 0.9715:
            db.x("UPDATE users SET cash=cash+? WHERE id=?", (C.coins(300_000), uid))
            core.mint(db, C.coins(300_000))
            attempt(progress.rebirth, db, uid)
        elif roll < 0.99:
            events.tick(db, random_events=False)
            trading.liquidate_shorts(db)
        else:
            progress.end_season_if_due(db)
        if step % 25 == 0:
            check(db)
    events.tick(db, random_events=False)
    check(db)
    C.BOT_ACT_PROB = 0.35
    assert db.q1("SELECT COUNT(*) c FROM quests WHERE user_id<0")["c"] == 0
    assert db.q1("SELECT COUNT(*) c FROM users WHERE bot=1 AND (cash<0 OR pending_div<0)")["c"] == 0
    # make sure the chaos actually exercised the interesting paths
    assert db.q1("SELECT COUNT(*) c FROM stocks")["c"] >= 4
    assert db.q1("SELECT COUNT(*) c FROM history")["c"] > 100
