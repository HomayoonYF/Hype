"""v0.18.1: blackjack is played for life cash. The chip leaves the cash when the hand is dealt (one transaction), a finished hand is paid
once (win 1:1, natural 3:2, push gives the chip back, loss nothing), nothing is ever charged or paid twice, a forgotten hand is stood and
settled by the sweep, and the stock coins never move."""
import random
import sqlite3

import pytest

import test_bot as tb
from test_casino import (A, B, CH, d, enter, h, next_hand, scripted, table_of, chip_data, chip_label, cash_of, walk_to_casino)  # noqa: F401 (fixtures)
from hypemarket import cash, casino, config as C, core, fmt, handlers, lifeui, mapui, world, worldmap, worldplaces
from hypemarket.core import GameError
from hypemarket.db import Database
from hypemarket.texts import t


def ledger(h, uid=A, kinds=("casino_bet", "casino_win")):
    return [(r["kind"], r["ref"], r["delta"]) for r in h.db.qa("SELECT * FROM life_ledger WHERE user_id=? ORDER BY id", (uid,)) if r["kind"] in kinds]


def set_cash(h, cents, uid=A):
    cash.balance(h.db, uid)
    h.db.x("UPDATE life_accounts SET balance=? WHERE user_id=?", (cents, uid))


def row_of(h, sid):
    return h.db.q1("SELECT * FROM casino_games WHERE sid=?", (sid,))


# ============================================================================================== the pay table
@pytest.mark.parametrize("result,bet,paid", [
    ("win", 1_00, 2_00), ("dealer_bust", 5_00, 10_00), ("player_blackjack", 1_00, 2_50), ("player_blackjack", 5_00, 12_50),
    ("player_blackjack", 25_00, 62_50), ("player_blackjack", 100_00, 250_00), ("push", 10_00, 10_00), ("push_blackjack", 25_00, 25_00),
    ("lose", 5_00, 0), ("bust", 100_00, 0), ("dealer_blackjack", 10_00, 0),
])
def test_the_pay_table(result, bet, paid):
    assert casino.payout(bet, result) == paid


def test_every_result_has_a_payout_and_every_chip_pays_whole_cents():
    for r in casino.RESULTS:
        for bet in C.CASINO_BETS_CENTS:
            p = casino.payout(bet, r)
            assert isinstance(p, int) and p >= 0
            assert (p > bet) == (r in casino.WIN_RESULTS) and (p == bet) == (r in casino.PUSH_RESULTS) and (p == 0) == (r in casino.LOSS_RESULTS)
    assert C.CASINO_BETS_CENTS == (100, 500, 1000, 2500, 10000) and all(b % 100 == 0 for b in C.CASINO_BETS_CENTS)


# ============================================================================================== chips
def test_the_chips_offered_are_the_ones_the_player_can_pay(h, d):
    v, lobby = enter(h, d, A)
    chips = lobby                                                                                       # the entry screen IS the table: title, game, balance, bet prompt, chips
    assert t("fa", "bj.cash", cash=mapui._money("fa", 100_00)) in lobby.caption                               # it says how much cash there is
    assert [x for x, _ in chips.buttons][:5] == [chip_label(b) for b in C.CASINO_BETS_CENTS] and t("fa", "bj.pick") in chips.caption
    assert not h.db.q1("SELECT 1 FROM casino_games")                                                    # opening the table deals nothing and charges nothing
    set_cash(h, 7_00)
    chips = d.press(A, f"mp:{v.sid}:bj:o", v)
    assert [x for x, _ in chips.buttons][:2] == [chip_label(1_00), chip_label(5_00)] and chip_label(10_00) not in [x for x, _ in chips.buttons]
    set_cash(h, 99)                                                                                     # 99 cents: not even the smallest chip
    poor = d.press(A, f"mp:{v.sid}:bj:o", v)
    assert not any(x.startswith("💵") for x, _ in poor.buttons) and t("fa", "bj.poor", min=fmt.usd(1_00, "fa")) in poor.caption
    assert t("fa", "mp.b.exit") in [x for x, _ in poor.buttons]


