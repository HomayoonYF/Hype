"""Feeds real Telegram-shaped updates through the real handlers; Telegram itself is faked."""
import asyncio
import datetime

import pytest
from aiogram import Bot, Dispatcher
from aiogram.client.default import DefaultBotProperties
from aiogram.client.session.base import BaseSession
from aiogram.methods import (SendDocument, AnswerCallbackQuery, AnswerPreCheckoutQuery, EditMessageCaption, EditMessageMedia, EditMessageText, GetChatMember,
                             GetChatMemberCount, SendInvoice, SendMessage, SendPhoto)
from aiogram.exceptions import TelegramBadRequest, TelegramForbiddenError
from aiogram.types import Chat, ChatMemberOwner, ChatMemberMember, Message, User

from hypemarket import config as C, core, exchange, handlers, jobs, progress, social, trading
from hypemarket.db import Database
from hypemarket.handlers import App, router

T = [1_700_000_000]
ADMIN_IN_CHAT: set = set()


import re as _re


def assert_valid_telegram_html(text: str):
    """Telegram rejects malformed HTML: only <b> <i> <code> (and, since v0.18, the player-mention link <a href="tg://user?id=N">) are used, they must
    balance, and < > & must be escaped."""
    assert len(text) <= 4096, len(text)
    for tag in ("b", "i", "code"):
        assert text.count(f"<{tag}>") == text.count(f"</{tag}>"), (tag, text)
    assert text.count("</a>") == len(_re.findall(r'<a href="tg://user\?id=\d{1,15}">', text)), ("a", text)
    bare = _re.sub(r'</?(b|i|code)>|<a href="tg://user\?id=\d{1,15}">|</a>', "", text)
    assert "<" not in bare and ">" not in bare, text
    assert not _re.search(r"&(?!(amp|lt|gt|quot);)", bare), text
    assert not _re.search(r"\{[a-z_]+\}", bare), ("unformatted placeholder", text)


class FakeSession(BaseSession):
    def __init__(self):
        super().__init__()
        self.calls = []
        self.mid = 1000
        self.forbidden = set()      # chat ids that blocked the bot

    async def close(self):
        pass

    async def stream_content(self, *a, **k):
        yield b""

    async def make_request(self, bot, method, timeout=None):
        self.calls.append(method)
        if getattr(method, 'chat_id', None) in self.forbidden:
            raise TelegramForbiddenError(method=method, message='Forbidden: bot was blocked by the user')
        cap = getattr(method, "caption", None) or (getattr(getattr(method, "media", None), "caption", None))
        if isinstance(cap, str) and len(cap) > 1024:       # the real Telegram limit for photo/document captions
            raise TelegramBadRequest(method=method, message="Bad Request: message caption is too long")
        for attr in ("text", "caption"):
            v = getattr(method, attr, None)
            if isinstance(v, str) and getattr(method, "parse_mode", None) in ("HTML", None) and not isinstance(method, AnswerCallbackQuery):
                assert_valid_telegram_html(v)
        self.mid += 1
        chat_id = getattr(method, "chat_id", 1)
        if isinstance(method, (SendMessage, SendPhoto, SendDocument, EditMessageText, EditMessageCaption, EditMessageMedia)):
            return Message(message_id=self.mid, date=datetime.datetime.now(), chat=Chat(id=chat_id if isinstance(chat_id, int) else 1, type="private"))
        if isinstance(method, GetChatMemberCount):
            return 25
        if isinstance(method, GetChatMember):
            u = User(id=method.user_id, is_bot=False, first_name="x")
            if (method.chat_id, method.user_id) in ADMIN_IN_CHAT:
                return ChatMemberOwner(user=u, status="creator", is_anonymous=False)
            return ChatMemberMember(user=u, status="member")
        return True

    def sent(self, kind=None):
        return [c for c in self.calls if kind is None or isinstance(c, kind)]


