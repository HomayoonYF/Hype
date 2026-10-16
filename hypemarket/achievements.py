"""Achievements: permanent badges with small coin rewards, unlocked automatically by real actions.

Each achievement lists the events that can unlock it (so only a few cheap checks run after each action)
and a test over the player's data. Bots never earn them. Rewards are minted coins, paid once.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Callable

from . import config as C
from .core import get_user, kv_int, mint, now
from .db import Database


@dataclass(frozen=True)
class A:
    id: str
    emoji: str
    tier: str            # bronze | silver | gold | legend
    events: tuple
    test: Callable       # (db, user_row, ctx) -> bool
    points: int = 0      # skill points (legend tier)


def _holdings(db, u) -> int:
    return db.q1("SELECT COUNT(*) c FROM holdings h JOIN stocks s ON s.id=h.stock_id WHERE h.user_id=? AND s.status='active'", (u["id"],))["c"]


def _investors(db, u) -> int:
    r = db.q1(
        "SELECT MAX(n) m FROM (SELECT COUNT(*) n FROM holdings h JOIN stocks s ON s.id=h.stock_id JOIN users x ON x.id=h.user_id "
        "WHERE s.owner_id=? AND h.user_id!=? AND x.bot=0 GROUP BY s.id)", (u["id"], u["id"]))
    return r["m"] or 0


def _owns(db, u, kind=None) -> bool:
    sql, args = "SELECT 1 FROM stocks WHERE owner_id=?", [u["id"]]
    if kind:
        sql += " AND kind=?"
        args.append(kind)
    return db.q1(sql, tuple(args)) is not None


def _trust_ok(db, u, ctx) -> bool:
    r = db.q1("SELECT COALESCE(SUM(value*weight),0) net, COUNT(*) n FROM trust WHERE target=?", (u["id"],))
    return r["net"] >= 10 and r["n"] >= 5


ALL_A = [
    A("first_trade", "🥉", "bronze", ("trade",), lambda db, u, c: u["trades"] >= 1),
    A("trades_100", "🥈", "silver", ("trade",), lambda db, u, c: u["trades"] >= 100),
    A("trades_1000", "🥇", "gold", ("trade",), lambda db, u, c: u["trades"] >= 1000),
    A("big_win", "📈", "silver", ("profit",), lambda db, u, c: c.get("pnl", 0) >= C.coins(500)),
    A("whale", "🐋", "gold", ("whale",), lambda db, u, c: True),
    A("diversified", "🧺", "silver", ("trade",), lambda db, u, c: _holdings(db, u) >= 5),
    A("diamond_hands", "💎", "gold", ("daily", "claim", "trade"),
      lambda db, u, c: db.q1("SELECT 1 FROM holdings h JOIN stocks s ON s.id=h.stock_id WHERE h.user_id=? AND s.status='active' AND h.last_buy<=?", (u["id"], now() - 7 * 86400)) is not None),
    A("founder", "🚀", "bronze", ("launch",), lambda db, u, c: _owns(db, u)),
    A("meme_lord", "🤪", "silver", ("launch",), lambda db, u, c: _owns(db, u, "meme")),
    A("investors_10", "👥", "silver", ("investor",), lambda db, u, c: _investors(db, u) >= 10),
    A("investors_50", "🌟", "gold", ("investor",), lambda db, u, c: _investors(db, u) >= 50),
    A("funded", "🎯", "gold", ("funded",), lambda db, u, c: True),
    A("passive_1k", "💸", "silver", ("claim",), lambda db, u, c: kv_int(db, f"pi:{u['id']}") >= C.coins(1000)),
    A("trusted", "🤝", "gold", ("trust",), _trust_ok),
    A("scam_hunter", "🕵️", "gold", ("report_ok",), lambda db, u, c: True),
    A("level_3", "⭐", "silver", ("level",), lambda db, u, c: u["max_level"] >= 3),
    A("level_5", "🌠", "gold", ("level",), lambda db, u, c: u["max_level"] >= 5),
    A("rebirth_1", "♻️", "gold", ("rebirth",), lambda db, u, c: u["rebirths"] >= 1),
    A("rebirth_5", "👑", "legend", ("rebirth",), lambda db, u, c: u["rebirths"] >= 5, points=1),
    A("streak_7", "🔥", "bronze", ("daily",), lambda db, u, c: u["streak"] >= 7),
    A("streak_30", "🌋", "gold", ("daily",), lambda db, u, c: u["streak"] >= 30),
    A("company", "🏢", "bronze", ("company",), lambda db, u, c: bool(u["company_id"])),
    A("quests_50", "📜", "silver", ("quest",), lambda db, u, c: db.q1("SELECT COUNT(*) c FROM quests WHERE user_id=? AND done=1", (u["id"],))["c"] >= 50),
    A("season_top3", "🏆", "legend", ("season",), lambda db, u, c: True, points=1),
    A("inviter_5", "📣", "silver", ("invite",), lambda db, u, c: db.q1("SELECT COUNT(*) c FROM users WHERE referrer=? AND activated=1", (u["id"],))["c"] >= 5),
    A("veteran", "🎖️", "silver", ("daily",), lambda db, u, c: now() - u["created_at"] >= 30 * 86400 and u["trades"] >= 10),
]
BY_ID = {a.id: a for a in ALL_A}
BY_EVENT: dict = {}
for _a in ALL_A:
    for _e in _a.events:
        BY_EVENT.setdefault(_e, []).append(_a)


def reward_of(a: A) -> int:
    return C.ACH_REWARD[a.tier]


def check(db: Database, uid: int, event: str, **ctx) -> list:
    """Unlock every achievement of this event whose test passes. Returns the newly unlocked ids."""
    if uid <= 0 or event not in BY_EVENT:
        return []
    u = get_user(db, uid)
    if not u or u["bot"]:
        return []
    got = []
    for a in BY_EVENT[event]:
        if db.q1("SELECT 1 FROM achievements WHERE user_id=? AND aid=?", (uid, a.id)):
            continue
        if a.test(db, u, ctx):
            db.x("INSERT INTO achievements(user_id,aid,ts) VALUES(?,?,?)", (uid, a.id, now()))
            r = reward_of(a)
            db.x("UPDATE users SET cash=cash+?, bonus_points=bonus_points+? WHERE id=?", (r, a.points, uid))
            mint(db, r)
            from .core import counter
            counter(db, "ach")
            db.ach_events.append((uid, a.id, r, a.points))
            got.append(a.id)
    return got


def unlocked(db: Database, uid: int) -> dict:
    return {r["aid"]: r["ts"] for r in db.qa("SELECT aid, ts FROM achievements WHERE user_id=?", (uid,))}


def drain_events(db: Database, actor: int | None) -> list:
    """Pop queued unlocks: the actor's are returned (aid, reward, points) for inline display, others become DMs."""
    from .core import notify
    events, db.ach_events = db.ach_events, []
    mine = []
    for uid, aid, reward, points in events:
        if actor is not None and uid == actor:
            mine.append((aid, reward, points))
        else:
            notify(db, uid, "ach_done", aid=aid, amount=reward, points=points)
    return mine
