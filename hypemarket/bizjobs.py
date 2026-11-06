"""The business world's periodic work (v0.211): what must keep happening while nobody is pressing a button.

    ads        an ad whose time has passed is closed and its unused budget goes back to the branch's till (market.sweep)
    quotes     a quote or a waiting order whose time is up is voided (store.expire)
    offers     a job offer nobody answered is closed, so its slot and its role are free again (staff.expire_offers)
    orders     the orders a hired production BOT works are finished when due (tailor.collect_due); a human's order waits for the human, who collects it
    payroll    every few hours the wages that have accrued are paid from each branch's till as far as it holds them (staff.settle; safe to repeat, and an employee can also ask for it at any time)

Every step is idempotent and works from the server clock and the saved state, so a restart, a slow loop or two loops at once change nothing but the timing; any screen runs the same steps for what it shows.
"""
from __future__ import annotations

import logging

from . import core, market, staff, store, tailor

log = logging.getLogger("hypemarket.bizjobs")

PAYROLL_EVERY = 6 * 3600                       # wages are paid in a run every few hours: not one tiny payment per tick
PAYROLL_KEY = "biz.payroll_at"
ENDED_GRACE = 7 * 86400                        # a contract that ended is still settled by the run for this long (the employee can always ask for it by hand)


def payroll_due(db, now: int) -> bool:
    try:
        last = int(core.kv_get(db, PAYROLL_KEY, 0) or 0)
    except (TypeError, ValueError):
        last = 0
    return now - last >= PAYROLL_EVERY


def run_payroll(db, now: int) -> dict:
    """Pay what every branch owes its staff from its till; returns {"branches", "paid", "left"} (cents)."""
    out = {"branches": 0, "paid": 0, "left": 0}
    ids = [r["branch_id"] for r in db.qa("SELECT DISTINCT branch_id FROM contracts WHERE state='active' OR (state='ended' AND ended_at>=?) ORDER BY branch_id", (now - ENDED_GRACE,))]
    for bid in ids:
        try:
            res = staff.settle(db, bid, now)
        except Exception:                                                                              # one branch's trouble must not stop the others
            log.exception("payroll of branch %s failed", bid)
            continue
        out["branches"] += 1
        out["paid"] += res["paid"]
        out["left"] += res["left"]
    core.kv_set(db, PAYROLL_KEY, str(now))
    return out


def tick(db, now: int | None = None, *, force_payroll: bool = False) -> dict:
    now = core.now() if now is None else now
    out = {"ads": market.sweep(db, now), "quotes": store.expire(db, now), "offers": staff.expire_offers(db, now), "orders": tailor.collect_due(db, None, now), "payroll": None}
    if force_payroll or payroll_due(db, now):
        out["payroll"] = run_payroll(db, now)
    return out