def test_stock_coins_cannot_buy_a_chip(h, d):
    v, lobby = enter(h, d, A)
    set_cash(h, 0)
    h.db.x("UPDATE users SET cash=? WHERE id=?", (10 ** 9, A))                                          # a rich stock player with no pocket money
    chips = d.press(A, f"mp:{v.sid}:bj:o", v)
    assert not any(x.startswith("💵") for x, _ in chips.buttons)
    out = d.press(A, f"mp:{v.sid}:bj:b:0:100", v)                                                       # a hand-made button
    assert out.alerts == [handlers.err_text("fa", GameError("cash_low", need=1_00, have=0))]
    assert not h.db.q1("SELECT 1 FROM casino_games") and h.db.q1("SELECT cash FROM users WHERE id=?", (A,))["cash"] == 10 ** 9 and not ledger(h)


def test_look_alike_digits_in_a_button_are_refused_not_crashed(h, d, monkeypatch):
    v, lobby = enter(h, d, A)
    scripted(monkeypatch, ["10♠", "9♥"], ["10♣", "8♦"], ["K♠"])
    table_of(d, A, v, lobby)
    row = tuple(row_of(h, v.sid))
    cash0 = cash_of(h)
    for junk in (f"bj:s:{row[8]}²", "bj:h:①", "bj:h:١", f"bj:b:{row[8]}:５００", "bj:b:０:١٠٠", "bj:b:1:5\u0660\u0660", "bj:s:\uff11"):
        out = d.press(A, f"mp:{v.sid}:{junk}", v)
        assert out.toasts and "Traceback" not in " ".join(out.toasts)
    assert tuple(row_of(h, v.sid)) == row and cash_of(h) == cash0


def test_a_chip_that_is_not_on_the_table_is_refused(h, d):
    v, lobby = enter(h, d, A)
    for cents in (1, 50, 99, 101, 150, 20_00, 50_00, 101_00, 1_000_000):
        out = d.press(A, f"mp:{v.sid}:bj:b:0:{cents}", v)
        assert (out.alerts or out.toasts) and not out.edits                                             # refused with a message, the screen is not redrawn
    assert not h.db.q1("SELECT 1 FROM casino_games") and not ledger(h) and cash_of(h) == 100_00


# ============================================================================================== the money of one hand
HANDS = [   # name, player, dealer, draws, buttons to press after the deal, result, profit in cents for a $5 bet
    ("dealer must draw on 17 against 19 and busts", ["10♠", "9♥"], ["10♣", "7♦"], ["K♠"], "s", "dealer_bust", 5_00),
    ("dealer draws and busts", ["10♠", "9♥"], ["10♣", "6♦"], ["K♠"], "s", "dealer_bust", 5_00),
    ("dealer draws once and beats you", ["10♠", "9♥"], ["10♣", "7♦"], ["3♠"], "s", "lose", -5_00),
    ("dealer draws twice and beats you", ["10♠", "9♥"], ["5♣", "4♦"], ["3♠", "4♥", "4♣"], "s", "lose", -5_00),
    ("dealer already higher", ["10♠", "8♥"], ["10♣", "9♦"], [], "s", "lose", -5_00),
    ("dealer draws on an equal total and busts", ["10♠", "K♥"], ["10♣", "Q♦"], ["5♠"], "s", "dealer_bust", 5_00),
    ("dealer draws on an equal total and beats you", ["10♠", "K♥"], ["10♣", "Q♦"], ["A♠"], "s", "lose", -5_00),
    ("bust", ["10♠", "6♥"], ["10♣", "9♦"], ["K♠"], "h", "bust", -5_00),
    ("21 against 21 is a push", ["10♠", "5♥"], ["10♣", "8♦"], ["6♠", "3♣"], "h", "push", 0),
    ("hit to 21, dealer busts: a win with 21 pays 2.5x", ["10♠", "5♥"], ["10♣", "8♦"], ["6♠", "K♣"], "h", "dealer_bust", 7_50),
    ("player blackjack", ["A♠", "K♥"], ["10♠", "9♦"], [], "", "player_blackjack", 7_50),
    ("dealer blackjack", ["10♠", "9♥"], ["A♦", "K♣"], [], "", "dealer_blackjack", -5_00),
    ("both blackjack", ["A♠", "K♥"], ["A♦", "Q♣"], [], "", "push_blackjack", 0),
]


