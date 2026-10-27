"""v0.18: life cash (a second currency in exact cents), the one-time $100, the ledger, and the currency exchange.

Rules under test: separate balances, exact integer arithmetic, 1,000 🪙 = $5.00 (v0.18.1; v0.18 had $20) with no fee, stock money moving in multiples of 2 🪙, every confirmed exchange atomic and
single-use, repeated / stale / expired / foreign confirmations harmless, invalid amounts refused, the stock game's own progression untouched.
v0.18.2: the exchange is ONE-WAY (stock money -> cash). tests/test_v0182_exchange.py proves that no path turns cash into stock money."""
import re
import sqlite3

import pytest

import test_bot as tb
from hypemarket import cash, config as C, core, fmt, lifeui
from hypemarket.core import GameError
from hypemarket.db import Database, MIGRATIONS, SCHEMA_VERSION
from aiogram.methods import AnswerCallbackQuery, EditMessageText, SendMessage

U1, U2 = 41001, 41002


@pytest.fixture()
def db():
    core.set_clock(lambda: 1_700_000_000)
    d = Database(":memory:")
    core.ensure_user(d, U1, "a", "Ali", lang="fa")
    core.ensure_user(d, U2, "b", "Bob", lang="fa")
    return d


def kv(db, key):
    r = db.q1("SELECT value FROM kv WHERE key=?", (key,))
    return int(r["value"]) if r else 0


def stock(db, uid=U1):
    return db.q1("SELECT cash FROM users WHERE id=?", (uid,))["cash"]


def life(db, uid=U1):
    return db.q1("SELECT balance FROM life_accounts WHERE user_id=?", (uid,))["balance"]


def ledger(db, uid=U1):
    return [tuple(r) for r in db.qa("SELECT kind,ref,delta,balance_after FROM life_ledger WHERE user_id=? ORDER BY id", (uid,))]


# ================================================================================================ the starting $100
def test_a_new_player_gets_exactly_100_dollars_once(db):
    assert life(db) == 10_000 and C.CASH_START_CENTS == 10_000
    assert ledger(db) == [("grant", "start", 10_000, 10_000)]
    for _ in range(5):                                         # loading the player, opening the wallet, a second registration: nothing is granted again
        cash.ensure_account(db, U1)
        cash.balance(db, U1)
        core.ensure_user(db, U1, "a", "Ali", lang="fa")
    assert life(db) == 10_000 and len(ledger(db)) == 1
    assert cash.apply(db, U1, 10_000, "grant", "start") is None            # even a direct retry of the grant is a no-op
    assert life(db) == 10_000


def test_the_grant_belongs_to_the_player_not_to_a_group_or_a_city(db):
    assert [r["user_id"] for r in db.qa("SELECT user_id FROM life_accounts ORDER BY user_id")] == [U1, U2]
    cols = [r[1] for r in db.qa("PRAGMA table_info(life_accounts)")]
    assert "world_id" not in cols and "chat_id" not in cols and cols[0] == "user_id"


def test_the_two_currencies_are_separate_columns_and_never_mix(db):
    before_stock = stock(db)
    cash.apply(db, U1, 700, "snapp_fare", "t1")
    assert stock(db) == before_stock and life(db) == 10_700
    with db.tx():
        db.x("UPDATE users SET cash=cash+12345 WHERE id=?", (U1,))
    assert life(db) == 10_700


