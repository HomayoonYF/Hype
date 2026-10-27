"""v0.18.2: blackjack with the custom dealer rule and the concise casino screens.

THE RULE. When the player stands on a total T the dealer does not stand on 17: he draws until he has BEATEN T (a higher total), busts, or has 21
(nothing beats 21). So the player can only win a stood hand by the dealer going over 21, and the only ties are 21 against 21 and two naturals.
This file defines and checks the whole hand state machine under that rule (pure rules, the money, the buttons) and what the screens show."""
import json
import random

import pytest

import test_bot as tb
from test_casino import (A, B, CH, chip_data, chip_label, cash_of, d, enter, h, hand, next_hand, scripted, table_of, walk_to_casino)  # noqa: F401 (fixtures)
from hypemarket import cash, casino, config as C, core, fmt, mapui
from hypemarket.core import GameError
from hypemarket.db import Database
from hypemarket.texts import FA, EN, t

U = 61001


# ============================================================================================== the dealer's decision
@pytest.mark.parametrize("dealer,player_total,draws", [
    (["10♠", "7♥"], 19, True),            # the example of the rule: 17 against 19 draws
    (["10♠", "7♥"], 18, True),
    (["10♠", "7♥"], 17, True),            # an equal total is not a win: he draws (default mode)
    (["10♠", "7♥"], 16, False),           # already higher
    (["10♠", "7♥"], 12, False),
    (["A♠", "6♥"], 19, True),             # soft 17 draws too
    (["A♠", "6♥"], 17, True),
    (["A♠", "6♥"], 16, False),
    (["10♠", "K♥"], 20, True),            # 20 against 20: he draws and risks everything
    (["10♠", "K♥"], 19, False),
    (["10♠", "A♥"], 21, False),           # 21 is never drawn on
    (["7♠", "7♥", "7♦"], 21, False),
    (["10♠", "6♥"], 21, True),            # 16 against 21
    (["2♠", "3♥"], 12, True),             # a small hand against a small hand
    (["5♠", "9♥"], 12, False),
])
def test_the_dealers_decision(dealer, player_total, draws):
    assert casino.dealer_wants_card(dealer, player_total) is draws


def test_stopping_on_a_tie_is_one_switch_and_changes_only_ties(monkeypatch):
    monkeypatch.setattr(casino, "DEALER_DRAWS_ON_TIE", False)
    assert casino.dealer_wants_card(["10♠", "7♥"], 17) is False and casino.dealer_wants_card(["10♠", "7♥"], 18) is True
    assert casino.dealer_wants_card(["10♠", "K♥"], 21) is True and casino.dealer_wants_card(["10♠", "A♥"], 21) is False
    hh = hand(["10♠", "8♥"], ["10♣", "8♦"])
    casino.stand(hh)
    assert hh.result == "push" and len(hh.dealer) == 2                                                  # the alternative: an equal 18 is a push


# ============================================================================================== every outcome of the pure state machine
def test_player_bust_ends_the_hand_without_the_dealer_playing():
    hh = hand(["10♠", "6♥"], ["9♠", "8♦"], draws=["K♣", "2♣"])
    casino.hit(hh)
    assert hh.over and hh.result == "bust" and len(hh.dealer) == 2 and casino.outcome(hh.result) == "lose"
    assert casino.payout(5_00, hh.result) == 0


def test_dealer_draws_several_cards_and_beats_the_player():
    hh = hand(["10♠", "9♥"], ["2♣", "3♦"], draws=["4♠", "5♣", "6♦"])                                   # 5 -> 9 -> 14 -> 20
    casino.stand(hh)
    assert hh.dealer == ["2♣", "3♦", "4♠", "5♣", "6♦"] and casino.total(hh.dealer) == 20 and hh.result == "lose"


def test_dealer_goes_over_21_and_the_player_wins():
    hh = hand(["10♠", "9♥"], ["10♣", "7♦"], draws=["9♣"])
    casino.stand(hh)
    assert hh.result == "dealer_bust" and casino.total(hh.dealer) == 26 and casino.outcome(hh.result) == "win"
    assert casino.payout(5_00, hh.result) == 10_00