@pytest.mark.parametrize("name,player,dealer,draws,press,result,profit", HANDS, ids=[x[0] for x in HANDS])
def test_a_hand_costs_the_chip_and_pays_by_the_table(h, d, monkeypatch, name, player, dealer, draws, press, result, profit):
    v, lobby = enter(h, d, A)
    scripted(monkeypatch, player, dealer, draws)
    stock0 = h.db.q1("SELECT cash FROM users WHERE id=?", (A,))["cash"]
    out = table_of(d, A, v, lobby, bet=5_00)
    row = row_of(h, v.sid)
    if press:
        assert row["state"] == "playing" and cash_of(h) == 95_00 and ledger(h) == [("casino_bet", f"{v.sid}:1", -5_00)]    # the chip is out the moment the hand is dealt
        out = d.press(A, f"mp:{v.sid}:bj:{press}:{row['seq']}", v)
        row = row_of(h, v.sid)
    assert row["state"] == "done" and row["result"] == result and row["bet"] == 5_00 and row["hand_no"] == 1
    assert row["paid"] == casino.payout(5_00, result, casino.total(casino.hand_of(row).player)) == 5_00 + profit if profit > -5_00 else row["paid"] == 0
    assert cash_of(h) == 100_00 + profit
    expect = [("casino_bet", f"{v.sid}:1", -5_00)] + ([("casino_win", f"{v.sid}:1", 5_00 + profit)] if row["paid"] else [])
    assert ledger(h) == expect
    assert h.db.q1("SELECT cash FROM users WHERE id=?", (A,))["cash"] == stock0                         # the stock coins never moved
    assert t("fa", "bj.head", bet=mapui._money("fa", 5_00)) in out.caption
    line = "bj.net.win" if profit > 0 else "bj.net.lose" if profit < 0 else "bj.net.push"
    assert t("fa", line, amount=mapui._money("fa", abs(profit), "+" if profit > 0 else "\u2212" if profit < 0 else "")) in out.caption and t("fa", "bj.cash", cash=mapui._money("fa", 100_00 + profit)) in out.caption


def test_the_english_table_shows_dollars(h, d, monkeypatch):
    from hypemarket import social
    d.open(A)                                                                                           # the group exists once somebody has spoken in it
    social.set_group_lang(h.db, CH, "en")
    v = walk_to_casino(h, d, B, "en")                                                                   # (a player who joins an English group plays in English)
    o = d.press(B, f"mp:{v.sid}:o:casino", v)
    lobby = d.go(B, o, t("en", "mp.b.enter"))
    chips = lobby                                                                                       # v0.18.2: entering the casino shows the chips at once
    assert "$100.00" in chips.caption and "Cash balance" in chips.caption
    scripted(monkeypatch, ["10♠", "9♥"], ["10♣", "8♦"], ["K♠"])
    out = d.press(B, chip_data(chips, 10_00, "en"), v)
    assert "$10.00" in out.caption
    out = d.press(B, f"mp:{v.sid}:bj:s:{row_of(h, v.sid)['seq']}", v)
    assert "+$10.00" in out.caption and "$110.00" in out.caption and "🪙" not in "".join(x for x, _ in out.buttons)       # dollars, never the stock coin's symbol


