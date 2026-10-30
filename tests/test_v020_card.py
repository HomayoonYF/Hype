"""v0.20: the national ID card, `کارت ملی`.

The card is a real picture: a 2D portrait drawn from the character's SAVED traits and gender (portrait.py) next to the saved names, citizen ID, age, birthplace and current
city (idcard.py). The picture is made in memory, never stored; the same saved data gives the same bytes in every group, city, restart and repeat. These tests cover the drawing
(every trait changes the picture, nothing random or remote is involved), what the card says (and does not say while the character is on a journey), the command and the menu
button, and the player whose character is not finished. What they cannot cover: how Telegram shows the photo on a real phone (the pictures were looked at by eye)."""
import io
import random
import socket

import pytest
from PIL import Image

import mapkit
import test_bot as tb
from hypemarket import character, config as C, core, idcard, intercity, portrait, textparse, traits as T, world, worldmap, worldplaces
from hypemarket.db import Database
from hypemarket.texts import ALIASES, t

REAL_CHARACTER_SETUP = True                                          # these tests drive the real set-up / finish characters themselves

CH1, CH2 = -100941, -100942
A, B = 883001, 883002
_LAYOUTS: dict = {}
BASE = {"height": "average", "build": "average", "skin_tone": "medium", "hair_color": "brown", "hair_style": "wavy", "eye_color": "brown", "face_shape": "oval"}
PORTRAIT_BOX = (44, 136, 44 + 300, 136 + 366)                        # where the Persian card puts the portrait (the English card mirrors it)


def layout(seed):
    if seed not in _LAYOUTS:
        _LAYOUTS[seed] = world.build(seed)
    return _LAYOUTS[seed]


@pytest.fixture()
def h():
    worldmap.cache_clear()
    worldplaces.cache_clear()
    portrait.cache_clear()
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


def data(traits=None, **kw):
    tr = dict(BASE, **(traits or {}))
    base = dict(first="آرمان", last="کیانی", citizen_id=12, gender="male", age=15, traits=tuple(tr.items()), birth=(1, "شهر یک", "City One"), here=(1, "شهر یک", "City One"), to=None)
    base.update(kw)
    return idcard.CardData(**base)


# ============================================================================================== the portrait is drawn from the traits
@pytest.mark.parametrize("trait", list(T.TRAIT_NAMES))
def test_every_value_of_every_trait_changes_the_picture(trait):
    seen = {}
    for code in T.CATALOG[trait]["values"]:
        img = portrait.render(dict(BASE, **{trait: code}), "male")
        seen[code] = img.tobytes()
    assert len(set(seen.values())) == len(seen), f"two values of {trait} look the same"


def test_gender_changes_the_picture_and_nothing_else_does():
    m = portrait.render(BASE, "male").tobytes()
    f = portrait.render(BASE, "female").tobytes()
    assert m != f
    assert portrait.render(BASE, "male").tobytes() == m and portrait.render(dict(reversed(list(BASE.items()))), "male").tobytes() == m      # the order of the dict does not matter


def test_the_portrait_is_the_same_every_time_even_after_the_cache_is_gone():
    first = portrait.render(BASE, "female").tobytes()
    portrait.cache_clear()
    assert portrait.render(BASE, "female").tobytes() == first
    img = portrait.render(BASE, "female")
    assert img.size == (portrait.PW, portrait.PH) and img.mode == "RGB"


def test_the_portrait_uses_no_randomness_and_no_network(monkeypatch):
    def boom(*a, **k):
        raise AssertionError("not allowed while drawing")
    portrait.cache_clear()
    for name in ("random", "randint", "choice", "shuffle", "uniform", "getrandbits", "randrange", "sample", "gauss"):
        monkeypatch.setattr(random, name, boom)
    monkeypatch.setattr(random, "Random", boom)
    monkeypatch.setattr(socket, "socket", boom)
    monkeypatch.setattr(socket, "create_connection", boom)
    monkeypatch.setattr(socket, "getaddrinfo", boom)
    img = portrait.render(BASE, "male")
    out = idcard.render(data(), "fa")
    assert img.size == (portrait.PW, portrait.PH) and png(out).size == (idcard.W, idcard.H)


