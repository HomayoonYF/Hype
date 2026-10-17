"""Regression tests for «ویرایش سهام» and «عرضه پروژه»: every natural way of typing them must work or explain itself."""
import pytest

from hypemarket import config as C, core

import test_bot as tb

G = -100777


@pytest.fixture()
def h():
    h = tb.Harness()
    for u in (1, 2):
        h.say(u, "شروع")
        h.say(u, "!شروع", chat=G)
    h.db.x("UPDATE users SET cash=?, max_level=2 WHERE id=1", (C.coins(80_000),))
    h.say(1, "عرضه SPEED اسپید | سهام تست")
    return h


def stock(h, ticker="SPEED"):
    return core.need_stock(h.db, ticker)


@pytest.mark.parametrize("chat", [None, G], ids=["private", "group"])
@pytest.mark.parametrize("text,field,expect", [
    ("ویرایش سهام نام اسپید جدید", "name", "اسپید جدید"),
    ("ویرایش سهام اسم اسپید دو", "name", "اسپید دو"),
    ("ویرایش سهام SPEED نام اسپید سه", "name", "اسپید سه"),
    ("ویرایش سهام نام SPEED اسپید چهار", "name", "اسپید چهار"),
    ("ویرایش سهام توضیح عالیه", "descr", "عالیه"),
    ("ویرایش سهام بیو بهترین سهام", "descr", "بهترین سهام"),
    ("ویرایش سهام SPEED توضیح با نماد", "descr", "با نماد"),
    ("editstock descr english text", "descr", "english text"),
    ("editstock SPEED name Rocket", "name", "Rocket"),
])
def test_every_way_of_editing_a_stock_works(h, chat, text, field, expect):
    calls = h.say(1, text, chat=chat)
    assert "ویرایش شد" in h.texts(calls) or "updated" in h.texts(calls), h.texts(calls)
    assert stock(h)[field] == expect


@pytest.mark.parametrize("chat", [None, G], ids=["private", "group"])
@pytest.mark.parametrize("text", ["ویرایش سهام", "ویرایش سهام نام", "ویرایش سهام SPEED", "ویرایش سهام چیزی بی‌معنی", "ویرایش سهام SPEED بی‌معنی"])
def test_bad_edit_input_always_explains_itself(h, chat, text):
    out = h.texts(h.say(1, text, chat=chat))
    assert out, f"no reply in {chat} for {text!r}"
    assert "نمونه" in out and "سهام ویرایش" in out


@pytest.mark.parametrize("chat", [None, G], ids=["private", "group"])
def test_editing_someone_elses_or_no_stock_gives_a_clear_error(h, chat):
    out = h.texts(h.say(2, "ویرایش سهام نام هک", chat=chat))
    assert "عرضه نکردی" in out
    out = h.texts(h.say(2, "ویرایش سهام SPEED نام هک", chat=chat))
    assert "مال تو نیست" in out
    assert stock(h)["name"] == "اسپید"


@pytest.mark.parametrize("chat", [None, G], ids=["private", "group"])
def test_logo_by_photo_caption_and_by_reply(h, chat):
    calls = h.say(1, "ویرایش سهام لوگو", chat=chat, photo=True, caption=True)
    assert "لوگو" in h.texts(calls) and stock(h)["logo"] == "FILE_ID_BIG"
    h.db.x("UPDATE stocks SET logo=NULL")
    calls = h.say(1, "ویرایش سهام لوگو", chat=chat, reply_photo=True)
    assert stock(h)["logo"] == "FILE_ID_BIG"
    h.db.x("UPDATE stocks SET logo=NULL")
    assert "عکس بفرست" in h.texts(h.say(1, "ویرایش سهام لوگو", chat=chat))        # no photo: explains how
    assert stock(h)["logo"] is None


def test_stock_with_logo_is_shown_as_a_photo(h):
    h.say(1, "ویرایش سهام لوگو", photo=True, caption=True)
    from aiogram.methods import SendPhoto
    before = len(h.session.sent(SendPhoto))
    h.say(2, "قیمت SPEED")
    assert len(h.session.sent(SendPhoto)) == before + 1


# ------------------------------------------------------------------------------ عرضه پروژه
@pytest.mark.parametrize("chat", [None, G], ids=["private", "group"])
@pytest.mark.parametrize("text,goal", [
    ("عرضه پروژه TRIP 20000 سفر شمال", C.coins(20_000)),
    ("عرضه پروژه TRIP ۲۰۰۰۰ سفر شمال | یه سفر خفن", C.coins(20_000)),
    ("عرضه پروژه TRIP 20k سفر شمال", C.coins(20_000)),
    ("عرضه پروژه TRIP سفر شمال", C.DEFAULT_PROJECT_GOAL),                   # goal is optional
    ("عرضه پروژه TRIP سفر شمال | توضیح", C.DEFAULT_PROJECT_GOAL),
    ("project TRIP 7000 North trip", C.coins(7_000)),
])
def test_every_way_of_launching_a_project_works(h, chat, text, goal):
    out = h.texts(h.say(1, text, chat=chat))
    assert "عرضه شد" in out or "is live" in out, out
    s = stock(h, "TRIP")
    assert s["kind"] == "project" and s["goal"] == goal


@pytest.mark.parametrize("chat", [None, G], ids=["private", "group"])
def test_project_problems_are_explained_not_ignored(h, chat):
    for text, must in [
        ("عرضه پروژه", "نمونه"),
        ("عرضه پروژه سفر شمال 20000", "حرف انگلیسی"),                       # Persian name where the ticker should be
        ("عرضه پروژه TRIP 20000", "نمونه"),                                   # goal but no name
        ("عرضه پروژه TRIP 100 سفر", "هدف مالی"),                              # goal below the minimum
        ("عرضه پروژه SPEED 20000 سفر", "قبلاً گرفته"),                       # ticker already taken (was silent in groups)
    ]:
        out = h.texts(h.say(1, text, chat=chat))
        assert must in out, (text, out)


def test_project_needs_level_two_and_says_so(h):
    h.db.x("UPDATE users SET max_level=0, cash=? WHERE id=2", (C.coins(3_000),))   # level 1 only
    out = h.texts(h.say(2, "عرضه پروژه TRIP 20000 سفر شمال"))
    assert "سطح ۲" in out and "الان سطح" in out


def test_meme_launch_errors_are_visible_in_groups(h):
    out = h.texts(h.say(1, "عرضه میم SPEED دوج", chat=G))
    assert "قبلاً گرفته" in out
    out = h.texts(h.say(1, "سهام عرضه میم", chat=G))
    assert "نمونه" in out
    assert "عرضه شد" in h.texts(h.say(1, "عرضه میم DOGE دوج", chat=G))


def test_chatty_words_still_do_not_trigger_the_bot_in_groups(h):
    for text in ("عرضه و تقاضا امروز بالاست", "عرضه کردم", "پروژه تموم شد", "سلام ویرایش شد؟", "میم جدید دیدی"):
        assert h.say(2, text, chat=G) == [], text
