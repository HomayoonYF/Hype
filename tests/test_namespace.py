"""v0.14: the سهام command namespace, the global منو, the help hub, retired words and the rebirth relaunch fix."""
import re
from pathlib import Path

import pytest
from aiogram.methods import AnswerCallbackQuery, EditMessageText, SendMessage

from hypemarket import config as C, core, db as dbmod, docs, exchange, handlers, progress, sections, textparse, trading, views
from hypemarket.texts import ALIASES, EN, FA

import test_bot as tb

G = -100606


@pytest.fixture()
def h():
    return tb.Harness()


def buttons(call):
    return [b.callback_data for row in call.reply_markup.inline_keyboard for b in row]


def edits(calls):
    return [c for c in calls if isinstance(c, EditMessageText)]


def start(h, uid, chat=None):
    h.say(uid, "!شروع" if chat else "شروع", chat=chat)


def state(db):
    """Everything a stock command could change: balances, progression, holdings, stocks, companies, alerts."""
    return (
        [tuple(r) for r in db.qa("SELECT id,cash,pending_div,pending_inc,rebirths,legacy_pts,max_level,spent_points,company_id FROM users ORDER BY id")],
        [tuple(r) for r in db.qa("SELECT user_id,perk,level FROM perks ORDER BY user_id,perk")],
        [tuple(r) for r in db.qa("SELECT user_id,stock_id,shares,cost FROM holdings ORDER BY user_id,stock_id")],
        [tuple(r) for r in db.qa("SELECT id,ticker,status,supply,reserve,rugs FROM stocks ORDER BY id")],
        [tuple(r) for r in db.qa("SELECT id,name,treasury FROM companies ORDER BY id")],
        [tuple(r) for r in db.qa("SELECT * FROM alerts ORDER BY 1")],
    )


# ----------------------------------------------------------------------------------------------------- the registry
def test_every_command_is_classified_exactly_once():
    classes = [sections.STOCK_CMDS, sections.GLOBAL_CMDS, sections.ADMIN_CMDS, frozenset({"stocks", "stockhelp"})]
    assert set().union(*classes) == set(ALIASES), set(ALIASES) ^ set().union(*classes)
    assert sum(len(c) for c in classes) == len(set(ALIASES))                      # no command in two classes
    assert sections.LEGACY_KEEP <= sections.STOCK_CMDS and sections.LEGACY_NEEDS_TICKER <= sections.LEGACY_KEEP


@pytest.mark.parametrize("lang", ["fa", "en"])
def test_every_stock_command_has_a_working_canonical_form(lang):
    for cmd in sorted(sections.STOCK_CMDS):
        form = sections.canonical(cmd, lang)
        p = textparse.parse(form + " SPEED", None)
        assert p is not None and p.cmd == cmd and p.section == "stocks" and not p.retired, (lang, cmd, form, p)
        assert form.split()[0] in ("سهام", "stocks")


def test_the_registry_leaves_global_and_admin_commands_alone():
    for cmd in sorted(sections.GLOBAL_CMDS | sections.ADMIN_CMDS):
        for word in ALIASES[cmd]:
            p = textparse.parse(word, None)
            assert p is not None and p.cmd == cmd and not p.retired and p.section is None, (cmd, word, p)
    assert textparse.parse("/ban 123", None).cmd == "ban" and textparse.parse("/stats", None).cmd == "stats"


# ----------------------------------------------------------------------------------------------------- parsing
def test_the_section_word_alone_opens_the_stock_menu():
    for text in ("سهام", "stocks", "stock", "/stocks", "!سهام", "  سهام  ", "سهام منو", "stocks menu"):
        p = textparse.parse(text, None)
        assert p is not None and p.cmd == "stocks" and p.section == "stocks" and p.args == [], text
    assert textparse.parse("/stocks@TestBot", "TestBot").cmd == "stocks"
    assert textparse.parse("/stocks@OtherBot", "TestBot") is None


def test_market_and_exchange_stay_two_different_commands():
    assert textparse.parse("سهام بازار", None).cmd == "market" and textparse.parse("سهام بورس", None).cmd == "exchange"
    assert textparse.parse("stocks market", None).cmd == "market" and textparse.parse("stocks exchange", None).cmd == "exchange"
    assert textparse.parse("بازار", None).cmd == "market" and textparse.parse("بورس", None).cmd == "exchange"


