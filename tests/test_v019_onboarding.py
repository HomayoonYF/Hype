"""v0.19: the character set-up through the real handlers: a pending profile at the first life-game entry, the private set-up chat, the persisted steps, who may do
what, and that nothing else in the bot is held up by it."""
import pytest

import mapkit
import test_bot as tb
from hypemarket import character, core, world, worldmap, worldplaces
from hypemarket.texts import t

CH, CH2 = -100901, -100902
A, B, C = 880001, 880002, 880003
BOT = "TestBot"

_LAYOUTS: dict = {}


def layout(seed):
    if seed not in _LAYOUTS:
        _LAYOUTS[seed] = world.build(seed)
    return _LAYOUTS[seed]


@pytest.fixture()
def h():
    worldmap.cache_clear()
    worldplaces.cache_clear()
    hh = tb.Harness()
    world.save_world(hh.db, CH, layout(7))
    return hh


def last(calls):
    sent = [c for c in calls if hasattr(c, "text") and getattr(c, "text", None)]
    return sent[-1] if sent else None


def text_of(calls):
    c = last(calls)
    return c.text if c else ""


def buttons(calls):
    c = last(calls)
    return mapkit.buttons_of(c) if c else []


def row(h, uid):
    return character.get(h.db, uid)


def finish(h, uid, first="علی", family="رضایی", gender="male"):
    h.say(uid, "/start char")
    h.say(uid, first)
    h.say(uid, family)
    return h.press(uid, f"cr:g:{gender}:{uid}")


# ============================================================================================== the first life-game entry
def test_the_first_map_creates_a_pending_profile_and_sends_the_player_to_the_private_chat(h):
    out = h.say(A, "مپ", chat=CH)
    ch = row(h, A)
    assert ch is not None and ch["citizen_id"] == 0 and ch["setup_state"] == "first" and ch["first_name"] == ""
    w = world.get_world(h.db, CH)
    assert ch["birth_world_id"] == ch["current_world_id"] == w["id"]                 # the city of the first life-game entry, saved right away
    msg = last(out)
    assert msg.text == t("fa", "char.need")
    assert (f"https://t.me/{BOT}?start=char") in [b.url for r in msg.reply_markup.inline_keyboard for b in r]
    assert h.db.q1("SELECT COUNT(*) c FROM map_sessions")["c"] == 0                  # no map yet
    assert not any(isinstance(c, tb.SendPhoto) for c in out)


def test_pending_players_are_asked_again_in_the_same_words_without_a_second_profile(h):
    h.say(A, "مپ", chat=CH)
    out = h.say(A, "مپ", chat=CH)
    assert text_of(out) == t("fa", "char.need")
    assert h.db.q1("SELECT COUNT(*) c FROM characters")["c"] == 1
    h.say(A, "/start char")
    h.say(A, "علی")
    out = h.say(A, "مپ", chat=CH)
    assert text_of(out) == t("fa", "char.need.resume") and "علی" not in text_of(out)       # a name is never said in a group
    assert [b.text for r in last(out).reply_markup.inline_keyboard for b in r] == [t("fa", "char.b.resume")]


def test_jobs_wait_for_the_character_too(h):
    out = h.say(A, "مشاغل", chat=CH)
    assert text_of(out) == t("fa", "char.need")
    out = h.press(A, f"jb:o:snapp:{A}", chat=CH)
    assert [c.text for c in out if isinstance(c, tb.AnswerCallbackQuery)][-1] == t("fa", "char.cb.need")


def test_a_bot_account_never_gets_a_character(h):
    core.ensure_user(h.db, 99, "somebot", "Some Bot")
    h.db.x("UPDATE users SET bot=1 WHERE id=99")
    assert character.ensure_profile(h.db, 99) is None and character.get(h.db, 99) is None
    assert character.ensure_profile(h.db, 123456789) is None                         # a player the game does not know


