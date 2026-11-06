"""v0.20: the character hub `شخصیت` and its six sections, driven through the real handlers (a fake Telegram records what the bot sent).

Every screen is TEXT (no pictures anywhere: the ID card is a text document and everybody has the same blank «guest» photo).
What they cover: the hub and every section (the card with its two sides, the profile, the appearance, the genetics, the personal information, the progress), that each shows what is
SAVED and accurate (XP, level, dates, traits, genes), that every screen has a way back, that nobody can press somebody else's buttons, that pressing again changes nothing, that no
button is dead, and that no out-of-character disclaimer is left. What they cannot cover: how Telegram shows the messages on a real phone."""
import pytest

import idkit
import test_bot as tb
from idkit import A, B, CH1, CH2, DISCLAIMERS, buttons, finish, kinds, of, photos
from hypemarket import character, charxp, charhub, config as C, core, fmt, identity, idcard, textparse, traits as T
from hypemarket.texts import ALIASES, t

REAL_CHARACTER_SETUP = True


@pytest.fixture()
def h():
    return idkit.make_harness()


def answers(calls):
    return of(calls, "AnswerCallbackQuery")


def toast(calls):
    a = answers(calls)
    return a[-1].text if a and a[-1].text else None


def all_text(calls):
    return tb.Harness.texts(calls)


# ============================================================================================== the hub
@pytest.mark.parametrize("word", ["شخصیت", "character", "/character"])
def test_the_hub_opens_with_the_six_sections_and_a_way_back(h, word):
    ch = finish(h, A)
    out = h.say(A, word)
    msg = of(out, "SendMessage")[-1]
    d = identity.dossier(h.db, A)
    assert d.name in msg.text and fmt.loc(str(ch["citizen_id"]), "fa") in msg.text
    assert buttons(msg) == [(t("fa", "cg.b.card"), f"cg:card:{A}"), (t("fa", "cg.b.prof"), f"cg:prof:{A}"), (t("fa", "cg.b.look"), f"cg:look:{A}"), (t("fa", "cg.b.gene"), f"cg:gene:{A}"),
                            (t("fa", "cg.b.pers"), f"cg:pers:{A}"), (t("fa", "cg.b.prog"), f"cg:prog:{A}"), (t("fa", "cg.b.inv"), f"iv:m:{A}"),
                            (t("fa", "menu.back"), f"mn:menu:{A}")]


def test_the_words_belong_to_the_hub_and_the_card_word_still_opens_the_card(h):
    for word in ALIASES["character"]:
        p = textparse.parse(word)
        assert p is not None and p.cmd == "character", word
    assert textparse.parse("کارت ملی").cmd == "idcard"


def test_in_a_group_only_the_exact_word_counts(h):
    finish(h, A)
    assert of(h.say(A, "شخصیت", chat=CH1), "SendMessage")
    for text in ("شخصیت من چطوره", "character please"):
        assert not tb.Harness.texts(h.say(A, text, chat=CH1))


def test_the_hub_of_an_unfinished_character_is_the_set_up(h):
    h.say(A, "مپ", chat=CH1)
    grp = h.say(A, "شخصیت", chat=CH1)
    assert t("fa", "char.need") in all_text(grp) and not photos(grp)
    priv = h.say(A, "شخصیت")
    assert t("fa", "char.s.first", min=C.CHAR_NAME_MIN, max=C.CHAR_NAME_MAX) in all_text(priv)


def test_the_main_menu_opens_the_hub_in_place_with_one_back_button(h):
    finish(h, A)
    menu = h.say(A, "منو")
    assert (t("fa", "menu.character"), f"mn:go:character:{A}") in buttons(of(menu, "SendMessage")[-1])
    out = h.press(A, f"mn:go:character:{A}")
    edit = of(out, "EditMessageText")
    assert len(edit) == 1 and "شخصیت" in edit[0].text
    b = buttons(edit[0])
    assert b.count((t("fa", "menu.back"), f"mn:menu:{A}")) == 1                           # the hub's own way back; the menu does not add a second one
    back = h.press(A, f"mn:menu:{A}")
    assert t("fa", "menu.title", brand=t("fa", "brand")) in all_text(back)              # and it does lead back to the main menu