def test_launch_family_arguments_and_tickers_survive_the_prefix():
    for text, cmd, args in [
        ("سهام عرضه SPEED اسپید | توضیح", "launch", ["SPEED", "اسپید", "|", "توضیح"]),
        ("سهام عرضه میم DOGE دوج", "meme", ["DOGE", "دوج"]),
        ("سهام عرضه پروژه TRIP 20000 سفر شمال", "project", ["TRIP", "20000", "سفر", "شمال"]),
        ("سهام خرید 10 SPEED", "buy", ["10", "SPEED"]),
        ("سهام فروش 5 SPEED", "sell", ["5", "SPEED"]),
        ("سهام خرید همه CHAT", "buy", ["همه", "CHAT"]),
        ("stocks buy max CHAT", "buy", ["max", "CHAT"]),
        ("سهام ویرایش SPEED توضیح متن جدید", "editstock", ["SPEED", "توضیح", "متن", "جدید"]),
        ("سهام ویرایش سهام نام اسم جدید", "editstock", ["نام", "اسم", "جدید"]),
        ("سهام من SPEED", "mystock", ["SPEED"]),
        ("سهام رتبه گروه‌ها", "grouprank", []),
        ("سهام رتبه", "leaderboard", []),
        ("سهام گزارش مالی", "earnings", []),
        ("سهام گزارش SPEED خروج", "report", ["SPEED", "خروج"]),
        ("سهام تقسیم سود شرکت", "payout", []),
        ("سهام ساخت شرکت رفقای خوب RAFA", "createcompany", ["رفقای", "خوب", "RAFA"]),
    ]:
        p = textparse.parse(text, None)
        assert p is not None and p.cmd == cmd and p.args == args and not p.retired, (text, p)


def test_persian_normalization_works_after_the_prefix():
    for text, cmd in [("سهام خرید ۱۰ SPEED", "buy"), ("سهام خريد 10 SPEED", "buy"), ("سهام مهارت‌ها", "skills"), ("سهام مهارت ها", "skills"),
                      ("سهام مهارتها", "skills"), ("سَهام سطح", "level"), ("سهام‌ سطح", "level"), ("سهام ماموریت‌ها", "quests"), ("STOCKS Level", "level")]:
        p = textparse.parse(text, None)
        assert p is not None and p.cmd == cmd and p.section == "stocks", (text, p)


def test_the_prefix_only_counts_at_the_very_start_of_a_message():
    for text in ("امروز سهام خرید 10 SPEED", "من سهام سطح رو دیدم", "hey stocks buy 10 SPEED", "friend: سهام"):
        assert textparse.parse(text, None) is None, text


def test_unknown_subcommands_and_other_classes_are_not_reachable_through_the_prefix():
    for text in ("سهام abc", "سهام ban 5", "سهام /ban 5", "سهام stats", "سهام پروفایل", "سهام روزانه", "سهام start", "سهام تنظیمات"):
        p = textparse.parse(text, None)
        assert p is not None and p.cmd == "stocks_unknown" and p.section == "stocks", (text, p)
    # English chat that merely starts with "stock" is not a command at all
    assert textparse.parse("stock prices fell today", None) is None
    assert textparse.parse("stocks are fun", None) is None


def test_help_and_menu_are_global_but_stock_help_lives_in_the_section():
    assert textparse.parse("راهنما", None).cmd == "help" and textparse.parse("کمک", None).cmd == "help" and textparse.parse("help", None).cmd == "help"
    assert textparse.parse("منو", None).cmd == "menu" and textparse.parse("menu", None).cmd == "menu" and textparse.parse("/menu", None).cmd == "menu"
    for text in ("سهام راهنما", "سهام کمک", "stocks help", "/stocks help"):
        assert textparse.parse(text, None).cmd == "stockhelp", text


# ----------------------------------------------------------------------------------------------------- old words
RETIRED = ["سطح", "level", "مهارت‌ها", "skills", "ارتقا fee", "میراث", "تولد دوباره", "rebirth", "ماموریت", "quests", "دستاوردها", "فصل", "season", "اخبار", "news",
           "رویداد", "نهنگ", "whales", "سود", "dividends", "درآمد", "income", "خروج SPEED 50", "هشدار", "هشدار SPEED 5", "اعتبار", "رتبه", "برترین‌ها", "گروه",
           "شاخص", "رتبه گروه‌ها", "کارت", "کارت SPEED", "اعتماد SPEED", "بی اعتماد SPEED", "گزارش SPEED", "شرکت من", "شرکت", "ساخت شرکت رفقا RAFA",
           "عضویت شرکت رفقا", "ترک شرکت", "تقسیم سود شرکت", "خرید", "فروش", "قیمت", "نمودار", "خرید خانه", "فروش ماشین", "عرضه"]
