"""The buttons of the business world (v0.211): every `bz:<action>[:<arguments>]:<player id>` press is dispatched here.

A button has exactly the pieces the bot drew into it: ACTS names, for every action, the SHAPE of its arguments (`i` a whole number, `t` an 8-digit token of a stock variant or a brand, `w` a short word) and
the function that answers. A press with another number of pieces, or a piece of the wrong shape, is a hand-made button: it changes nothing and gets one short alert. The trailing player id was already
checked against the presser (handlers.BOUND), and every function re-reads the SAVED state (a property, a quote, a stack, a contract) before it does anything, so an outdated or repeated press is
answered with a short alert and never done twice. Where the player is, is never read from a button: functions that need it ask the server (presence).
"""
from __future__ import annotations

import inspect
import logging
import re

from . import bizui, peopleui, shopui
from .core import GameError
from .texts import t
from .views import Reply

log = logging.getLogger("hypemarket")

_SHAPE = {"i": re.compile(r"^\d{1,12}$"), "t": re.compile(r"^[0-9a-f]{8}$"), "w": re.compile(r"^[a-z][a-z0-9_]{0,23}$")}


# action: (shape, function, constant arguments after the pressed ones)  -  a tuple of shapes lists the accepted alternatives
ACTS: dict = {
    # --- hub, properties, brands, branches, till, upgrades (bizui)
    "h": ("", bizui.hub, ()),
    "n": ("i", bizui.near_list, ()),
    "p": ("i", bizui.props_list, ()),
    "pi": ("i", bizui.prop_inspect, ()),
    "pg": ("i", bizui.prop_walk, ()),
    "pb": ("i", bizui.prop_buy_ask, ()),
    "pd": ("i", bizui.prop_buy, ()),
    "po": ("i", bizui.prop_manage, ()),
    "pm": ("iw", bizui.mode_ask, ()),
    "pM": ("iw", bizui.mode_do, ()),
    "b": ("", bizui.brands_screen, ()),
    "br": ("t", bizui.brand_ask, ()),
    "bR": ("t", bizui.brand_do, ()),
    "ob": ("i", bizui.open_ask, ()),
    "oB": ("it", bizui.open_do, ()),
    "l": ("", bizui.branches_list, ()),
    "bo": ("i", bizui.branch_screen, ()),
    "bf": ("i", bizui.till_screen, ()),
    "fm": ("ii", bizui.fund_do, ()),
    "wd": ("ii", bizui.withdraw_do, ()),
    "u": ("i", bizui.upgrades_screen, ()),
    "ui": ("iw", bizui.upgrade_detail, ()),
    "uI": ("iwi", bizui.upgrade_do, ()),
    "cl": ("i", bizui.close_ask, ()),
    "cL": ("i", bizui.close_do, ()),
    # --- stock and display, production, ads, the shop floor, approvals (shopui)
    "s": (("i", "ii"), shopui.stock_screen, ()),
    "sv": ("it", shopui.variant_screen, ()),
    "sm": ("itwi", shopui.stock_move, ()),
    "sp": ("iti", shopui.stock_price, ()),
    "st": ("iti", shopui.stock_number, ("target",)),
    "sk": ("iti", shopui.stock_number, ("keep",)),
    "sr": ("it", shopui.stock_toggle, ("refill",)),
    "sbb": ("it", shopui.stock_toggle, ("buyback",)),
    "sw": ("itwi", shopui.stock_withdraw, ()),
    "sx": ("it", shopui.transfer_list, ()),
    "sX": ("itii", shopui.transfer_do, ()),
    "sd": ("ii", shopui.deposit_list, ()),
    "sdi": ("ii", shopui.deposit_pick, ()),
    "sdn": ("iii", shopui.deposit_do, ()),
    "sR": ("i", shopui.stock_restock, ()),
    "pr": ("i", shopui.prod_screen, ()),
    "pp": ("iw", shopui.prod_fabric, ()),
    "pq": ("iwi", shopui.prod_pieces, ()),
    "pn": ("iwii", shopui.prod_plan, ()),
    "pS": ("iwiii", shopui.prod_start, ()),
    "pc": ("ii", shopui.prod_claim, ()),
    "pXa": ("ii", shopui.prod_cancel_ask, ()),
    "pX": ("ii", shopui.prod_cancel, ()),
    "ab": ("i", shopui.board_screen, ()),
    "af": ("i", shopui.ad_screen, ()),
    "afn": ("iii", shopui.ad_fill, ()),
    "am": ("i", shopui.myads_screen, ()),
    "ac1": ("i", shopui.ad_new_q, ()),
    "ac2": ("ii", shopui.ad_new_price, ()),
    "ac3": ("iii", shopui.ad_new_qty, ()),
    "ac4": ("iiii", shopui.ad_new_ttl, ()),
    "acD": ("iiiiii", shopui.ad_create, ()),
    "ax": ("ii", shopui.ad_cancel, ()),
    "sh": ("", shopui.shops_screen, ()),
    "sb": ("i", shopui.shop_front, ()),
    "sq": ("it", shopui.shop_item, ()),
    "sQ": ("iti", shopui.shop_quote, ()),
    "sC": ("i", shopui.shop_confirm, ()),
    "sK": ("i", shopui.shop_cancel, ()),
    "ap": ("i", shopui.approvals_screen, ()),
    "aa": ("i", shopui.approve_do, (True,)),
    "an": ("i", shopui.approve_do, (False,)),
    # --- staff, contracts, reports, reputation, style (peopleui)
    "sf": ("i", peopleui.staff_screen, ()),
    "sc": ("i", peopleui.contract_screen, ()),
    "se": ("i", peopleui.end_ask, ()),
    "sE": ("i", peopleui.end_do, ()),
    "ow": ("i", peopleui.withdraw_do, ()),
    "hp": ("i", peopleui.hire_list, ()),
    "hq": ("ii", peopleui.hire_role, ()),
    "hr": ("iii", peopleui.hire_role_chosen, ()),
    "hm": ("iiii", peopleui.hire_perms, ()),
    "hy": ("iiii", peopleui.hire_pay, ()),
    "hf": ("iiiiwi", peopleui.hire_confirm, ()),
    "hO": ("iiiiwi", peopleui.hire_do, ()),
    "hb": ("i", peopleui.hire_bot_screen, ()),
    "hB": ("ii", peopleui.hire_bot_do, ()),
    "k": ("", peopleui.my_contracts, ()),
    "ka": ("i", peopleui.contract_accept, ()),
    "kd": ("i", peopleui.contract_decline, ()),
    "kw": ("i", peopleui.claim_wage, ()),
    "rp": (("i", "ii"), peopleui.report_screen, ()),
    "ro": ("", peopleui.overview_screen, ()),
    "t": (("", "w"), peopleui.rep_screen, ()),
    "y": ("", peopleui.style_screen, ()),
    "ys": ("w", peopleui.outfit_save, ()),
    "yw": ("w", peopleui.outfit_wear, ()),
}

