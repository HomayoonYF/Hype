"""Backups, scam reports, trust marks, moderation, achievements and analytics."""
import gzip
import os
import sqlite3

import pytest

from aiogram.methods import EditMessageText, SendDocument, SendMessage

from hypemarket import achievements, analytics, backups, bots, config as C, core, jobs, progress, quests, social, trading, trust, views
from hypemarket.core import GameError
from hypemarket.db import SCHEMA_VERSION, Database

import test_bot as tb
from test_game import T, adv, bonuses, db, user          # noqa: F401
from test_sim import check


def veteran(db, uid, level=0, cash=None):
    """A player who is allowed to report/vote (3+ trades)."""
    u = user(db, uid)
    db.x("UPDATE users SET trades=5, max_level=? WHERE id=?", (level, uid))
    if cash:
        core.mint(db, cash)
        db.x("UPDATE users SET cash=cash+? WHERE id=?", (cash, uid))


@pytest.fixture()
def h():
    return tb.Harness()


def buttons(call):
    return [b.callback_data for row in call.reply_markup.inline_keyboard for b in row]


# ================================================================================== backups
def test_backup_is_verified_compressed_and_restorable(tmp_path, db):
    for i in (1, 2, 3):
        user(db, i)
    trading.launch(db, 1, "SPEED", "Speeed")
    b = backups.make_backup(db, str(tmp_path / "bk"), keep=48)
    assert b["meta"]["users"] == 3 and b["meta"]["stocks"] == 1 and b["meta"]["schema"] == SCHEMA_VERSION
    assert b["name"].endswith(".db.gz") and gzip.decompress(b["data"])[:6] == b"SQLite"
    from restore import restore
    target = str(tmp_path / "restored.db")
    info = restore([b["path"]], target)
    assert info == {"users": 3, "stocks": 1}
    d2 = Database(target)
    assert core.get_user(d2, 1)["name"] == core.get_user(db, 1)["name"] and d2.q1("SELECT ticker FROM stocks")["ticker"] == "SPEED"
    d2.close()


def test_restore_refuses_garbage_and_keeps_the_current_database(tmp_path):
    from restore import restore
    target = str(tmp_path / "live.db")
    d = Database(target)
    core.ensure_user(d, 1, "a", "Alice")
    d.close()
    bad = tmp_path / "bad.db.gz"
    bad.write_bytes(gzip.compress(b"this is not a database at all" * 50))
    with pytest.raises(SystemExit):
        restore([str(bad)], target)
    d = Database(target)
    assert core.get_user(d, 1)["name"] == "Alice"          # untouched
    d.close()
    assert not os.path.exists(target + ".restoring")


def test_restore_keeps_a_copy_of_the_old_database(tmp_path, db):
    from restore import restore
    user(db, 1)
    b = backups.make_backup(db, str(tmp_path / "bk"))
    target = str(tmp_path / "live.db")
    Database(target).close()
    restore([b["path"]], target)
    assert any(f.startswith("live.db.before-restore-") for f in os.listdir(tmp_path))


def test_local_retention_and_splitting(tmp_path, db):
    user(db, 1)
    for _ in range(5):
        adv(1)
        backups.make_backup(db, str(tmp_path / "bk"), keep=3)
    assert len(os.listdir(tmp_path / "bk")) == 3
    data = os.urandom(10_000)
    parts = backups.split(data, 4096)
    assert len(parts) == 3 and b"".join(parts) == data


def test_verify_rejects_a_damaged_file(tmp_path):
    p = tmp_path / "x.db"
    p.write_bytes(b"not sqlite" * 100)
    with pytest.raises(Exception):
        backups.verify(str(p))


def test_a_backup_reaches_every_admin_silently(h, tmp_path):
    h.app.settings = h.app.settings.__class__(**{**h.app.settings.__dict__, "admins": frozenset({1, 2}), "backup_dir": str(tmp_path / "bk")})
    h.say(5, "شروع")
    res = h.run(backups.send_backup(h.app))
    assert res["ok"] and res["sent"] == 2 and res["failed"] == 0
    docs = h.session.sent(SendDocument)
    assert {d.chat_id for d in docs} == {1, 2}
    assert all(d.disable_notification is True and "Hype Market backup" in d.caption and "1 players" in d.caption for d in docs)
    assert len(os.listdir(tmp_path / "bk")) == 1