def test_the_migration_grants_every_existing_human_once_and_never_again(tmp_path):
    path = str(tmp_path / "old.db")
    core.set_clock(lambda: 1_700_000_000)
    d = Database(path)
    core.ensure_user(d, U1, "a", "Ali", lang="fa")
    core.ensure_user(d, U2, "b", "Bob", lang="fa")
    d.x("INSERT INTO users(id,name,created_at,bot,cash) VALUES(-5,'robot',1,1,100)")       # an automated trader
    before = [tuple(r) for r in d.qa("SELECT * FROM users ORDER BY id")]
    for tbl in ("life_ledger", "life_accounts", "fx_quotes", "work_tasks", "interactions"):
        d.x(f"DROP TABLE {tbl}")
    d.x("ALTER TABLE world_trips DROP COLUMN mode")
    d.x("PRAGMA user_version=7")
    d.close()
    d2 = Database(path)                                                                      # a v0.17 database opens on v0.18
    assert d2.q1("PRAGMA user_version")[0] == SCHEMA_VERSION == 9 and 8 in MIGRATIONS and 9 in MIGRATIONS
    assert [(r["user_id"], r["balance"]) for r in d2.qa("SELECT * FROM life_accounts ORDER BY user_id")] == [(U1, 10_000), (U2, 10_000)]
    assert len(d2.qa("SELECT * FROM life_ledger WHERE kind='grant'")) == 2
    assert d2.q1("SELECT 1 FROM life_accounts WHERE user_id=-5") is None                    # bots have no account
    assert [tuple(r) for r in d2.qa("SELECT * FROM users ORDER BY id")] == before            # no stock balance or profile was touched
    assert "mode" in [r[1] for r in d2.qa("PRAGMA table_info(world_trips)")]
    cash.apply(d2, U1, -2500, "snapp_fee", "x")
    d2.close()
    d3 = Database(path)                                                                      # reopening (a restart) grants nothing and keeps the balance
    assert [(r["user_id"], r["balance"]) for r in d3.qa("SELECT * FROM life_accounts ORDER BY user_id")] == [(U1, 7_500), (U2, 10_000)]
    assert len(d3.qa("SELECT * FROM life_ledger WHERE kind='grant'")) == 2
    core.ensure_user(d3, 99, "c", "Cy", lang="fa")                                          # a player who joins later gets theirs at registration
    assert d3.q1("SELECT balance FROM life_accounts WHERE user_id=99")["balance"] == 10_000


# ================================================================================================ the ledger
def test_every_change_is_one_ledger_row_and_repeating_it_changes_nothing(db):
    assert cash.apply(db, U1, 450, "snapp_fare", "7") == 10_450
    assert cash.apply(db, U1, 450, "snapp_fare", "7") is None                               # the same payout again
    assert cash.apply(db, U1, -20, "snapp_fee", "7") == 10_430
    assert life(db) == 10_430
    assert ledger(db)[-2:] == [("snapp_fare", "7", 450, 10_450), ("snapp_fee", "7", -20, 10_430)]
    assert sum(r[2] for r in ledger(db)) == life(db)                                         # the ledger always adds up to the balance


def test_a_balance_never_goes_below_zero_or_past_the_cap(db):
    with pytest.raises(GameError) as e:
        cash.apply(db, U1, -10_001, "snapp_fee", "big")
    assert e.value.code == "cash_low" and e.value.kw == {"need": 10_001, "have": 10_000}
    assert life(db) == 10_000 and len(ledger(db)) == 1                                       # the failed spend left no trace
    db.x("UPDATE life_accounts SET balance=? WHERE user_id=?", (C.CASH_CAP_CENTS - 5, U1))
    with pytest.raises(GameError) as e:
        cash.apply(db, U1, 6, "mcd_wage", "w")
    assert e.value.code == "cash_cap"
    with pytest.raises(sqlite3.IntegrityError):                                               # and the table itself refuses a negative balance
        db.x("UPDATE life_accounts SET balance=-1 WHERE user_id=?", (U1,))


@pytest.mark.parametrize("bad", [0, 1.5, "10", None, True, float("nan")])
def test_only_nonzero_integer_cents_can_be_applied(db, bad):
    with pytest.raises(ValueError):
        cash.apply(db, U1, bad, "mcd_wage", "z")
    assert life(db) == 10_000


def test_a_failing_transaction_leaves_the_balance_and_ledger_untouched(db):
    with pytest.raises(RuntimeError):
        with db.tx():
            cash.apply(db, U1, 999, "mcd_wage", "boom")
            raise RuntimeError("something later in the same action failed")
    assert life(db) == 10_000 and ledger(db) == [("grant", "start", 10_000, 10_000)]


