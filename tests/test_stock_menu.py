"""v0.14: the stock section's button menu: every command is one tap away, typed input is guided, coin-moving buttons ask first."""
import random

import pytest
from aiogram.methods import AnswerCallbackQuery, EditMessageText, SendMessage

from hypemarket import config as C, core, docs, handlers, sections, textparse, views
from hypemarket.texts import ALIASES, EN, FA, STR

import test_bot as tb

G = -100707
UID_BIG = 7_000_000_000_000                       # real Telegram ids can be this long: callback_data must still fit in 64 bytes


@pytest.fixture()
def h():
    return tb.Harness()


def buttons(call):
    return [b.callback_data for row in call.reply_markup.inline_keyboard for b in row]


def edits(calls):
    return [c for c in calls if isinstance(c, EditMessageText)]


def toasts(calls):
    return [c.text for c in calls if isinstance(c, AnswerCallbackQuery) and c.text]


def start(h, *uids):
    for uid in uids:
        h.say(uid, "شروع")


def seeded(h):
    """Player 1 owns SPEED (listed and bought by 2); everybody has the starting cash."""
    start(h, 1, 2, 3)
    h.say(1, "سهام عرضه SPEED اسپید")
    h.advance(60)
    h.say(2, "سهام خرید 10 SPEED")
    h.advance(60)


# ----------------------------------------------------------------------------------------------------------- the layout
def test_the_categories_cover_every_stock_command_exactly_once():
    listed = [c for _cid, cmds in sections.STOCK_CATEGORIES for c in cmds]
    assert sorted(listed) == sorted(sections.STOCK_CMDS) and len(listed) == len(set(listed))
    assert len({cid for cid, _ in sections.STOCK_CATEGORIES}) == len(sections.STOCK_CATEGORIES)
    for row in sections.STOCK_QUICK:
        assert all(c in sections.STOCK_CMDS or c == "profile" for c in row)
    assert sections.ASK_CMDS <= sections.STOCK_CMDS and sections.SURE_CMDS <= sections.STOCK_CMDS and not (sections.ASK_CMDS & sections.SURE_CMDS)
    assert sections.SCREEN_CMDS | sections.ASK_CMDS | sections.SURE_CMDS == sections.STOCK_CMDS


@pytest.mark.parametrize("lang", ["fa", "en"])
def test_every_button_has_a_label_a_valid_callback_and_fits_telegrams_limit(lang):
    for uid in (1, UID_BIG):
        menu = views.stock_menu(lang, uid)
        screens = [menu] + [views.stock_category(lang, uid, cid) for cid, _ in sections.STOCK_CATEGORIES]
        seen = set()
        for r in screens:
            for row in r.kb.inline_keyboard:
                assert 1 <= len(row) <= 2
                for b in row:
                    assert b.text and b.text.strip() and len(b.text) <= 32, b.text
                    assert len(b.callback_data.encode()) <= 64, b.callback_data
                    assert b.callback_data.endswith(f":{uid}")
                    seen.add(b.callback_data)
        for cid, cmds in sections.STOCK_CATEGORIES:
            for cmd in cmds:
                label, data = views.cmd_button(lang, cmd, cid, uid)
                assert data in seen, (cmd, data)
        for r in (views.ask_view(lang, uid, "buy", "trade", True), views.sure_view(lang, uid, "payout", "company")):
            assert all(len(b.callback_data.encode()) <= 64 for row in r.kb.inline_keyboard for b in row)


def test_each_command_button_matches_its_class():
    for cid, cmds in sections.STOCK_CATEGORIES:
        for cmd in cmds:
            _label, data = views.cmd_button("fa", cmd, cid, 5)
            kind = data.split(":")[1]
            assert kind == ("ask" if cmd in sections.ASK_CMDS else "sure" if cmd in sections.SURE_CMDS else "go"), (cmd, data)
            if kind == "go":
                target = data.split(":")[2]
                assert target == sections.SCREEN_OF.get(cmd, cmd) and target in views.STOCK_GO and target in handlers.HANDLERS
            else:
                assert data.split(":")[2] == cmd


