"""v0.20, blackjack: a win with a final total of exactly 21 pays 2.5x (every other win stays 2x), and the table is laid out one card per line with the
score on a line of its own, with Unicode isolates so Telegram's bidi algorithm cannot reorder a card or an amount inside the Persian text.

The money is tested on the pay function, on scripted decks through the real buttons, on the ledger and on the balance. The layout is tested on the text
the bot sends; how a particular Telegram client draws it cannot be proven by a unit test (it is listed as a manual check in the README)."""
import re
from html.parser import HTMLParser

import pytest

import test_bot as tb
from test_casino import (A, CH, chip_data, chip_label, cash_of, cl, d, enter, h, next_hand, scripted, table_of)  # noqa: F401 (fixtures)
from hypemarket import cash, casino, config as C, fmt, mapui
from hypemarket.texts import t

LRI, PDI, RLM = fmt.LRI, fmt.PDI, fmt.RLM
CARD = re.compile(r"(?:10|[2-9AJQK])[♠♥♦♣]")


def ledger(h, uid=A):
    return [(r["kind"], r["ref"], r["delta"]) for r in h.db.qa("SELECT * FROM life_ledger WHERE user_id=? ORDER BY id", (uid,)) if r["kind"] in ("casino_bet", "casino_win")]


def row_of(h, sid):
    return h.db.q1("SELECT * FROM casino_games WHERE sid=?", (sid,))


def play(h, d, monkeypatch, player, dealer, draws, presses, bet=10_00):
    """Deal `bet` on a scripted deck and press `presses` ('h' / 's') in turn. Returns (session, last screen)."""
    v, lobby = enter(h, d, A)
    scripted(monkeypatch, player, dealer, draws)
    out = table_of(d, A, v, lobby, bet=bet)
    for p in presses:
        row = row_of(h, v.sid)
        out = d.press(A, f"mp:{v.sid}:bj:{p}:{row['seq']}", v)
    return v, out


# ============================================================================================== the pay function
@pytest.mark.parametrize("result,total,bet,paid", [
    ("win", 20, 10_00, 20_00),                      # an ordinary win stays 2x
    ("win", 17, 1_00, 2_00),
    ("dealer_bust", 19, 5_00, 10_00),
    ("dealer_bust", 12, 100_00, 200_00),
    ("win", 21, 10_00, 25_00),                      # a win with a final 21: 2.5x
    ("win", 21, 1_00, 2_50),
    ("dealer_bust", 21, 5_00, 12_50),
    ("dealer_bust", 21, 25_00, 62_50),
    ("dealer_bust", 21, 100_00, 250_00),
    ("player_blackjack", 21, 10_00, 25_00),         # a natural is a win with 21
    ("player_blackjack", None, 5_00, 12_50),
    ("push", 21, 10_00, 10_00),                     # a tie with 21 is a push: the bet back, nothing more
    ("push", 20, 10_00, 10_00),
    ("push_blackjack", 21, 25_00, 25_00),
    ("lose", 21, 10_00, 0),                         # a loss with 21 pays nothing
    ("lose", 18, 10_00, 0),
    ("dealer_blackjack", 20, 10_00, 0),
    ("bust", 26, 10_00, 0),                         # a bust pays nothing, whatever the total says
    ("bust", 22, 100_00, 0),
])
def test_the_pay_function(result, total, bet, paid):
    assert casino.payout(bet, result, total) == paid


def test_won_with_21_is_exactly_a_win_whose_final_total_is_21():
    for result in casino.RESULTS:
        for total in (None, 12, 20, 21, 22, 30):
            expected = result in casino.WIN_RESULTS and (result == "player_blackjack" or total == 21)
            assert casino.won_with_21(result, total) is expected, (result, total)


def test_every_chip_pays_whole_cents_at_both_win_rates():
    for bet in C.CASINO_BETS_CENTS:
        assert casino.payout(bet, "win", 20) == 2 * bet
        assert casino.payout(bet, "win", 21) == bet * 5 // 2 and (bet * 5) % 2 == 0          # whole cents for every chip of the table
        assert casino.payout(bet, "push", 21) == bet and casino.payout(bet, "lose", 21) == 0 and casino.payout(bet, "bust", 25) == 0