def test_the_player_beats_the_dealer_only_by_the_dealer_going_over():
    """Under this rule a stood hand cannot be won on points: the dealer never stops below the player's total. (The plain 'win' result stays in
    the code for a dealer who ran out of cards, which cannot happen; see below.)"""
    seen = set()
    rng = random.Random(8)
    for _ in range(3000):
        hh = casino.deal(casino.shuffled(rng))
        stand_at = rng.choice((10, 12, 14, 16, 17, 18, 19, 20))
        while not hh.over and casino.total(hh.player) < stand_at:
            casino.hit(hh)
        if not hh.over:
            casino.stand(hh)
        seen.add(hh.result)
    assert "win" not in seen and {"bust", "lose", "dealer_bust", "player_blackjack", "dealer_blackjack"} <= seen <= set(casino.RESULTS)


def test_a_tie_exists_only_at_21(monkeypatch):
    hh = hand(["10♠", "5♥"], ["10♣", "8♦"], draws=["6♠", "3♣"])
    casino.hit(hh)
    assert hh.result == "push" and casino.total(hh.player) == casino.total(hh.dealer) == 21 and casino.payout(5_00, "push") == 5_00
    hh = hand(["10♠", "K♥"], ["10♣", "Q♦"], draws=["A♠"])                                               # 20 against 20 is NOT a tie: the dealer draws and gets 21
    casino.stand(hh)
    assert hh.result == "lose" and casino.total(hh.dealer) == 21


def test_naturals_are_untouched_by_the_rule():
    for player, dealer, result in [(["A♠", "K♥"], ["10♠", "9♦"], "player_blackjack"), (["10♠", "9♦"], ["A♠", "Q♥"], "dealer_blackjack"),
                                   (["A♠", "K♥"], ["A♦", "J♣"], "push_blackjack")]:
        hh = hand(player, dealer)
        assert hh.over and hh.result == result and len(hh.dealer) == 2
    assert casino.payout(5_00, "player_blackjack") == 12_50 and casino.payout(5_00, "dealer_blackjack") == 0 and casino.payout(5_00, "push_blackjack") == 5_00


def test_payout_ratios_and_chips_are_the_ones_of_v0181():
    assert C.CASINO_BETS_CENTS == (100, 500, 1000, 2500, 10000) and (C.CASINO_BLACKJACK_NUM, C.CASINO_BLACKJACK_DEN) == (3, 2) and C.CASINO_IDLE_SECS == 1800
    assert [casino.payout(10_00, r) for r in ("win", "dealer_bust", "player_blackjack", "push", "push_blackjack", "lose", "bust", "dealer_blackjack")] == \
        [20_00, 20_00, 25_00, 10_00, 10_00, 0, 0, 0]


def test_every_hand_ends_in_a_consistent_state_whatever_the_cards_are():
    """30 000 random hands with random player strategies: the dealer never stops while he still wants a card, the result agrees with the totals,
    and nothing ever raises (an empty deck included)."""
    rng = random.Random(20261026)
    kinds = {}
    for i in range(30_000):
        hh = casino.deal(casino.shuffled(rng))
        stand_at = rng.choice((4, 9, 12, 13, 15, 17, 19, 20, 21))
        while not hh.over and casino.total(hh.player) < stand_at:
            casino.hit(hh)
        if not hh.over:
            casino.stand(hh)
        assert hh.over and hh.state == "done" and hh.result in casino.RESULTS
        pt, dt = casino.total(hh.player), casino.total(hh.dealer)
        assert len(set(hh.deck) | set(hh.player) | set(hh.dealer)) == 52 and len(hh.deck) + len(hh.player) + len(hh.dealer) == 52     # no card lost or doubled
        if hh.result == "bust":
            assert pt > 21 and len(hh.dealer) == 2
        elif hh.result in ("player_blackjack", "dealer_blackjack", "push_blackjack"):
            assert len(hh.player) == 2 == len(hh.dealer) and 21 in (pt, dt)
        else:
            assert pt <= 21
            assert not casino.dealer_wants_card(hh.dealer, pt), (hh.player, hh.dealer)                         # he stopped for a reason (never for lack of cards)
            if hh.result == "dealer_bust":
                assert dt > 21
            elif hh.result == "lose":
                assert dt > pt and dt <= 21
            elif hh.result == "push":
                assert dt == pt == 21
            else:
                pytest.fail(f"a stood hand was won on points: {hh.player} {hh.dealer}")
        kinds[hh.result] = kinds.get(hh.result, 0) + 1
    assert set(kinds) >= {"bust", "lose", "dealer_bust", "push", "player_blackjack", "dealer_blackjack"}


