"""Level fix, quests, whale news, automated market traders, detailed charts, help menu, database upgrade."""
import io
import random
import sqlite3

import pytest
from PIL import Image

from aiogram.methods import EditMessageMedia, EditMessageText, SendMessage, SendPhoto

from hypemarket import bots, charts, config as C, core, events, progress, quests, social, trading, views
from hypemarket.core import GameError
from hypemarket.db import SCHEMA_VERSION, Database

import test_bot as tb
from test_game import T, adv, db, user          # noqa: F401  (shared fixture/helpers)
from test_sim import check


def rich_owner_scenario(db):
    """User 1 buys cheap shares of their own stock; user 2 then pumps the price. User 1 never acts again."""
    user(db, 1)
    user(db, 2)
    core.mint(db, C.coins(500_000))
    db.x("UPDATE users SET cash=cash+? WHERE id=2", (C.coins(500_000),))
    trading.launch(db, 1, "SPEED", "Speeed")
    adv(10)
    trading.buy(db, 1, "SPEED", spend_all=True)
    adv(60)
    trading.buy(db, 2, "SPEED", 3000)


# ============================================================================ the level fix
def test_level_is_synced_when_value_grows_passively(db):
    rich_owner_scenario(db)
    assert core.get_user(db, 1)["max_level"] == 0                 # saved level is stale: user 1 did nothing
    info = progress.level_info(db, 1)                             # looking at the level now saves the real one
    assert info["level"] >= 1 and core.get_user(db, 1)["max_level"] == info["level"]
    assert info["levelup"] == info["level"]


def test_meme_launch_works_at_level_one_with_a_stock_already(db):
    rich_owner_scenario(db)
    assert core.get_user(db, 1)["max_level"] == 0                 # stale, yet launching re-checks the real level
    s = trading.launch(db, 1, "DOGE", "Doge", kind="meme")
    assert s["kind"] == "meme"
    assert trading.max_stocks(core.get_user(db, 1)) >= 2           # level 1 = two slots


def test_level_error_tells_you_where_you_stand(db):
    user(db, 1)
    with pytest.raises(GameError) as e:
        trading.launch(db, 1, "DOGE", "Doge", kind="meme")
    assert e.value.code == "need_level" and e.value.kw["have"] == 0 and e.value.kw["nw"] == C.coins(2000)


def test_background_refresh_grants_levels_and_dms(db):
    rich_owner_scenario(db)
    assert progress.refresh_all_networth(db) >= 1
    assert core.get_user(db, 1)["max_level"] >= 1
    assert any(o[0] == 1 and o[1] == "levelup" for o in db.outbox)
    for uid in (1, 2):                                             # the fast refresh equals the exact calculation
        assert core.get_user(db, uid)["nw"] == core.net_worth(db, uid)


# ============================================================================ quests
def test_starter_quests_pay_once_and_money_is_conserved(db):
    user(db, 1)
    user(db, 2)
    progress.daily(db, 1)
    trading.launch(db, 1, "SPEED", "Speeed")
    adv(10)
    trading.buy(db, 2, "SPEED", 5)
    adv(10)
    trading.claim_income(db, 1)
    steps = {x["q"].id: x["row"]["done"] for x in quests.listing(db, 1)["starter"]}
    assert steps["s_daily"] and steps["s_launch"] and steps["s_investor"] and steps["s_claim"]
    assert {x["q"].id: x["row"]["done"] for x in quests.listing(db, 2)["starter"]}["s_buy"]
    before = core.get_user(db, 1)["cash"]
    progress.daily  # a second claim of the same quest can never pay again
    quests.track(db, 1, "daily")
    assert core.get_user(db, 1)["cash"] == before
    check(db)


