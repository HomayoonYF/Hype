"""The daily salary DM: a short morning message with the player's wealth summary and a button that claims the daily bonus.

Politeness rules (so it never becomes spam):
* only players seen in the last 14 days, only one message per day, only inside a 6-hour window after SALARY_HOUR_UTC;
* paused after 3 salaries in a row that were not claimed (a claim, or any later claim of the daily bonus, resumes it);
* never retried for players who blocked the bot or never opened a private chat (they are un-blocked when they next write to the bot);
* every player can switch it off/on with the «حقوق» command or the button under the message.
"""
from __future__ import annotations

from . import config as C
from . import core, progress, quests
from .core import change_pct, holdings_rows, net_worth, now
from .db import Database


def get(db: Database, uid: int) -> dict:
    r = db.q1("SELECT * FROM salary WHERE user_id=?", (uid,))
    return dict(r) if r else {"user_id": uid, "enabled": 1, "last_sent_day": 0, "ignored": 0, "last_nw": 0, "blocked": 0}


def _upsert(db: Database, uid: int, **fields) -> None:
    cur = get(db, uid)
    cur.update(fields)
    db.x("INSERT INTO salary(user_id,enabled,last_sent_day,ignored,last_nw,blocked) VALUES(?,?,?,?,?,?) "
         "ON CONFLICT(user_id) DO UPDATE SET enabled=excluded.enabled, last_sent_day=excluded.last_sent_day, ignored=excluded.ignored, last_nw=excluded.last_nw, blocked=excluded.blocked",
         (uid, cur["enabled"], cur["last_sent_day"], cur["ignored"], cur["last_nw"], cur["blocked"]))


def set_enabled(db: Database, uid: int, on: bool) -> None:
    _upsert(db, uid, enabled=1 if on else 0, **({"ignored": 0, "blocked": 0} if on else {}))


def unblock(db: Database, uid: int) -> None:
    db.x("UPDATE salary SET blocked=0 WHERE user_id=? AND blocked=1", (uid,))


def mark_blocked(db: Database, uid: int, day: int) -> None:
    _upsert(db, uid, blocked=1, last_sent_day=day)


def mark_sent(db: Database, uid: int, day: int) -> None:
    cur, u = get(db, uid), core.need_user(db, uid)
    acted = cur["last_sent_day"] == 0 or u["last_daily"] >= cur["last_sent_day"] * 86400     # claimed since the previous salary DM?
    _upsert(db, uid, last_sent_day=day, ignored=0 if acted else cur["ignored"] + 1, last_nw=net_worth(db, uid))
    core.counter(db, "salary_sent")


def candidates(db: Database, day: int, limit: int = C.SALARY_BATCH) -> list:
    return db.qa(
        "SELECT u.* FROM users u LEFT JOIN salary s ON s.user_id=u.id WHERE u.bot=0 AND u.banned=0 AND u.id>0 "
        "AND COALESCE(s.enabled,1)=1 AND COALESCE(s.blocked,0)=0 AND COALESCE(s.last_sent_day,0)<? AND COALESCE(s.ignored,0)<? "
        "AND EXISTS (SELECT 1 FROM seen x WHERE x.user_id=u.id AND x.day>=?) ORDER BY u.id LIMIT ?",
        (day, C.SALARY_MAX_IGNORED, day - C.SALARY_SEEN_DAYS, limit))


def summary(db: Database, uid: int) -> dict:
    """Everything the salary message shows."""
    u = core.need_user(db, uid)
    nw = net_worth(db, uid)
    prev = get(db, uid)["last_nw"]
    best = None
    for r in holdings_rows(db, uid):
        chg = change_pct(db, r)
        if best is None or chg > best[1]:
            best = (r["ticker"], chg)
    with db.tx():
        quests.ensure(db, uid)
        qd = db.q1("SELECT COALESCE(SUM(done),0) d, COUNT(*) n FROM quests WHERE user_id=? AND period=? AND qid LIKE 'd\\_%' ESCAPE '\\'", (uid, quests.day()))
    return {
        "user": u, "nw": nw, "change": (nw - prev) * 100.0 / prev if prev > 0 else None,
        "rank": db.q1("SELECT COUNT(*)+1 r FROM users WHERE bot=0 AND banned=0 AND trades>0 AND nw>?", (u["nw"],))["r"] if u["trades"] > 0 else None,
        "pending": u["pending_div"] + u["pending_inc"], "best": best, "quests_done": qd["d"], "quests_total": qd["n"] or 3,
        "preview": progress.daily_preview(db, uid), "streak": u["streak"],
    }