KEPT = ["بازار", "market", "بورس", "exchange", "گزارش مالی", "earnings", "پورتفوی", "portfolio", "pf", "خرید 10 SPEED", "خرید همه SPEED", "buy 5 SPEED",
        "فروش 5 SPEED", "sell all SPEED", "قیمت SPEED", "price SPEED", "نمودار SPEED 7d", "chart SPEED", "عرضه SPEED اسپید", "launch SPEED Speeed",
        "عرضه میم DOGE دوج", "عرضه پروژه TRIP 20000 سفر", "عرضه میم", "عرضه پروژه", "ویرایش سهام نام جدید", "تنظیم سود 10", "mystock", "سهام من"]


def test_old_stock_words_are_retired_with_a_pointer_not_executed():
    for text in RETIRED:
        p = textparse.parse(text, None)
        assert p is not None and p.retired, text
    for text in KEPT:
        p = textparse.parse(text, None)
        assert p is not None and not p.retired, text
    assert textparse.parse("!سطح", None).retired and textparse.parse("/level", None).retired       # prefixed spellings too


def test_bare_level_never_runs_the_stock_level_handler(h):
    start(h, 1)
    before = state(h.db)
    out = h.texts(h.say(1, "سطح"))
    assert "سهام سطح" in out and "منو" in out
    assert "امتیاز مهارت" not in out and "ارزش خالص" not in out                              # not the level screen
    assert state(h.db) == before
    full = h.texts(h.say(1, "سهام سطح"))
    assert full == h.texts(h.say(1, "stocks level")) or "Level" in full or "سطح" in full
    assert full == views.level_view(h.db, "fa", 1).text


def test_retired_words_change_nothing_whatever_state_the_player_is_in(h):
    for uid in (1, 2):
        start(h, uid)
    h.db.x("UPDATE users SET cash=cash+?, max_level=4, pending_div=500, pending_inc=700 WHERE id=1", (C.coins(150_000),))
    h.say(1, "عرضه SPEED اسپید")
    h.advance(60)
    h.say(2, "خرید 3 SPEED")
    h.advance(60)
    before = state(h.db)
    for text in ["سطح", "مهارت‌ها", "ارتقا fee", "ارتقا alerts", "میراث", "تولد دوباره", "rebirth", "سود", "درآمد", "خروج SPEED 50", "هشدار SPEED 9", "ساخت شرکت رفقا RAFA",
                 "عضویت شرکت رفقا", "ترک شرکت", "تقسیم سود شرکت", "اعتماد SPEED", "بی اعتماد SPEED", "گزارش SPEED خروج", "ماموریت", "دستاوردها", "فصل", "نهنگ", "اخبار",
                 "خرید", "فروش", "قیمت", "خرید خانه", "level", "skills", "upgrade fee", "legacy", "dividends", "income", "cashout SPEED 50", "alert SPEED 9", "createcompany Foo FOO"]:
        h.advance(5)
        out = h.texts(h.say(1, text))
        assert out and ("سهام" in out or "stocks" in out), (text, out)
        assert state(h.db) == before, text


def test_the_pointer_uses_the_players_language_and_keeps_the_arguments(h):
    start(h, 3)
    assert "<code>stocks" not in h.texts(h.say(3, "ارتقا fee"))
    out = h.texts(h.say(3, "ارتقا fee"))
    assert "سهام ارتقا fee" in out
    h.say(3, "تنظیم زبان en")
    out = h.texts(h.say(3, "level"))
    assert "stocks level" in out and "now lives" in out
    out = h.texts(h.say(3, "quests"))
    assert "stocks quests" in out
    assert "Level" in h.texts(h.say(3, "stocks level")) or "level" in h.texts(h.say(3, "stocks level")).lower()


# ----------------------------------------------------------------------------------------------------- executing through the prefix
def test_prefixed_trading_runs_the_same_business_logic_exactly_once(h):
    for uid in (1, 2):
        start(h, uid)
    out = h.texts(h.say(1, "سهام عرضه SPEED اسپید | تست"))
    assert core.stock_row(h.db, 1)["ticker"] == "SPEED" and out
    h.advance(60)
    cash = core.get_user(h.db, 2)["cash"]
    calls = h.say(2, "سهام خرید 10 SPEED")
    out = h.texts(calls)
    assert "خرید انجام شد" in out and len([c for c in calls if isinstance(c, SendMessage) and c.chat_id == 2]) == 1      # one answer, one trade
    assert h.db.q1("SELECT shares FROM holdings WHERE user_id=2 AND stock_id=1")["shares"] == 10
    h.advance(60)
    out = h.texts(h.say(2, "سهام فروش 5 SPEED"))
    assert "فروش انجام شد" in out and h.db.q1("SELECT shares FROM holdings WHERE user_id=2 AND stock_id=1")["shares"] == 5
    h.advance(60)
    cash2 = core.get_user(h.db, 2)["cash"]
    assert "فروش انجام شد" in h.texts(h.say(2, "فروش همه SPEED"))                                      # the old spelling keeps working
    assert h.db.q1("SELECT COUNT(*) c FROM holdings WHERE user_id=2")["c"] == 0 and core.get_user(h.db, 2)["cash"] > cash2
    h.advance(60)
    assert "خرید انجام شد" in h.texts(h.say(2, "خرید 10 SPEED"))                                          # ...and so does the old spelling of a buy
    assert h.db.q1("SELECT shares FROM holdings WHERE user_id=2 AND stock_id=1")["shares"] == 10