def test_the_dealer_can_never_run_out_of_cards_even_on_the_worst_decks():
    """The most cards one hand can use: a player drawing the smallest cards up to 21 and a dealer who must reach 21 too. Decks stacked small-first
    (and every rotation of it) prove the deck is never short, so a settlement can never fail on an empty deck."""
    base = sorted(casino.new_deck(), key=lambda c: (casino.value([c])[0] if casino.rank_of(c) != "A" else 1, c))
    worst = 0
    for k in range(52):
        for deck in (base[k:] + base[:k], list(reversed(base[k:] + base[:k]))):
            hh = casino.deal(deck)
            if hh.over:
                continue
            while not hh.over and casino.total(hh.player) < 21:
                casino.hit(hh)
            if not hh.over:
                casino.stand(hh)
            assert hh.over and hh.deck, "the deck ran dry"
            worst = max(worst, len(hh.player) + len(hh.dealer))
    assert worst < 40


def test_a_dealer_without_cards_stops_instead_of_crashing():
    hh = casino.Hand([], ["10♠", "9♥"], ["10♣", "7♦"])
    casino.stand(hh)
    assert hh.over and hh.dealer == ["10♣", "7♦"] and hh.result == "win"                                  # nothing to draw: the totals are compared as they stand


# ============================================================================================== the money, atomic and once (database level)
class Stack:
    """A shuffle that puts `order` (player, dealer, player, dealer, then the draws) on top of the deck."""

    def __init__(self, player, dealer, draws=()):
        self.order = [player[0], dealer[0], player[1], dealer[1], *draws]

    def shuffle(self, deck):
        deck[:] = [c for c in casino.new_deck() if c not in self.order] + list(reversed(self.order))


@pytest.fixture()
def db():
    core.set_clock(lambda: 1_700_000_000)
    d_ = Database(":memory:")
    core.ensure_user(d_, U, "a", "Ali", lang="fa")
    return d_


def play(db, sid, player, dealer, draws, presses, bet=5_00, uid=U, seq=0):
    row = casino.start(db, sid, 1, uid, seq, 1_700_000_000, bet, rng=Stack(player, dealer, draws))
    for what in presses:
        row = casino.act(db, sid, 1, uid, what, row["seq"], 1_700_000_001) or casino.get(db, sid)
    return row


def ledger(db, uid=U):
    return [(r["kind"], r["delta"]) for r in db.qa("SELECT * FROM life_ledger WHERE user_id=? AND kind LIKE 'casino%' ORDER BY id", (uid,))]