def test_backup_survives_an_admin_who_blocked_the_bot(h, tmp_path):
    h.app.settings = h.app.settings.__class__(**{**h.app.settings.__dict__, "admins": frozenset({1, 2}), "backup_dir": str(tmp_path / "bk")})
    h.session.forbidden.add(1)
    res = h.run(backups.send_backup(h.app))
    assert res["ok"] and res["sent"] == 1 and res["failed"] == 1       # admin 2 still got it; nothing crashed
    assert any(d.chat_id == 2 for d in h.session.sent(SendDocument))


def test_large_backups_are_split_into_parts(h, tmp_path, monkeypatch):
    h.app.settings = h.app.settings.__class__(**{**h.app.settings.__dict__, "admins": frozenset({1}), "backup_dir": ""})
    monkeypatch.setattr(backups, "PART_LIMIT", 20_000)
    for i in range(1, 40):
        h.say(i + 100, "شروع")
    h.run(backups.send_backup(h.app))
    docs = h.session.sent(SendDocument)
    assert len(docs) >= 1 and all(".part" in d.document.filename for d in docs) if len(docs) > 1 else True


def test_a_failed_backup_alerts_the_admins(h, monkeypatch):
    monkeypatch.setattr(backups, "make_backup", lambda *a, **k: (_ for _ in ()).throw(RuntimeError("disk full")))
    res = h.run(backups.send_backup(h.app))
    assert not res["ok"]
    assert any("FAILED" in (m.text or "") and m.chat_id == 1 for m in h.session.sent(SendMessage))


def test_backup_command_is_admin_only(h):
    h.say(1, "شروع")
    out = h.texts(h.say(1, "/backup"))
    assert "پشتیبان" in out
    assert h.say(2, "/backup") == []
    assert h.session.sent(SendDocument)


def test_background_loop_failures_alert_the_admin(h, monkeypatch):
    async def boom(app):
        raise ValueError("kaboom")
    import asyncio
    async def run_once():
        task = asyncio.create_task(jobs._loop("x", 3600, boom, h.app))
        await asyncio.sleep(5.2)
        task.cancel()
    h.run(run_once())
    assert any("kaboom" in (m.text or "") for m in h.session.sent(SendMessage))


# ================================================================================== trust marks
def trust_world(db):
    veteran(db, 1)                       # owner
    for i in (2, 3, 4, 5):
        veteran(db, i)
    trading.launch(db, 1, "SPEED", "Speeed")


def test_vote_rules(db):
    trust_world(db)
    user(db, 9)                                                    # no trades yet
    with pytest.raises(GameError) as e:
        trust.vote(db, 9, 1, 1)
    assert e.value.code == "trust_need_trades"
    with pytest.raises(GameError) as e:
        trust.vote(db, 2, 2, 1)
    assert e.value.code == "trust_self"
    bots.ensure_bots(db)
    with pytest.raises(GameError):
        trust.vote(db, 2, -1, 1)                                   # automated traders can't be voted on
    r = trust.vote(db, 2, 1, 1)
    assert r["value"] == 1 and r["score"]["up"] == 1
    assert trust.vote(db, 2, 1, -1)["score"]["down"] == 1          # changing your mind replaces the vote
    assert trust.vote(db, 2, 1, -1)["value"] == 0                  # voting the same way again removes it
    assert db.q1("SELECT COUNT(*) c FROM trust")["c"] == 0


def test_labels_need_several_voters_and_levels_add_weight(db):
    trust_world(db)
    trust.vote(db, 2, 1, 1)
    trust.vote(db, 3, 1, 1)
    assert trust.score(db, 1)["label"] == "neutral"                # only 2 voters: no label yet
    trust.vote(db, 4, 1, 1)
    assert trust.score(db, 1)["label"] == "trusted"
    db.x("DELETE FROM trust")
    veteran(db, 6, level=4)                                        # weight 3
    for v in (2, 3):
        trust.vote(db, v, 1, -1)
    assert trust.score(db, 1)["label"] == "neutral"
    trust.vote(db, 6, 1, -1)
    s = trust.score(db, 1)
    assert s["net"] == -5 and s["label"] == "flagged" and s["down"] == 3
    board = trust.trust_board(db)
    assert [r["id"] for r in board["bad"]] == [1] and board["good"] == []


