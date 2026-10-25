import random

import pytest

from hypemarket import config as C
from hypemarket import core, engine as E, events, progress, shop, social, trading
from hypemarket.core import GameError
from hypemarket.db import Database

T = [1_700_000_000]


@pytest.fixture()
def db():
    T[0] = 1_700_000_000
    core.set_clock(lambda: T[0])
    d = Database(":memory:")
    progress.ensure_season(d)
    yield d
    d.close()


def adv(secs):
    T[0] += secs


def user(db, uid, name="u", cash=None, **kw):
    u, _ = core.ensure_user(db, uid, f"user{uid}", f"{name}{uid}", **kw)
    if cash is not None:
        db.x("UPDATE users SET cash=? WHERE id=?", (cash, uid))
    return u


def bonuses(db, uid=None):
    """Coins paid out as quest + achievement rewards (these are minted on top of trading)."""
    from hypemarket import achievements
    where, args = ("WHERE user_id=?", (uid,)) if uid else ("", ())
    q = db.q1(f"SELECT COALESCE(SUM(reward),0) FROM quests {where + (' AND' if where else 'WHERE')} done=1", args)[0]
    a = sum(achievements.reward_of(achievements.BY_ID[r["aid"]]) for r in db.qa(f"SELECT aid FROM achievements {where}", args))
    return q + a


def check_invariants(db):
    """Reserve must always equal the integral of the curve; supply must equal the sum of holdings."""
    for s in db.qa("SELECT * FROM stocks WHERE status='active'"):
        assert s["reserve"] == E.f_eff(s["supply"], s["p0"], s["k"], core.eff(s)), s["ticker"]
        held = db.q1("SELECT COALESCE(SUM(shares),0) n FROM holdings WHERE stock_id=?", (s["id"],))["n"]
        assert held == s["supply"], s["ticker"]
        assert db.q1("SELECT COUNT(*) c FROM holdings WHERE stock_id=?", (s["id"],))["c"] == s["holders"]


# ---------------------------------------------------------------------------- engine
def test_curve_is_symmetric():
    for s in (0, 1, 7, 100):
        for n in (1, 5, 50):
            assert E.quote_buy(s, n, 100, 1, 10000) == E.quote_sell(s + n, n, 100, 1, 10000)


def test_curve_telescopes_with_multiplier():
    a = (100, 2, 11234)
    assert E.quote_buy(0, 30, *a) + E.quote_buy(30, 20, *a) == E.quote_buy(0, 50, *a)


def test_max_affordable_is_exact():
    a = (100, 1, 10000)
    for budget in (150, 999, 100_000, 5_000_000):
        n = E.max_affordable(40, budget, *a, 200)
        c = E.quote_buy(40, n, *a)
        assert c + E.fee_of(c, 200) <= budget
        c2 = E.quote_buy(40, n + 1, *a)
        assert c2 + E.fee_of(c2, 200) > budget


def test_levels_and_rebirth_math():
    assert E.level_for(0) == 0
    assert E.level_for(C.coins(1999)) == 0
    assert E.level_for(C.coins(2000)) == 1
    assert E.level_for(C.coins(6000)) == 2
    assert E.legacy_gain(C.coins(100_000)) == 3
    assert E.rebirth_min(2) == C.coins(400_000)


# ---------------------------------------------------------------------------- users & launch
def test_new_user_and_referral(db):
    user(db, 1)
    u, created = core.ensure_user(db, 2, "b", "B", referrer=1)
    assert created and u["cash"] == C.START_CASH + C.REF_BONUS_NEW and u["referrer"] == 1
    again, created = core.ensure_user(db, 2, "b", "B2")
    assert not created and again["name"] == "B2"


def test_launch_rules(db):
    user(db, 1)
    s = trading.launch(db, 1, "$speed", "Speeed")
    assert s["ticker"] == "SPEED" and s["supply"] == C.FOUNDER_SHARES
    check_invariants(db)
    with pytest.raises(GameError) as e:
        trading.launch(db, 1, "SPEED", "Other")
    assert e.value.code in ("ticker_taken", "max_stocks")
    user(db, 2)
    with pytest.raises(GameError) as e:
        trading.launch(db, 2, "SPEED", "Dup")
    assert e.value.code == "ticker_taken"
    for bad in ("1AB", "A", "TOOLONGTICKER1", "IDX1", "AB-C"):
        with pytest.raises(GameError) as e:
            trading.launch(db, 2, bad, "Name")
        assert e.value.code == "bad_ticker"
    with pytest.raises(GameError) as e:
        trading.launch(db, 2, "OKAY", "t.me/spam")
    assert e.value.code == "bad_text"
    with pytest.raises(GameError) as e:
        trading.launch(db, 2, "MEMEX", "Meme", kind="meme")
    assert e.value.code == "need_level"


