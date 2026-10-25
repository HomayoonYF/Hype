"""v0.18.1: numbers are shown with English digits (1,000 🪙 · $5.00 · 12.5%) everywhere the bot says anything; the old Persian digits still exist and
come back with one switch (env DIGITS=fa). The rest of the suite runs in the legacy mode (tests/conftest.py); this file flips the switch per test."""
import collections
import re

import pytest
from aiogram.methods import EditMessageMedia

import mapkit
import test_bot as tb
from hypemarket import cards, config as C, fmt, views, world, worldmap, worldplaces
from hypemarket.texts import EN, FA, t

PERSIAN = re.compile("[۰-۹٠-٩٬٫٪]")
CH = -100515


@pytest.fixture()
def en(monkeypatch):
    monkeypatch.setenv("DIGITS", "en")


@pytest.fixture()
def fa(monkeypatch):
    monkeypatch.setenv("DIGITS", "fa")


# ============================================================================================== the switch
def test_english_digits_are_the_default_and_the_switch_spellings(monkeypatch):
    monkeypatch.delenv("DIGITS", raising=False)
    assert C.digits_mode() == "en"
    for v in ("fa", "FA", " persian ", "Farsi", "old", "Legacy"):
        monkeypatch.setenv("DIGITS", v)
        assert C.digits_mode() == "fa", v
    for v in ("en", "EN", "", "english", "1", "junk"):
        monkeypatch.setenv("DIGITS", v)
        assert C.digits_mode() == "en", v


def test_the_shipped_env_example_documents_the_switch():
    text = open(".env.example", encoding="utf-8").read() if __import__("os").path.exists(".env.example") else ""
    assert "DIGITS" in text and "DAILY_UTC_OFFSET_MIN" in text


def test_digits_function_converts_every_persian_and_arabic_digit_and_sign(en):
    assert fmt.digits("۰۱۲۳۴۵۶۷۸۹") == "0123456789" and fmt.digits("٠١٢٣٤٥٦٧٨٩") == "0123456789"
    assert fmt.digits("۱٬۲۳۴٬۵۶۷٫۸۹ و ۵٪") == "1,234,567.89 و 5%"
    assert fmt.digits("abc 12 $5.00") == "abc 12 $5.00" and fmt.digits("") == "" and fmt.digits(None) is None and fmt.digits(5) == 5
    assert fmt.digits(fmt.digits("۱۲۳")) == "123"                                                    # idempotent


def test_digits_function_leaves_text_alone_in_the_old_mode(fa):
    s = "۱٬۲۳۴٬۵۶۷٫۸۹ و ۵٪"
    assert fmt.digits(s) == s


# ============================================================================================== the formatters
def test_every_formatter_speaks_english_digits(en):
    for lang in ("fa", "en"):
        assert fmt.num(1234567, lang) == "1,234,567"
        assert fmt.usd(500, lang) == "$5.00" and fmt.usd(-1234567, lang) == "-$12,345.67"
        assert fmt.pct(12.5, lang, arrows=False) == "12.5%"
        assert not PERSIAN.search(fmt.coins(123456789, lang)) and not PERSIAN.search(fmt.coins(12345, lang)) and not PERSIAN.search(fmt.price(123456, lang))
        assert not PERSIAN.search(fmt.dur(93784, lang)) and not PERSIAN.search(fmt.clock(1_700_000_000, lang)) and not PERSIAN.search(fmt.coins_x(123456, lang))
    assert fmt.loc("12,345.6", "fa") == "12,345.6"


def test_the_old_look_comes_back_with_the_switch(fa):
    assert fmt.num(1234567, "fa") == "۱٬۲۳۴٬۵۶۷" and fmt.usd(500, "fa") == "$۵٫۰۰" and fmt.pct(12.5, "fa", arrows=False) == "۱۲٫۵٪"
    assert fmt.num(1234567, "en") == "1,234,567"                                                    # English texts never had Persian digits


# ============================================================================================== every text, every button label
def test_no_template_of_the_bot_has_a_persian_digit_left_when_spoken(en):
    for lang, table in (("fa", FA), ("en", EN)):
        for key, raw in table.items():
            if isinstance(raw, str):
                assert not PERSIAN.search(t(lang, key)), (lang, key)
    assert any(PERSIAN.search(v) for v in FA.values() if isinstance(v, str))                         # (the Persian digits are still written in the texts: nothing was deleted)


def test_a_hard_coded_persian_number_is_spoken_in_english_digits(en):
    key = next(k for k, v in FA.items() if isinstance(v, str) and "۱٬۰۰۰" in v and "{" not in v)
    assert "۱" not in t("fa", key) and "1,000" in t("fa", key)


def test_buttons_and_replies_follow_the_style(en):
    markup = views.kb([[("۱۲ 🪙 · ۵٪", "b:5:10"), ("$۵٫۰۰", "https://example.com/۱", "url")]])
    labels = [b.text for row in markup.inline_keyboard for b in row]
    assert labels == ["12 🪙 · 5%", "$5.00"] and markup.inline_keyboard[0][0].callback_data == "b:5:10"        # the data of a button is never touched
    r = views.Reply(text="۱٬۰۰۰", toast="۲۳٪")
    assert r.text == "1,000" and r.toast == "23%"
    assert "۱" not in cards.shape("۱۲ 🪙") and "12" in cards.shape("۱۲ 🪙")