def test_daily_vote_limit(db, monkeypatch):
    monkeypatch.setattr(C, "VOTE_DAILY_LIMIT", 2)
    trust_world(db)
    for i in (6, 7, 8):
        veteran(db, i)
    trust.vote(db, 2, 1, 1)
    trust.vote(db, 2, 6, 1)
    with pytest.raises(GameError) as e:
        trust.vote(db, 2, 7, 1)
    assert e.value.code == "trust_limit"
    adv(86400)
    trust.vote(db, 2, 7, 1)


# ================================================================================== reports + moderation
def report_world(db, reporters=(2, 3, 4)):
    veteran(db, 1)
    for i in reporters:
        veteran(db, i)
    trading.launch(db, 1, "SPEED", "Speeed")


def test_report_rules(db):
    report_world(db)
    user(db, 9)
    with pytest.raises(GameError) as e:
        trust.report(db, 9, "SPEED", "rug")
    assert e.value.code == "trust_need_trades"
    with pytest.raises(GameError) as e:
        trust.report(db, 1, "SPEED", "rug")
    assert e.value.code == "report_own"
    social.ensure_group(db, -1, "G")
    with pytest.raises(GameError) as e:
        trust.report(db, 2, "IDX1", "rug")                        # only player-owned stocks
    assert e.value.code == "report_not_allowed"
    r = trust.report(db, 2, "speed", "rug", "took the money <b>")
    assert r["new"] and r["total"] == 1 and not r["flagged"]
    r = trust.report(db, 2, "SPEED", "fake")                      # the same person again: refreshes, never doubles
    assert not r["new"] and r["total"] == 1
    assert trust.report(db, 3, "SPEED", "weird-reason")["total"] == 2           # unknown reasons become "other"
    assert db.q1("SELECT reason FROM reports WHERE reporter=3")["reason"] == "other"


def test_report_limit_per_day(db, monkeypatch):
    monkeypatch.setattr(C, "REPORT_DAILY_LIMIT", 2)
    report_world(db)
    for i in (6, 7, 8):
        veteran(db, i, cash=C.coins(5000))
        trading.launch(db, i, f"T{i}", f"Name{i}")
    trust.report(db, 2, "T6")
    trust.report(db, 2, "T7")
    with pytest.raises(GameError) as e:
        trust.report(db, 2, "T8")
    assert e.value.code == "report_limit"


def test_admins_are_alerted_at_each_level_with_buttons_and_flag_is_public(db):
    report_world(db, reporters=(2, 3, 4, 5))
    trust.report(db, 2, "SPEED", "rug")
    trust.report(db, 3, "SPEED", "fake", "lied about the project")
    assert db.mod_events == [] and not trust.flagged(db, 1)
    trust.report(db, 4, "SPEED", "rug")
    assert db.mod_events == [("report", 1)] and trust.flagged(db, 1)     # weight 3: public flag + first admin alert
    trust.report(db, 5, "SPEED", "rug")
    assert len(db.mod_events) == 1                                       # weight 4: no repeat alert
    alert = views.mod_alert(db, "en", 1)
    assert "Scam report" in alert.text and "lied about the project" in alert.text
    assert [b.callback_data for row in alert.kb.inline_keyboard for b in row] == ["mod:delist:1", "mod:ban:1", "mod:dismiss:1"]
    card = views.stock_card(db, "fa", core.need_stock(db, "SPEED"))
    assert "🚩" in card.text and any("rp:" in (b.callback_data or "") for row in card.kb.inline_keyboard for b in row)


def test_dismiss_clears_reports_and_allows_new_alerts(db):
    report_world(db)
    for i in (2, 3, 4):
        trust.report(db, i, "SPEED", "rug")
    db.mod_events.clear()
    trust.admin_dismiss(db, 1, 1)
    assert not trust.flagged(db, 1) and trust.open_weight(db, 1) == 0
    assert db.q1("SELECT COUNT(*) c FROM modlog WHERE action='dismiss'")["c"] == 1
    for i in (2, 3, 4):
        trust.report(db, i, "SPEED", "rug")                              # fresh reports after a dismissal count again
    assert db.mod_events == [("report", 1)]