CASES = [   # name, player, dealer, draws, presses, result, balance change for a $5 bet
    ("player bust", ["10♠", "6♥"], ["9♠", "8♦"], ["K♣"], ["hit"], "bust", -5_00),
    ("dealer must draw and busts", ["10♠", "9♥"], ["10♣", "7♦"], ["K♣"], ["stand"], "dealer_bust", 5_00),
    ("dealer draws and beats the player", ["10♠", "9♥"], ["10♣", "7♦"], ["4♣"], ["stand"], "lose", -5_00),
    ("dealer already beats the player", ["10♠", "8♥"], ["10♣", "9♦"], [], ["stand"], "lose", -5_00),
    ("21 against 21", ["10♠", "5♥"], ["10♣", "8♦"], ["6♠", "3♣"], ["hit"], "push", 0),
    ("natural", ["A♠", "K♥"], ["10♠", "9♦"], [], [], "player_blackjack", 7_50),
    ("dealer natural", ["10♠", "9♦"], ["A♠", "Q♥"], [], [], "dealer_blackjack", -5_00),
    ("both naturals", ["A♠", "K♥"], ["A♦", "J♣"], [], [], "push_blackjack", 0),
]


@pytest.mark.parametrize("name,player,dealer,draws,presses,result,change", CASES, ids=[c[0] for c in CASES])
def test_every_outcome_settles_the_bet_exactly_once(db, name, player, dealer, draws, presses, result, change):
    c0 = cash.balance(db, U)
    row = play(db, "s1", player, dealer, draws, presses)
    assert row["state"] == "done" and row["result"] == result
    assert cash.balance(db, U) == c0 + change
    expect = [("casino_bet", -5_00)] + ([("casino_win", casino.payout(5_00, result))] if casino.payout(5_00, result) else [])
    assert ledger(db) == expect
    for _ in range(4):                                                                                  # every kind of repeat changes nothing
        assert casino.act(db, "s1", 1, U, "stand", row["seq"], 1_700_000_005) is None
        assert casino.act(db, "s1", 1, U, "hit", row["seq"] - 1, 1_700_000_005) is None
        assert casino.start(db, "s1", 1, U, row["seq"] - 1, 1_700_000_005, 5_00) is None
        assert casino.sweep(db, 1_800_000_000) == 0
    assert cash.balance(db, U) == c0 + change and ledger(db) == expect


def test_after_a_bust_no_further_action_is_possible(db):
    row = play(db, "s1", ["10♠", "6♥"], ["9♠", "8♦"], ["K♣", "2♣"], ["hit"])
    assert row["result"] == "bust"
    for what in ("hit", "stand"):
        for seq in (row["seq"], row["seq"] - 1, row["seq"] + 1):
            assert casino.act(db, "s1", 1, U, what, seq, 1_700_000_002) is None
    again = casino.get(db, "s1")
    assert tuple(again) == tuple(row) and ledger(db) == [("casino_bet", -5_00)]


def test_a_stood_hand_cannot_be_hit_and_a_hit_hand_keeps_its_stake_in_play(db):
    row = play(db, "s1", ["2♠", "3♥"], ["2♣", "3♦"], ["4♥", "5♠"], ["hit"])
    assert row["state"] == "playing" and json.loads(row["player"]) == ["2♠", "3♥", "4♥"] and ledger(db) == [("casino_bet", -5_00)]      # the chip stays on the table
    row = casino.act(db, "s1", 1, U, "stand", row["seq"], 1_700_000_002)
    assert row["state"] == "done" and json.loads(row["dealer"]) == ["2♣", "3♦", "5♠"] and row["result"] == "lose"
    assert casino.act(db, "s1", 1, U, "hit", row["seq"], 1_700_000_003) is None


def test_an_abandoned_hand_is_stood_by_the_same_rule_and_paid_once(db):
    c0 = cash.balance(db, U)
    play(db, "s1", ["10♠", "9♥"], ["10♣", "7♦"], ["K♣"], [])                                           # 19 against 17, left alone
    assert casino.sweep(db, 1_700_000_000 + C.CASINO_IDLE_SECS + 1) == 1
    row = casino.get(db, "s1")
    assert row["result"] == "dealer_bust" and row["paid"] == 10_00 and cash.balance(db, U) == c0 + 5_00     # the dealer had to draw, and busted
    assert casino.sweep(db, 1_900_000_000) == 0 and ledger(db) == [("casino_bet", -5_00), ("casino_win", 10_00)]
    play(db, "s2", ["10♠", "9♥"], ["10♣", "7♦"], ["4♣"], [])
    assert casino.sweep(db, 1_900_000_000) == 1
    assert casino.get(db, "s2")["result"] == "lose" and casino.get(db, "s2")["paid"] == 0                 # the dealer beat the player: the chip is gone