def test_every_menu_text_exists_in_both_languages_and_is_valid_html():
    import test_bot as T
    for lang in ("fa", "en"):
        for cmd in sections.STOCK_CMDS:
            assert f"sm.{cmd}" in STR[lang], (lang, cmd)
        for cid, _ in sections.STOCK_CATEGORIES:
            assert f"sm.cat.{cid}" in STR[lang] and f"sm.catname.{cid}" in STR[lang]
        for cmd in sections.ASK_CMDS:
            assert f"ask.fmt.{cmd}" in STR[lang] and cmd in docs.DOCS, (lang, cmd)
        assert "sm.view_trust" in STR[lang] and "sm.view_alert" in STR[lang]
        pages = [views.stock_menu(lang, 1)] + [views.stock_category(lang, 1, cid) for cid, _ in sections.STOCK_CATEGORIES]
        pages += [views.ask_view(lang, 1, c, "m", p) for c in sorted(sections.ASK_CMDS) for p in (True, False)]
        pages += [views.sure_view(lang, 1, c, "m") for c in sorted(sections.SURE_CMDS)]
        for r in pages:
            assert T.assert_valid_telegram_html(r.text) is None, r.text
            assert "{" not in r.text and "}" not in r.text, r.text
    fa_keys = {k for k in FA if k.startswith(("sm.", "ask."))}
    assert fa_keys == {k for k in EN if k.startswith(("sm.", "ask."))}


# ------------------------------------------------------------------------------------------------------ opening screens
def test_the_menu_and_each_category_open_in_place(h):
    start(h, 1)
    first = edits(h.press(1, "sm:menu:1"))[-1]
    assert buttons(first)[-1] == "mn:menu:1"
    for cid, cmds in sections.STOCK_CATEGORIES:
        e = edits(h.press(1, f"sm:cat:{cid}:1"))
        assert len(e) == 1, cid
        data = buttons(e[0])
        assert data[-1] == "sm:menu:1"
        assert len(data) == len(cmds) + 1
        assert data[:-1] == [views.cmd_button("fa", c, cid, 1)[1] for c in cmds]
    assert "sm:menu:1" in buttons(edits(h.press(1, "sm:cat:nonsense:1"))[-1]) or edits(h.press(1, "sm:cat:nonsense:1"))   # an unknown category shows the menu


def test_every_screen_button_opens_the_commands_screen_with_a_way_back(h):
    seeded(h)
    h.db.x("UPDATE users SET max_level=4, cash=cash+? WHERE id=1", (C.coins(60_000),))
    h.say(1, "سهام ساخت شرکت رفقا RAFA")
    opened = 0
    for cid, cmds in sections.STOCK_CATEGORIES:
        for cmd in cmds:
            if cmd not in sections.SCREEN_CMDS:
                continue
            target = sections.SCREEN_OF.get(cmd, cmd)
            calls = h.press(1, f"sm:go:{target}:{cid}:1")
            e = edits(calls)
            if not e:                                                          # a screen that has nothing to show answers with its own alert, never silence
                assert toasts(calls), (cmd, calls)
                continue
            opened += 1
            assert buttons(e[-1])[-1] == f"sm:cat:{cid}:1", cmd              # 🔙 returns to the category the button was pressed in
            assert "{" not in e[-1].text, cmd
    assert opened >= len(sections.SCREEN_CMDS) - 4
    for cmd in ("market", "portfolio", "profile", "level", "skills", "quests", "mystock", "exchange"):                      # shortcuts return to the stock menu
        e = edits(h.press(1, f"sm:go:{cmd}:m:1"))
        assert e and buttons(e[-1])[-1] == "sm:menu:1", cmd


def test_a_screen_with_nothing_to_show_alerts_instead_of_going_silent(h):
    start(h, 3)
    calls = h.press(3, "sm:go:mystock:m:3")                                    # player 3 has no stock yet
    assert not edits(calls) and "سهامی عرضه نکردی" in " ".join(toasts(calls))


def test_the_upgrade_button_opens_the_skills_screen(h):
    start(h, 1)
    up = edits(h.press(1, "sm:go:skills:progress:1"))[-1]
    assert any(b.startswith("up:") or b.startswith("sk") for b in buttons(up)) or buttons(up)[-1] == "sm:cat:progress:1"
    assert views.cmd_button("fa", "upgrade", "progress", 1)[1] == "sm:go:skills:progress:1"


# ------------------------------------------------------------------------------------------------------- guided input
def test_buy_button_then_the_next_message_buys(h):
    seeded(h)
    before = h.db.q1("SELECT shares FROM holdings WHERE user_id=3")
    calls = h.press(3, "sm:ask:buy:trade:3")
    e = edits(calls)[-1]
    assert "10 SPEED" in e.text and buttons(e) == ["sm:cancel:trade:3"]
    assert h.app.pending[3][0] == "buy"
    out = h.texts(h.say(3, "5 SPEED"))
    assert "خرید انجام شد" in out
    assert h.db.q1("SELECT shares FROM holdings WHERE user_id=3")["shares"] > 0 and before is None
    assert 3 not in h.app.pending
    h.advance(60)
    shares = h.db.q1("SELECT shares FROM holdings WHERE user_id=3")["shares"]
    assert not h.say(3, "5 SPEED") and h.db.q1("SELECT shares FROM holdings WHERE user_id=3")["shares"] == shares      # one answer, one command


