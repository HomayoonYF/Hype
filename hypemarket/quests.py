"""Quests: a starter checklist (the in-game guide), 3 daily and 3 weekly quests per player.

Quests complete automatically and pay out immediately (coins and sometimes a skill point).
Money targets and rewards grow slowly with the player's level, so they stay meaningful.
Only real actions count: trades must be worth at least MIN_TRADE, chat messages are the
already rate-limited "counted" ones, and automated market traders never take part.
"""
from __future__ import annotations

import random
from dataclasses import dataclass

from . import achievements, config as C
from .core import counter, mint, now
from .db import Database

MIN_TRADE = C.coins(20)


@dataclass(frozen=True)
class Q:
    id: str
    scope: str            # starter | daily | weekly
    metric: str
    target: int           # count, or units for money metrics
    reward: int           # units
    points: int = 0       # skill points
    money: bool = False   # target is money (scales with level)
    needs_stock: bool = False


SNAPSHOT = {"level", "streak", "holdings", "hold24", "company"}
MONEY_METRICS = {"buy_volume", "volume", "profit"}

STARTER = [
    Q("s_daily", "starter", "daily", 1, C.coins(150)),
    Q("s_buy", "starter", "buy", 1, C.coins(250)),
    Q("s_launch", "starter", "launch", 1, C.coins(400)),
    Q("s_investor", "starter", "investor", 1, C.coins(300)),
    Q("s_claim", "starter", "claim", 1, C.coins(200)),
    Q("s_level", "starter", "level", 1, C.coins(300), points=1),
]
DAILY = [
    Q("d_daily", "daily", "daily", 1, C.coins(60)),
    Q("d_trade", "daily", "trade_ok", 3, C.coins(90)),
    Q("d_buyvol", "daily", "buy_volume", C.coins(300), C.coins(100), money=True),
    Q("d_profit", "daily", "profit", C.coins(40), C.coins(110), money=True),
    Q("d_chat", "daily", "chat", 20, C.coins(80)),
    Q("d_claim", "daily", "claim", 1, C.coins(70)),
    Q("d_investor", "daily", "investor", 1, C.coins(120), needs_stock=True),
    Q("d_hold", "daily", "hold24", 1, C.coins(90)),
    Q("d_diverse", "daily", "holdings", 3, C.coins(100)),
]
WEEKLY = [
    Q("w_volume", "weekly", "volume", C.coins(3000), C.coins(500), money=True),
    Q("w_streak", "weekly", "streak", 5, C.coins(400)),
    Q("w_investors", "weekly", "investor", 5, C.coins(600), needs_stock=True),
    Q("w_profit", "weekly", "profit", C.coins(500), C.coins(700), points=1, money=True),
    Q("w_chat", "weekly", "chat", 150, C.coins(400)),
    Q("w_invite", "weekly", "invite", 1, C.coins(600)),
    Q("w_company", "weekly", "company", 1, C.coins(300)),
]
ALL = {q.id: q for q in STARTER + DAILY + WEEKLY}
BY_METRIC: dict = {}
for _q in ALL.values():
    BY_METRIC.setdefault(_q.metric, []).append(_q)


def day() -> int:
    return now() // 86400


def week() -> int:
    return (day() + 3) // 7        # weeks start on Monday (UTC)


def period_of(scope: str) -> int:
    return 0 if scope == "starter" else day() if scope == "daily" else week()


def seconds_left(scope: str) -> int:
    if scope == "daily":
        return 86400 - now() % 86400
    if scope == "weekly":
        return ((week() + 1) * 7 - 3) * 86400 - now()
    return 0


def _lvl(u) -> int:
    return min(int(u["max_level"]), 10)


def _scaled(q: Q, level: int) -> tuple[int, int]:
    """(target, reward) for a player of this level."""
    target = int(q.target * (1 + 0.5 * level)) if q.money else q.target
    reward = int(q.reward * (1 + 0.25 * level))
    return target, reward


def _owns_stock(db: Database, uid: int) -> bool:
    return db.q1("SELECT 1 FROM stocks WHERE owner_id=? AND status='active' AND kind IN ('personal','meme','project','bluechip')", (uid,)) is not None


def ensure(db: Database, uid: int) -> None:
    """Create this player's quest rows for the current day/week (idempotent)."""
    if uid < 0:
        return
    u = db.q1("SELECT * FROM users WHERE id=?", (uid,))
    if not u or u["bot"]:
        return
    lvl = _lvl(u)
    for q in STARTER:
        t, r = _scaled(q, 0)
        db.x("INSERT OR IGNORE INTO quests(user_id,qid,period,target,reward,points) VALUES(?,?,?,?,?,?)", (uid, q.id, 0, t, r, q.points))
    for scope, pool in (("daily", DAILY), ("weekly", WEEKLY)):
        p = period_of(scope)
        prefix = "d_" if scope == "daily" else "w_"
        if db.q1("SELECT 1 FROM quests WHERE user_id=? AND period=? AND qid LIKE ?", (uid, p, prefix + "%")):
            continue
        rng = random.Random(f"{uid}:{scope}:{p}")
        owns = _owns_stock(db, uid)
        pick = rng.sample([q for q in pool if owns or not q.needs_stock], 3)
        for q in pick:
            t, r = _scaled(q, lvl)
            db.x("INSERT OR IGNORE INTO quests(user_id,qid,period,target,reward,points) VALUES(?,?,?,?,?,?)", (uid, q.id, p, t, r, q.points))


