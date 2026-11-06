"""Tailoring (v0.211): making clothes from fabric, in real time, at a property that is fitted for production.

WHAT IT TAKES. A branch whose property allows PRODUCTION, a sewing machine installed (an upgrade), the recipe's other equipment (a cutting table for trousers and coats ...), enough fabric of the chosen
quality in the branch's WAREHOUSE, a worker whose Tailoring skill reaches the recipe's, and a recipe that can be crafted at all (`fashion.Recipe.craftable`: a product made of leather or metal has a
recipe that waits for a material the game does not produce yet, and is refused). The worker is a PLAYER (the owner, a manager or a production worker: in person, standing at the property, with their one job
slot for the whole time) or a BOT production worker the owner hired (fixed performance, no skill, no XP, no job slot).

QUALITY IS NOT LUCK. The final quality of every piece of an order is the LOWEST of four factors, so the weakest link limits the result:
  material   the stars of the fabric used;
  skill      the stars the worker's Tailoring skill allows (`config.TAILOR_SKILL_Q`: 1 star from the start ... 5 at 900);
  equipment  the stars of the best installed machine (2, 3 or 4);
  property   the highest quality the property can make (a commercial property never more than FOUR: `config.COMMERCIAL_MAX_QUALITY`, also held by the database).
Five stars therefore cannot come out of a business, whatever the skill and the fabric: they are for gifted items (admin gift codes) and for a future residential workshop (the same factors, a
property that allows five). The factors, the weakest one and everything else about the order are SAVED in the order, which becomes the PRODUCTION HISTORY of its pieces.

TIME AND MATERIAL. A piece takes the recipe's seconds, shortened by the worker's skill (up to `TAILOR_SPEED_BPS_MAX`) and the machine (the upgrade's speed); the order takes pieces x that, in real time
on the server clock, and cannot be finished early. The skill also saves a little material (up to `TAILOR_SAVE_BPS_MAX`, never below one unit a piece). The fabric leaves the warehouse when the order STARTS.

FINISHING. A finished order puts its pieces into the branch's WAREHOUSE with their data (quality, brand, production-history id), once (guarded update), and a human worker gets character XP and the work
of Tailoring skill for the whole order, each once per order. A full warehouse leaves the order waiting, nothing is lost. Cancelling an order forfeits its materials (they were used).
"""
from __future__ import annotations

import json
import zlib

from . import biz, brands, charxp, config as C, core, estate, fashion, items, presence, skills, staff, stock, textile, upgrades, work
from .core import GameError

JOB = work.TAILOR


# =====================================================================================================================
#                                                       the quality rule (pure)
# =====================================================================================================================
def skill_quality(level: int) -> int:
    out = 1
    for need, q in C.TAILOR_SKILL_Q:
        if level >= need:
            out = q
    return out


def final_quality(material_q: int, skill_q: int, equipment_q: int, property_q: int) -> tuple:
    """(quality, name of the weakest factor): the lowest of the four factors, never above the commercial maximum. Deterministic: no randomness anywhere. A tie names the first in the order material,
    skill, equipment, property."""
    factors = (("material", material_q), ("skill", skill_q), ("equipment", equipment_q), ("property", min(property_q, C.COMMERCIAL_MAX_QUALITY)))
    low = min(v for _, v in factors)
    return low, next(name for name, v in factors if v == low)


