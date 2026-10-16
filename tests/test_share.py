"""Share cards: pictures, captions, the share button, and the deep link a friend opens."""
import io
import urllib.parse

import pytest
from PIL import Image

from aiogram.methods import SendMessage, SendPhoto

from hypemarket import cards, config as C, core, progress, trading, trust, views
from hypemarket.db import Database

import test_bot as tb
from test_game import T, adv, db, user          # noqa: F401


def world(db, name1="سینا رضایی"):
    core.mint(db, C.coins(300_000))
    core.ensure_user(db, 1, "s", name1)
    db.x("UPDATE users SET cash=?, trades=6, max_level=2 WHERE id=1", (C.coins(100_000),))
    for i in (2, 3, 4):
        user(db, i)
        db.x("UPDATE users SET cash=cash+?, trades=6 WHERE id=?", (C.coins(30_000), i))
    trading.launch(db, 1, "SPEED", "اسپید راکت | 🚀")
    adv(10)
    for uid in (2, 3, 4):
        trading.buy(db, uid, "SPEED", 40)
        adv(5)
    return core.need_stock(db, "SPEED")


def png_size(data):
    img = Image.open(io.BytesIO(data))
    assert img.format == "PNG"
    return img.size


@pytest.mark.parametrize("lang", ["fa", "en"])
def test_all_three_cards_render(db, lang):
    s = world(db)
    link = "https://t.me/HypeMarketBot?start=ref_1"
    for data in (cards.portfolio_card(db, lang, 1, link), cards.rank_card(db, lang, 1, link, chat_id=-5), cards.stock_card(db, lang, s, link)):
        assert png_size(data) == (1080, 1350) and len(data) > 20_000


@pytest.mark.parametrize("name", ["🚀🔥💎", "نام خیلی خیلی خیلی خیلی طولانی که باید کوتاه بشه و از کارت بیرون نزنه چون جا نداره", "Ali رضا 123", "a", "‮‏mixed"])
def test_awkward_names_never_break_a_card(db, name):
    s = world(db, name)
    db.x("UPDATE stocks SET name=? WHERE id=?", (name, s["id"]))
    link = "https://t.me/HypeMarketBot?start=ref_1"
    for lang in ("fa", "en"):
        assert png_size(cards.portfolio_card(db, lang, 1, link)) == (1080, 1350)
        assert png_size(cards.stock_card(db, lang, core.need_stock(db, "SPEED"), link)) == (1080, 1350)


def test_empty_many_and_flagged_cases(db):
    s = world(db)
    link = "https://t.me/HypeMarketBot?start=ref_9"
    user(db, 9)
    assert png_size(cards.portfolio_card(db, "fa", 9, link)) == (1080, 1350)         # no holdings, no trades
    assert png_size(cards.rank_card(db, "en", 9, link)) == (1080, 1350)
    core.mint(db, C.coins(50_000))
    db.x("UPDATE users SET cash=cash+?, max_level=3 WHERE id=1", (C.coins(50_000),))
    for i, tk in enumerate(["AAA", "BBB", "CCC", "DDD", "EEE", "FFF", "GGG"]):         # more rows than the card has room for
        db.x("UPDATE users SET max_level=8 WHERE id=1")
        user(db, 20 + i)
        db.x("UPDATE users SET cash=cash+?, max_level=8 WHERE id=?", (C.coins(20_000), 20 + i))
        core.mint(db, C.coins(20_000))
        trading.launch(db, 20 + i, tk, f"Name{tk}")
        adv(5)
        trading.buy(db, 1, tk, 3)
    assert png_size(cards.portfolio_card(db, "fa", 1, link)) == (1080, 1350)
    for v in (2, 3, 4):
        trust.report(db, v, "SPEED", "rug")
    assert trust.flagged(db, s["id"])
    assert png_size(cards.stock_card(db, "fa", s, link)) == (1080, 1350)


def test_persian_is_really_shaped_and_the_font_ships_with_the_bot():
    assert cards.shape("سلام") != "سلام" and cards.shape("Hello") == "Hello"
    assert cards.shape("🚀") == "" and cards.shape("a 🚀 b") == "a  b"
    assert (cards.FONT_DIR / "Vazirmatn-Regular.ttf").exists() and (cards.FONT_DIR / "Vazirmatn-Bold.ttf").exists() and (cards.FONT_DIR / "OFL.txt").exists()
    assert "Vazirmatn" in cards.font(30).getname()[0]


# ====================================================================== through the chat
@pytest.fixture()
def h():
    h = tb.Harness()
    for u in (1, 2):
        h.say(u, "شروع")
    h.say(1, "عرضه SPEED اسپید | سهام تست")
    return h


def url_of(call):
    return [b.url for row in call.reply_markup.inline_keyboard for b in row if b.url]


def test_share_menu_and_each_card(h):
    h.say(1, "کارت")
    menu = h.session.sent(SendMessage)[-1]
    data = [b.callback_data for row in menu.reply_markup.inline_keyboard for b in row]
    assert data[:2] == ["scp:1", "scr:1"] and data[2].startswith("scs:")
    n = len(h.session.sent(SendPhoto))
    h.say(1, "کارت پورتفوی")
    photo = h.session.sent(SendPhoto)[-1]
    assert len(h.session.sent(SendPhoto)) == n + 1
    assert "start=ref_1" in photo.caption and "پورتفوی" in photo.caption
    link = url_of(photo)[0]
    assert link.startswith("https://t.me/share/url?") and urllib.parse.quote("https://t.me/TestBot?start=ref_1", safe="") in link
    h.say(1, "کارت رتبه")
    assert "رتبه" in h.session.sent(SendPhoto)[-1].caption
    h.say(1, "share SPEED")
    cap = h.session.sent(SendPhoto)[-1].caption
    assert "start=s_SPEED_r1" in cap and "$" in cap
    assert "ناشناخته" not in h.texts(h.say(1, "کارت پروفایل"))


