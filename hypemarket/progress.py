"""Daily bonus, skill tree, legacy perks, rebirth and seasons."""
from __future__ import annotations

from . import config as C
from . import engine as E
from .core import (
    GameError, add_news, burn, check_level, get_user, holdings_rows, kv_int, kv_set, mint, need_user, net_worth, notify, now,
    perk, touch_nw,
)
from .db import Database
from . import quests, trading


# --- daily ---------------------------------------------------------------------
def company_synergy_pct(db: Database, u) -> int:
    if not u["company_id"]:
        return 0
    n = db.q1("SELECT COUNT(*) c FROM users WHERE company_id=?", (u["company_id"],))["c"]
    return min(20, max(0, n - 1) * C.COMPANY_SYNERGY_PER_MEMBER)


def daily(db: Database, uid: int) -> dict:
    with db.tx():
        u = need_user(db, uid)
        t = now()
        wait = C.DAILY_COOLDOWN - (t - u["last_daily"])
        if u["last_daily"] and wait > 0:
            raise GameError("daily_wait", secs=wait)
        streak = u["streak"] + 1 if u["last_daily"] and t - u["last_daily"] < C.DAILY_RESET else 1
        base = C.DAILY_BASE + C.DAILY_STEP * min(streak, C.DAILY_STREAK_CAP)
        pct = 100 + 15 * perk(db, uid, "daily") + 5 * u["rebirths"] + company_synergy_pct(db, u)
        if u["pass_season"] and u["pass_season"] == season_id(db):
            pct += 25
        amount = base * pct // 100
        db.x("UPDATE users SET cash=cash+?, last_daily=?, streak=? WHERE id=?", (amount, t, streak, uid))
        mint(db, amount)
        quests.track(db, uid, "daily")
        if streak % 7 == 0:
            db.x("UPDATE users SET rep=MIN(?, rep+1) WHERE id=?", (C.REP_MAX, uid))
        nw, lvl = check_level(db, uid)
        return {"amount": amount, "streak": streak, "pct": pct, "levelup": lvl}


# --- levels & skill points -------------------------------------------------------
def points_total(u) -> int:
    return u["max_level"] + C.REBIRTH_POINTS * u["rebirths"] + u["bonus_points"]


def points_free(u) -> int:
    return points_total(u) - u["spent_points"]


def level_info(db: Database, uid: int) -> dict:
    with db.tx():
        need_user(db, uid)
        nw, new_level = check_level(db, uid)       # saves the level, so what you see is what the game uses
        u = need_user(db, uid)
        lvl = u["max_level"]
        nxt = E.level_threshold(lvl + 1)
        cur = E.level_threshold(lvl)
        frac = 0.0 if nxt <= cur else max(0.0, min(1.0, (nw - cur) / (nxt - cur)))
        quests.refresh(db, uid)
        return {"user": u, "level": lvl, "nw": nw, "next": nxt, "frac": frac, "points_free": points_free(u),
                "points_total": points_total(u), "levelup": new_level}


def perk_list(db: Database, uid: int, kind: str) -> list:
    out = []
    for p in C.PERKS.values():
        if p.kind != kind:
            continue
        lvl = perk(db, uid, p.key)
        out.append({"key": p.key, "level": lvl, "max": p.max_level, "cost": None if lvl >= p.max_level else E.perk_cost(p.key, lvl)})
    return out


def upgrade(db: Database, uid: int, key: str) -> dict:
    key = (key or "").strip().lower()
    p = C.PERKS.get(key)
    if not p:
        raise GameError("no_perk")
    with db.tx():
        need_user(db, uid)
        check_level(db, uid)
        u = need_user(db, uid)
        lvl = perk(db, uid, key)
        if lvl >= p.max_level:
            raise GameError("perk_max")
        cost = E.perk_cost(key, lvl)
        if p.kind == "skill":
            if points_free(u) < cost:
                raise GameError("no_points", need=cost, have=points_free(u))
            db.x("UPDATE users SET spent_points=spent_points+? WHERE id=?", (cost, uid))
        else:
            if u["legacy_pts"] < cost:
                raise GameError("no_legacy", need=cost, have=u["legacy_pts"])
            db.x("UPDATE users SET legacy_pts=legacy_pts-? WHERE id=?", (cost, uid))
        db.x("INSERT INTO perks(user_id,perk,level) VALUES(?,?,?) ON CONFLICT(user_id,perk) DO UPDATE SET level=excluded.level", (uid, key, lvl + 1))
        return {"key": key, "level": lvl + 1, "kind": p.kind}


# --- reputation -------------------------------------------------------------------
def reputation_info(db: Database, uid: int) -> dict:
    u = need_user(db, uid)
    mult = max(50, min(300, 200 - u["rep"]))
    rugs = db.q1("SELECT COALESCE(SUM(rugs),0) r FROM stocks WHERE owner_id=?", (uid,))["r"]
    return {"rep": u["rep"], "launch_mult": mult, "rugs": rugs}


# --- rebirth ----------------------------------------------------------------------
def rebirth_info(db: Database, uid: int) -> dict:
    u = need_user(db, uid)
    nw = touch_nw(db, uid)
    need = E.rebirth_min(u["rebirths"])
    return {"user": u, "nw": nw, "need": need, "ok": nw >= need, "gain": E.legacy_gain(nw), "count": u["rebirths"]}