# ============================================================================================== the private set-up
def test_the_whole_set_up_in_the_private_chat(h):
    h.say(A, "مپ", chat=CH)
    out = h.say(A, "/start char")
    assert t("fa", "char.s.first", min=2, max=20) in text_of(out) or "نام شخصیتت" in text_of(out)
    out = h.say(A, "علی")
    assert row(h, A)["first_name"] == "علی" and row(h, A)["setup_state"] == "last"
    assert "علی" in text_of(out) and [d for _t, d in buttons(out)] == [f"cr:n:{A}"]
    out = h.say(A, "رضایی")
    ch = row(h, A)
    assert ch["last_name"] == "رضایی" and ch["setup_state"] == "gender"
    assert [d for _t, d in buttons(out)] == [f"cr:g:male:{A}", f"cr:g:female:{A}", f"cr:n:{A}"]
    assert character.age(ch) is None and ch["age_started_at"] is None                 # nobody ages before they exist
    h.advance(1234)
    out = h.press(A, f"cr:g:female:{A}")
    ch = row(h, A)
    assert ch["setup_state"] == "done" and ch["gender"] == "female" and ch["age_started_at"] == tb.T[0]
    txt = [c for c in out if isinstance(c, tb.EditMessageText)][-1].text
    assert "علی رضایی" in txt and "کد شهروندی: <b>۰</b>" in txt and "<b>۱۵</b>" in txt
    assert character.age(ch) == 15
    again = h.say(A, "مپ", chat=CH)
    assert any(isinstance(c, tb.SendPhoto) for c in again)                            # the map opens; the set-up never repeats


def test_the_set_up_survives_a_restart_and_a_new_session(h):
    h.say(A, "مپ", chat=CH)
    h.say(A, "/start char")
    h.say(A, "علی")
    cid = row(h, A)["citizen_id"]
    traits_before = h.db.qa("SELECT trait, code FROM character_traits WHERE user_id=?", (A,))
    h2 = tb.Harness()                                                                # a "restarted" bot: new process objects, the same database
    h2.db.close()
    h2.db = h2.app.db = h.db
    out = h2.say(A, "/start")
    assert "نام خانوادگی" in text_of(out)                                            # resumes at the last-name step
    h2.say(A, "رضایی")
    h2.press(A, f"cr:g:male:{A}")
    ch = row(h2, A)
    assert ch["citizen_id"] == cid and ch["setup_state"] == "done"
    assert h.db.qa("SELECT trait, code FROM character_traits WHERE user_id=?", (A,)) == traits_before        # the same face
    assert h.db.q1("SELECT COUNT(*) c FROM characters")["c"] == 1


def test_changing_the_name_goes_back_one_step_and_keeps_the_profile(h):
    h.say(A, "مپ", chat=CH)
    h.say(A, "/start char")
    h.say(A, "علی")
    h.say(A, "رضایی")
    cid = row(h, A)["citizen_id"]
    out = h.press(A, f"cr:n:{A}")
    assert row(h, A)["setup_state"] == "first" and row(h, A)["citizen_id"] == cid
    h.say(A, "حسین")
    h.say(A, "کریمی")
    h.press(A, f"cr:g:male:{A}")
    ch = row(h, A)
    assert (ch["first_name"], ch["last_name"], ch["setup_state"], ch["citizen_id"]) == ("حسین", "کریمی", "done", cid)


def test_a_repeated_or_late_gender_button_changes_nothing(h):
    finish(h, A, gender="male")
    ch = row(h, A)
    h.advance(86400)
    h.press(A, f"cr:g:male:{A}")
    h.press(A, f"cr:g:female:{A}")
    h.press(A, f"cr:n:{A}")
    same = row(h, A)
    assert same["gender"] == "male" and same["age_started_at"] == ch["age_started_at"] and same["setup_state"] == "done" and same["first_name"] == "علی"
    h.say(A, "یکی دیگه")                                                               # a plain message is not a name any more
    assert row(h, A)["first_name"] == "علی"


def test_an_unfinished_gender_press_is_refused(h):
    h.say(A, "مپ", chat=CH)
    h.say(A, "/start char")
    out = h.press(A, f"cr:g:male:{A}")                                                 # before the names: nothing finishes
    assert row(h, A)["setup_state"] == "first" and row(h, A)["gender"] is None
    assert [c for c in out if isinstance(c, tb.AnswerCallbackQuery)][-1].text == t("fa", "e.char_state")


# ============================================================================================== who may do what
def test_nobody_else_can_advance_or_finish_a_players_profile(h):
    h.say(A, "مپ", chat=CH)
    h.say(A, "/start char")
    h.say(A, "علی")
    h.say(A, "رضایی")
    before = dict(row(h, A))
    h.say(B, "/start")                                                                 # B is just a registered player
    out = h.press(B, f"cr:g:female:{A}")                                               # A's button, pressed by B
    assert [c for c in out if isinstance(c, tb.AnswerCallbackQuery)][-1].text == t("fa", "not_yours")
    out = h.press(B, f"cr:n:{A}")
    assert dict(row(h, A)) == before
    out = h.press(B, f"cr:g:female:{B}")                                               # B's own button: B has no profile, none is created by a button
    assert row(h, B) is None and [c for c in out if isinstance(c, tb.AnswerCallbackQuery)][-1].text == t("fa", "e.char_none")
    h.say(B, "علی")                                                                    # ...and a plain message from B is nobody's name
    assert dict(row(h, A)) == before and row(h, B) is None


