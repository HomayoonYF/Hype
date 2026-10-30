"""Blackjack, the casino's one game: player against dealer, played for life cash (v0.18.1; v0.17 had no stakes).

Rules (shown to a player on request, not on every screen): one standard 52-card deck, freshly shuffled for every hand. Two cards each; one
dealer card stays face down. An ace counts 11 unless that busts the hand, then 1; J, Q and K count 10. Two cards worth 21 are a natural
blackjack: if either side has one the hand ends at once (both: push; one: that side wins). Otherwise the player may hit or stand; going over 21
loses at once (the dealer does not play); reaching 21 stands automatically.

THE DEALER'S RULE (v0.18.2, a custom head-to-head rule; standard blackjack's "dealer stands on 17" is gone). When the player stands on a
total T (21 or less) the dealer turns the hidden card over and then tries to BEAT T: he draws while his total is not higher than T, and stops
when he is higher (he wins), when he busts (over 21, the player wins), or when he has 21 (nothing can beat 21: equal to a player's 21 it is a
push). So the dealer keeps drawing on 17 against a 19, and also on an equal total below 21 (a tie is not a win); the only ties left are
21 against 21 and the two-naturals push. `DEALER_DRAWS_ON_TIE = False` would make him stop on an equal total instead (tie = push), which is a
much harsher table for the player (see the v0.18.2 notes); it is a code constant, not a setting, and both modes are tested. A dealer who runs
out of cards (it cannot happen with one deck, a test stacks the worst decks to prove it) simply stops instead of crashing a settlement.

THE MONEY (life cash only, in cents; never the stock coins, shares, levels or items)
    * A hand is played for one CHIP (`config.CASINO_BETS_CENTS`: $1, $5, $10, $25, $100). The chip is taken from the player's life cash when
      the hand is dealt, in the SAME database transaction that creates the hand: a player who cannot pay the chip gets no hand and loses nothing.
    * Payout, credited in the same transaction in which the hand finishes: win or dealer bust 1 to 1 (the chip comes back plus the same
      again, i.e. 2x the chip), a win with a FINAL TOTAL OF EXACTLY 21 3 to 2 (v0.20: the chip plus one and a half times it, i.e. 2.5x the
      chip; a natural blackjack is such a win, and so is reaching 21 by hitting and then seeing the dealer bust), a push gives the chip back,
      a loss or a bust pays nothing. The 2.5x belongs to the hand that WINS with 21: a 21 that is only a push, or that the dealer beats, or
      a hand that passed through 21 on the way, pays by its own result.
    * Every movement is a row of the cash ledger (`casino_bet` / `casino_win`, ref "<session>:<hand number>"), and (user, kind, ref) is
      UNIQUE: a repeated, late or duplicated press, a retry after a crash, or the sweep below can never charge or pay a hand twice.
    * A hand nobody touches for `config.CASINO_IDLE_SECS`, or whose map message has expired or been dropped, is STOOD by `sweep` (the player's
      cards are played out exactly like a press on Stand) and paid by the same rules, so a stake never disappears with a forgotten message.
    * A balance is never pushed past the cash cap: a payout that would pass it is cut to the room that is left.

The module reads and writes only `casino_games` and, through `cash.apply`, the player's life cash. Its cards come out of a
`random.SystemRandom()` of its own (no seed, no shared state), so it never reads or advances the module-level `random` that the rest of
the program may use; a test can swap `RNG_FACTORY` for a seeded generator to get a fixed deal.

A game belongs to one map session (`sid`) and its owner. Every state change carries the game's `seq`: the buttons of the current screen hold
the seq they were drawn for, a change is applied with `UPDATE ... WHERE seq=?`, and the seq moves on, so a repeated, late or duplicated
button press matches nothing and changes nothing.
"""
from __future__ import annotations

import json
import random
import sqlite3
from dataclasses import dataclass

from . import cash, config as C, core

SUITS = "♠♥♦♣"
RANKS = ("A", "2", "3", "4", "5", "6", "7", "8", "9", "10", "J", "Q", "K")
RNG_FACTORY = random.SystemRandom          # one fresh, unseeded generator per shuffle
DEALER_DRAWS_ON_TIE = True          # v0.18.2: the dealer keeps drawing on a total equal to the player's (a tie is not a win), except on 21

WIN_RESULTS = ("player_blackjack", "dealer_bust", "win")
LOSS_RESULTS = ("dealer_blackjack", "bust", "lose")
PUSH_RESULTS = ("push_blackjack", "push")
RESULTS = WIN_RESULTS + LOSS_RESULTS + PUSH_RESULTS


# --- cards ----------------------------------------------------------------------------------------------------------------------
def new_deck() -> list:
    return [r + s for s in SUITS for r in RANKS]


def shuffled(rng=None) -> list:
    deck = new_deck()
    (rng or RNG_FACTORY()).shuffle(deck)
    return deck


def rank_of(card: str) -> str:
    return card[:-1]