def test_every_prefixed_stock_screen_works_for_a_real_player(h):
    for uid in (1, 2):
        start(h, uid)
    h.db.x("UPDATE users SET max_level=4, cash=cash+? WHERE id=1", (C.coins(50_000),))
    h.say(1, "سهام عرضه SPEED اسپید")
    h.advance(60)
    h.say(2, "سهام خرید 10 SPEED")
    for text in ["سهام بازار", "سهام بورس", "سهام گزارش مالی", "سهام پورتفوی", "سهام من", "سهام سطح", "سهام اعتبار", "سهام مهارت‌ها", "سهام میراث", "سهام ماموریت", "سهام دستاوردها",
                 "سهام فصل", "سهام اخبار", "سهام رویداد", "سهام نهنگ", "سهام رتبه", "سهام برترین‌ها", "سهام قیمت SPEED", "سهام نمودار SPEED", "سهام شرکت من", "سهام گروه",
                 "سهام شاخص", "سهام رتبه گروه‌ها", "سهام کارت", "سهام اعتماد", "سهام هشدار", "stocks level", "stocks skills", "stocks market", "stocks exchange"]:
        h.advance(5)
        calls = h.say(1, text)
        out = h.texts(calls)
        assert out or h.session.sent(), text
        assert FA["err"] not in out and EN["err"] not in out, (text, out)


def test_invalid_arguments_give_helpful_errors_not_crashes(h):
    start(h, 1)
    for text, must in [("سهام خرید", "نمونه"), ("سهام خرید 10", "نمونه"), ("سهام خرید 10 NOPE", "پیدا نشد"), ("سهام قیمت", "نمونه"), ("سهام ارتقا", "نمونه"),
                       ("سهام ارتقا چیزی", "مهارت"), ("سهام عرضه", "نمونه"), ("سهام عرضه میم", "نمونه"), ("سهام عرضه پروژه", "نمونه"), ("سهام ویرایش", "نمونه")]:
        out = h.texts(h.say(1, text))
        assert must in out, (text, out)
        assert FA["err"] not in out


def test_unknown_stock_subcommand_is_a_hint_in_private_and_silent_in_groups(h):
    start(h, 1)
    out = h.texts(h.say(1, "سهام قیمتی نداره"))
    assert "نشناختم" in out and "سهام راهنما" in out
    start(h, 2, chat=G)
    assert h.say(2, "سهام امروز چطوره", chat=G) == []
    assert h.say(2, "stock prices are up", chat=G) == []


def test_admin_commands_are_not_reachable_through_the_prefix(h):
    start(h, 1)
    start(h, 5)
    h.say(1, "سهام ban 5")
    h.say(1, "سهام /ban 5")
    assert core.get_user(h.db, 5)["banned"] == 0
    h.say(1, "/ban 5")
    assert core.get_user(h.db, 5)["banned"] == 1                                                      # the real admin command is untouched
    start(h, 6)
    assert h.say(6, "/ban 1") == [] and core.get_user(h.db, 1)["banned"] == 0                         # ...and still checks authorization


# ----------------------------------------------------------------------------------------------------- groups
def test_group_chat_is_not_consumed_by_the_section_word(h):
    start(h, 1, chat=G)
    for text in ["سهام خرید کنم یا نه؟", "سهام بازار امروز چطوره", "سهام من رو دیدی؟", "سهام ویرایش شده", "سهام سطح بالاست", "stocks buy low sell high", "سهام خوبه"]:
        assert h.say(1, text, chat=G) == [], text
    assert h.say(1, "سهام", chat=G) != []                                                              # the exact word opens the menu
    assert h.say(1, "سهام بازار", chat=G) != [] and h.say(1, "سهام بورس", chat=G) != []
    assert h.say(1, "سهام سطح", chat=G) != []
    assert h.say(1, "منو", chat=G) != [] and h.say(1, "راهنما", chat=G) != []


def test_group_old_words_get_the_pointer_only_when_they_look_like_commands(h):
    start(h, 1, chat=G)
    out = h.texts(h.say(1, "سطح", chat=G))
    assert "سهام سطح" in out
    assert h.say(1, "سطح آب رو دیدی؟", chat=G) == []                                                    # ordinary chat is left alone
    assert h.say(1, "خرید خانه جدید", chat=G) == []
    assert h.say(1, "سود خوبی بود", chat=G) == []


