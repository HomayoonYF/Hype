"""Groups (each one gets its own index stock), chat activity, leaderboards and companies."""
from __future__ import annotations

from . import config as C
from .core import (
    GameError, TICKER_RE, RESERVED, add_dividend, add_news, burn, check_level, check_text, clean, counter, get_user, kv_add, mint, need_user,
    norm_ticker, notify, now, record, stock_row,
)
from .db import Database
from . import engine as E
from . import achievements, quests, trading


# --- groups ---------------------------------------------------------------------
def get_group(db: Database, chat_id: int):
    return db.q1("SELECT * FROM groups WHERE id=?", (chat_id,))


def ensure_group(db: Database, chat_id: int, title: str, added_by: int | None = None, lang: str = "fa"):
    title = clean(title, 40) or "Group"
    with db.tx():
        g = get_group(db, chat_id)
        if g:
            if g["title"] != title or not g["active"]:
                db.x("UPDATE groups SET title=?, active=1 WHERE id=?", (title, chat_id))
                if g["index_stock"]:
                    db.x("UPDATE stocks SET name=? WHERE id=?", (title, g["index_stock"]))
                g = get_group(db, chat_id)
            return g
        db.x("INSERT INTO groups(id,title,lang,msg_cap,added_by,created_at) VALUES(?,?,?,?,?,?)", (chat_id, title, lang, C.DEFAULT_GROUP_MSG_CAP, added_by, now()))
        seq = kv_add(db, "idx_seq", 1)
        cur = db.x(
            "INSERT INTO stocks(ticker,name,kind,group_id,p0,k,created_at) VALUES(?,?,?,?,?,?,?)",
            (f"IDX{seq}", title, "index", chat_id, C.coins(1), 1, now()),
        )
        db.x("UPDATE groups SET index_stock=? WHERE id=?", (cur.lastrowid, chat_id))
        record(db, cur.lastrowid)
        return get_group(db, chat_id)


def deactivate_group(db: Database, chat_id: int) -> None:
    db.x("UPDATE groups SET active=0, board_msg=NULL WHERE id=?", (chat_id,))


def set_group_lang(db: Database, chat_id: int, lang: str) -> None:
    if lang not in ("fa", "en"):
        raise GameError("bad_usage")
    db.x("UPDATE groups SET lang=? WHERE id=?", (lang, chat_id))


def set_news_mode(db: Database, chat_id: int, mode: str) -> None:
    if mode not in ("off", "quiet", "live"):
        raise GameError("bad_usage")
    last = db.q1("SELECT COALESCE(MAX(id),0) m FROM news")["m"]
    db.x("UPDATE groups SET news_mode=?, board_msg=NULL, board_hash='', board_fail=0, last_news_id=? WHERE id=?", (mode, last, chat_id))


def set_msg_cap(db: Database, chat_id: int, cap: int) -> None:
    if not 0 <= cap <= C.DAILY_ACTIVITY_CAP:
        raise GameError("bad_cap", max=C.DAILY_ACTIVITY_CAP)
    db.x("UPDATE groups SET msg_cap=? WHERE id=?", (cap, chat_id))


def group_add_bonus(db: Database, chat_id: int, adder: int, member_count: int) -> int:
    """One-time reward for adding the bot to a real (not tiny) group."""
    with db.tx():
        g = get_group(db, chat_id)
        if not g or g["bonus_paid"] or member_count < C.GROUP_ADD_MIN_MEMBERS or not get_user(db, adder):
            return 0
        db.x("UPDATE groups SET bonus_paid=1, added_by=? WHERE id=?", (adder, chat_id))
        db.x("UPDATE users SET cash=cash+? WHERE id=?", (C.GROUP_ADD_BONUS, adder))
        mint(db, C.GROUP_ADD_BONUS)
        return C.GROUP_ADD_BONUS