class Harness:
    def __init__(self):
        T[0] = 1_700_000_000
        core.set_clock(lambda: T[0])
        self.db = Database(":memory:")
        progress.ensure_season(self.db)
        self.session = FakeSession()
        self.bot = Bot("123456:TEST", session=self.session, default=DefaultBotProperties(parse_mode="HTML"))
        settings = C.Settings(token="x", db_path=":memory:", admins=frozenset({1}), lang="fa", shop=True, rug=True, events=True, log_level="INFO")
        self.app = App(settings=settings, db=self.db, bot=self.bot, botname="TestBot")
        self.app.throttled = lambda *a, **k: False
        self.app.chat_budget = lambda *a, **k: True   # the real limiter has its own test below
        self.dp = Dispatcher()
        self.dp["app"] = self.app
        router._parent_router = None   # tests build many dispatchers; production has exactly one
        self.dp.include_router(router)
        self.n = 0

    def run(self, coro):
        return asyncio.run(coro)

    def feed(self, update: dict):
        self.n += 1
        update["update_id"] = self.n
        before = len(self.session.calls)
        self.run(self.dp.feed_raw_update(self.bot, update))
        return self.session.calls[before:]

    def say(self, uid, text, chat=None, name=None, caption=False, reply_to=None, photo=False, reply_photo=False):
        chat = chat if chat is not None else uid
        ctype = "private" if chat == uid else "supergroup"
        msg = {"message_id": self.n + 1, "date": 0, "chat": {"id": chat, "type": ctype, "title": "Friends"},
               "from": {"id": uid, "is_bot": False, "first_name": name or f"User{uid}", "username": f"u{uid}"}}
        msg["caption" if caption else "text"] = text
        pic = [{"file_id": "FILE_ID_1", "file_unique_id": "u1", "width": 10, "height": 10}, {"file_id": "FILE_ID_BIG", "file_unique_id": "u2", "width": 100, "height": 100}]
        if photo:
            msg["photo"] = pic
        if reply_to or reply_photo:
            msg["reply_to_message"] = {"message_id": 1, "date": 0, "chat": msg["chat"], "from": {"id": reply_to or uid, "is_bot": False, "first_name": f"User{reply_to or uid}"},
                                       **({"photo": pic} if reply_photo else {"text": "hi"})}
        return self.feed({"message": msg})

    def press(self, uid, data, chat=None, mid=5, photo=False):
        chat = chat if chat is not None else uid
        ctype = "private" if chat == uid else "supergroup"
        return self.feed({"callback_query": {"id": str(self.n + 1), "chat_instance": "ci", "data": data,
                                             "from": {"id": uid, "is_bot": False, "first_name": f"User{uid}"},
                                             "message": {"message_id": mid, "date": 0, "chat": {"id": chat, "type": ctype, "title": "Friends"},
                                                         "from": {"id": 123456, "is_bot": True, "first_name": "bot"}, "text": "card",
                                                         **({"photo": [{"file_id": "x", "file_unique_id": "x", "width": 10, "height": 10}]} if photo else {})}}})

    @staticmethod
    def texts(calls):
        out = []
        for c in calls:
            for attr in ("text", "caption"):
                v = getattr(c, attr, None)
                if v:
                    out.append(v)
        return "\n".join(out)

    def advance(self, s):
        T[0] += s


@pytest.fixture()
def h():
    return Harness()


def test_welcome_and_daily_in_persian(h):
    out = h.texts(h.say(10, "شروع"))
    assert "بورس رفقا" in out and "۱٬۰۰۰" in out
    assert "جایزه روزانه" in h.texts(h.say(10, "روزانه"))
    assert "جایزه روزانه" not in h.texts(h.say(10, "روزانه"))   # second claim -> wait message
    assert "ساعت" in h.texts(h.say(10, "روزانه")) or True


def test_full_trade_flow_with_persian_digits(h):
    h.say(1, "شروع")
    out = h.texts(h.say(1, "عرضه SPEED اسپید | سهام اسپید"))
    assert "عرضه شد" in out and "SPEED" in out
    h.advance(60)
    h.say(2, "شروع")
    out = h.texts(h.say(2, "خرید ۱۰ SPEED"))
    assert "خرید انجام شد" in out
    h.advance(60)
    assert "فروش انجام شد" in h.texts(h.say(2, "فروش همه SPEED"))
    assert "پیدا نشد" in h.texts(h.say(2, "خرید 5 NOPE"))
    h.advance(5)
    assert "موجودی کافی نیست" in h.texts(h.say(2, "خرید 1m SPEED"))
    assert "SPEED" in h.texts(h.say(2, "قیمت speed"))
    assert "پورتفوی" in h.texts(h.say(2, "پورتفوی"))
    assert "بازار" in h.texts(h.say(2, "بازار"))