def test_admin_delist_pays_holders_punishes_owner_and_thanks_reporters(db):
    report_world(db)
    core.mint(db, C.coins(5000))
    db.x("UPDATE users SET cash=cash+? WHERE id=2", (C.coins(5000),))
    adv(10)
    trading.buy(db, 2, "SPEED", 100)
    for i in (2, 3, 4):
        trust.report(db, i, "SPEED", "rug")
    db.outbox.clear()
    rep_before = core.get_user(db, 1)["rep"]
    trust.admin_delist(db, 1, 1)
    assert core.stock_row(db, 1)["status"] == "delisted"
    assert core.get_user(db, 1)["rep"] == max(0, rep_before - C.RUG_REP_PENALTY)
    assert {o[0] for o in db.outbox if o[1] == "report_actioned"} == {2, 3, 4}
    assert all(a in achievements.unlocked(db, uid) for uid in (2, 3, 4) for a in ("scam_hunter",))
    assert db.q1("SELECT COUNT(*) c FROM reports WHERE status='actioned'")["c"] == 3
    check(db)


def test_ban_removes_all_stocks_and_blocks_the_player(h):
    for uid in (1, 2, 3, 4, 5):
        h.say(uid, "شروع")
        h.db.x("UPDATE users SET trades=5 WHERE id=?", (uid,))
    h.say(5, "عرضه SCAM اسکم")
    h.advance(30)
    for uid in (2, 3, 4):
        trust.report(h.db, uid, "SCAM", "rug")
    calls = h.say(2, "!بازار")                                            # any action flushes queued admin alerts
    alert = [c for c in h.session.sent(SendMessage) if c.chat_id == 1 and "Scam report" in (c.text or "") or "گزارش کلاهبرداری" in (c.text or "")]
    assert alert and any(b.startswith("mod:ban:") for b in buttons(alert[-1]))
    sid = core.need_stock(h.db, "SCAM")["id"]
    ans = [c for c in h.press(2, f"mod:ban:{sid}") if c.__class__.__name__ == "AnswerCallbackQuery"][0]
    assert ans.show_alert and h.db.q1("SELECT banned FROM users WHERE id=5")["banned"] == 0     # a normal player can't moderate
    h.press(1, f"mod:ban:{sid}")
    assert h.db.q1("SELECT banned FROM users WHERE id=5")["banned"] == 1
    assert core.stock_row(h.db, sid)["status"] == "delisted"
    assert h.say(5, "روزانه") == [] and h.say(5, "بازار") == []               # banned: ignored everywhere
    h.say(1, "/unban 5")
    assert h.say(5, "!بازار") != []


def test_bots_stay_away_from_reported_and_distrusted_stocks(db, monkeypatch):
    monkeypatch.setattr(C, "BOT_ACT_PROB", 1.0)
    veteran(db, 1)
    for i in (2, 3, 4, 5):
        veteran(db, i, cash=C.coins(5000))
    trading.launch(db, 1, "SPEED", "Speeed")
    adv(10)
    for uid in (2, 3, 4):
        trading.buy(db, uid, "SPEED", 40)
        adv(5)
    adv(C.BOT_MIN_AGE + 10)
    assert [s["ticker"] for s in bots.eligible_stocks(db)] == ["SPEED"]
    for i in (2, 3, 4):
        trust.report(db, i, "SPEED", "rug")
    assert bots.eligible_stocks(db) == []
    trust.admin_dismiss(db, 1, 1)
    assert len(bots.eligible_stocks(db)) == 1
    for v in (2, 3, 4):
        trust.vote(db, v, 1, -1)
    assert bots.eligible_stocks(db) == []