# --- activity -------------------------------------------------------------------
def flush_activity(db: Database, counts: dict, seen: set) -> int:
    """Turn buffered chat messages into dividends. counts: {(uid, chat): n}; seen: {(chat, uid)}."""
    t = now()
    day = t // 86400
    total = 0
    with db.tx():
        for chat, uid in seen:
            db.x("INSERT INTO group_members(group_id,user_id,last_seen) VALUES(?,?,?) ON CONFLICT(group_id,user_id) DO UPDATE SET last_seen=excluded.last_seen", (chat, uid, t))
        for (uid, chat), c in counts.items():
            if not get_user(db, uid):
                continue
            g = get_group(db, chat)
            cap = g["msg_cap"] if g else C.DEFAULT_GROUP_MSG_CAP
            used_chat = db.q1("SELECT COALESCE(SUM(count),0) n FROM activity WHERE user_id=? AND chat_id=? AND day=?", (uid, chat, day))["n"]
            used_all = db.q1("SELECT COALESCE(SUM(count),0) n FROM activity WHERE user_id=? AND day=?", (uid, day))["n"]
            allowed = min(c, cap - used_chat, C.DAILY_ACTIVITY_CAP - used_all)
            if allowed <= 0:
                continue
            db.x("INSERT INTO activity(user_id,chat_id,day,count) VALUES(?,?,?,?) ON CONFLICT(user_id,chat_id,day) DO UPDATE SET count=count+excluded.count", (uid, chat, day, allowed))
            quests.track(db, uid, "chat", allowed)
            counter(db, "chat", allowed)
            amount = allowed * C.ACT_UNITS
            targets = []
            if g and g["index_stock"]:
                targets.append(g["index_stock"])
            targets += [s["id"] for s in trading.my_stocks(db, uid)[:3]]
            for sid in targets:
                db.x("UPDATE stocks SET hype=hype+? WHERE id=?", (allowed, sid))
                if add_dividend(db, sid, amount):
                    mint(db, amount)
                    total += amount
    return total


# --- leaderboards -----------------------------------------------------------------
def global_top(db: Database, limit: int = 10) -> list:
    return db.qa("SELECT id,name,badge,nw,max_level,rebirths FROM users WHERE banned=0 AND bot=0 AND trades>0 ORDER BY nw DESC LIMIT ?", (limit,))


def group_leaderboard(db: Database, chat_id: int, limit: int = 10) -> list:
    return db.qa(
        "SELECT u.id,u.name,u.badge,u.nw,u.max_level FROM group_members m JOIN users u ON u.id=m.user_id "
        "WHERE m.group_id=? AND u.banned=0 ORDER BY u.nw DESC LIMIT ?", (chat_id, limit),
    )


def refresh_group_scores(db: Database) -> None:
    with db.tx():
        for g in db.qa("SELECT id FROM groups WHERE active=1"):
            top = group_leaderboard(db, g["id"], 20)
            db.x("UPDATE groups SET score=? WHERE id=?", (sum(r["nw"] for r in top), g["id"]))


def group_ranking(db: Database, limit: int = 10) -> list:
    return db.qa("SELECT id,title,score FROM groups WHERE active=1 ORDER BY score DESC LIMIT ?", (limit,))


def group_info(db: Database, chat_id: int) -> dict:
    g = get_group(db, chat_id)
    if not g:
        raise GameError("no_group")
    rank = db.q1("SELECT COUNT(*)+1 r FROM groups WHERE active=1 AND score>?", (g["score"],))["r"]
    members = db.q1("SELECT COUNT(*) c FROM group_members WHERE group_id=?", (chat_id,))["c"]
    return {"group": g, "rank": rank, "members": members, "top": group_leaderboard(db, chat_id, 3), "index": stock_row(db, g["index_stock"]) if g["index_stock"] else None}


# --- companies --------------------------------------------------------------------
def create_company(db: Database, uid: int, name: str, ticker: str):
    name, ticker = clean(name, 30), norm_ticker(ticker)
    check_text(name, 2)
    if not TICKER_RE.match(ticker) or ticker.startswith("IDX") or ticker in RESERVED:
        raise GameError("bad_ticker")
    with db.tx():
        need_user(db, uid)
        check_level(db, uid)
        u = need_user(db, uid)
        if u["company_id"]:
            raise GameError("in_company")
        if u["max_level"] < C.COMPANY_MIN_LEVEL:
            raise GameError("need_level", level=C.COMPANY_MIN_LEVEL, have=u["max_level"], nw=E.level_threshold(C.COMPANY_MIN_LEVEL))
        if db.q1("SELECT 1 FROM companies WHERE name=?", (name,)):
            raise GameError("company_taken")
        if db.q1("SELECT 1 FROM stocks WHERE ticker=?", (ticker,)):
            raise GameError("ticker_taken", ticker=ticker)
        if u["cash"] < C.COMPANY_COST:
            raise GameError("funds", need=C.COMPANY_COST, have=u["cash"])
        db.x("UPDATE users SET cash=cash-? WHERE id=?", (C.COMPANY_COST, uid))
        burn(db, C.COMPANY_COST)
        cid = db.x("INSERT INTO companies(name,owner_id,created_at) VALUES(?,?,?)", (name, uid, now())).lastrowid
        sid = db.x(
            "INSERT INTO stocks(ticker,name,kind,company_id,p0,k,created_at) VALUES(?,?,?,?,?,?,?)",
            (ticker, name, "company", cid, C.coins(2), 1, now()),
        ).lastrowid
        db.x("UPDATE companies SET stock_id=? WHERE id=?", (sid, cid))
        db.x("UPDATE users SET company_id=?, company_joined=? WHERE id=?", (cid, now(), uid))
        record(db, sid)
        achievements.check(db, uid, "company")
        add_news(db, "company", sid, ticker=ticker, name=name, owner=u["name"])
        return db.q1("SELECT * FROM companies WHERE id=?", (cid,))


