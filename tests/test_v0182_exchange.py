"""v0.18.2: the exchange is ONE-WAY. Stock money -> cash works; cash -> stock money exists nowhere (no function, no button, no command, no callback,
no screen, no text), and what may still be lying around from v0.18.x (an old button on somebody's screen, an open `c2s` preview stored in a database)
is refused without moving a cent."""
import inspect
import re
from pathlib import Path

import pytest

import test_bot as tb
from hypemarket import cash, config as C, core, lifeui, texts
from hypemarket.core import GameError
from hypemarket.db import Database
from aiogram.methods import AnswerCallbackQuery, EditMessageText, SendMessage

U1, U2 = 52001, 52002
PKG = Path(__file__).resolve().parent.parent / "hypemarket"


@pytest.fixture()
def db():
    core.set_clock(lambda: 1_700_000_000)
    d = Database(":memory:")
    core.ensure_user(d, U1, "a", "Ali", lang="fa")
    core.ensure_user(d, U2, "b", "Bob", lang="fa")
    return d


def stock(db, uid=U1):
    return db.q1("SELECT cash FROM users WHERE id=?", (uid,))["cash"]


def life(db, uid=U1):
    return db.q1("SELECT balance FROM life_accounts WHERE user_id=?", (uid,))["balance"]


def kv(db, key):
    r = db.q1("SELECT value FROM kv WHERE key=?", (key,))
    return int(r["value"]) if r else 0


def quotes(db):
    return db.q1("SELECT COUNT(*) c FROM fx_quotes")["c"]


# ============================================================================================ the code has no way back
def test_the_module_has_no_cash_to_stock_direction():
    assert not hasattr(cash, "C2S") and not hasattr(cash, "parse_cents")
    assert list(inspect.signature(cash.make_quote).parameters) == ["db", "uid", "amount", "now"]       # no direction parameter that could be set to the removed one
    assert not hasattr(lifeui, "fx_direction") and not hasattr(lifeui, "direction_of")
    assert not hasattr(C, "FX_MAX_CENTS")
    src = (PKG / "cash.py").read_text(encoding="utf-8")
    assert "core.mint" not in src and not re.search(r"UPDATE users SET cash\s*=\s*cash\s*\+", src)   # cash.py never credits stock money


def test_the_only_c2s_mentions_left_are_the_refusals():
    """«c2s» may only appear where something of the old version is REFUSED."""
    allowed = {"cash.py": 4, "lifeui.py": 1, "handlers.py": 3, "db.py": 1}
    for path in sorted(PKG.glob("*.py")):
        n = len(re.findall(r"c2s|C2S", path.read_text(encoding="utf-8")))
        assert n <= allowed.get(path.name, 0), f"{path.name}: {n} mentions of the removed direction"


def test_no_text_or_button_label_offers_the_removed_direction():
    for lang, table in (("fa", texts.FA), ("en", texts.EN)):
        for key in table:
            assert not key.startswith(("fx.dir", "fx.give")) and key not in ("fx.b.s2c", "fx.b.c2s", "fx.b.back"), (lang, key)
        for key in ("fx.home", "fx.confirm", "fx.usage", "cash.body"):
            body = table[key]
            assert "نقد به سهام" not in body and "Cash to stock" not in body and "cash to stock" not in body.lower().replace("cash cannot be turned into stock", ""), (lang, key)
    assert "e.fx_oneway" in texts.FA and "e.fx_oneway" in texts.EN
    assert "برگشت" in texts.FA["fx.home"] and "no way back" in texts.EN["fx.home"]


def test_the_documented_command_is_one_way():
    from hypemarket import docs
    fa, ex_fa, en, ex_en = docs.DOCS["fx"]
    assert "یک‌طرفه" in fa and "one-way" in en
    assert "نقد 5" not in ex_fa and "cash 5" not in ex_en                               # no example of the removed direction


