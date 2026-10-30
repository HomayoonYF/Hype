"""The Rofaqa Exchange (v0.13): house-priced companies that nobody can pump or dump.

Part 1  mechanics: listing, trades never move prices, levy / slippage maths, dividends, conservation, earnings nights, migration.
Part 2  the oracle: no drift, no autocorrelation, capped heat, seasonal patterns cancelled.
Part 3  attacks: simulated farmers (trend followers, mean reverters, time-of-day, insiders who steer the activity, earnings holders,
        lone-holder chunk traders, alt-account wash trading, pump & dump) must ALL lose after the trading costs.
None of this needs Telegram (no aiogram), so it runs anywhere.
"""
import json
import math
import random
import sqlite3
import statistics

import pytest

from hypemarket import config as C
from hypemarket import achievements, core, exchange as X, progress, quests, trading, trust
from hypemarket.core import GameError
from hypemarket.db import SCHEMA_VERSION, Database

T = [1_700_000_000]            # Tuesday 14 Nov 2023, 22:13 UTC (the earnings night of that week is two days away)
DAY = 86400


@pytest.fixture(autouse=True)
def no_quest_or_achievement_rewards(monkeypatch):
    """Quests and achievements mint reward coins on trades; these tests measure what the exchange itself costs and pays."""
    monkeypatch.setattr(quests, "track", lambda *a, **k: None)
    monkeypatch.setattr(achievements, "check", lambda *a, **k: None)


@pytest.fixture()
def db():
    T[0] = 1_700_000_000
    core.set_clock(lambda: T[0])
    d = Database(":memory:")
    progress.ensure_season(d)
    X.ensure_listed(d, random.Random(7))
    yield d
    d.close()


def adv(secs):
    T[0] += secs


def player(db, uid, coins=100_000):
    """A player with exactly `coins` cash; the money supply bookkeeping (minted - burned) stays exact."""
    core.ensure_user(db, uid, f"u{uid}", f"P{uid}")
    have = core.get_user(db, uid)["cash"]
    want = C.coins(coins)
    db.x("UPDATE users SET cash=? WHERE id=?", (want, uid))
    (core.mint if want > have else core.burn)(db, abs(want - have))


def stock(db, key):
    return core.stock_row(db, X.stock_id(db, key))


def total_money(db):
    users = db.q1("SELECT COALESCE(SUM(cash+pending_div+pending_inc),0) v FROM users")["v"]
    reserves = db.q1("SELECT COALESCE(SUM(reserve),0) v FROM stocks")["v"]
    treasuries = db.q1("SELECT COALESCE(SUM(treasury),0) v FROM companies")["v"]
    owed = 0
    for r in db.qa("SELECT h.shares, h.debt, s.acc_div FROM holdings h JOIN stocks s ON s.id=h.stock_id WHERE s.status='active'"):
        owed += r["shares"] * r["acc_div"] // C.ACC - r["debt"]
    return users + reserves + treasuries + owed


def assert_conserved(db, slack=300):
    expected = core.kv_int(db, "minted") - core.kv_int(db, "burned")
    got = total_money(db)
    assert abs(got - expected) <= slack, (got, expected, got - expected)
    for s in db.qa("SELECT * FROM stocks WHERE status='active'"):
        assert s["reserve"] == core.E.f_eff(s["supply"], s["p0"], s["k"], core.eff(s)), s["ticker"]
        held = db.q1("SELECT COALESCE(SUM(shares),0) n FROM holdings WHERE stock_id=?", (s["id"],))["n"]
        assert held == s["supply"], s["ticker"]


def wealth(db, uid):
    return core.net_worth(db, uid)


def claim(db, uid):
    try:
        trading.claim_dividends(db, uid)
    except GameError:                                        # "nothing to claim"
        pass


# ============================================================================== part 1: mechanics
def test_the_six_companies_are_listed_once(db):
    rows = X.listed_rows(db)
    assert [co.key for co, _ in rows] == [c.key for c in X.COMPANIES] and len(rows) == 6
    assert X.ensure_listed(db) == 0                                    # idempotent
    for co, s in rows:
        assert s["kind"] == "listed" and s["k"] == 0 and s["supply"] == 0 and s["owner_id"] is None
        assert s["p0"] == co.p0 and X.company_of(db, s) is co
        assert db.q1("SELECT COUNT(*) c FROM history WHERE stock_id=?", (s["id"],))["c"] >= 280      # the first chart is not empty
    assert len({s["ticker"] for _, s in rows}) == 6


def test_a_player_ticker_never_blocks_the_exchange():
    T[0] = 1_700_000_000
    core.set_clock(lambda: T[0])
    d = Database(":memory:")
    progress.ensure_season(d)
    player(d, 1, 500_000)
    trading.launch(d, 1, "CHAT", "My chat stock")
    assert X.ensure_listed(d, random.Random(1)) == 6
    chat = stock(d, "chat")
    assert chat["kind"] == "listed" and chat["ticker"] != "CHAT" and chat["ticker"].startswith("CHAT")
    assert core.stock_row(d, d.q1("SELECT id FROM stocks WHERE ticker='CHAT'")["id"])["kind"] != "listed"
    d.close()


