"""Cosmetics and small perks sold for Telegram Stars (currency "XTR").

Nothing here sells coins: Stars only buy badges, a season pass, a stock slot or
a spotlight, so the game economy cannot be bought.
"""
from __future__ import annotations

from . import config as C
from .core import GameError, need_user, now
from .db import Database
from . import progress, trading


def item_check(db: Database, uid: int, item: str) -> dict:
    """Validate that `uid` may buy `item` right now (called before sending the invoice)."""
    spec = C.SHOP_ITEMS.get(item)
    if not spec:
        raise GameError("no_item")
    u = need_user(db, uid)
    if spec["kind"] == "pass" and u["pass_season"] == progress.season_id(db):
        raise GameError("already_have")
    if spec["kind"] == "badge" and u["badge"] == spec["value"]:
        raise GameError("already_have")
    if spec["kind"] == "spotlight" and not trading.my_stocks(db, uid):
        raise GameError("no_own_stock")
    if spec["kind"] == "slot" and u["extra_slots"] >= 5:
        raise GameError("already_have")
    return spec


def grant(db: Database, uid: int, item: str, charge_id: str, stars: int) -> bool:
    """Apply a paid item. Idempotent on `charge_id`; returns False if it was already granted."""
    spec = C.SHOP_ITEMS.get(item)
    if not spec:
        raise GameError("no_item")
    with db.tx():
        if db.q1("SELECT 1 FROM purchases WHERE charge_id=?", (charge_id,)):
            return False
        need_user(db, uid)
        db.x("INSERT INTO purchases(charge_id,user_id,item,stars,ts) VALUES(?,?,?,?,?)", (charge_id, uid, item, stars, now()))
        kind = spec["kind"]
        if kind == "badge":
            db.x("UPDATE users SET badge=? WHERE id=?", (spec["value"], uid))
        elif kind == "pass":
            db.x("UPDATE users SET pass_season=?, badge=CASE WHEN badge='' THEN '⭐' ELSE badge END, spent_points=spent_points-1 WHERE id=?", (progress.season_id(db), uid))
        elif kind == "slot":
            db.x("UPDATE users SET extra_slots=extra_slots+1 WHERE id=?", (uid,))
        elif kind == "spotlight":
            s = trading.my_stock(db, uid)
            db.x("UPDATE stocks SET featured_until=? WHERE id=?", (now() + 6 * 3600, s["id"]))
        return True
