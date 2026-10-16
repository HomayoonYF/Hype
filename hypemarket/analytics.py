"""Admin analytics: who plays, who comes back, where new players drop off, and the health of the economy.

Cheap by design: one tiny `seen(day, user)` row per player per day, plus per-day counters kept in `kv`.
Only aggregated numbers are shown; nothing here is visible to players.
"""
from __future__ import annotations

from . import config as C
from . import core, quests
from .core import counter_get, kv_get, kv_int, kv_set, now, price
from .db import Database


def touch(db: Database, uid: int) -> None:
    if uid > 0:
        db.x("INSERT OR IGNORE INTO seen(day,user_id) VALUES(?,?)", (now() // 86400, uid))


def snapshot(db: Database) -> None:
    """Remember the money supply at the start of each day (so daily minted/burned can be computed)."""
    day = now() // 86400
    if kv_get(db, f"mint_at:{day}") is None:
        kv_set(db, f"mint_at:{day}", kv_int(db, "minted"))
        kv_set(db, f"burn_at:{day}", kv_int(db, "burned"))


def _delta(db: Database, name: str, total_key: str, day: int) -> int:
    start = kv_get(db, f"{name}_at:{day}")
    if start is None:
        return 0
    end = kv_get(db, f"{name}_at:{day + 1}")
    return (int(end) if end is not None else kv_int(db, total_key)) - int(start)


def _retention(db: Database, offset: int, window: int = 7):
    today = now() // 86400
    lo, hi = today - offset - window + 1, today - offset
    n = db.q1("SELECT COUNT(*) c FROM users WHERE bot=0 AND created_at/86400 BETWEEN ? AND ?", (lo, hi))["c"]
    if not n:
        return None, 0
    kept = db.q1(
        "SELECT COUNT(*) c FROM users u JOIN seen s ON s.user_id=u.id AND s.day=u.created_at/86400+? WHERE u.bot=0 AND u.created_at/86400 BETWEEN ? AND ?",
        (offset, lo, hi))["c"]
    return kept * 100.0 / n, n


def dashboard(db: Database) -> dict:
    snapshot(db)
    today = now() // 86400
    q1 = lambda sql, *a: db.q1(sql, a)[0] or 0
    total = q1("SELECT COUNT(*) FROM users WHERE bot=0")
    d1, d1n = _retention(db, 1)
    d7, d7n = _retention(db, 7)
    funnel = []
    if total:
        done = {r["qid"]: r["d"] for r in db.qa("SELECT qid, SUM(done) d FROM quests WHERE period=0 AND qid LIKE 's\\_%' ESCAPE '\\' AND user_id>0 GROUP BY qid")}
        funnel = [(q.id, 100.0 * (done.get(q.id) or 0) / total) for q in quests.STARTER]
    active = db.qa("SELECT * FROM stocks WHERE status='active'")
    flagged = db.q1("SELECT COUNT(*) c FROM (SELECT stock_id FROM reports WHERE status='open' GROUP BY stock_id HAVING SUM(weight)>=?)", (C.REPORT_FLAG_WEIGHT,))["c"]
    ctr = lambda name, day=today: counter_get(db, day, name)
    minted, burned = kv_int(db, "minted"), kv_int(db, "burned")
    return {
        "players": {"total": total, "new_today": q1("SELECT COUNT(*) FROM users WHERE bot=0 AND created_at>=?", today * 86400),
                    "new_7d": q1("SELECT COUNT(*) FROM users WHERE bot=0 AND created_at>=?", (today - 6) * 86400),
                    "dau": q1("SELECT COUNT(*) FROM seen WHERE day=?", today), "dau_yesterday": q1("SELECT COUNT(*) FROM seen WHERE day=?", today - 1),
                    "wau": q1("SELECT COUNT(DISTINCT user_id) FROM seen WHERE day>=?", today - 6), "mau": q1("SELECT COUNT(DISTINCT user_id) FROM seen WHERE day>=?", today - 29),
                    "banned": q1("SELECT COUNT(*) FROM users WHERE banned=1")},
        "retention": {"d1": d1, "d1_n": d1n, "d7": d7, "d7_n": d7n},
        "funnel": funnel,
        "economy": {"minted": minted, "burned": burned, "supply": minted - burned,
                    "minted_today": _delta(db, "mint", "minted", today), "burned_today": _delta(db, "burn", "burned", today),
                    "minted_yesterday": _delta(db, "mint", "minted", today - 1), "burned_yesterday": _delta(db, "burn", "burned", today - 1)},
        "market": {"stocks": len(active), "mcap": sum(price(s) * s["supply"] for s in active), "trades": ctr("trades"), "bot_trades": ctr("bot_trades"),
                   "volume": ctr("volume"), "launches": ctr("launches"), "rugs": ctr("rugs"), "whales": ctr("whales"),
                   "trades_yesterday": ctr("trades", today - 1), "volume_yesterday": ctr("volume", today - 1)},
        "engagement": {"quests": ctr("quests"), "achievements": ctr("ach"), "votes": ctr("votes"), "reports": ctr("reports"),
                       "quests_yesterday": ctr("quests", today - 1), "achievements_yesterday": ctr("ach", today - 1)},
        "moderation": {"open_reports": q1("SELECT COUNT(DISTINCT stock_id) FROM reports WHERE status='open'"), "flagged": flagged,
                       "actions_7d": q1("SELECT COUNT(*) FROM modlog WHERE ts>=?", now() - 7 * 86400)},
        "groups": {"active": q1("SELECT COUNT(*) FROM groups WHERE active=1"), "chatting_today": q1("SELECT COUNT(DISTINCT group_id) FROM group_members WHERE last_seen>=?", today * 86400),
                   "top": [dict(r) for r in db.qa("SELECT title, score FROM groups WHERE active=1 ORDER BY score DESC LIMIT 3")]},
    }


def prune(db: Database) -> None:
    day = now() // 86400
    db.x("DELETE FROM seen WHERE day<?", (day - 120,))
    for prefix in ("ctr", "mint_at", "burn_at"):
        for r in db.qa("SELECT key FROM kv WHERE key LIKE ?", (prefix + ":%",)):
            try:
                d = int(r["key"].split(":")[1])
            except (IndexError, ValueError):
                continue
            if d < day - 90:
                db.x("DELETE FROM kv WHERE key=?", (r["key"],))