def test_a_total_nobody_gave_never_pays_the_21_rate_except_for_a_natural():
    assert casino.payout(5_00, "win") == 10_00 and casino.payout(5_00, "dealer_bust") == 10_00
    assert casino.payout(5_00, "player_blackjack") == 12_50


def test_the_21_rate_comes_from_the_configured_fraction():
    assert (C.CASINO_BLACKJACK_NUM, C.CASINO_BLACKJACK_DEN) == (3, 2)


# ============================================================================================== the money of real hands
def test_an_ordinary_win_pays_2x(h, d, monkeypatch):
    v, out = play(h, d, monkeypatch, ["10♠", "9♥"], ["10♣", "7♦"], ["K♠"], "s")                 # 19, the dealer draws and busts
    row = row_of(h, v.sid)
    assert row["result"] == "dealer_bust" and row["paid"] == 20_00 and cash_of(h) == 110_00
    assert ledger(h) == [("casino_bet", f"{v.sid}:1", -10_00), ("casino_win", f"{v.sid}:1", 20_00)]
    assert t("fa", "bj.r.win21") not in out.caption                                              # nothing is said about 21: it was no 21
    assert t("fa", "bj.net.win", amount=mapui._money("fa", 10_00, "+")) in out.caption


def test_a_win_with_21_after_hitting_pays_2_5x(h, d, monkeypatch):
    v, out = play(h, d, monkeypatch, ["10♠", "5♥"], ["10♣", "8♦"], ["6♠", "K♣"], "h")           # hit to 21, the dealer's 18 draws a king and busts
    row = row_of(h, v.sid)
    assert row["state"] == "done" and row["result"] == "dealer_bust" and casino.total(casino.hand_of(row).player) == 21
    assert row["bet"] == 10_00 and row["paid"] == 25_00 and cash_of(h) == 115_00                 # 100 - 10 + 25
    assert ledger(h) == [("casino_bet", f"{v.sid}:1", -10_00), ("casino_win", f"{v.sid}:1", 25_00)]
    assert t("fa", "bj.r.win21") in out.caption and t("fa", "bj.net.win", amount=mapui._money("fa", 15_00, "+")) in out.caption
    assert t("fa", "bj.cash", cash=mapui._money("fa", 115_00)) in out.caption


def test_a_natural_blackjack_still_pays_2_5x_and_does_not_repeat_itself(h, d, monkeypatch):
    v, out = play(h, d, monkeypatch, ["A♠", "K♥"], ["10♠", "9♦"], [], "")
    row = row_of(h, v.sid)
    assert row["result"] == "player_blackjack" and row["paid"] == 25_00 and cash_of(h) == 115_00
    assert t("fa", "bj.r.player_blackjack") in out.caption and t("fa", "bj.r.win21") not in out.caption      # the natural has its own result line


def test_a_win_with_a_soft_21_pays_2_5x(h, d, monkeypatch):
    v, out = play(h, d, monkeypatch, ["A♠", "5♥"], ["10♣", "8♦"], ["5♠", "K♣"], "h")            # soft 16 + 5 = soft 21: stands, the dealer busts
    row = row_of(h, v.sid)
    assert casino.total(casino.hand_of(row).player) == 21 and row["result"] == "dealer_bust" and row["paid"] == 25_00 and cash_of(h) == 115_00


def test_a_tie_with_21_gives_the_bet_back_and_nothing_more(h, d, monkeypatch):
    v, out = play(h, d, monkeypatch, ["10♠", "5♥"], ["10♣", "8♦"], ["6♠", "3♣"], "h")           # 21 against the dealer's 21
    row = row_of(h, v.sid)
    assert row["result"] == "push" and row["paid"] == 10_00 and cash_of(h) == 100_00
    assert ledger(h) == [("casino_bet", f"{v.sid}:1", -10_00), ("casino_win", f"{v.sid}:1", 10_00)]
    assert t("fa", "bj.net.push") in out.caption and t("fa", "bj.r.win21") not in out.caption


