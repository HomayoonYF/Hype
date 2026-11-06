"""Business reputation (v0.211): a persistent score for every branch, earned only by real, recorded business.

WHAT EARNS IT: a completed sale to a CUSTOMER (the points follow the quality of the piece sold: `config.REP_SALE_PER_STAR` per star), a material ad that a seller really fulfilled (`REP_FILL`) and a payroll
paid in full (`REP_WAGE_RUN`). A CCTV upgrade raises what a sale earns (`rep_gain`, basis points). WHAT NEVER DOES: a purchase by the owner or by the branch's own staff (store.py refuses those), a sale below
`ACCOUNTING_MIN_SALE_CENTS`, and anything the same reference has already paid: every award is a row of `rep_log`, UNIQUE per branch, kind and reference, so the same sale cannot count twice. One customer
can give a branch at most `REP_PER_BUYER_DAILY` points a (server) day, so a reputation cannot be farmed with one account.

The score only goes up. `title` turns it into a name for the directory and the rankings (`directory`: the open branches of a city, best first; a sign or shop-window upgrade lifts a branch in the ORDER only).
"""
from __future__ import annotations

from . import brands, config as C, core


def get(db, branch_id: int) -> dict:
    r = db.q1("SELECT * FROM branch_rep WHERE branch_id=?", (branch_id,))
    return dict(r) if r else {"branch_id": branch_id, "score": 0, "sales": 0, "updated_at": 0}


def score(db, branch_id: int) -> int:
    return get(db, branch_id)["score"]


def title_id(points: int) -> str:
    out = C.REP_TITLES[0][1]
    for lo, tid in C.REP_TITLES:
        if points >= lo:
            out = tid
    return out


def next_title(points: int) -> tuple | None:
    for lo, tid in C.REP_TITLES:
        if lo > points:
            return lo, tid
    return None


def add(db, branch_id: int, kind: str, ref: str, points: int, who: int | None, now: int | None = None, *, sale: bool = False) -> int:
    """Award `points` once per (branch, kind, ref). Returns the points actually added (0 when this reference was already counted or the customer's daily limit is reached)."""
    if not isinstance(points, int) or isinstance(points, bool) or points <= 0:
        return 0
    now = core.now() if now is None else now
    with db.savepoint():
        if db.q1("SELECT 1 FROM rep_log WHERE branch_id=? AND kind=? AND ref=?", (branch_id, kind, ref)) is not None:
            return 0
        if who is not None:
            day = now - now % 86400
            got = db.q1("SELECT COALESCE(SUM(delta),0) s FROM rep_log WHERE branch_id=? AND who=? AND created_at>=?", (branch_id, who, day))["s"]
            points = min(points, max(0, C.REP_PER_BUYER_DAILY - got))
            if points <= 0:
                return 0
        db.x("INSERT INTO rep_log(branch_id,kind,ref,delta,who,created_at) VALUES(?,?,?,?,?,?)", (branch_id, kind, ref, points, who, now))
        db.x("INSERT INTO branch_rep(branch_id,score,sales,updated_at) VALUES(?,?,?,?) ON CONFLICT(branch_id) DO UPDATE SET score=score+excluded.score, sales=sales+excluded.sales, updated_at=excluded.updated_at",
             (branch_id, points, 1 if sale else 0, now))
    return points


def _ranked(db, rows, limit: int) -> list:
    """The directory order: the REAL score, lifted by the branch's `attract` (the sign and the shop-window upgrades: basis points on the score, `upgrades.effects`). The score that is shown is
    always the real one; ties go to the older branch. Only the order changes, so an upgrade never makes a reputation look bigger than it is."""
    from . import upgrades
    out = []
    for b in rows:
        lift = upgrades.effects(db, b["id"])["attract"]
        out.append((-(b["score"] * (10000 + lift) // 10000), b["opened_at"], b["id"], b))
    out.sort(key=lambda r: r[:3])
    return [{"branch": r[3], "score": r[3]["score"], "sales": r[3]["sales"], "title": title_id(r[3]["score"])} for r in out[:limit]]


def directory(db, world_id: int, limit: int = 10) -> list:
    """The open branches of a city by reputation: [{branch, score, sales, title}] best first (ties: the older branch)."""
    rows = db.qa("SELECT b.*, COALESCE(r.score,0) score, COALESCE(r.sales,0) sales FROM branches b LEFT JOIN branch_rep r ON r.branch_id=b.id WHERE b.world_id=? AND b.state='open'", (world_id,))
    return _ranked(db, rows, limit)


def ranking(db, limit: int = 10) -> list:
    """The same across the whole game."""
    rows = db.qa("SELECT b.*, COALESCE(r.score,0) score, COALESCE(r.sales,0) sales FROM branches b LEFT JOIN branch_rep r ON r.branch_id=b.id WHERE b.state='open'")
    return _ranked(db, rows, limit)
