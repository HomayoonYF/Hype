"""Pure economy maths (no database, no Telegram).

Every stock sells shares along a *linear bonding curve*: the i-th share
(i = 0, 1, 2, ...) costs  p0 + k*i  units.  The coins paid in by buyers are
locked in the stock's reserve, and the reserve is always exactly the integral
of the curve:

    reserve == F(supply) = p0*supply + k*supply*(supply-1)/2

Buying n shares costs F(s+n) - F(s); selling n shares pays F(s) - F(s-n).
Because both are differences of the same function, buying and selling are
perfectly symmetric (no arbitrage, no insolvency) and the maths telescopes.

A price multiplier in basis points (`bps`) lets events and rug pulls move the
whole curve up or down; the reserve is re-synced whenever it changes.
"""
from __future__ import annotations

import math

from . import config as C


def curve_F(s: int, p0: int, k: int) -> int:
    return p0 * s + k * (s * (s - 1) // 2)


def f_eff(s: int, p0: int, k: int, bps: int) -> int:
    return curve_F(s, p0, k) * bps // C.BPS


def spot(s: int, p0: int, k: int, bps: int) -> int:
    """Price of the next share."""
    return (p0 + k * s) * bps // C.BPS


def quote_buy(s: int, n: int, p0: int, k: int, bps: int) -> int:
    return f_eff(s + n, p0, k, bps) - f_eff(s, p0, k, bps)


def quote_sell(s: int, n: int, p0: int, k: int, bps: int) -> int:
    return f_eff(s, p0, k, bps) - f_eff(s - n, p0, k, bps)


def fee_of(amount: int, bps: int) -> int:
    """Fee rounded up so the house never loses a unit."""
    if amount <= 0 or bps <= 0:
        return 0
    return -(-amount * bps // C.BPS)


def max_affordable(s: int, budget: int, p0: int, k: int, bps: int, fee_bps: int) -> int:
    """Largest n such that cost(n) + fee(cost(n)) <= budget."""
    def total(n: int) -> int:
        c = quote_buy(s, n, p0, k, bps)
        return c + fee_of(c, fee_bps)

    if budget <= 0 or total(1) > budget:
        return 0
    lo, hi = 1, 2
    while hi < C.MAX_SHARES_PER_TRADE and total(hi) <= budget:
        lo, hi = hi, hi * 2
    hi = min(hi, C.MAX_SHARES_PER_TRADE + 1)
    while hi - lo > 1:
        mid = (lo + hi) // 2
        if total(mid) <= budget:
            lo = mid
        else:
            hi = mid
    return min(lo, C.MAX_SHARES_PER_TRADE)


# --- levels ----------------------------------------------------------------
def level_threshold(level: int) -> int:
    return 0 if level <= 0 else C.LEVEL_BASE * 3 ** (level - 1)


def level_for(net_worth: int) -> int:
    lvl = 0
    while net_worth >= level_threshold(lvl + 1):
        lvl += 1
    return lvl


# --- rebirth ---------------------------------------------------------------
def rebirth_min(rebirths: int) -> int:
    return C.REBIRTH_BASE * 2 ** rebirths


def legacy_gain(net_worth: int) -> int:
    return max(1, math.isqrt(max(0, net_worth // C.UNIT // 10_000)))


# --- perks -----------------------------------------------------------------
def perk_cost(perk_key: str, current_level: int) -> int:
    return C.PERKS[perk_key].base_cost * (current_level + 1)
