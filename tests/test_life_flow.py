"""v0.18: the jobs through the real handlers (typed words and buttons), owner locks, stale buttons, the map's jobs button, other cities, restarts of
the callback state, and the guarantee that every new text exists, balances and is valid Telegram HTML."""
import copy
import re

import pytest

import mapkit
import test_bot as tb
from hypemarket import cash, config as C, core, fmt, presence, work, world, worldmap, worldplaces
from hypemarket.texts import EN, FA, t
from aiogram.methods import AnswerCallbackQuery, EditMessageCaption, EditMessageMedia, EditMessageText, SendMessage, SendPhoto

CH, CH2 = -100606, -100607
A, B = 71001, 71002
T0 = 1_700_000_000


@pytest.fixture(scope="module")
def layout():
    return world.build(7)


@pytest.fixture()
def h(layout):
    worldmap.cache_clear()
    worldplaces.cache_clear()
    hh = tb.Harness()
    world.save_world(hh.db, CH, copy.deepcopy(layout))
    for u in (A, B):
        hh.say(u, "شروع")
    return hh


def city(h, chat=CH):
    return worldplaces.load_city(h.db, world.get_world(h.db, chat))


def sent(calls):
    """New messages: text or (v0.18.1: the job screens carry a picture) photo."""
    return [c for c in calls if isinstance(c, (SendMessage, SendPhoto))]


def edits(calls):
    return [c for c in calls if isinstance(c, (EditMessageText, EditMessageCaption, EditMessageMedia))]


def text_of(call):
    """The text of a screen, whether it is a message text or a photo caption."""
    if isinstance(call, EditMessageMedia):
        return call.media.caption or ""
    return getattr(call, "caption", None) or getattr(call, "text", None) or ""


def alerts(calls):
    return [c.text for c in calls if isinstance(c, AnswerCallbackQuery) and c.show_alert and c.text]


def toasts(calls):
    return [c.text for c in calls if isinstance(c, AnswerCallbackQuery) and c.text]


def data_of(call):
    mk = getattr(call, "reply_markup", None)
    return [b.callback_data for row in mk.inline_keyboard for b in row] if mk else []


def labels_of(call):
    mk = getattr(call, "reply_markup", None)
    return [b.text for row in mk.inline_keyboard for b in row] if mk else []


def screen(calls):
    """The last screen the bot drew for a press or a word: (text, callback data list)."""
    out = edits(calls) or sent(calls)
    assert out, "the bot drew nothing"
    return text_of(out[-1]), data_of(out[-1])


def press(h, uid, data, chat=CH):
    return h.press(uid, data, chat=chat, photo=True)           # v0.18.1: a job screen is a picture message


def find(datas, prefix):
    hits = [d for d in datas if d.startswith(prefix)]
    assert hits, (prefix, datas)
    return hits[0]


def arrive(h, uid):
    """Move the Harness clock to the end of the player's trip."""
    tr = presence.walking_trip(h.db, city(h).id, uid)
    assert tr is not None, "no trip in progress"
    h.advance(max(0, tr["arrive_at"] - core.now()))


def bal(h, uid=A):
    return cash.balance(h.db, uid)


# ================================================================================================ the board
def test_the_board_lists_the_three_jobs_and_the_cash(h):
    text, datas = screen(h.say(A, "مشاغل", chat=CH))
    assert "مشاغل" in text and "$۱۰۰٫۰۰" in text and text.count("وضعیت: 🟢 آماده") == 3                 # a compact list: name, short description, status
    assert datas[:3] == [f"jb:o:snapp:{A}", f"jb:o:mcd:{A}", f"jb:o:muni:{A}"] and f"cs:w:{A}" in datas
    for word in ("jobs", "شغل"):
        assert "مشاغل" in screen(h.say(A, word, chat=CH))[0]
    assert "💼" in tb.Harness.texts(h.say(A, "مشاغل"))                                              # in a private chat too (the board needs no city)


def test_in_a_private_chat_the_jobs_say_where_to_play_them(h):
    for word, key in (("اسنپ", "snapp"), ("مک دونالد", "mcd"), ("شهرداری", "muni")):
        text, datas = screen(h.say(A, word))
        assert "گروه" in text and f"jb:h:{A}" in datas, word
    out = press(h, A, f"jb:sr:{A}", chat=A)                                                          # a ride cannot be taken outside a city
    assert alerts(out) and work.current(h.db, A) is None