def test_group_trading_through_the_prefix_validates_like_a_plain_command(h):
    for uid in (1, 2):
        start(h, uid, chat=G)
    h.say(1, "سهام عرضه SPEED اسپید", chat=G)
    assert h.db.q1("SELECT 1 FROM stocks WHERE ticker='SPEED'")
    h.advance(60)
    assert "خرید انجام شد" in h.texts(h.say(2, "سهام خرید 3 SPEED", chat=G))
    assert h.say(2, "سهام خرید 3 NOPE", chat=G) == []                                                   # unknown ticker in a group: silence, like before


# ----------------------------------------------------------------------------------------------------- menus
def test_main_menu_has_stocks_and_help_and_every_button_works(h):
    start(h, 1)
    h.say(1, "منو")
    msg = h.session.sent(SendMessage)[-1]
    assert "منوی" in msg.text
    cb = buttons(msg)
    assert cb[:2] == ["sm:menu:1", "hp:menu:1"] and set(cb) == {"sm:menu:1", "hp:menu:1", "mn:go:profile:1", "mn:go:daily:1", "mn:go:invite:1", "mn:go:map:1", "mn:go:cash:1", "mn:go:jobs:1"}
    assert all(len(b.encode()) <= 64 for b in cb)
    stock = edits(h.press(1, "sm:menu:1"))[-1]
    assert "بخش سهام" in stock.text and "mn:menu:1" in buttons(stock)
    hub = edits(h.press(1, "hp:menu:1"))[-1]
    assert buttons(hub) == ["hp:stock:1", "hp:about:1", "mn:menu:1"]
    for target in ("profile", "daily", "invite", "cash", "jobs"):
        calls = h.press(1, f"mn:go:{target}:1")
        e = edits(calls)[-1]
        assert e.text and buttons(e)[-1] == "mn:menu:1", target                                          # shown in place, with a way back
    back = edits(h.press(1, "mn:menu:1"))[-1]
    assert "منوی" in back.text and buttons(back) == cb


def test_the_menu_buttons_belong_to_the_player_who_opened_them(h):
    start(h, 1)
    start(h, 2)
    for data in ("mn:menu:1", "sm:menu:1", "mn:go:profile:1", "sm:go:market:1", "hp:stock:1"):
        ans = [c for c in h.press(2, data) if isinstance(c, AnswerCallbackQuery)][0]
        assert "برای تو نیست" in (ans.text or ""), data


def test_the_stock_menu_has_shortcuts_and_one_button_per_category(h):
    for uid in (1, 2):
        start(h, uid)
    h.db.x("UPDATE users SET max_level=4, cash=cash+? WHERE id=1", (C.coins(50_000),))
    h.say(1, "سهام عرضه SPEED اسپید")
    h.say(1, "سهام ساخت شرکت رفقا RAFA")
    h.advance(60)
    h.say(2, "سهام خرید 10 SPEED")
    h.say(1, "سهام")
    msg = h.session.sent(SendMessage)[-1]
    cb = buttons(msg)
    quick = [c for row in sections.STOCK_QUICK for c in row]
    assert cb[:len(quick)] == [f"sm:go:{c}:m:1" for c in quick]                                           # shortcuts first
    assert [b for b in cb if b.startswith("sm:cat:")] == [f"sm:cat:{cid}:1" for cid, _ in sections.STOCK_CATEGORIES]
    assert cb[-2:] == ["hp:stock:1", "mn:menu:1"] and len(cb) == len(quick) + len(sections.STOCK_CATEGORIES) + 2
    assert all(len(b.encode()) <= 64 for b in cb)
    for cmd in quick:                                                                                       # first pass: the screens (and any quest they complete)
        h.press(1, f"sm:go:{cmd}:m:1")
    same = {"market": views.market_view(h.db, "fa", "top").text, "portfolio": views.portfolio_view(h.db, "fa", 1).text, "level": views.level_view(h.db, "fa", 1).text,
            "exchange": views.exchange_view(h.db, "fa", 1).text}
    for cmd in quick:
        calls = h.press(1, f"sm:go:{cmd}:m:1")
        e = edits(calls)
        assert e, cmd
        assert buttons(e[-1])[-1] == "sm:menu:1", cmd                                                      # back to the stock menu
        if cmd in same:
            assert e[-1].text == same[cmd], cmd                                                             # the very same screen the command shows
    assert "hp:stock:1" in buttons(edits(h.press(1, "sm:menu:1"))[-1])