# ============================================================================================ the exchange still works, one way
def test_stock_to_cash_still_works_atomically_and_the_ledger_agrees(db):
    s0, c0 = stock(db), life(db)
    q = cash.make_quote(db, U1, 400)
    r = cash.confirm(db, U1, q.id)
    assert (r.stock_before - r.stock_after, r.cash_after - r.cash_before) == (400 * C.UNIT, 200)
    assert (stock(db), life(db)) == (s0 - 400 * C.UNIT, c0 + 200)
    row = db.q1("SELECT * FROM life_ledger WHERE user_id=? AND kind='fx'", (U1,))
    assert (row["delta"], row["stock_delta"], row["balance_after"]) == (200, -400 * C.UNIT, c0 + 200)


def test_no_sequence_of_exchange_calls_can_ever_raise_the_stock_balance(db):
    """Fifty tries, valid and invalid, from both players: the stock balance never goes up and every exchange ledger row is stock -> cash."""
    import random
    rng = random.Random(7)
    m0 = kv(db, "minted")
    pool = [2, 4, 50, 250, 998, 1, 3, 0, -4, 2.5, "250", None, 10 ** 15, True]
    for i in range(50):
        uid = rng.choice((U1, U2))
        before = stock(db, uid)
        try:
            q = cash.make_quote(db, uid, rng.choice(pool))
            if rng.random() < 0.7:
                cash.confirm(db, uid, q.id)
            if rng.random() < 0.3:
                cash.confirm(db, uid, q.id)                                              # a repeat
        except GameError:
            pass
        assert stock(db, uid) <= before
    rows = db.qa("SELECT delta, stock_delta FROM life_ledger WHERE kind='fx'")
    assert all(r["delta"] > 0 and r["stock_delta"] < 0 and r["stock_delta"] == -r["delta"] * C.FX_UNITS_PER_CENT for r in rows)
    assert kv(db, "minted") == m0                                                        # the exchange never mints a coin


# ============================================================================================ what may still be lying around is refused
def _plant_c2s_quote(db, uid=U1, qid="abcd1234", state="open", src=500, dst=100_000):
    now = core.now()
    db.x("INSERT INTO fx_quotes(id,user_id,direction,src,dst,state,created_at,expires_at) VALUES(?,?,?,?,?,?,?,?)", (qid, uid, "c2s", src, dst, state, now, now + 120))
    return qid


def test_a_stored_cash_to_stock_preview_is_refused_and_moves_nothing(db):
    m0 = kv(db, "minted")
    for state in ("open", "done"):
        qid = _plant_c2s_quote(db, qid="abcd123" + ("4" if state == "open" else "5"), state=state)
        with pytest.raises(GameError) as e:
            cash.confirm(db, U1, qid)
        assert e.value.code == "fx_oneway"
        assert (stock(db), life(db)) == (100_000, 10_000) and kv(db, "minted") == m0
        assert db.q1("SELECT COUNT(*) c FROM life_ledger WHERE user_id=? AND kind='fx'", (U1,))["c"] == 0
    assert db.q1("SELECT state FROM fx_quotes WHERE id='abcd1234'")["state"] == "cancelled"        # the open one is voided so it cannot be tried again
    with pytest.raises(GameError) as e:
        cash.confirm(db, U1, "abcd1234")
    assert e.value.code == "fx_oneway" and (stock(db), life(db)) == (100_000, 10_000)               # still refused, still nothing moves


def test_an_old_exchange_row_in_the_ledger_is_never_rewritten(db):
    with db.tx():
        db.x("INSERT INTO life_ledger(user_id,kind,ref,delta,balance_after,stock_delta,note,created_at) VALUES(?,?,?,?,?,?,?,?)", (U1, "fx", "old-v018", -500, 9_500, 100_000, "cash->stock", 1))
    old = [tuple(r) for r in db.qa("SELECT * FROM life_ledger WHERE ref='old-v018'")]
    cash.confirm(db, U1, cash.make_quote(db, U1, 100).id)
    assert [tuple(r) for r in db.qa("SELECT * FROM life_ledger WHERE ref='old-v018'")] == old
    assert any(r["ref"] == "old-v018" for r in cash.history(db, U1, 10))                # still listed in the wallet history


