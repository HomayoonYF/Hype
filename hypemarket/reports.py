"""Business reports (v0.211): what a branch took in, spent and holds, worked out from its OWN RECORDS (the till ledger, the fulfilment record, the stock log); nothing is estimated or projected.

INCOME: the `sale` rows (customers) and the `buyback` rows (surplus the game bought). COSTS: material (what the branch paid sellers through its ads: `ad_fills`), wages (`wage` rows, players and bots)
and upgrades (`upgrade` rows). PROFIT = income - costs. Money the owner puts in or takes out (`fund`, `withdraw`, the payout on closing) and the budget reserved in an open ad (not spent until a
seller fills it) are not income or cost; they are shown beside the report as capital. STOCK VALUE is what the game itself thinks everything in the warehouse and on display is worth (`stock.value_of`).
"""
from __future__ import annotations

from . import biz, rep, stock


def pnl(db, branch_id: int, since: int = 0, until: int | None = None) -> dict:
    until = 2 ** 62 if until is None else until
    led = {r["kind"]: r["s"] for r in db.qa("SELECT kind, SUM(delta) s FROM biz_ledger WHERE branch_id=? AND created_at>=? AND created_at<=? GROUP BY kind", (branch_id, since, until))}
    materials = db.q1("SELECT COALESCE(SUM(f.total_cents),0) s FROM ad_fills f JOIN material_ads a ON a.id=f.ad_id WHERE a.branch_id=? AND f.created_at>=? AND f.created_at<=?", (branch_id, since, until))["s"]
    sales = db.q1("SELECT COUNT(*) c FROM biz_ledger WHERE branch_id=? AND kind='sale' AND created_at>=? AND created_at<=?", (branch_id, since, until))["c"]
    revenue, buyback = led.get("sale", 0), led.get("buyback", 0)
    wages, upgrades_ = -led.get("wage", 0), -led.get("upgrade", 0)
    income, costs = revenue + buyback, materials + wages + upgrades_
    b = biz.get(db, branch_id)
    return {"revenue": revenue, "sales": sales, "buyback": buyback, "income": income, "materials": materials, "wages": wages, "upgrades": upgrades_, "costs": costs, "profit": income - costs,
            "funded": led.get("fund", 0), "withdrawn": -led.get("withdraw", 0), "till": b["till"] if b else 0, "reserved_wages": biz.reserved(db, b) if b and b["state"] == "open" else 0,
            "reserved_ads": db.q1("SELECT COALESCE(SUM(reserved),0) s FROM material_ads WHERE branch_id=? AND state='open'", (branch_id,))["s"],
            "stock_value": stock.stock_value(db, branch_id), "reputation": rep.score(db, branch_id)}


def best_sellers(db, branch_id: int, limit: int = 5) -> list:
    """[(item id, data, pieces sold, cents)] best first."""
    return [(r["item_id"], r["data"], r["n"], r["c"]) for r in db.qa(
        "SELECT item_id, data, SUM(qty) n, SUM(total_cents) c FROM store_orders WHERE branch_id=? AND state='done' GROUP BY item_id, data ORDER BY c DESC, item_id LIMIT ?", (branch_id, limit))]


def overview(db, uid: int) -> list:
    """The owner's overview of every open branch: [{branch, pnl, staff (active contracts), working order, open ads}]."""
    from . import market, staff, tailor
    out = []
    for b in biz.of_owner(db, uid, open_only=True):
        out.append({"branch": b, "pnl": pnl(db, b["id"]), "staff": staff.of_branch(db, b["id"], states=("active",)), "order": tailor.working(db, b["id"]), "ads": market.of_branch(db, b["id"], True)})
    return out