def test_launch_funds(db):
    user(db, 1, cash=C.coins(100))
    with pytest.raises(GameError) as e:
        trading.launch(db, 1, "BROKE", "Broke")
    assert e.value.code == "funds"


# ---------------------------------------------------------------------------- trading
def test_buy_sell_roundtrip_and_fees(db):
    user(db, 1)
    user(db, 2)
    trading.launch(db, 1, "SPEED", "Speeed")
    adv(60)
    minted_before = core.kv_int(db, "minted")
    rewards_before = bonuses(db)
    r = trading.buy(db, 2, "SPEED", 50)
    check_invariants(db)
    assert r["total"] == r["cost"] + r["fee"]
    owner_pending = core.get_user(db, 1)["pending_inc"]
    assert owner_pending > 0 and owner_pending * 2 <= r["fee"] + 1
    adv(40)
    sr = trading.sell(db, 2, "SPEED", sell_all=True)
    check_invariants(db)
    assert sr["net"] == sr["proceeds"] - sr["fee"]
    # a round trip can only lose money (fees), never gain -- ignoring quest rewards, which are paid for being active
    rewards = bonuses(db, 2)
    assert core.get_user(db, 2)["cash"] - rewards < C.START_CASH
    assert core.kv_int(db, "minted") == minted_before + bonuses(db) - rewards_before


def test_sell_lock_and_cooldown(db):
    user(db, 1)
    user(db, 2)
    trading.launch(db, 1, "SPEED", "Speeed")
    adv(60)
    trading.buy(db, 2, "SPEED", 5)
    with pytest.raises(GameError) as e:
        trading.buy(db, 2, "SPEED", 5)
    assert e.value.code == "cooldown"
    adv(3)
    with pytest.raises(GameError) as e:
        trading.sell(db, 2, "SPEED", 5)
    assert e.value.code == "locked"
    adv(40)
    trading.sell(db, 2, "SPEED", 5)
    with pytest.raises(GameError) as e:
        trading.sell(db, 2, "SPEED", 1)
    assert e.value.code in ("cooldown", "shares")


def test_buy_errors(db):
    user(db, 1, cash=C.coins(10))
    user(db, 2)
    trading.launch(db, 2, "SPEED", "Speeed")
    adv(10)
    with pytest.raises(GameError) as e:
        trading.buy(db, 1, "SPEED", 100000)
    assert e.value.code == "funds"
    with pytest.raises(GameError) as e:
        trading.buy(db, 1, "NOPE", 1)
    assert e.value.code == "no_stock"
    with pytest.raises(GameError) as e:
        trading.buy(db, 1, "SPEED", 0)
    assert e.value.code == "bad_amount"
    with pytest.raises(GameError) as e:
        trading.sell(db, 1, "SPEED", 1)
    assert e.value.code == "shares"


def test_buy_max(db):
    user(db, 1)
    user(db, 2)
    trading.launch(db, 2, "SPEED", "Speeed")
    adv(10)
    r = trading.buy(db, 1, "SPEED", spend_all=True)
    rewards = bonuses(db, 1)
    left = core.get_user(db, 1)["cash"] - rewards      # quest/achievement rewards arrive after the purchase
    assert left < r["price"] * 2  # cannot afford one more share
    check_invariants(db)


def test_random_trading_keeps_invariants(db):
    rnd = random.Random(7)
    for i in range(1, 7):
        user(db, i, cash=C.coins(50_000))
    trading.launch(db, 1, "AAA", "Aaa")
    trading.launch(db, 2, "BBB", "Bbb")
    for step in range(400):
        adv(rnd.randint(3, 120))
        uid = rnd.randint(1, 6)
        tk = rnd.choice(["AAA", "BBB"])
        try:
            if rnd.random() < 0.55:
                trading.buy(db, uid, tk, rnd.randint(1, 300))
            else:
                trading.sell(db, uid, tk, sell_all=rnd.random() < 0.3, n=rnd.randint(1, 100))
        except GameError:
            pass
        if step % 40 == 0:
            check_invariants(db)
    check_invariants(db)