def test_two_naturals_are_still_a_push(h, d, monkeypatch):
    v, out = play(h, d, monkeypatch, ["A♠", "K♥"], ["A♦", "Q♣"], [], "")
    row = row_of(h, v.sid)
    assert row["result"] == "push_blackjack" and row["paid"] == 10_00 and cash_of(h) == 100_00 and t("fa", "bj.r.win21") not in out.caption


def test_a_loss_after_standing_on_20_pays_nothing(h, d, monkeypatch):
    v, out = play(h, d, monkeypatch, ["10♠", "K♥"], ["10♣", "Q♦"], ["A♠"], "s")                 # 20 against 20: the dealer draws on the tie and gets 21
    row = row_of(h, v.sid)
    assert row["result"] == "lose" and row["paid"] == 0 and cash_of(h) == 90_00
    assert ledger(h) == [("casino_bet", f"{v.sid}:1", -10_00)] and t("fa", "bj.net.lose", amount=mapui._money("fa", 10_00, "−")) in out.caption


def test_a_dealer_with_21_beats_a_player_with_less_and_pays_nothing(h, d, monkeypatch):
    v, out = play(h, d, monkeypatch, ["10♠", "9♥"], ["A♦", "K♣"], [], "")                      # the dealer's natural ends the hand
    row = row_of(h, v.sid)
    assert row["result"] == "dealer_blackjack" and row["paid"] == 0 and cash_of(h) == 90_00 and ledger(h) == [("casino_bet", f"{v.sid}:1", -10_00)]


def test_a_loss_with_21_pays_nothing_through_the_settlement_code(h, d, monkeypatch):
    v, out = play(h, d, monkeypatch, ["10♠", "6♥"], ["10♣", "9♦"], ["K♠"], "")                  # a running hand: charged, not paid
    sid = v.sid
    before = cash_of(h)
    with h.db.tx():
        paid = casino._pay(h.db, A, sid, 77, 10_00, "lose", 5, 21)                               # a lost hand whose player held 21 (it cannot happen at the table; the rule is the point)
    assert paid == 0 and cash_of(h) == before and not [r for r in ledger(h) if r[1] == f"{sid}:77"]


def test_a_bust_pays_nothing(h, d, monkeypatch):
    v, out = play(h, d, monkeypatch, ["10♠", "6♥"], ["10♣", "9♦"], ["K♠"], "h")
    row = row_of(h, v.sid)
    assert row["result"] == "bust" and row["paid"] == 0 and cash_of(h) == 90_00
    assert ledger(h) == [("casino_bet", f"{v.sid}:1", -10_00)] and t("fa", "bj.r.win21") not in out.caption


def test_every_money_movement_is_one_ledger_row_and_the_balance_is_their_sum(h, d, monkeypatch):
    v, out = play(h, d, monkeypatch, ["10♠", "5♥"], ["10♣", "8♦"], ["6♠", "K♣"], "h", bet=25_00)
    assert row_of(h, v.sid)["paid"] == 62_50
    rows = h.db.qa("SELECT * FROM life_ledger WHERE user_id=? ORDER BY id", (A,))
    assert cash_of(h) == 100_00 + sum(r["delta"] for r in rows if r["kind"] in ("casino_bet", "casino_win"))
    assert len({(r["kind"], r["ref"]) for r in rows}) == len(rows)                               # one row per movement, no duplicates


