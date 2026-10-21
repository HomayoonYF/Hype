"""Blackjack, the casino's one game (v0.17): player against dealer, no stakes of any kind.

Rules (also shown to players): one standard 52-card deck, freshly shuffled for every hand. Two cards each; one dealer card stays face down.
An ace counts 11 unless that busts the hand, then 1; J, Q and K count 10. Two cards worth 21 are a natural blackjack: if either side has
one the hand ends at once (both: push; one: that side wins). Otherwise the player may hit or stand; going over 21 loses at once; reaching
21 stands automatically. When the player stands the dealer turns the hidden card over and draws until the hand is worth 17 or more: the
dealer STANDS ON EVERY 17, soft 17 included. Dealer over 21: the player wins; higher total wins; equal totals push. There is no
doubling, splitting or insurance.

This module is deliberately sealed off from the game. It imports no economy code, reads and writes only the `casino_games` table, and
nothing in it can touch money, shares, levels, items or any other part of a player's standing: there is nothing to bet, win or lose but the hand itself. Its cards come out of a
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

SUITS = "♠♥♦♣"
RANKS = ("A", "2", "3", "4", "5", "6", "7", "8", "9", "10", "J", "Q", "K")
RNG_FACTORY = random.SystemRandom          # one fresh, unseeded generator per shuffle
DEALER_STANDS_ON = 17

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


def _dealer_plays(h: Hand) -> Hand:
    while total(h.dealer) < DEALER_STANDS_ON:
        h.dealer.append(h.deck.pop())
    pt, dt = total(h.player), total(h.dealer)
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


# --- storage: one row per map session ---------------------------------------------------------------------------------------------
def get(db, sid: str):
    return db.q1("SELECT * FROM casino_games WHERE sid=?", (sid,))


def hand_of(row) -> Hand:
    return Hand(json.loads(row["deck"]), json.loads(row["player"]), json.loads(row["dealer"]), row["state"], row["result"])


def _dump(h: Hand) -> tuple:
    return json.dumps(h.deck), json.dumps(h.player, ensure_ascii=False), json.dumps(h.dealer, ensure_ascii=False)


def start(db, sid: str, world_id: int, uid: int, seq: int, now: int, rng=None):
    """Deal a new hand for session `sid` if the press is current: `seq` 0 when there is no game yet, or the finished game's seq. Returns
    the row, or None when the press is stale (a game is already running, or it was pressed twice): nothing changes then."""
    cards = shuffled(rng)                                                  # the shuffle happens before the write, outside the transaction
    with db.tx():
        row = get(db, sid)
        h = deal(cards)
        deck, pl, de = _dump(h)
        if row is None:
            if seq != 0:
                return None
            try:
                db.x("INSERT INTO casino_games(sid,world_id,user_id,deck,player,dealer,state,result,seq,created_at,updated_at) VALUES(?,?,?,?,?,?,?,?,1,?,?)",
                     (sid, world_id, uid, deck, pl, de, h.state, h.result, now, now))
            except sqlite3.IntegrityError:
                return None
        else:
            if row["user_id"] != uid or row["world_id"] != world_id or row["state"] != "done" or row["seq"] != seq:
                return None
            if db.x("UPDATE casino_games SET deck=?,player=?,dealer=?,state=?,result=?,seq=seq+1,updated_at=? WHERE sid=? AND seq=? AND state='done' AND user_id=?",
                    (deck, pl, de, h.state, h.result, now, sid, seq, uid)).rowcount != 1:
                return None
        return get(db, sid)


def act(db, sid: str, world_id: int, uid: int, action: str, seq: int, now: int):
    """Apply 'hit' or 'stand' to the running hand of `sid` if the press is current (right owner, right seq, hand still being played).
    Returns the new row, or None when nothing was changed."""
    if action not in ("hit", "stand"):
        return None
    with db.tx():
        row = get(db, sid)
        if row is None or row["user_id"] != uid or row["world_id"] != world_id or row["state"] != "playing" or row["seq"] != seq:
            return None
        h = hand_of(row)
        (hit if action == "hit" else stand)(h)
        deck, pl, de = _dump(h)
        if db.x("UPDATE casino_games SET deck=?,player=?,dealer=?,state=?,result=?,seq=seq+1,updated_at=? WHERE sid=? AND seq=? AND state='playing' AND user_id=?",
                (deck, pl, de, h.state, h.result, now, sid, seq, uid)).rowcount != 1:
            return None
        return get(db, sid)