def test_a_hand_dealt_by_v0181_is_finished_by_the_new_rule(db):
    """A hand a player was in the middle of when the new version started (the dealer holds 17 and would have stood on it before)."""
    row = play(db, "old", ["10♠", "9♥"], ["10♣", "7♦"], ["3♠"], [])
    assert row["state"] == "playing"
    row = casino.act(db, "old", 1, U, "stand", row["seq"], 1_700_000_050)
    assert json.loads(row["dealer"]) == ["10♣", "7♦", "3♠"] and row["result"] == "lose" and row["paid"] == 0


def test_a_second_hand_on_the_same_table_is_a_new_charge_and_a_new_ledger_ref(db):
    row = play(db, "s1", ["10♠", "9♥"], ["10♣", "7♦"], ["K♣"], ["stand"])
    row = casino.start(db, "s1", 1, U, row["seq"], 1_700_000_010, 1_00, rng=Stack(["10♠", "8♥"], ["10♣", "9♦"]))
    assert row["hand_no"] == 2 and row["state"] == "playing"
    assert ledger(db) == [("casino_bet", -5_00), ("casino_win", 10_00), ("casino_bet", -1_00)]
    refs = [r["ref"] for r in db.qa("SELECT ref FROM life_ledger WHERE kind LIKE 'casino%' ORDER BY id")]
    assert refs == ["s1:1", "s1:1", "s1:2"] and len(set(zip(["casino_bet", "casino_win", "casino_bet"], refs))) == 3


def test_a_thousand_hands_with_repeated_presses_keep_the_books_exact(db):
    rng = random.Random(26)
    cash.apply(db, U, 10 ** 7, "grant", "bank")
    c0 = cash.balance(db, U)
    for i in range(1000):
        sid = f"t{i % 5}"
        row = casino.get(db, sid)
        if row is None or row["state"] == "done":
            row = casino.start(db, sid, 1, U, 0 if row is None else row["seq"], 1_700_000_000, rng.choice(C.CASINO_BETS_CENTS), rng=random.Random(i))
            assert row is not None
        while row["state"] == "playing":
            what = rng.choice(("hit", "stand"))
            row = casino.act(db, sid, 1, U, what, row["seq"], 1_700_000_000)
            for _ in range(rng.choice((0, 0, 1, 3))):                                                   # the same press again
                assert casino.act(db, sid, 1, U, what, row["seq"] - 1, 1_700_000_000) is None
    rows = db.qa("SELECT * FROM life_ledger WHERE user_id=? AND kind LIKE 'casino%'", (U,))
    assert cash.balance(db, U) == c0 + sum(r["delta"] for r in rows)
    assert len({(r["kind"], r["ref"]) for r in rows}) == len(rows)
    paid = {r["ref"]: r["delta"] for r in rows if r["kind"] == "casino_win"}
    for g in db.qa("SELECT * FROM casino_games"):
        assert g["state"] == "done" and (paid.get(f"{g['sid']}:{g['hand_no']}", 0)) == g["paid"]


# ============================================================================================== the screens
def lines_of(out):
    return out.caption.split("\n")


def test_the_entry_screen_is_title_game_balance_prompt_and_the_bets(h, d):
    v, lobby = enter(h, d, A)
    assert lines_of(lobby) == [t("fa", "mp.cas.title"), t("fa", "bj.game"), "", t("fa", "bj.cash", cash=fmt.usd(100_00, "fa")), "", t("fa", "bj.pick")]
    assert lobby.caption == "🎰 <b>کازینو</b>\n♠️ بلک‌جک\n\n💵 موجودی نقدی: <b>$۱۰۰٫۰۰</b>\n\nمبلغ شرطت رو انتخاب کن."
    assert [x for x, _ in lobby.buttons] == [chip_label(b) for b in C.CASINO_BETS_CENTS] + [t("fa", "bj.b.rules"), t("fa", "mp.b.exit")]