def test_messages_in_a_group_are_never_taken_as_a_name(h):
    h.say(A, "مپ", chat=CH)
    out = h.say(A, "علی", chat=CH)
    assert out == [] or not [c for c in out if hasattr(c, "text") and getattr(c, "text", None)]
    assert row(h, A)["setup_state"] == "first" and row(h, A)["first_name"] == ""
    h.say(B, "سلام به همه", chat=CH)                                                  # other people's chat is not touched either
    assert row(h, A)["first_name"] == ""


def test_a_group_button_press_of_the_set_up_is_refused(h):
    h.say(A, "مپ", chat=CH)
    out = h.press(A, f"cr:g:male:{A}", chat=CH)
    assert row(h, A)["gender"] is None
    assert [c for c in out if isinstance(c, tb.AnswerCallbackQuery)][-1].text == t("fa", "char.private")


@pytest.mark.parametrize("bad", ["<b>x</b>", "علی\nرضایی", "Ali&Bob", "علی۱", "😀😀", "x", "a" * 40, "علی Ali", "@admin", "علی‎رضا", "http://t.me/x"])
def test_unacceptable_names_are_refused_and_the_step_stays(h, bad):
    h.say(A, "مپ", chat=CH)
    h.say(A, "/start char")
    out = h.say(A, bad)
    assert row(h, A)["setup_state"] == "first" and row(h, A)["first_name"] == ""
    assert "❌" in text_of(out) or text_of(out) == ""


def test_a_command_still_runs_while_a_name_is_awaited(h):
    h.say(A, "مپ", chat=CH)
    h.say(A, "/start char")
    out = h.say(A, "/menu")
    assert out and row(h, A)["setup_state"] == "first" and row(h, A)["first_name"] == ""


def test_names_are_normalised_and_saved_as_typed(h):
    h.say(A, "مپ", chat=CH)
    h.say(A, "/start char")
    h.say(A, "  عليرضا  ")                                                               # Arabic yeh, padded
    assert row(h, A)["first_name"] == "علیرضا" and row(h, A)["setup_state"] == "last"
    h.say(A, "Ali علی")                                                                # one name is one alphabet: refused, the step stays
    assert row(h, A)["last_name"] == "" and row(h, A)["setup_state"] == "last"
    h.say(A, "موسوی‌نژاد")                                                              # the half-space inside a Persian name is kept
    assert row(h, A)["last_name"] == "موسوی‌نژاد" and row(h, A)["setup_state"] == "gender"


def test_latin_names_work_and_are_escaped(h):
    h.say(A, "مپ", chat=CH)
    h.say(A, "/start char")
    h.say(A, "O'Brien")
    out = h.say(A, "Smith-Jones")
    assert row(h, A)["first_name"] == "O'Brien" and row(h, A)["last_name"] == "Smith-Jones"
    out = h.press(A, f"cr:n:{A}")                                                      # «change the name» goes back to the first step and keeps the profile
    assert row(h, A)["setup_state"] == "first" and "O&#x27;Brien" not in text_of(out)
    h.say(A, "Zed")
    h.say(A, "Ng")
    out = h.press(A, f"cr:g:male:{A}")
    assert "Zed Ng" in [c for c in out if isinstance(c, tb.EditMessageText)][-1].text


# ============================================================================================== nothing else is held up
def test_stock_commands_never_need_a_character(h):
    h.say(A, "/start")
    for cmd in ("/menu", "/level", "/help", "/profile", "/stocks"):
        assert h.say(A, cmd)
    assert row(h, A) is None
    out = h.say(A, "/start s_NOPE_r1")
    assert row(h, A) is None


def test_a_stock_link_keeps_working_with_a_pending_profile(h):
    h.say(A, "مپ", chat=CH)
    out = h.say(A, "/start")
    assert "نام شخصیتت" in " ".join(getattr(c, "text", "") or "" for c in out)
    assert row(h, A)["setup_state"] == "first"


def test_the_exchange_and_the_wallet_do_not_wait_for_a_character(h):
    h.say(A, "/start")
    out = h.say(A, "صرافی")
    assert out and row(h, A) is None