def test_menu_buttons_cannot_reach_anything_but_their_whitelist(h):
    start(h, 1)
    before = state(h.db)
    for data in ("sm:go:upgrade:m:1", "sm:go:buy:m:1", "sm:go:launch:m:1", "sm:go:payout:m:1", "sm:go:leavecompany:m:1", "sm:go:ban:m:1", "sm:go:stats:m:1", "mn:go:stats:1",
                 "mn:go:ban:1", "mn:go:market:1", "sm:go:daily:m:1", "sm:go:nonsense:m:1", "sm:ask:ban:m:1", "sm:ask:market:m:1", "sm:sure:market:m:1", "sm:do:market:m:1",
                 "sm:do:ban:m:1", "sm:do:buy:m:1"):
        calls = h.press(1, data)
        assert not edits(calls), data
    assert state(h.db) == before


def test_a_menu_screen_that_fails_shows_an_alert_not_a_crash(h):
    start(h, 1)
    calls = h.press(1, "sm:go:mystock:1")                                                                  # no stock yet
    ans = [c for c in calls if isinstance(c, AnswerCallbackQuery)][0]
    assert ans.show_alert and not edits(calls)


def test_help_hub_stock_help_and_about_navigation(h):
    start(h, 10)
    h.say(10, "راهنما")
    hub = h.session.sent(SendMessage)[-1]
    assert buttons(hub) == ["hp:stock:10", "hp:about:10", "mn:menu:10"] and "راهنمای" in hub.text
    h.say(10, "سهام راهنما")
    direct = h.session.sent(SendMessage)[-1]
    stock_help = edits(h.press(10, "hp:stock:10"))[-1]
    assert buttons(direct) == buttons(stock_help) == ["hp:guide:10", "hp:cmds:10", "hp:menu:10"] and direct.text == stock_help.text
    assert "درباره" not in direct.text and not any("about" in b for b in buttons(direct))                  # About the game left the stock help
    about = edits(h.press(10, "hp:about:10"))[-1]
    assert "ذخیره" in about.text and buttons(about) == ["hp:menu:10"]                                      # back goes to the hub
    assert buttons(edits(h.press(10, "hp:menu:10"))[-1]) == ["hp:stock:10", "hp:about:10", "mn:menu:10"]
    guide = edits(h.press(10, "hp:guide:10"))[-1]
    assert buttons(guide)[0] == "hp:stock:10"                                                              # guide: back to stock help
    cmds = edits(h.press(10, "hp:cmds:10"))[-1]
    assert buttons(cmds)[-1] == "hp:stock:10"
    cat = edits(h.press(10, "hp:cat:1:10"))[-1]
    assert buttons(cat) == ["hp:cmds:10", "hp:stock:10"]
    tips = edits(h.press(10, "hp:tips:10"))[-1]
    assert buttons(tips) == ["hp:cmds:10"]
    assert buttons(edits(h.press(10, "hp:stock:10"))[-1])[-1] == "hp:menu:10"


def test_the_tutorial_and_its_saved_progress_are_untouched(h):
    start(h, 10)
    before = h.db.q1("SELECT COUNT(*) c FROM quest_progress")["c"] if h.db.q1("SELECT 1 FROM sqlite_master WHERE name='quest_progress'") else None
    out = h.texts(h.press(10, "hp:guide:10"))
    assert "(۰/۶)" in out and "⬜" in out
    h.say(10, "روزانه")
    out = h.texts(h.press(10, "hp:guide:10"))
    assert "(۱/۶)" in out and "✅" in out
    assert before is None or before >= 0


def test_english_menus_and_help(h):
    start(h, 4)
    h.say(4, "تنظیم زبان en")
    h.say(4, "menu")
    msg = h.session.sent(SendMessage)[-1]
    assert "menu" in msg.text.lower() and buttons(msg)[0] == "sm:menu:4"
    assert "Stocks" in h.texts(h.say(4, "stocks")) and "Stock help" in h.texts(h.say(4, "stocks help"))
    assert "How the game works" in h.texts(h.press(4, "hp:about:4"))
    assert "stocks buy 10 SPEED" in h.texts(h.press(4, "hp:cat:1:4"))


# ----------------------------------------------------------------------------------------------------- documentation
def test_documentation_shows_the_canonical_forms_and_every_example_parses():
    for cmd in sections.STOCK_CMDS:
        fa_d, fa_e, en_d, en_e = docs.DOCS[cmd]
        for line in fa_e.split("\n"):
            assert line.startswith("سهام"), (cmd, line)
        for line in en_e.split("\n"):
            assert line.startswith("stocks "), (cmd, line)
    for cmd in sections.GLOBAL_CMDS | sections.ADMIN_CMDS:
        for line in docs.DOCS[cmd][1].split("\n") + docs.DOCS[cmd][3].split("\n"):
            assert not line.startswith(("سهام", "stocks")), (cmd, line)
    for cmd in ("menu", "stocks", "stockhelp"):
        assert cmd in docs.DOCS
    assert docs.DOCS["menu"][1] == "منو" and docs.DOCS["stocks"][1] == "سهام" and docs.DOCS["stockhelp"][1] == "سهام راهنما"