def test_persian_digits_and_extra_words_work_in_a_guided_answer(h):
    seeded(h)
    h.press(3, "sm:ask:buy:trade:3")
    assert "خرید انجام شد" in h.texts(h.say(3, "۵ SPEED"))
    h.advance(60)
    h.press(3, "sm:ask:sell:trade:3")
    assert "فروش انجام شد" in h.texts(h.say(3, "همه SPEED"))


def test_launch_button_lists_a_stock_from_the_answer(h):
    start(h, 4)
    h.press(4, "sm:ask:launch:own:4")
    out = h.texts(h.say(4, "ZOOM زوم | سریع"))
    assert "عرضه شد" in out and h.db.q1("SELECT ticker FROM stocks WHERE owner_id=4")["ticker"] == "ZOOM"


def test_price_chart_alert_and_trust_buttons_guide_too(h):
    seeded(h)
    h.press(3, "sm:ask:price:trade:3")
    assert "SPEED" in h.texts(h.say(3, "SPEED"))
    h.press(3, "sm:ask:chart:trade:3")
    assert h.say(3, "SPEED")
    h.press(3, "sm:ask:alert:trade:3")
    assert "هشدار" in h.texts(h.say(3, "SPEED 99"))                          # the command's own answer (price alerts are a skill: it says so)
    assert 3 not in h.app.pending
    h.press(3, "sm:ask:trust:safety:3")
    assert h.say(3, "SPEED")


@pytest.mark.parametrize("cmd", sorted(sections.ASK_CMDS))
def test_every_ask_button_turns_the_answer_into_that_exact_command(h, cmd):
    start(h, 5)
    for lang in ("fa", "en"):
        example = STR[lang][f"ask.fmt.{cmd}"]
        p = textparse.parse(f"stocks {cmd} {example}", None)
        assert p is not None and p.cmd == cmd and p.section == "stocks" and not p.retired, (lang, cmd, p)
    e = edits(h.press(5, f"sm:ask:{cmd}:m:5"))
    assert e and h.app.pending[5][0] == cmd
    out = h.say(5, STR["fa"][f"ask.fmt.{cmd}"])
    assert out, cmd                                                          # the command ran (success or its own error): never silence
    assert 5 not in h.app.pending


def test_the_guided_answer_expires(h):
    seeded(h)
    h.press(3, "sm:ask:buy:trade:3")
    h.advance(sections.GUIDED_TTL + 1)
    assert not h.say(3, "5 SPEED")
    assert not h.db.q1("SELECT 1 FROM holdings WHERE user_id=3") and 3 not in h.app.pending


def test_cancel_and_any_other_button_or_command_drop_the_prompt(h):
    seeded(h)
    h.press(3, "sm:ask:buy:trade:3")
    e = edits(h.press(3, "sm:cancel:trade:3"))
    assert e and buttons(e[-1])[-1] == "sm:menu:3" and "sm:ask:buy:trade:3" in buttons(e[-1]) and 3 not in h.app.pending
    assert not h.say(3, "5 SPEED")
    h.press(3, "sm:ask:buy:m:3")
    e = edits(h.press(3, "sm:cancel:m:3"))
    assert e and 3 not in h.app.pending                                        # cancelled from the menu: back on the menu
    h.press(3, "sm:ask:buy:trade:3")
    h.press(3, "sm:menu:3")                                                    # another button
    assert 3 not in h.app.pending and not h.say(3, "5 SPEED")
    h.press(3, "sm:ask:buy:trade:3")
    h.say(3, "بازار")                                                          # a real command instead of the answer
    assert 3 not in h.app.pending and not h.say(3, "5 SPEED")
    h.press(3, "sm:ask:buy:trade:3")
    h.press(3, "mn:menu:3")
    assert 3 not in h.app.pending


def test_a_second_ask_replaces_the_first(h):
    seeded(h)
    h.press(3, "sm:ask:buy:trade:3")
    h.press(3, "sm:ask:price:trade:3")
    assert h.app.pending[3][0] == "price"
    out = h.texts(h.say(3, "SPEED"))
    assert "خرید انجام شد" not in out and not h.db.q1("SELECT 1 FROM holdings WHERE user_id=3")