def test_the_job_words_work_in_a_group_only_as_whole_messages(h):
    for text in ("اسنپ", "مک دونالد", "شهرداری", "مشاغل", "پول", "صرافی", "snapp"):
        assert h.say(A, text, chat=CH), text
    for text in ("اسنپ خوبه یا تپسی", "من مک دونالد دوست دارم", "شهرداری چیکار کرد", "پول چقدر شد", "امروز پول ندارم"):
        assert h.say(A, text, chat=CH) == [], text


# ================================================================================================ Snapp, button by button
def test_a_whole_snapp_ride_through_the_buttons(h):
    text, datas = screen(h.say(A, "اسنپ", chat=CH))
    assert "اسنپ" in text and "$۰٫۲۰" in text and "$۰٫۲۲" in text and "$۰٫۱۰" in text                      # the rental fee, the base fare and the largest tip are shown up front
    assert datas[0] == f"jb:sr:{A}"
    text, datas = screen(press(h, A, datas[0]))
    task = work.current(h.db, A)
    d = work.data_of(task)
    assert task["stage"] == "assigned" and f"jb:sd:{task['id']}:{A}" in datas and f"jb:x:{task['id']}:{A}" in datas
    assert fmt.usd(task["reward"], "fa") in text and "$۰٫۲۰" in text and fmt.dist(d["d2"], "fa") in text
    assert bal(h) == 10_000                                                                          # nothing charged yet
    again = press(h, A, f"jb:sr:{A}")                                                                # a second «take a ride» press
    assert alerts(again) and len(h.db.qa("SELECT * FROM work_tasks WHERE user_id=?", (A,))) == 1
    out = press(h, A, f"jb:sd:{task['id']}:{A}")
    assert work.current(h.db, A)["stage"] == "to_pickup" and presence.walking_trip(h.db, city(h).id, A)["mode"] == "car"
    assert "🚗" in " ".join(toasts(out))
    press(h, A, f"jb:sd:{task['id']}:{A}")                                                           # pressing «drive» again changes nothing
    assert len(h.db.qa("SELECT * FROM world_trips WHERE user_id=?", (A,))) == 1
    out = press(h, A, f"jb:sp:{task['id']}:{A}")                                                     # picking up before arriving
    assert alerts(out) and work.current(h.db, A)["stage"] == "to_pickup"
    arrive(h, A)
    text, datas = screen(press(h, A, f"jb:o:snapp:{A}"))
    assert f"jb:sp:{task['id']}:{A}" in datas and "به مبدا رسیدی" in text
    press(h, A, f"jb:sp:{task['id']}:{A}")
    assert work.current(h.db, A)["stage"] == "to_dest"
    assert alerts(press(h, A, f"jb:sp:{task['id']}:{A}"))                                            # a second pick-up press is stale
    text, _ = screen(press(h, A, f"jb:o:snapp:{A}"))
    assert "در راه" in text or "مقصد" in text
    assert bal(h) == 10_000
    arrive(h, A)
    text, datas = screen(press(h, A, f"jb:o:snapp:{A}"))                                             # the first look after the arrival shows the receipt
    row = h.db.q1("SELECT * FROM work_tasks WHERE id=?", (task["id"],))
    rc = work.receipt_of(row)
    assert row["state"] == "done" and "سفر تموم شد" in text and fmt.usd(rc["net"], "fa") in text and fmt.usd(rc["balance"], "fa") in text
    assert f"jb:sr:{A}" in datas
    assert bal(h) == 10_000 + rc["net"]
    for _ in range(2):
        press(h, A, f"jb:o:snapp:{A}")
        press(h, A, f"jb:h:{A}")
    assert bal(h) == 10_000 + rc["net"]                                                              # looking again never pays again
    wallet = screen(h.say(A, "پول", chat=CH))[0]
    assert fmt.usd(10_000 + rc["net"], "fa") in wallet and "کرایه‌ی سفر اسنپ" in wallet


