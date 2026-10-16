"""Everything that moves money between players and stocks."""
from __future__ import annotations

import random

from . import config as C
from . import engine as E
from . import achievements, exchange, quests
from .core import (
    GameError, add_dividend, add_news, burn, change_pct, check_level, check_text, clean, clear_event, counter, eff, get_user, holdings_rows,
    kv_get, kv_int, kv_set, liq, mint, need_stock, need_user, net_worth, norm_ticker, notify, now, perk, price, record, resync,
    set_event, settle, stock_row, TICKER_RE, RESERVED,
)
from .db import Database


# --- fees -------------------------------------------------------------------------
def trade_fee_bps(db: Database, uid: int) -> int:
    if kv_int(db, "fee_free_until") > now():
        return 0
    return C.TRADE_FEE_BPS * (100 - 10 * perk(db, uid, "fee")) // 100


def _distribute_fee(db: Database, s, fee: int, beneficiary: bool = True) -> None:
    """Split a trade fee between the stock's beneficiary and the burn pile. `s` is the PRE-trade row.
    Automated market traders (beneficiary=False) pay the fee but nobody earns it, so they can't be farmed."""
    if fee <= 0:
        return
    if s["kind"] == "listed":                       # house-priced company: part goes to the holders, the rest burns
        exchange.distribute_levy(db, s["id"], fee)
        return
    if not beneficiary:
        burn(db, fee)
        return
    share = C.OWNER_SHARE_BPS
    if s["owner_id"]:
        share = min(9000, share * (100 + 10 * perk(db, s["owner_id"], "owner")) // 100)
    cut = fee * share // C.BPS
    burn(db, fee - cut)
    if cut <= 0:
        return
    sid, kind = s["id"], s["kind"]
    if kind == "index":
        if not add_dividend(db, sid, cut):
            burn(db, cut)
        return
    db.x("UPDATE stocks SET fees_earned=fees_earned+? WHERE id=?", (cut, sid))
    if kind == "company":
        db.x("UPDATE companies SET treasury=treasury+? WHERE id=?", (cut, s["company_id"]))
        return
    div = cut * s["div_pct"] // 100
    if div and not add_dividend(db, sid, div):
        div = 0  # nobody holds shares yet: the owner keeps it
    db.x("UPDATE users SET pending_inc=pending_inc+? WHERE id=?", (cut - div, s["owner_id"]))


def _count_trade(db: Database, s, amount: int) -> None:
    """Analytics counters of a player trade. Trades in listed companies are counted apart: the exchange's own prices
    read the ordinary counters, and they must never feed back from the trades made in the exchange itself."""
    if s["kind"] == "listed":
        counter(db, "ex_trades")
        counter(db, "ex_volume", amount)
        return
    counter(db, "trades")
    counter(db, "volume", amount)
    if s["kind"] == "meme":
        counter(db, "meme_trades")


def _bump_rep(db: Database, owner_id: int) -> None:
    key = f"rep:{owner_id}:{now() // 86400}"
    if kv_int(db, key) >= C.REP_DAILY_GAIN_CAP:
        return
    kv_set(db, key, kv_int(db, key) + 1)
    db.x("UPDATE users SET rep=MIN(?, rep+1) WHERE id=?", (C.REP_MAX, owner_id))


def _after_buy_news(db: Database, sid: int, new_holder: bool) -> None:
    s = stock_row(db, sid)
    if s["kind"] in ("index", "listed"):
        return
    if new_holder and s["holders"] in (10, 25, 50, 100, 250, 500, 1000):
        add_news(db, "holders", sid, ticker=s["ticker"], n=s["holders"])
    p_ago = _price_1h(db, sid)
    if p_ago and s["supply"] >= 20:
        gain = (price(s) - p_ago) * 100.0 / p_ago
        if gain >= 100 and now() - kv_int(db, f"bubble:{sid}") > 6 * 3600:
            kv_set(db, f"bubble:{sid}", now())
            add_news(db, "bubble", sid, ticker=s["ticker"], pct=round(gain))
            if random.random() < 0.5:
                kv_set(db, f"corr:{sid}", now() + random.randint(20, 60) * 60)
        elif gain >= 50 and now() - kv_int(db, f"surge:{sid}") > 3 * 3600:
            kv_set(db, f"surge:{sid}", now())
            add_news(db, "surge", sid, ticker=s["ticker"], pct=round(gain))


def _price_1h(db: Database, sid: int):
    r = db.q1("SELECT price FROM history WHERE stock_id=? AND ts<=? ORDER BY ts DESC LIMIT 1", (sid, now() - 3600))
    if r:
        return r["price"]
    r = db.q1("SELECT price FROM history WHERE stock_id=? ORDER BY ts ASC LIMIT 1", (sid,))
    return r["price"] if r else None


def _check_funded(db: Database, sid: int) -> None:
    s = stock_row(db, sid)
    if s["kind"] != "project" or s["funded"] or s["goal"] <= 0 or s["reserve"] < s["goal"]:
        return
    db.x("UPDATE stocks SET funded=1 WHERE id=?", (sid,))
    reward = s["goal"] * C.PROJECT_REWARD_BPS // C.BPS
    mint(db, reward)
    db.x("UPDATE users SET pending_inc=pending_inc+? WHERE id=?", (reward, s["owner_id"]))
    special = s["goal"] * C.PROJECT_DIVIDEND_BPS // C.BPS
    if add_dividend(db, sid, special):
        mint(db, special)
    add_news(db, "funded", sid, ticker=s["ticker"])
    achievements.check(db, s["owner_id"], "funded")


def _activate(db: Database, uid: int) -> None:
    u = need_user(db, uid)
    if u["activated"] or u["trades"] < C.ACTIVATION_TRADES:
        return
    db.x("UPDATE users SET activated=1 WHERE id=?", (uid,))
    ref = u["referrer"]
    if ref and get_user(db, ref):
        db.x("UPDATE users SET cash=cash+? WHERE id=?", (C.REF_BONUS_INVITER, ref))
        mint(db, C.REF_BONUS_INVITER)
        notify(db, ref, "referral", name=u["name"], amount=C.REF_BONUS_INVITER)
        quests.track(db, ref, "invite")
        achievements.check(db, ref, "invite")


def _whale(db: Database, s, u, side: str, amount: int, reserve_before: int, shares: int) -> None:
    """Headline (and DMs to holders) when a single trade is huge compared with the stock's reserve."""
    if amount < max(C.WHALE_MIN, reserve_before * C.WHALE_PCT_BPS // C.BPS):
        return
    key = f"whale:{s['id']}"
    if now() - kv_int(db, key) < C.WHALE_COOLDOWN:
        return
    kv_set(db, key, now())
    pct = min(999, round(amount * 100 / reserve_before)) if reserve_before > 0 else 100
    params = dict(ticker=s["ticker"], who=u["name"], amount=amount, pct=pct, shares=shares)
    add_news(db, f"whale_{side}", s["id"], **params)
    if s["kind"] != "listed":
        counter(db, "whales")
    achievements.check(db, u["id"], "whale")
    for r in db.qa(
        "SELECT h.user_id FROM holdings h JOIN users x ON x.id=h.user_id WHERE h.stock_id=? AND h.user_id!=? AND x.whale_dm=1 AND x.bot=0 LIMIT ?",
        (s["id"], u["id"], C.WHALE_DM_MAX),
    ):
        notify(db, r["user_id"], f"whale_{side}", **params)


# --- launch -----------------------------------------------------------------------
def max_stocks(u) -> int:
    return min(8, 1 + u["max_level"]) + u["extra_slots"]


def launch_cost(db: Database, u, at: C.AssetType) -> int:
    rep_mult = max(50, min(300, 200 - u["rep"]))
    cost = at.launch_cost * rep_mult // 100
    return cost * (100 - 10 * perk(db, u["id"], "launch")) // 100


def launch(db: Database, uid: int, ticker: str, name: str, kind: str = "personal", descr: str = "", goal: int = 0):
    ticker = norm_ticker(ticker)
    if not TICKER_RE.match(ticker) or ticker.startswith("IDX") or ticker in RESERVED:
        raise GameError("bad_ticker")
    name, descr = clean(name, 30), clean(descr, 120)
    check_text(name, 2)
    if descr:
        check_text(descr)
    at = C.ASSET_TYPES.get(kind)
    if not at:
        raise GameError("bad_kind")
    with db.tx():
        need_user(db, uid)
        check_level(db, uid)          # unlocks must use the player's real level, not a stale saved one
        u = need_user(db, uid)
        if db.q1("SELECT 1 FROM stocks WHERE ticker=?", (ticker,)):
            raise GameError("ticker_taken", ticker=ticker)
        if u["max_level"] < at.min_level:
            raise GameError("need_level", level=at.min_level, have=u["max_level"], nw=E.level_threshold(at.min_level))
        if at.legacy_perk and perk(db, uid, at.legacy_perk) < 1:
            raise GameError("need_legacy", perk=at.legacy_perk)
        owned = db.q1("SELECT COUNT(*) c FROM stocks WHERE owner_id=? AND status='active' AND kind IN ('personal','meme','project','bluechip')", (uid,))["c"]
        if owned >= max_stocks(u):
            raise GameError("max_stocks", n=max_stocks(u))
        if kind == "project":
            if not (C.PROJECT_MIN_GOAL <= goal <= C.PROJECT_MAX_GOAL):
                raise GameError("bad_goal", lo=C.PROJECT_MIN_GOAL, hi=C.PROJECT_MAX_GOAL)
        else:
            goal = 0
        cost = launch_cost(db, u, at)
        founder = E.curve_F(C.FOUNDER_SHARES, at.p0, at.k)
        if u["cash"] < cost + founder:
            raise GameError("funds", need=cost + founder, have=u["cash"])
        t = now()
        cur = db.x(
            "INSERT INTO stocks(ticker,name,descr,kind,owner_id,p0,k,supply,reserve,holders,created_at,goal) VALUES(?,?,?,?,?,?,?,?,?,?,?,?)",
            (ticker, name, descr, kind, uid, at.p0, at.k, C.FOUNDER_SHARES, founder, 1, t, goal),
        )
        sid = cur.lastrowid
        db.x("INSERT INTO holdings(user_id,stock_id,shares,cost,debt,last_buy,last_bonus) VALUES(?,?,?,?,0,?,?)", (uid, sid, C.FOUNDER_SHARES, founder, t, t))
        db.x("UPDATE users SET cash=cash-?, last_trade=? WHERE id=?", (cost + founder, t, uid))
        burn(db, cost)
        boost = perk(db, uid, "boost")
        if boost:
            set_event(db, sid, C.BPS + 1000 * boost, t + 3600, "launch")
        record(db, sid)
        add_news(db, "launch", sid, ticker=ticker, name=name, owner=u["name"], asset=kind)
        quests.track(db, uid, "launch")
        counter(db, "launches")
        achievements.check(db, uid, "launch")
        check_level(db, uid)
        return stock_row(db, sid)


# --- buy / sell -------------------------------------------------------------------
def buy(db: Database, uid: int, ticker: str, n: int = 0, spend_all: bool = False) -> dict:
    with db.tx():
        u = need_user(db, uid)
        s = need_stock(db, ticker)
        is_bot = bool(u["bot"])
        t = now()
        if t - u["last_trade"] < C.TRADE_COOLDOWN:
            raise GameError("cooldown")
        listed = s["kind"] == "listed"
        fee_bps = trade_fee_bps(db, uid)
        a = (s["p0"], s["k"], eff(s))
        if spend_all:
            n = exchange.max_affordable(db, s, u["cash"]) if listed else E.max_affordable(s["supply"], u["cash"], *a, fee_bps)
            if n <= 0:
                raise GameError("funds", need=E.quote_buy(s["supply"], 1, *a), have=u["cash"])
        if n <= 0 or n > C.MAX_SHARES_PER_TRADE:
            raise GameError("bad_amount")
        cost = E.quote_buy(s["supply"], n, *a)
        fee = exchange.levy(db, s, cost) if listed else E.fee_of(cost, fee_bps)
        total = cost + fee
        if u["cash"] < total:
            raise GameError("funds", need=total, have=u["cash"])
        reserve_before = s["reserve"]
        _distribute_fee(db, s, fee, beneficiary=not is_bot)
        s2 = stock_row(db, s["id"])
        h = db.q1("SELECT * FROM holdings WHERE user_id=? AND stock_id=?", (uid, s["id"]))
        new_holder = h is None
        old_shares = 0 if new_holder else h["shares"]
        if not new_holder:
            settle(db, uid, h, s2)
        new_shares = old_shares + n
        db.x("UPDATE users SET cash=cash-?, last_trade=?, trades=trades+1 WHERE id=?", (total, t, uid))
        db.x(
            "UPDATE stocks SET reserve=reserve+?, supply=supply+?, volume=volume+?, holders=holders+? WHERE id=?",
            (cost, n, cost, 1 if new_holder else 0, s["id"]),
        )
        debt = new_shares * s2["acc_div"] // C.ACC
        if new_holder:
            db.x("INSERT INTO holdings(user_id,stock_id,shares,cost,debt,last_buy,last_bonus) VALUES(?,?,?,?,?,?,?)", (uid, s["id"], new_shares, total, debt, t, t))
        else:
            db.x("UPDATE holdings SET shares=?, cost=cost+?, debt=?, last_buy=? WHERE user_id=? AND stock_id=?", (new_shares, total, debt, t, uid, s["id"]))
        if listed:
            exchange.add_flow(db, s["id"], cost)
        record(db, s["id"])
        if not is_bot:
            if new_holder and s["owner_id"] and s["owner_id"] != uid:
                _bump_rep(db, s["owner_id"])
                quests.track(db, s["owner_id"], "investor")
                achievements.check(db, s["owner_id"], "investor")
            _after_buy_news(db, s["id"], new_holder)
            _whale(db, s, u, "buy", cost, reserve_before, n)
            quests.track(db, uid, "buy")
            if cost >= quests.MIN_TRADE:
                quests.track(db, uid, "trade_ok")
            quests.track(db, uid, "buy_volume", cost)
            quests.track(db, uid, "volume", cost)
            _count_trade(db, s, cost)
            achievements.check(db, uid, "trade")
        else:
            counter(db, "bot_trades")
        _check_funded(db, s["id"])
        lvl = None
        if not is_bot:
            _activate(db, uid)
            nw, lvl = check_level(db, uid)
        new = stock_row(db, s["id"])
        return {"stock": new, "shares": n, "cost": cost, "fee": fee, "total": total, "price": price(new), "levelup": lvl, "holding": new_shares}


def sell(db: Database, uid: int, ticker: str, n: int = 0, sell_all: bool = False, *, fee_bps: int | None = None, force: bool = False) -> dict:
    with db.tx():
        u = need_user(db, uid)
        s = need_stock(db, ticker)
        is_bot = bool(u["bot"])
        t = now()
        if not force and t - u["last_trade"] < C.TRADE_COOLDOWN:
            raise GameError("cooldown")
        h = db.q1("SELECT * FROM holdings WHERE user_id=? AND stock_id=?", (uid, s["id"]))
        have = h["shares"] if h else 0
        if sell_all:
            n = have
        if n <= 0 or n > C.MAX_SHARES_PER_TRADE:
            raise GameError("bad_amount")
        if n > have:
            raise GameError("shares", have=have)
        if not force and t - h["last_buy"] < C.SELL_LOCK:
            raise GameError("locked", secs=C.SELL_LOCK - (t - h["last_buy"]))
        listed = s["kind"] == "listed"
        override = fee_bps                      # an explicit fee (rebirth liquidation: 0) replaces the whole levy
        if fee_bps is None:
            fee_bps = trade_fee_bps(db, uid)
        proceeds = E.quote_sell(s["supply"], n, s["p0"], s["k"], eff(s))
        fee = exchange.levy(db, s, proceeds, spread_bps=override, slippage=override is None) if listed else E.fee_of(proceeds, fee_bps)
        net = proceeds - fee
        reserve_before = s["reserve"]
        if not listed:
            _distribute_fee(db, s, fee, beneficiary=not is_bot)
        s2 = stock_row(db, s["id"])
        settle(db, uid, h, s2)
        new_shares = have - n
        cost_removed = h["cost"] * n // have
        db.x("UPDATE users SET cash=cash+?, last_trade=?, trades=trades+1 WHERE id=?", (net, t, uid))
        db.x(
            "UPDATE stocks SET reserve=reserve-?, supply=supply-?, volume=volume+?, holders=holders-? WHERE id=?",
            (proceeds, n, proceeds, 1 if new_shares == 0 else 0, s["id"]),
        )
        if new_shares == 0:
            db.x("DELETE FROM holdings WHERE user_id=? AND stock_id=?", (uid, s["id"]))
        else:
            db.x("UPDATE holdings SET shares=?, cost=cost-?, debt=? WHERE user_id=? AND stock_id=?", (new_shares, cost_removed, new_shares * s2["acc_div"] // C.ACC, uid, s["id"]))
        if listed:
            _distribute_fee(db, s, fee, beneficiary=not is_bot)     # AFTER the sale: the sold shares don't share in their own levy
            if override is None:
                exchange.add_flow(db, s["id"], proceeds)
        record(db, s["id"])
        lvl = None
        if not is_bot:
            if not force:
                _whale(db, s, u, "sell", proceeds, reserve_before, n)
                if proceeds >= quests.MIN_TRADE:
                    quests.track(db, uid, "trade_ok")
                quests.track(db, uid, "volume", proceeds)
                if net - cost_removed > 0:
                    quests.track(db, uid, "profit", net - cost_removed)
                _count_trade(db, s, proceeds)
                achievements.check(db, uid, "trade")
                achievements.check(db, uid, "profit", pnl=net - cost_removed)
            _activate(db, uid)
        else:
            counter(db, "bot_trades")
        if not is_bot:
            nw, lvl = check_level(db, uid)
        new = stock_row(db, s["id"])
        return {"stock": new, "shares": n, "proceeds": proceeds, "fee": fee, "net": net, "pnl": net - cost_removed, "price": price(new), "levelup": lvl, "holding": new_shares}


# --- passive income ---------------------------------------------------------------
def settle_all(db: Database, uid: int) -> None:
    for r in holdings_rows(db, uid):
        settle(db, uid, r, r)
        db.x("UPDATE holdings SET debt=? WHERE user_id=? AND stock_id=?", (r["shares"] * r["acc_div"] // C.ACC, uid, r["id"]))


def accrue_bonus(db: Database, uid: int) -> int:
    """Holder bonus: a tiny hourly yield on the liquidation value of every position (max 24h per claim)."""
    mult = 100 + 50 * perk(db, uid, "bonus")
    t, total = now(), 0
    for r in holdings_rows(db, uid):
        if r["kind"] == "listed":                # house-priced companies pay no minted yield (only a share of other traders' levies)
            continue
        hours = min(C.BONUS_MAX_HOURS, (t - r["last_bonus"]) / 3600.0)
        if hours <= 0:
            continue
        b = int(liq(r, r["shares"]) * C.HOLDER_BONUS_BPS_PER_HOUR * hours * mult / 100 / C.BPS)
        total += max(b, 0)
        db.x("UPDATE holdings SET last_bonus=? WHERE user_id=? AND stock_id=?", (t, uid, r["id"]))
    if total:
        db.x("UPDATE users SET pending_inc=pending_inc+? WHERE id=?", (total, uid))
        mint(db, total)
    return total


def _payout(db: Database, u, amount: int) -> tuple[int, int, int]:
    """Apply the 'income' legacy perk and the company tax. Returns (net, tax, bonus)."""
    bonus = amount * 5 * perk(db, u["id"], "income") // 100
    if bonus:
        mint(db, bonus)
    gross = amount + bonus
    tax = 0
    if u["company_id"]:
        c = db.q1("SELECT stock_id FROM companies WHERE id=?", (u["company_id"],))
        if c and c["stock_id"]:
            tax = gross * C.COMPANY_TAX_BPS // C.BPS
            if tax and not add_dividend(db, c["stock_id"], tax):
                burn(db, tax)
    return gross - tax, tax, bonus


def claim_dividends(db: Database, uid: int) -> dict:
    with db.tx():
        u = need_user(db, uid)
        settle_all(db, uid)
        u = need_user(db, uid)
        amt = u["pending_div"]
        if amt <= 0:
            raise GameError("nothing")
        net, tax, bonus = _payout(db, u, amt)
        db.x("UPDATE users SET cash=cash+?, pending_div=0 WHERE id=?", (net, uid))
        if not u["bot"]:
            counter(db, "payouts")
        quests.track(db, uid, "claim")
        kv_set(db, f"pi:{uid}", kv_int(db, f"pi:{uid}") + net)
        achievements.check(db, uid, "claim")
        nw, lvl = check_level(db, uid)
        return {"gross": amt, "net": net, "tax": tax, "bonus": bonus, "levelup": lvl}


def claim_income(db: Database, uid: int) -> dict:
    with db.tx():
        need_user(db, uid)
        bonus_acc = accrue_bonus(db, uid)
        u = need_user(db, uid)
        amt = u["pending_inc"]
        if amt <= 0:
            raise GameError("nothing")
        net, tax, bonus = _payout(db, u, amt)
        db.x("UPDATE users SET cash=cash+?, pending_inc=0 WHERE id=?", (net, uid))
        if not u["bot"]:
            counter(db, "payouts")
        quests.track(db, uid, "claim")
        kv_set(db, f"pi:{uid}", kv_int(db, f"pi:{uid}") + net)
        achievements.check(db, uid, "claim")
        nw, lvl = check_level(db, uid)
        return {"gross": amt, "net": net, "tax": tax, "bonus": bonus, "holder_bonus": bonus_acc, "levelup": lvl}


def set_dividend(db: Database, uid: int, pct: int, ticker: str | None = None):
    if not 0 <= pct <= 50:
        raise GameError("bad_pct")
    with db.tx():
        s = my_stock(db, uid, ticker)
        db.x("UPDATE stocks SET div_pct=? WHERE id=?", (pct, s["id"]))
        return stock_row(db, s["id"])


def my_stocks(db: Database, uid: int) -> list:
    return db.qa("SELECT * FROM stocks WHERE owner_id=? AND status='active' AND kind IN ('personal','meme','project','bluechip') ORDER BY created_at", (uid,))


def my_stock(db: Database, uid: int, ticker: str | None = None):
    if ticker:
        s = need_stock(db, ticker)
        if s["owner_id"] != uid or s["kind"] not in C.OWNER_KINDS:
            raise GameError("not_owner")
        return s
    rows = my_stocks(db, uid)
    if not rows:
        raise GameError("no_own_stock")
    return max(rows, key=lambda r: price(r) * r["supply"])


def edit_stock(db: Database, uid: int, field: str, value: str, ticker: str | None = None):
    with db.tx():
        s = my_stock(db, uid, ticker)
        if field == "name":
            value = clean(value, 30)
            check_text(value, 2)
        elif field == "descr":
            value = clean(value, 120)
            check_text(value, 0)
        elif field == "logo":
            pass
        else:
            raise GameError("bad_usage")
        col = {"name": "name", "descr": "descr", "logo": "logo"}[field]
        db.x(f"UPDATE stocks SET {col}=? WHERE id=?", (value, s["id"]))
        return stock_row(db, s["id"])


# --- rug pull / delisting ----------------------------------------------------------
def rug(db: Database, uid: int, ticker: str | None = None, pct: int = 50, allowed: bool = True) -> dict:
    """Owner drains `pct`% of the reserve: the price crashes for everyone, the owner takes the coins."""
    if not allowed:
        raise GameError("rug_off")
    if not 10 <= pct <= 70:
        raise GameError("bad_pct")
    with db.tx():
        s = my_stock(db, uid, ticker)
        t = now()
        if t - s["created_at"] < C.RUG_MIN_AGE:
            raise GameError("rug_young", secs=C.RUG_MIN_AGE - (t - s["created_at"]))
        if s["last_rug"] and t - s["last_rug"] < C.RUG_COOLDOWN:
            raise GameError("rug_cooldown", secs=C.RUG_COOLDOWN - (t - s["last_rug"]))
        if s["supply"] <= 0 or s["reserve"] <= 0:
            raise GameError("nothing")
        new_base = max(1000, s["base_bps"] * (100 - pct) // 100)
        db.x("UPDATE stocks SET base_bps=?, rugs=rugs+1, last_rug=? WHERE id=?", (new_base, t, s["id"]))
        before = s["reserve"]
        s2 = stock_row(db, s["id"])
        target = E.f_eff(s2["supply"], s2["p0"], s2["k"], eff(s2))
        taken = max(0, before - target)
        tax = taken * C.RUG_TAX_BPS // C.BPS
        db.x("UPDATE stocks SET reserve=? WHERE id=?", (target, s["id"]))
        db.x("UPDATE users SET cash=cash+?, rep=MAX(0, rep-?) WHERE id=?", (taken - tax, C.RUG_REP_PENALTY, uid))
        burn(db, tax)
        record(db, s["id"])
        counter(db, "rugs")
        add_news(db, "rug", s["id"], ticker=s["ticker"], owner=need_user(db, uid)["name"], pct=pct)
        for r in db.qa("SELECT user_id FROM holdings WHERE stock_id=? AND user_id!=? LIMIT 200", (s["id"], uid)):
            notify(db, r["user_id"], "rugged", ticker=s["ticker"], pct=pct)
        nw, lvl = check_level(db, uid)
        return {"stock": stock_row(db, s["id"]), "taken": taken - tax, "tax": tax, "pct": pct, "levelup": lvl}


def delist(db: Database, sid: int) -> None:
    """Retire a stock: pay every holder their pro-rata share of the reserve."""
    s = stock_row(db, sid)
    if not s or s["status"] != "active":
        return
    holders = db.qa("SELECT * FROM holdings WHERE stock_id=?", (sid,))
    paid = 0
    for h in holders:
        settle(db, h["user_id"], h, s)
        part = s["reserve"] * h["shares"] // s["supply"] if s["supply"] else 0
        paid += part
        db.x("UPDATE users SET cash=cash+? WHERE id=?", (part, h["user_id"]))
        notify(db, h["user_id"], "delisted", ticker=s["ticker"], amount=part)
    burn(db, s["reserve"] - paid)
    db.x("DELETE FROM holdings WHERE stock_id=?", (sid,))
    db.x("DELETE FROM alerts WHERE stock_id=?", (sid,))
    db.x("UPDATE stocks SET status='delisted', supply=0, reserve=0, holders=0, event_bps=?, event_until=0, event_kind='' WHERE id=?", (C.BPS, sid))
    add_news(db, "delist", sid, ticker=s["ticker"])


# --- alerts ------------------------------------------------------------------------
def set_alert(db: Database, uid: int, ticker: str, target: int) -> dict:
    with db.tx():
        need_user(db, uid)
        lvl = perk(db, uid, "alerts")
        if lvl < 1:
            raise GameError("alerts_locked")
        s = need_stock(db, ticker)
        if target <= 0:
            raise GameError("bad_amount")
        limit = lvl * C.ALERT_PERK_STEP
        if db.q1("SELECT COUNT(*) c FROM alerts WHERE user_id=?", (uid,))["c"] >= limit:
            raise GameError("alerts_max", n=limit)
        cur = price(s)
        if target == cur:
            raise GameError("bad_amount")
        direction = "above" if target > cur else "below"
        db.x("INSERT INTO alerts(user_id,stock_id,price,direction,created) VALUES(?,?,?,?,?)", (uid, s["id"], target, direction, now()))
        return {"stock": s, "target": target, "direction": direction}


def list_alerts(db: Database, uid: int) -> list:
    return db.qa("SELECT a.*, s.ticker FROM alerts a JOIN stocks s ON s.id=a.stock_id WHERE a.user_id=? ORDER BY a.id", (uid,))


def delete_alerts(db: Database, uid: int, ticker: str | None = None) -> int:
    if ticker:
        s = need_stock(db, ticker, active=False)
        return db.x("DELETE FROM alerts WHERE user_id=? AND stock_id=?", (uid, s["id"])).rowcount
    return db.x("DELETE FROM alerts WHERE user_id=?", (uid,)).rowcount


def check_alerts(db: Database) -> int:
    n = 0
    with db.tx():
        for a in db.qa("SELECT a.*, s.ticker FROM alerts a JOIN stocks s ON s.id=a.stock_id"):
            s = stock_row(db, a["stock_id"])
            p = price(s)
            if (a["direction"] == "above" and p >= a["price"]) or (a["direction"] == "below" and p <= a["price"]):
                notify(db, a["user_id"], "alert", ticker=a["ticker"], price=p, target=a["price"])
                db.x("DELETE FROM alerts WHERE id=?", (a["id"],))
                n += 1
    return n


# --- read-only views of the market ---------------------------------------------------
def portfolio(db: Database, uid: int) -> dict:
    u = need_user(db, uid)
    rows = []
    for r in holdings_rows(db, uid):
        value = liq(r, r["shares"])
        rows.append({"stock": r, "shares": r["shares"], "value": value, "cost": r["cost"], "pnl": value - r["cost"],
                     "pct": (value - r["cost"]) * 100.0 / r["cost"] if r["cost"] else 0.0})
    return {"user": u, "rows": rows, "networth": net_worth(db, uid)}


def market(db: Database, view: str = "top", limit: int = 8) -> list:
    rows = db.qa("SELECT * FROM stocks WHERE status='active' AND (supply>0 OR kind='listed')")
    t = now()
    if view == "trend":
        rows = sorted(rows, key=lambda r: (r["hype"], r["volume"]), reverse=True)
    elif view == "new":
        rows = sorted(rows, key=lambda r: r["created_at"], reverse=True)
    elif view in ("gain", "lose"):
        rows = sorted(rows, key=lambda r: change_pct(db, r), reverse=(view == "gain"))
    else:
        rows = sorted(rows, key=lambda r: (1 if r["featured_until"] > t else 0, price(r) * r["supply"]), reverse=True)
    return rows[:limit]