def test_a_damaged_set_of_traits_is_refused_not_drawn_as_someone_else():
    for bad in ({}, dict(BASE, build="enormous"), {k: v for k, v in BASE.items() if k != "height"}, dict(BASE, extra="x")):
        with pytest.raises(Exception):
            portrait.render(bad, "male")
    with pytest.raises(Exception):
        portrait.render(BASE, "other")


def test_every_combination_the_catalog_allows_can_be_drawn():
    """Each value of each trait on top of the other six at their extremes: nothing raises and nothing is blank."""
    for trait, spec in T.CATALOG.items():
        for code in spec["values"]:
            for gender in ("male", "female"):
                img = portrait.render(dict(BASE, **{trait: code}), gender)
                assert len(set(img.resize((24, 30)).getdata())) > 8


# ============================================================================================== the card picture
def test_the_card_is_a_png_of_a_fixed_size_and_the_same_every_time():
    d = data()
    a, b = idcard.render(d, "fa"), idcard.render(d, "fa")
    assert a == b and a[:8] == b"\x89PNG\r\n\x1a\n" and png(a).size == (idcard.W, idcard.H)
    assert idcard.render(d, "en") != a and idcard.render(d, "en") == idcard.render(d, "en")


@pytest.mark.parametrize("field,value", [
    ("first", "سارا"), ("last", "احمدی"), ("citizen_id", 13), ("age", 16), ("gender", "female"),
    ("birth", (2, "شهر دو", "City Two")), ("here", (2, "شهر دو", "City Two")), ("to", (2, "شهر دو", "City Two")),
])
@pytest.mark.parametrize("lang", ["fa", "en"])
def test_every_field_is_on_the_card(field, value, lang):
    base = idcard.render(data(), lang)
    changed = idcard.render(data(**{field: value}), lang)
    assert changed != base


@pytest.mark.parametrize("lang", ["fa", "en"])
def test_a_change_of_text_never_changes_the_portrait(lang):
    a = png(idcard.render(data(), lang))
    b = png(idcard.render(data(first="سارا", last="احمدی", citizen_id=9999, age=40, birth=(7, "x", "x"), here=(8, "y", "y")), lang))
    box = PORTRAIT_BOX if lang == "fa" else (idcard.W - 44 - 300, 136, idcard.W - 44, 136 + 366)
    assert a.crop(box).tobytes() == b.crop(box).tobytes()
    assert a.tobytes() != b.tobytes()


@pytest.mark.parametrize("lang", ["fa", "en"])
def test_a_change_of_traits_changes_the_portrait_only(lang):
    a = png(idcard.render(data(), lang))
    b = png(idcard.render(data({"skin_tone": "dark", "hair_color": "blond"}), lang))
    box = PORTRAIT_BOX if lang == "fa" else (idcard.W - 44 - 300, 136, idcard.W - 44, 136 + 366)
    outside = Image.composite(a, b, Image.new("L", a.size, 255))                      # (a itself; the next lines compare everything outside the box)
    for img in (a, b):
        img.paste((0, 0, 0), box)
    assert a.tobytes() == b.tobytes() == outside.tobytes() or a.tobytes() == b.tobytes()
    assert idcard.render(data(), lang) != idcard.render(data({"skin_tone": "dark", "hair_color": "blond"}), lang)


def test_while_travelling_the_card_does_not_show_the_city_they_left_as_the_current_one():
    on_road = data(to=(2, "شهر دو", "City Two"))
    for lang in ("fa", "en"):
        a = idcard.render(on_road, lang)
        b = idcard.render(data(to=(2, "شهر دو", "City Two"), here=(9, "جای دیگه", "Elsewhere")), lang)
        assert a == b                                                                 # where they set off from is not on the card
        assert a != idcard.render(data(), lang)


def test_very_long_names_and_cities_still_fit_the_card():
    long = "ا" * 60
    for lang in ("fa", "en"):
        out = idcard.render(data(first=long, last="ب" * 80, birth=(1, "ج" * 90, "J" * 90), here=(2, "د" * 90, "D" * 90)), lang)
        assert png(out).size == (idcard.W, idcard.H)
        img = png(out)
        edge = [img.getpixel((x, y)) for y in range(140, 520, 3) for x in (idcard.W - 40, idcard.W - 36)]        # nothing runs out of the card at the right edge
        assert max(max(p) for p in edge) < 120