def test_dividends_flow_to_holders(db):
    user(db, 1)
    user(db, 2)
    user(db, 3)
    trading.launch(db, 1, "SPEED", "Speeed")
    trading.set_dividend(db, 1, 50)
    adv(10)
    trading.buy(db, 2, "SPEED", 100)
    adv(10)
    trading.buy(db, 3, "SPEED", 100)
    adv(10)
    trading.buy(db, 3, "SPEED", 100)
    r = trading.claim_dividends(db, 2)
    assert r["net"] > 0
    with pytest.raises(GameError):
        trading.claim_dividends(db, 2)
    check_invariants(db)


def test_holder_bonus_and_income(db):
    user(db, 1)
    user(db, 2)
    trading.launch(db, 1, "SPEED", "Speeed")
    adv(10)
    trading.buy(db, 2, "SPEED", 200)
    adv(3 * 3600)
    r = trading.claim_income(db, 2)
    assert r["holder_bonus"] > 0 and r["net"] == r["gross"]
    r2 = trading.claim_income(db, 1)
    assert r2["gross"] > 0


def test_company_tax_and_synergy(db):
    user(db, 1)
    db.x("UPDATE users SET max_level=3, cash=? WHERE id=1", (C.coins(20000),))
    c = social.create_company(db, 1, "Acme", "ACME")
    user(db, 2)
    social.join_company(db, 2, "acme")
    assert progress.company_synergy_pct(db, core.get_user(db, 2)) == 2
    d = progress.daily(db, 2)
    assert d["pct"] == 102
    info = social.company_info(db, 1)
    assert len(info["members"]) == 2 and info["stock"]["ticker"] == "ACME"


def test_company_payout_and_leave(db):
    user(db, 1)
    db.x("UPDATE users SET max_level=3, cash=? WHERE id=1", (C.coins(30000),))
    social.create_company(db, 1, "Acme", "ACME")
    user(db, 2)
    social.join_company(db, 2, "Acme")
    user(db, 3, cash=C.coins(5000))
    adv(10)
    trading.buy(db, 3, "ACME", 500)
    assert db.q1("SELECT treasury FROM companies")["treasury"] > 0
    with pytest.raises(GameError) as e:
        social.payout_company(db, 2)
    assert e.value.code == "not_company_owner"
    before = core.get_user(db, 2)["cash"]
    r = social.payout_company(db, 1)
    assert core.get_user(db, 2)["cash"] == before + r["each"]
    social.leave_company(db, 1)
    assert social.company_of(db, 2)["owner_id"] == 2


# ---------------------------------------------------------------------------- daily & perks
def test_daily_streak(db):
    user(db, 1)
    d1 = progress.daily(db, 1)
    assert d1["streak"] == 1
    with pytest.raises(GameError) as e:
        progress.daily(db, 1)
    assert e.value.code == "daily_wait"
    adv(21 * 3600)                                                  # 21 hours later it is still the same Tehran calendar day (the clock here is 01:43 Tehran time)
    with pytest.raises(GameError) as e:
        progress.daily(db, 1)
    assert e.value.code == "daily_wait"
    adv(3 * 3600)                                                   # past 00:00 Tehran time: a new day, the streak goes on
    assert progress.daily(db, 1)["streak"] == 2
    adv(60 * 3600)                                                  # two whole days without a claim: the streak starts again
    assert progress.daily(db, 1)["streak"] == 1


def test_perks(db):
    user(db, 1)
    with pytest.raises(GameError) as e:
        progress.upgrade(db, 1, "fee")
    assert e.value.code == "no_points"
    db.x("UPDATE users SET max_level=4 WHERE id=1")
    assert progress.upgrade(db, 1, "fee")["level"] == 1
    assert progress.upgrade(db, 1, "fee")["level"] == 2   # costs 2 points -> 3 used of 4
    with pytest.raises(GameError):
        progress.upgrade(db, 1, "fee")
    assert trading.trade_fee_bps(db, 1) == 160
    with pytest.raises(GameError) as e:
        progress.upgrade(db, 1, "head")
    assert e.value.code == "no_legacy"
    with pytest.raises(GameError) as e:
        progress.upgrade(db, 1, "bogus")
    assert e.value.code == "no_perk"