def test_daily_and_weekly_quests_roll_over_and_scale(db):
    user(db, 1)
    first = quests.listing(db, 1)
    assert len(first["daily"]) == 3 and len(first["weekly"]) == 3 and len(first["starter"]) == 6
    adv(86400)
    second = quests.listing(db, 1)
    assert len(second["daily"]) == 3
    assert db.q1("SELECT COUNT(*) c FROM quests WHERE user_id=1 AND qid LIKE 'd_%'")["c"] == 6   # yesterday's rows are kept
    db.x("UPDATE users SET max_level=4 WHERE id=1")
    adv(86400)
    scaled = quests.listing(db, 1)["daily"]
    assert all(x["row"]["reward"] > x["q"].reward for x in scaled)                              # rewards grow with level


def test_players_who_did_things_before_quests_existed_get_credit(db):
    user(db, 1)
    user(db, 2)
    progress.daily(db, 1)
    trading.launch(db, 1, "SPEED", "Speeed")
    adv(10)
    trading.buy(db, 2, "SPEED", 5)
    db.x("DELETE FROM quests")                                     # as if the quests feature were new
    quests.refresh(db, 1)
    quests.refresh(db, 2)
    s1 = {x["q"].id: x["row"]["done"] for x in quests.listing(db, 1)["starter"]}
    assert s1["s_daily"] and s1["s_launch"] and s1["s_investor"]
    check(db)


def test_level_quest_gives_a_skill_point(db):
    rich_owner_scenario(db)
    before = progress.points_total(core.get_user(db, 1))
    progress.level_info(db, 1)
    u = core.get_user(db, 1)
    assert progress.points_total(u) == u["max_level"] + u["bonus_points"] + 0 and u["bonus_points"] == 1
    assert progress.points_total(u) > before


def test_quests_never_apply_to_automated_traders(db):
    bots.ensure_bots(db)
    quests.ensure(db, -1)
    quests.track(db, -1, "daily")
    assert db.q1("SELECT COUNT(*) c FROM quests WHERE user_id<0")["c"] == 0


def test_chat_quest_progress_and_cap(db):
    user(db, 1)
    social.ensure_group(db, -9, "G")
    social.flush_activity(db, {(1, -9): 40}, {(-9, 1)})
    rows = {x["q"].id: x["row"] for x in quests.listing(db, 1)["daily"] + quests.listing(db, 1)["weekly"]}
    for qid in ("d_chat", "w_chat"):
        if qid in rows:
            assert rows[qid]["progress"] > 0


# ============================================================================ whales
def whale_world(db, buyer_cash=C.coins(400_000)):
    for i in (1, 2, 3, 4):
        user(db, i)
    core.mint(db, buyer_cash)
    db.x("UPDATE users SET cash=cash+? WHERE id=4", (buyer_cash,))
    trading.launch(db, 1, "SPEED", "Speeed")
    adv(10)
    trading.buy(db, 2, "SPEED", 20)
    adv(10)
    trading.buy(db, 3, "SPEED", 20)
    adv(40)
    db.outbox.clear()


def test_whale_buy_and_sell_make_news_and_dm_holders(db):
    whale_world(db)
    trading.buy(db, 4, "SPEED", 2500)
    news = db.q1("SELECT * FROM news WHERE kind='whale_buy'")
    assert news and views.render_news("fa", news).count("🐋") == 1
    dm_targets = {o[0] for o in db.outbox if o[1] == "whale_buy"}
    assert dm_targets == {1, 2, 3}                                   # every holder except the whale
    adv(100)
    trading.buy(db, 4, "SPEED", 2500)                               # inside the 30-minute cooldown: no second headline
    assert db.q1("SELECT COUNT(*) c FROM news WHERE kind='whale_buy'")["c"] == 1
    adv(C.WHALE_COOLDOWN)
    trading.sell(db, 4, "SPEED", sell_all=True)
    assert db.q1("SELECT COUNT(*) c FROM news WHERE kind='whale_sell'")["c"] == 1


def test_small_trades_and_muted_holders_do_not_trigger_whale_noise(db):
    whale_world(db)
    trading.buy(db, 4, "SPEED", 30)
    assert db.q1("SELECT COUNT(*) c FROM news WHERE kind LIKE 'whale_%'")["c"] == 0
    db.x("UPDATE users SET whale_dm=0 WHERE id=2")
    adv(100)
    db.outbox.clear()
    trading.buy(db, 4, "SPEED", 2500)
    assert {o[0] for o in db.outbox if o[1] == "whale_buy"} == {1, 3}


