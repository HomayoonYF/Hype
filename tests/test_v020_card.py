"""v0.20: the two-sided national ID card, `کارت ملی`, as TEXT.

There is no picture: each side is a message written from the character's SAVED rows (identity.dossier). Where a photo would be, everybody has the same blank default («مهمان» / GUEST).
Front: full name, citizen ID, gender, birthday, age, height, birthplace, nationality, current city, status, the appearance (skin tone, hair, eyes, build, face shape), character level and XP with a
progress bar and the XP still needed. Back: home address (city, district, street, building), languages, fingerprint ID, signature, issue and expiry dates, issuing authority and document number.
These tests cover every field, both languages, the blank photo, the XP bar, the in-world wording of what is not assigned yet, the journey state, determinism, HTML safety and that rendering
changes nothing. What they cannot cover: how the text looks on a real phone (fonts, the monospace frame of the blank photo) and the real arabic-reshaper / python-bidi behaviour: none of it is drawn
any more, but Telegram does the right-to-left layout itself."""
import ast
import dataclasses
import inspect
import re

import pytest

import charkit
import idkit
from idkit import A, B, DISCLAIMERS, snapshot
from hypemarket import charxp, config as C, core, fmt, identity, idcard, traits as T
from hypemarket.db import Database

T0 = 1_800_000_000
FRONT_FIELDS_FA = ("نام کامل", "کد شهروندی", "جنسیت", "تاریخ تولد", "سن", "قد", "زادگاه", "ملیت", "شهر فعلی", "وضعیت شهروندی", "مشخصات ظاهری", "سطح", "تجربه")
FRONT_FIELDS_EN = ("Full name", "Citizen ID", "Gender", "Date of birth", "Age", "Height", "Birthplace", "Nationality", "Current city", "Citizen status", "Appearance", "Level", "XP")
BACK_FIELDS_FA = ("نشانی محل سکونت", "شهر", "محله", "خیابان", "ساختمان", "زبان‌های شناخته‌شده", "شناسه‌ی اثر انگشت", "امضای دارنده", "تاریخ صدور", "تاریخ انقضا", "مرجع صدور", "شناسه‌ی سند")
BACK_FIELDS_EN = ("Home address", "City", "District", "Street", "Building", "Known languages", "Fingerprint ID", "signature", "Issued", "Expires", "Issued by", "Document no.")


@pytest.fixture(scope="module")
def world():
    """Two finished characters in a memory database: (database, dossier of the first at the middle of its second year)."""
    d = Database(":memory:")
    w1, w2 = charkit.fake_world(d, -1, "One"), charkit.fake_world(d, -2, "Two")
    for uid, first, last, gender in ((1, "آرمان", "کیانی", "male"), (2, "Sam", "Lee", "female")):
        core.ensure_user(d, uid, None, "x")
        charkit.complete(d, uid, first=first, last=last, gender=gender, world_id=w1, now=T0)
    return d, w1, w2


@pytest.fixture()
def d1(world):
    return identity.dossier(world[0], 1, T0 + 9 * 86400)


@pytest.fixture()
def d2(world):
    return identity.dossier(world[0], 2, T0 + 9 * 86400)


def plain(text):
    """The text without its HTML tags."""
    return re.sub(r"<[^>]+>", "", text)


# ================================================================================================ both sides, every field
@pytest.mark.parametrize("lang,fields", [("fa", FRONT_FIELDS_FA), ("en", FRONT_FIELDS_EN)])
def test_the_front_has_every_field(d1, lang, fields):
    text = plain(idcard.front(lang, d1))
    for f in fields:
        assert f in text, f
    assert d1.name in text and fmt.loc(str(d1.citizen_id), lang) in text and fmt.num(d1.height_cm, lang) in text
    assert fmt.date(d1.born_at, lang) in text and idcard.city_text(lang, d1.birth) in text and idcard.city_text(lang, d1.current) in text
    assert idcard.gender_name(lang, d1.gender) in text and identity.nationality_name(lang, d1.nationality) in text and identity.status_name(lang, d1.status) in text