def test_after_a_ride_the_cooldown_is_shown_and_the_next_ride_waits(h):
    h.say(A, "اسنپ", chat=CH)
    press(h, A, f"jb:sr:{A}")
    tid = work.current(h.db, A)["id"]
    press(h, A, f"jb:sd:{tid}:{A}")
    arrive(h, A)
    press(h, A, f"jb:sp:{tid}:{A}")
    arrive(h, A)
    press(h, A, f"jb:o:snapp:{A}")
    text, datas = screen(press(h, A, f"jb:o:snapp:{A}"))
    assert "⏳" in text and f"jb:sr:{A}" not in datas
    out = press(h, A, f"jb:sr:{A}")                                                                  # an old «next ride» button pressed during the cooldown
    assert alerts(out) and len(h.db.qa("SELECT * FROM work_tasks WHERE user_id=?", (A,))) == 1
    h.advance(C.SNAPP_COOLDOWN)
    assert f"jb:sr:{A}" in screen(press(h, A, f"jb:o:snapp:{A}"))[1]


def test_buttons_of_a_ride_belong_to_the_driver(h):
    h.say(A, "اسنپ", chat=CH)
    press(h, A, f"jb:sr:{A}")
    tid = work.current(h.db, A)["id"]
    before = [tuple(r) for r in h.db.qa("SELECT * FROM work_tasks")]
    for data in (f"jb:sd:{tid}:{A}", f"jb:x:{tid}:{A}", f"jb:sp:{tid}:{A}", f"jb:o:snapp:{A}", f"jb:h:{A}", f"cs:w:{A}"):
        out = press(h, B, data)                                                                      # B presses A's button
        assert not edits(out) and not sent(out), data
        assert [tuple(r) for r in h.db.qa("SELECT * FROM work_tasks")] == before
    forged = press(h, B, f"jb:sd:{tid}:{B}")                                                         # B rewrites the id at the end to their own: the task is not B's
    assert alerts(forged) and [tuple(r) for r in h.db.qa("SELECT * FROM work_tasks")] == before
    assert presence.walking_trip(h.db, city(h).id, A) is None and presence.walking_trip(h.db, city(h).id, B) is None


def test_cancelling_a_ride_with_the_button_and_with_an_old_button(h):
    h.say(A, "اسنپ", chat=CH)
    press(h, A, f"jb:sr:{A}")
    tid = work.current(h.db, A)["id"]
    press(h, A, f"jb:sd:{tid}:{A}")
    out = press(h, A, f"jb:x:{tid}:{A}")
    text, datas = screen(out)
    assert h.db.q1("SELECT state FROM work_tasks WHERE id=?", (tid,))["state"] == "cancelled" and "لغو شد" in " ".join(toasts(out))
    assert presence.walking_trip(h.db, city(h).id, A) is None and bal(h) == 10_000 and f"jb:o:snapp:{A}" in datas
    out = press(h, A, f"jb:x:{tid}:{A}")                                                             # the same button again
    assert h.db.q1("SELECT state FROM work_tasks WHERE id=?", (tid,))["state"] == "cancelled" and bal(h) == 10_000
    assert not alerts(out) or "معتبر نیست" in " ".join(alerts(out) + toasts(out))
    out = press(h, A, f"jb:sd:{tid}:{A}")
    assert alerts(out) and presence.walking_trip(h.db, city(h).id, A) is None


def test_someone_without_enough_cash_is_told_what_to_do(h):
    h.db.x("UPDATE life_accounts SET balance=? WHERE user_id=?", (C.SNAPP_FEE_CENTS - 1, A))
    text, datas = screen(h.say(A, "اسنپ", chat=CH))
    assert "پول نقد" in text and f"jb:sr:{A}" not in datas and f"jb:o:muni:{A}" in datas
    out = press(h, A, f"jb:sr:{A}")
    assert alerts(out) and work.current(h.db, A) is None


def test_the_map_blocks_walking_away_from_a_ride_and_offers_the_job_board(h):
    d = mapkit.Driver(h, CH)
    v = d.open(A)
    assert f"jb:m:{A}" in [x for _t, x in v.buttons]
    out = d.press(A, f"jb:m:{A}", v)                                                                  # the board opens in a NEW message; the map picture stays
    assert out.sent and not out.edits and "مشاغل" in mapkit.text_of(out.sent[-1]) and h.db.q1("SELECT message_id FROM map_sessions")["message_id"] == v.mid
    h.say(A, "اسنپ", chat=CH)
    press(h, A, f"jb:sr:{A}")
    tid = work.current(h.db, A)["id"]
    press(h, A, f"jb:sd:{tid}:{A}")
    v2 = d.open(A)
    o = d.press(A, f"mp:{v2.sid}:o:casino", v2)
    go = [x for x_, x in o.buttons if x.endswith(":go")]
    out = d.press(A, go[0], o) if go else None
    assert out is None or (out.alerts and not out.edits)
    assert presence.walking_trip(h.db, city(h).id, A)["mode"] == "car"                                # the ride's trip was not replaced
    other = d.press(B, f"jb:m:{A}", v)
    assert not other.sent and not other.edits                                                          # B cannot open A's board from A's map


