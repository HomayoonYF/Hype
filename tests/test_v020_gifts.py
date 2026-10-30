"""v0.20: admin gift codes: the admin's «ساخت گیفت کارت» flow in /admin, and the player's `کد هدیه <code>`.

Covered here: how a code is made and typed back, the three rewards (stock money, life cash, character XP) through their own ledgers, exactly-once / once-per-player / the player limit,
atomicity (a failing reward leaves nothing behind), two connections racing, a restart, the admin flow step by step with cancel / wrong input / double tap / expiry, who may do what
(handler, button and execution), and the Persian answers. A real Telegram client was not used."""
import dataclasses
import sqlite3
import threading

import pytest
from aiogram.methods import AnswerCallbackQuery, EditMessageText, SendMessage

import charkit
import mapkit
import test_bot as tb
from hypemarket import adminpanel, cash, character, charxp, config as C, core, fmt, giftui, gifts, handlers, sections, textparse
from hypemarket.core import GameError
from hypemarket.db import Database
from hypemarket.texts import ALIASES, EN, FA, t

REAL_CHARACTER_SETUP = True                                          # (only the finished-character case of an XP code cares; the tests finish characters themselves)

ADMIN = 1
P1, P2, P3, P4, P5 = 884001, 884002, 884003, 884004, 884005
GROUP = -100951
CODE_RE = r"HM-[A-HJ-NP-Z2-9]{4}-[A-HJ-NP-Z2-9]{4}-[A-HJ-NP-Z2-9]{4}"


@pytest.fixture()
def h():
    return tb.Harness()


@pytest.fixture()
def db():
    d = Database(":memory:")
    core.set_clock(lambda: 1_700_000_000)
    for uid in (P1, P2, P3, P4, P5, ADMIN):
        core.ensure_user(d, uid, None, f"U{uid}")
    return d


def make(db, reward="stock_money", n=500, uses=3, by=ADMIN):
    return gifts.create(db, by, reward, gifts.to_units(reward, n), uses, frozenset({ADMIN}))


def stock(db, uid):
    return core.get_user(db, uid)["cash"]


def texts(calls):
    return tb.Harness.texts(calls)


def count(db, table, where="1", args=()):
    return db.q1(f"SELECT COUNT(*) c FROM {table} WHERE {where}", args)["c"]


# ============================================================================================== the code itself
def test_codes_have_one_shape_use_only_the_safe_alphabet_and_do_not_repeat():
    seen = set()
    for _ in range(3000):
        c = gifts.make_code()
        assert len(c) == 17 and c[:3] == "HM-" and c[7] == c[12] == "-"
        assert set(c.replace("-", "")[2:]) <= set(gifts.ALPHABET) and not set(c) & set("01OIl")
        seen.add(c)
    assert len(seen) == 3000                                                       # 60 random bits: no clash in any practical run


def test_codes_come_from_the_operating_systems_generator_not_from_a_seeded_one(monkeypatch):
    import random
    monkeypatch.setattr(random, "choice", lambda *a: (_ for _ in ()).throw(AssertionError("random module used")))
    monkeypatch.setattr(random, "random", lambda *a: (_ for _ in ()).throw(AssertionError("random module used")))
    random.seed(1)
    a = gifts.make_code()
    random.seed(1)
    assert gifts.make_code() != a                                                  # reseeding the module changes nothing: the code does not depend on it


@pytest.mark.parametrize("typed", [
    ["HM-ABCD-EFGH-JKMN"], ["hm-abcd-efgh-jkmn"], ["HM", "ABCD", "EFGH", "JKMN"], ["hm abcd efgh jkmn"], ["ABCDEFGHJKMN"], ["abcd-efgh-jkmn"], ["HM_ABCD_EFGH_JKMN"],
    ["‏HM-ABCD-EFGH-JKMN‎"], ["  HM-ABCD-EFGH-JKMN  "],
])
def test_a_code_is_understood_with_any_spacing_case_and_marks(typed):
    assert gifts.normalize(typed) == "HM-ABCD-EFGH-JKMN"


def test_digits_of_any_script_are_read_as_digits():
    assert gifts.normalize(["HM-AB۲D-EF۳H-JK۹N"]) == "HM-AB2D-EF3H-JK9N"
    assert gifts.normalize(["HM-AB٢D-EF٣H-JK٩N"]) == "HM-AB2D-EF3H-JK9N"


@pytest.mark.parametrize("typed", [
    [], [""], ["x"], ["HM-ABCD-EFGH"], ["HM-ABCD-EFGH-JKMNP"], ["HM-ABCD-EFGH-JKM0"], ["HM-ABCD-EFGH-JKMO"], ["HM-ABCD-EFGH-JKM1"], ["HM-ABCD-EFGH-JKMI"], ["HM-ABCD-EFGH-JK!N"],
    ["HM-ABCD-EFGH-JKمN"], ["کد"], ["A" * 200], [None], [5], ["HM-ABCD-EFGH-JKMN; DROP TABLE gift_codes"], ["HM-ABCD-EFGH-JKMN\nHM-ABCD-EFGH-JKMN"],
])
def test_anything_that_cannot_be_a_code_is_refused_before_any_lookup(typed):
    assert gifts.normalize(typed) is None


def test_normalize_is_idempotent_on_made_codes():
    for _ in range(50):
        c = gifts.make_code()
        assert gifts.normalize([c]) == c == gifts.normalize([gifts.normalize([c])])