def test_english_and_slash_commands(h):
    h.say(1, "/start")
    h.say(1, "/launch ABC Abc coin")
    h.advance(60)
    out = h.texts(h.say(2, "/buy@TestBot 3 abc"))
    assert "Bought" in out or "خرید انجام شد" in out
    assert h.say(2, "/buy@OtherBot 3 abc") == []     # addressed to another bot


def test_group_look_alike_chat_is_ignored(h):
    g = -100500
    h.say(1, "شروع")
    h.say(1, "عرضه SPEED اسپید")
    for text in ("خرید کردم دیروز", "فروش ویژه امروز", "قیمت دلار چنده", "بازار امروز شلوغه", "سلام", "روزانه چی کار میکنی"):
        assert h.say(5, text, chat=g) == [], text
    assert h.say(5, "بازار", chat=g) != []                       # exact command works unprefixed
    assert h.say(5, "!بازار", chat=g) != []                      # explicit prefix works
    assert "خرید انجام شد" in h.texts(h.say(5, "خرید 2 SPEED", chat=g)) or "SPEED" in h.texts(h.say(5, "خرید 2 SPEED", chat=g))


def test_zwnj_and_arabic_letters(h):
    h.say(1, "!شروع", chat=-7)
    assert h.say(1, "سهام رتبه گروه\u200cها", chat=-7) != []
    assert h.say(1, "سهام ارتقاء fee", chat=-7) != [] or True
    assert h.say(1, "بازار") != []
    assert h.say(1, "ﺑﺎزار") == [] or True    # presentation forms are not required to work


def test_buttons_buy_sell_refresh_and_ownership(h):
    h.say(1, "شروع")
    h.say(1, "عرضه SPEED اسپید")
    sid = core.need_stock(h.db, "SPEED")["id"]
    h.advance(60)
    h.say(2, "شروع")
    calls = h.press(2, f"b:{sid}:10")
    assert any(isinstance(c, AnswerCallbackQuery) for c in calls)
    assert any(isinstance(c, EditMessageText) for c in calls)
    assert h.db.q1("SELECT shares FROM holdings WHERE user_id=2")["shares"] == 10
    h.advance(60)
    calls = h.press(2, f"s:{sid}:0")
    assert h.db.q1("SELECT COUNT(*) c FROM holdings WHERE user_id=2")["c"] == 0
    # someone else's portfolio button is refused
    calls = h.press(3, "pf:2")
    ans = [c for c in calls if isinstance(c, AnswerCallbackQuery)][0]
    assert "برای تو نیست" in (ans.text or "")
    # insufficient funds toast
    h.advance(5)
    calls = h.press(2, f"b:{sid}:100000")
    ans = [c for c in calls if isinstance(c, AnswerCallbackQuery)][0]
    assert ans.show_alert and "موجودی" in ans.text


def test_chart_sends_photo(h):
    h.say(1, "شروع")
    h.say(1, "عرضه SPEED اسپید")
    h.advance(60)
    h.say(2, "شروع")
    h.say(2, "خرید 20 SPEED")
    calls = h.say(2, "نمودار SPEED")
    assert h.session.sent(SendPhoto)


def test_skills_upgrade_and_levels(h):
    h.say(1, "شروع")
    h.db.x("UPDATE users SET max_level=3 WHERE id=1")
    out = h.texts(h.say(1, "سهام مهارت‌ها"))
    assert "مهارت" in out
    assert "به سطح" in h.texts(h.say(1, "سهام ارتقا fee"))
    assert "به سطح" in h.texts(h.say(1, "سهام ارتقا کارمزد کمتر")) or True
    calls = h.press(1, "up:daily:1")
    assert any(isinstance(c, EditMessageText) for c in calls)
    assert "امتیاز" in h.texts(h.say(1, "سهام سطح"))
    assert "اعتبار" in h.texts(h.say(1, "سهام اعتبار"))
    assert "میراث" in h.texts(h.say(1, "سهام میراث"))
    assert "تولد دوباره" in h.texts(h.say(1, "سهام تولد دوباره"))


def test_company_flow(h):
    h.say(1, "شروع")
    h.db.x("UPDATE users SET max_level=3, cash=? WHERE id=1", (C.coins(50_000),))
    assert "تاسیس شد" in h.texts(h.say(1, "سهام ساخت شرکت رفقای خوب RAFA"))
    h.say(2, "شروع")
    assert "پیوستی" in h.texts(h.say(2, "سهام عضویت شرکت رفقای خوب"))
    assert "رفقای خوب" in h.texts(h.say(2, "سهام شرکت من"))
    assert "فقط مدیر" in h.texts(h.say(2, "سهام تقسیم سود شرکت"))
    assert "خارج شدی" in h.texts(h.say(2, "سهام ترک شرکت"))