# ============================================================================================ through the real handlers
@pytest.fixture()
def h():
    return tb.Harness()


def start(h, uid):
    h.say(uid, "شروع")


def last(calls, kind=EditMessageText):
    return [c for c in calls if isinstance(c, kind)][-1]


def data_of(call):
    return [b.callback_data for row in call.reply_markup.inline_keyboard for b in row]


def alerts(calls):
    return [c.text for c in calls if isinstance(c, AnswerCallbackQuery) and c.show_alert]


def snapshot(h, uid=1):
    return (stock(h.db, uid), life(h.db, uid), quotes(h.db), kv(h.db, "minted"))


@pytest.mark.parametrize("data", ["cs:a:c2s:500:1", "cs:a:c2s:all:1", "cs:d:c2s:1", "cs:a:c2s:1:1"])
def test_an_old_cash_to_stock_button_is_answered_with_a_refusal(h, data):
    start(h, 1)
    before = snapshot(h)
    out = h.press(1, data)
    assert alerts(out) and "یک‌طرفه" in alerts(out)[0]
    assert snapshot(h) == before and not [c for c in out if isinstance(c, EditMessageText)]


def test_an_open_old_preview_pressed_from_an_old_screen_changes_nothing(h):
    start(h, 1)
    _plant_c2s_quote(h.db, uid=1, qid="deadbeef")
    before = snapshot(h)
    out = h.press(1, "cs:ok:deadbeef:1")
    assert alerts(out) and "یک‌طرفه" in alerts(out)[0]
    assert (stock(h.db, 1), life(h.db, 1), kv(h.db, "minted")) == before[:2] + (before[3],)
    assert h.db.q1("SELECT state FROM fx_quotes WHERE id='deadbeef'")["state"] == "cancelled"


@pytest.mark.parametrize("text", ["صرافی نقد 5", "صرافی نقد", "صرافی دلار 5", "صرافی پول-نقد 5", "صرافی c2s 5", "currency cash 5", "currency usd 5", "currency dollars all", "صرافی نقد همه"])
def test_typing_the_removed_direction_is_refused_in_private_and_in_a_group(h, text):
    start(h, 1)
    before = snapshot(h)
    out = tb.Harness.texts(h.say(1, text))
    assert "یک‌طرفه" in out and "تأیید تبدیل" not in out
    out = tb.Harness.texts(h.say(1, text, chat=-100777))
    assert "یک‌طرفه" in out and "تأیید تبدیل" not in out
    assert snapshot(h) == before


def test_chat_that_merely_starts_with_the_word_is_not_an_exchange_command_in_a_group(h):
    start(h, 1)
    assert h.say(1, "صرافی نقد بیشتر از این حرفاست", chat=-100778) == []
    assert snapshot(h)[2] == 0


def test_every_exchange_screen_is_one_way(h):
    start(h, 1)
    screens = [last(h.say(1, "پول"), SendMessage), last(h.say(1, "صرافی"), SendMessage)]
    prev = last(h.press(1, "cs:a:s2c:250:1"))
    screens.append(prev)
    ok = next(x for x in data_of(prev) if x.startswith("cs:ok:"))
    screens.append(last(h.press(1, ok)))
    screens.append(last(h.press(1, "cs:no:" + ok.split(":")[2] + ":1")))
    for scr in screens:
        assert not [x for x in data_of(scr) if "c2s" in x]
        assert "نقد به سهام" not in scr.text and "← 🪙" not in scr.text
    assert stock(h.db, 1) == 75_000 and life(h.db, 1) == 10_125                          # and the one real exchange went through exactly once


def test_the_wallet_says_there_is_no_way_back(h):
    start(h, 1)
    msg = last(h.say(1, "پول"), SendMessage)
    assert "برگشتی نداره" in msg.text
    assert "برگشت" in last(h.say(1, "صرافی"), SendMessage).text