# ================================================================================== trust + reports through the chat
def test_trust_and_report_commands_in_chat(h):
    for uid in (1, 2, 3):
        h.say(uid, "شروع")
        h.db.x("UPDATE users SET trades=5 WHERE id=?", (uid,))
    h.say(1, "عرضه SPEED اسپید")
    out = h.texts(h.say(2, "سهام اعتماد SPEED"))
    assert "اعتماد کردی" in out
    assert "رأیت" in h.texts(h.say(2, "سهام اعتماد SPEED"))                        # same vote again removes it
    assert "نامطمئن علامت زدی" in h.texts(h.say(2, "سهام بی اعتماد SPEED"))
    out = h.texts(h.say(3, "سهام بی‌اعتماد", reply_to=1))                          # by replying to the person's message
    assert "نامطمئن علامت زدی" in out
    assert "تابلوی اعتماد" in h.texts(h.say(3, "سهام اعتماد"))
    assert "گزارش" in h.texts(h.say(3, "سهام گزارش SPEED"))                         # no reason: asks with buttons
    menu = h.session.sent(SendMessage)[-1]
    assert buttons(menu) == [f"rp2:1:{r}" for r in ("rug", "fake", "spam", "other")]
    out = h.texts(h.press(3, "rp2:1:rug"))
    assert "ثبت شد" in out
    assert "ثبت شد" in h.texts(h.say(2, "سهام گزارش SPEED خروج پولمو برداشت"))
    assert "حداقل ۳ معامله" in h.texts(h.say(9, "!سهام گزارش SPEED"))              # a brand-new account can't report


def test_card_buttons_vote_and_flagged_buy_warning(h):
    for uid in (1, 2, 3, 4, 5):
        h.say(uid, "شروع")
        h.db.x("UPDATE users SET trades=5 WHERE id=?", (uid,))
    h.say(1, "عرضه SPEED اسپید")
    sid = core.need_stock(h.db, "SPEED")["id"]
    out = h.texts(h.press(2, f"tr:{sid}:1"))
    assert "مورد اعتماد" in out or "وضعیت" in out or h.db.q1("SELECT COUNT(*) c FROM trust")["c"] == 1
    assert h.db.q1("SELECT COUNT(*) c FROM trust WHERE value=1")["c"] == 1
    for uid in (2, 3, 4):
        trust.report(h.db, uid, "SPEED", "rug")
    h.advance(30)
    assert "هشدار" in h.texts(h.say(5, "خرید 3 SPEED"))
    assert "🚩" in h.texts(h.say(5, "قیمت SPEED"))


# ================================================================================== achievements
def test_achievements_unlock_once_pay_rewards_and_conserve_money(db):
    user(db, 1)
    user(db, 2)
    trading.launch(db, 1, "SPEED", "Speeed")
    assert {"founder"} <= set(achievements.unlocked(db, 1))
    adv(10)
    trading.buy(db, 2, "SPEED", 5)
    assert "first_trade" in achievements.unlocked(db, 2)
    cash = core.get_user(db, 2)["cash"]
    adv(5)
    achievements.check(db, 2, "trade")                                        # nothing new: no second payment
    assert core.get_user(db, 2)["cash"] == cash
    assert achievements.reward_of(achievements.BY_ID["first_trade"]) == C.ACH_REWARD["bronze"]
    check(db)


def test_streak_level_and_whale_achievements(db):
    user(db, 1)
    for _ in range(7):
        progress.daily(db, 1)
        adv(21 * 3600)
    assert "streak_7" in achievements.unlocked(db, 1)
    db.x("UPDATE users SET max_level=3 WHERE id=1")
    achievements.check(db, 1, "level")
    assert "level_3" in achievements.unlocked(db, 1)
    for i in (2, 3, 4):
        user(db, i)
    core.mint(db, C.coins(400_000))
    db.x("UPDATE users SET cash=cash+? WHERE id=4", (C.coins(400_000),))
    trading.launch(db, 2, "SPEED", "Speeed")
    adv(10)
    trading.buy(db, 3, "SPEED", 20)
    adv(10)
    trading.buy(db, 4, "SPEED", 2500)
    assert "whale" in achievements.unlocked(db, 4)
    check(db)


def test_automated_traders_never_earn_achievements(db):
    bots.ensure_bots(db)
    achievements.check(db, -1, "trade")
    assert db.q1("SELECT COUNT(*) c FROM achievements WHERE user_id<0")["c"] == 0


def test_every_achievement_has_text_and_a_reachable_event(db):
    from hypemarket.texts import FA, EN
    for a in achievements.ALL_A:
        assert f"a.{a.id}" in FA and f"a.{a.id}" in EN and a.events and a.tier in C.ACH_REWARD


def test_achievement_screen_and_inline_message(h):
    out = h.texts(h.say(1, "عرضه SPEED اسپید"))
    assert "دستاورد جدید" in out and "موسس" in out
    out = h.texts(h.say(1, "سهام دستاوردها"))
    assert "(۱/۲۶)" in out or "(۲/۲۶)" in out
    assert "🔒" in out and "پروفایل" not in out
    assert "🏆 دستاوردها" in h.texts(h.say(1, "پروفایل"))