def test_the_next_hand_after_a_loss_to_zero_has_no_chips(h, d, monkeypatch):
    v, lobby = enter(h, d, A)
    set_cash(h, 1_00)
    scripted(monkeypatch, ["10♠", "8♥"], ["10♣", "9♦"])
    out = table_of(d, A, v, lobby)
    out = d.press(A, f"mp:{v.sid}:bj:s:{row_of(h, v.sid)['seq']}", v)
    assert cash_of(h) == 0 and t("fa", "bj.poor", min=fmt.usd(1_00, "fa")) in out.caption and not any(x.startswith("💵") for x, _ in out.buttons)
    stale = d.press(A, f"mp:{v.sid}:bj:b:{row_of(h, v.sid)['seq']}:100", v)                              # the old chip button: nothing to pay it with
    assert stale.alerts == [handlers.err_text("fa", GameError("cash_low", need=1_00, have=0))]
    assert row_of(h, v.sid)["hand_no"] == 1 and len(ledger(h)) == 1


def test_hand_numbers_move_on_and_every_hand_has_its_own_ledger_rows(h, d, monkeypatch):
    v, lobby = enter(h, d, A)
    scripted(monkeypatch, ["10♠", "9♥"], ["10♣", "8♦"], ["K♠"])
    out = table_of(d, A, v, lobby, bet=1_00)
    out = d.press(A, f"mp:{v.sid}:bj:s:{row_of(h, v.sid)['seq']}", v)
    scripted(monkeypatch, ["10♠", "8♥"], ["10♣", "9♦"])
    out = next_hand(d, A, v, out, bet=5_00)
    out = d.press(A, f"mp:{v.sid}:bj:s:{row_of(h, v.sid)['seq']}", v)
    assert row_of(h, v.sid)["hand_no"] == 2
    assert ledger(h) == [("casino_bet", f"{v.sid}:1", -1_00), ("casino_win", f"{v.sid}:1", 2_00), ("casino_bet", f"{v.sid}:2", -5_00)]
    assert cash_of(h) == 100_00 + 1_00 - 5_00


# ============================================================================================== atomic, and never twice
def test_no_chip_no_hand(h, d, monkeypatch):
    v, lobby = enter(h, d, A)
    set_cash(h, 4_00)
    out = d.press(A, f"mp:{v.sid}:bj:b:0:500", v)
    assert out.alerts == [handlers.err_text("fa", GameError("cash_low", need=5_00, have=4_00))] and not out.edits
    assert not h.db.q1("SELECT 1 FROM casino_games") and cash_of(h) == 4_00 and not ledger(h)


def test_a_failure_while_taking_the_chip_leaves_nothing_behind(h, d, monkeypatch):
    v, lobby = enter(h, d, A)
    real = cash.apply

    def boom(db, uid, delta, kind, *a, **k):
        if kind == "casino_bet":
            raise RuntimeError("disk on fire")
        return real(db, uid, delta, kind, *a, **k)
    monkeypatch.setattr(cash, "apply", boom)
    d.press(A, f"mp:{v.sid}:bj:b:0:500", v)
    assert not h.db.q1("SELECT 1 FROM casino_games") and cash_of(h) == 100_00 and not ledger(h)        # no hand without its chip


def test_a_failure_while_paying_undoes_the_whole_press(h, d, monkeypatch):
    v, lobby = enter(h, d, A)
    scripted(monkeypatch, ["10♠", "9♥"], ["10♣", "8♦"], ["K♠"])
    out = table_of(d, A, v, lobby, bet=5_00)
    before = tuple(row_of(h, v.sid))
    real = cash.apply

    def boom(db, uid, delta, kind, *a, **k):
        if kind == "casino_win":
            raise RuntimeError("disk on fire")
        return real(db, uid, delta, kind, *a, **k)
    monkeypatch.setattr(cash, "apply", boom)
    d.press(A, f"mp:{v.sid}:bj:s:{before[8]}", v)
    assert tuple(row_of(h, v.sid)) == before and cash_of(h) == 95_00 and len(ledger(h)) == 1          # still a running hand: the press did not half-happen
    monkeypatch.setattr(cash, "apply", real)
    d.press(A, f"mp:{v.sid}:bj:s:{before[8]}", v)                                                     # the retry works and pays
    assert row_of(h, v.sid)["state"] == "done" and cash_of(h) == 105_00


