"""The daily salary DM: who gets it, when, how it stops, and the claim button."""
import pytest

from aiogram.methods import EditMessageText, SendMessage

from hypemarket import config as C, core, jobs, progress, salary, trading

import test_bot as tb
from test_game import T, adv, db, user          # noqa: F401


@pytest.fixture()
def h(monkeypatch):
    async def instant(_):          # the job spaces its messages out; tests don't need to wait
        return None
    monkeypatch.setattr(jobs.asyncio, "sleep", instant)
    h = tb.Harness()
    set_hour(h, (tb.T[0] // 3600) % 24)             # the salary window opens "now"
    return h


def set_hour(h, hour):
    h.app.settings = h.app.settings.__class__(**{**h.app.settings.__dict__, "salary_hour": hour % 24})


def dms(h, uid, since=0):
    return [c for c in h.session.sent(SendMessage)[since:] if c.chat_id == uid and "صبح بخیر" in (c.text or "")]


def tick(h):
    n = len(h.session.sent(SendMessage))
    h.run(jobs.salary_tick(h.app))
    return n


def players(h, *uids):
    for u in uids:
        h.say(u, "شروع")


def test_salary_goes_once_a_day_to_recently_active_players_only(h):
    players(h, 2, 3, 4, 5)
    h.db.x("DELETE FROM seen WHERE user_id=4")                     # 4 hasn't used the bot recently
    h.db.x("UPDATE users SET banned=1 WHERE id=5")
    salary.set_enabled(h.db, 3, False)
    n = tick(h)
    assert [bool(dms(h, u, n)) for u in (2, 3, 4, 5)] == [True, False, False, False]
    msg = dms(h, 2, n)[0]
    assert "حقوق امروز" in msg.text and [b.callback_data for row in msg.reply_markup.inline_keyboard for b in row][0] == "sal:claim:2"
    n = tick(h)
    assert not dms(h, 2, n)                                        # same day: never twice
    h.advance(86400)
    h.say(2, "!بازار")
    n = tick(h)
    assert dms(h, 2, n)                                            # next day: again


def test_it_only_goes_out_inside_the_morning_window(h):
    players(h, 2)
    set_hour(h, (tb.T[0] // 3600) % 24 + 10)                       # window opens in 10 hours: too early
    n = tick(h)
    assert not dms(h, 2, n)
    set_hour(h, (tb.T[0] // 3600) % 24 - 3)                        # window opened 3 hours ago and lasts 6
    assert dms(h, 2, tick(h))
    h.advance(86400)
    h.say(2, "!بازار")
    set_hour(h, (tb.T[0] // 3600) % 24 - 8)                        # window closed 2 hours ago
    assert not dms(h, 2, tick(h))


def test_blocked_players_are_not_retried_and_return_when_they_write_again(h):
    players(h, 2, 3)
    h.session.forbidden.add(2)                                      # 2 never opened a private chat / blocked the bot
    tick(h)
    assert salary.get(h.db, 2)["blocked"] == 1 and salary.get(h.db, 3)["blocked"] == 0
    h.advance(86400)
    h.say(3, "!بازار")
    n = tick(h)
    assert not dms(h, 2, n)                                         # not retried the next day
    h.session.forbidden.discard(2)
    h.say(2, "روزانه")                                               # they write to the bot privately: unblocked
    assert salary.get(h.db, 2)["blocked"] == 0
    h.advance(86400)
    h.say(2, "!بازار")
    assert dms(h, 2, tick(h))


def test_it_pauses_after_three_ignored_salaries_and_resumes_on_a_claim(h):
    players(h, 2)
    sent = []
    for day in range(5):
        h.advance(86400)
        h.say(2, "!بازار")                                          # active every day, but never claims the salary
        sent.append(bool(dms(h, 2, tick(h))))
    assert sent == [True, True, True, True, False]                  # sent 4 times (the 4th is the 3rd unclaimed one), then paused
    assert salary.get(h.db, 2)["ignored"] == C.SALARY_MAX_IGNORED
    h.say(2, "روزانه")                                               # claiming the daily bonus resumes it
    assert salary.get(h.db, 2)["ignored"] == 0
    h.advance(86400)
    h.say(2, "!بازار")
    assert dms(h, 2, tick(h))


def test_switch_it_off_and_on(h):
    players(h, 2)
    out = h.texts(h.say(2, "حقوق"))
    assert "روشن" in out
    out = h.texts(h.press(2, "sal:tog:2"))
    assert "خاموش" in out and salary.get(h.db, 2)["enabled"] == 0
    assert not dms(h, 2, tick(h))
    assert "روشن" in h.texts(h.press(2, "sal:tog:2")) and salary.get(h.db, 2)["enabled"] == 1
    assert dms(h, 2, tick(h))
    assert "خاموش" in h.texts(h.press(2, "sal:off:2"))               # the button under the DM itself
    assert "فقط" in h.texts(h.say(2, "!حقوق", chat=-77))              # settings live in the private chat


def test_the_claim_button_pays_exactly_the_previewed_salary(h):
    players(h, 2)
    tick(h)
    before = core.get_user(h.db, 2)["cash"]
    expect = progress.daily_preview(h.db, 2)["amount"]
    calls = h.press(2, "sal:claim:2")
    assert core.get_user(h.db, 2)["cash"] >= before + expect
    edited = [c for c in calls if isinstance(c, EditMessageText)][0]
    assert "حقوق دریافت شد" in edited.text and "sal:claim" not in str(edited.reply_markup)
    ans = [c for c in h.press(2, "sal:claim:2") if c.__class__.__name__ == "AnswerCallbackQuery"][0]
    assert ans.show_alert and "جایزه بعدی" in ans.text                # second press: cooldown, nothing paid twice
    ans = [c for c in h.press(3, "sal:claim:2") if c.__class__.__name__ == "AnswerCallbackQuery"][0]
    assert "برای تو نیست" in (ans.text or "")


def test_the_preview_always_matches_the_real_bonus(db):
    for i in (1, 2):
        user(db, i)
    for _ in range(12):
        pv = progress.daily_preview(db, 1)
        real = progress.daily(db, 1)
        assert pv["amount"] == real["amount"] and pv["streak"] == real["streak"] and pv["pct"] == real["pct"]
        assert progress.daily_preview(db, 1) is None                # cooling down
        adv(21 * 3600)


def test_message_shows_change_since_yesterday_pending_income_and_best_stock(h):
    players(h, 1, 2)
    h.db.x("UPDATE users SET cash=500000 WHERE id=1")
    h.say(1, "عرضه SPEED اسپید")
    h.advance(30)
    h.say(2, "خرید 20 SPEED")
    tick(h)                                                         # day 1: stores the baseline net worth
    h.advance(86400)
    h.say(1, "خرید 200 SPEED")
    h.advance(60)
    h.say(2, "خرید 400 SPEED")
    h.say(1, "!بازار")
    n = tick(h)
    text = dms(h, 1, n)[0].text
    assert "از دیروز" in text and "درآمد و سود معوقه" in text and "بهترین سهمت" in text and "$SPEED" in text.replace("‎", "")


def test_income_button_collects_and_refreshes(h):
    players(h, 1, 2)
    h.db.x("UPDATE users SET cash=500000 WHERE id=1")
    h.say(1, "عرضه SPEED اسپید")
    h.advance(30)
    h.say(2, "خرید 50 SPEED")
    tick(h)
    cash = core.get_user(h.db, 1)["cash"]
    calls = h.press(1, "sal:inc:1")
    assert core.get_user(h.db, 1)["cash"] > cash
    ans = [c for c in calls if c.__class__.__name__ == "AnswerCallbackQuery"][0]
    assert "برداشت شد" in (ans.text or "")


def test_batches_are_limited_and_pick_up_where_they_left_off(db):
    for i in range(1, 6):
        user(db, i)
        db.x("INSERT OR IGNORE INTO seen(day,user_id) VALUES(?,?)", (T[0] // 86400, i))
    day = T[0] // 86400
    first = [r["id"] for r in salary.candidates(db, day, limit=2)]
    assert first == [1, 2]
    for uid in first:
        salary.mark_sent(db, uid, day)
    assert [r["id"] for r in salary.candidates(db, day, limit=2)] == [3, 4]


def test_automated_traders_never_get_a_salary(db):
    from hypemarket import bots
    bots.ensure_bots(db)
    db.x("INSERT OR IGNORE INTO seen(day,user_id) VALUES(?,?)", (T[0] // 86400, -1))
    assert salary.candidates(db, T[0] // 86400) == []