def test_a_ride_started_in_one_group_cannot_be_driven_from_another(h, layout):
    world.save_world(h.db, CH2, world.build(42))
    h.say(A, "اسنپ", chat=CH)
    press(h, A, f"jb:sr:{A}")
    tid = work.current(h.db, A)["id"]
    text, datas = screen(h.say(A, "اسنپ", chat=CH2))
    assert "شهر یه گروه دیگه" in text and f"jb:x:{tid}:{A}" in datas
    out = press(h, A, f"jb:sd:{tid}:{A}", chat=CH2)
    assert alerts(out) and presence.walking_trip(h.db, city(h).id, A) is None
    text, datas = screen(press(h, A, f"jb:x:{tid}:{A}", chat=CH2))                                   # but it can be given up from anywhere
    assert work.current(h.db, A) is None


# ================================================================================================ McDonald's, button by button
def walk_to_mcd(h, uid=A):
    text, datas = screen(h.say(uid, "مک دونالد", chat=CH))
    assert f"jb:mg:{uid}" in datas and "متر" in text and "باید اون‌جا باشی" in text
    press(h, uid, f"jb:mg:{uid}")
    arrive(h, uid)


def test_a_whole_mcdonalds_shift_through_the_buttons(h):
    walk_to_mcd(h)
    text, datas = screen(h.say(A, "مک دونالد", chat=CH))
    assert f"jb:ms:{A}" in datas and "جلوی" in text and "$۱۲٫۰۰" in text
    text, datas = screen(press(h, A, f"jb:ms:{A}"))                                                  # the confirmation before the 8 hours
    assert "$۱۲٫۰۰" in text and "۸ ساعت" in text and datas[0] == f"jb:my:{A}"
    assert work.current(h.db, A) is None
    out = press(h, A, f"jb:my:{A}")
    task = work.current(h.db, A)
    assert task["job"] == "mcd" and (task["due_at"] - task["started_at"]) == 28_800
    text, datas = screen(out)
    assert "شیفت مک‌دونالد" in text and "$۱۲٫۰۰" in text and fmt.clock(task["started_at"], "fa") in text and fmt.clock(task["due_at"], "fa") in text
    assert fmt.dur(28_800, "fa") in text and "در حال کار" in text and "نه، هنوز" in text and f"jb:o:mcd:{A}" in datas                  # wage, start, expected end, time left, and not collected yet
    press(h, A, f"jb:my:{A}")                                                                        # a second «yes»
    assert len(h.db.qa("SELECT * FROM work_tasks WHERE user_id=?", (A,))) == 1 and work.current(h.db, A)["due_at"] == task["due_at"]
    out = press(h, A, f"jb:mc:{task['id']}:{A}")                                                     # collecting too early
    assert alerts(out) and bal(h) == 10_000
    h.advance(28_799)
    assert alerts(press(h, A, f"jb:mc:{task['id']}:{A}")) and bal(h) == 10_000
    h.advance(1)
    text, datas = screen(press(h, A, f"jb:o:mcd:{A}"))
    assert f"jb:mc:{task['id']}:{A}" in datas
    text, datas = screen(press(h, A, f"jb:mc:{task['id']}:{A}"))
    assert "دستمزد شیفت برداشته شد" in text and "$۱۲٫۰۰" in text and "$۱۱۲٫۰۰" in text and bal(h) == 11_200
    for _ in range(2):
        out = press(h, A, f"jb:mc:{task['id']}:{A}")
        assert alerts(out) and bal(h) == 11_200
    assert "شیفت" in screen(h.say(A, "مک دونالد", chat=CH))[0] and len(h.db.qa("SELECT * FROM life_ledger WHERE kind='mcd_wage'")) == 1


def test_applying_away_from_the_mcdonalds_is_refused(h):
    out = press(h, A, f"jb:ms:{A}")
    assert alerts(out) and work.current(h.db, A) is None
    out = press(h, A, f"jb:my:{A}")
    assert alerts(out) and work.current(h.db, A) is None