def test_a_failure_on_a_natural_undoes_the_deal(h, d, monkeypatch):
    v, lobby = enter(h, d, A)
    scripted(monkeypatch, ["A♠", "K♥"], ["10♠", "9♦"])
    real = cash.apply

    def boom(db, uid, delta, kind, *a, **k):
        if kind == "casino_win":
            raise RuntimeError("disk on fire")
        return real(db, uid, delta, kind, *a, **k)
    monkeypatch.setattr(cash, "apply", boom)
    d.press(A, chip_data(lobby, 5_00), v)
    assert not h.db.q1("SELECT 1 FROM casino_games") and cash_of(h) == 100_00 and not ledger(h)


def test_a_hand_is_charged_and_paid_once_however_often_it_is_asked(h, d, monkeypatch):
    v, lobby = enter(h, d, A)
    scripted(monkeypatch, ["10♠", "9♥"], ["10♣", "8♦"], ["K♠"])
    out = table_of(d, A, v, lobby, bet=5_00)
    row = row_of(h, v.sid)
    stand = f"mp:{v.sid}:bj:s:{row['seq']}"
    for _ in range(5):
        d.press(A, stand, v)                                                                           # the same press delivered five times
    assert cash_of(h) == 105_00 and len(ledger(h)) == 2
    assert cash.apply(h.db, A, 10_00, "casino_win", f"{v.sid}:1") is None                              # the ledger itself refuses a second payout of that hand
    assert cash.apply(h.db, A, -5_00, "casino_bet", f"{v.sid}:1") is None
    done = row_of(h, v.sid)
    assert casino.act(h.db, v.sid, done["world_id"], A, "stand", done["seq"], tb.T[0]) is None
    assert casino.sweep(h.db, tb.T[0] + 10 ** 6) == 0                                                  # and the sweep leaves a finished hand alone
    assert cash_of(h) == 105_00 and len(ledger(h)) == 2


def test_nobody_can_put_a_chip_on_somebody_elses_table(h, d, monkeypatch):
    v, lobby = enter(h, d, A)
    scripted(monkeypatch, ["10♠", "9♥"], ["10♣", "8♦"], ["K♠"])
    out = table_of(d, A, v, lobby)
    d.press(A, f"mp:{v.sid}:bj:s:{row_of(h, v.sid)['seq']}", v)
    d.open(B)
    cash_b = cash.balance(h.db, B)
    seq = row_of(h, v.sid)["seq"]
    for uid in (B, 31337):
        r = d.press(uid, f"mp:{v.sid}:bj:b:{seq}:500", v)
        assert r.alerts == [t("fa", "mp.not_yours")] and not r.edits
    assert cash.balance(h.db, B) == cash_b and cash_of(h) == 101_00
    row = row_of(h, v.sid)                                                                             # and the store itself checks the owner too
    assert casino.start(h.db, v.sid, row["world_id"], B, row["seq"], tb.T[0], 5_00) is None and cash.balance(h.db, B) == cash_b


# ============================================================================================== forgotten hands
def forgotten_hand(h, d, monkeypatch, bet=5_00):
    """A running hand (player 19 against a dealer who has 17 and, under the v0.18.2 rule, must draw: he busts) that the player walks away from."""
    v, lobby = enter(h, d, A)
    scripted(monkeypatch, ["10♠", "9♥"], ["10♣", "7♦"], ["K♠"])
    table_of(d, A, v, lobby, bet=bet)
    assert row_of(h, v.sid)["state"] == "playing"
    return v