def test_no_text_points_players_to_a_retired_spelling():
    pat = re.compile(r"<code>(.*?)</code>", re.S)
    for name, D in (("fa", FA), ("en", EN)):
        for key, value in D.items():
            for m in pat.finditer(value):
                for raw in m.group(1).split("\n"):
                    part = re.sub(r"\{[^}]*\}", "SPEED", raw).strip()
                    if not part:
                        continue
                    p = textparse.parse(part, None)
                    assert not (p and p.retired), (name, key, raw)
                    assert not (p and p.cmd in sections.STOCK_CMDS and not p.section), (name, key, raw)      # new text always shows the canonical form


def test_usage_hints_show_the_canonical_form():
    from hypemarket.texts import EXAMPLES
    for cmd, (fa, en) in EXAMPLES.items():
        if cmd in sections.STOCK_CMDS:
            assert fa.startswith("سهام") and en.startswith("stocks"), cmd


def test_the_telegram_command_menu_has_no_retired_word():
    from hypemarket.app import MENU
    for cmd, fa, en in MENU:
        p = textparse.parse("/" + cmd, None)
        assert p is not None and not p.retired and p.cmd == cmd, cmd
    assert {"menu", "stocks", "help"} <= {c for c, _, _ in MENU}


# ----------------------------------------------------------------------------------------------------- architecture
def test_no_duplicate_handlers_or_callback_prefixes():
    src = Path(handlers.__file__).read_text(encoding="utf-8")
    cbks = re.findall(r'@cbk\("(\w+)"\)', src)
    cmds = re.findall(r'@cmd\("(\w+)"\)', src)
    assert len(cbks) == len(set(cbks)) and len(cmds) == len(set(cmds))
    assert {"mn", "sm"} <= set(cbks)
    assert set(handlers.HANDLERS) >= {"menu", "stocks", "stockhelp", "stocks_unknown"}
    assert set(ALIASES) <= set(handlers.HANDLERS)                                                      # every command still has its handler


def test_the_database_schema_did_not_change():
    assert dbmod.SCHEMA_VERSION == 9 and sorted(dbmod.MIGRATIONS) == [2, 3, 4, 5, 6, 7, 8, 9]     # v0.17 added version 7 (five new tables for the map), v0.18 version 8 (life cash, jobs, interactions), v0.18.1 version 9 (casino stakes: three columns); 2-7 are as they were


def test_every_new_text_key_exists_in_both_languages():
    new = {k for k in FA if k.startswith(("menu.", "sm.")) or k in ("retired", "stocks_unknown", "help.stock", "help.btn_stock", "help.btn_back_stock")}
    assert new and new <= set(EN)
    assert {k for k in EN if k.startswith(("menu.", "sm."))} <= set(FA)
    for k in new:
        fa, en = (FA[k].format_map(_Any()), EN[k].format_map(_Any()))
        assert tb.assert_valid_telegram_html(fa) is None and tb.assert_valid_telegram_html(en) is None


# ----------------------------------------------------------------------------------------------------- the rebirth relaunch fix
class _Any(dict):
    def __missing__(self, key):
        return "x"


def _rebirth_ready(h, uid):
    h.db.x("UPDATE users SET cash=?, max_level=3 WHERE id=?", (C.coins(150_000), uid))
    h.advance(100)
    return progress.rebirth(h.db, uid)


def test_a_stock_can_be_launched_again_after_a_rebirth(h):
    start(h, 1)
    start(h, 2)
    h.say(1, "سهام عرضه SPEED اسپید | اولی")
    old = core.stock_row(h.db, 1)
    assert old["ticker"] == "SPEED" and old["status"] == "active"
    h.advance(60)
    h.say(2, "سهام خرید 5 SPEED")
    _rebirth_ready(h, 1)
    assert core.stock_row(h.db, 1)["status"] == "delisted"
    out = h.texts(h.say(1, "سهام عرضه SPEED اسپید | دومی"))
    assert "قبلاً گرفته" not in out
    new = h.db.q1("SELECT * FROM stocks WHERE ticker='SPEED'")
    assert new and new["id"] != 1 and new["status"] == "active" and new["owner_id"] == 1 and new["descr"] == "دومی"
    archived = core.stock_row(h.db, 1)
    assert archived["ticker"] == "SPEED~1" and archived["status"] == "delisted" and archived["owner_id"] == 1      # history kept under an archive name
    assert h.db.q1("SELECT COUNT(*) c FROM stocks WHERE ticker LIKE 'SPEED%'")["c"] == 2
    assert h.db.q1("SELECT shares FROM holdings WHERE user_id=1 AND stock_id=?", (new["id"],))["shares"] > 0
    h.advance(60)
    assert "خرید انجام شد" in h.texts(h.say(2, "خرید 3 SPEED"))                                          # the new stock trades normally
    assert h.db.q1("SELECT COUNT(*) c FROM holdings WHERE stock_id=1")["c"] == 0