# ============================================================================================== the sections show what is saved
def test_the_profile_shows_every_saved_fact(h):
    ch = finish(h, A, gender="female")
    out = h.press(A, f"cg:prof:{A}")
    d = identity.dossier(h.db, A)
    text = of(out, "EditMessageText")[0].text
    for must in (d.name, fmt.loc(str(d.citizen_id), "fa"), "زن", fmt.date(d.born_at, "fa"), fmt.num(d.age, "fa"), identity.nationality_name("fa", d.nationality), idcard.city_text("fa", d.birth),
                 idcard.city_text("fa", d.current), identity.status_name("fa", "active")):
        assert must in text, must
    assert t("fa", "cg.addr.none") in text                                               # no street or building was ever assigned: it says so and invents nothing
    assert d.birth == d.current == d.home and ch["birth_world_id"] == d.birth[0]


def test_the_appearance_shows_the_blank_guest_photo_and_every_saved_trait(h):
    finish(h, A, gender="female")
    out = h.press(A, f"cg:look:{A}")                                                       # a text screen, edited in place: no picture is sent
    d = identity.dossier(h.db, A)
    assert len(of(out, "EditMessageText")) == 1 and not photos(out) and not of(out, "DeleteMessage") and not of(out, "EditMessageMedia")
    text = of(out, "EditMessageText")[0].text
    assert f"<pre>{idcard.GUEST}</pre>" in text and "GUEST" in text                       # the same blank default photo for everybody
    for k, code in d.traits:
        assert t("fa", f"tr.{k}.{code}") in text, (k, code)
    for k in T.TRAIT_NAMES:
        assert t("fa", f"tr.n.{k}") in text, k
    assert fmt.num(d.height_cm, "fa") in text and charhub.trait_value("fa", "skin_tone", dict(d.traits)["skin_tone"]) in text
    assert text == charhub.look_reply("fa", d, A).text


def test_the_genetics_show_both_genes_of_every_trait_and_the_result(h):
    finish(h, A)
    out = h.press(A, f"cg:gene:{A}")
    text = of(out, "EditMessageText")[0].text
    genes, res = T.genes_of(h.db, A), T.traits_of(h.db, A)
    for trait in T.TRAIT_NAMES:
        a1, a2 = genes[trait]
        line = next(ln for ln in text.split("\n") if t("fa", f"tr.n.{trait}") in ln and "←" in ln)
        assert t("fa", f"tr.{trait}.{a1}") in line and t("fa", f"tr.{trait}.{a2}") in line and t("fa", f"tr.{trait}.{res[trait]}") in line
    assert text.count("←") == len(T.TRAIT_NAMES)


def test_the_personal_information_has_every_field_and_the_unassigned_state(h):
    finish(h, A)
    out = h.press(A, f"cg:pers:{A}")
    d = identity.dossier(h.db, A)
    assert not photos(out) and len(of(out, "EditMessageText")) == 1
    text = of(out, "EditMessageText")[0].text
    for must in (identity.nationality_name("fa", d.nationality), "فارسی", d.fingerprint, fmt.date(d.issued_at, "fa"), fmt.date(d.expires_at, "fa"), identity.authority_name("fa", d.authority),
                 d.doc_number[:3], idcard.city_text("fa", d.home), idcard.signature_text(d)):
        assert must in text, must
    assert text.count(t("fa", "cg.unassigned")) == 3                                       # district, street, building: nobody has assigned them, and none is invented


def test_the_progress_screen_is_exact_for_a_new_character_and_after_xp(h):
    finish(h, A)
    text = of(h.press(A, f"cg:prog:{A}"), "EditMessageText")[0].text
    assert "0 XP" in text or "۰ XP" in text
    assert f"{fmt.num(0, 'fa')} / {fmt.num(100, 'fa')} XP" in text and fmt.num(100, "fa") in text and "▱▱▱▱▱▱▱▱▱▱▱▱" in text and f"{fmt.loc('0', 'fa')}٪" in text
    charxp.grant(h.db, A, 250, "test", "t1")                                               # level 2 starts at 100, level 3 at 400
    d = identity.dossier(h.db, A)
    assert (d.level, d.xp, d.xp_floor, d.xp_next, d.xp_left, d.xp_into, d.xp_span) == (2, 250, 100, 400, 150, 150, 300)
    text = of(h.press(A, f"cg:prog:{A}"), "EditMessageText")[0].text
    for must in (fmt.num(250, "fa"), f"{fmt.num(150, 'fa')} / {fmt.num(300, 'fa')}", fmt.num(150, "fa"), fmt.num(3, "fa"), "▰▰▰▰▰▰▱▱▱▱▱▱"):
        assert must in text, must
    assert charxp.level_of(h.db, A) == 2 and core.get_user(h.db, A)["cash"] is not None        # the stock progression is a different thing and was not touched


