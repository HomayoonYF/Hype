"""The Rofaqa Exchange through the real handlers (fake Telegram): commands, buttons, group posts, earnings night, admin numbers."""
import random

import pytest
from aiogram.methods import EditMessageText, SendMessage, SendPhoto

from hypemarket import config as C, core, docs, exchange as X, jobs, social, textparse, trading, views
from hypemarket.texts import ALIASES, EN, FA

import test_bot as tb

G = -100808


@pytest.fixture()
def h():
    h = tb.Harness()
    X.ensure_listed(h.db, random.Random(5))
    return h


def buttons(call):
    return [b.callback_data for row in call.reply_markup.inline_keyboard for b in row]


def rich(h, uid, coins=300_000, chat=None):
    h.say(uid, "!شروع" if chat else "شروع", chat=chat)
    h.db.x("UPDATE users SET cash=? WHERE id=?", (C.coins(coins), uid))
    core.mint(h.db, C.coins(coins))


def sid(h, key):
    return X.stock_id(h.db, key)


def test_the_exchange_screen_lists_all_six_companies_with_buttons(h):
    rich(h, 2)
    calls = h.say(2, "بورس")
    text = h.texts(calls)
    for co in X.COMPANIES:
        assert co.ticker in text
    cb = buttons(calls[-1])
    assert {f"st:{sid(h, co.key)}" for co in X.COMPANIES} <= set(cb) and "ex:e" in cb and "ex:r" in cb
    assert all(len(b.encode()) <= 64 for b in cb)
    assert h.texts(h.say(2, "exchange")) and h.texts(h.say(2, "/exchange"))


def test_buy_and_sell_listed_shares_with_commands_and_the_price_never_moves(h):
    rich(h, 2)
    s = core.stock_row(h.db, sid(h, "chat"))
    p = s["p0"]
    out = h.texts(h.say(2, "خرید 40 CHAT"))
    assert "خرید انجام شد" in out
    assert core.stock_row(h.db, s["id"])["p0"] == p
    assert h.db.q1("SELECT shares FROM holdings WHERE user_id=2 AND stock_id=?", (s["id"],))["shares"] == 40
    h.advance(60)
    assert "فروش انجام شد" in h.texts(h.say(2, "فروش همه CHAT"))
    assert core.stock_row(h.db, s["id"])["p0"] == p
    assert core.get_user(h.db, 2)["cash"] < C.coins(300_000) + 20_000_000                          # (rewards aside, the levy was paid)
    out = h.texts(h.say(2, "price CHAT")) + h.texts(h.say(2, "قیمت LEVI"))
    assert "CHAT" in out and "LEVI" in out


def test_the_card_buttons_work_and_stay_in_the_exchange(h):
    rich(h, 2)
    s = sid(h, "levi")
    calls = h.press(2, f"st:{s}")
    assert any(isinstance(c, EditMessageText) for c in calls)
    text = h.texts(calls)
    assert "LEVI" in text and "لغزش" in text
    cb = buttons([c for c in calls if isinstance(c, EditMessageText)][-1])
    assert f"b:{s}:1" in cb and f"s:{s}:0" in cb and f"ch:{s}:24h" in cb and "ex:r" in cb          # a way back to the exchange
    h.press(2, f"b:{s}:5")
    assert h.db.q1("SELECT shares FROM holdings WHERE user_id=2 AND stock_id=?", (s,))["shares"] == 5
    h.advance(60)
    h.press(2, f"s:{s}:0")
    assert h.db.q1("SELECT COUNT(*) c FROM holdings WHERE user_id=2")["c"] == 0
    assert h.texts(h.press(2, "ex:r")) and h.texts(h.press(2, "ex:e"))


def test_the_chart_of_a_listed_company_is_sent_as_a_photo(h):
    rich(h, 2)
    h.say(2, "نمودار CHAT")
    photo = h.session.sent(SendPhoto)[-1]
    assert photo.caption and len(photo.caption) <= 1024 and "CHAT" in photo.caption
    h.say(2, "chart MEME 7d")
    assert len(h.session.sent(SendPhoto)) == 2


def test_group_chat_commands_work_unprefixed_for_exchange_words_only(h):
    rich(h, 3, chat=G)
    assert h.say(3, "بورس", chat=G) != []
    assert h.say(3, "گزارش مالی", chat=G) != []
    assert "خرید انجام شد" in h.texts(h.say(3, "خرید 2 CHAT", chat=G))
    assert h.say(3, "بورس امروز خوبه", chat=G) == []                                                 # ordinary chat is ignored