@pytest.mark.parametrize("raw,lo,hi,expected", [
    ("500", 1, 1000, 500), ("۵۰۰", 1, 1000, 500), ("٥٠٠", 1, 1000, 500), ("1,000", 1, 1000, 1000), ("۱٬۰۰۰", 1, 1000, 1000), (" 7 ", 1, 1000, 7), ("1", 1, 1, 1),
    ("0", 1, 1000, None), ("1001", 1, 1000, None), ("-5", 1, 1000, None), ("+5", 1, 1000, None), ("2.5", 1, 1000, None), ("2٫5", 1, 1000, None), ("1e3", 1, 1000, None),
    ("1,00", 1, 1000, None), ("12abc", 1, 1000, None), ("", 1, 1000, None), ("abc", 1, 1000, None), ("5 5", 1, 1000, None), (None, 1, 1000, None), (5, 1, 1000, None),
    ("٠", 1, 1000, None), ("99999999999999999", 1, 10 ** 18, None),
])
def test_amounts_are_whole_numbers_inside_the_limit(raw, lo, hi, expected):
    assert gifts.parse_whole(raw, lo, hi) == expected


def test_the_units_of_each_reward():
    assert gifts.to_units("stock_money", 5) == 5 * C.UNIT and gifts.to_units("life_cash", 5) == 500 and gifts.to_units("char_xp", 5) == 5
    for r in gifts.REWARDS:
        assert gifts.from_units(r, gifts.to_units(r, 7)) == 7
    assert gifts.limit("stock_money") == C.GIFT_MAX_STOCK_COINS


# ============================================================================================== making a code (the database layer)
def test_only_an_admin_makes_a_code_and_nothing_is_stored_otherwise(db):
    with pytest.raises(GameError) as e:
        gifts.create(db, P1, "stock_money", 500 * C.UNIT, 3, frozenset({ADMIN}))
    assert e.value.code == "gift_denied" and count(db, "gift_codes") == 0


@pytest.mark.parametrize("reward,amount,uses", [
    ("nonsense", 100, 1), ("stock_money", 0, 1), ("stock_money", -100, 1), ("stock_money", 150, 1), ("stock_money", (C.GIFT_MAX_STOCK_COINS + 1) * C.UNIT, 1),
    ("life_cash", 150, 1), ("life_cash", (C.GIFT_MAX_CASH_DOLLARS + 1) * 100, 1), ("char_xp", C.GIFT_MAX_XP + 1, 1), ("char_xp", 0, 1),
    ("stock_money", 100, 0), ("stock_money", 100, -1), ("stock_money", 100, C.GIFT_MAX_USES + 1), ("stock_money", True, 1), ("stock_money", 100, True), ("stock_money", 1.5, 1),
])
def test_a_code_outside_the_limits_is_refused(db, reward, amount, uses):
    with pytest.raises((ValueError, GameError)):
        gifts.create(db, ADMIN, reward, amount, uses, frozenset({ADMIN}))
    assert count(db, "gift_codes") == 0


def test_a_made_code_is_stored_with_everything_about_it(db):
    row = make(db, "life_cash", 25, 7)
    assert gifts.get(db, row["code"])["reward_type"] == "life_cash"
    r = gifts.get(db, row["code"])
    assert (r["amount"], r["max_uses"], r["used_count"], r["created_by"], r["created_at"]) == (2500, 7, 0, ADMIN, 1_700_000_000)
    assert len({make(db)["code"] for _ in range(20)}) == 20


def test_the_tables_protect_a_code_from_being_changed_or_removed(db):
    row = make(db)
    code = row["code"]
    for sql in ("UPDATE gift_codes SET amount=amount+1 WHERE code=?", "UPDATE gift_codes SET max_uses=99 WHERE code=?", "UPDATE gift_codes SET reward_type='char_xp' WHERE code=?",
                "UPDATE gift_codes SET created_by=5 WHERE code=?", "UPDATE gift_codes SET code='HM-AAAA-AAAA-AAAA' WHERE code=?", "DELETE FROM gift_codes WHERE code=?"):
        with pytest.raises(sqlite3.IntegrityError):
            db.x(sql, (code,))
    db.x("UPDATE gift_codes SET used_count=1 WHERE code=?", (code,))                      # only the counter moves, and only up
    with pytest.raises(sqlite3.IntegrityError):
        db.x("UPDATE gift_codes SET used_count=0 WHERE code=?", (code,))
    with pytest.raises(sqlite3.IntegrityError):
        db.x("UPDATE gift_codes SET used_count=999 WHERE code=?", (code,))                  # never past the limit


def test_a_redemption_row_cannot_be_invented_changed_or_removed(db):
    row = make(db)
    code = row["code"]
    with pytest.raises(sqlite3.IntegrityError):
        db.x("INSERT INTO gift_redemptions(code,user_id,reward_type,amount,redeemed_at) VALUES('HM-AAAA-AAAA-AAAA',?,?,?,1)", (P1, "stock_money", 100))       # unknown code
    with pytest.raises(sqlite3.IntegrityError):
        db.x("INSERT INTO gift_redemptions(code,user_id,reward_type,amount,redeemed_at) VALUES(?,?,?,?,1)", (code, P1, "stock_money", 1))                      # not the code's reward
    gifts.redeem(db, P1, code)
    with pytest.raises(sqlite3.IntegrityError):
        db.x("UPDATE gift_redemptions SET user_id=? WHERE code=?", (P2, code))
    with pytest.raises(sqlite3.IntegrityError):
        db.x("DELETE FROM gift_redemptions WHERE code=?", (code,))
    with pytest.raises(sqlite3.IntegrityError):
        db.x("INSERT INTO gift_redemptions(code,user_id,reward_type,amount,redeemed_at) VALUES(?,?,?,?,1)", (code, P1, row["reward_type"], row["amount"]))   # twice for one player


# ============================================================================================== the three rewards
def test_stock_money_goes_to_the_stock_balance_and_is_counted_as_minted(db):
    code = make(db, "stock_money", 500, 3)["code"]
    before, minted = stock(db, P1), core.kv_get(db, "minted", 0)
    r = gifts.redeem(db, P1, code, now=1_700_000_100)
    assert r["reward"] == "stock_money" and r["amount"] == 500 * C.UNIT and r["balance"] == before + 500 * C.UNIT == stock(db, P1)
    assert int(core.kv_get(db, "minted", 0) or 0) == int(minted or 0) + 500 * C.UNIT
    red = db.q1("SELECT * FROM gift_redemptions WHERE code=?", (code,))
    assert (red["user_id"], red["reward_type"], red["amount"], red["redeemed_at"]) == (P1, "stock_money", 500 * C.UNIT, 1_700_000_100)
    assert gifts.get(db, code)["used_count"] == 1
    assert count(db, "life_ledger", "kind='gift'") == 0 and count(db, "character_xp_log") == 0   # nothing else moved