def test_prompts_are_per_player(h):
    seeded(h)
    h.press(2, "sm:ask:buy:trade:2")
    assert not h.say(3, "5 SPEED")                                             # player 3 did not press anything
    assert not h.db.q1("SELECT 1 FROM holdings WHERE user_id=3") and h.app.pending[2][0] == "buy"
    h.press(3, "sm:ask:price:trade:3")
    assert h.app.pending[2][0] == "buy" and h.app.pending[3][0] == "price"


def test_nobody_can_press_someone_elses_menu(h):
    seeded(h)
    for data in ("sm:ask:buy:trade:3", "sm:menu:3", "sm:cat:trade:3", "sm:go:market:m:3", "sm:sure:payout:company:3", "sm:do:payout:company:3", "sm:cancel:m:3"):
        calls = h.press(2, data)
        assert not edits(calls) and "برای تو نیست" in " ".join(toasts(calls)), data
    assert 2 not in h.app.pending and 3 not in h.app.pending


def test_a_prompt_answered_with_a_command_text_is_not_rewritten(h):
    seeded(h)
    h.press(3, "sm:ask:price:trade:3")
    out = h.texts(h.say(3, "سهام خرید 5 SPEED"))
    assert "خرید انجام شد" in out and 3 not in h.app.pending


# ----------------------------------------------------------------------------------------------------- groups never guide
def test_in_a_group_the_button_shows_the_command_to_type_and_waits_for_nothing(h):
    seeded(h)
    h.say(3, "!شروع", chat=G)
    calls = h.press(3, "sm:ask:buy:trade:3", chat=G)
    e = edits(calls)
    assert e and "سهام خرید" in e[-1].text and 3 not in h.app.pending
    assert not h.say(3, "5 SPEED", chat=G)
    assert not h.say(3, "5 SPEED") and not h.db.q1("SELECT 1 FROM holdings WHERE user_id=3")       # and the private chat is not armed either


# ----------------------------------------------------------------------------------------------------------- confirms
def _company(h):
    h.db.x("UPDATE users SET max_level=4, cash=cash+? WHERE id=2", (C.coins(60_000),))
    h.say(2, "سهام ساخت شرکت رفقا RAFA")
    assert h.db.q1("SELECT company_id FROM users WHERE id=2")["company_id"]
    h.say(3, "سهام عضویت شرکت رفقا")
    assert h.db.q1("SELECT company_id FROM users WHERE id=3")["company_id"]


def test_leave_company_asks_before_leaving(h):
    seeded(h)
    _company(h)
    e = edits(h.press(3, "sm:sure:leavecompany:company:3"))
    assert e and buttons(e[-1]) == ["sm:do:leavecompany:company:3", "sm:cancel:company:3"]
    assert h.db.q1("SELECT company_id FROM users WHERE id=3")["company_id"]                 # asking changes nothing
    e = edits(h.press(3, "sm:cancel:company:3"))
    assert e and buttons(e[-1])[-1] == "sm:menu:3" and h.db.q1("SELECT company_id FROM users WHERE id=3")["company_id"]
    h.press(3, "sm:sure:leavecompany:company:3")
    e = edits(h.press(3, "sm:do:leavecompany:company:3"))
    assert e and h.db.q1("SELECT company_id FROM users WHERE id=3")["company_id"] is None
    assert buttons(e[-1])[-1] == "sm:cat:company:3"


def test_payout_asks_before_paying_and_pays_once_confirmed(h):
    seeded(h)
    _company(h)
    h.advance(10)
    h.say(1, "سهام خرید 20 RAFA")
    treasury = h.db.q1("SELECT treasury FROM companies")["treasury"]
    assert treasury > 0
    e = edits(h.press(2, "sm:sure:payout:company:2"))
    assert e and "sm:do:payout:company:2" in buttons(e[-1])
    assert h.db.q1("SELECT treasury FROM companies")["treasury"] == treasury
    h.press(2, "sm:do:payout:company:2")
    assert h.db.q1("SELECT treasury FROM companies")["treasury"] < treasury


def test_confirming_without_a_company_is_a_polite_error(h):
    start(h, 1)
    calls = h.press(1, "sm:do:leavecompany:company:1")
    assert not any(isinstance(c, EditMessageText) and "{" in c.text for c in calls)
    calls = h.press(1, "sm:do:payout:company:1")
    assert calls                                                                   # an alert, never a crash