def test_before_the_first_earnings_night_there_is_a_friendly_answer(h):
    rich(h, 2)
    out = h.texts(h.say(2, "گزارش مالی"))
    assert "هنوز" in out and "شب گزارش" in out


def test_earnings_night_is_posted_to_the_groups_with_a_chart(h):
    rich(h, 3, chat=G)
    social.set_news_mode(h.db, G, "quiet")
    rich(h, 4, chat=-100909)
    social.set_news_mode(h.db, -100909, "off")
    for _ in range(40):
        h.advance(60)
        core.counter(h.db, "chat", 30)
        X.tick(h.db, random.Random(1))
    h.advance(X.sched_ts(tb.T[0]) + 1800 - tb.T[0])
    photos = len(h.session.sent(SendPhoto))
    h.run(jobs.exchange_tick(h.app))
    posted = h.session.sent(SendPhoto)[photos:]
    assert len(posted) == 1 and posted[0].chat_id == G                                               # not to the group with news switched off
    assert len(posted[0].caption) <= 1024 and "گزارش" in posted[0].caption
    assert not X.earnings_due(h.db)
    h.run(jobs.exchange_tick(h.app))
    assert len(h.session.sent(SendPhoto)) == photos + 1                                              # once per week
    out = h.texts(h.say(3, "!گزارش مالی", chat=G))
    assert "CHAT" in out and "RBNK" in out and "TRST" in out
    assert "گزارش مالی" in h.texts(h.say(3, "!اخبار", chat=G))                                         # the report is also in the news


def test_boards_show_the_live_exchange_block(h):
    rich(h, 3, chat=G)
    social.set_news_mode(h.db, G, "quiet")
    h.run(jobs.slow_tick(h.app))
    first = [c for c in h.session.sent(SendMessage) if c.chat_id == G][-1]
    assert "CHAT" in first.text and "LEVI" in first.text
    n_edits = len(h.session.sent(EditMessageText))
    h.advance(120)
    X.tick(h.db, random.Random(2))
    h.run(jobs.slow_tick(h.app))
    assert len(h.session.sent(EditMessageText)) == n_edits + 1                                       # prices moved: the board is edited in place


def test_admin_dashboard_shows_the_exchange_numbers(h):
    rich(h, 2)
    h.say(2, "خرید 30 CHAT")
    out = h.texts(h.say(1, "/stats"))
    assert "بورس" in out and "کارمزد" in out
    assert "Exchange" in views.dashboard_view(h.db, "en").text


def test_short_selling_is_gone_from_every_menu_and_text(h):
    assert "short" not in ALIASES and "short" not in docs.DOCS and "short" not in C.PERKS
    for words in (["شورت"], ["شورت", "بستن شورت"]):
        for w in words:
            assert textparse.parse(w + " SPEED 5", None) is None or textparse.parse(w + " SPEED 5", None).cmd not in ("short", "cover")
    assert not any("short" in k.lower() and not k.startswith("n.short_removed") for k in FA)
    assert not any("short" in k.lower() and not k.startswith("n.short_removed") for k in EN)
    rich(h, 2)
    assert h.say(2, "شورت CHAT 5") == []


def test_the_migration_notice_reaches_the_affected_player_once(h):
    rich(h, 2)
    h.db.x("INSERT INTO kv(key,value) VALUES('mig5:2','750,3')")
    h.run(jobs.fast_tick(h.app))
    dms = [c for c in h.session.sent(SendMessage) if c.chat_id == 2]
    assert any("شورت" in (c.text or "") and "بورس" in (c.text or "") for c in dms)
    n = len(h.session.sent(SendMessage))
    h.run(jobs.fast_tick(h.app))
    assert not [c for c in h.session.sent(SendMessage)[n:] if c.chat_id == 2 and "شورت" in (c.text or "")]


def test_help_and_docs_cover_the_exchange(h):
    for cmd in ("exchange", "earnings"):
        assert cmd in docs.DOCS and cmd in ALIASES
    page = h.texts(h.press(1, f"hp:cat:{[c for c, _ in docs.CATEGORIES].index('trade')}:1"))
    assert "بورس" in page and "گزارش مالی" in page