def test_the_entry_screen_shows_the_real_balance_and_only_affordable_bets(h, d):
    v, lobby = enter(h, d, A)
    h.db.x("UPDATE life_accounts SET balance=? WHERE user_id=?", (11_339, A))                            # «$113.39», not a hard-coded example
    lobby = d.press(A, f"mp:{v.sid}:bj:o", v)
    assert t("fa", "bj.cash", cash=fmt.usd(11_339, "fa")) in lobby.caption
    assert [x for x, _ in lobby.buttons][:5] == [chip_label(b) for b in C.CASINO_BETS_CENTS]
    h.db.x("UPDATE life_accounts SET balance=? WHERE user_id=?", (7_00, A))
    chips = d.press(A, f"mp:{v.sid}:bj:o", v)
    assert [x for x, _ in chips.buttons][:3] == [chip_label(1_00), chip_label(5_00), t("fa", "bj.b.rules")]
    h.db.x("UPDATE life_accounts SET balance=? WHERE user_id=?", (50, A))
    poor = d.press(A, f"mp:{v.sid}:bj:o", v)
    assert t("fa", "bj.poor", min=fmt.usd(1_00, "fa")) in poor.caption and [x for x, _ in poor.buttons] == [t("fa", "bj.b.rules"), t("fa", "mp.b.exit")]


def test_a_hand_in_progress_shows_cards_status_and_only_hit_and_stand(h, d, monkeypatch):
    v, lobby = enter(h, d, A)
    scripted(monkeypatch, ["10♥", "6♣"], ["7♠", "9♦"], ["2♣"])
    table = table_of(d, A, v, lobby, bet=5_00)
    assert lines_of(table) == [t("fa", "bj.head", bet=fmt.usd(5_00, "fa")), "", "دیلر: 7♠ 🎴", f"تو: 10♥ 6♣ — {fmt.num(16, 'fa')}", "", "نوبت توئه."]
    assert [x for x, _ in table.buttons] == ["🃏 کارت بکش", "✋ بایست"]                                    # no chips, no rules, no door while a hand is running
    assert "قانون" not in table.caption and "۳ به ۲" not in table.caption


def test_a_finished_hand_shows_result_money_and_balance_then_the_bets(h, d, monkeypatch):
    v, lobby = enter(h, d, A)
    scripted(monkeypatch, ["10♥", "9♣"], ["10♠", "7♦"], ["K♣"])
    table = table_of(d, A, v, lobby, bet=5_00)
    out = d.go(A, table, t("fa", "mp.b.stand"))
    assert lines_of(out) == [t("fa", "bj.head", bet=fmt.usd(5_00, "fa")), "", f"دیلر: 10♠ 7♦ K♣ — {fmt.num(27, 'fa')}", f"تو: 10♥ 9♣ — {fmt.num(19, 'fa')}", "",
                             t("fa", "bj.r.dealer_bust"), t("fa", "bj.net.win", amount=fmt.usd(5_00, "fa")), t("fa", "bj.cash", cash=fmt.usd(105_00, "fa")), "", t("fa", "bj.pick")]
    assert [x for x, _ in out.buttons] == [chip_label(b) for b in C.CASINO_BETS_CENTS] + [t("fa", "bj.b.rules"), t("fa", "mp.b.exit")]
    scripted(monkeypatch, ["10♥", "9♣"], ["10♠", "7♦"], ["4♣"])
    out = d.go(A, next_hand(d, A, v, out, bet=10_00), t("fa", "mp.b.stand"))
    assert t("fa", "bj.net.lose", amount=fmt.usd(10_00, "fa")) in out.caption and t("fa", "bj.cash", cash=fmt.usd(95_00, "fa")) in out.caption
    assert t("fa", "bj.r.lose") in out.caption and "−" in out.caption