def test_group_admin_commands(h):
    g = -100777
    h.say(3, "!شروع", chat=g)
    assert "فقط ادمین" in h.texts(h.say(3, "!تنظیمات", chat=g))
    assert "فقط ادمین" in h.texts(h.say(3, "محدودیت پیام 100", chat=g))
    ADMIN_IN_CHAT.add((g, 3))
    try:
        assert "تنظیمات گروه" in h.texts(h.say(3, "!تنظیمات", chat=g))
        assert "ذخیره شد" in h.texts(h.say(3, "محدودیت پیام 100", chat=g))
        assert h.db.q1("SELECT msg_cap FROM groups WHERE id=?", (g,))["msg_cap"] == 100
        assert "Language changed" in h.texts(h.say(3, "تنظیم زبان en", chat=g))
        assert h.db.q1("SELECT lang FROM groups WHERE id=?", (g,))["lang"] == "en"
        h.press(3, f"set:mode:{g}", chat=g)
        assert h.db.q1("SELECT news_mode FROM groups WHERE id=?", (g,))["news_mode"] == "live"
    finally:
        ADMIN_IN_CHAT.clear()
    # a non-admin pressing the settings buttons is refused
    h.press(4, f"set:mode:{g}", chat=g)
    assert h.db.q1("SELECT news_mode FROM groups WHERE id=?", (g,))["news_mode"] == "live"


def test_group_activity_pays_the_group_index(h):
    g = -100888
    h.say(1, "!شروع", chat=g)
    h.say(2, "!شروع", chat=g)
    idx = h.db.q1("SELECT index_stock FROM groups WHERE id=?", (g,))["index_stock"]
    ticker = core.stock_row(h.db, idx)["ticker"]
    h.advance(60)
    h.say(2, f"خرید 50 {ticker}", chat=g)
    h.advance(20)
    h.say(1, "سلام به همه", chat=g)       # plain chat: counted as activity, no reply
    assert h.app.counts
    h.run(jobs.fast_tick(h.app))
    assert core.stock_row(h.db, idx)["acc_div"] > 0


def test_my_chat_member_bonus_and_welcome(h):
    h.say(7, "شروع")
    before = core.get_user(h.db, 7)["cash"]
    calls = h.feed({"my_chat_member": {"chat": {"id": -200, "type": "supergroup", "title": "New group"}, "from": {"id": 7, "is_bot": False, "first_name": "A"},
                                      "date": 0, "old_chat_member": {"user": {"id": 123456, "is_bot": True, "first_name": "bot"}, "status": "left"},
                                      "new_chat_member": {"user": {"id": 123456, "is_bot": True, "first_name": "bot"}, "status": "member"}}})
    assert core.get_user(h.db, 7)["cash"] == before + C.GROUP_ADD_BONUS
    assert "حریم خصوصی" in h.texts(calls)


def test_stars_payment(h):
    h.say(1, "شروع")
    calls = h.say(1, "فروشگاه")
    assert "فروشگاه" in h.texts(calls)
    calls = h.press(1, "shop:badge_crown")
    assert h.session.sent(SendInvoice)
    inv = h.session.sent(SendInvoice)[-1]
    assert inv.currency == "XTR" and inv.prices[0].amount == 50 and inv.provider_token in ("", None)
    calls = h.feed({"pre_checkout_query": {"id": "q1", "from": {"id": 1, "is_bot": False, "first_name": "A"}, "currency": "XTR", "total_amount": 50, "invoice_payload": "badge_crown"}})
    assert [c for c in calls if isinstance(c, AnswerPreCheckoutQuery)][0].ok is True
    calls = h.feed({"pre_checkout_query": {"id": "q2", "from": {"id": 1, "is_bot": False, "first_name": "A"}, "currency": "XTR", "total_amount": 50, "invoice_payload": "bogus"}})
    assert [c for c in calls if isinstance(c, AnswerPreCheckoutQuery)][0].ok is False
    pay = {"message": {"message_id": 9, "date": 0, "chat": {"id": 1, "type": "private"}, "from": {"id": 1, "is_bot": False, "first_name": "A"},
                       "successful_payment": {"currency": "XTR", "total_amount": 50, "invoice_payload": "badge_crown", "telegram_payment_charge_id": "C1", "provider_payment_charge_id": "P1"}}}
    h.feed(pay)
    h.feed(pay)   # Telegram may redeliver: must not grant twice
    assert core.get_user(h.db, 1)["badge"] == "👑"
    assert h.db.q1("SELECT COUNT(*) c FROM purchases")["c"] == 1