def piece_seconds(recipe, skill_level: int, machine_speed_bps: int) -> int:
    cut = min(9000, skills.bonus_bps(skill_level, C.TAILOR_SPEED_BPS_MAX) + max(0, machine_speed_bps))
    return max(60, -(-recipe.seconds * (10000 - cut) // 10000))


def material_units(per_piece: int, pieces: int, skill_level: int) -> int:
    save = skills.bonus_bps(skill_level, C.TAILOR_SAVE_BPS_MAX)
    return max(pieces, -(-per_piece * pieces * (10000 - save) // 10000))


# =====================================================================================================================
#                                                       planning an order
# =====================================================================================================================
def worker_level(db, worker: int | None) -> int:
    return C.BOT_WORKER_SKILL if worker is None else skills.level_of(db, worker, "tailoring")


def plan(db, branch, product: str, pieces: int, material_q: int, worker: int | None) -> dict:
    """Everything an order would be, and what is in its way: {"ok", "problems": [codes], "quality", "limit", "factors", "seconds", "units", "per_piece", "recipe", ...}. Pure (reads only); `start` uses it
    and the screens show it before the player confirms. Problem codes: recipe, material_kind, pieces, skill, equipment, machine, mode, fabric."""
    prop = biz.property_of(db, branch)
    r = fashion.recipe_of(product)
    out: dict = {"ok": False, "problems": [], "recipe": r, "product": product, "pieces": pieces, "material_q": material_q}
    if r is None:
        out["problems"].append("recipe")
        return out
    if not r.craftable:
        out["problems"].append("material_kind")
    if not isinstance(pieces, int) or isinstance(pieces, bool) or not 1 <= pieces <= C.TAILOR_MAX_BATCH:
        out["problems"].append("pieces")
    if not estate.mode_allows(prop["mode"], "production"):
        out["problems"].append("mode")
    eff = upgrades.effects(db, branch["id"])
    level = worker_level(db, worker)
    if level < r.min_skill:
        out["problems"].append("skill")
    if eff["quality"] <= 0:
        out["problems"].append("machine")
    missing = [n for n in r.needs if n not in eff["unlock"]]
    if missing:
        out["problems"].append("equipment")
        out["missing"] = missing
    if not (isinstance(material_q, int) and not isinstance(material_q, bool) and 1 <= material_q <= 5):
        out["problems"].append("fabric")
        return out
    q, limit = final_quality(material_q, skill_quality(level), max(1, eff["quality"]), prop["max_quality"])
    secs = piece_seconds(r, level, eff["speed"])
    per = r.materials[0][1] if r.materials else 0
    units = material_units(per, pieces, level) if isinstance(pieces, int) and pieces >= 1 else 0
    have = sum(x["qty"] for x in stock.contents(db, branch["id"], stock.WH) if x["item_id"] == "fabric" and (items.quality_of(x["data"]) or 0) == material_q)
    if r.craftable and have < units:
        out["problems"].append("fabric")
    out.update({"quality": q, "limit": limit, "factors": {"material": material_q, "skill": skill_quality(level), "equipment": max(1, eff["quality"]), "property": min(prop["max_quality"], C.COMMERCIAL_MAX_QUALITY)},
                "seconds": secs * pieces if isinstance(pieces, int) else secs, "piece_seconds": secs, "units": units, "have": have, "level": level, "machine_level": upgrades.level_of(db, branch["id"], "machines")})
    out["ok"] = not out["problems"]
    return out


def fabric_qualities(db, branch_id: int) -> dict:
    """{stars: pieces} of the fabric in the branch's warehouse."""
    out: dict = {}
    for x in stock.contents(db, branch_id, stock.WH):
        if x["item_id"] == "fabric":
            q = items.quality_of(x["data"]) or 0
            out[q] = out.get(q, 0) + x["qty"]
    return out


# =====================================================================================================================
#                                                       starting
# =====================================================================================================================
def start(db, city, uid: int, branch_id: int, product: str, pieces: int, material_q: int, now: int | None = None, *, bot: bool = False):
    """Start an order. `bot=True` hands it to the branch's hired production bot; otherwise the player works it in person (they must be standing at the property and have no other job running).
    Returns the order row. GameError: branch_gone, branch_denied, prod_busy (the workshop is working), prod_bot (no bot hired), job_far, job_busy, recipe_none, recipe_locked, recipe_materials,
    recipe_pieces, recipe_mode, recipe_machine, recipe_equipment, recipe_fabric."""
    now = core.now() if now is None else now
    with db.tx():
        b = biz.need(db, uid, branch_id, "produce")
        if db.q1("SELECT 1 FROM prod_orders WHERE branch_id=? AND state='working'", (branch_id,)) is not None:
            raise GameError("prod_busy")
        worker = None
        if bot:
            if staff.bot_role(db, branch_id, "production") is None:
                raise GameError("prod_bot")
        else:
            worker = uid
            work.check_start(db, uid, JOB, now)
            prop = biz.property_of(db, b)
            st = presence.stand(db, city, uid, now)
            if st.trip is not None or st.row["at_place"] != prop["parcel_id"] or prop["world_id"] != city.id:
                raise GameError("job_far")
        pl = plan(db, b, product, pieces, material_q, worker)
        if not pl["ok"]:
            code = pl["problems"][0]
            raise GameError({"recipe": "recipe_none", "material_kind": "recipe_materials", "pieces": "recipe_pieces", "skill": "recipe_locked", "equipment": "recipe_equipment",
                             "machine": "recipe_machine", "mode": "recipe_mode", "fabric": "recipe_fabric"}[code], **({"need": pl["recipe"].min_skill} if code == "skill" else {}))
        prop = biz.property_of(db, b)
        mats = [{"kind": "fabric", "q": material_q, "units": pl["units"]}]
        cur = db.x("INSERT INTO prod_orders(branch_id,property_id,world_id,brand,product,pieces,worker_id,bot,requested_by,state,quality,factors,materials,seconds,skill,equipment,started_at,due_at) "
                   "VALUES(?,?,?,?,?,?,?,?,?,'working',?,?,?,?,?,?,?,?)",
                   (branch_id, prop["id"], b["world_id"], b["brand"], product, pieces, worker, 1 if bot else 0, uid, pl["quality"], json.dumps({**pl["factors"], "limit": pl["limit"]}, sort_keys=True, separators=(",", ":")),
                    json.dumps(mats, separators=(",", ":")), pl["seconds"], pl["level"], f"machines:{pl['machine_level']}", now, now + pl["seconds"]))
        oid = cur.lastrowid
        left = pl["units"]
        for x in sorted((r for r in stock.contents(db, branch_id, stock.WH) if r["item_id"] == "fabric" and (items.quality_of(r["data"]) or 0) == material_q), key=lambda r: (-r["qty"], r["data"])):
            if left <= 0:
                break
            k = min(left, x["qty"])
            stock.remove(db, branch_id, stock.WH, "fabric", x["data"], k, "craft_in", f"o{oid}:{zlib.crc32(x['data'].encode()):08x}", actor=uid, now=now)
            left -= k
        if left > 0:
            raise GameError("recipe_fabric")
        if worker is not None:
            tid = work._open(db, uid, city.id, JOB, kind=product, stage="working", data={"place": prop["parcel_id"], "order": oid, "branch": branch_id}, reward=pieces, now=now, started=now, due=now + pl["seconds"])
            db.x("UPDATE prod_orders SET task_id=? WHERE id=?", (tid, oid))
        estate.event(db, "order_started", str(oid), uid, {"branch": branch_id, "product": product, "pieces": pieces, "q": pl["quality"]}, now)
    return get(db, oid)


# =====================================================================================================================
#                                                       reading
# =====================================================================================================================
def get(db, order_id):
    if not isinstance(order_id, int) or isinstance(order_id, bool):
        return None
    return db.q1("SELECT * FROM prod_orders WHERE id=?", (order_id,))


def working(db, branch_id: int):
    return db.q1("SELECT * FROM prod_orders WHERE branch_id=? AND state='working'", (branch_id,))


def recent(db, branch_id: int, limit: int = 6) -> list:
    return db.qa("SELECT * FROM prod_orders WHERE branch_id=? ORDER BY id DESC LIMIT ?", (branch_id, limit))


def history_of(db, ph: int):
    """The production history of pieces that carry `ph` (a finished order), or None."""
    r = db.q1("SELECT * FROM prod_orders WHERE id=? AND state='done'", (ph,)) if isinstance(ph, int) and not isinstance(ph, bool) else None
    return r


def history_lines(db, lang: str, data) -> list:
    """The production history of one item instance as text lines (empty for an item with none): product, brand, quality, producer, materials, time, property, date."""
    ph = items.parse(data).get("ph")
    r = history_of(db, ph) if ph else None
    if r is None:
        return []
    from . import fmt
    prop = estate.get(db, r["property_id"])
    who = "bot" if r["bot"] else core.get_user(db, r["worker_id"])
    name = "🤖" if r["bot"] else (core.display_name(who, with_badge=False) if who else "?")
    mats = json.loads(r["materials"])
    fq = json.loads(r["factors"])
    return [("ثبت تولید" if lang != "en" else "Production record") + f" #{r['id']}",
            f"{fashion.PRODUCTS[r['product']].fa if lang != 'en' else fashion.PRODUCTS[r['product']].en} · {brands.name_of(r['brand'])} · {items.stars(r['quality'])}",
            ("سازنده: " if lang != "en" else "Producer: ") + fmt.esc(str(name)),
            ("مواد: " if lang != "en" else "Materials: ") + ", ".join(f"{m['units']}× {fashion.MATERIAL_NAMES[m['kind']][0 if lang != 'en' else 1]} {items.stars(m['q'])}" for m in mats),
            ("زمان: " if lang != "en" else "Time: ") + fmt.dur(r["seconds"], lang),
            ("مهارت: " if lang != "en" else "Skill: ") + fmt.num(r["skill"], lang) + " · " + r["equipment"] + (f" · {fq.get('limit', '')}" if fq.get("limit") else ""),
            ("محل: " if lang != "en" else "Place: ") + (prop["parcel_id"].upper() if prop else "?")]


# =====================================================================================================================
#                                                       finishing
# =====================================================================================================================
def state(order, now: int) -> str:
    """'working' | 'ready' (due, not yet put in the warehouse) | 'done' | 'cancelled'."""
    if order["state"] != "working":
        return order["state"]
    return "ready" if order["due_at"] <= now else "working"


def _finish(db, order, now: int) -> dict:
    """Put a due order's pieces in the branch warehouse (once) and pay the worker's progress. GameError: wh_full (the order stays waiting), job_notready."""
    if order["due_at"] > now:
        raise GameError("job_notready", secs=max(1, order["due_at"] - now))
    b = biz.get(db, order["branch_id"])
    if db.x("UPDATE prod_orders SET state='done', finished_at=? WHERE id=? AND state='working'", (now, order["id"])).rowcount != 1:
        raise GameError("job_claimed")
    data = {"q": order["quality"], "b": order["brand"], "ph": order["id"]}
    stock.add(db, b, stock.WH, order["product"], data, order["pieces"], "craft_out", f"o{order['id']}", actor=order["worker_id"], now=now)
    out = {"order": order["id"], "pieces": order["pieces"], "quality": order["quality"], "product": order["product"], "xp": 0, "level_from": None, "level_to": None}
    w = order["worker_id"]
    if w is not None:
        if order["task_id"] is not None:
            work._close(db, w, order["task_id"], "working", [], {"job": JOB, "order": order["id"], "product": order["product"], "pieces": order["pieces"], "q": order["quality"]}, now)
        xp = max(1, order["seconds"] * C.TAILOR_XP_PER_HOUR // 3600)
        if charxp.get(db, w) is not None and charxp.grant(db, w, xp, f"job:{JOB}", str(order["id"]), now) is not None:
            out["xp"] = xp
        out["level_from"] = out["level_to"] = skills.level_of(db, w, "tailoring")
        moved = skills.grant_work(db, w, "tailoring", skills.milli(order["seconds"]), f"job:{JOB}", str(order["id"]), now)
        if moved is not None:
            out["level_from"], out["level_to"] = moved
    estate.event(db, "order_done", str(order["id"]), w, {"branch": order["branch_id"], "pieces": order["pieces"]}, now)
    return out


def claim(db, uid: int, order_id: int, now: int | None = None) -> dict:
    """The human worker collects their finished order. GameError: job_stale (not theirs), job_claimed, job_notready, wh_full."""
    now = core.now() if now is None else now
    with db.tx():
        o = get(db, order_id)
        if o is None or o["worker_id"] != uid or o["state"] == "cancelled":
            raise GameError("job_stale")
        if o["state"] == "done":
            raise GameError("job_claimed")
        return _finish(db, o, now)


def collect_due(db, branch_id: int | None = None, now: int | None = None) -> int:
    """Finish every due order a BOT is working (lazily: any screen and the periodic job call it). A full warehouse leaves an order waiting. Returns how many were finished."""
    now = core.now() if now is None else now
    q = "SELECT * FROM prod_orders WHERE state='working' AND bot=1 AND due_at<=?" + (" AND branch_id=?" if branch_id is not None else "")
    n = 0
    for o in db.qa(q, (now,) + ((branch_id,) if branch_id is not None else ())):
        try:
            with db.tx():
                _finish(db, get(db, o["id"]), now)
            n += 1
        except GameError:
            continue
    return n


def cancel_order(db, uid: int, order_id: int, now: int | None = None) -> bool:
    """Cancel a working order (owner, or whoever may `produce` there): the materials were used and are not returned. True when this call cancelled it."""
    now = core.now() if now is None else now
    with db.tx():
        o = get(db, order_id)
        if o is None or o["state"] != "working":
            return False
        b = biz.get(db, o["branch_id"])
        if b is None or not biz.can(db, uid, b, "produce"):
            raise GameError("branch_denied")
        if db.x("UPDATE prod_orders SET state='cancelled', finished_at=? WHERE id=? AND state='working'", (now, order_id)).rowcount != 1:
            return False
        if o["task_id"] is not None:
            work._stop(db, o["worker_id"], o["task_id"], None, now)
        return True


# =====================================================================================================================
#                                                       hooks
# =====================================================================================================================
def _blockers(db, prop) -> list:
    b = db.q1("SELECT id FROM branches WHERE property_id=? AND state='open'", (prop["id"],))
    return ["order"] if b is not None and working(db, b["id"]) is not None else []


def _on_close(db, branch, now: int) -> None:
    if working(db, branch["id"]) is not None:
        raise GameError("close_busy")


estate.register_blocker_hook(_blockers)
biz.register_close_hook(_on_close, first=True)