def test_a_repeated_stand_or_hit_pays_the_21_win_once(h, d, monkeypatch):
    v, lobby = enter(h, d, A)
    scripted(monkeypatch, ["10♠", "5♥"], ["10♣", "8♦"], ["6♠", "K♣"])
    table_of(d, A, v, lobby, bet=10_00)
    seq = row_of(h, v.sid)["seq"]
    first = d.press(A, f"mp:{v.sid}:bj:h:{seq}", v)
    second = d.press(A, f"mp:{v.sid}:bj:h:{seq}", v)                                             # the same button pressed again
    third = d.press(A, f"mp:{v.sid}:bj:s:{seq + 1}", v)                                          # and a stand on the finished hand
    assert cash_of(h) == 115_00 and ledger(h) == [("casino_bet", f"{v.sid}:1", -10_00), ("casino_win", f"{v.sid}:1", 25_00)]
    assert row_of(h, v.sid)["paid"] == 25_00 and (second.toasts or second.alerts) and (third.toasts or third.alerts)


def test_the_next_round_after_a_21_win_is_a_normal_new_hand(h, d, monkeypatch):
    v, out = play(h, d, monkeypatch, ["10♠", "5♥"], ["10♣", "8♦"], ["6♠", "K♣"], "h")
    scripted(monkeypatch, ["10♠", "9♥"], ["10♣", "7♦"], ["K♠"])
    out = next_hand(d, A, v, out, bet=5_00)
    row = row_of(h, v.sid)
    assert row["hand_no"] == 2 and row["state"] == "playing" and cash_of(h) == 115_00 - 5_00
    assert t("fa", "mp.b.hit") in [x for x, _ in out.buttons] and t("fa", "mp.b.stand") in [x for x, _ in out.buttons]
    seq = row["seq"]
    out = d.press(A, f"mp:{v.sid}:bj:s:{seq}", v)
    assert row_of(h, v.sid)["paid"] == 10_00 and cash_of(h) == 120_00                            # round 2 is an ordinary win: 2x, not 2.5x
    assert ledger(h)[-2:] == [("casino_bet", f"{v.sid}:2", -5_00), ("casino_win", f"{v.sid}:2", 10_00)]


def test_a_21_win_near_the_balance_cap_is_cut_to_the_room_that_is_left(h, d, monkeypatch):
    d.open(A)
    cash.balance(h.db, A)
    h.db.x("UPDATE life_accounts SET balance=? WHERE user_id=?", (C.CASH_CAP_CENTS - 1_00, A))
    v, out = play(h, d, monkeypatch, ["10♠", "5♥"], ["10♣", "8♦"], ["6♠", "K♣"], "h", bet=5_00)
    assert cash_of(h) == C.CASH_CAP_CENTS and row_of(h, v.sid)["paid"] == 6_00                   # the old cap rule, unchanged by the new rate


def test_the_stock_coins_never_move_in_a_21_win(h, d, monkeypatch):
    d.open(A)
    stock0 = h.db.q1("SELECT cash FROM users WHERE id=?", (A,))["cash"]
    play(h, d, monkeypatch, ["10♠", "5♥"], ["10♣", "8♦"], ["6♠", "K♣"], "h")
    assert h.db.q1("SELECT cash FROM users WHERE id=?", (A,))["cash"] == stock0


# ============================================================================================== the layout
class _Tags(HTMLParser):
    def __init__(self):
        super().__init__()
        self.stack, self.bad = [], []

    def handle_starttag(self, tag, attrs):
        self.stack.append(tag)

    def handle_endtag(self, tag):
        if not self.stack or self.stack.pop() != tag:
            self.bad.append(tag)


def assert_valid_html(text):
    p = _Tags()
    p.feed(text)
    p.close()
    assert not p.stack and not p.bad, (p.stack, p.bad)
    assert not re.search(r"<(?!/?b>)", text) and "&" not in text.replace("&amp;", "").replace("&lt;", "").replace("&gt;", "")          # only <b> tags, nothing unescaped


def card_lines(caption):
    return [ln for ln in caption.split("\n") if CARD.search(ln)]