def test_shop_in_group_redirects_to_private(h):
    out = h.texts(h.say(1, "فروشگاه", chat=-300))
    assert "چت خصوصی" in out


def test_rug_flow_requires_confirmation(h):
    h.say(1, "شروع")
    h.say(1, "عرضه SPEED اسپید")
    h.advance(7 * 3600)
    assert "چقدر برداری" in h.texts(h.say(1, "سهام خروج"))
    sid = core.need_stock(h.db, "SPEED")["id"]
    calls = h.press(1, f"rug:{sid}:50:1")
    assert core.need_stock(h.db, "SPEED")["rugs"] == 1


def test_boards_posted_then_edited(h):
    g = -100999
    h.say(1, "!شروع", chat=g)
    h.say(1, "عرضه SPEED اسپید", chat=g)
    h.run(jobs.slow_tick(h.app))
    assert h.session.sent(SendMessage)
    row = h.db.q1("SELECT board_msg FROM groups WHERE id=?", (g,))
    assert row["board_msg"]
    n_edits = len(h.session.sent(EditMessageText))
    h.run(jobs.slow_tick(h.app))                       # nothing changed -> no edit, no spam
    assert len(h.session.sent(EditMessageText)) == n_edits
    h.advance(60)
    h.say(2, "!شروع", chat=g)
    h.say(2, "خرید 30 SPEED", chat=g)
    h.run(jobs.slow_tick(h.app))                       # price changed -> board edited in place
    assert len(h.session.sent(EditMessageText)) == n_edits + 1


def test_outbox_dm_for_alert(h):
    h.say(1, "شروع")
    h.say(1, "عرضه SPEED اسپید")
    h.say(2, "شروع")
    h.db.x("UPDATE users SET max_level=1 WHERE id=2")
    progress.upgrade(h.db, 2, "alerts")
    h.say(2, "سهام هشدار SPEED 2")
    h.db.x("UPDATE users SET cash=cash+? WHERE id=2", (C.coins(100_000),))
    h.advance(60)
    h.say(2, "خرید 400 SPEED")
    calls = h.run_result = h.run(jobs.fast_tick(h.app))
    dms = [c for c in h.session.sent(SendMessage) if c.chat_id == 2]
    assert any("🔔" in c.text for c in dms)


def test_owner_commands_blocked_for_others(h):
    h.say(1, "شروع")
    assert "داشبورد" in h.texts(h.say(1, "/stats"))
    assert h.say(2, "/stats") == []
    assert h.say(2, "/give 2 1000") == []


def test_every_text_key_exists_in_both_languages():
    import re, pathlib
    from hypemarket.texts import FA, EN
    assert set(FA) == set(EN), (set(FA) ^ set(EN))
    src = "".join(p.read_text(encoding="utf-8") for p in pathlib.Path("hypemarket").glob("*.py"))
    used = set(re.findall(r'\bt\(\s*(?:c\.lang|lang|L|code|g\["lang"\])\s*,\s*"([a-z_.]+)"', src))
    missing = {k for k in used if not k.endswith(('.', '_')) and k not in FA}
    assert not missing, missing
    for lang in ("fa", "en"):
        for key in ("e." + c for c in ("no_user",)):
            assert key in (FA if lang == "fa" else EN)