@pytest.mark.parametrize("lang,fields", [("fa", BACK_FIELDS_FA), ("en", BACK_FIELDS_EN)])
def test_the_back_has_every_field(d1, lang, fields):
    text = plain(idcard.back(lang, d1))
    for f in fields:
        assert f in text, f
    assert d1.fingerprint in text and fmt.date(d1.issued_at, lang) in text and fmt.date(d1.expires_at, lang) in text
    assert f"{d1.doc_number[:3]} {d1.doc_number[3:6]} {d1.doc_number[6:]}" in text and identity.authority_name(lang, d1.authority) in text
    assert ", ".join(identity.language_names("en", d1.languages)) in text if lang == "en" else "فارسی" in text
    assert idcard.signature_text(d1) in text


def test_the_two_sides_and_the_two_languages_are_different_texts(d1):
    t = {(lang, side): idcard.render(d1, lang, side) for lang in ("fa", "en") for side in ("front", "back")}
    assert len(set(t.values())) == 4
    assert "1 / 2" in plain(t["fa", "front"]) and "2 / 2" in plain(t["fa", "back"]) and "1 / 2" in plain(t["en", "front"]) and "2 / 2" in plain(t["en", "back"])
    assert not re.search("[؀-ۿ]", plain(t["en", "front"]).replace(d1.name, "")) and not re.search("[؀-ۿ]", plain(t["en", "back"]).replace(idcard.signature_text(d1), ""))
    assert idcard.render(d1, "fa", "front") == idcard.front("fa", d1) and idcard.render(d1, "en", "back") == idcard.back("en", d1)


def test_everything_the_card_says_is_read_from_the_saved_character(d1, d2):
    a, b = idcard.front("fa", d1), idcard.front("fa", d2)
    assert a != b and d2.name in b and d1.name not in b
    assert idcard.back("fa", d1) != idcard.back("fa", d2)                                  # the fingerprint, the document number and the signature are each person's own
    assert d1.fingerprint != d2.fingerprint and d1.doc_number != d2.doc_number


# ================================================================================================ no picture, one blank photo for everybody
def test_there_is_no_picture_and_the_blank_photo_is_the_same_for_everybody(d1, d2):
    for d in (d1, d2):
        for lang in ("fa", "en"):
            text = idcard.front(lang, d)
            assert isinstance(text, str) and f"<pre>{idcard.GUEST}</pre>" in text
    assert "GUEST" in idcard.GUEST and idcard.GUEST.count("\n") == 5
    traits_a = dict(d1.traits)
    other = {k: T.CATALOG[k]["values"][-1] for k in T.TRAIT_NAMES}
    d3 = dataclasses.replace(d1, traits=tuple((k, other[k]) for k, _ in d1.traits), gender="female")
    assert idcard.front("en", d3).split("Photo: guest")[0].split("<pre>")[1] == idcard.front("en", d1).split("Photo: guest")[0].split("<pre>")[1]     # whatever the genes, the same blank photo
    assert traits_a != other


def test_the_card_module_draws_nothing_and_the_portrait_is_gone():
    tree = ast.parse(inspect.getsource(idcard))
    imported = {(n.module if isinstance(n, ast.ImportFrom) else a.name) for n in ast.walk(tree) for a in (getattr(n, "names", None) or [None]) if isinstance(n, (ast.Import, ast.ImportFrom))}
    assert imported <= {"__future__", None, "texts"} | {"."}, imported                           # only the game's own text and format helpers: no imaging library, no random numbers, no clock
    names = {a.name for n in ast.walk(tree) if isinstance(n, ast.ImportFrom) for a in n.names}
    assert names <= {"annotations", "fmt", "identity", "t"}, names
    with pytest.raises(ImportError):
        import hypemarket.portrait  # noqa: F401
    with pytest.raises(ImportError):
        import hypemarket.cardart  # noqa: F401
    assert not hasattr(idcard, "render_front") and not hasattr(idcard, "record_picture")