def test_nobody_can_pump_or_dump_a_listed_company(db):
    player(db, 1, 5_000_000)
    player(db, 2, 5_000_000)
    s = stock(db, "chat")
    p = s["p0"]
    b = trading.buy(db, 1, s["ticker"], spend_all=True)
    assert b["shares"] > 10_000 and core.stock_row(db, s["id"])["p0"] == p and b["price"] == p       # a huge buy: no pump
    adv(40)
    trading.buy(db, 2, s["ticker"], 500)
    adv(40)
    r = trading.sell(db, 1, s["ticker"], sell_all=True)                                              # dumping it all: no dump
    assert core.stock_row(db, s["id"])["p0"] == p and r["price"] == p
    assert wealth(db, 1) < C.coins(5_000_000)                                                       # the only result is the levy
    assert_conserved(db)


def test_dump_with_one_account_and_buy_with_another_prints_nothing(db):
    """The old shorting exploit: account A dumps, account B profits. With house prices B's trade can't profit from A's dump."""
    player(db, 1, 2_000_000)
    player(db, 2, 2_000_000)
    s = stock(db, "levi")
    trading.buy(db, 1, s["ticker"], spend_all=True)
    adv(40)
    start = wealth(db, 1) + wealth(db, 2)
    trading.sell(db, 1, s["ticker"], sell_all=True)                                                  # "dump"
    adv(5)
    trading.buy(db, 2, s["ticker"], spend_all=True)                                                  # "buy the dip": there is no dip
    adv(40)
    trading.sell(db, 2, s["ticker"], sell_all=True)
    assert wealth(db, 1) + wealth(db, 2) < start
    assert_conserved(db)