# ================================================================================================ reading amounts
@pytest.mark.parametrize("text,coins", [("250", 250), ("1,000", 1000), ("۱٬۰۰۰", 1000), ("۲۵۰", 250), ("1000🪙", 1000), (" 50 ", 50)])
def test_whole_coin_amounts_are_understood(text, coins):
    assert cash.parse_coins(text) == coins


@pytest.mark.parametrize("text", ["2.5", "1e3", "-5", "0", "00", "NaN", "inf", "", "abc", "1_000", "٫5", "99999999999999999999", "1000000000001", "5%", "--5", "+5", "1,00", "5,25", "1,0000", "12,34", ",5", "5,"])
def test_malformed_coin_amounts_are_refused(text):
    assert cash.parse_coins(text) is None


# ================================================================================================ the exchange
def test_the_rate_is_exactly_1000_coins_for_5_dollars(db):
    assert C.FX_UNITS_PER_CENT * 500 == 1000 * C.UNIT                                         # 500 cents are worth exactly 1,000 coins
    assert C.fx_coin_step() == 2 and C.fx_cents_for_coins(1000) == 500 and C.fx_coins_for_cents(100) == 200
    q = cash.make_quote(db, U1, 1000)
    assert (q.src, q.dst) == (1000 * C.UNIT, 500)
    r = cash.confirm(db, U1, q.id)
    assert (r.stock_before, r.stock_after, r.cash_before, r.cash_after) == (100_000, 0, 10_000, 10_500)
    assert stock(db) == 0 and life(db) == 10_500                                              # the coins are gone for good: there is no way back
    assert fmt.coins_x(1 * 200, "en") == "2 🪙"                                               # one cent is exactly two coins


@pytest.mark.parametrize("coins", [2, 4, 6, 50, 250, 998])
def test_every_even_coin_amount_converts_exactly(db, coins):
    q = cash.make_quote(db, U1, coins)
    assert q.dst == coins // 2 and q.src == coins * C.UNIT and q.coins == coins
    r = cash.confirm(db, U1, q.id)
    assert r.cash_after - r.cash_before == coins // 2 and r.stock_before - r.stock_after == coins * C.UNIT


@pytest.mark.parametrize("coins,near", [(1, 2), (3, 2), (7, 6), (999, 998), (251, 250)])
def test_an_odd_coin_amount_is_refused_never_rounded(db, coins, near):
    s0, c0 = stock(db), life(db)
    with pytest.raises(GameError) as e:
        cash.make_quote(db, U1, coins)
    assert e.value.code == "fx_step" and e.value.kw == {"step": 2, "near": near}               # half a cent would need rounding: the player is told the nearest valid amount
    assert (stock(db), life(db)) == (s0, c0) and db.q1("SELECT COUNT(*) c FROM fx_quotes")["c"] == 0


def test_the_whole_balance_options(db):
    with db.tx():
        db.x("UPDATE users SET cash=? WHERE id=?", (123_456, U1))                              # 1,234.56 coins
    q = cash.make_quote(db, U1, None)
    assert q.coins == 1234 and q.dst == 617                                                   # only WHOLE steps of 2 coins move; the 56 hundredths stay
    cash.confirm(db, U1, q.id)
    assert stock(db) == 56
    with pytest.raises(GameError) as e:
        cash.make_quote(db, U1, None)                                                          # 0.56 coins left: nothing to convert
    assert e.value.code == "fx_nothing"
    with db.tx():
        db.x("UPDATE users SET cash=? WHERE id=?", (123_556, U1))                              # 1,235.56 coins: an odd whole number of coins, so 1,234 move and 1.56 stay
    q = cash.make_quote(db, U1, None)
    assert q.coins == 1234 and q.dst == 617
    cash.confirm(db, U1, q.id)
    assert stock(db) == 156                                                                    # 1.56 coins: one whole coin is not a whole cent, so nothing more can move
    with pytest.raises(GameError) as e:
        cash.make_quote(db, U1, None)
    assert e.value.code == "fx_nothing"
    assert cash.max_coins(db, U1) == 0