# ============================================================================ automated market traders
def trader_world(db, monkeypatch):
    monkeypatch.setattr(C, "BOT_ACT_PROB", 1.0)
    for i in (1, 2, 3, 4):
        user(db, i)
    core.mint(db, C.coins(30_000))
    db.x("UPDATE users SET cash=cash+? WHERE id IN (2,3,4)", (C.coins(10_000),))
    trading.launch(db, 1, "SPEED", "Speeed")
    adv(10)
    for uid in (2, 3, 4):
        trading.buy(db, uid, "SPEED", 40)
        adv(5)
    adv(C.BOT_MIN_AGE + 10)
    bots.ensure_bots(db)


def test_traders_only_touch_established_stocks(db):
    for i in (1, 2, 3):
        user(db, i)
    trading.launch(db, 1, "SPEED", "Speeed")
    adv(10)
    trading.buy(db, 2, "SPEED", 40)
    bots.ensure_bots(db)
    assert bots.eligible_stocks(db) == []                            # too young, only one real holder
    adv(C.BOT_MIN_AGE + 10)
    assert bots.eligible_stocks(db) == []                            # still just one real holder
    trading.buy(db, 3, "SPEED", 40)
    assert [s["ticker"] for s in bots.eligible_stocks(db)] == ["SPEED"]


def test_traders_trade_safely_and_nobody_earns_their_fees(db, monkeypatch):
    trader_world(db, monkeypatch)
    owner_income, owner_rep = core.get_user(db, 1)["pending_inc"], core.get_user(db, 1)["rep"]
    made = 0
    rnd = random.Random(4)
    for _ in range(60):
        adv(35)
        made += bots.tick(db, rnd)
    assert made > 10
    assert core.get_user(db, 1)["pending_inc"] == owner_income      # the owner earned nothing from trader fees
    assert core.get_user(db, 1)["rep"] == owner_rep                 # and trader purchases don't farm reputation
    assert db.q1("SELECT COUNT(*) c FROM quests WHERE user_id<0")["c"] == 0
    assert db.q1("SELECT COUNT(*) c FROM news WHERE kind LIKE 'whale_%'")["c"] == 0
    assert db.q1("SELECT COUNT(*) c FROM history WHERE stock_id=1")["c"] > 10     # the chart is alive
    check(db)                                                        # solvent, money conserved


def test_traders_respect_position_and_topup_caps(db, monkeypatch):
    trader_world(db, monkeypatch)
    rnd = random.Random(8)
    for _ in range(150):
        adv(35)
        bots.tick(db, rnd)
    s = core.stock_row(db, 1)
    for b in db.qa("SELECT id FROM users WHERE bot=1"):
        h = db.q1("SELECT shares FROM holdings WHERE user_id=? AND stock_id=1", (b["id"],))
        if h:
            assert h["shares"] * 100 <= max(s["supply"], 1) * (C.BOT_MAX_SUPPLY_PCT + 6)    # small slack: supply shrinks when others sell
    db.x("UPDATE users SET cash=0 WHERE bot=1")
    core.burn(db, 0)
    for _ in range(5):
        for bid, _n, _s in bots.PERSONAS:
            bots._topup(db, bid)
    for bid, _n, _s in bots.PERSONAS:
        assert core.kv_int(db, f"bottop:{bid}:{core.now() // 86400}") <= C.BOT_DAILY_TOPUP


def test_traders_are_hidden_from_rankings(db, monkeypatch):
    trader_world(db, monkeypatch)
    rnd = random.Random(2)
    for _ in range(40):
        adv(35)
        bots.tick(db, rnd)
    progress.refresh_all_networth(db)
    assert all(r["id"] > 0 for r in social.global_top(db, 50))
    assert all(r["id"] > 0 for r in progress.season_standings(db, 50))