def test_an_idle_hand_is_stood_and_paid_once(h, d, monkeypatch):
    v = forgotten_hand(h, d, monkeypatch)
    h.advance(C.CASINO_IDLE_SECS - 60)
    assert casino.sweep(h.db) == 0 and row_of(h, v.sid)["state"] == "playing"                           # not yet
    h.advance(120)
    assert casino.sweep(h.db) == 1
    row = row_of(h, v.sid)
    assert row["state"] == "done" and row["result"] == "dealer_bust" and row["paid"] == 10_00
    assert cash_of(h) == 105_00 and ledger(h) == [("casino_bet", f"{v.sid}:1", -5_00), ("casino_win", f"{v.sid}:1", 10_00)]
    assert casino.sweep(h.db) == 0 and cash_of(h) == 105_00                                              # and never a second time


def test_a_hand_that_is_being_played_is_not_swept(h, d, monkeypatch):
    v, lobby = enter(h, d, A)
    scripted(monkeypatch, ["2♠", "3♥"], ["10♣", "7♦"], draws=["4♥", "5♠", "6♣", "2♦", "2♥"])
    table = table_of(d, A, v, lobby)
    for _ in range(4):
        h.advance(C.CASINO_IDLE_SECS - 100)                                                             # each press restarts the clock
        table = d.press(A, f"mp:{v.sid}:bj:h:{row_of(h, v.sid)['seq']}", v)
        assert casino.sweep(h.db) == 0
    assert row_of(h, v.sid)["state"] in ("playing", "done")


def test_a_hand_whose_map_message_is_gone_is_settled_by_the_sweep(h, d, monkeypatch):
    v = forgotten_hand(h, d, monkeypatch)
    h.db.x("DELETE FROM map_sessions WHERE sid=?", (v.sid,))                                            # the message was deleted / its session dropped
    assert casino.sweep(h.db) == 1 and cash_of(h) == 105_00 and row_of(h, v.sid)["state"] == "done"


def test_an_expired_session_settles_its_hand_even_before_the_idle_time(h, d, monkeypatch):
    v = forgotten_hand(h, d, monkeypatch)
    h.db.x("UPDATE map_sessions SET expires_at=? WHERE sid=?", (tb.T[0] + 5, v.sid))
    h.db.x("UPDATE casino_games SET updated_at=? WHERE sid=?", (tb.T[0] + 3600, v.sid))                # (touched "in the future": the idle rule alone would not fire)
    assert casino.sweep(h.db, tb.T[0] + 10) == 1 and cash_of(h) == 105_00


def test_pushing_old_map_messages_out_never_swallows_a_stake(h, d, monkeypatch):
    v = forgotten_hand(h, d, monkeypatch)
    for _ in range(C.MAP_SESSIONS_PER_PLAYER):                                                          # the player opens more maps than the limit: the table's message is dropped
        d.open(A)
    assert not h.db.q1("SELECT 1 FROM map_sessions WHERE sid=?", (v.sid,))
    assert not h.db.q1("SELECT 1 FROM casino_games WHERE sid=?", (v.sid,))                              # the game row is gone...
    assert ledger(h) == [("casino_bet", f"{v.sid}:1", -5_00), ("casino_win", f"{v.sid}:1", 10_00)]      # ...but only after the hand was played out and paid
    assert cash_of(h) == 105_00


def test_the_background_loop_runs_the_sweep(h, d, monkeypatch):
    import asyncio
    from hypemarket import jobs
    v = forgotten_hand(h, d, monkeypatch)
    h.advance(C.CASINO_IDLE_SECS + 30)
    calls = []
    real = casino.sweep
    monkeypatch.setattr(casino, "sweep", lambda *a, **k: calls.append(1) or real(*a, **k))
    for name in ("events", "analytics", "progress", "social", "core"):                                  # only the casino part of the tick is under test
        mod = getattr(jobs, name)
        for fn in ("tick", "record_stale", "snapshot", "refresh_all_networth", "refresh_group_scores", "end_season_if_due"):
            if hasattr(mod, fn):
                monkeypatch.setattr(mod, fn, lambda *a, **k: None)

    async def nothing(*a, **k):
        return None
    for fn in ("daily_report", "post_boards", "flush_outbox"):
        monkeypatch.setattr(jobs, fn, nothing)
    monkeypatch.setattr(jobs.work, "notify_ready", lambda *a, **k: None)
    asyncio.run(jobs.slow_tick(h.app))
    assert calls and row_of(h, v.sid)["state"] == "done" and cash_of(h) == 105_00