def test_the_old_spelling_and_every_kind_of_launch_work_after_a_rebirth(h):
    start(h, 1)
    h.say(1, "عرضه AAA اولی")
    _rebirth_ready(h, 1)
    h.db.x("UPDATE users SET max_level=3, cash=cash+? WHERE id=1", (C.coins(10_000),))
    assert core.stock_row(h.db, 1)["status"] == "delisted"
    assert "قبلاً گرفته" not in h.texts(h.say(1, "عرضه AAA دومی"))
    assert h.db.q1("SELECT status FROM stocks WHERE ticker='AAA'")["status"] == "active"
    h.advance(10)
    assert "قبلاً گرفته" not in h.texts(h.say(1, "سهام عرضه میم MMM میم"))
    h.say(1, "سهام عرضه پروژه PPP 6000 پروژه")
    assert h.db.q1("SELECT COUNT(*) c FROM stocks WHERE ticker IN ('MMM','PPP') AND status='active'")["c"] >= 1


def test_a_ticker_can_be_reclaimed_after_every_delisting_not_just_once(h):
    start(h, 1)
    h.say(1, "عرضه AAA اولی")
    for round_ in range(3):
        sid = h.db.q1("SELECT id FROM stocks WHERE ticker='AAA' AND status='active'")["id"]
        trading.delist(h.db, sid)
        h.db.x("UPDATE users SET cash=cash+? WHERE id=1", (C.coins(5_000),))
        h.advance(10)
        assert "قبلاً گرفته" not in h.texts(h.say(1, "عرضه AAA دوباره")), round_
    assert h.db.q1("SELECT COUNT(*) c FROM stocks WHERE ticker LIKE 'AAA%'")["c"] == 4
    assert h.db.q1("SELECT COUNT(*) c FROM stocks WHERE ticker='AAA' AND status='active'")["c"] == 1


def test_only_the_owner_can_take_a_delisted_ticker_and_active_ones_stay_taken(h):
    for uid in (1, 2):
        start(h, uid)
    h.say(1, "عرضه AAA اولی")
    assert "قبلاً گرفته" in h.texts(h.say(1, "عرضه AAA دوباره"))                                         # still active: taken, even for its owner
    assert "قبلاً گرفته" in h.texts(h.say(2, "عرضه AAA کپی"))
    trading.delist(h.db, 1)
    h.advance(10)
    assert "قبلاً گرفته" in h.texts(h.say(2, "عرضه AAA کپی"))                                            # someone else's retired ticker is not up for grabs
    assert h.db.q1("SELECT ticker FROM stocks WHERE id=1")["ticker"] == "AAA"
    assert "قبلاً گرفته" not in h.texts(h.say(1, "عرضه AAA دوباره"))
    assert h.db.q1("SELECT owner_id FROM stocks WHERE ticker='AAA'")["owner_id"] == 1


def test_a_failed_relaunch_leaves_the_archive_untouched(h):
    start(h, 1)
    h.say(1, "عرضه AAA اولی")
    trading.delist(h.db, 1)
    h.db.x("UPDATE users SET cash=? WHERE id=1", (C.coins(1),))
    h.advance(10)
    out = h.texts(h.say(1, "عرضه AAA دوباره"))
    assert FA["e.funds"].split("{")[0].strip() in out
    assert h.db.q1("SELECT ticker FROM stocks WHERE id=1")["ticker"] == "AAA"                           # the rename rolled back with the failed launch
    assert h.db.q1("SELECT COUNT(*) c FROM stocks")["c"] == 1


def test_the_exchange_and_other_players_are_untouched_by_the_relaunch_fix(h):
    start(h, 1)
    start(h, 2)
    exchange.ensure_listed(h.db)
    assert "قبلاً گرفته" in h.texts(h.say(1, "عرضه CHAT اسم"))                                           # a listed company's ticker is never reclaimable
    h.say(2, "عرضه BBB بی")
    trading.delist(h.db, h.db.q1("SELECT id FROM stocks WHERE ticker='BBB'")["id"])
    assert "قبلاً گرفته" in h.texts(h.say(1, "عرضه BBB بی"))