def test_the_rules_are_one_button_away_and_never_repeated(h, d, monkeypatch):
    v, lobby = enter(h, d, A)
    rules = d.go(A, lobby, t("fa", "bj.b.rules"))
    assert "روی ۱۷ نمی‌ایسته" in rules.caption and "۳ به ۲" in rules.caption and "۳۰ دقیقه" in rules.caption and "سکه و سهام دست نمی‌خورن" in rules.caption
    assert [x for x, _ in rules.buttons] == [t("fa", "bj.b.back")]
    assert not h.db.q1("SELECT 1 FROM casino_games") and cash_of(h) == 100_00                            # reading costs nothing
    back = d.go(A, rules, t("fa", "bj.b.back"))
    assert lines_of(back) == lines_of(lobby)
    scripted(monkeypatch, ["10♥", "9♣"], ["10♠", "7♦"], ["K♣"])
    table = table_of(d, A, v, back, bet=1_00)
    done = d.go(A, table, t("fa", "mp.b.stand"))
    for screen in (lobby, back, table, done):
        assert not any(w in screen.caption for w in ("۳ به ۲", "می‌ایسته", "آس ۱ یا ۱۱", "تصویرها"))
    again = d.press(A, f"mp:{v.sid}:bj:i", v)                                                           # asking for the rules in any state changes nothing
    assert "روی ۱۷ نمی‌ایسته" in again.caption and cash_of(h) == 101_00 and h.db.q1("SELECT state FROM casino_games")["state"] == "done"


def test_the_rules_say_what_the_dealer_really_does_in_both_languages():
    for lang, must in (("fa", ("روی ۱۷ نمی‌ایسته", "فقط روی ۲۱", "هر دو ۲۱")), ("en", ("does not stop on 17", "only stops on 21", "both of you have 21"))):
        text = t(lang, "bj.rules", mins="30")
        for part in must:
            assert part in text, (lang, part)
    for lang, table in (("fa", FA), ("en", EN)):                                                         # and no text still promises the old rule
        for key, value in table.items():
            if key.startswith(("bj.", "mp.cas", "mp.obj.casino")):
                assert "stands on every 17" not in value and "روی هر ۱۷ می‌ایسته" not in value, (lang, key)


def test_the_english_screens_follow_the_same_shape(h, d, monkeypatch):
    from hypemarket import social
    d.open(A)
    social.set_group_lang(h.db, CH, "en")
    v = walk_to_casino(h, d, B, "en")
    o = d.press(B, f"mp:{v.sid}:o:casino", v)
    lobby = d.go(B, o, t("en", "mp.b.enter"))
    assert lines_of(lobby) == [t("en", "mp.cas.title"), "♠️ Blackjack", "", "💵 Cash balance: <b>$100.00</b>", "", "Choose your bet."]
    assert "$100.00" in lobby.caption and lobby.buttons[-2][0] == "📖 Rules"
    scripted(monkeypatch, ["10♥", "6♣"], ["7♠", "9♦"], ["2♣"])
    table = d.press(B, chip_data(lobby, 5_00, "en"), v)
    assert lines_of(table) == ["🎰 <b>Blackjack</b> • 🎟 Bet: <b>$5.00</b>", "", "Dealer: 7♠ 🎴", "You: 10♥ 6♣ — 16", "", "Your move."]
    assert [x for x, _ in table.buttons] == ["🃏 Hit", "✋ Stand"]