def test_the_card_never_carries_a_disclaimer(d1):
    for lang in ("fa", "en"):
        for side in ("front", "back"):
            text = idcard.render(d1, lang, side)
            for bad in DISCLAIMERS:
                assert bad not in text, (lang, side, bad)


# ================================================================================================ the appearance words
@pytest.mark.parametrize("trait", [n for n in T.TRAIT_NAMES if n != "height"])         # the height is the number of centimetres, tested below
def test_every_value_of_every_trait_is_named_on_the_front(d1, trait):
    key = {"skin_tone": "skin", "hair_color": "hair", "hair_style": "hair", "eye_color": "eyes", "build": "build", "face_shape": "face"}[trait]
    for lang in ("fa", "en"):
        fronts = set()
        for code in T.CATALOG[trait]["values"]:
            d = dataclasses.replace(d1, traits=tuple((k, code if k == trait else v) for k, v in d1.traits))
            word = identity.trait_value(lang, trait, code)
            text = idcard.front(lang, d)
            assert word and (lang == "en" or word != code) and word in idcard.traits_line(lang, d)[key] and f"<b>{idcard.traits_line(lang, d)[key]}</b>" in text, (trait, code, lang)
            fronts.add(text)
        assert len(fronts) == len(T.CATALOG[trait]["values"]), (trait, lang)                  # every value reads differently: no two values share a word


def test_the_height_in_centimetres_is_the_saved_one(d1, d2):
    assert f"{d1.height_cm} cm" in plain(idcard.front("en", d1)) and f"{d2.height_cm} cm" in plain(idcard.front("en", d2))
    assert T.traits_of is not None and C.HEIGHT_BANDS_CM[d1.gender][dict(d1.traits)["height"]] <= d1.height_cm < C.HEIGHT_BANDS_CM[d1.gender][dict(d1.traits)["height"]] + 10


# ================================================================================================ the XP bar
def test_a_new_character_is_level_one_with_an_empty_visible_bar(d1):
    text = idcard.front("fa", d1)
    assert d1.level == 1 and d1.xp == 0
    assert "▱" * 12 in text and "▰" not in text                                             # always drawn, empty at 0 XP
    assert f"{fmt.num(100, 'fa')} XP" in plain(text) and f"سطح {fmt.num(2, 'fa')}" in plain(text)         # the XP still needed for the next level


def test_the_bar_fills_with_the_xp_and_the_needed_amount_is_exact(world):
    d, w1, w2 = world
    core.ensure_user(d, 3, None, "x")
    charkit.complete(d, 3, first="Xp", last="Test", world_id=w1, now=T0)
    seen = []
    for total, level in ((0, 1), (50, 1), (99, 1), (100, 2), (250, 2), (399, 2), (400, 3), (900, 4)):
        d.x("UPDATE character_xp SET xp=? WHERE user_id=3", (total,))
        dos = identity.dossier(d, 3, T0 + 5)
        text = idcard.front("en", dos)
        frac = dos.xp_fraction
        assert dos.level == level and text.count("▰") == int(round(frac * 12)) and text.count("▰") + text.count("▱") == 12
        assert f"{dos.xp_left} XP to level {level + 1}" in plain(text) and f"Level: {level}" in plain(text) and f"XP: {total}" in plain(text)
        seen.append((level, text.count("▰")))
    for lv in (1, 2, 3, 4):                                                                 # inside one level, more XP never shows fewer segments
        segs = [n for l, n in seen if l == lv]
        assert segs == sorted(segs)