@pytest.mark.parametrize("key", ["chat", "levi", "meme", "bank"])
def test_a_round_trip_loses_at_least_both_spreads(db, key):
    rnd = random.Random(3)
    player(db, 1, 5_000_000)
    s = stock(db, key)
    for _ in range(12):
        n = rnd.randint(1, 3000)
        before = wealth(db, 1)
        b = trading.buy(db, 1, s["ticker"], n)
        adv(40)
        trading.sell(db, 1, s["ticker"], sell_all=True)
        assert before - wealth(db, 1) >= 2 * (b["cost"] * C.LISTED_SPREAD_BPS // C.BPS)
        adv(2000)                                                                                   # (the flow memory fades)
    assert_conserved(db)


def test_the_levy_is_fixed_spread_plus_capped_slippage(db):
    s = stock(db, "chat")
    notional = C.coins(1_000)
    assert X.levy(db, s, 0) == 0
    base = X.levy(db, s, notional, slippage=False)
    assert base == -(-notional * C.LISTED_SPREAD_BPS // C.BPS)
    quiet = X.levy(db, s, notional)
    assert quiet >= base
    X.add_flow(db, s["id"], C.coins(50_000))                                                        # the whole market just traded a lot
    busy = X.levy(db, s, notional)
    assert busy > quiet
    X.add_flow(db, s["id"], C.coins(50_000_000))
    capped = X.levy(db, s, notional)
    assert capped == -(-notional * (C.LISTED_SPREAD_BPS + C.LISTED_SLIP_MAX_BPS) // C.BPS)          # slippage never exceeds its cap
    adv(10 * 3600)
    assert X.levy(db, s, notional) < capped                                                         # ...and fades with time
    s2 = stock(db, "bank")
    assert X.levy(db, s2, notional) < capped                                                        # flow is per company


def test_the_fee_perks_never_discount_the_levy(db):
    player(db, 1, 100_000)
    db.x("INSERT INTO perks(user_id,perk,level) VALUES(1,'fee',3)")
    s = stock(db, "chat")
    cost = core.E.quote_buy(0, 100, s["p0"], 0, s["base_bps"] * s["event_bps"] // C.BPS)
    expected = X.levy(db, s, cost)
    assert trading.buy(db, 1, s["ticker"], 100)["fee"] == expected >= -(-cost * C.LISTED_SPREAD_BPS // C.BPS)


def test_max_affordable_is_exact(db):
    s = stock(db, "meme")
    rnd = random.Random(5)
    for _ in range(60):
        budget = rnd.randint(1, C.coins(5_000_000))
        n = X.max_affordable(db, s, budget)
        eff = s["base_bps"] * s["event_bps"] // C.BPS

        def total(m):
            c = core.E.quote_buy(s["supply"], m, s["p0"], 0, eff)
            return c + X.levy(db, s, c)
        if n:
            assert total(n) <= budget
        assert total(n + 1) > budget


def test_spend_all_buys_the_most_and_leaves_less_than_one_share(db):
    player(db, 1, 777_777)
    s = stock(db, "strk")
    b = trading.buy(db, 1, s["ticker"], spend_all=True)
    left = core.get_user(db, 1)["cash"]
    assert left >= 0 and left < b["price"] * 2
    player(db, 2, 0)
    with pytest.raises(GameError):
        trading.buy(db, 2, s["ticker"], spend_all=True)


def test_levy_goes_to_other_holders_never_more_than_their_share_and_the_rest_burns(db):
    player(db, 1, 400_000)
    player(db, 2, 400_000)
    s = stock(db, "chat")
    burned0 = core.kv_int(db, "burned")
    b1 = trading.buy(db, 1, s["ticker"], 1000)                                                      # nobody holds yet: the whole levy burns
    assert core.kv_int(db, "burned") - burned0 == b1["fee"]
    cash1 = core.get_user(db, 1)["cash"]
    adv(40)
    burned1 = core.kv_int(db, "burned")
    b2 = trading.buy(db, 2, s["ticker"], 1000)                                                      # player 1 holds: gets the holder share
    div = b2["fee"] * C.LISTED_HOLDER_SHARE_BPS // C.BPS
    assert 0 < div < b2["fee"] and core.kv_int(db, "burned") - burned1 == b2["fee"] - div
    trading.claim_dividends(db, 1)
    assert abs(core.get_user(db, 1)["cash"] - cash1 - div) <= 1
    assert core.kv_int(db, "tot:ex_levy_div") * C.BPS <= core.kv_int(db, "tot:ex_levy") * C.LISTED_HOLDER_SHARE_BPS + C.BPS
    assert_conserved(db)


def test_the_seller_does_not_share_in_his_own_levy(db):
    """A lone holder who sells everything gets nothing back from the levy of that sale (it burns)."""
    player(db, 1, 300_000)
    s = stock(db, "chat")
    trading.buy(db, 1, s["ticker"], 2000)
    adv(40)
    burned0 = core.kv_int(db, "burned")
    r = trading.sell(db, 1, s["ticker"], sell_all=True)
    assert core.kv_int(db, "burned") - burned0 == r["fee"]
    assert core.get_user(db, 1)["pending_div"] == 0
    assert_conserved(db)


def test_a_lone_holder_trading_in_slices_still_gets_back_at_most_the_holder_share(db):
    """Slicing orders so that the remaining shares collect part of every levy: the loss is still >= (1 - holder share) of all levies."""
    player(db, 1, 2_000_000)
    s = stock(db, "chat")
    paid = 0
    start = wealth(db, 1)
    for _ in range(40):
        b = trading.buy(db, 1, s["ticker"], 300)
        paid += b["fee"]
        adv(3)
    adv(40)
    while True:
        h = db.q1("SELECT shares FROM holdings WHERE user_id=1")
        if not h:
            break
        r = trading.sell(db, 1, s["ticker"], min(300, h["shares"]))
        paid += r["fee"]
        adv(3)
    trading.claim_dividends(db, 1)
    loss = start - wealth(db, 1)
    assert loss >= paid * (C.BPS - C.LISTED_HOLDER_SHARE_BPS) // C.BPS - 5
    assert_conserved(db)


def test_listed_trades_never_feed_the_counters_that_drive_the_prices(db):
    player(db, 1, 500_000)
    keys = ("tot:trades", "tot:volume", "tot:meme_trades", "tot:whales", "tot:chat", "tot:payouts", "tot:dailies", "tot:votes")
    before = {k: core.kv_int(db, k) for k in keys}
    for key in ("meme", "levi", "chat"):
        s = stock(db, key)
        trading.buy(db, 1, s["ticker"], 800)
        adv(40)
        trading.sell(db, 1, s["ticker"], sell_all=True)
        adv(10)
    assert {k: core.kv_int(db, k) for k in keys} == before                                         # no self-feedback possible
    assert core.kv_int(db, "tot:ex_trades") == 6 and core.kv_int(db, "tot:ex_volume") > 0


def test_listed_companies_have_no_owner_event_rug_report_or_delist(db):
    s = stock(db, "chat")
    player(db, 1, 100_000)
    trading.buy(db, 1, s["ticker"], 50)
    with pytest.raises(GameError):
        trust.admin_delist(db, 1, s["id"])                                                          # (kind not allowed)
    db.x("UPDATE users SET trades=9 WHERE id=1")
    player(db, 2, 100_000)
    db.x("UPDATE users SET trades=9 WHERE id=2")
    with pytest.raises(GameError):
        trust.report(db, 2, s["ticker"], "rug")
    from hypemarket import events
    for _ in range(30):
        events.fire_event(db, "pump")
        events.fire_event(db, "crash")
    assert stock(db, "chat")["event_until"] == 0 and stock(db, "chat")["p0"] == s["p0"]


def test_rebirth_sells_listed_shares_without_a_levy_and_conserves_money(db):
    player(db, 1, 400_000)
    s = stock(db, "levi")
    trading.buy(db, 1, s["ticker"], 1000)
    adv(100)
    fees0 = core.kv_int(db, "tot:ex_levy")
    db.x("UPDATE users SET cash=cash+? WHERE id=1", (C.coins(300_000),))
    core.mint(db, C.coins(300_000))
    progress.rebirth(db, 1)
    assert core.kv_int(db, "tot:ex_levy") == fees0                                                  # the forced sale paid no levy
    assert db.q1("SELECT COUNT(*) c FROM holdings WHERE user_id=1")["c"] == 0
    assert_conserved(db)


# ---------------------------------------------------------------------------- prices, money and earnings nights
def test_prices_move_and_money_is_conserved_through_ticks_trades_and_earnings(db):
    rnd = random.Random(11)
    for uid in range(1, 7):
        player(db, uid, 300_000)
    for step in range(900):
        adv(rnd.randint(20, 400))
        uid = rnd.randint(1, 6)
        s = stock(db, rnd.choice([c.key for c in X.COMPANIES]))
        try:
            if rnd.random() < 0.6:
                trading.buy(db, uid, s["ticker"], rnd.randint(1, 800))
            else:
                trading.sell(db, uid, s["ticker"], rnd.randint(1, 800))
        except GameError:
            pass
        core.counter(db, "chat", rnd.randint(0, 40))
        core.counter(db, "payouts", rnd.randint(0, 3))
        if rnd.random() < 0.3:
            X.tick(db, rnd)
            if X.earnings_due(db):
                X.run_earnings(db, rnd)
        if step % 60 == 0:
            assert_conserved(db)
    assert_conserved(db)
    assert core.kv_int(db, "tot:ex_earnings") >= 1
    st = X.house_stats(db)
    assert st["pnl"] == -(core.kv_int(db, "tot:ex_mint") - core.kv_int(db, "tot:ex_burn"))          # house result = what price steps minted - burned
    assert st["levy_total"] > 0 and st["trades_today"] >= 0
    for co, s in X.listed_rows(db):
        listing = co.p0
        assert listing * C.EX_PRICE_FLOOR <= s["p0"] <= listing * C.EX_PRICE_CEIL


def test_the_clock_of_earnings_nights(db):
    t = T[0]
    sched = X.sched_ts(t)
    import datetime
    dt = datetime.datetime.fromtimestamp(sched, datetime.timezone.utc)
    assert dt.weekday() == C.EX_EARN_WEEKDAY == 3 and dt.hour == C.EX_EARN_HOUR and dt.minute == 0
    assert {X.sched_ts(t + k * 3600) for k in range(0, 24 * 7) if X.week_id(t + k * 3600) == X.week_id(t)} == {sched}
    assert not X.earnings_due(db) and X.run_earnings(db) is None
    assert X.next_earnings(db) == sched
    T[0] = sched - 5
    assert not X.earnings_due(db)
    T[0] = sched + 5
    assert X.earnings_due(db) and X.next_earnings(db) == T[0]
    rep = X.run_earnings(db, random.Random(1))
    assert rep and rep["week"] == X.week_id(T[0]) and len(rep["rows"]) == 6
    assert not X.earnings_due(db) and X.run_earnings(db) is None                                    # once per week
    assert X.next_earnings(db) == X.sched_ts(sched + 7 * DAY) and X.last_report(db) == rep
    assert db.q1("SELECT COUNT(*) c FROM news WHERE kind='ex_earn'")["c"] == 6
    for row in rep["rows"]:
        assert abs(row["jump"]) <= C.EX_EARN_MAX * 100 + 1e-9 and row["price"] > 0
    T[0] = sched + 7 * DAY + 60
    assert X.earnings_due(db)


def test_opening_after_this_weeks_earnings_night_does_not_trigger_one():
    T[0] = X.sched_ts(1_700_000_000) + 3 * 3600
    core.set_clock(lambda: T[0])
    d = Database(":memory:")
    progress.ensure_season(d)
    X.ensure_listed(d, random.Random(1))
    assert not X.earnings_due(d)
    assert X.next_earnings(d) == X.sched_ts(T[0] + 7 * DAY)
    d.close()


def test_earnings_jumps_are_random_zero_mean_and_not_predictable_from_prices(db):
    rnd = random.Random(21)
    jumps, prev_jumps = [], {}
    for week in range(160):
        T[0] = X.sched_ts(T[0]) + 600 + (7 * DAY if week else 0)
        for _ in range(3):
            core.counter(db, "chat", rnd.randint(0, 500))
        rep = X.run_earnings(db, rnd)
        assert rep, week
        for r in rep["rows"]:
            jumps.append(r["jump"])
    mean = statistics.mean(jumps)
    sd = statistics.pstdev(jumps)
    assert abs(mean) < 4 * sd / math.sqrt(len(jumps))                                              # zero mean (within 4 standard errors)
    assert 1.5 < sd < 3.5                                                                          # about 2.5% either way
    assert max(abs(j) for j in jumps) <= C.EX_EARN_MAX * 100 + 1e-9
    # the activity part is tiny: even 3 sigma of weekly activity moves a price by at most this much
    assert C.EX_EARN_BETA * C.EX_ZCLIP < 0.005


def test_each_listed_company_reads_its_own_numbers(db):
    """CHAT listens to chat, MEME to meme trades... and nothing else (a counter bump moves only its own company's heat)."""
    for _ in range(80):                                    # warm-up: steady background activity
        adv(60)
        core.counter(db, "chat", 20)
        core.counter(db, "meme_trades", 5)
        X.tick(db, random.Random(1))
    base = {co.key: json.loads(core.kv_get(db, f"exs:{co.key}"))["y"] for co in X.COMPANIES}
    for _ in range(40):
        adv(60)
        core.counter(db, "chat", 400)                      # a flood of chat only
        core.counter(db, "meme_trades", 5)
        X.tick(db, random.Random(2))
    now_y = {co.key: json.loads(core.kv_get(db, f"exs:{co.key}"))["y"] for co in X.COMPANIES}
    assert now_y["chat"] > base["chat"] + 1e-4
    assert abs(now_y["meme"] - base["meme"]) < 1e-3 and abs(now_y["trst"]) < 1e-3
    for co in X.COMPANIES:
        assert abs(now_y[co.key]) <= C.EX_YCAP + 1e-12


# ---------------------------------------------------------------------------- migration from v0.12 (shorting removed)
def test_database_with_open_shorts_upgrades_refunds_margins_and_returns_the_skill(tmp_path):
    path = str(tmp_path / "v4.db")
    d = Database(path)
    for uid in (1, 2, 3):
        core.ensure_user(d, uid, f"u{uid}", f"P{uid}")
    d.x("UPDATE users SET cash=1000, spent_points=7 WHERE id=1")
    d.x("UPDATE users SET cash=2000, spent_points=3 WHERE id=2")
    d.x("UPDATE users SET cash=3000, spent_points=0 WHERE id=3")
    d.close()
    con = sqlite3.connect(path)                                   # turn it into a real v0.12 database (schema 4, with shorts)
    con.execute("DROP TABLE exflow")
    con.execute("CREATE TABLE shorts(id INTEGER PRIMARY KEY AUTOINCREMENT, user_id INTEGER NOT NULL, stock_id INTEGER NOT NULL, shares INTEGER NOT NULL, "
                "entry INTEGER NOT NULL, margin INTEGER NOT NULL, opened INTEGER NOT NULL, UNIQUE(user_id, stock_id))")
    con.execute("INSERT INTO shorts(user_id,stock_id,shares,entry,margin,opened) VALUES(1,5,10,100,500,1),(1,6,10,100,250,1),(2,5,10,100,900,1)")
    con.execute("INSERT INTO perks(user_id,perk,level) VALUES(1,'short',1),(2,'short',1),(2,'fee',1),(3,'alerts',2)")
    con.execute("PRAGMA user_version=4")
    con.commit()
    con.close()
    d = Database(path)
    assert d.q1("PRAGMA user_version")[0] == SCHEMA_VERSION == 11                                 # (v0.17 moved the current version from 6 to 7, v0.18 to 8, v0.18.1 to 9, v0.19 to 10; the 4 -> 6 steps run exactly as before)
    assert d.q1("SELECT 1 FROM sqlite_master WHERE name='shorts'") is None
    assert d.q1("SELECT 1 FROM sqlite_master WHERE name='exflow'") is not None
    u1, u2, u3 = (core.get_user(d, i) for i in (1, 2, 3))
    assert u1["cash"] == 1000 + 750 and u2["cash"] == 2000 + 900 and u3["cash"] == 3000            # margins are back in cash
    assert u1["spent_points"] == 4 and u2["spent_points"] == 0 and u3["spent_points"] == 0         # the 3 skill points are back (never below 0)
    assert d.q1("SELECT COUNT(*) c FROM perks WHERE perk='short'")["c"] == 0
    assert d.q1("SELECT 1 FROM perks WHERE user_id=2 AND perk='fee'") and d.q1("SELECT 1 FROM perks WHERE user_id=3 AND perk='alerts'")
    assert [d.q1("SELECT value FROM kv WHERE key=?", (f"mig5:{i}",))["value"] for i in (1, 2)] == ["750,3", "900,3"]
    assert d.q1("SELECT value FROM kv WHERE key='mig5:3'") is None                                  # nobody else gets a message
    assert X.announce_migration(d) == 2
    assert {o[0] for o in d.outbox} == {1, 2} and all(o[1] == "short_removed" for o in d.outbox)
    assert X.announce_migration(d) == 0                                                             # told once
    d.close()
    assert Database(path).q1("PRAGMA user_version")[0] == 11                                        # reopening is a no-op


def test_a_database_that_never_had_shorts_upgrades_too(tmp_path):
    path = str(tmp_path / "v4b.db")
    d = Database(path)
    core.ensure_user(d, 1, "a", "A")
    d.x("INSERT INTO perks(user_id,perk,level) VALUES(1,'short',1)")
    d.x("UPDATE users SET spent_points=3 WHERE id=1")
    d.close()
    con = sqlite3.connect(path)
    con.execute("PRAGMA user_version=4")
    con.commit()
    con.close()
    d = Database(path)
    assert core.get_user(d, 1)["spent_points"] == 0 and d.q1("SELECT value FROM kv WHERE key='mig5:1'")["value"] == "0,3"
    d.close()


# ============================================================================== part 2: the oracle
def poisson(rnd, lam):
    L, k, p = math.exp(-lam), 0, 1.0
    while True:
        p *= rnd.random()
        if p <= L:
            return k
        k += 1


def simulate(seed, steps, base=3.0, steer=None, p0=100_000, start=0):
    """One company, one tick per minute. Background activity: Poisson with a daily and a weekly rhythm plus rare bursts.
    `steer(i)` adds activity on top (an attacker who controls accounts)."""
    rnd = random.Random(seed)
    st = X.new_state(p0, start, {})
    prices, ys, rets = [], [], []
    last = float(p0)
    burst_until = -1
    for i in range(steps):
        t = start + i * 60
        if burst_until < i and rnd.random() < 1 / 4000:
            burst_until = i + rnd.randint(20, 120)
        rhythm = (1.0 + 0.7 * math.sin(2 * math.pi * (t / DAY - 0.25))) * (1.3 if (t // DAY) % 7 in (4, 5) else 1.0)
        lam = base * rhythm * (3.0 if burst_until >= i else 1.0)
        x = poisson(rnd, lam) + (steer(i) if steer else 0.0)
        out = X.oracle_step(st, x, X.slot_of(t), 1.0, rnd)
        p = X.state_price(st)
        prices.append(p)
        ys.append(out["y"])
        rets.append(math.log(p / last))
        last = p
    return prices, ys, rets


def test_the_hidden_noise_has_no_drift_and_the_price_is_a_martingale():
    rnd = random.Random(1)
    st = X.new_state(100_000, 0, {})
    eps = [X.oracle_step(st, 3.0, i % 48, 1.0, rnd)["eps"] for i in range(250_000)]
    se = statistics.pstdev(eps) / math.sqrt(len(eps))
    assert abs(statistics.mean(eps)) < 4 * se
    assert max(abs(e) for e in eps) <= C.EX_MAX_STEP + 1e-12


def test_returns_have_no_autocorrelation_so_trends_and_reversals_are_not_a_strategy():
    _prices, _ys, rets = simulate(3, 120_000)
    m = statistics.mean(rets)
    var = sum((r - m) ** 2 for r in rets)
    for lag in (1, 2, 3, 5, 10, 30, 60):
        ac = sum((rets[i] - m) * (rets[i + lag] - m) for i in range(len(rets) - lag)) / var
        assert abs(ac) < 0.02, (lag, ac)


def test_the_heat_is_capped_whatever_the_activity():
    for pattern in (lambda i: 1e6, lambda i: 500.0 if (i // 90) % 2 else 0.0, lambda i: 1e9 * math.sin(i / 40.0) ** 2):
        _p, ys, _r = simulate(5, 20_000, base=1.0, steer=pattern)
        assert max(abs(y) for y in ys) <= C.EX_YCAP + 1e-12


def test_daily_rhythm_is_cancelled(monkeypatch):
    """Activity that is high every afternoon and low every night would make the heat swing at fixed hours; the learned per-half-hour bias removes it."""
    def by_slot(seed):
        _p, ys, _r = simulate(seed, 40 * 1440, base=8.0)
        buckets = [[] for _ in range(48)]
        for i in range(20 * 1440, len(ys)):
            buckets[X.slot_of(i * 60)].append(ys[i])
        means = [statistics.mean(b) for b in buckets]
        return statistics.pstdev(means)
    with_cancel = statistics.mean(by_slot(s) for s in (1, 2))
    monkeypatch.setattr(C, "EX_ALPHA_BIAS", 0.0)
    without = statistics.mean(by_slot(s) for s in (1, 2))
    assert with_cancel < 0.6 * without, (with_cancel, without)


def test_surprises_are_standardised_and_uncorrelated():
    rnd = random.Random(8)
    st = X.new_state(100_000, 0, {})
    zs = []
    for i in range(80_000):
        t = i * 60
        lam = 4 * (1 + 0.6 * math.sin(2 * math.pi * t / DAY)) * (2.5 if (i // 700) % 5 == 0 else 1.0)
        zs.append(X.oracle_step(st, poisson(rnd, lam), X.slot_of(t), 1.0, rnd)["z"])
    zs = zs[5000:]
    assert abs(statistics.mean(zs)) < 0.05 and 0.4 < statistics.pstdev(zs) < 1.3
    m = statistics.mean(zs)
    var = sum((z - m) ** 2 for z in zs)
    ac1 = sum((zs[i] - m) * (zs[i + 1] - m) for i in range(len(zs) - 1)) / var
    assert abs(ac1) < 0.5       # (bursts persist for a while; the whitening removes most of it and the cap keeps the rest harmless)


# ============================================================================== part 3: attacks
COST = C.LISTED_SPREAD_BPS / C.BPS                                    # one side, cheapest possible (no slippage)
RECYCLE = C.LISTED_HOLDER_SHARE_BPS / C.BPS                           # the most a lone holder can get back of his own levies
CHEAPEST_SIDE = COST * (1 - RECYCLE)
ROUND_TRIP = (1 + CHEAPEST_SIDE) / (1 - CHEAPEST_SIDE) - 1           # the cheapest conceivable round trip, as a gross price gain needed


def test_the_safety_rule_of_the_exchange_holds():
    """The most anybody can predict or steer is smaller than the cheapest round trip (see config.py)."""
    assert 2 * C.EX_YCAP + C.EX_EARN_BETA * C.EX_ZCLIP < 2 * (C.LISTED_SPREAD_BPS / C.BPS) * (1 - C.LISTED_HOLDER_SHARE_BPS / C.BPS)
    edge = math.exp(2 * C.EX_YCAP + C.EX_EARN_BETA * C.EX_ZCLIP) - 1
    assert edge < ROUND_TRIP, (edge, ROUND_TRIP)
    assert C.LISTED_HOLDER_SHARE_BPS <= 5000 and C.LISTED_SPREAD_BPS >= 100


def test_the_worst_case_steering_pattern_cannot_swing_the_price_beyond_the_cap():
    """An attacker who controls (many) accounts feeds activity in every rhythm he can think of; the swing of the heat from its
    lowest to its highest point never exceeds 2 x cap, which is below the cost of a round trip."""
    worst = 0.0
    for half_period in (20, 60, 120, 240, 480, 720, 960, 1440, 2160, 2880):
        for base in (0.0, 4.0):
            for amp in (3.0, 80.0):
                pattern = lambda i, p=half_period, a=amp: a if (i // p) % 2 == 0 else 0.0
                _p, ys, _r = simulate(half_period, 4 * 1440 + 8 * half_period, base=base, steer=pattern)
                seg = ys[1440:]
                worst = max(worst, max(seg) - min(seg))
    assert worst <= 2 * C.EX_YCAP + 1e-12
    assert math.exp(worst) - 1 < ROUND_TRIP


def keep_all_below_cost(gross):
    return all((1 - CHEAPEST_SIDE) / (1 + CHEAPEST_SIDE) * (1 + g) < 1 for g in gross)


def _trades(prices, signal, hold):
    """Long-only: whenever signal(i) fires and we are flat, buy at prices[i] and sell at prices[i+hold]. Returns the gross returns."""
    out, i = [], 0
    while i < len(prices) - hold - 1:
        if signal(i):
            out.append(prices[i + hold] / prices[i] - 1)
            i += hold
        else:
            i += 1
    return out


def _assert_loses(gross, label, min_trades=100, strict=True):
    """strict: even the GROSS edge is under half of the cheapest costs (for strategies without inside knowledge);
    always: after the cheapest possible costs the average trade loses and no single trade wins."""
    assert len(gross) >= min_trades, (label, len(gross))
    keep = (1 - CHEAPEST_SIDE) / (1 + CHEAPEST_SIDE)
    net = statistics.mean(keep * (1 + g) - 1 for g in gross)
    if strict:
        assert statistics.mean(gross) < ROUND_TRIP / 2, (label, statistics.mean(gross))
    assert net < 0, (label, net)


@pytest.mark.parametrize("seed", [1, 2, 3])
def test_trend_followers_and_mean_reverters_lose(seed):
    prices, _ys, _r = simulate(seed, 40 * 1440)
    for lookback in (10, 30, 120):
        for hold in (30, 120, 480):
            thr = 0.006
            up = _trades(prices, lambda i: i > lookback and prices[i] / prices[i - lookback] - 1 > thr, hold)
            down = _trades(prices, lambda i: i > lookback and prices[i] / prices[i - lookback] - 1 < -thr, hold)
            _assert_loses(up, ("trend", seed, lookback, hold), min_trades=20)
            _assert_loses(down, ("reversion", seed, lookback, hold), min_trades=20)


@pytest.mark.parametrize("seed", [4, 5])
def test_time_of_day_traders_lose(seed):
    """Learn from the first half of the history which hours go up, then hold exactly those hours every day."""
    prices, _ys, _r = simulate(seed, 60 * 1440, base=6.0)
    half = 30 * 1440
    per_slot = [[] for _ in range(48)]
    for i in range(0, half - 30):
        per_slot[X.slot_of(i * 60)].append(prices[i + 30] / prices[i] - 1)
    best = sorted(range(48), key=lambda s: -statistics.mean(per_slot[s]))[:6]
    gross = []
    for i in range(half, len(prices) - 31):
        if i % 30 == 0 and X.slot_of(i * 60) in best:
            gross.append(prices[i + 30] / prices[i] - 1)
    _assert_loses(gross, "time of day")


@pytest.mark.parametrize("seed", [6, 7, 8])
def test_an_insider_who_knows_the_future_activity_still_loses(seed):
    """The strongest possible insider knows the whole algorithm and the exact future heat (he even controls the activity):
    he buys whenever the heat is about to rise by a lot over the next hours and sells when it has risen."""
    steer = lambda i: 60.0 if (i // 480) % 2 == 0 else 0.0                    # he feeds activity in 8-hour blocks
    prices, ys, _r = simulate(seed, 30 * 1440, base=2.0, steer=steer)
    gross = []
    i = 1440
    hold = 480
    while i < len(prices) - hold - 1:
        if ys[i + hold] - ys[i] > 0.008:                                     # he KNOWS the heat will rise by at least 0.8%
            gross.append(math.exp(ys[i + hold] - ys[i]) - 1)                 # (the noise part of the price is zero-mean: he gains the heat)
            i += hold
        else:
            i += 1
    assert gross, "the pattern must produce winning swings for the attack to mean anything"
    assert max(gross) <= math.exp(2 * C.EX_YCAP) - 1 + 1e-12
    assert keep_all_below_cost(gross)
    _assert_loses(gross, ("insider", seed), min_trades=5, strict=False)


@pytest.mark.parametrize("seed", [9, 10])
def test_an_insider_who_also_knows_the_earnings_signal_still_loses(seed):
    """Heat swing at its maximum PLUS the activity part of an earnings night at its maximum: still below the cheapest round trip."""
    best_case = math.exp(2 * C.EX_YCAP + C.EX_EARN_BETA * C.EX_ZCLIP) - 1
    assert best_case < ROUND_TRIP
    assert (1 - CHEAPEST_SIDE) / (1 + CHEAPEST_SIDE) * (1 + best_case) < 1


def test_buying_before_every_earnings_night_has_no_edge(db):
    rnd = random.Random(31)
    gains = []
    for week in range(150):
        T[0] = X.sched_ts(T[0] + (0 if week == 0 else 4 * DAY)) - 3600
        X.tick(db, rnd)
        before = {s["ticker"]: s["p0"] for _c, s in X.listed_rows(db)}
        T[0] += 7200
        for _ in range(2):
            core.counter(db, "chat", rnd.randint(0, 300))
        rep = X.run_earnings(db, rnd)
        assert rep
        for r in rep["rows"]:
            gains.append(r["price"] / before[r["ticker"]] - 1)
    mean = statistics.mean(gains)
    assert abs(mean) < 4 * statistics.pstdev(gains) / math.sqrt(len(gains))
    assert mean < ROUND_TRIP / 2


def test_wash_trading_between_alt_accounts_only_burns_money(db):
    """A bank account holds most of a company; its alt trades back and forth to 'farm' levy dividends. Every cycle loses."""
    player(db, 1, 3_000_000)          # the bank
    player(db, 2, 3_000_000)          # the alt that trades
    s = stock(db, "chat")
    trading.buy(db, 1, s["ticker"], 20_000)
    adv(40)
    start = wealth(db, 1) + wealth(db, 2)
    paid = 0
    for _ in range(30):
        b = trading.buy(db, 2, s["ticker"], 4000)
        adv(35)
        r = trading.sell(db, 2, s["ticker"], sell_all=True)
        paid += b["fee"] + r["fee"]
        adv(35)
        assert wealth(db, 1) + wealth(db, 2) < start
    claim(db, 1)
    claim(db, 2)
    loss = start - (wealth(db, 1) + wealth(db, 2))
    assert loss >= paid * (C.BPS - C.LISTED_HOLDER_SHARE_BPS) // C.BPS - 10
    assert_conserved(db)


def test_many_alts_cannot_dodge_the_slippage(db):
    """Slippage reads the market-wide flow of the last minutes, not the flow of one account: splitting an order over alts is no cheaper."""
    for uid in range(1, 21):
        player(db, uid, 100_000)
    s = stock(db, "levi")
    shares = C.coins(3_000) // s["p0"]
    total_notional = 20 * shares * s["p0"]
    one_big_order = X.levy(db, s, total_notional)
    paid = 0
    for uid in range(1, 21):
        paid += trading.buy(db, uid, s["ticker"], shares)["fee"]
    assert paid >= one_big_order * 0.95
    assert_conserved(db)


def test_a_long_random_life_of_the_exchange_stays_exactly_conserved(db):
    """Random players, random sizes, price steps and earnings nights for days: every coin stays accounted for."""
    rnd = random.Random(77)
    for uid in range(1, 9):
        player(db, uid, 200_000)
    for step in range(1500):
        adv(rnd.randint(30, 600))
        uid = rnd.randint(1, 8)
        s = stock(db, rnd.choice([c.key for c in X.COMPANIES]))
        try:
            (trading.buy if rnd.random() < 0.5 else trading.sell)(db, uid, s["ticker"], rnd.randint(1, 500))
        except GameError:
            pass
        core.counter(db, "chat", rnd.randint(0, 60))
        X.tick(db, rnd)
        if X.earnings_due(db):
            X.run_earnings(db, rnd)
    assert_conserved(db)
    assert core.kv_int(db, "tot:ex_levy") > 0 and core.kv_int(db, "tot:ex_trades") > 100
    for co, s in X.listed_rows(db):
        assert co.p0 * C.EX_PRICE_FLOOR <= s["p0"] <= co.p0 * C.EX_PRICE_CEIL


def test_the_levy_never_exceeds_a_few_percent_of_any_trade(db):
    rnd = random.Random(12)
    player(db, 1, 5_000_000)
    for _ in range(30):
        s = stock(db, rnd.choice([c.key for c in X.COMPANIES]))
        n = rnd.choice([1, 10, 500, 20_000])
        try:
            b = trading.buy(db, 1, s["ticker"], n)
        except GameError:
            continue
        assert b["fee"] <= -(-b["cost"] * (C.LISTED_SPREAD_BPS + C.LISTED_SLIP_MAX_BPS) // C.BPS)
        assert b["fee"] >= -(-b["cost"] * C.LISTED_SPREAD_BPS // C.BPS)
        adv(rnd.choice([3, 50, 700]))