def test_supply_bookkeeping_follows_the_exchange(db):
    m0, b0 = kv(db, "minted"), kv(db, "burned")
    cash.confirm(db, U1, cash.make_quote(db, U1, 400).id)
    assert (kv(db, "minted") - m0, kv(db, "burned") - b0) == (0, 400 * C.UNIT)                 # coins leaving the stock economy are burned; nothing is ever minted by the exchange


@pytest.mark.parametrize("amount", [0, -1, -250, C.FX_MAX_COINS + 1, 10 ** 30, 2.5, 250.0, "250", object(), True, False, float("nan")])
def test_invalid_exchange_amounts_are_refused_and_nothing_changes(db, amount):
    s0, c0 = stock(db), life(db)
    with pytest.raises(GameError) as e:
        cash.make_quote(db, U1, amount)
    assert e.value.code == "fx_amount"
    assert (stock(db), life(db)) == (s0, c0) and db.q1("SELECT COUNT(*) c FROM fx_quotes")["c"] == 0


def test_you_cannot_exchange_more_than_you_have(db):
    with pytest.raises(GameError) as e:
        cash.make_quote(db, U1, 1002)
    assert e.value.code == "fx_low_stock" and e.value.kw == {"need": 1002 * C.UNIT, "have": 1000 * C.UNIT}
    with pytest.raises(GameError) as e:
        cash.make_quote(db, U1, "sideways")
    assert e.value.code == "fx_amount"
    assert (stock(db), life(db)) == (100_000, 10_000)


def test_the_cash_cap_stops_an_exchange_that_would_pass_it(db):
    db.x("UPDATE life_accounts SET balance=? WHERE user_id=?", (C.CASH_CAP_CENTS - 100, U1))
    with pytest.raises(GameError) as e:
        cash.make_quote(db, U1, 202)                                                 # +101 cents would pass the cap
    assert e.value.code == "cash_cap"
    assert cash.make_quote(db, U1, 200).dst == 100                                   # exactly up to the cap is fine
    assert cash.make_quote(db, U1, None).coins == 200                                # «everything» is trimmed to what still fits under the cap
    db.x("UPDATE life_accounts SET balance=? WHERE user_id=?", (C.CASH_CAP_CENTS, U1))
    with pytest.raises(GameError) as e:
        cash.make_quote(db, U1, None)
    assert e.value.code == "cash_cap"


def test_a_confirmed_exchange_cannot_be_confirmed_twice(db):
    q = cash.make_quote(db, U1, 100)
    cash.confirm(db, U1, q.id)
    s1, c1 = stock(db), life(db)
    for _ in range(3):
        with pytest.raises(GameError) as e:
            cash.confirm(db, U1, q.id)
        assert e.value.code == "fx_done"
    assert (stock(db), life(db)) == (s1, c1) and len([r for r in ledger(db) if r[0] == "fx"]) == 1


def test_a_cancelled_or_replaced_preview_cannot_be_confirmed(db):
    q = cash.make_quote(db, U1, 100)
    assert cash.cancel(db, U1, q.id) is True and cash.cancel(db, U1, q.id) is False
    with pytest.raises(GameError) as e:
        cash.confirm(db, U1, q.id)
    assert e.value.code == "fx_gone"
    q1 = cash.make_quote(db, U1, 100)
    q2 = cash.make_quote(db, U1, 200)                                                # a newer preview replaces the older one
    with pytest.raises(GameError):
        cash.confirm(db, U1, q1.id)
    assert cash.confirm(db, U1, q2.id).quote.coins == 200
    assert (stock(db), life(db)) == (100_000 - 200 * C.UNIT, 10_100)