# ============================================================================================== the balance cap
def test_a_payout_never_pushes_the_balance_past_the_cap(h, d, monkeypatch):
    v, lobby = enter(h, d, A)
    monkeypatch.setattr(C, "CASH_CAP_CENTS", 100_00)                                                    # tiny cap: after the $5 chip the room is exactly $5
    scripted(monkeypatch, ["10♠", "9♥"], ["10♣", "8♦"], ["K♠"])
    table_of(d, A, v, lobby, bet=5_00)
    d.press(A, f"mp:{v.sid}:bj:s:{row_of(h, v.sid)['seq']}", v)
    row = row_of(h, v.sid)
    assert row["result"] == "dealer_bust" and row["paid"] == 5_00 and cash_of(h) == 100_00                       # a $10 payout, cut to the room that was left
    assert ledger(h)[-1] == ("casino_win", f"{v.sid}:1", 5_00)


# ============================================================================================== many hands: the books always balance
def test_two_hundred_random_hands_keep_the_books_exact(h):
    core.ensure_user(h.db, A, "a", "A", lang="fa")
    set_cash(h, 10 ** 7)
    start = cash_of(h)
    rng = random.Random(20261024)
    wins = pushes = losses = 0
    for i in range(200):
        sid = f"p{i % 7}"                                                                               # seven tables, hands played one after another
        row = casino.get(h.db, sid)
        bet = rng.choice(C.CASINO_BETS_CENTS)
        before = cash_of(h)
        row = casino.start(h.db, sid, 1, A, 0 if row is None else row["seq"], tb.T[0], bet, rng=random.Random(i)) if row is None or row["state"] == "done" else row
        assert row is not None and row["bet"] in C.CASINO_BETS_CENTS
        while row["state"] == "playing":
            row = casino.act(h.db, sid, 1, A, rng.choice(("hit", "stand")), row["seq"], tb.T[0])
            assert row is not None
        assert row["paid"] == casino.payout(row["bet"], row["result"], casino.total(casino.hand_of(row).player))
        assert cash_of(h) == before - row["bet"] + row["paid"]
        kind = casino.outcome(row["result"])
        wins, pushes, losses = wins + (kind == "win"), pushes + (kind == "push"), losses + (kind == "lose")
    rows = h.db.qa("SELECT * FROM life_ledger WHERE user_id=? AND kind LIKE 'casino%'", (A,))
    assert cash_of(h) == start + sum(r["delta"] for r in rows)                                          # the balance is the ledger, to the cent
    assert len({(r["kind"], r["ref"]) for r in rows}) == len(rows)                                       # no ref used twice
    assert wins and losses and pushes
    assert h.db.q1("SELECT COUNT(*) c FROM casino_games WHERE state='playing'")["c"] == 0


# ============================================================================================== what the player sees elsewhere
def test_the_wallet_names_the_casino_rows(h, d, monkeypatch):
    v, lobby = enter(h, d, A)
    scripted(monkeypatch, ["10♠", "9♥"], ["10♣", "8♦"], ["K♠"])
    table_of(d, A, v, lobby, bet=5_00)
    d.press(A, f"mp:{v.sid}:bj:s:{row_of(h, v.sid)['seq']}", v)
    text = lifeui.wallet(h.db, "fa", A).text
    assert t("fa", "cash.k.casino_bet") in text and t("fa", "cash.k.casino_win") in text and "casino_bet" not in text
    en = lifeui.wallet(h.db, "en", A).text
    assert t("en", "cash.k.casino_bet") in en and t("en", "cash.k.casino_win") in en