def rebirth(db: Database, uid: int) -> dict:
    with db.tx():
        u = need_user(db, uid)
        nw = net_worth(db, uid)
        need = E.rebirth_min(u["rebirths"])
        if nw < need:
            raise GameError("rb_need", need=need, nw=nw)
        gain = E.legacy_gain(nw)
        # 1. leave / hand over the company
        from . import social
        social.leave_for_rebirth(db, uid)
        # 2. retire everything the player owns (holders are paid pro-rata from the reserve)
        for s in trading.my_stocks(db, uid):
            trading.delist(db, s["id"])
        # 3. close shorts and alerts
        for sh in db.qa("SELECT * FROM shorts WHERE user_id=?", (uid,)):
            s = db.q1("SELECT * FROM stocks WHERE id=?", (sh["stock_id"],))
            trading._settle_short(db, sh, trading._short_pnl(s, sh))
        db.x("DELETE FROM alerts WHERE user_id=?", (uid,))
        # 4. liquidate every holding at market, no fee
        for r in holdings_rows(db, uid):
            trading.sell(db, uid, r["ticker"], sell_all=True, fee_bps=0, force=True)
        # 5. reset the account
        u = need_user(db, uid)
        burn(db, u["cash"] + u["pending_div"] + u["pending_inc"])
        for p in C.PERKS.values():
            if p.kind == "skill":
                db.x("DELETE FROM perks WHERE user_id=? AND perk=?", (uid, p.key))
        db.x(
            "UPDATE users SET rebirths=rebirths+1, legacy_pts=legacy_pts+?, legacy_total=legacy_total+?, spent_points=0, "
            "max_level=0, pending_div=0, pending_inc=0, last_trade=0 WHERE id=?",
            (gain, gain, uid),
        )
        cash = C.START_CASH * (100 + 20 * perk(db, uid, "head")) // 100
        db.x("UPDATE users SET cash=?, season_base=? WHERE id=?", (cash, cash, uid))
        mint(db, cash)
        touch_nw(db, uid)
        add_news(db, "rebirth", None, name=u["name"], n=u["rebirths"] + 1)
        return {"gain": gain, "count": u["rebirths"] + 1, "cash": cash}


# --- seasons ----------------------------------------------------------------------
def season_id(db: Database) -> int:
    return kv_int(db, "season_id", 1)


def ensure_season(db: Database) -> None:
    if not kv_int(db, "season_end"):
        kv_set(db, "season_id", 1)
        kv_set(db, "season_start", now())
        kv_set(db, "season_end", now() + C.SEASON_DAYS * 86400)


def season_info(db: Database) -> dict:
    ensure_season(db)
    return {"id": season_id(db), "start": kv_int(db, "season_start"), "end": kv_int(db, "season_end"), "left": max(0, kv_int(db, "season_end") - now())}


def season_standings(db: Database, limit: int = 10) -> list:
    return db.qa(
        "SELECT id, name, badge, nw, nw - season_base AS gain FROM users WHERE trades>0 AND banned=0 AND bot=0 ORDER BY gain DESC LIMIT ?", (limit,)
    )


def refresh_all_networth(db: Database) -> int:
    """Recompute every player's net worth with one pass over the holdings, and grant any level they have earned
    (players who only watched their stocks rise get their level-up, and a DM, here). Returns the number of level-ups."""
    ups = []
    with db.tx():
        nw = {r["id"]: r["cash"] + r["pending_div"] + r["pending_inc"] for r in db.qa("SELECT id,cash,pending_div,pending_inc FROM users")}
        for r in db.qa(
            "SELECT h.user_id AS uid, h.shares, s.supply, s.p0, s.k, s.base_bps, s.event_bps FROM holdings h "
            "JOIN stocks s ON s.id=h.stock_id WHERE s.status='active'"
        ):
            nw[r["uid"]] += E.quote_sell(r["supply"], r["shares"], r["p0"], r["k"], r["base_bps"] * r["event_bps"] // C.BPS)
        for r in db.qa("SELECT user_id, SUM(margin) m FROM shorts GROUP BY user_id"):
            nw[r["user_id"]] += r["m"]
        db.conn.executemany("UPDATE users SET nw=? WHERE id=?", [(v, k) for k, v in nw.items()])
        for r in db.qa("SELECT id, max_level, bot FROM users"):
            lvl = E.level_for(nw[r["id"]])
            if lvl > r["max_level"]:
                db.x("UPDATE users SET max_level=? WHERE id=?", (lvl, r["id"]))
                if not r["bot"]:
                    ups.append(r["id"])
                    notify(db, r["id"], "levelup", level=lvl)
        for uid in ups:
            quests.refresh(db, uid)      # may pay the "reach level 1" reward...
            touch_nw(db, uid)            # ...so store the net worth again
    return len(ups)


def end_season_if_due(db: Database) -> dict | None:
    ensure_season(db)
    if now() < kv_int(db, "season_end"):
        return None
    with db.tx():
        refresh_all_networth(db)
        top = season_standings(db, len(C.SEASON_REWARDS))
        winners = []
        for (reward, lp), row in zip(C.SEASON_REWARDS, top):
            if row["gain"] <= 0:
                continue
            db.x("UPDATE users SET cash=cash+?, legacy_pts=legacy_pts+?, legacy_total=legacy_total+? WHERE id=?", (reward, lp, lp, row["id"]))
            mint(db, reward)
            notify(db, row["id"], "season_prize", coins=reward, lp=lp, rank=len(winners) + 1)
            winners.append({"id": row["id"], "name": row["name"], "gain": row["gain"]})
        db.x("UPDATE users SET season_base=nw")
        sid = season_id(db)
        kv_set(db, "season_id", sid + 1)
        kv_set(db, "season_start", now())
        kv_set(db, "season_end", now() + C.SEASON_DAYS * 86400)
        add_news(db, "season_end", None, season=sid, winner=winners[0]["name"] if winners else "-")
        return {"season": sid, "winners": winners}