def test_life_cash_goes_through_the_cash_ledger_once(db):
    code = make(db, "life_cash", 25, 3)["code"]
    start = cash.balance(db, P1)
    r = gifts.redeem(db, P1, code)
    assert r["reward"] == "life_cash" and r["balance"] == start + 2500 == cash.balance(db, P1)
    row = db.q1("SELECT * FROM life_ledger WHERE user_id=? AND kind='gift'", (P1,))
    assert row["ref"] == code and row["delta"] == 2500 and row["balance_after"] == start + 2500
    assert stock(db, P1) == stock(db, P2)                                                          # the stock balance is untouched


def test_character_xp_goes_through_the_xp_grant_once(db):
    charkit.complete(db, P1)
    code = make(db, "char_xp", 150, 3)["code"]
    before = charxp.get(db, P1)
    r = gifts.redeem(db, P1, code)
    assert r["xp"] == before + 150 == charxp.get(db, P1) and r["level"] == charxp.level_for(before + 150)
    log = db.q1("SELECT * FROM character_xp_log WHERE user_id=?", (P1,))
    assert (log["source"], log["ref"], log["delta"]) == ("gift", code, 150)
    assert stock(db, P1) == stock(db, P2) and count(db, "life_ledger", "kind='gift'") == 0


def test_an_xp_code_needs_a_finished_character_and_is_not_used_up_without_one(db):
    code = make(db, "char_xp", 150, 1)["code"]
    with pytest.raises(GameError) as e:
        gifts.redeem(db, P1, code)                                                                 # no character at all
    assert e.value.code == "gift_char"
    character.ensure_profile(db, P1)                                                               # a pending one
    with pytest.raises(GameError) as e:
        gifts.redeem(db, P1, code)
    assert e.value.code == "gift_char"
    assert gifts.get(db, code)["used_count"] == 0 and count(db, "gift_redemptions") == 0 and count(db, "character_xp_log") == 0
    charkit.complete(db, P1)
    assert gifts.redeem(db, P1, code)["reward"] == "char_xp"                                       # the place was still free
    assert gifts.get(db, code)["used_count"] == 1


def test_stock_and_cash_codes_do_not_need_a_character(db):
    assert character.get(db, P2) is None
    assert gifts.redeem(db, P2, make(db, "stock_money")["code"])["reward"] == "stock_money"
    assert gifts.redeem(db, P2, make(db, "life_cash", 5)["code"])["reward"] == "life_cash"


# ============================================================================================== once per player, the limit, retries
def test_a_player_gets_a_code_once_and_a_repeat_changes_nothing(db):
    code = make(db, "stock_money", 500, 5)["code"]
    gifts.redeem(db, P1, code)
    snap = (stock(db, P1), core.kv_get(db, "minted", 0), count(db, "gift_redemptions"), gifts.get(db, code)["used_count"])
    for _ in range(3):
        with pytest.raises(GameError) as e:
            gifts.redeem(db, P1, code)
        assert e.value.code == "gift_used"
    assert snap == (stock(db, P1), core.kv_get(db, "minted", 0), count(db, "gift_redemptions"), gifts.get(db, code)["used_count"])


def test_a_code_is_good_for_exactly_its_number_of_different_players(db):
    code = make(db, "stock_money", 10, 3)["code"]
    got, refused = [], []
    for uid in (P1, P2, P3, P4, P5):
        try:
            gifts.redeem(db, uid, code)
            got.append(uid)
        except GameError as e:
            refused.append((uid, e.code))
    assert got == [P1, P2, P3] and refused == [(P4, "gift_exhausted"), (P5, "gift_exhausted")]
    assert gifts.get(db, code)["used_count"] == 3 == count(db, "gift_redemptions", "code=?", (code,))
    assert stock(db, P4) == stock(db, P5) < stock(db, P1)
    with pytest.raises(GameError) as e:
        gifts.redeem(db, P1, code)
    assert e.value.code == "gift_used"                                                             # a player who has it is told so, even when it is full


def test_each_code_pays_each_player_separately(db):
    a, b = make(db, "stock_money", 10, 2)["code"], make(db, "stock_money", 20, 2)["code"]
    s = stock(db, P1)
    gifts.redeem(db, P1, a)
    gifts.redeem(db, P1, b)
    assert stock(db, P1) == s + 30 * C.UNIT


def test_bad_unknown_and_foreign_accounts_are_refused(db):
    code = make(db)["code"]
    for bad, why in (("HM-AAAA-AAAA-AAAA", "gift_unknown"), ("nonsense", "gift_bad"), ("", "gift_bad"), (code.lower(), "gift_bad")):
        with pytest.raises(GameError) as e:
            gifts.redeem(db, P1, bad)
        assert e.value.code == why
    with pytest.raises(GameError) as e:
        gifts.redeem(db, 424242, code)                                                             # not a registered player
    assert e.value.code == "no_user"
    core.ensure_user(db, 777, None, "Bot")
    db.x("UPDATE users SET bot=1 WHERE id=777")
    with pytest.raises(GameError) as e:
        gifts.redeem(db, 777, code)
    assert e.value.code == "no_user"
    assert gifts.get(db, code)["used_count"] == 0 and count(db, "gift_redemptions") == 0