def _complete(db: Database, row, uid: int) -> None:
    db.x("UPDATE quests SET progress=target, done=1 WHERE user_id=? AND qid=? AND period=?", (uid, row["qid"], row["period"]))
    db.x("UPDATE users SET cash=cash+?, bonus_points=bonus_points+? WHERE id=?", (row["reward"], row["points"], uid))
    mint(db, row["reward"])
    db.quest_events.append((uid, row["qid"], row["reward"], row["points"]))
    counter(db, "quests")
    achievements.check(db, uid, "quest")


def _set(db: Database, row, uid: int, progress: int) -> None:
    progress = min(row["target"], max(row["progress"], progress))
    if progress >= row["target"]:
        _complete(db, row, uid)
    elif progress != row["progress"]:
        db.x("UPDATE quests SET progress=? WHERE user_id=? AND qid=? AND period=?", (progress, uid, row["qid"], row["period"]))


def track(db: Database, uid: int, metric: str, amount: int = 1) -> None:
    """Add `amount` to every open quest of this player that counts `metric`."""
    if uid < 0 or amount <= 0 or metric not in BY_METRIC:
        return
    ensure(db, uid)
    for q in BY_METRIC[metric]:
        row = db.q1("SELECT * FROM quests WHERE user_id=? AND qid=? AND period=? AND done=0", (uid, q.id, period_of(q.scope)))
        if row:
            _set(db, row, uid, row["progress"] + amount)


def _snapshot_value(db: Database, u, metric: str) -> int:
    uid = u["id"]
    if metric == "level":
        return int(u["max_level"])
    if metric == "streak":
        return int(u["streak"]) if u["last_daily"] and C.daily_day(u["last_daily"]) >= C.daily_day(now()) - 1 else 0
    if metric == "holdings":
        return db.q1("SELECT COUNT(*) c FROM holdings h JOIN stocks s ON s.id=h.stock_id WHERE h.user_id=? AND s.status='active'", (uid,))["c"]
    if metric == "hold24":
        return db.q1("SELECT COUNT(*) c FROM holdings h JOIN stocks s ON s.id=h.stock_id WHERE h.user_id=? AND s.status='active' AND h.last_buy<=?", (uid, now() - 86400))["c"]
    if metric == "company":
        return 1 if u["company_id"] else 0
    return 0


def refresh(db: Database, uid: int) -> None:
    """Re-check state-based quests (level, streak, holdings...) and starter steps the player already did before quests existed."""
    if uid < 0:
        return
    u = db.q1("SELECT * FROM users WHERE id=?", (uid,))
    if not u or u["bot"]:
        return
    with db.tx():
        ensure(db, uid)
        for row in db.qa("SELECT * FROM quests WHERE user_id=? AND done=0", (uid,)):
            q = ALL.get(row["qid"])
            if not q or row["period"] != period_of(q.scope):
                continue
            if q.metric in SNAPSHOT:
                _set(db, row, uid, _snapshot_value(db, u, q.metric))
            elif q.scope == "starter":
                done_before = False
                if q.metric == "daily":
                    done_before = u["last_daily"] > 0
                elif q.metric == "buy":
                    done_before = u["trades"] > 0 or db.q1("SELECT 1 FROM holdings WHERE user_id=?", (uid,)) is not None
                elif q.metric == "launch":
                    done_before = db.q1("SELECT 1 FROM stocks WHERE owner_id=?", (uid,)) is not None
                elif q.metric == "investor":
                    done_before = db.q1(
                        "SELECT 1 FROM holdings h JOIN stocks s ON s.id=h.stock_id JOIN users x ON x.id=h.user_id "
                        "WHERE s.owner_id=? AND h.user_id!=? AND x.bot=0", (uid, uid)) is not None
                if done_before:
                    _set(db, row, uid, row["target"])


def listing(db: Database, uid: int) -> dict:
    """Everything the quest screen shows."""
    refresh(db, uid)
    out = {"starter": [], "daily": [], "weekly": []}
    for scope in out:
        for row in db.qa("SELECT * FROM quests WHERE user_id=? AND period=? ORDER BY rowid", (uid, period_of(scope))):
            q = ALL.get(row["qid"])
            if q and q.scope == scope:
                out[scope].append({"q": q, "row": row})
    out["starter"] = [x for q in STARTER for x in out["starter"] if x["q"].id == q.id]
    return out


def drain_events(db: Database, actor: int | None) -> list:
    """Pop queued completions. The actor's are returned as (qid, reward, points, target) to show inline;
    everyone else's become direct messages."""
    from .core import notify
    events, db.quest_events = db.quest_events, []
    mine = []
    for uid, qid, reward, points in events:
        row = db.q1("SELECT target FROM quests WHERE user_id=? AND qid=? ORDER BY period DESC LIMIT 1", (uid, qid))
        target = row["target"] if row else 0
        if actor is not None and uid == actor:
            mine.append((qid, reward, points, target))
        else:
            notify(db, uid, "quest_done", qid=qid, n=target, amount=reward, points=points)
    return mine