def test_the_card_says_it_is_in_game_only():
    for lang in ("fa", "en"):
        assert "legal" in idcard.LABELS["en"]["foot"] and "فاقد اعتبار" in idcard.LABELS["fa"]["foot"]
    assert "فقط توی بازیه" in t("fa", "idc.caption", first="a", last="b") and "in-game" in t("en", "idc.caption", first="a", last="b")


# ============================================================================================== the command
def test_the_card_of_a_finished_character_comes_as_a_photo_built_from_the_saved_rows(h):
    ch = finish(h, A)
    calls = h.say(A, "کارت ملی")
    ph = photos(calls)
    assert len(ph) == 1
    d = idcard.build(h.db, A)
    assert pic(ph[0]) == idcard.render(d, "fa")
    assert d.first == ch["first_name"] and d.last == ch["last_name"] and d.citizen_id == ch["citizen_id"] and d.gender == "male"
    assert dict(d.traits) == T.traits_of(h.db, A) and d.age == C.CHARACTER_START_AGE
    w = intercity.city(h.db, ch["birth_world_id"])
    assert d.birth == (w["id"], w["name_fa"], w["name_en"]) and d.here == d.birth and d.to is None
    cap = ph[0].caption
    assert "آرمان" in cap and "کیانی" in cap and "کارت ملی" in cap
    assert png(pic(ph[0])).size == (idcard.W, idcard.H)


def test_the_portrait_on_the_card_is_the_one_drawn_from_the_saved_traits(h):
    finish(h, A, gender="female")
    d = idcard.build(h.db, A)
    expected = portrait.render(T.traits_of(h.db, A), "female").resize((300, 366), Image.LANCZOS)
    got = png(pic(photos(h.say(A, "کارت ملی"))[0])).crop(PORTRAIT_BOX)
    diff = sum(abs(p - q) for pa, pb in zip(expected.getdata(), got.getdata()) for p, q in zip(pa, pb)) / (300 * 366 * 3)
    assert diff < 6                                                                  # the same picture (its rounded corners and the PNG are all that differ)
    assert d.gender == "female"


@pytest.mark.parametrize("word", ["کارت ملی", "کارتملی", "کارت‌ملی", "id card", "idcard", "national id", "/idcard"])
def test_every_way_to_ask_gives_the_same_card(h, word):
    finish(h, A)
    ph = photos(h.say(A, word))
    assert len(ph) == 1 and pic(ph[0]) == idcard.render(idcard.build(h.db, A), "fa")


def test_the_same_card_in_every_group_every_repeat_and_after_a_restart(h, tmp_path):
    finish(h, A)
    first = pic(photos(h.say(A, "کارت ملی"))[0])
    assert pic(photos(h.say(A, "کارت ملی"))[0]) == first                                              # again
    assert pic(photos(h.say(A, "کارت ملی", chat=CH1))[0]) == first                                    # in the group of the character's city
    assert pic(photos(h.say(A, "کارت ملی", chat=CH2))[0]) == first                                    # in another group: the same character, the same card
    worldmap.cache_clear()
    portrait.cache_clear()
    assert pic(photos(h.say(A, "کارت ملی"))[0]) == first                                              # with every cache gone
    # a restart: a new connection to the same saved rows
    path = str(tmp_path / "game.db")
    d1 = Database(path)
    world.save_world(d1, CH1, layout(7))
    import charkit
    core.ensure_user(d1, A, None, "آرمان")
    charkit.complete(d1, A, first="آرمان", last="کیانی", world_id=1)
    png1 = idcard.render(idcard.build(d1, A, now=core.now()), "fa")
    d2 = Database(path)
    assert idcard.render(idcard.build(d2, A, now=core.now()), "fa") == png1


def test_two_players_get_their_own_cards(h):
    finish(h, A, first="آرمان", gender="male")
    finish(h, B, first="سارا", family="احمدی", gender="female")
    a, b = pic(photos(h.say(A, "کارت ملی"))[0]), pic(photos(h.say(B, "کارت ملی"))[0])
    assert a != b
    assert photos(h.say(A, "کارت ملی"))[0].caption != photos(h.say(B, "کارت ملی"))[0].caption