# ============================================================================================== atomicity
def test_a_failure_after_the_use_was_taken_leaves_nothing_behind(db, monkeypatch):
    code = make(db, "stock_money", 500, 3)["code"]
    snap = lambda: (stock(db, P1), core.kv_get(db, "minted", 0), count(db, "gift_redemptions"), gifts.get(db, code)["used_count"])
    before = snap()
    monkeypatch.setattr(core, "mint", lambda *a, **k: (_ for _ in ()).throw(RuntimeError("crash while paying")))
    with pytest.raises(RuntimeError):
        gifts.redeem(db, P1, code)
    assert snap() == before                                                                        # no coin, no use, no row
    monkeypatch.undo()
    core.set_clock(lambda: 1_700_000_000)
    assert gifts.redeem(db, P1, code)["reward"] == "stock_money" and stock(db, P1) == before[0] + 500 * C.UNIT      # and it can simply be tried again


def test_a_failing_xp_grant_or_cash_ledger_leaves_nothing_behind(db, monkeypatch):
    charkit.complete(db, P1)
    xp_code, cash_code = make(db, "char_xp", 50, 3)["code"], make(db, "life_cash", 10, 3)["code"]
    cash.ensure_account(db, P1)
    snap = lambda: (charxp.get(db, P1), cash.balance(db, P1), count(db, "gift_redemptions"), count(db, "life_ledger"), count(db, "character_xp_log"),
                    gifts.get(db, xp_code)["used_count"], gifts.get(db, cash_code)["used_count"])
    before = snap()
    monkeypatch.setattr(charxp, "grant", lambda *a, **k: (_ for _ in ()).throw(RuntimeError("boom")))
    with pytest.raises(RuntimeError):
        gifts.redeem(db, P1, xp_code)
    assert snap() == before
    monkeypatch.setattr(cash, "apply", lambda *a, **k: (_ for _ in ()).throw(RuntimeError("boom")))
    with pytest.raises(RuntimeError):
        gifts.redeem(db, P1, cash_code)
    assert snap() == before


def test_a_cash_balance_at_its_cap_cannot_take_the_gift_and_the_code_is_not_used_up(db):
    code = make(db, "life_cash", 100, 2)["code"]
    cash.ensure_account(db, P1)
    db.x("UPDATE life_accounts SET balance=? WHERE user_id=?", (C.CASH_CAP_CENTS - 50, P1))
    with pytest.raises(GameError) as e:
        gifts.redeem(db, P1, code)
    assert e.value.code == "gift_unavailable"
    assert gifts.get(db, code)["used_count"] == 0 and count(db, "gift_redemptions") == 0 and count(db, "life_ledger", "kind='gift'") == 0
    assert gifts.redeem(db, P2, code)["reward"] == "life_cash"                                    # somebody else still can


def test_the_reward_ledgers_alone_also_refuse_a_second_payment(db):
    """Belt and braces: even with the redemption row gone from the picture, the two ledgers pay one (player, code) once."""
    charkit.complete(db, P1)
    xp, cs = make(db, "char_xp", 50, 2)["code"], make(db, "life_cash", 10, 2)["code"]
    assert charxp.grant(db, P1, 50, "gift", xp) is not None and charxp.grant(db, P1, 50, "gift", xp) is None
    assert cash.apply(db, P1, 1000, "gift", cs) is not None and cash.apply(db, P1, 1000, "gift", cs) is None
    with pytest.raises(GameError) as e:                                                            # such a ledger row without a redemption never pays again
        gifts.redeem(db, P1, xp)
    assert e.value.code == "gift_used"
    assert count(db, "gift_redemptions") == 0 and gifts.get(db, xp)["used_count"] == 0


# ============================================================================================== two connections, a restart
def test_two_players_racing_for_the_last_place_cannot_both_get_it(tmp_path):
    path = str(tmp_path / "race.db")
    boot = Database(path)
    core.set_clock(lambda: 1_700_000_000)
    for uid in range(1, 11):
        core.ensure_user(boot, 900 + uid, None, f"R{uid}")
    code = gifts.create(boot, ADMIN, "stock_money", 100 * C.UNIT, 1, frozenset({ADMIN}))["code"]
    barrier, out, lock = threading.Barrier(10), [], threading.Lock()

    def worker(uid):
        d = Database(path)
        barrier.wait()
        try:
            gifts.redeem(d, uid, code)
            res = "ok"
        except GameError as e:
            res = e.code
        with lock:
            out.append((uid, res))

    ts = [threading.Thread(target=worker, args=(900 + i,)) for i in range(1, 11)]
    [t_.start() for t_ in ts]
    [t_.join() for t_ in ts]
    assert sorted(r for _u, r in out).count("ok") == 1 and all(r in ("ok", "gift_exhausted") for _u, r in out)
    chk = Database(path)
    assert gifts.get(chk, code)["used_count"] == 1 == count(chk, "gift_redemptions")
    bal = [stock(chk, 900 + uid) for uid in range(1, 11)]
    assert max(bal) - min(bal) == 100 * C.UNIT and bal.count(max(bal)) == 1 and bal.count(min(bal)) == 9          # exactly one player got the 100 coins


def test_the_same_player_racing_with_himself_is_paid_once(tmp_path):
    path = str(tmp_path / "self.db")
    boot = Database(path)
    core.set_clock(lambda: 1_700_000_000)
    core.ensure_user(boot, P1, None, "Me")
    code = gifts.create(boot, ADMIN, "life_cash", 2500, 5, frozenset({ADMIN}))["code"]
    start = cash.balance(boot, P1)
    barrier, out, lock = threading.Barrier(8), [], threading.Lock()

    def worker():
        d = Database(path)
        barrier.wait()
        try:
            gifts.redeem(d, P1, code)
            res = "ok"
        except GameError as e:
            res = e.code
        with lock:
            out.append(res)

    ts = [threading.Thread(target=worker) for _ in range(8)]
    [t_.start() for t_ in ts]
    [t_.join() for t_ in ts]
    assert out.count("ok") == 1 and out.count("gift_used") == 7
    chk = Database(path)
    assert cash.balance(chk, P1) == start + 2500 and count(chk, "life_ledger", "kind='gift'") == 1 and gifts.get(chk, code)["used_count"] == 1


