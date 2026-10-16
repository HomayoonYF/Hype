"""Scam reports, trust marks and the admin moderation actions behind them.

* Trust marks (👍/👎) are about PEOPLE (stock owners): one vote per voter, changeable, weighted by the voter's level.
  A label (✅ trusted / ⚠️ not trusted) needs at least TRUST_MIN_VOTERS different voters, so a single account can't brand anyone.
* Reports are about STOCKS (rug, fake, spam, other). At REPORT_FLAG_WEIGHT the stock shows a public 🚩 warning; at the alert
  levels the admins get a message with Delist / Ban owner / Dismiss buttons. Nothing is removed automatically, so brigading
  can't kill a stock, but buyers are warned and the admins see everything.
* Only players with >= 3 trades can report or vote, with daily limits; owners can't report themselves; bots can't take part.
"""
from __future__ import annotations

from . import achievements, config as C
from .core import (GameError, burn, clean, counter, get_user, kv_int, kv_set, need_stock, need_user, notify, now, stock_row)
from .db import Database
from . import trading

REASONS = ("rug", "fake", "spam", "other")


def weight(u) -> int:
    """Higher-level players are more credible: weight 1..3."""
    return min(3, 1 + int(u["max_level"]) // 2)


def _eligible(u) -> None:
    if u["bot"] or u["trades"] < C.REPORT_MIN_TRADES:
        raise GameError("trust_need_trades", n=C.REPORT_MIN_TRADES)


def can_act(db: Database, uid: int) -> None:
    """Raise early (before showing menus) if this player may not report or vote yet."""
    _eligible(need_user(db, uid))


def _limited(db: Database, key: str, limit: int, code: str) -> None:
    k = f"{key}:{now() // 86400}"
    if kv_int(db, k) >= limit:
        raise GameError(code, n=limit)
    kv_set(db, k, kv_int(db, k) + 1)


# --- trust marks -----------------------------------------------------------------------
def score(db: Database, target: int) -> dict:
    r = db.q1(
        "SELECT COALESCE(SUM(CASE WHEN value>0 THEN weight END),0) up, COALESCE(SUM(CASE WHEN value<0 THEN weight END),0) down, "
        "SUM(value>0) ups, SUM(value<0) downs, COUNT(*) voters FROM trust WHERE target=?", (target,))
    net = r["up"] - r["down"]
    voters = r["voters"] or 0
    label = "neutral"
    if voters >= C.TRUST_MIN_VOTERS:
        label = "trusted" if net >= C.TRUST_GOOD else "flagged" if net <= C.TRUST_BAD else "neutral"
    return {"up": r["ups"] or 0, "down": r["downs"] or 0, "net": net, "voters": voters, "label": label}


def vote(db: Database, voter: int, target: int, value: int) -> dict:
    """value +1 trust, -1 distrust. Voting the same way again removes your vote."""
    if value not in (1, -1):
        raise GameError("bad_usage")
    with db.tx():
        u = need_user(db, voter)
        _eligible(u)
        if target == voter:
            raise GameError("trust_self")
        t = get_user(db, target)
        if not t or t["bot"]:
            raise GameError("no_user")
        old = db.q1("SELECT value FROM trust WHERE target=? AND voter=?", (target, voter))
        if old and old["value"] == value:
            db.x("DELETE FROM trust WHERE target=? AND voter=?", (target, voter))
            now_value = 0
        else:
            _limited(db, f"votes:{voter}", C.VOTE_DAILY_LIMIT, "trust_limit")
            db.x("INSERT INTO trust(target,voter,value,weight,ts) VALUES(?,?,?,?,?) ON CONFLICT(target,voter) DO UPDATE SET value=excluded.value, weight=excluded.weight, ts=excluded.ts",
                 (target, voter, value, weight(u), now()))
            now_value = value
        counter(db, "votes")
        achievements.check(db, target, "trust")
        return {"target": t, "value": now_value, "score": score(db, target)}


def trust_board(db: Database, limit: int = 8) -> dict:
    rows = db.qa(
        "SELECT x.id, x.name, x.badge, SUM(t.value*t.weight) net, COUNT(*) voters FROM trust t JOIN users x ON x.id=t.target "
        "WHERE x.banned=0 AND x.bot=0 GROUP BY t.target HAVING voters>=?", (C.TRUST_MIN_VOTERS,))
    good = sorted([r for r in rows if r["net"] >= C.TRUST_GOOD], key=lambda r: -r["net"])[:limit]
    bad = sorted([r for r in rows if r["net"] <= C.TRUST_BAD], key=lambda r: r["net"])[:limit]
    return {"good": good, "bad": bad}


# --- reports ---------------------------------------------------------------------------
def open_weight(db: Database, sid: int) -> int:
    return db.q1("SELECT COALESCE(SUM(weight),0) w FROM reports WHERE stock_id=? AND status='open'", (sid,))["w"]


def flagged(db: Database, sid: int) -> bool:
    return open_weight(db, sid) >= C.REPORT_FLAG_WEIGHT


def report(db: Database, reporter: int, ticker: str, reason: str = "other", note: str = "") -> dict:
    reason = reason if reason in REASONS else "other"
    note = clean(note, 100)
    with db.tx():
        u = need_user(db, reporter)
        _eligible(u)
        s = need_stock(db, ticker)
        if s["kind"] not in C.OWNER_KINDS or not s["owner_id"]:
            raise GameError("report_not_allowed")
        if s["owner_id"] == reporter:
            raise GameError("report_own")
        old = db.q1("SELECT id FROM reports WHERE stock_id=? AND reporter=?", (s["id"], reporter))
        if old:        # reporting again just refreshes your report (no extra weight, no extra limit use)
            db.x("UPDATE reports SET reason=?, note=?, weight=?, ts=?, status='open' WHERE id=?", (reason, note, weight(u), now(), old["id"]))
        else:
            _limited(db, f"reports:{reporter}", C.REPORT_DAILY_LIMIT, "report_limit")
            db.x("INSERT INTO reports(stock_id,owner_id,reporter,reason,note,weight,ts) VALUES(?,?,?,?,?,?,?)", (s["id"], s["owner_id"], reporter, reason, note, weight(u), now()))
            counter(db, "reports")
        total = open_weight(db, s["id"])
        level = max([lv for lv in C.REPORT_ALERT_LEVELS if total >= lv] or [0])
        if level > kv_int(db, f"repalert:{s['id']}"):
            kv_set(db, f"repalert:{s['id']}", level)
            db.mod_events.append(("report", s["id"]))
        return {"stock": s, "total": total, "new": old is None, "flagged": total >= C.REPORT_FLAG_WEIGHT}


def summary(db: Database, sid: int) -> dict:
    s = stock_row(db, sid)
    rows = db.qa("SELECT reason, COUNT(*) n, SUM(weight) w FROM reports WHERE stock_id=? AND status='open' GROUP BY reason", (sid,))
    notes = [r["note"] for r in db.qa("SELECT note FROM reports WHERE stock_id=? AND status='open' AND note!='' ORDER BY ts DESC LIMIT 3", (sid,))]
    owner = get_user(db, s["owner_id"]) if s and s["owner_id"] else None
    return {"stock": s, "owner": owner, "weight": open_weight(db, sid), "reasons": {r["reason"]: r["n"] for r in rows}, "notes": notes,
            "trust": score(db, s["owner_id"]) if owner else None, "reporters": db.q1("SELECT COUNT(*) c FROM reports WHERE stock_id=? AND status='open'", (sid,))["c"]}


# --- admin actions ---------------------------------------------------------------------
def _log(db: Database, admin: int, action: str, target: str, note: str = "") -> None:
    db.x("INSERT INTO modlog(ts,admin,action,target,note) VALUES(?,?,?,?,?)", (now(), admin, action, target, note))
    counter(db, "modact")


def _close_reports(db: Database, sid: int, status: str) -> list:
    reporters = [r["reporter"] for r in db.qa("SELECT reporter FROM reports WHERE stock_id=? AND status='open'", (sid,))]
    db.x("UPDATE reports SET status=? WHERE stock_id=? AND status='open'", (status, sid))
    kv_set(db, f"repalert:{sid}", 0)
    return reporters


def admin_dismiss(db: Database, admin: int, sid: int) -> None:
    with db.tx():
        s = stock_row(db, sid)
        _close_reports(db, sid, "dismissed")
        _log(db, admin, "dismiss", s["ticker"] if s else str(sid))


def _thank_reporters(db: Database, reporters: list, ticker: str) -> None:
    for uid in reporters[:50]:
        notify(db, uid, "report_actioned", ticker=ticker)
        achievements.check(db, uid, "report_ok")


def admin_delist(db: Database, admin: int, sid: int, scam: bool = True) -> dict:
    with db.tx():
        s = stock_row(db, sid)
        if not s or s["status"] != "active":
            raise GameError("no_stock", ticker=str(sid))
        if s["kind"] not in C.OWNER_KINDS:
            raise GameError("bad_kind")
        reporters = _close_reports(db, sid, "actioned")
        trading.delist(db, sid)
        if scam and s["owner_id"]:
            db.x("UPDATE users SET rep=MAX(0, rep-?) WHERE id=?", (C.RUG_REP_PENALTY, s["owner_id"]))
        _log(db, admin, "delist", s["ticker"])
        _thank_reporters(db, reporters, s["ticker"])
        return {"stock": s}


def admin_ban(db: Database, admin: int, uid: int) -> dict:
    with db.tx():
        u = get_user(db, uid)
        if not u or u["bot"]:
            raise GameError("no_user")
        db.x("UPDATE users SET banned=1 WHERE id=?", (uid,))
        delisted = []
        for s in trading.my_stocks(db, uid):
            reporters = _close_reports(db, s["id"], "actioned")
            trading.delist(db, s["id"])
            delisted.append(s["ticker"])
            _thank_reporters(db, reporters, s["ticker"])
        _log(db, admin, "ban", str(uid), ",".join(delisted))
        return {"user": u, "delisted": delisted}


def admin_unban(db: Database, admin: int, uid: int) -> None:
    with db.tx():
        if not get_user(db, uid):
            raise GameError("no_user")
        db.x("UPDATE users SET banned=0 WHERE id=?", (uid,))
        _log(db, admin, "unban", str(uid))