def test_an_expired_preview_changes_nothing(db):
    now = [1_700_000_000]
    core.set_clock(lambda: now[0])
    q = cash.make_quote(db, U1, 100)
    now[0] += C.FX_QUOTE_TTL - 1
    assert cash.get_quote(db, q.id, U1).expires_at > core.now()
    now[0] += 1
    with pytest.raises(GameError) as e:
        cash.confirm(db, U1, q.id)
    assert e.value.code == "fx_expired"
    assert (stock(db), life(db)) == (100_000, 10_000)


def test_someone_elses_or_an_invented_preview_is_not_found(db):
    q = cash.make_quote(db, U1, 100)
    for token in (q.id, "00000000", "zzzz", "", "1' OR 1=1", "../../x", q.id.upper(), q.id + "0", 12345):
        with pytest.raises(GameError) as e:
            cash.confirm(db, U2, token)
        assert e.value.code == "fx_gone"
    assert cash.cancel(db, U2, q.id) is False                                                  # and U2 cannot cancel U1's preview either
    assert cash.get_quote(db, q.id, U1).state == "open"
    assert (stock(db, U2), life(db, U2)) == (100_000, 10_000)


def test_a_balance_that_changed_after_the_preview_makes_the_confirmation_fail_cleanly(db):
    q = cash.make_quote(db, U1, 800)
    with db.tx():
        db.x("UPDATE users SET cash=? WHERE id=?", (500 * C.UNIT, U1))                         # the player spent coins in between
    with pytest.raises(GameError) as e:
        cash.confirm(db, U1, q.id)
    assert e.value.code == "fx_low_stock"
    assert (stock(db), life(db)) == (500 * C.UNIT, 10_000) and len(ledger(db)) == 1             # nothing was deducted or credited...
    assert cash.get_quote(db, q.id, U1).state == "open"                                        # ...and the preview was not burned by the failed attempt


def test_an_error_inside_the_exchange_rolls_both_balances_back(db, monkeypatch):
    q = cash.make_quote(db, U1, 100)
    real = cash.apply

    def broken(*a, **k):
        raise RuntimeError("disk full")
    monkeypatch.setattr(cash, "apply", broken)
    with pytest.raises(RuntimeError):
        cash.confirm(db, U1, q.id)
    monkeypatch.setattr(cash, "apply", real)
    assert (stock(db), life(db)) == (100_000, 10_000)                                          # the coins were NOT taken without the dollars being given
    assert cash.get_quote(db, q.id, U1).state == "open"
    assert cash.confirm(db, U1, q.id).cash_after == 10_050                                     # and the same preview still works afterwards, once


def test_exchanging_does_not_touch_the_stock_games_own_progress(db):
    u0 = {k: db.q1("SELECT * FROM users WHERE id=?", (U1,))[k] for k in ("trades", "rebirths", "legacy_pts", "legacy_total", "spent_points", "bonus_points", "rep", "streak", "last_daily")}
    tables = {n: [tuple(r) for r in db.qa(f"SELECT * FROM {n}")] for n in ("stocks", "holdings", "perks", "quests", "achievements")}
    cash.confirm(db, U1, cash.make_quote(db, U1, 700).id)
    u1 = {k: db.q1("SELECT * FROM users WHERE id=?", (U1,))[k] for k in u0}
    assert u1 == u0
    assert {n: [tuple(r) for r in db.qa(f"SELECT * FROM {n}")] for n in tables} == tables


def test_stock_money_is_never_mixed_up_with_portfolio_value():
    src = open("hypemarket/cash.py", encoding="utf-8").read()
    assert "holdings" not in src and "shares" not in src and ".price" not in src                # the exchange reads users.cash and the life account only