def test_a_restart_keeps_the_codes_their_counters_and_who_has_redeemed(tmp_path):
    path = str(tmp_path / "game.db")
    d1 = Database(path)
    core.set_clock(lambda: 1_700_000_000)
    for uid in (P1, P2, P3, ADMIN):
        core.ensure_user(d1, uid, None, f"U{uid}")
    code = gifts.create(d1, ADMIN, "stock_money", 10 * C.UNIT, 2, frozenset({ADMIN}))["code"]
    gifts.redeem(d1, P1, code)
    d2 = Database(path)                                                                            # the bot restarted
    assert gifts.get(d2, code)["used_count"] == 1
    with pytest.raises(GameError) as e:
        gifts.redeem(d2, P1, code)
    assert e.value.code == "gift_used"
    assert gifts.redeem(d2, P2, code)["reward"] == "stock_money"
    d3 = Database(path)
    with pytest.raises(GameError) as e:
        gifts.redeem(d3, P3, code)
    assert e.value.code == "gift_exhausted"


# ============================================================================================== the admin flow, through the real handlers
def panel(calls):
    return [c for c in calls if isinstance(c, (EditMessageText, SendMessage))][-1]


def cbs(msg):
    return [b.callback_data for row in msg.reply_markup.inline_keyboard for b in row] if getattr(msg, "reply_markup", None) else []


def toasts(calls):
    return [c.text for c in calls if isinstance(c, AnswerCallbackQuery) and c.text]


def start_gift(h, reward="stock_money", amount="500", uses="3", confirm=True):
    """The admin's whole walk; returns the last screen (the made code, or the confirm screen)."""
    h.say(ADMIN, "/start")
    h.press(ADMIN, f"ad:gift:{ADMIN}")
    h.press(ADMIN, f"ad:gt:{reward}:{ADMIN}")
    h.say(ADMIN, amount)
    out = h.say(ADMIN, uses)
    m = panel(out)
    if not confirm:
        return m
    ok = [c for c in cbs(m) if c.startswith("ad:gok:")][0]
    return panel(h.press(ADMIN, ok))


def made_code(msg):
    import re
    m = re.search(CODE_RE, msg.text)
    assert m, msg.text
    return m.group(0)


def test_the_panel_has_the_gift_button_and_the_list(h):
    m = h.say(ADMIN, "/admin")[-1]
    assert cbs(m)[2:] == [f"ad:gift:{ADMIN}", f"ad:gl:{ADMIN}"]
    assert any(FA["ad.b.gift"] == b.text for row in m.reply_markup.inline_keyboard for b in row) and "ساخت گیفت کارت" in FA["ad.b.gift"]


def test_the_admin_walks_through_the_flow_and_gets_a_code_that_players_can_redeem(h):
    made = start_gift(h, "life_cash", "25", "2")
    code = made_code(made)
    assert fmt.usd(2500, "fa") in made.text and fmt.num(2, "fa") in made.text and f"کد هدیه {code}" in made.text
    row = gifts.get(h.db, code)
    assert (row["reward_type"], row["amount"], row["max_uses"], row["used_count"], row["created_by"]) == ("life_cash", 2500, 2, 0, ADMIN)
    assert count(h.db, "gift_codes") == 1 and ADMIN not in h.app.pending and ADMIN not in h.app.gift_drafts
    out = h.say(P1, f"کد هدیه {code}")
    assert "کد هدیه پذیرفته شد" in texts(out) and fmt.usd(2500, "fa") in texts(out) and code not in texts(out)             # the code is never repeated to the player
    assert cash.get_account(h.db, P1)["balance"] == C.CASH_START_CENTS + 2500


def test_every_step_names_what_it_asks_for(h):
    h.say(ADMIN, "/start")
    t1 = panel(h.press(ADMIN, f"ad:gift:{ADMIN}"))
    assert cbs(t1) == [f"ad:gt:stock_money:{ADMIN}", f"ad:gt:life_cash:{ADMIN}", f"ad:gt:char_xp:{ADMIN}", f"ad:gx:{ADMIN}"] and FA["gift.type"] in t1.text
    t2 = panel(h.press(ADMIN, f"ad:gt:char_xp:{ADMIN}"))
    assert "XP" in t2.text and fmt.num(C.GIFT_MAX_XP, "fa") in t2.text and h.app.pending[ADMIN][0] == adminpanel.GIFT_AMOUNT and cbs(t2) == [f"ad:gx:{ADMIN}"]
    t3 = panel(h.say(ADMIN, "150"))
    assert h.app.pending[ADMIN][0] == adminpanel.GIFT_USES and f"{fmt.num(150, 'fa')} XP" in t3.text and cbs(t3) == [f"ad:gx:{ADMIN}"]
    t4 = panel(h.say(ADMIN, "4"))
    assert f"{fmt.num(150, 'fa')} XP" in t4.text and f"{fmt.num(600, 'fa')} XP" in t4.text and fmt.num(4, "fa") in t4.text and ADMIN not in h.app.pending
    assert cbs(t4)[0].startswith("ad:gok:") and cbs(t4)[1] == f"ad:gx:{ADMIN}" and all(len(c.encode()) <= 64 for c in cbs(t4))
    assert count(h.db, "gift_codes") == 0                                                          # nothing is stored before the confirmation


@pytest.mark.parametrize("step", ["type", "amount", "uses", "confirm"])
def test_cancel_at_any_step_leaves_nothing(h, step):
    h.say(ADMIN, "/start")
    h.press(ADMIN, f"ad:gift:{ADMIN}")
    if step != "type":
        h.press(ADMIN, f"ad:gt:stock_money:{ADMIN}")
    if step in ("uses", "confirm"):
        h.say(ADMIN, "500")
    if step == "confirm":
        h.say(ADMIN, "3")
    out = h.press(ADMIN, f"ad:gx:{ADMIN}")
    assert FA["gift.cancelled"] in panel(out).text
    assert count(h.db, "gift_codes") == 0 and ADMIN not in h.app.pending and ADMIN not in h.app.gift_drafts
    assert h.say(ADMIN, "500") == [] and h.say(ADMIN, "3") == []                                  # a later number is not taken as an answer
    assert count(h.db, "gift_codes") == 0