def test_unknown_stock_gives_an_error_not_silence(h):
    assert "پیدا نشد" in h.texts(h.say(1, "!کارت NOPE"))


def test_buttons_on_portfolio_profile_and_stock_card(h):
    h.advance(60)
    h.say(2, "خرید 5 SPEED")
    for text, expect in (("پورتفوی", "scp:2"), ("پروفایل", "scr:2"), ("قیمت SPEED", "scs:")):
        h.say(2, text)
        last = h.session.sent(SendMessage)[-1]
        assert expect in [b.callback_data for row in last.reply_markup.inline_keyboard for b in row if b.callback_data] or any(
            (b.callback_data or "").startswith(expect) for row in last.reply_markup.inline_keyboard for b in row), text


def test_share_buttons_make_photos_and_carry_the_pressers_own_invite_link(h):
    sid = core.need_stock(h.db, "SPEED")["id"]
    n = len(h.session.sent(SendPhoto))
    h.press(2, f"scs:{sid}")                                                  # player 2 shares player 1's stock
    photo = h.session.sent(SendPhoto)[-1]
    assert len(h.session.sent(SendPhoto)) == n + 1 and "start=s_SPEED_r2" in photo.caption
    h.press(1, "scp:1")
    assert "start=ref_1" in h.session.sent(SendPhoto)[-1].caption
    ans = [c for c in h.press(2, "scp:1") if c.__class__.__name__ == "AnswerCallbackQuery"][0]
    assert "برای تو نیست" in (ans.text or "")                                  # nobody can render someone else's portfolio card


def test_group_share_includes_the_group_rank(h):
    h.say(1, "!شروع", chat=-300)
    h.say(1, "!کارت رتبه", chat=-300)
    assert h.session.sent(SendPhoto)


# ====================================================================== the link a friend opens
def start(h, uid, payload):
    return h.say(uid, f"/start {payload}")


def test_a_friend_opening_a_shared_stock_gets_welcome_card_and_invites_the_sharer(h):
    before = core.get_user(h.db, 1)["cash"]
    calls = start(h, 5, "s_SPEED_r1")
    msgs = [c for c in calls if isinstance(c, SendMessage)]
    assert len(msgs) == 2 and "خوش اومدی" in msgs[0].text and "SPEED" in msgs[1].text
    new = core.get_user(h.db, 5)
    assert new["referrer"] == 1 and new["cash"] == C.START_CASH + C.REF_BONUS_NEW             # the friend gets the invite bonus right away
    assert any(b.callback_data and b.callback_data.startswith("b:") for row in msgs[1].reply_markup.inline_keyboard for b in row)


def test_existing_players_just_see_the_card_and_bad_links_fall_back_to_welcome(h):
    msgs = [c for c in start(h, 2, "s_SPEED_r1") if isinstance(c, SendMessage)]
    assert len(msgs) == 1 and "SPEED" in msgs[0].text
    msgs = [c for c in start(h, 6, "s_NOPE_r1") if isinstance(c, SendMessage)]
    assert len(msgs) == 1 and "خوش اومدی" in msgs[0].text
    msgs = [c for c in start(h, 7, "s_SPEED_r999999999") if isinstance(c, SendMessage)]       # unknown inviter: ignored, still works
    assert "خوش اومدی" in msgs[0].text and core.get_user(h.db, 7)["referrer"] is None
    msgs = [c for c in start(h, 8, "ref_1") if isinstance(c, SendMessage)]
    assert core.get_user(h.db, 8)["referrer"] == 1


def test_share_texts_exist_in_both_languages_and_are_valid_html(db):
    s = world(db)
    for lang in ("fa", "en"):
        for kind, stock in (("p", None), ("r", None), ("s", s)):
            r = views.share_reply(db, lang, kind, 1, "TestBot", stock)
            assert tb.assert_valid_telegram_html(r.text) is None and len(r.text) < 1024      # Telegram's photo-caption limit
            assert r.kb.inline_keyboard[0][0].url.startswith("https://t.me/share/url?")


# ====================================================================== caption safety (Telegram allows 1024 characters)
def test_worst_case_captions_fit_and_overlong_ones_are_trimmed_cleanly(db):
    from hypemarket import charts
    from hypemarket.handlers import fit_caption
    s = world(db, "نام خیلی بلند برای تست " + "ک" * 10)
    db.x("UPDATE stocks SET name=?, descr=?, div_pct=50, fees_earned=?, hype=9999, rugs=12 WHERE id=?", ("ن" * 30, "توضیح خیلی بلند " * 8, C.coins(9_000_000), s["id"]))
    s = core.need_stock(db, "SPEED")
    for lang in ("fa", "en"):
        _, d = charts.build(db, s, "7d")
        assert len(views.chart_caption(db, lang, s, d)) <= 1024
        assert len(views.stock_card(db, lang, s, 2).text) <= 1024
    long = "\n".join(f"<b>line {i}</b> " + "x" * 80 for i in range(30))
    short = fit_caption(long)
    assert len(short) <= 1024 and tb.assert_valid_telegram_html(short) is None and short.startswith("<b>line 0</b>")
    assert fit_caption("small") == "small" and fit_caption(None) is None