def test_every_card_is_on_its_own_line_and_the_score_has_a_line_of_its_own(h, d, monkeypatch):
    v, out = play(h, d, monkeypatch, ["10♥", "6♣"], ["7♠", "9♦"], ["2♣"], "", bet=5_00)
    lines = out.caption.split("\n")
    assert lines == [t("fa", "bj.head", bet=mapui._money("fa", 5_00)), "", "<b>دیلر:</b>", cl("7♠"), RLM + "🎴", "", "<b>تو:</b>", cl("10♥"), cl("6♣"), "",
                     f"<b>مجموع: {LRI}{fmt.num(16, 'fa')}{PDI}</b>", "", "نوبت توئه."]
    for ln in card_lines(out.caption):
        assert len(CARD.findall(ln)) == 1 and ln.startswith(RLM + LRI) and ln.endswith(PDI)     # one card, isolated, on a right-to-left line
    totals = [ln for ln in lines if "مجموع" in ln]
    assert len(totals) == 1 and not CARD.search(totals[0])                                       # the score never shares a line with a card


def test_a_hand_with_five_cards_still_has_one_card_per_line(h, d, monkeypatch):
    v, out = play(h, d, monkeypatch, ["2♠", "3♥"], ["10♣", "9♦"], ["2♦", "2♣", "3♣"], "hhh")
    cards = casino.hand_of(row_of(h, v.sid)).player
    assert len(cards) >= 5
    ours = card_lines(out.caption)
    assert len(ours) == len(cards) + 1 and all(len(CARD.findall(ln)) == 1 for ln in ours)       # while the hand runs the dealer shows one card
    assert cl(*cards) in out.caption                                                             # the player's cards, in order, one per line


def test_the_hidden_dealer_card_stays_hidden(h, d, monkeypatch):
    v, out = play(h, d, monkeypatch, ["10♥", "6♣"], ["7♠", "K♦"], [], "", bet=5_00)
    assert "K♦" not in out.caption and "🎴" in out.caption and cl("7♠") in out.caption
    assert len([ln for ln in out.caption.split("\n") if "🎴" in ln]) == 1


def test_a_finished_hand_shows_every_dealer_card_on_its_own_line_and_both_totals(h, d, monkeypatch):
    v, out = play(h, d, monkeypatch, ["10♥", "9♣"], ["10♠", "7♦"], ["K♣"], "s", bet=5_00)
    assert cl("10♠", "7♦", "K♣") in out.caption and cl("10♥", "9♣") in out.caption and "🎴" not in out.caption
    sums = [ln for ln in out.caption.split("\n") if "مجموع" in ln]
    assert sums == [f"<b>مجموع: {LRI}{fmt.num(27, 'fa')}{PDI}</b>", f"<b>مجموع: {LRI}{fmt.num(19, 'fa')}{PDI}</b>"]
    assert out.caption.index("دیلر") < out.caption.index(cl("10♠", "7♦", "K♣")) < out.caption.index(sums[0]) < out.caption.index("تو:") < out.caption.index(sums[1])


def test_a_soft_total_is_still_one_isolated_number(h, d, monkeypatch):
    v, out = play(h, d, monkeypatch, ["A♠", "5♥"], ["10♣", "8♦"], [], "", bet=5_00)
    sums = [ln for ln in out.caption.split("\n") if "مجموع" in ln]
    assert len(sums) == 1 and LRI in sums[0] and PDI in sums[0] and not CARD.search(sums[0])


def test_amounts_are_isolated_so_the_dollar_sign_stays_on_the_left_of_the_digits(h, d, monkeypatch):
    v, out = play(h, d, monkeypatch, ["10♠", "5♥"], ["10♣", "8♦"], ["6♠", "K♣"], "h")
    assert f"{LRI}$" in out.caption and f"{LRI}+$" in out.caption                                # the bet, the profit and the balance are isolated runs
    money = re.findall(rf"{LRI}([^{PDI}]*){PDI}", out.caption)
    assert any(x.startswith("+$") for x in money) and any(x.startswith("$") for x in money)
    for x in money:
        assert re.fullmatch(r"[+\u2212]?\$?[\d\u06f0-\u06f9\u066c\u066b,.]+", x) or CARD.fullmatch(x), x           # nothing but an amount, a total or a card inside an isolate
    assert out.caption.count(LRI) == out.caption.count(PDI)                                      # every isolate is closed