def test_asking_for_the_card_changes_nothing_that_is_saved(h):
    finish(h, A)
    tables = ("characters", "character_genes", "character_traits", "character_xp", "users", "intercity_trips", "worlds", "xp_ledger", "cash_ledger")
    def snap():
        out = {}
        for tb_ in tables:
            try:
                out[tb_] = [tuple(r) for r in h.db.qa(f"SELECT * FROM {tb_} ORDER BY 1")]
            except Exception:
                pass
        return out
    before = snap()
    for _ in range(3):
        h.say(A, "کارت ملی")
        h.say(A, "کارت ملی", chat=CH1)
    after = snap()
    users_b, users_a = before.pop("users"), after.pop("users")
    assert before == after                                                                         # (the user row itself may note activity; nothing of the character moves)
    assert len(users_b) == len(users_a)


def test_the_english_card_for_an_english_player(h):
    finish(h, A)
    h.db.x("UPDATE users SET lang='en' WHERE id=?", (A,))
    ph = photos(h.say(A, "id card"))
    assert len(ph) == 1 and pic(ph[0]) == idcard.render(idcard.build(h.db, A), "en")
    assert "National ID" in ph[0].caption and pic(ph[0]) != idcard.render(idcard.build(h.db, A), "fa")


# ============================================================================================== the age on the card
def test_the_age_on_the_card_is_the_current_age(h):
    ch = finish(h, A)
    start = idcard.build(h.db, A).age
    assert start == C.CHARACTER_START_AGE
    tb.T[0] += 4 * 86400 + 5
    older = idcard.build(h.db, A)
    assert older.age == start + 1
    assert pic(photos(h.say(A, "کارت ملی"))[0]) == idcard.render(older, "fa")
    assert idcard.build(h.db, A, now=ch["age_started_at"]).age == start


# ============================================================================================== the card and a journey between cities
def test_during_a_journey_the_card_names_the_destination_and_after_it_the_new_city(h):
    ch = finish(h, A)
    h.say(B, "مپ", chat=CH2)                                                                          # (makes city 2 exist for a player's eyes; A's own city stays 1)
    home = ch["birth_world_id"]
    dest = 2 if home == 1 else 1
    before = pic(photos(h.say(A, "کارت ملی"))[0])
    h.say(A, f"سفر {dest}", chat=CH1)
    assert intercity.active(h.db, A) is not None
    mid = idcard.build(h.db, A)
    assert mid.to is not None and mid.to[0] == dest and mid.birth[0] == home
    on_road = pic(photos(h.say(A, "کارت ملی"))[0])
    assert on_road != before and on_road == idcard.render(mid, "fa")
    tb.T[0] += 300                                                                                    # half way: still on the road, still not the new city
    assert idcard.build(h.db, A).to is not None
    assert pic(photos(h.say(A, "کارت ملی"))[0]) == idcard.render(idcard.build(h.db, A), "fa") != before
    tb.T[0] += 301                                                                                    # the journey is over: applied before the card is made
    arrived = pic(photos(h.say(A, "کارت ملی"))[0])
    after = idcard.build(h.db, A)
    assert after.to is None and after.here[0] == dest and after.birth[0] == home
    assert arrived == idcard.render(after, "fa") and arrived != on_road and arrived != before
    assert character.get(h.db, A)["birth_world_id"] == home                                          # the birthplace never changes
    assert png(arrived).crop(PORTRAIT_BOX).tobytes() == png(before).crop(PORTRAIT_BOX).tobytes()    # and neither does the face


def test_a_card_after_a_journey_is_the_same_every_time(h):
    finish(h, A)
    h.say(A, "سفر 2", chat=CH1) if character.get(h.db, A)["birth_world_id"] != 2 else h.say(A, "سفر 1", chat=CH2)
    tb.T[0] += 700
    one = pic(photos(h.say(A, "کارت ملی"))[0])
    assert pic(photos(h.say(A, "کارت ملی", chat=CH2))[0]) == one == pic(photos(h.say(A, "کارت ملی", chat=CH1))[0])