def test_the_shift_status_and_wage_work_from_a_private_chat(h):
    walk_to_mcd(h)
    press(h, A, f"jb:ms:{A}")
    press(h, A, f"jb:my:{A}")
    tid = work.current(h.db, A)["id"]
    h.advance(28_800)
    text, datas = screen(h.say(A, "مک دونالد"))
    assert f"jb:mc:{tid}:{A}" in datas
    out = press(h, A, f"jb:mc:{tid}:{A}", chat=A)
    assert bal(h) == 11_200 and "دستمزد" in screen(out)[0]


def test_a_shift_blocks_the_other_jobs_and_cannot_be_cancelled_from_the_board(h):
    walk_to_mcd(h)
    press(h, A, f"jb:ms:{A}")
    press(h, A, f"jb:my:{A}")
    tid = work.current(h.db, A)["id"]
    for word in ("اسنپ", "شهرداری"):
        text, datas = screen(h.say(A, word, chat=CH))
        assert "کار دیگه‌ای داری" in text, word
    assert alerts(press(h, A, f"jb:sr:{A}")) and alerts(press(h, A, f"jb:x:{tid}:{A}"))
    assert work.current(h.db, A)["id"] == tid
    assert "شیفت" in screen(h.say(A, "مشاغل", chat=CH))[0]


def test_nobody_else_can_use_my_shift_buttons(h):
    walk_to_mcd(h)
    press(h, A, f"jb:ms:{A}")
    press(h, A, f"jb:my:{A}")
    tid = work.current(h.db, A)["id"]
    h.advance(28_800)
    for data in (f"jb:mc:{tid}:{A}", f"jb:o:mcd:{A}", f"jb:my:{A}"):
        out = press(h, B, data)
        assert not edits(out) and not sent(out)
    out = press(h, B, f"jb:mc:{tid}:{B}")                                                            # B tries with their own id on A's shift
    assert alerts(out) and bal(h, B) == 10_000 and bal(h) == 10_000


# ================================================================================================ municipal work, button by button
def test_municipal_work_through_the_buttons(h):
    text, datas = screen(h.say(A, "شهرداری", chat=CH))
    assert "پاداش" in text
    offers = [d for d in datas if d.startswith("jb:na:")]
    assert offers and all(re.fullmatch(rf"jb:na:(sweep|waste|green):0:{A}", d) for d in offers)
    labels = labels_of(edits([]) or sent(h.say(A, "شهرداری", chat=CH))[-1])
    assert any(fmt.usd(C.MUNI_TASKS[k], "fa") in " ".join(labels) for k in C.MUNI_TASKS)            # the reward is on the button, before accepting
    kind = offers[0].split(":")[2]
    out = press(h, A, offers[0])
    task = work.current(h.db, A)
    assert (task["job"], task["kind"], task["reward"], task["stage"]) == ("muni", kind, C.MUNI_TASKS[kind], "travel") and bal(h) == 10_000
    text, datas = screen(out)
    assert find(datas, "jb:ng:") and find(datas, "jb:x:")
    assert alerts(press(h, A, f"jb:na:{kind}:0:{A}"))                                                # the same offer again: one task at a time
    assert alerts(press(h, A, f"jb:nd:{task['id']}:{A}")) and bal(h) == 10_000                       # «done» without going there
    press(h, A, f"jb:ng:{task['id']}:{A}")
    assert alerts(press(h, A, f"jb:nd:{task['id']}:{A}")) and bal(h) == 10_000                       # «done» while still walking
    arrive(h, A)
    text, datas = screen(press(h, A, f"jb:o:muni:{A}"))
    assert f"jb:nd:{task['id']}:{A}" in datas
    text, datas = screen(press(h, A, f"jb:nd:{task['id']}:{A}"))
    reward = C.MUNI_TASKS[kind]
    assert fmt.usd(reward, "fa") in text and fmt.usd(10_000 + reward, "fa") in text and bal(h) == 10_000 + reward
    for _ in range(2):
        assert alerts(press(h, A, f"jb:nd:{task['id']}:{A}")) and bal(h) == 10_000 + reward
    text, datas = screen(press(h, A, f"jb:o:muni:{A}"))                                              # the next offers come after the short pause
    assert "⏳" in text
    h.advance(C.MUNI_COOLDOWN)
    text, datas = screen(press(h, A, f"jb:o:muni:{A}"))
    assert any(d.startswith("jb:na:") and d.split(":")[3] == "1" for d in datas)                     # a new round of offers
    assert alerts(press(h, A, f"jb:na:{kind}:0:{A}"))                                                # an old offer button no longer works