def test_the_texts_no_longer_promise_no_stakes():
    for lang in ("fa", "en"):
        from hypemarket import docs
        for key in ("mp.obj.casino", "bj.rules", "bj.head", "bj.pick"):
            txt = t(lang, key, cash="$1.00", mins="30", bet="$1.00")
            assert "بدون شرط" not in txt and "no stakes" not in txt.lower() and "no-stakes" not in txt.lower()
        assert "3" in t(lang, "bj.rules", mins="30") or "۳" in t(lang, "bj.rules", mins="30")
    assert "بدون شرط‌بندی" not in docs.DOCS["map"][0] and "no-stakes" not in docs.DOCS["map"][1]


# ============================================================================================== migration 9
def test_schema_9_adds_the_money_columns_and_keeps_every_old_game(tmp_path):
    path = str(tmp_path / "v8.db")
    db = Database(path)
    core.ensure_user(db, A, "a", "A", lang="fa")
    db.x("DROP TABLE casino_games")
    db.x("CREATE TABLE casino_games(sid TEXT PRIMARY KEY, world_id INTEGER NOT NULL, user_id INTEGER NOT NULL, deck TEXT NOT NULL, player TEXT NOT NULL, "
         "dealer TEXT NOT NULL, state TEXT NOT NULL CHECK(state IN ('playing','done')), result TEXT NOT NULL DEFAULT '', seq INTEGER NOT NULL DEFAULT 1, "
         "created_at INTEGER NOT NULL, updated_at INTEGER NOT NULL)")                                    # the v0.18 shape: no stakes
    db.x("INSERT INTO casino_games VALUES('old1',1,?,'[\"2♠\"]','[\"10♠\",\"9♥\"]','[\"10♣\",\"7♦\"]','playing','',4,100,200)", (A,))
    db.x("INSERT INTO casino_games VALUES('old2',1,?,'[\"2♠\"]','[\"10♠\",\"8♥\"]','[\"10♣\",\"9♦\"]','done','lose',7,100,200)", (A,))
    db.x("PRAGMA user_version=8")
    db.close()
    d2 = Database(path)
    assert d2.q1("PRAGMA user_version")[0] == 11                                                           # (v0.19 then added schema 10, v0.20 schema 11)
    cols = [r["name"] for r in d2.qa("PRAGMA table_info(casino_games)")]
    assert cols[-3:] == ["bet", "hand_no", "paid"]
    rows = {r["sid"]: r for r in d2.qa("SELECT * FROM casino_games")}
    assert rows["old1"]["state"] == "playing" and rows["old1"]["seq"] == 4 and rows["old1"]["player"] == '["10♠","9♥"]'
    assert rows["old2"]["result"] == "lose" and rows["old2"]["seq"] == 7
    assert all(r["bet"] == 0 and r["hand_no"] == 1 and r["paid"] == 0 for r in rows.values())             # old hands had nothing at risk
    assert Database(path).q1("PRAGMA user_version")[0] == 11                                             # reopening changes nothing
    # an old hand that is left behind is stood without any money moving; the old table then deals the next hand for a chip
    ledger0 = d2.q1("SELECT COUNT(*) c FROM life_ledger")["c"]
    assert casino.sweep(d2, 10 ** 9) == 1 and d2.q1("SELECT state FROM casino_games WHERE sid='old1'")["state"] == "done"
    assert d2.q1("SELECT COUNT(*) c FROM life_ledger")["c"] == ledger0
    new = casino.start(d2, "old2", 1, A, 7, 300, 5_00)
    assert new["hand_no"] == 2 and new["bet"] == 5_00 and cash.balance(d2, A) <= 100_00 - 5_00 + casino.payout(5_00, new["result"])