def test_a_pressed_confirm_after_cancel_or_a_second_press_creates_at_most_one_code(h):
    m = start_gift(h, confirm=False)
    ok = cbs(m)[0]
    first = h.press(ADMIN, ok)
    assert made_code(panel(first))
    again = h.press(ADMIN, ok)
    assert FA["gift.stale"] in panel(again).text and count(h.db, "gift_codes") == 1
    m2 = start_gift(h, confirm=False)
    ok2 = cbs(m2)[0]
    h.press(ADMIN, f"ad:gx:{ADMIN}")
    stale = h.press(ADMIN, ok2)
    assert FA["gift.stale"] in panel(stale).text and count(h.db, "gift_codes") == 1


def test_a_confirm_button_of_an_older_draft_does_nothing(h):
    m1 = start_gift(h, confirm=False)
    old = cbs(m1)[0]
    m2 = start_gift(h, "life_cash", "5", "1", confirm=False)                                       # a new draft replaces it
    assert cbs(m2)[0] != old
    assert FA["gift.stale"] in panel(h.press(ADMIN, old)).text and count(h.db, "gift_codes") == 0
    assert made_code(panel(h.press(ADMIN, cbs(m2)[0])))
    assert count(h.db, "gift_codes") == 1 and gifts.recent(h.db, 1)[0]["reward_type"] == "life_cash"


@pytest.mark.parametrize("bad", ["abc", "0", "-5", "2.5", "1e3", "5 5", "1,00", "99999999", "٠", "۵۰۰۰۰۰۰۰۰"])
def test_a_wrong_amount_repeats_the_question_and_keeps_the_step(h, bad):
    h.say(ADMIN, "/start")
    h.press(ADMIN, f"ad:gift:{ADMIN}")
    h.press(ADMIN, f"ad:gt:stock_money:{ADMIN}")
    m = panel(h.say(ADMIN, bad))
    assert FA["gift.bad_amount"].split("{")[0] in m.text and h.app.pending[ADMIN][0] == adminpanel.GIFT_AMOUNT
    assert h.app.gift_drafts[ADMIN]["step"] == "amount" and count(h.db, "gift_codes") == 0
    panel(h.say(ADMIN, "۱٬۰۰۰"))                                                                   # then a good one is taken
    assert h.app.gift_drafts[ADMIN]["amount"] == 1000 and h.app.gift_drafts[ADMIN]["step"] == "uses"


@pytest.mark.parametrize("bad", ["abc", "0", "-1", "2.5", "10001", "1e2", "٠"])
def test_a_wrong_player_limit_repeats_the_question(h, bad):
    h.say(ADMIN, "/start")
    h.press(ADMIN, f"ad:gift:{ADMIN}")
    h.press(ADMIN, f"ad:gt:life_cash:{ADMIN}")
    h.say(ADMIN, "10")
    m = panel(h.say(ADMIN, bad))
    assert h.app.pending[ADMIN][0] == adminpanel.GIFT_USES and h.app.gift_drafts[ADMIN]["step"] == "uses" and "❌" in m.text
    assert count(h.db, "gift_codes") == 0


def test_the_amount_limits_follow_the_reward(h):
    for reward, limit in (("stock_money", C.GIFT_MAX_STOCK_COINS), ("life_cash", C.GIFT_MAX_CASH_DOLLARS), ("char_xp", C.GIFT_MAX_XP)):
        h.say(ADMIN, "/start")
        h.press(ADMIN, f"ad:gift:{ADMIN}")
        h.press(ADMIN, f"ad:gt:{reward}:{ADMIN}")
        assert "❌" in panel(h.say(ADMIN, str(limit + 1))).text and h.app.gift_drafts[ADMIN]["amount"] is None
        assert "❌" not in panel(h.say(ADMIN, str(limit))).text and h.app.gift_drafts[ADMIN]["amount"] == limit


def test_a_draft_that_waited_too_long_is_gone_and_nothing_is_created(h):
    m = start_gift(h, confirm=False)
    h.advance(adminpanel.GIFT_TTL + 5)
    assert FA["gift.stale"] in panel(h.press(ADMIN, cbs(m)[0])).text and count(h.db, "gift_codes") == 0
    h.press(ADMIN, f"ad:gift:{ADMIN}")
    h.press(ADMIN, f"ad:gt:stock_money:{ADMIN}")
    h.advance(adminpanel.GIFT_TTL + 5)
    assert h.say(ADMIN, "500") == []                                                               # the prompt itself expired too


def test_a_restart_in_the_middle_leaves_no_code_and_the_old_button_is_stale(h):
    m = start_gift(h, confirm=False)
    ok = cbs(m)[0]
    h.app.gift_drafts.clear()
    h.app.pending.clear()                                                                          # what a restarted bot remembers
    assert FA["gift.stale"] in panel(h.press(ADMIN, ok)).text and count(h.db, "gift_codes") == 0


def test_non_admins_get_silence_and_dead_buttons_for_everything_about_gifts(h):
    h.say(P1, "/start")
    for sub in ("gift", "gt:stock_money", "gt:life_cash", "gt:char_xp", "gx", "gok:deadbeef", "gl"):
        for uid, chat in ((P1, P1), (P1, GROUP)):
            calls = h.press(P1, f"ad:{sub}:{uid}", chat=chat)
            assert toasts(calls) and not [c for c in calls if isinstance(c, (EditMessageText, SendMessage))], sub
        calls = h.press(P1, f"ad:{sub}:{ADMIN}")                                                   # the admin's id as the suffix is not theirs
        assert not [c for c in calls if isinstance(c, (EditMessageText, SendMessage))], sub
    assert count(h.db, "gift_codes") == 0 and not h.app.gift_drafts and P1 not in h.app.pending
    assert h.say(P1, "/admin") == []