def company_of(db: Database, uid: int):
    u = need_user(db, uid)
    if not u["company_id"]:
        raise GameError("no_company")
    return db.q1("SELECT * FROM companies WHERE id=?", (u["company_id"],))


def company_info(db: Database, uid: int) -> dict:
    c = company_of(db, uid)
    members = db.qa("SELECT id,name,badge,nw FROM users WHERE company_id=? ORDER BY company_joined", (c["id"],))
    return {"company": c, "members": members, "stock": stock_row(db, c["stock_id"]) if c["stock_id"] else None}


def join_company(db: Database, uid: int, name: str) -> dict:
    name = clean(name, 30)
    with db.tx():
        u = need_user(db, uid)
        if u["company_id"]:
            raise GameError("in_company")
        c = db.q1("SELECT * FROM companies WHERE name=? COLLATE NOCASE", (name,))
        if not c:
            raise GameError("no_such_company")
        if db.q1("SELECT COUNT(*) c FROM users WHERE company_id=?", (c["id"],))["c"] >= C.COMPANY_MAX_MEMBERS:
            raise GameError("company_full", n=C.COMPANY_MAX_MEMBERS)
        db.x("UPDATE users SET company_id=?, company_joined=? WHERE id=?", (c["id"], now(), uid))
        notify(db, c["owner_id"], "company_join", name=u["name"], company=c["name"])
        achievements.check(db, uid, "company")
        return c


def _leave(db: Database, uid: int) -> dict | None:
    u = need_user(db, uid)
    if not u["company_id"]:
        return None
    c = db.q1("SELECT * FROM companies WHERE id=?", (u["company_id"],))
    db.x("UPDATE users SET company_id=NULL, company_joined=0 WHERE id=?", (uid,))
    if c["owner_id"] == uid:
        nxt = db.q1("SELECT id FROM users WHERE company_id=? ORDER BY company_joined LIMIT 1", (c["id"],))
        if nxt:
            db.x("UPDATE companies SET owner_id=? WHERE id=?", (nxt["id"], c["id"]))
            notify(db, nxt["id"], "company_owner", company=c["name"])
        else:
            if c["stock_id"]:
                trading.delist(db, c["stock_id"])
            if c["treasury"]:
                db.x("UPDATE users SET cash=cash+? WHERE id=?", (c["treasury"], uid))
            db.x("DELETE FROM companies WHERE id=?", (c["id"],))
    return c


def leave_company(db: Database, uid: int) -> dict:
    with db.tx():
        c = _leave(db, uid)
        if not c:
            raise GameError("no_company")
        return c


def leave_for_rebirth(db: Database, uid: int) -> None:
    _leave(db, uid)


def payout_company(db: Database, uid: int) -> dict:
    with db.tx():
        c = company_of(db, uid)
        if c["owner_id"] != uid:
            raise GameError("not_company_owner")
        members = db.qa("SELECT id FROM users WHERE company_id=?", (c["id"],))
        share = c["treasury"] // max(1, len(members))
        if share <= 0:
            raise GameError("nothing")
        for m in members:
            db.x("UPDATE users SET cash=cash+? WHERE id=?", (share, m["id"]))
            if m["id"] != uid:
                notify(db, m["id"], "company_payout", amount=share, company=c["name"])
        db.x("UPDATE companies SET treasury=treasury-? WHERE id=?", (share * len(members), c["id"]))
        return {"each": share, "members": len(members), "company": c}