# ---------------------------------------------------------------------------- rug pull
def test_rug_pull(db):
    user(db, 1)
    user(db, 2, cash=C.coins(5000))
    trading.launch(db, 1, "SPEED", "Speeed")
    adv(10)
    trading.buy(db, 2, "SPEED", 400)
    adv(60)
    with pytest.raises(GameError) as e:
        trading.rug(db, 1)
    assert e.value.code == "rug_young"
    with pytest.raises(GameError) as e:
        trading.rug(db, 2, "SPEED")
    assert e.value.code == "not_owner"
    adv(7 * 3600)
    p_before = core.price(core.need_stock(db, "SPEED"))
    cash_before = core.get_user(db, 1)["cash"]
    r = trading.rug(db, 1, pct=50)
    check_invariants(db)
    s = core.need_stock(db, "SPEED")
    assert core.price(s) < p_before * 0.55
    assert core.get_user(db, 1)["cash"] == cash_before + r["taken"]
    assert core.get_user(db, 1)["rep"] == C.REP_START + 1 - C.RUG_REP_PENALTY  # +1 from the investor, -40 for the rug
    assert s["rugs"] == 1
    with pytest.raises(GameError) as e:
        trading.rug(db, 1)
    assert e.value.code == "rug_cooldown"
    assert any(o[1] == "rugged" and o[0] == 2 for o in db.outbox)
    # trading continues normally on the damaged curve
    adv(10)
    trading.sell(db, 2, "SPEED", sell_all=True)
    check_invariants(db)


def test_rug_can_be_disabled(db):
    user(db, 1)
    trading.launch(db, 1, "SPEED", "Speeed")
    with pytest.raises(GameError) as e:
        trading.rug(db, 1, allowed=False)
    assert e.value.code == "rug_off"


# ---------------------------------------------------------------------------- events
def test_events_keep_reserve_consistent(db):
    for i in range(1, 5):
        user(db, i, cash=C.coins(20_000))
    trading.launch(db, 1, "AAA", "Aaa")
    adv(5)
    trading.buy(db, 2, "AAA", 200)
    adv(5)
    trading.buy(db, 3, "AAA", 200)
    random.seed(3)
    for kind in ("pump", "crash", "boom", "special", "holiday"):
        assert events.fire_event(db, kind) == kind
        check_invariants(db)
        adv(4000)
        events.tick(db, random_events=False)
        adv(3000)                      # let any bubble-correction event that just started expire too
        events.tick(db, random_events=False)
        check_invariants(db)
    s = core.need_stock(db, "AAA")
    assert s["event_bps"] == C.BPS and s["event_until"] == 0
    assert events.active_events(db)["fee_free"] == 0


def test_bubble_and_correction(db):
    for i in range(1, 4):
        user(db, i, cash=C.coins(500_000))
    trading.launch(db, 1, "AAA", "Aaa")
    random.seed(1)
    adv(5)
    trading.buy(db, 2, "AAA", 3000)
    adv(5)
    trading.buy(db, 3, "AAA", 3000)
    kinds = [r["kind"] for r in db.qa("SELECT kind FROM news")]
    assert "bubble" in kinds or "surge" in kinds


# ---------------------------------------------------------------------------- shorting is gone
def test_shorting_is_gone(db):
    """Shorting was farmed with alt accounts (dump with one, short with another), so it no longer exists anywhere."""
    assert not hasattr(trading, "open_short") and not hasattr(trading, "close_short") and not hasattr(trading, "liquidate_shorts")
    assert db.q1("SELECT 1 FROM sqlite_master WHERE name='shorts'") is None
    assert "short" not in C.PERKS
    user(db, 1)
    db.x("UPDATE users SET max_level=5 WHERE id=1")
    with pytest.raises(GameError):
        progress.upgrade(db, 1, "short")