# --------------------------------------------------------------------------------------------------------- extra buttons
def test_trust_and_alert_prompts_offer_their_board_buttons(h):
    seeded(h)
    for cmd, view in (("trust", "sm:go:trust:safety:3"), ("alert", "sm:go:alert:trade:3")):
        origin = "safety" if cmd == "trust" else "trade"
        e = edits(h.press(3, f"sm:ask:{cmd}:{origin}:3"))[-1]
        assert view in buttons(e) and buttons(e)[-1] == f"sm:cancel:{origin}:3"
        shown = edits(h.press(3, view))
        assert shown and buttons(shown[-1])[-1] == f"sm:cat:{origin}:3"
        assert 3 not in h.app.pending                                                # looking at the board ends the prompt


def test_hand_made_buttons_do_nothing(h):
    start(h, 1)
    before = h.db.q1("SELECT cash FROM users WHERE id=1")["cash"]
    for data in ("sm:go:upgrade:m:1", "sm:go:buy:m:1", "sm:go:launch:m:1", "sm:go:ban:m:1", "sm:go:stats:m:1", "sm:ask:ban:m:1", "sm:ask:market:m:1", "sm:ask:level:m:1",
                 "sm:sure:market:m:1", "sm:sure:buy:m:1", "sm:do:market:m:1", "sm:do:ban:m:1", "sm:do:buy:m:1", "sm:nonsense:x:m:1", "sm:go:market:zzz:1"):
        calls = h.press(1, data)
        e = edits(calls)
        assert not e or data == "sm:go:market:zzz:1", data                     # (an unknown origin falls back to the stock menu as the way back)
        assert 1 not in h.app.pending, data
    assert h.db.q1("SELECT cash FROM users WHERE id=1")["cash"] == before


def test_menu_and_slash_menu_still_work_in_both_chat_types(h):
    start(h, 1)
    for text in ("سهام", "سهام منو", "stocks", "/stocks", "stocks menu"):
        calls = h.say(1, text)
        sent = [c for c in calls if isinstance(c, SendMessage)]
        assert sent and "sm:cat:trade:1" in buttons(sent[-1]), text
    h.say(1, "!شروع", chat=G)
    sent = [c for c in h.say(1, "سهام", chat=G) if isinstance(c, SendMessage)]
    assert sent and "sm:cat:trade:1" in buttons(sent[-1])


# ----------------------------------------------------------------------------------------------------------------- fuzz
def test_random_menu_taps_and_messages_never_crash_or_corrupt(h):
    seeded(h)
    crashes = []

    async def record(msg, key=None):
        crashes.append(msg)

    h.app.alert_admins = record
    rnd = random.Random(14)
    pieces = ["sm", "mn", "hp"]
    subs = ["menu", "cat", "go", "ask", "sure", "do", "cancel", "stock", "about", "guide", "cmds", "tips", "x", ""]
    cmds = sorted(sections.STOCK_CMDS) + ["ban", "stats", "nonsense", "profile", "daily", "invite", "m", "99", "-1", "اسپید", "‮"]
    origins = ["m"] + [cid for cid, _ in sections.STOCK_CATEGORIES] + ["zzz", ""]
    texts = ["5 SPEED", "SPEED", "سهام", "سهام منو", "سهام خرید 1 SPEED", "بازار", "x", "123", "stocks level", "سطح", "قیمت SPEED", "SPEED 5.5", "رفقای خوب RAFA"]
    for i in range(500):
        uid = rnd.choice([1, 2, 3, 4])
        chat = G if rnd.random() < 0.25 else None
        if rnd.random() < 0.6:
            parts = [rnd.choice(pieces), rnd.choice(subs)]
            for _ in range(rnd.randint(0, 3)):
                parts.append(rnd.choice(cmds + origins))
            if rnd.random() < 0.8:
                parts.append(str(rnd.choice([uid, uid, uid, 1, 2, 3, 999])))
            data = ":".join(parts)[:64]
            h.press(uid, data, chat=chat)
        else:
            h.say(uid, rnd.choice(texts), chat=chat)
        if rnd.random() < 0.1:
            h.advance(rnd.choice([5, 60, 400]))
    assert not crashes, crashes[:3]
    assert all(v[1] > 0 for v in h.app.pending.values())
    assert h.db.q1("SELECT COUNT(*) c FROM users WHERE cash < 0")["c"] == 0
    assert h.db.q1("PRAGMA integrity_check")[0] == "ok"