def test_a_forged_prompt_or_draft_gives_a_regular_player_nothing(h):
    h.say(P1, "/start")
    h.app.pending[P1] = (adminpanel.GIFT_AMOUNT, core.now() + 300)
    h.app.gift_drafts[P1] = {"type": "stock_money", "step": "uses", "amount": 999, "uses": 5, "nonce": "aaaaaaaa", "exp": core.now() + 300}
    assert h.say(P1, "5") == [] and P1 not in h.app.pending
    h.press(P1, f"ad:gok:aaaaaaaa:{P1}")
    assert count(h.db, "gift_codes") == 0
    with pytest.raises(GameError):
        gifts.create(h.db, P1, "stock_money", 100, 1, h.app.settings.admins)


def test_an_admin_who_is_no_longer_an_admin_at_execution_creates_nothing(h):
    m = start_gift(h, confirm=False)
    h.app.settings = dataclasses.replace(h.app.settings, admins=frozenset({999}))
    calls = h.press(ADMIN, cbs(m)[0])
    assert count(h.db, "gift_codes") == 0 and toasts(calls) and not [c for c in calls if isinstance(c, EditMessageText)]


def test_the_admin_cannot_use_the_flow_in_a_group(h):
    h.say(ADMIN, "/start")
    calls = h.press(ADMIN, f"ad:gift:{ADMIN}", chat=GROUP)
    assert toasts(calls) and not [c for c in calls if isinstance(c, EditMessageText)] and not h.app.gift_drafts


def test_the_list_shows_recent_codes_to_the_admin_only(h):
    c1 = made_code(start_gift(h, "stock_money", "500", "3"))
    c2 = made_code(start_gift(h, "char_xp", "50", "10"))
    m = panel(h.press(ADMIN, f"ad:gl:{ADMIN}"))
    assert c1 in m.text and c2 in m.text and f"{fmt.num(0, 'fa')}/{fmt.num(3, 'fa')}" in m.text and f"{fmt.num(0, 'fa')}/{fmt.num(10, 'fa')}" in m.text
    h.say(P1, f"کد هدیه {c1}")
    assert f"{fmt.num(1, 'fa')}/{fmt.num(3, 'fa')}" in panel(h.press(ADMIN, f"ad:gl:{ADMIN}")).text
    h.say(P2, "/start")
    assert not [c for c in h.press(P2, f"ad:gl:{P2}") if isinstance(c, EditMessageText)]


def test_the_empty_list_says_so(h):
    h.say(ADMIN, "/start")
    assert FA["gift.list_empty"] in panel(h.press(ADMIN, f"ad:gl:{ADMIN}")).text


def test_the_flow_works_in_english_too(h):
    h.say(ADMIN, "/start")
    h.db.x("UPDATE users SET lang='en' WHERE id=?", (ADMIN,))
    assert "Create a gift card" in panel(h.say(ADMIN, "/admin")).reply_markup.inline_keyboard[1][0].text
    assert "What should this code give" in panel(h.press(ADMIN, f"ad:gift:{ADMIN}")).text
    assert "XP points" in panel(h.press(ADMIN, f"ad:gt:char_xp:{ADMIN}")).text
    h.say(ADMIN, "20")
    conf = panel(h.say(ADMIN, "2"))
    assert "Confirm the gift card" in conf.text and f"{fmt.num(40, 'en')} XP" in conf.text
    made = panel(h.press(ADMIN, cbs(conf)[0]))
    assert "Gift card created" in made.text and f"gift code {made_code(made)}" in made.text


def test_the_callback_data_of_the_flow_fits_telegram(h):
    m = start_gift(h, confirm=False)
    for c in cbs(m) + [f"ad:gt:stock_money:{2 ** 40 + 7}", f"ad:gok:{'f' * 8}:{2 ** 40 + 7}", f"ad:gift:{2 ** 40 + 7}", f"ad:gl:{2 ** 40 + 7}"]:
        assert len(c.encode()) <= 64


# ============================================================================================== the player's command
def new_code(h, reward="stock_money", n=500, uses=3):
    return gifts.create(h.db, ADMIN, reward, gifts.to_units(reward, n), uses, h.app.settings.admins)["code"]


def test_the_player_redeems_stock_money_in_private_and_sees_the_new_balance(h):
    h.say(P1, "/start")
    code = new_code(h, "stock_money", 500)
    before = stock(h.db, P1)
    out = texts(h.say(P1, f"کد هدیه {code}"))
    assert "کد هدیه پذیرفته شد" in out and giftui.amount_text("fa", "stock_money", 500 * C.UNIT) in out and code not in out
    assert stock(h.db, P1) == before + 500 * C.UNIT


def test_the_player_redeems_cash_and_xp(h):
    h.say(P1, "/start")
    charkit.complete(h.db, P1)
    out = texts(h.say(P1, f"کد هدیه {new_code(h, 'life_cash', 40)}"))
    assert fmt.usd(4000, "fa") in out and fmt.usd(14000, "fa") in out
    out = texts(h.say(P1, f"کد هدیه {new_code(h, 'char_xp', 150)}"))
    assert f"{fmt.num(150, 'fa')} XP" in out and f"{fmt.num(2, 'fa')}" in out
    assert fmt.usd(14000, "fa") in texts(h.say(P1, "پول"))                                         # the wallet shows it


@pytest.mark.parametrize("wrap", ["کد هدیه {c}", "کد هدیه {l}", "کد هدیه {s}", "کدهدیه {c}", "gift code {c}", "giftcode {c}", "/giftcode {c}", "کد هدیه {u}"])
def test_every_way_of_typing_the_code_and_the_command_works(h, wrap):
    h.say(P1, "/start")
    c = new_code(h)
    typed = wrap.format(c=c, l=c.lower(), s=c.replace("-", " "), u=c.replace("-", ""))
    assert "کد هدیه پذیرفته شد" in texts(h.say(P1, typed)), typed
    assert gifts.get(h.db, c)["used_count"] == 1