@pytest.mark.parametrize("data", ["jb:na:sweep:x:{u}", "jb:na:evil:0:{u}", "jb:na::0:{u}", "jb:na:sweep:{u}", "jb:na:sweep:0:1:{u}", "jb:nd:abc:{u}", "jb:nd:{u}", "jb:ng:99999:{u}",
                                  "jb:o:plumber:{u}", "jb:zz:{u}", "jb:sd:-1:{u}", "jb:mc:1.5:{u}", "jb:x:" + "9" * 30 + ":{u}", "jb:sr:7:{u}"])
def test_hand_made_job_buttons_are_harmless(h, data):
    before = ([tuple(r) for r in h.db.qa("SELECT * FROM work_tasks")], [tuple(r) for r in h.db.qa("SELECT * FROM life_ledger")],
              [tuple(r) for r in h.db.qa("SELECT * FROM world_trips")])
    out = press(h, A, data.format(u=A))
    assert [tuple(r) for r in h.db.qa("SELECT * FROM life_ledger")] == before[1] and [tuple(r) for r in h.db.qa("SELECT * FROM world_trips")] == before[2]
    assert [tuple(r) for r in h.db.qa("SELECT * FROM work_tasks")] == before[0]


# ================================================================================================ every new text
NEW_KEY = re.compile(r"^(cash|fx|job|snapp|mcd|muni|ia|n\.shift_ready|e\.(fx|cash|job|ia)|mp\.(b\.jobs|in_ride)|cat\.(life|money|jobs|social))")


def _life_keys(lang_table):
    return sorted(k for k in lang_table if NEW_KEY.match(k))


def test_every_new_text_exists_in_both_languages_with_the_same_placeholders():
    fa, en = _life_keys(FA), _life_keys(EN)
    assert fa and set(fa) == set(en), sorted(set(fa) ^ set(en))
    ph = re.compile(r"\{([a-z_0-9]+)\}")
    for k in fa:
        assert set(ph.findall(FA[k])) == set(ph.findall(EN[k])), (k, FA[k], EN[k])


def test_every_new_text_is_valid_telegram_html_once_filled_in():
    ph = re.compile(r"\{([a-z_0-9]+)\}")
    for table in (FA, EN):
        for k in _life_keys(table):
            text = table[k]
            filled = text.format(**{p: "7" for p in ph.findall(text)}) if "{" in text else text
            if re.match(r"^(ia\.hug|ia\.greet)", k):
                filled = filled.replace("7", '<a href="tg://user?id=7">u</a>', 2) if "{a}" in text else filled
            assert tb.assert_valid_telegram_html(filled) is None, (k, filled)
            assert len(filled) < 1024 or k.startswith(("snapp.home", "muni.home", "mcd.home")), (k, len(filled))


def test_every_error_code_the_new_modules_can_raise_has_a_message():
    src = "".join(open(f"hypemarket/{m}.py", encoding="utf-8").read() for m in ("cash", "work", "interact", "workui", "lifeui"))
    codes = set(re.findall(r'GameError\(\s*"([a-z_]+)"', src)) | set(re.findall(r'raise GameError\(([a-z_]+)\)', src)) - {"failed", "key"}
    codes -= {"no_user"}
    for code in sorted(codes):
        for table in (FA, EN):
            assert f"e.{code}" in table, code


def test_error_messages_with_numbers_fill_in_their_values():
    from hypemarket.handlers import err_text
    from hypemarket.core import GameError
    for e, needle in ((GameError("cash_low", need=2000, have=150), "$۲۰٫۰۰"), (GameError("fx_low_stock", need=500_000, have=10_000), "۵٬۰۰۰ 🪙"),
                      (GameError("job_cooldown", secs=75), "۱"), (GameError("job_notready", secs=3700), "ساعت"), (GameError("ia_cooldown", secs=30), "۳۰"),
                      (GameError("job_busy", job="mcd"), "مک‌دونالد")):
        text = err_text("fa", e)
        assert needle in text and "{" not in text, (e.code, text)
        assert "{" not in err_text("en", e)