def test_the_hub_shows_the_current_level_and_age(h):
    finish(h, A)
    charxp.grant(h.db, A, 400, "test", "t2")
    text = of(h.say(A, "شخصیت"), "SendMessage")[-1].text
    assert f"{fmt.num(C.CHARACTER_START_AGE, 'fa')}" in text and fmt.num(3, "fa") in text
    tb.T[0] += 4 * 86400 + 1
    assert fmt.num(C.CHARACTER_START_AGE + 1, "fa") in of(h.say(A, "شخصیت"), "SendMessage")[-1].text


# ============================================================================================== the ID card with its two sides
def test_the_card_command_opens_the_front_with_its_navigation(h):
    finish(h, A)
    out = h.say(A, "کارت ملی")
    msg = of(out, "SendMessage")[-1]
    d = identity.dossier(h.db, A)
    assert not photos(out) and msg.text == idcard.render(d, "fa", "front")                 # the card is a text message: no picture
    assert buttons(msg) == [(t("fa", "cg.b.front_on"), f"cg:nf:{A}"), (t("fa", "cg.b.rear"), f"cg:cardb:{A}"), (t("fa", "cg.b.hub"), f"cg:hub:{A}")]
    assert "کارت ملی" in msg.text and d.first in msg.text and f"<pre>{idcard.GUEST}</pre>" in msg.text


def test_the_sides_switch_by_editing_the_same_message_one_message_per_click(h):
    finish(h, A)
    d = identity.dossier(h.db, A)
    back = h.press(A, f"cg:cardb:{A}")
    assert len(of(back, "EditMessageText")) == 1 and not photos(back) and not of(back, "DeleteMessage") and not of(back, "SendMessage")      # one message, in place
    assert buttons(of(back, "EditMessageText")[0]) == [(t("fa", "cg.b.front"), f"cg:card:{A}"), (t("fa", "cg.b.rear_on"), f"cg:nr:{A}"), (t("fa", "cg.b.hub"), f"cg:hub:{A}")]
    assert of(back, "EditMessageText")[0].text == idcard.render(d, "fa", "back")           # the reverse, exactly as written from the saved rows
    front = h.press(A, f"cg:card:{A}")
    assert len(of(front, "EditMessageText")) == 1 and of(front, "EditMessageText")[0].text == idcard.render(d, "fa", "front")
    assert idcard.render(d, "fa", "front") != idcard.render(d, "fa", "back")


def test_the_side_that_is_showing_only_answers_with_a_note(h):
    finish(h, A)
    for sub in ("nf", "nr"):
        out = h.press(A, f"cg:{sub}:{A}")
        assert toast(out) == t("fa", "cg.noop") and not of(out, "EditMessageText") and not photos(out) and not of(out, "DeleteMessage")


def test_back_from_the_card_edits_the_message_into_the_hub(h):
    finish(h, A)
    out = h.press(A, f"cg:hub:{A}")
    assert len(of(out, "EditMessageText")) == 1 and "شخصیت" in of(out, "EditMessageText")[0].text and not of(out, "DeleteMessage") and not photos(out)


def test_opened_from_a_picture_message_the_screen_replaces_it(h):
    """If the button sits on a picture message (an older message), the text screen replaces it instead of failing."""
    finish(h, A)
    out = h.press(A, f"cg:hub:{A}", photo=True)
    assert of(out, "DeleteMessage") and len(of(out, "SendMessage")) == 1 and "شخصیت" in of(out, "SendMessage")[0].text and not photos(out)


# ============================================================================================== safety of the buttons
def test_nobody_else_can_press_a_character_button(h):
    finish(h, A)
    finish(h, B, first="سارا", family="احمدی", gender="female")
    for sub in ("hub", "card", "cardb", "prof", "look", "gene", "pers", "prog", "nf", "nr"):
        out = h.press(B, f"cg:{sub}:{A}")
        assert toast(out) == t("fa", "not_yours")
        assert not photos(out) and not of(out, "EditMessageText") and not of(out, "EditMessageMedia") and not of(out, "DeleteMessage") and not of(out, "SendMessage")


@pytest.mark.parametrize("data", ["cg:evil:{u}", "cg:prof:extra:{u}", "cg::{u}", "cg:{u}", "cg:../../x:{u}", "cg:HUB:{u}"])
def test_a_hand_made_button_does_nothing(h, data):
    finish(h, A)
    out = h.press(A, data.format(u=A))
    assert not photos(out) and not of(out, "EditMessageText") and not of(out, "EditMessageMedia") and not of(out, "DeleteMessage")