def test_rug_pays_only_the_owner(db):
    """A rug pull moves the drained reserve to the owner (minus tax); nobody else gets side payouts, and the curve stays exact."""
    user(db, 1)
    for i in range(2, 9):
        user(db, i, cash=C.coins(20_000))
    trading.launch(db, 1, "AAA", "Aaa")
    adv(10)
    for i in range(2, 9):
        trading.buy(db, i, "AAA", 40)
        adv(3)
    adv(7 * 3600)
    before = {i: core.get_user(db, i)["cash"] for i in range(1, 9)}
    r = trading.rug(db, 1, pct=50)
    check_invariants(db)
    assert core.get_user(db, 1)["cash"] == before[1] + r["taken"]
    assert all(core.get_user(db, i)["cash"] == before[i] for i in range(2, 9))


# ---------------------------------------------------------------------------- alerts
def test_alerts(db):
    user(db, 1)
    user(db, 2, cash=C.coins(20_000))
    trading.launch(db, 2, "AAA", "Aaa")
    with pytest.raises(GameError) as e:
        trading.set_alert(db, 1, "AAA", 500)
    assert e.value.code == "alerts_locked"
    db.x("UPDATE users SET max_level=1 WHERE id=1")
    progress.upgrade(db, 1, "alerts")
    target = core.price(core.need_stock(db, "AAA")) * 2
    trading.set_alert(db, 1, "AAA", target)
    assert trading.check_alerts(db) == 0
    adv(10)
    trading.buy(db, 2, "AAA", 800)
    assert trading.check_alerts(db) == 1
    assert any(o[1] == "alert" for o in db.outbox)


# ---------------------------------------------------------------------------- projects
def test_project_funding(db):
    user(db, 1)
    db.x("UPDATE users SET max_level=2, cash=? WHERE id=1", (C.coins(50_000),))
    user(db, 2, cash=C.coins(500_000))
    with pytest.raises(GameError) as e:
        trading.launch(db, 1, "TRIP", "Trip", kind="project", goal=C.coins(100))
    assert e.value.code == "bad_goal"
    trading.launch(db, 1, "TRIP", "Trip", kind="project", goal=C.PROJECT_MIN_GOAL)
    adv(10)
    trading.buy(db, 2, "TRIP", 800)
    s = core.need_stock(db, "TRIP")
    assert s["funded"] == 1
    check_invariants(db)


# ---------------------------------------------------------------------------- groups & activity
def test_group_activity_and_index(db):
    user(db, 1)
    user(db, 2)
    g = social.ensure_group(db, -100123, "Friends")
    assert g["index_stock"]
    idx = core.stock_row(db, g["index_stock"])
    assert idx["ticker"] == "IDX1" and idx["kind"] == "index"
    trading.launch(db, 1, "AAA", "Aaa")
    adv(10)
    trading.buy(db, 2, "IDX1", 100)
    trading.buy(db, 2, "AAA", 10) if False else None
    social.flush_activity(db, {(1, -100123): 20}, {(-100123, 1), (-100123, 2)})
    assert db.q1("SELECT COUNT(*) c FROM group_members")["c"] == 2
    # the group index paid its holder
    assert core.stock_row(db, g["index_stock"])["acc_div"] > 0
    # daily cap
    social.flush_activity(db, {(1, -100123): 5000}, set())
    used = db.q1("SELECT SUM(count) n FROM activity WHERE user_id=1")["n"]
    assert used <= C.DEFAULT_GROUP_MSG_CAP
    check_invariants(db)


def test_group_bonus_only_once(db):
    user(db, 1)
    social.ensure_group(db, -5, "G")
    assert social.group_add_bonus(db, -5, 1, 3) == 0
    assert social.group_add_bonus(db, -5, 1, 20) == C.GROUP_ADD_BONUS
    assert social.group_add_bonus(db, -5, 1, 20) == 0


def test_referral_reward_on_activation(db):
    user(db, 1)
    user(db, 2, referrer=1)
    user(db, 3)
    trading.launch(db, 3, "AAA", "Aaa")
    before = core.get_user(db, 1)["cash"]
    for _ in range(3):
        adv(5)
        trading.buy(db, 2, "AAA", 2)
    assert core.get_user(db, 1)["cash"] == before + C.REF_BONUS_INVITER
    assert any(o[1] == "referral" for o in db.outbox)