# ================================================================================================ money formatting
def test_dollars_are_always_labelled_and_exact():
    assert fmt.usd(10_000, "en") == "$100.00" and fmt.usd(45, "en") == "$0.45" and fmt.usd(1, "en") == "$0.01"
    assert fmt.usd(123_456_789, "en") == "$1,234,567.89" and fmt.usd(-250, "en") == "-$2.50"
    fa = fmt.usd(10_000, "fa")
    assert fa.startswith("$") and "۱۰۰" in fa and "۰۰" in fa
    assert fmt.coins_x(25_000, "en") == "250 🪙" and fmt.coins_x(25_050, "en") == "250.5 🪙" and fmt.coins_x(1_000_000, "en") == "10,000 🪙"


# ================================================================================================ through the real handlers
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


def test_the_wallet_shows_both_currencies_under_their_own_names(h):
    start(h, 1)
    msg = last(h.say(1, "پول"), SendMessage)
    assert "پول نقد" in msg.text and "پول سهام" in msg.text and "$۱۰۰٫۰۰" in msg.text and "🪙" in msg.text
    assert "۱٬۰۰۰ 🪙 = $۵٫۰۰" in msg.text.replace("‏", "")
    assert "cs:fx:1" in data_of(msg) and "jb:h:1" in data_of(msg)
    for word in ("کیف پول", "wallet", "cash"):
        assert "پول نقد" in tb.Harness.texts(h.say(1, word)) or "Cash" in tb.Harness.texts(h.say(1, word))


def test_in_a_group_only_the_bare_word_opens_the_wallet(h):
    G = -100999
    start(h, 1)
    assert "پول نقد" in tb.Harness.texts(h.say(1, "پول", chat=G))
    assert h.say(1, "پول چقدره", chat=G) == [] and h.say(1, "این پول نیست", chat=G) == []


def test_the_exchange_end_to_end_with_buttons_and_a_double_press(h):
    start(h, 1)
    home = last(h.say(1, "صرافی"), SendMessage)
    assert "cs:a:s2c:250:1" in data_of(home) and "cs:a:s2c:all:1" in data_of(home) and "۱٬۰۰۰ 🪙 = $۵٫۰۰" in home.text
    assert not [x for x in data_of(home) if "c2s" in x or x.startswith("cs:d:")]                  # one screen, one direction: no direction picker at all
    prev = last(h.press(1, "cs:a:s2c:250:1"))
    assert "۲۵۰ 🪙" in prev.text and "$۱٫۲۵" in prev.text and "۷۵۰ 🪙" in prev.text and "$۱۰۱٫۲۵" in prev.text      # what you give, get, and both balances after
    ok = next(x for x in data_of(prev) if x.startswith("cs:ok:"))
    no = next(x for x in data_of(prev) if x.startswith("cs:no:"))
    assert all(len(x.encode()) <= 64 for x in data_of(prev)) and "250" not in ok                  # the confirm button carries only the preview's id
    res = h.press(1, ok)
    assert "تبدیل انجام شد" in last(res).text
    db = h.db
    assert stock(db, 1) == 75_000 and life(db, 1) == 10_125
    again = h.press(1, ok)                                                                         # a second press of the same button
    assert alerts(again) and (stock(db, 1), life(db, 1)) == (75_000, 10_125)
    assert len([r for r in ledger(db, 1) if r[0] == "fx"]) == 1
    late = h.press(1, no)                                                                          # cancelling something already done changes nothing
    assert (stock(db, 1), life(db, 1)) == (75_000, 10_125) and "لغو شد" in last(late).text


def test_only_the_owner_can_press_exchange_buttons(h):
    start(h, 1)
    start(h, 2)
    prev = last(h.press(1, "cs:a:s2c:100:1"))
    ok = next(x for x in data_of(prev) if x.startswith("cs:ok:"))
    out = h.press(2, ok)                                                                           # a player presses somebody else's confirm button
    assert not [c for c in out if isinstance(c, EditMessageText)]
    assert (stock(h.db, 1), life(h.db, 1), stock(h.db, 2), life(h.db, 2)) == (100_000, 10_000, 100_000, 10_000)
    forged = f"cs:ok:{ok.split(':')[2]}:2"                                                         # ...or rewrites the id at the end to their own
    out = h.press(2, forged)
    assert alerts(out) and (stock(h.db, 1), life(h.db, 1), stock(h.db, 2), life(h.db, 2)) == (100_000, 10_000, 100_000, 10_000)