def value(hand) -> tuple:
    """(best total, is_soft): aces count 11 while that does not bust the hand. `is_soft` means an ace is currently counted as 11."""
    total, aces = 0, 0
    for c in hand:
        r = rank_of(c)
        if r == "A":
            aces += 1
            total += 11
        elif r in ("J", "Q", "K"):
            total += 10
        else:
            total += int(r)
    while total > 21 and aces:
        total -= 10
        aces -= 1
    return total, aces > 0


def total(hand) -> int:
    return value(hand)[0]


def is_blackjack(hand) -> bool:
    return len(hand) == 2 and total(hand) == 21


# --- one hand -------------------------------------------------------------------------------------------------------------------
@dataclass
class Hand:
    deck: list                 # the cards still to be drawn, next one last
    player: list
    dealer: list               # dealer[1] is the face-down card until `state` is "done"
    state: str = "playing"     # "playing" | "done"
    result: str = ""

    @property
    def over(self) -> bool:
        return self.state == "done"


def _finish(h: Hand, result: str) -> Hand:
    h.state, h.result = "done", result
    return h


def deal(deck: list) -> Hand:
    """Two cards each (player, dealer, player, dealer) from `deck`; naturals end the hand immediately."""
    deck = list(deck)
    p = [deck.pop(), None]
    d = [deck.pop(), None]
    p[1] = deck.pop()
    d[1] = deck.pop()
    h = Hand(deck, p, d)
    pb, db_ = is_blackjack(p), is_blackjack(d)
    if pb and db_:
        return _finish(h, "push_blackjack")
    if pb:
        return _finish(h, "player_blackjack")
    if db_:
        return _finish(h, "dealer_blackjack")
    return h


def dealer_wants_card(dealer, player_total: int) -> bool:
    """The dealer's one decision (v0.18.2): draw while he has not beaten the player's final total. Never on 21 (nothing beats it), never when
    higher than the player, and on an equal total only when `DEALER_DRAWS_ON_TIE`."""
    t = total(dealer)
    if t >= 21:
        return False
    return t < player_total or (DEALER_DRAWS_ON_TIE and t == player_total)


def _dealer_plays(h: Hand) -> Hand:
    pt = total(h.player)
    while h.deck and dealer_wants_card(h.dealer, pt):
        h.dealer.append(h.deck.pop())
    dt = total(h.dealer)
    if dt > 21:
        return _finish(h, "dealer_bust")
    return _finish(h, "win" if pt > dt else "lose" if pt < dt else "push")


def hit(h: Hand) -> Hand:
    """The player takes a card (the caller checks the hand is still being played)."""
    h.player.append(h.deck.pop())
    t = total(h.player)
    if t > 21:
        return _finish(h, "bust")
    if t == 21:
        return _dealer_plays(h)                                            # nothing to gain from another card: stand
    return h


def stand(h: Hand) -> Hand:
    return _dealer_plays(h)


def outcome(result: str) -> str:
    """'win' | 'lose' | 'push' for a finished hand's result code."""
    return "win" if result in WIN_RESULTS else "lose" if result in LOSS_RESULTS else "push"


# --- money -----------------------------------------------------------------------------------------------------------------------
def won_with_21(result: str, player_total: int | None) -> bool:
    """Did the player WIN the hand with a final total of exactly 21? (A natural blackjack counts: it is a won hand of 21.)"""
    return result in WIN_RESULTS and (result == "player_blackjack" or player_total == 21)


def payout(bet: int, result: str, player_total: int | None = None) -> int:
    """Cents the table gives back for a finished hand of stake `bet` (the chip itself included): 0 for a loss, `bet` for a push,
    2 x `bet` for a win, `bet` + 3/2 x `bet` (2.5 x `bet`) for a win with a final total of exactly 21 (a natural blackjack included).
    `player_total` is the player's final total (None: unknown, then only a natural pays the 21 rate)."""
    if won_with_21(result, player_total):
        return bet + bet * C.CASINO_BLACKJACK_NUM // C.CASINO_BLACKJACK_DEN
    if result in ("win", "dealer_bust"):
        return 2 * bet
    if result in PUSH_RESULTS:
        return bet
    return 0


def ref_of(sid: str, hand_no: int) -> str:
    return f"{sid}:{int(hand_no)}"


def _pay(db, uid: int, sid: str, hand_no: int, bet: int, result: str, now: int, player_total: int | None = None) -> int:
    """Credit a finished hand (inside the caller's transaction). Returns the cents credited (cut to the room under the balance cap)."""
    amount = payout(bet, result, player_total)
    if amount <= 0:
        return 0
    amount = min(amount, C.CASH_CAP_CENTS - cash.balance(db, uid))
    if amount <= 0:
        return 0
    cash.apply(db, uid, amount, "casino_win", ref_of(sid, hand_no), note="blackjack", now=now)
    return amount


# --- storage: one row per map session ---------------------------------------------------------------------------------------------
def get(db, sid: str):
    return db.q1("SELECT * FROM casino_games WHERE sid=?", (sid,))


def hand_of(row) -> Hand:
    return Hand(json.loads(row["deck"]), json.loads(row["player"]), json.loads(row["dealer"]), row["state"], row["result"])