# ============================================================================================== who gets a card
def test_a_character_that_is_not_finished_gets_the_set_up_and_never_a_card(h):
    h.say(A, "مپ", chat=CH1)                                                                         # a pending profile
    out = h.say(A, "کارت ملی", chat=CH1)
    assert not photos(out)
    assert t("fa", "char.need") in tb.Harness.texts(out)                                              # in a group: the button that opens the private chat
    priv = h.say(A, "کارت ملی")
    assert not photos(priv)
    assert t("fa", "char.s.first", min=C.CHAR_NAME_MIN, max=C.CHAR_NAME_MAX) in tb.Harness.texts(priv)  # in private: the set-up itself, where it stopped
    h.say(A, "/start char")
    h.say(A, "آرمان")
    mid = h.say(A, "کارت ملی")
    assert not photos(mid) and "آرمان" in tb.Harness.texts(mid)                                      # a name already given is kept; the set-up continues
    h.say(A, "کیانی")
    h.press(A, f"cr:g:male:{A}")
    assert len(photos(h.say(A, "کارت ملی"))) == 1                                                    # finished: now there is a card


def test_nothing_is_drawn_for_a_character_that_is_not_finished(h):
    h.say(A, "مپ", chat=CH1)
    with pytest.raises(core.GameError) as e:
        idcard.build(h.db, A)
    assert e.value.code == "card_char"
    with pytest.raises(core.GameError) as e2:
        idcard.build(h.db, 424242)
    assert e2.value.code == "char_none"


def test_a_finished_character_without_saved_traits_is_reported_not_drawn(h):
    finish(h, A)
    h.db.x("DROP TRIGGER IF EXISTS character_traits_no_delete")
    for trg in [r["name"] for r in h.db.qa("SELECT name FROM sqlite_master WHERE type='trigger' AND tbl_name='character_traits'")]:
        h.db.x(f"DROP TRIGGER {trg}")
    h.db.x("DELETE FROM character_traits WHERE user_id=?", (A,))
    with pytest.raises(core.GameError) as e:
        idcard.build(h.db, A)
    assert e.value.code == "card_traits"
    out = h.say(A, "کارت ملی")
    assert not photos(out) and t("fa", "e.card_traits") in tb.Harness.texts(out)


# ============================================================================================== entry points
def test_the_command_words_belong_to_the_card_and_the_old_card_word_still_shares(h):
    for word in ALIASES["idcard"]:
        p = textparse.parse(word)
        assert p is not None and p.cmd == "idcard", word
    assert textparse.parse("کارت").cmd == "share" and textparse.parse("کارت من").cmd == "share"
    assert textparse.parse("card").cmd == "share"


def test_in_a_group_only_the_exact_words_count_as_the_command(h):
    finish(h, A)
    assert len(photos(h.say(A, "کارت ملی", chat=CH1))) == 1
    for chat_text in ("کارت ملی من گم شد", "کارت ملی رو دیدی؟", "id card please"):
        assert not photos(h.say(A, chat_text, chat=CH1)) and not tb.Harness.texts(h.say(A, chat_text, chat=CH1))


def test_the_main_menu_has_the_card_button_and_it_opens_the_same_card(h):
    finish(h, A)
    out = h.say(A, "منو")
    data_ = mapkit.buttons_of(out[-1])
    assert (t("fa", "menu.idcard"), f"mn:go:idcard:{A}") in data_
    assert (t("fa", "menu.cities"), f"mn:go:cities:{A}") in data_
    calls = h.press(A, f"mn:go:idcard:{A}")
    ph = photos(calls)
    assert len(ph) == 1 and pic(ph[0]) == idcard.render(idcard.build(h.db, A), "fa")
    assert not [c for c in calls if isinstance(c, (tb.EditMessageText, tb.EditMessageMedia))]          # like the map: the card is a new message, the menu above it stays as it was


def test_somebody_elses_menu_button_does_nothing(h):
    finish(h, A)
    finish(h, B, first="سارا", family="احمدی")
    calls = h.press(B, f"mn:go:idcard:{A}")
    assert not photos(calls) and not [c for c in calls if isinstance(c, tb.EditMessageMedia)]


def test_the_card_has_a_help_entry_in_both_languages():
    from hypemarket import docs
    fa_desc, fa_ex, en_desc, en_ex = docs.DOCS["idcard"]
    assert fa_desc and en_desc and "کارت ملی" in fa_ex and "id card" in en_ex
    assert "idcard" in dict(docs.CATEGORIES)["start"]