def test_persian_digits_in_a_typed_code_are_understood(h):
    h.say(P1, "/start")
    for _ in range(200):                                                                           # a code that has a digit in it
        c = new_code(h)
        if any(ch.isdigit() for ch in c):
            break
    typed = c.translate(str.maketrans("23456789", "۲۳۴۵۶۷۸۹"))
    assert "کد هدیه پذیرفته شد" in texts(h.say(P1, f"کد هدیه {typed}"))


def test_every_refusal_is_a_persian_sentence_and_changes_nothing(h):
    h.say(P1, "/start")
    c = new_code(h, uses=1)
    h.say(P2, "/start")
    h.say(P2, f"کد هدیه {c}")
    snap = lambda: (stock(h.db, P1), count(h.db, "gift_redemptions"), gifts.get(h.db, c)["used_count"], core.kv_get(h.db, "minted", 0))
    before = snap()
    cases = [("کد هدیه", FA["gift.usage"]), ("کد هدیه abc", FA["e.gift_bad"]), ("کد هدیه HM-ABCD-EFGH-JKM0", FA["e.gift_bad"]), ("کد هدیه a b c d e f g h", FA["e.gift_bad"]),
             ("کد هدیه HM-AAAA-AAAA-AAAA", FA["e.gift_unknown"]), (f"کد هدیه {c}", FA["e.gift_exhausted"])]
    for text, expected in cases:
        out = h.say(P1, text)
        assert texts(out) == expected, text
    assert texts(h.say(P2, f"کد هدیه {c}")) == FA["e.gift_used"]
    assert snap() == before


def test_an_xp_code_for_a_player_without_a_finished_character_says_so_and_is_not_used_up(h):
    h.say(P1, "مپ", chat=GROUP)                                                                  # a pending profile
    c = new_code(h, "char_xp", 50, 1)
    assert texts(h.say(P1, f"کد هدیه {c}")) == FA["e.gift_char"]
    assert gifts.get(h.db, c)["used_count"] == 0
    charkit.complete(h.db, P1)
    assert "کد هدیه پذیرفته شد" in texts(h.say(P1, f"کد هدیه {c}"))


def test_the_english_answers(h):
    h.say(P1, "/start")
    h.db.x("UPDATE users SET lang='en' WHERE id=?", (P1,))
    c = new_code(h, "stock_money", 25, 1)
    assert "Gift code accepted" in texts(h.say(P1, f"gift code {c}"))
    assert texts(h.say(P1, f"gift code {c}")) == EN["e.gift_used"]
    assert texts(h.say(P1, "gift code HM-AAAA-AAAA-AAAA")) == EN["e.gift_unknown"]
    assert texts(h.say(P1, "gift code nonsense")) == EN["e.gift_bad"]
    assert texts(h.say(P1, "gift code")) == EN["gift.usage"]


def test_in_a_group_the_command_works_once_per_player_across_chats(h):
    h.say(P1, "/start")
    c = new_code(h, uses=5)
    assert "کد هدیه پذیرفته شد" in texts(h.say(P1, f"کد هدیه {c}", chat=GROUP))
    assert texts(h.say(P1, f"کد هدیه {c}")) == FA["e.gift_used"]                                   # in private afterwards: the same player, already redeemed
    assert texts(h.say(P1, f"کد هدیه {c}", chat=GROUP)) == FA["e.gift_used"]
    assert gifts.get(h.db, c)["used_count"] == 1


def test_in_a_group_ordinary_chat_about_gift_codes_is_not_answered(h):
    h.say(P1, "/start")
    for chat_text in ("کد هدیه نداری؟", "کد هدیه رو از کجا بگیرم", "کد هدیه دارم", "کد هدیه", "giftcode", "gift code?", "کد هدیه دوست من"):
        assert h.say(P1, chat_text, chat=GROUP) == [], chat_text


def test_in_a_group_a_mistyped_code_is_answered_but_with_the_prefix_the_usage_is_too(h):
    h.say(P1, "/start")
    assert texts(h.say(P1, "کد هدیه HM-ABCD-EFGH-JKM0", chat=GROUP)) == FA["e.gift_bad"]            # code-shaped: a mistake worth answering
    assert texts(h.say(P1, "/giftcode", chat=GROUP)) == FA["gift.usage"]


def test_a_banned_player_gets_nothing(h):
    h.say(P1, "/start")
    c = new_code(h)
    h.db.x("UPDATE users SET banned=1 WHERE id=?", (P1,))
    assert h.say(P1, f"کد هدیه {c}") == [] and gifts.get(h.db, c)["used_count"] == 0


def test_gift_words_belong_to_the_gift_command_and_are_registered_everywhere():
    for w in ALIASES["giftcode"]:
        p = textparse.parse(w + " HM-ABCD-EFGH-JKMN")
        assert p and p.cmd == "giftcode" and p.args == ["HM-ABCD-EFGH-JKMN"], w
    assert "giftcode" in sections.GLOBAL_CMDS and "giftcode" in handlers.HANDLERS
    from hypemarket import docs
    fa_desc, fa_ex, en_desc, en_ex = docs.DOCS["giftcode"]
    assert "کد هدیه" in fa_ex and "gift code" in en_ex and "giftcode" in dict(docs.CATEGORIES)["start"]


def test_amounts_are_shown_in_the_players_own_units():
    assert giftui.amount_text("fa", "stock_money", 500 * C.UNIT) == f"{fmt.num(500, 'fa')} 🪙"
    assert giftui.amount_text("fa", "life_cash", 2500) == fmt.usd(2500, "fa")
    assert giftui.amount_text("fa", "char_xp", 150) == f"{fmt.num(150, 'fa')} XP"
    assert "پول سهام" in giftui.what("fa", "stock_money", 500 * C.UNIT) and "life cash" in giftui.what("en", "life_cash", 2500)