def test_the_old_look_is_kept_by_replies_and_buttons_in_the_old_mode(fa):
    markup = views.kb([[("۱۲ 🪙", "b:5:10")]])
    assert markup.inline_keyboard[0][0].text == "۱۲ 🪙" and views.Reply(text="۱٬۰۰۰").text == "۱٬۰۰۰"


# ============================================================================================== crawl: open every screen, look for a Persian digit
def seen_in(calls):
    """Every string the bot showed in these calls: texts, captions, picture captions, button labels and answers to presses."""
    out = []
    for c in calls:
        if isinstance(c, EditMessageMedia):
            out.append(c.media.caption or "")
        for attr in ("text", "caption"):
            v = getattr(c, attr, None)
            if isinstance(v, str):
                out.append(v)
        mk = getattr(c, "reply_markup", None)
        if mk is not None and hasattr(mk, "inline_keyboard"):
            out += [b.text for row in mk.inline_keyboard for b in row]
    return out


def data_in(calls):
    out = []
    for c in calls:
        mk = getattr(c, "reply_markup", None)
        if mk is not None and hasattr(mk, "inline_keyboard"):
            out += [b.callback_data for row in mk.inline_keyboard for b in row if b.callback_data]
    return out


SAFE_SKIP = ("sm:", "ad:", "adm", "rb:")                                                           # nothing here is a screen worth crawling


def crawl(press, first_calls, limit=140):
    """Breadth-first: press every button of every screen reached (once each), and collect everything the bot showed."""
    shown, queue, done = [], collections.deque(), set()
    shown += seen_in(first_calls)
    queue.extend(data_in(first_calls))
    while queue and len(done) < limit:
        data = queue.popleft()
        if data in done or data.startswith(SAFE_SKIP) or len(data.encode()) > 64:
            continue
        done.add(data)
        calls = press(data)
        shown += seen_in(calls)
        shown += [c.text for c in calls if getattr(c, "text", None) and c.__class__.__name__ == "AnswerCallbackQuery"]
        queue.extend(data_in(calls))
    return shown, done


def test_private_chat_screens_have_no_persian_digits(en):
    h = tb.Harness()
    shown = []
    for cmd in ("شروع", "منو", "پروفایل", "روزانه", "بازار", "اخبار", "بورس", "گزارش مالی", "کیف پول", "صرافی", "مشاغل", "اسنپ", "مک‌دونالد",
                "شهرداری", "راهنما", "رتبه‌بندی", "ماموریت", "دستاوردها", "فروشگاه", "سهام عرضه SPEED اسپید", "خرید 3 SPEED", "پورتفو", "آمار", "daily", "help", "wallet"):
        calls = h.say(11, cmd)
        shown += seen_in(calls)
    first = h.say(11, "منو")
    shown_more, done = crawl(lambda data: h.press(11, data, mid=9, photo=True), first)
    shown += shown_more
    bad = [s for s in shown if PERSIAN.search(s)]
    assert len(shown) > 60 and len(done) > 12 and not bad, bad[:5]


def test_map_casino_and_job_screens_have_no_persian_digits(en):
    worldmap.cache_clear()
    worldplaces.cache_clear()
    h = tb.Harness()
    world.save_world(h.db, CH, world.build(7))
    d = mapkit.Driver(h, CH)
    v = d.open(21)
    shown = seen_in([]) + [v.caption] + [x for x, _ in v.buttons]
    stack = [(x, dd) for x, dd in v.buttons]
    queue, done = collections.deque(dd for _x, dd in v.buttons), set()
    while queue and len(done) < 120:
        data = queue.popleft()
        if data in done or len(data.encode()) > 64:
            continue
        done.add(data)
        out = d.press(21, data, v)
        shown += [out.caption] + [x for x, _ in out.buttons] + out.toasts
        queue.extend(dd for _x, dd in out.buttons)
    bad = [s for s in shown if s and PERSIAN.search(s)]
    assert len(done) > 15 and not bad, bad[:5]


def test_casino_and_wallet_screens_have_no_persian_digits(en, monkeypatch):
    from test_casino import A, CH as CAS, d as _d, enter, h as _h, scripted, table_of, next_hand  # noqa: F401
    worldmap.cache_clear()
    worldplaces.cache_clear()
    hh = tb.Harness()
    world.save_world(hh.db, CAS, world.build(7))
    dd = mapkit.Driver(hh, CAS)
    v, lobby = enter(hh, dd, A)
    scripted(monkeypatch, ["10♠", "9♥"], ["10♣", "8♦"])
    table = table_of(dd, A, v, lobby, bet=5_00)
    hit = dd.press(A, f"mp:{v.sid}:bj:s:{hh.db.q1('SELECT seq FROM casino_games')['seq']}", v)
    nxt = next_hand(dd, A, v, hit)
    shown = [lobby.caption, table.caption, hit.caption, nxt.caption] + [x for out in (lobby, table, hit, nxt) for x, _ in out.buttons]
    assert "$5.00" in table.caption and "$105.00" in hit.caption and "+$5.00" in hit.caption
    assert not [s for s in shown if PERSIAN.search(s)]
    from hypemarket import lifeui
    wallet = lifeui.wallet(hh.db, "fa", A)
    assert "$" in wallet.text and not PERSIAN.search(wallet.text)