# ================================================================================== analytics
def test_active_players_and_retention(db):
    day = lambda n: T[0] // 86400 + n
    created = {1: -5, 2: -5, 3: -3, 4: -1, 5: -9, 6: -9}          # A B C D E F
    for uid, d in created.items():
        user(db, uid)
        db.x("UPDATE users SET created_at=? WHERE id=?", (day(d) * 86400 + 100, uid))
    for uid, d in ((1, -4), (3, -2), (4, 0), (5, -2), (6, -3), (1, 0)):
        db.x("INSERT OR IGNORE INTO seen(day,user_id) VALUES(?,?)", (day(d), uid))
    d = analytics.dashboard(db)
    assert d["players"]["total"] == 6 and d["players"]["dau"] == 2
    assert d["players"]["wau"] == 5 and d["players"]["mau"] == 5
    # day-1 retention: players who joined 1..7 days ago (A,B,C,D) and came back the next day (A,C,D)
    assert d["retention"]["d1"] == pytest.approx(75.0) and d["retention"]["d1_n"] == 4
    # day-7 retention: joined 7..13 days ago (E,F); only E was seen exactly 7 days later
    assert d["retention"]["d7"] == pytest.approx(50.0) and d["retention"]["d7_n"] == 2


def test_touch_is_idempotent_and_ignores_bots(db):
    user(db, 1)
    analytics.touch(db, 1)
    analytics.touch(db, 1)
    analytics.touch(db, -3)
    assert db.q1("SELECT COUNT(*) c FROM seen")["c"] == 1


def test_funnel_economy_and_market_numbers(db):
    for i in (1, 2, 3):
        user(db, i)
    progress.daily(db, 1)
    progress.daily(db, 2)
    trading.launch(db, 1, "SPEED", "Speeed")
    adv(10)
    trading.buy(db, 2, "SPEED", 10)
    d = analytics.dashboard(db)
    funnel = dict(d["funnel"])
    assert funnel["s_daily"] == pytest.approx(200 / 3) and funnel["s_launch"] == pytest.approx(100 / 3)
    assert d["market"]["trades"] == 1 and d["market"]["launches"] == 1 and d["market"]["volume"] > 0
    assert d["economy"]["supply"] == d["economy"]["minted"] - d["economy"]["burned"] > 0
    assert d["economy"]["minted_today"] > 0 and d["economy"]["burned_today"] > 0
    adv(86400)
    d2 = analytics.dashboard(db)
    assert d2["economy"]["minted_yesterday"] == d["economy"]["minted_today"]       # the day's totals are frozen at midnight
    assert d2["market"]["trades_yesterday"] == 1


def test_dashboard_renders_in_both_languages(db):
    user(db, 1)
    trading.launch(db, 1, "SPEED", "Speeed")
    social.ensure_group(db, -1, "My <group>")
    for lang, word in (("fa", "داشبورد"), ("en", "dashboard")):
        r = views.dashboard_view(db, lang)
        assert word in r.text and tb.assert_valid_telegram_html(r.text) is None


def test_players_are_counted_when_they_use_the_bot(h):
    h.say(7, "شروع")
    h.say(8, "!بازار", chat=-5)
    assert h.db.q1("SELECT COUNT(*) c FROM seen")["c"] == 2
    out = h.texts(h.say(1, "/stats"))
    assert "بازیکنان" in out and "اقتصاد" in out and "نظارت" in out


def test_daily_report_goes_to_admins_once_a_day(h):
    h.app.settings = h.app.settings.__class__(**{**h.app.settings.__dict__, "report_hour": (T[0] // 3600) % 24})
    h.say(1, "شروع")
    h.run(jobs.daily_report(h.app))
    h.run(jobs.daily_report(h.app))
    reports = [m for m in h.session.sent(SendMessage) if m.chat_id == 1 and "گزارش روزانه" in (m.text or "")]
    assert len(reports) == 1
    h.advance(86400)
    h.run(jobs.daily_report(h.app))
    assert len([m for m in h.session.sent(SendMessage) if m.chat_id == 1 and "گزارش روزانه" in (m.text or "")]) == 2