def test_hand_made_exchange_buttons_do_nothing_harmful(h):
    start(h, 1)
    before = (stock(h.db, 1), life(h.db, 1))
    for data in ("cs:a:s2c:99999999999999999999:1", "cs:a:s2c:-5:1", "cs:a:s2c:2.5:1", "cs:a:c2s:0:1", "cs:a:x:5:1", "cs:a:s2c:1e3:1", "cs:ok:zzz:1", "cs:ok:00000000:1",
                 "cs:d:sideways:1", "cs:zz:1", "cs:a:s2c:5", "cs:ok:1"):
        h.press(1, data)
        assert (stock(h.db, 1), life(h.db, 1)) == before, data


def test_typed_exchange_commands(h):
    start(h, 1)
    for form in ("صرافی 250", "صرافی سهام 250", "currency 250", "currency stocks 250"):
        t1 = tb.Harness.texts(h.say(1, form))
        assert "تأیید تبدیل" in t1 and "$۱٫۲۵" in t1, form
    t3 = tb.Harness.texts(h.say(1, "currency stocks all"))
    assert "۱٬۰۰۰ 🪙" in t3 and "$۵٫۰۰" in t3
    assert "$۵٫۰۰" in tb.Harness.texts(h.say(1, "صرافی همه"))
    odd = tb.Harness.texts(h.say(1, "صرافی سهام 251"))
    assert "مضرب ۲" in odd and "۲۵۰" in odd                                                        # half a cent: refused, with the nearest valid amount
    for bad in ("صرافی سهام 2.5", "صرافی سهام 0", "صرافی سهام -5", "صرافی 5.255", "صرافی 0", "صرافی سهام 1e9", "صرافی سهام abc", "صرافی سهام 99999999999999999999"):
        out = tb.Harness.texts(h.say(1, bad))
        assert "این مقدار درست نیست" in out or "کافی نیست" in out, (bad, out)
    assert "کافی نیست" in tb.Harness.texts(h.say(1, "صرافی سهام 1002"))
    assert (stock(h.db, 1), life(h.db, 1)) == (100_000, 10_000)                                      # previews never move anything


def test_a_private_chat_prompt_takes_the_next_message_as_the_amount(h):
    start(h, 1)
    h.say(1, "صرافی")                                                                              # opening the exchange in private makes the next plain message the amount
    assert "$۲٫۵۰" in tb.Harness.texts(h.say(1, "۵۰۰")) and life(h.db, 1) == 10_000
    h.press(1, "cs:fx:1")
    assert "$۱٫۲۵" in tb.Harness.texts(h.say(1, "250")) and life(h.db, 1) == 10_000


def test_group_chat_exchange_words_need_a_valid_form(h):
    G = -100998
    start(h, 1)
    assert "صرافی" in tb.Harness.texts(h.say(1, "صرافی", chat=G))
    assert "تأیید تبدیل" in tb.Harness.texts(h.say(1, "صرافی سهام 100", chat=G))
    assert "تأیید تبدیل" in tb.Harness.texts(h.say(1, "صرافی 100", chat=G))
    assert h.say(1, "صرافی خوبیه ولی گرونه", chat=G) == [] and h.say(1, "صرافی سهام بیشتر از این", chat=G) == []


def test_the_exchange_leaves_every_stock_command_alone(h):
    start(h, 1)
    h.say(1, "سهام عرضه SPEED اسپید | سهام اسپید")
    h.say(1, "سهام خرید 3 SPEED")
    s = [tuple(r) for r in h.db.qa("SELECT * FROM holdings")]
    before = life(h.db, 1)
    h.say(1, "سهام پورتفوی")
    h.say(1, "سهام بازار")
    assert life(h.db, 1) == before and [tuple(r) for r in h.db.qa("SELECT * FROM holdings")] == s
