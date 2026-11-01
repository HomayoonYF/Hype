"""Shared helpers of the v0.20 identity tests: two real cities, a real character set-up, pictures and the calls the fake Telegram recorded."""
import io

import pytest
from PIL import Image

import test_bot as tb
from hypemarket import character, world, worldmap, worldplaces

CH1, CH2 = -100941, -100942
A, B = 883001, 883002
_LAYOUTS: dict = {}
BASE = {"height": "average", "build": "average", "skin_tone": "medium", "hair_color": "brown", "hair_style": "wavy", "eye_color": "brown", "face_shape": "oval"}


def layout(seed):
    if seed not in _LAYOUTS:
        _LAYOUTS[seed] = world.build(seed)
    return _LAYOUTS[seed]


def make_harness():
    worldmap.cache_clear()
    worldplaces.cache_clear()
    hh = tb.Harness()
    world.save_world(hh.db, CH1, layout(7))
    world.save_world(hh.db, CH2, layout(5))
    return hh


def finish(h, uid, first="آرمان", family="کیانی", gender="male", chat=CH1):
    """The real thing: the first `مپ` makes the pending profile (and the city), the private set-up chat finishes it."""
    h.say(uid, "مپ", chat=chat)
    h.say(uid, "/start char")
    h.say(uid, first)
    h.say(uid, family)
    h.press(uid, f"cr:g:{gender}:{uid}")
    ch = character.get(h.db, uid)
    assert character.is_done(ch)
    return ch


def photos(calls):
    return [c for c in calls if isinstance(c, tb.SendPhoto)]


def pic(call):
    return call.photo.data


def png(b):
    return Image.open(io.BytesIO(b))


def kinds(calls):
    return [type(c).__name__ for c in calls]


def of(calls, name):
    return [c for c in calls if type(c).__name__ == name]


def buttons(call):
    """[(label, callback_data)] of the inline keyboard of a recorded send / edit call."""
    kbd = getattr(call, "reply_markup", None)
    if kbd is None:
        return []
    return [(b.text, b.callback_data) for row in kbd.inline_keyboard for b in row if b.callback_data]


def snapshot(db, tables):
    return {t: [tuple(r) for r in db.qa(f"SELECT * FROM {t} ORDER BY 1")] for t in tables}


CHAR_TABLES = ("characters", "character_genes", "character_traits", "character_xp", "character_xp_log", "character_identity", "citizen_seq", "intercity_trips", "worlds")
DISCLAIMERS = ("فقط توی بازیه", "هویت واقعی", "in-game only", "real identity", "not your real", "legal validity", "فاقد اعتبار", "اعتبار قانونی", "این کارت فقط", "این نام فقط")