def test_the_fa_screens_have_balanced_isolates_and_valid_html_in_every_state(h, d, monkeypatch):
    v, lobby = enter(h, d, A)
    assert_valid_html(lobby.caption)
    assert lobby.caption.count(LRI) == lobby.caption.count(PDI)
    scripted(monkeypatch, ["10♠", "5♥"], ["10♣", "8♦"], ["6♠", "K♣"])
    running = table_of(d, A, v, lobby, bet=5_00)
    assert_valid_html(running.caption)
    assert running.caption.count(LRI) == running.caption.count(PDI)
    done = d.press(A, f"mp:{v.sid}:bj:h:{row_of(h, v.sid)['seq']}", v)
    assert_valid_html(done.caption)
    assert done.caption.count(LRI) == done.caption.count(PDI)
    assert [x for x, _ in done.buttons][:5] == [chip_label(b) for b in C.CASINO_BETS_CENTS]       # the chips are back for the next round
    assert t("fa", "bj.b.rules") in [x for x, _ in done.buttons]


def test_the_buttons_of_a_running_hand_are_still_only_hit_and_stand(h, d, monkeypatch):
    v, out = play(h, d, monkeypatch, ["10♥", "6♣"], ["7♠", "9♦"], ["2♣"], "", bet=5_00)
    assert [x for x, _ in out.buttons] == [t("fa", "mp.b.hit"), t("fa", "mp.b.stand")]
    hit = d.press(A, [dd for x, dd in out.buttons if x == t("fa", "mp.b.hit")][0], v)
    assert cl("10♥", "6♣", "2♣") in hit.caption and f"{LRI}{fmt.num(18, 'fa')}{PDI}" in hit.caption   # the new card is a new line, the total moved


def test_the_win_with_21_line_is_in_the_result_block_before_the_money(h, d, monkeypatch):
    v, out = play(h, d, monkeypatch, ["10♠", "5♥"], ["10♣", "8♦"], ["6♠", "K♣"], "h")
    lines = out.caption.split("\n")
    i = lines.index(t("fa", "bj.r.dealer_bust"))
    assert lines[i + 1] == t("fa", "bj.r.win21") and lines[i + 2].startswith("💰")


def test_the_english_table_is_one_card_per_line_without_bidi_marks(h, d, monkeypatch):
    from hypemarket import social
    d.open(A)
    social.set_group_lang(h.db, CH, "en")
    from test_casino import walk_to_casino
    v = walk_to_casino(h, d, A, "en")
    o = d.press(A, f"mp:{v.sid}:o:casino", v)
    lobby = d.go(A, o, t("en", "mp.b.enter"))
    scripted(monkeypatch, ["10♠", "5♥"], ["10♣", "8♦"], ["6♠", "K♣"])
    lobby.sid, lobby.mid = v.sid, v.mid
    running = d.press(A, chip_data(lobby, 10_00, "en"), v)
    assert RLM not in running.caption and LRI not in running.caption and PDI not in running.caption
    assert running.caption.split("\n") == [t("en", "bj.head", bet="$10.00"), "", "<b>Dealer:</b>", "10♣", "🎴", "", "<b>You:</b>", "10♠", "5♥", "", f"<b>Total: {fmt.num(15, 'en')}</b>", "", "Your move."]
    done = d.press(A, f"mp:{v.sid}:bj:h:{row_of(h, v.sid)['seq']}", v)
    assert t("en", "bj.r.win21") in done.caption and "+$15.00" in done.caption and "$115.00" in done.caption
    assert done.caption.split("\n").count("10♠") == 1 and all(len(CARD.findall(ln)) <= 1 for ln in done.caption.split("\n"))


def test_the_two_languages_agree_on_the_win21_rate_in_the_rules():
    for lang, needle in (("fa", "۲٫۵"), ("en", "2.5")):
        assert needle in t(lang, "bj.rules") or needle in t(lang, "bj.r.win21")
    assert "2.5" in t("en", "bj.r.win21") and "۲٫۵" in t("fa", "bj.r.win21")