# a few functions take their constant arguments BEFORE the pressed ones in their signature order: (fn, constants) is called as fn(c, *pressed, *constants), except these, called as fn(c, first, constant, last)
_CONST_AFTER_FIRST = {"st", "sk", "sr", "sbb"}


def _split(shape):
    return shape if isinstance(shape, tuple) else (shape,)


def parse(act: str, pieces: list):
    """(function, converted pieces, constants) for a well-formed press, else None."""
    row = ACTS.get(act)
    if row is None:
        return None
    shape, fn, fixed = row
    for opt in _split(shape):
        if len(opt) != len(pieces):
            continue
        if all(_SHAPE[k].match(p) for k, p in zip(opt, pieces)):
            return fn, [int(p) if k == "i" else p for k, p in zip(opt, pieces)], fixed
    return None


def _call_args(act: str, args: list, fixed: tuple) -> list:
    """The arguments in the order the function takes them."""
    if act in _CONST_AFTER_FIRST:                                   # stock_number(c, bid, tok, field, n) / stock_toggle(c, bid, tok, field)
        return args[:2] + list(fixed) + args[2:]
    return args + list(fixed)


async def handle(c) -> Reply:
    """`bz:<action>[:<arguments>]:<player id>` (the player id was checked against the presser)."""
    rest = c.args[:-1]
    act = rest[0] if rest else "h"
    got = parse(act, rest[1:])
    if got is None:
        return Reply(toast=bizui.plain(t(c.lang, "bz.stale")), alert=True)
    fn, args, fixed = got
    try:
        r = fn(c, *_call_args(act, args, fixed))
        if inspect.isawaitable(r):
            r = await r
    except bizui.NoWhere as e:
        return e.reply
    return r


async def open_hub(c, screen: str = "h") -> Reply:
    """The commands (`کسب‌وکار`, `املاک`): the same screens a button opens, as a NEW message (nothing to edit)."""
    try:
        r = ACTS[screen][1](c, *([1] if ACTS[screen][0] == "i" else []))
        if inspect.isawaitable(r):
            r = await r
    except bizui.NoWhere as e:
        r = e.reply
    r.edit = False
    return r
