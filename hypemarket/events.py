"""Random market events and periodic upkeep."""
from __future__ import annotations

import json
import random

from . import config as C
from .core import (
    add_dividend, add_news, clear_event, kv_get, kv_int, kv_set, mint, now, price, set_event, stock_row,
)
from .db import Database


def _eligible(db: Database, min_supply: int = 5) -> list:
    return db.qa("SELECT * FROM stocks WHERE status='active' AND kind IN ('personal','meme','project','bluechip') AND supply>=? AND event_until=0", (min_supply,))


def _global(db: Database, kind: str, bps: int, minutes: int, kinds: tuple | None = None) -> int:
    until = now() + minutes * 60
    n = 0
    for s in db.qa("SELECT * FROM stocks WHERE status='active' AND event_until=0 AND supply>0"):
        if kinds and s["kind"] not in kinds:
            continue
        set_event(db, s["id"], bps, until, kind)
        n += 1
    kv_set(db, "global_event", json.dumps({"kind": kind, "bps": bps, "until": until}))
    return n


def fire_event(db: Database, kind: str | None = None) -> str | None:
    """Trigger one random market event (or a specific one). Returns the event kind that fired."""
    with db.tx():
        kinds = ["pump", "pump", "crash", "boom", "special", "holiday", "mania"]
        kind = kind or random.choice(kinds)
        t = now()
        if kind == "pump":
            rows = _eligible(db)
            if not rows:
                return None
            s = random.choices(rows, weights=[1 + r["hype"] + r["holders"] for r in rows])[0]
            bps = C.BPS + random.randint(10, 30) * 100
            set_event(db, s["id"], bps, t + 3600, "pump")
            add_news(db, "pump", s["id"], ticker=s["ticker"], pct=(bps - C.BPS) // 100)
        elif kind == "crash":
            minutes = random.randint(30, 45)
            pct = random.randint(8, 20)
            if not _global(db, "crash", C.BPS - pct * 100, minutes):
                return None
            add_news(db, "crash", None, pct=pct, minutes=minutes)
        elif kind == "boom":
            minutes = random.randint(30, 45)
            pct = random.randint(5, 12)
            if not _global(db, "boom", C.BPS + pct * 100, minutes):
                return None
            add_news(db, "boom", None, pct=pct, minutes=minutes)
        elif kind == "special":
            rows = [r for r in db.qa("SELECT * FROM stocks WHERE status='active' AND supply>=5 AND kind!='index'")]
            if not rows:
                return None
            s = random.choice(rows)
            amount = s["reserve"] * 2 // 100 + C.coins(20)
            if not add_dividend(db, s["id"], amount):
                return None
            mint(db, amount)
            add_news(db, "special", s["id"], ticker=s["ticker"])
        elif kind == "holiday":
            kv_set(db, "fee_free_until", t + 3600)
            add_news(db, "holiday", None)
        elif kind == "mania":
            until = t + 45 * 60
            pct = random.randint(20, 40)
            n = 0
            for s in db.qa("SELECT * FROM stocks WHERE status='active' AND kind='meme' AND supply>0 AND event_until=0"):
                set_event(db, s["id"], C.BPS + pct * 100, until, "mania")
                n += 1
            if not n:
                return None
            kv_set(db, "global_event", json.dumps({"kind": "mania", "bps": C.BPS + pct * 100, "until": until}))
            add_news(db, "mania", None, pct=pct)
        else:
            return None
        return kind


def tick(db: Database, random_events: bool = True) -> dict:
    """Run every 5 minutes: expire events, pop bubbles, maybe fire a new event, decay hype."""
    out = {"expired": 0, "event": None, "corrections": 0}
    t = now()
    with db.tx():
        for s in db.qa("SELECT id FROM stocks WHERE event_until>0 AND event_until<=?", (t,)):
            clear_event(db, s["id"])
            out["expired"] += 1
        # bubble corrections
        for r in db.qa("SELECT key, value FROM kv WHERE key LIKE 'corr:%'"):
            if int(r["value"]) <= t:
                sid = int(r["key"].split(":")[1])
                db.x("DELETE FROM kv WHERE key=?", (r["key"],))
                s = stock_row(db, sid)
                if s and s["status"] == "active" and s["event_until"] == 0 and s["supply"] > 0:
                    pct = random.randint(15, 30)
                    set_event(db, sid, C.BPS - pct * 100, t + 40 * 60, "correction")
                    add_news(db, "correction", sid, ticker=s["ticker"], pct=pct)
                    out["corrections"] += 1
        ge = kv_get(db, "global_event")
        if ge and json.loads(ge)["until"] <= t:
            db.x("DELETE FROM kv WHERE key='global_event'")
        db.x("UPDATE stocks SET hype=hype*0.93 WHERE hype>0")
        db.x("UPDATE stocks SET hype=0 WHERE hype<0.05")
        if random_events and t >= kv_int(db, "next_event"):
            kv_set(db, "next_event", t + random.randint(30, 75) * 60)
            out["event"] = fire_event(db)
    return out


def prune(db: Database) -> None:
    cut = now() - C.HISTORY_KEEP_DAYS * 86400
    db.x("DELETE FROM history WHERE ts<?", (cut,))
    db.x("DELETE FROM news WHERE ts<?", (cut,))
    db.x("DELETE FROM activity WHERE day<?", (now() // 86400 - 3,))
    db.x("DELETE FROM kv WHERE key LIKE 'bottop:%' AND CAST(replace(key, rtrim(key, '0123456789'), '') AS INTEGER) < ?", (now() // 86400 - 2,))
    # reputation-cap counters are keyed rep:<owner>:<day>; drop the old days (day = digits after the last colon)
    db.x("DELETE FROM kv WHERE key LIKE 'rep:%' AND CAST(replace(key, rtrim(key, '0123456789'), '') AS INTEGER) < ?", (now() // 86400 - 2,))


def active_events(db: Database) -> dict:
    t = now()
    rows = db.qa("SELECT ticker, event_bps, event_until, event_kind FROM stocks WHERE status='active' AND event_until>? ORDER BY event_until", (t,))
    ge = kv_get(db, "global_event")
    ge = json.loads(ge) if ge else None
    if ge and ge["until"] <= t:
        ge = None
    singles = [r for r in rows if not ge or r["event_kind"] != ge["kind"]]
    return {"global": ge, "stocks": singles[:8], "fee_free": max(0, kv_int(db, "fee_free_until") - t)}