# ---------------------------------------------------------------------------- rebirth
def test_rebirth(db):
    user(db, 1)
    user(db, 2, cash=C.coins(1000))
    trading.launch(db, 1, "AAA", "Aaa")
    adv(10)
    trading.buy(db, 2, "AAA", 50)
    with pytest.raises(GameError) as e:
        progress.rebirth(db, 1)
    assert e.value.code == "rb_need"
    db.x("UPDATE users SET cash=?, max_level=3 WHERE id=1", (C.coins(150_000),))
    adv(100)
    r = progress.rebirth(db, 1)
    assert r["gain"] >= 3 and r["count"] == 1
    u = core.get_user(db, 1)
    assert u["cash"] == C.START_CASH + C.ACH_REWARD["gold"]      # fresh start cash + the "Rebirth" achievement reward
    assert u["max_level"] == 0 and u["legacy_pts"] == r["gain"]
    assert core.stock_row(db, 1)["status"] == "delisted"
    assert core.get_user(db, 2)["cash"] > C.coins(1000) - C.coins(1000) // 2   # holder was paid out
    check_invariants(db)
    # skill points kept as a rebirth bonus, legacy perks purchasable
    assert progress.points_free(core.get_user(db, 1)) == C.REBIRTH_POINTS
    assert progress.upgrade(db, 1, "head")["level"] == 1


# ---------------------------------------------------------------------------- seasons
def test_season_rollover(db):
    user(db, 1)
    user(db, 2)
    user(db, 3)
    trading.launch(db, 1, "AAA", "Aaa")
    adv(10)
    trading.buy(db, 2, "AAA", 20)
    db.x("UPDATE users SET cash=cash+? WHERE id=3", (C.coins(9000),))
    db.x("UPDATE users SET trades=1 WHERE id=3")
    assert progress.end_season_if_due(db) is None
    adv(C.SEASON_DAYS * 86400 + 1)
    r = progress.end_season_if_due(db)
    assert r["season"] == 1 and r["winners"]
    assert progress.season_id(db) == 2
    assert any(o[1] == "season_prize" for o in db.outbox)


# ---------------------------------------------------------------------------- shop
def test_shop_idempotent(db):
    user(db, 1)
    assert shop.grant(db, 1, "badge_crown", "charge1", 50)
    assert not shop.grant(db, 1, "badge_crown", "charge1", 50)
    assert core.get_user(db, 1)["badge"] == "👑"
    shop.grant(db, 1, "pass", "charge2", 150)
    assert core.get_user(db, 1)["pass_season"] == 1
    d = progress.daily(db, 1)
    assert d["pct"] == 125
    with pytest.raises(GameError):
        shop.item_check(db, 1, "pass")
    with pytest.raises(GameError):
        shop.item_check(db, 1, "spotlight")
    with pytest.raises(GameError):
        shop.item_check(db, 1, "nonexistent")


def test_net_worth_and_ranking(db):
    user(db, 1)
    user(db, 2)
    trading.launch(db, 1, "AAA", "Aaa")
    adv(10)
    trading.buy(db, 2, "AAA", 30)
    social.ensure_group(db, -9, "G")
    social.flush_activity(db, {}, {(-9, 1), (-9, 2)})
    social.refresh_group_scores(db)
    lb = social.group_leaderboard(db, -9)
    assert [r["id"] for r in lb][0] in (1, 2)
    assert social.group_info(db, -9)["rank"] == 1
    assert [r["id"] for r in social.global_top(db)] == [2]   # only players who traded are ranked


def test_prune_removes_only_old_counters(db):
    core.kv_set(db, f"rep:1:{T[0] // 86400 - 10}", 5)
    core.kv_set(db, f"rep:1:{T[0] // 86400}", 5)
    core.kv_set(db, "minted", 5)
    db.x("INSERT INTO history VALUES(1,?,100,1)", (T[0] - 30 * 86400,))
    db.x("INSERT INTO history VALUES(1,?,100,1)", (T[0],))
    events.prune(db)
    keys = {r["key"] for r in db.qa("SELECT key FROM kv")}
    assert f"rep:1:{T[0] // 86400}" in keys and f"rep:1:{T[0] // 86400 - 10}" not in keys and "minted" in keys
    assert db.q1("SELECT COUNT(*) c FROM history")["c"] == 1