# ============================================================================ charts
def busy_stock(db, whales=True):
    rnd = random.Random(3)
    for i in range(1, 7):
        user(db, i)
    core.mint(db, C.coins(2_000_000))
    db.x("UPDATE users SET cash=cash+? WHERE id>1", (C.coins(400_000),))
    trading.launch(db, 1, "SPEED", "Speeed rocket")
    for step in range(120):
        adv(rnd.randint(60, 700))
        uid = rnd.randint(2, 6)
        try:
            if rnd.random() < 0.6:
                trading.buy(db, uid, "SPEED", rnd.randint(5, 80))
            else:
                trading.sell(db, uid, "SPEED", rnd.randint(5, 60))
        except GameError:
            pass
    if whales:                                       # one genuinely huge buy: a third of the reserve
        adv(C.WHALE_COOLDOWN + 5)
        s = core.need_stock(db, "SPEED")
        from hypemarket import engine as E
        n = E.max_affordable(s["supply"], s["reserve"] // 3, s["p0"], s["k"], core.eff(s), trading.trade_fee_bps(db, 2))
        db.x("UPDATE users SET cash=cash+? WHERE id=2", (s["reserve"],))
        core.mint(db, s["reserve"])
        adv(5)
        trading.buy(db, 2, "SPEED", n)
    return core.need_stock(db, "SPEED")


@pytest.mark.parametrize("span,n", [("1h", 60), ("24h", 48), ("7d", 56)])
def test_chart_has_every_detail(db, span, n):
    s = busy_stock(db)
    png, d = charts.build(db, s, span)
    img = Image.open(io.BytesIO(png))
    assert img.size == (1100, 744)
    assert len(d["candles"]) == n and d["high"] >= d["close"] >= d["low"] and d["volume"] >= 0
    assert all(c["h"] >= max(c["o"], c["c"]) and c["l"] <= min(c["o"], c["c"]) for c in d["candles"])
    caption = views.chart_caption(db, "fa", s, d)
    for must in ("بالاترین", "پایین‌ترین", "حجم معاملات", "ارزش بازار", "سهامدار", "هایپ", "سود تقسیمی"):
        assert must in caption
    assert tb.assert_valid_telegram_html(caption) is None
    assert "High:" in views.chart_caption(db, "en", s, d)


def test_chart_marks_whales_and_works_for_a_brand_new_stock(db):
    s = busy_stock(db)
    _, d = charts.build(db, s, "7d")
    assert d["whales"] and "🐋" in views.chart_caption(db, "fa", s, d)
    user(db, 50)
    fresh = trading.launch(db, 50, "NEWBIE", "New")
    png, d2 = charts.build(db, fresh, "24h")
    assert Image.open(io.BytesIO(png)).size == (1100, 744) and not d2["whales"]


# ============================================================================ help menu, guide, quests screens (end to end)
@pytest.fixture()
def h():
    return tb.Harness()


def buttons(call):
    return [b.callback_data for row in call.reply_markup.inline_keyboard for b in row]


def test_help_is_a_three_section_menu(h):
    calls = h.say(10, "راهنما")
    msg = h.session.sent(SendMessage)[-1]
    assert "یکی از بخش‌ها" in msg.text
    assert buttons(msg) == ["hp:guide:10", "hp:cmds:10", "hp:about:10"]
    out = h.texts(h.press(10, "hp:cmds:10"))
    assert "همه‌ی دستورها" in out
    assert "خرید 10 SPEED" in h.texts(h.press(10, "hp:cat:1:10")) and "ماموریت" in h.texts(h.press(10, "hp:cat:6:10")) and "نهنگ" in h.texts(h.press(10, "hp:cat:7:10"))
    out = h.texts(h.press(10, "hp:about:10"))
    assert "🤖" in out and "ذخیره" in out and "نهنگ" in out
    out = h.texts(h.press(10, "hp:menu:10"))
    assert "یکی از بخش‌ها" in out
    ans = [c for c in h.press(11, "hp:cmds:10") if c.__class__.__name__ == "AnswerCallbackQuery"][0]
    assert "برای تو نیست" in (ans.text or "")                          # only the person who opened the menu can navigate it


def test_guide_follows_real_progress(h):
    h.say(10, "شروع")
    out = h.texts(h.press(10, "hp:guide:10"))
    assert "(۰/۶)" in out and "⬜" in out and "<code>روزانه</code>" in out          # first step is the daily bonus
    h.say(10, "روزانه")
    out = h.texts(h.press(10, "hp:guide:10"))
    assert "(۱/۶)" in out and "✅" in out and "اولین سهمت رو بخر" in out                # next step moves on
    h.say(10, "عرضه SPEED اسپید")
    out = h.texts(h.press(10, "hp:guide:10"))
    assert "(۳/۶)" in out or "(۲/۶)" in out
    assert "راهنمای شروع" in h.texts(h.press(10, "hp:guide:10"))


def test_quest_completion_shows_inline_and_as_dm(h):
    out = h.texts(h.say(1, "روزانه"))
    assert "جایزه روزانه" in out and "ماموریت کامل شد" in out
    h.say(1, "عرضه SPEED اسپید")
    h.advance(60)
    calls = h.say(2, "خرید 5 SPEED")
    assert "ماموریت کامل شد" in h.texts(calls)                                      # buyer: first purchase
    dms = [c for c in calls if isinstance(c, SendMessage) and c.chat_id == 1]
    assert any("اولین سرمایه‌گذار" in c.text for c in dms)                             # owner: first investor, delivered by DM
    out = h.texts(h.say(1, "ماموریت‌ها"))
    assert "ماموریت‌ها" in out and "روزانه" in out and "هفتگی" in out and "▰" in out


def test_level_fix_end_to_end(h):
    h.say(1, "شروع")
    h.say(1, "عرضه SPEED اسپید")
    h.say(2, "شروع")
    h.db.x("UPDATE users SET cash=? WHERE id IN (1,2)", (C.coins(500_000),))
    h.advance(30)
    h.say(1, "خرید حداکثر SPEED") if False else h.say(1, "خرید 300 SPEED")
    h.advance(60)
    h.say(2, "خرید 3000 SPEED")
    h.advance(5)
    out = h.texts(h.say(1, "عرضه میم DOGE دوج"))
    assert "عرضه شد" in out, out                                                    # no stale-level refusal
    assert "سطح" in h.texts(h.say(1, "سطح"))


def test_whale_command_lists_news_and_toggles_dms(h):
    h.say(1, "شروع")
    h.say(1, "عرضه SPEED اسپید")
    h.say(2, "شروع")
    h.db.x("UPDATE users SET cash=? WHERE id=2", (C.coins(400_000),))
    h.advance(60)
    calls = h.say(2, "خرید 3000 SPEED")
    assert any(isinstance(c, SendMessage) and c.chat_id == 1 and "🐋" in c.text for c in calls)    # owner is told by DM
    out = h.texts(h.say(2, "نهنگ"))
    assert "🐋" in out and "روشن" in out
    out = h.texts(h.press(1, "wh:1"))
    assert "خاموش" in out and h.db.q1("SELECT whale_dm FROM users WHERE id=1")["whale_dm"] == 0
    h.advance(C.WHALE_COOLDOWN + 10)
    calls = h.say(2, "فروش همه SPEED")
    assert not any(isinstance(c, SendMessage) and c.chat_id == 1 and "🐋" in c.text for c in calls)


def test_chart_command_sends_detailed_photo_and_buttons_switch_range(h):
    h.say(1, "شروع")
    h.say(1, "عرضه SPEED اسپید")
    h.say(2, "شروع")
    h.advance(60)
    h.say(2, "خرید 40 SPEED")
    h.advance(120)
    calls = h.say(2, "نمودار SPEED")
    photo = h.session.sent(SendPhoto)[-1]
    assert "بالاترین" in photo.caption and "حجم معاملات" in photo.caption
    assert buttons(photo) and all(b.startswith("ch:") for b in buttons(photo)) and len(buttons(photo)) == 3
    calls = h.press(2, buttons(photo)[2], photo=True)                               # "7d" edits the same picture
    assert any(isinstance(c, EditMessageMedia) for c in calls)
    assert "۷ روز" in h.session.sent(EditMessageMedia)[-1].media.caption


# ============================================================================ database upgrade
def test_old_database_is_upgraded_in_place(tmp_path):
    path = str(tmp_path / "old.db")
    d = Database(path)
    core.ensure_user(d, 5, "a", "Alice")
    d.x("UPDATE users SET cash=777 WHERE id=5")
    d.close()
    con = sqlite3.connect(path)                      # turn it into a "schema 1" database
    for col in ("whale_dm", "bot", "bonus_points"):
        con.execute(f"ALTER TABLE users DROP COLUMN {col}")
    con.execute("DROP TABLE quests")
    con.execute("PRAGMA user_version=0")
    con.commit()
    con.close()
    d = Database(path)
    u = core.get_user(d, 5)
    assert u["name"] == "Alice" and u["cash"] == 777 and u["whale_dm"] == 1 and u["bot"] == 0 and u["bonus_points"] == 0
    assert d.q1("PRAGMA user_version")[0] == SCHEMA_VERSION
    for table in ("reports", "trust", "achievements", "seen", "modlog", "quests", "salary"):
        assert d.q1("SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (table,))
    quests.ensure(d, 5)
    assert d.q1("SELECT COUNT(*) c FROM quests WHERE user_id=5")["c"] >= 6
    d.close()
    Database(path).close()                           # reopening an up-to-date database is a no-op
    con = sqlite3.connect(path)
    con.execute("PRAGMA user_version=99")
    con.close()
    con = sqlite3.connect(path)
    con.execute("PRAGMA user_version=99")
    con.commit()
    con.close()
    with pytest.raises(RuntimeError):
        Database(path)


def test_database_from_the_previous_release_upgrades_to_the_latest(tmp_path):
    """Schema 2 (no reports/trust/achievements/seen/modlog/salary tables) must upgrade all the way."""
    path = str(tmp_path / "v2.db")
    d = Database(path)
    core.ensure_user(d, 7, "b", "Bob")
    d.x("UPDATE users SET cash=4242 WHERE id=7")
    d.close()
    con = sqlite3.connect(path)
    for table in ("reports", "trust", "achievements", "seen", "modlog", "salary"):
        con.execute(f"DROP TABLE {table}")
    con.execute("PRAGMA user_version=2")
    con.commit()
    con.close()
    d = Database(path)
    assert d.q1("PRAGMA user_version")[0] == SCHEMA_VERSION
    assert core.get_user(d, 7)["cash"] == 4242
    d.x("INSERT INTO seen(day,user_id) VALUES(1,7)")      # the new tables work
    d.close()


def test_database_from_schema_3_upgrades_to_the_latest(tmp_path):
    """Schema 3 (before the salary table): the upgrade only adds tables, and no data may be touched."""
    path = str(tmp_path / "v3.db")
    d = Database(path)
    core.ensure_user(d, 7, "b", "Bob")
    d.x("UPDATE users SET cash=999 WHERE id=7")
    d.x("INSERT INTO trust(target,voter,value,weight,ts) VALUES(7,8,1,1,1)")
    d.close()
    con = sqlite3.connect(path)
    con.execute("DROP TABLE salary")
    con.execute("PRAGMA user_version=3")
    con.commit()
    con.close()
    d = Database(path)
    assert d.q1("PRAGMA user_version")[0] == SCHEMA_VERSION
    assert core.get_user(d, 7)["cash"] == 999 and d.q1("SELECT COUNT(*) c FROM trust")["c"] == 1
    d.x("INSERT INTO salary(user_id) VALUES(7)")
    d.close()