# ================================================================================================ what is not assigned, journeys, the home
def test_an_unassigned_address_is_said_in_the_documents_own_words_and_never_invented(d1):
    for lang, word in (("fa", "تخصیص‌نیافته"), ("en", "Unassigned")):
        text = plain(idcard.back(lang, d1))
        assert text.count(word) == 3                                                         # district, street, building
    assert idcard.address_parts("fa", d1)[1:] == (None, None, None, False) and idcard.address_detail("fa", d1) == idcard.LABELS["fa"]["addr_none"]
    assigned = dataclasses.replace(d1, district="Hill", street="Oak Road", building="12")
    text = plain(idcard.back("en", assigned))
    assert "Hill" in text and "Oak Road" in text and "12" in text and "Unassigned" not in text
    assert idcard.address_parts("en", assigned)[4] is True and idcard.address_detail("en", assigned) == "Hill, Oak Road, 12"


def test_the_card_names_the_city_and_during_a_journey_the_destination(world, d1):
    d, w1, w2 = world
    assert idcard.city_text("fa", d1.current) in idcard.front("fa", d1)
    away = dataclasses.replace(d1, current=None, to=(w2, "دو", "Two"))
    assert "در سفر به" in idcard.front("fa", away) and "Travelling to" in idcard.front("en", away) and "Two" in idcard.front("en", away)
    assert idcard.city_text("fa", away.birth) in idcard.front("fa", away)                    # the birthplace never changes
    assert idcard.city_text("fa", None) == idcard.LABELS["fa"]["unset"] and idcard.city_text("en", None) == idcard.LABELS["en"]["unset"]


# ================================================================================================ names, escaping, size
@pytest.mark.parametrize("first,last", [("<b>x</b>", "&amp;"), ("A" * 40, "B" * 40), ("علی‌اکبر", "حسینی‌نژاد"), ("Zoë", "O'Neil")])
def test_names_are_escaped_and_the_text_stays_valid_and_short(world, first, last):
    d, w1, w2 = world
    uid = 100 + len(first) + len(last) + ord(first[0])
    core.ensure_user(d, uid, None, "x")
    charkit.complete(d, uid, first=first, last=last, world_id=w1, now=T0)
    dos = identity.dossier(d, uid, T0 + 5)
    for lang in ("fa", "en"):
        for side in ("front", "back"):
            text = idcard.render(dos, lang, side)
            assert len(text) < 4096
            assert not re.search(r"<(?!/?(b|i|code|pre)>)", text)                             # only the allowed Telegram tags: every other < in a name is escaped
            assert not re.search(r"&(?!(amp|lt|gt|quot|#\d+);)", text)                         # and every & is an entity
            assert text.count("<b>") == text.count("</b>") and text.count("<i>") == text.count("</i>") and text.count("<code>") == text.count("</code>") and text.count("<pre>") == 1 * (side == "front")
    if first.startswith("<"):
        assert "&lt;b&gt;x&lt;/b&gt;" in idcard.front("en", dos) and "&amp;amp;" in idcard.front("en", dos)


# ================================================================================================ the signature
def test_the_signature_is_stable_and_comes_from_the_saved_name(d1, d2):
    s1, s2 = idcard.signature_text(d1), idcard.signature_text(d2)
    assert s1 == idcard.signature_text(d1) and s1 != s2 and s1.startswith("آ. کیانی ") and s2.startswith("S. Lee ")
    assert s1.rstrip("∼~≈⁓ ") == "آ. کیانی" and s1.split()[-1] in {f * n for f in idcard.FLOURISH for n in (2, 3, 4)}
    other = dataclasses.replace(d1, signature_seed="0" * 16)
    assert idcard.signature_text(other) != s1 or idcard.FLOURISH[0] * 2 == s1.split()[-1]


# ================================================================================================ rendering changes nothing
def test_showing_the_card_changes_nothing(world):
    d, w1, w2 = world
    tables = idkit.CHAR_TABLES + ("users", "life_accounts", "life_ledger")
    before = snapshot(d, tables)
    for uid in (1, 2):
        dos = identity.dossier(d, uid, T0 + 9 * 86400)
        for lang in ("fa", "en"):
            for side in ("front", "back"):
                assert idcard.render(dos, lang, side) == idcard.render(dos, lang, side)
    assert snapshot(d, tables) == before