def _dump(h: Hand) -> tuple:
    return json.dumps(h.deck), json.dumps(h.player, ensure_ascii=False), json.dumps(h.dealer, ensure_ascii=False)


def start(db, sid: str, world_id: int, uid: int, seq: int, now: int, bet: int, rng=None):
    """Deal a new hand for session `sid`, for the chip `bet` (cents), if the press is current: `seq` 0 when there is no game yet, or the
    finished game's seq. The chip is taken from the player's life cash in the same transaction (GameError('cash_low') when they cannot pay:
    nothing happens then). Returns the row, or None when the press is stale (a game is already running, or it was pressed twice): nothing
    changes then."""
    if bet not in C.CASINO_BETS_CENTS:
        raise ValueError("not a chip of the table")
    cards = shuffled(rng)                                                  # the shuffle happens before the write, outside the transaction
    with db.tx():
        row = get(db, sid)
        h = deal(cards)
        deck, pl, de = _dump(h)
        if row is None:
            if seq != 0:
                return None
            hand_no = 1
            try:
                db.x("INSERT INTO casino_games(sid,world_id,user_id,deck,player,dealer,state,result,seq,created_at,updated_at,bet,hand_no,paid) VALUES(?,?,?,?,?,?,?,?,1,?,?,?,?,0)",
                     (sid, world_id, uid, deck, pl, de, h.state, h.result, now, now, bet, hand_no))
            except sqlite3.IntegrityError:
                return None
        else:
            if row["user_id"] != uid or row["world_id"] != world_id or row["state"] != "done" or row["seq"] != seq:
                return None
            hand_no = row["hand_no"] + 1
            if db.x("UPDATE casino_games SET deck=?,player=?,dealer=?,state=?,result=?,seq=seq+1,updated_at=?,bet=?,hand_no=?,paid=0 WHERE sid=? AND seq=? AND state='done' AND user_id=?",
                    (deck, pl, de, h.state, h.result, now, bet, hand_no, sid, seq, uid)).rowcount != 1:
                return None
        # the chip: raises 'cash_low' (the whole transaction, the new hand included, is then undone)
        if cash.apply(db, uid, -bet, "casino_bet", ref_of(sid, hand_no), note="blackjack", now=now) is None:
            raise RuntimeError("a casino hand was charged twice")          # the ref names a hand that exists once: cannot happen
        if h.over:                                                         # a natural ends the hand at the deal: pay it now
            paid = _pay(db, uid, sid, hand_no, bet, h.result, now, total(h.player))
            db.x("UPDATE casino_games SET paid=? WHERE sid=?", (paid, sid))
        return get(db, sid)


def _apply_action(db, row, action: str, now: int):
    """Play `action` on the running hand of `row` and pay it if it finishes (inside the caller's transaction). None when the row had changed."""
    h = hand_of(row)
    (hit if action == "hit" else stand)(h)
    deck, pl, de = _dump(h)
    if db.x("UPDATE casino_games SET deck=?,player=?,dealer=?,state=?,result=?,seq=seq+1,updated_at=? WHERE sid=? AND seq=? AND state='playing' AND user_id=?",
            (deck, pl, de, h.state, h.result, now, row["sid"], row["seq"], row["user_id"])).rowcount != 1:
        return None
    if h.over:
        paid = _pay(db, row["user_id"], row["sid"], row["hand_no"], row["bet"], h.result, now, total(h.player))
        db.x("UPDATE casino_games SET paid=? WHERE sid=?", (paid, row["sid"]))
    return get(db, row["sid"])


def act(db, sid: str, world_id: int, uid: int, action: str, seq: int, now: int):
    """Apply 'hit' or 'stand' to the running hand of `sid` if the press is current (right owner, right seq, hand still being played), and
    pay the hand if it ends. Returns the new row, or None when nothing was changed."""
    if action not in ("hit", "stand"):
        return None
    with db.tx():
        row = get(db, sid)
        if row is None or row["user_id"] != uid or row["world_id"] != world_id or row["state"] != "playing" or row["seq"] != seq:
            return None
        return _apply_action(db, row, action, now)


def sweep(db, now: int | None = None, idle: int | None = None) -> int:
    """Stand and pay every hand that was left behind: untouched for `idle` seconds (CASINO_IDLE_SECS), or whose map message (session) has
    expired or been dropped. Each hand is settled in its own transaction and exactly once (the seq guard and the ledger's unique refs).
    Returns how many hands were settled."""
    now = core.now() if now is None else now
    idle = C.CASINO_IDLE_SECS if idle is None else idle
    rows = db.qa("SELECT sid FROM casino_games WHERE state='playing' AND (updated_at<=? OR sid NOT IN (SELECT sid FROM map_sessions WHERE expires_at>?))",
                 (now - idle, now))
    n = 0
    for r in rows:
        with db.tx():
            row = get(db, r["sid"])
            if row is not None and row["state"] == "playing" and _apply_action(db, row, "stand", now) is not None:
                n += 1
    return n