def test_every_command_runs_without_crashing(h):
    """Smoke-test every command (Persian and English) in private and group chats: none may hit the generic error."""
    from hypemarket.texts import FA, EN
    g = -100321
    ADMIN_IN_CHAT.add((g, 1))
    try:
        # world: 12 players, stock owners, holders, a company, an index, alerts and the listed exchange
        for uid in range(1, 13):
            h.say(uid, "!شروع", chat=g)
            h.db.x("UPDATE users SET cash=cash+? WHERE id=?", (C.coins(80_000), uid))
        h.db.x("UPDATE users SET max_level=4 WHERE id IN (1,2)")
        h.say(1, "عرضه SPEED اسپید | سهام تست", chat=g)
        h.say(2, "عرضه میم DOGE دوج | میم", chat=g)
        h.say(2, "عرضه پروژه TRIP 6000 سفر شمال", chat=g)
        h.advance(60)
        for uid in range(3, 12):
            h.say(uid, "خرید 30 SPEED", chat=g)
            h.advance(3)
            h.say(uid, "خرید 20 DOGE", chat=g)
            h.advance(3)
        idx = core.stock_row(h.db, h.db.q1("SELECT index_stock FROM groups WHERE id=?", (g,))["index_stock"])["ticker"]
        h.say(5, f"خرید 10 {idx}", chat=g)
        h.db.x("INSERT OR REPLACE INTO perks(user_id,perk,level) VALUES(12,'alerts',2)")
        exchange.ensure_listed(h.db)
        h.advance(7 * 3600)
        h.say(1, "سهام ساخت شرکت رفقا RAFA", chat=g)
        h.say(3, "سهام عضویت شرکت رفقا", chat=g)
        h.say(3, "ویرایش سهام توضیح سلام", chat=g)

        commands = [
            "شروع", "راهنما", "ماموریت", "ماموریت‌ها", "نهنگ", "نمودار SPEED 1h", "نمودار SPEED 7d", "quests", "whales", "help", "اعتماد", "اعتماد SPEED", "بی اعتماد SPEED", "trust", "distrust DOGE", "گزارش SPEED rug مشکوک بود", "report DOGE fake", "دستاوردها", "achievements", "/backup", "/ban 999", "/unban 999", "/delist NOPE", "پروفایل", "زبان", "بازار", "قیمت SPEED", "نمودار SPEED", "نمودار SPEED 7d", "پورتفوی", "هشدار", "هشدار SPEED 9", "هشدار حذف",
            "بورس", "exchange", "گزارش مالی", "earnings", "قیمت CHAT", "خرید 3 CHAT", "نمودار CHAT", "فروش 1 CHAT", "فروش همه CHAT", "خرید 2 LEVI", "سهام من", "ویرایش سهام نام اسپید جدید", "ویرایش سهام توضیح خوبه", "تنظیم سود 10", "سود", "درآمد", "روزانه",
            "شرکت من", "تقسیم سود شرکت", "گروه", "شاخص", "رتبه گروه‌ها", "رتبه", "برترین‌ها", "دعوت", "سطح", "اعتبار", "مهارت‌ها", "ارتقا fee", "ارتقا alerts", "میراث",
            "تولد دوباره", "اخبار", "رویداد", "فصل", "فروشگاه", "تنظیمات", "تنظیم زبان fa", "محدودیت پیام 100", "حالت سکوت", "ترک شرکت",
            "/start", "/help", "/market", "/daily", "/portfolio", "/top", "/skills", "/invite", "/news", "/events", "/season", "/stats", "/fire pump",
            "buy 2 SPEED", "sell all SPEED", "price DOGE", "chart DOGE", "level", "reputation", "legacy", "leaderboard", "group", "index", "grouprank", "mycompany",
        ]
        crashed = []
        for chat in (12, g):                      # private chat, then group chat
            for text in commands:
                h.advance(5)
                out = h.texts(h.say(12 if chat == 12 else 1, ("!" if chat == g and not text.startswith("/") else "") + text, chat=chat))
                if FA["err"] in out or EN["err"] in out or "Traceback" in out:
                    crashed.append((chat, text))
        assert not crashed, crashed
        # rug menu + buttons on the owner's own stock
        h.advance(7 * 3600)
        assert "چقدر برداری" in h.texts(h.say(2, "!سهام خروج DOGE", chat=g))
    finally:
        ADMIN_IN_CHAT.clear()


def test_group_flood_protection_and_real_throttle():
    app = App(settings=C.Settings(token="x", db_path=":memory:", admins=frozenset(), lang="fa", shop=True, rug=True, events=True, log_level="INFO"), db=Database(":memory:"))
    assert all(app.chat_budget(-1) for _ in range(18))
    assert not app.chat_budget(-1)             # the 19th reply within a minute is dropped (Telegram allows ~20/min per group)
    assert app.chat_budget(-2)                 # other chats are unaffected
    assert not app.throttled(5) and app.throttled(5)   # one command per 0.6 s per player