# ============================================================================================== returning to a hand
def test_walking_away_and_coming_back_resumes_the_same_hand(h, d, monkeypatch):
    v, lobby = enter(h, d, A)
    scripted(monkeypatch, ["10♥", "6♣"], ["7♠", "9♦"], ["2♣", "K♦"])
    table = table_of(d, A, v, lobby, bet=5_00)
    row = tuple(h.db.q1("SELECT * FROM casino_games WHERE sid=?", (v.sid,)))
    main = d.press(A, f"mp:{v.sid}:rf", v)                                                               # back to the map (an old «leave» button, or a refresh)
    assert t("fa", "mp.b.near") in [x for x, _ in main.buttons]
    o = d.press(A, f"mp:{v.sid}:o:casino", main)
    again = d.go(A, o, t("fa", "mp.b.enter"))                                                            # entering again: the hand that was left, not a new deal
    assert lines_of(again) == lines_of(table) and [x for x, _ in again.buttons] == [x for x, _ in table.buttons]
    assert tuple(h.db.q1("SELECT * FROM casino_games WHERE sid=?", (v.sid,))) == row and cash_of(h) == 95_00
    both = d.press(A, f"mp:{v.sid}:ce", again)                                                           # pressing «enter» twice in a row is fine
    assert lines_of(both) == lines_of(table)
    assert h.db.q1("SELECT COUNT(*) c FROM life_ledger WHERE kind='casino_bet'")["c"] == 1


def test_entering_after_a_finished_hand_shows_its_result_and_the_next_bets(h, d, monkeypatch):
    v, lobby = enter(h, d, A)
    scripted(monkeypatch, ["10♥", "9♣"], ["10♠", "7♦"], ["K♣"])
    table = table_of(d, A, v, lobby, bet=5_00)
    done = d.go(A, table, t("fa", "mp.b.stand"))
    main = d.press(A, f"mp:{v.sid}:rf", v)
    o = d.press(A, f"mp:{v.sid}:o:casino", main)
    back = d.go(A, o, t("fa", "mp.b.enter"))
    assert lines_of(back) == lines_of(done)                                                              # the finished hand is still what is shown
    assert cash_of(h) == 105_00


def test_a_stale_chip_cannot_start_a_hand_over_a_running_one(h, d, monkeypatch):
    v, lobby = enter(h, d, A)
    scripted(monkeypatch, ["10♥", "6♣"], ["7♠", "9♦"], ["2♣"])
    table_of(d, A, v, lobby, bet=5_00)
    for data in (chip_data(lobby, 25_00), f"mp:{v.sid}:bj:b:1:2500", f"mp:{v.sid}:bj:b:2:500"):
        out = d.press(A, data, v)
        assert t("fa", "bj.stale") in out.toasts[-1]
    assert cash_of(h) == 95_00 and h.db.q1("SELECT COUNT(*) c FROM life_ledger WHERE kind='casino_bet'")["c"] == 1


def test_rules_button_of_somebody_elses_table_is_refused(h, d):
    v, lobby = enter(h, d, A)
    d.open(B)
    for uid in (B, 31337):
        out = d.press(uid, f"mp:{v.sid}:bj:i", v)
        assert out.alerts == [t("fa", "mp.not_yours")] and not out.edits


def test_the_rules_button_needs_the_player_to_be_at_the_casino(h, d):
    v, lobby = enter(h, d, A)
    other = d.open(A)
    o = d.press(A, f"mp:{other.sid}:o:p3", other)
    d.go(A, o, t("fa", "mp.b.go"))                                                                       # walks away from the door
    out = d.press(A, f"mp:{v.sid}:bj:i", v)
    assert out.alerts == [t("fa", "mp.cas.left")] and not out.edits


def test_the_screens_fit_telegram_in_every_state_and_language(h, d, monkeypatch):
    from hypemarket import social
    for lang, uid in (("fa", A), ("en", B)):
        social.set_group_lang(h.db, CH, lang)
        v = walk_to_casino(h, d, uid, lang)
        o = d.press(uid, f"mp:{v.sid}:o:casino", v)
        lobby = d.go(uid, o, t(lang, "mp.b.enter"))
        rules = d.go(uid, lobby, t(lang, "bj.b.rules"))
        assert len(lobby.caption) <= 400 and len(rules.caption) <= 1024
        for label, data in rules.buttons + lobby.buttons:
            assert len(data.encode()) <= 64