def test_pressing_a_screen_again_draws_the_same_screen_and_changes_nothing(h):
    finish(h, A)
    before = idkit.snapshot(h.db, idkit.CHAR_TABLES)
    first = {}
    for sub in ("prof", "gene", "prog"):
        first[sub] = of(h.press(A, f"cg:{sub}:{A}"), "EditMessageText")[0].text
    for _ in range(3):
        for sub in ("prof", "gene", "prog"):
            assert of(h.press(A, f"cg:{sub}:{A}"), "EditMessageText")[0].text == first[sub]
        h.say(A, "شخصیت")
        h.say(A, "کارت ملی")
        h.press(A, f"cg:cardb:{A}")
        h.press(A, f"cg:look:{A}")
        h.press(A, f"cg:pers:{A}")
    assert idkit.snapshot(h.db, idkit.CHAR_TABLES) == before


def test_every_button_of_every_screen_works_and_leads_somewhere(h):
    """Walk the whole tree from the hub: every button answers, every screen has a way back, the walk ends, and no screen is a picture."""
    finish(h, A)
    seen, todo = set(), [f"cg:hub:{A}"]
    while todo:
        data = todo.pop()
        if data in seen:
            continue
        seen.add(data)
        out = h.press(A, data)
        assert answers(out), data
        assert not photos(out) and not of(out, "EditMessageMedia") and not of(out, "DeleteMessage"), data
        shown = of(out, "EditMessageText")
        if data.split(":")[1] in ("nf", "nr"):
            assert toast(out) == t("fa", "cg.noop")
            continue
        assert len(shown) == 1, f"a dead button: {data}"
        bs = buttons(shown[0])
        assert bs, f"no buttons on the screen of {data}"
        sub = data.split(":")[1]
        if sub != "hub":
            assert (t("fa", "cg.b.hub"), f"cg:hub:{A}") in bs, f"no way back from {data}"
        else:
            assert (t("fa", "menu.back"), f"mn:menu:{A}") in bs
        todo += [cb for _label, cb in bs if cb.startswith("cg:")]
    assert {s.split(":")[1] for s in seen} == {"hub", "card", "cardb", "prof", "look", "gene", "pers", "prog", "nf", "nr"}


def test_a_character_without_a_city_still_gets_every_screen(h):
    """A character finished in the private chat before ever entering a city has no city yet: nothing is invented, nothing crashes."""
    core.ensure_user(h.db, A, None, "آرمان")
    h.say(A, "/start char")
    h.say(A, "آرمان")
    h.say(A, "کیانی")
    h.press(A, f"cr:g:male:{A}")
    assert character.is_done(character.get(h.db, A)) and character.get(h.db, A)["current_world_id"] is None
    for sub in ("hub", "card", "cardb", "prof", "look", "gene", "pers", "prog"):
        out = h.press(A, f"cg:{sub}:{A}")
        assert out and of(out, "EditMessageText") and not photos(out), sub
    assert t("fa", "e.ic_nocity") not in all_text(out)


# ============================================================================================== no out-of-character disclaimers
def test_no_screen_of_the_character_interface_carries_a_disclaimer(h):
    finish(h, A)
    texts = [all_text(h.say(A, "شخصیت")), all_text(h.say(A, "کارت ملی"))]
    for sub in ("cardb", "card", "prof", "look", "gene", "pers", "prog", "hub"):
        texts.append(all_text(h.press(A, f"cg:{sub}:{A}")))
    h.db.x("UPDATE users SET lang='en' WHERE id=?", (A,))
    texts += [all_text(h.say(A, "character")), all_text(h.say(A, "id card"))]
    for sub in ("cardb", "prof", "look", "gene", "pers", "prog"):
        texts.append(all_text(h.press(A, f"cg:{sub}:{A}")))
    blob = "\n".join(texts)
    assert blob.strip()
    for phrase in DISCLAIMERS:
        assert phrase not in blob, phrase
    from hypemarket import texts as TX
    for lang in ("fa", "en"):
        for key, value in TX.STR[lang].items():
            if key.startswith(("cg.", "idc.", "char.s.", "menu.idcard", "menu.character")):
                for phrase in DISCLAIMERS:
                    assert phrase not in value, (key, phrase)
    for lab in idcard.LABELS.values():
        for value in lab.values():
            for phrase in DISCLAIMERS:
                assert phrase not in value


def test_the_set_up_screens_have_no_disclaimer_either(h):
    out = h.say(A, "/start char")
    assert not any(p in all_text(out) for p in DISCLAIMERS) and t("fa", "char.s.first", min=C.CHAR_NAME_MIN, max=C.CHAR_NAME_MAX) in all_text(out)
