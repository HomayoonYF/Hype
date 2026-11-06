"""Business upgrades (v0.211): a configurable catalog, what each one costs, where it fits and what it REALLY does.

An upgrade is a group with levels (`Upgrade.levels` = ((cost in cents, effect value), ...)). A branch installs it level by level (only up, never removed), paying from ITS OWN TILL (`biz.till_apply`, kind
`upgrade`), so an upgrade is a real expense of that branch (it shows in the branch's report) and a branch's upgrades never touch another branch's. What decides whether an upgrade can be installed:

* the property must be fitted for the activity it serves (`needs`: retail upgrades only in a store, production ones only where something is made) and be of at least the tier it needs (`min_tier`);
* an upgrade that is a piece of EQUIPMENT (`slot`) takes one of the property's equipment slots (`properties.equip_slots` + the workbench); a second level of the same upgrade never takes another;
* the next level only, and only if the till can pay for it.

EFFECTS (each is read by the system it belongs to; `effects()` adds them up for a branch):
    cap_wh / cap_disp   more warehouse / display capacity (basis points of the property's own)         -> stock.py
    speed / quality     faster production (basis points off the time) and a better machine (quality points) -> tailor.py
    unlock              a machine that makes more products possible (the cutting table)                 -> tailor.py
    attract             the store ranks higher in the directory (basis points on its ranking score)     -> rep.py (the directory order)
    rep_gain            security cameras: every sale builds reputation faster (basis points)             -> store.py
    selfcheck           a self-checkout: purchases up to this many cents go through without approval when no accountant is on duty -> store.py
    slots_staff / slots_equip   one more employee / equipment place per level                              -> staff.py / here
The look of the storefront follows the sign: level 1 is green, 2 neon, 3 two-coloured (the map marker and the storefront picture are drawn from it).
"""
from __future__ import annotations

from dataclasses import dataclass

from . import biz, config as C, core, estate
from .core import GameError

SIGN_STYLES = {0: "plain", 1: "green", 2: "neon", 3: "two"}


@dataclass(frozen=True)
class Upgrade:
    id: str
    effect: str
    levels: tuple                    # ((cost cents, value), ...)
    needs: str | None = None         # 'retail' | 'production' | None (any)
    min_tier: str = "kiosk"
    slot: bool = False               # a piece of equipment: takes one of the property's equipment slots
    icon: str = "🔧"


UPGRADES: dict = {}


def register(u: Upgrade) -> Upgrade:
    if u.id in UPGRADES:
        raise ValueError(f"upgrade {u.id!r} is already registered")
    if not u.levels or any(len(lv) != 2 or lv[0] < 1 for lv in u.levels):
        raise ValueError("an upgrade has levels of (cost, value)")
    if u.needs not in (None, "retail", "production"):
        raise ValueError("an upgrade serves retail, production or both")
    UPGRADES[u.id] = u
    return u


register(Upgrade("shelving", "cap_wh", ((150_00, 2500), (400_00, 6000), (1000_00, 12000)), icon="🗄"))
register(Upgrade("racks", "cap_disp", ((120_00, 2000), (300_00, 5000), (800_00, 10000)), needs="retail", icon="🧺"))
register(Upgrade("machines", "speed", ((300_00, (1000, 2)), (900_00, (2200, 3)), (2500_00, (3500, 4))), needs="production", min_tier="small", slot=True, icon="🧵"))
register(Upgrade("cutting_table", "unlock", ((600_00, "cutting_table"),), needs="production", min_tier="small", slot=True, icon="✂️"))
register(Upgrade("sign", "attract", ((150_00, 500), (400_00, 1000), (700_00, 1500)), needs="retail", icon="💡"))
register(Upgrade("presentation", "attract", ((200_00, 300), (500_00, 600), (1200_00, 1000)), needs="retail", icon="🪞"))
register(Upgrade("cameras", "rep_gain", ((200_00, 1000), (600_00, 2500)), icon="📹"))
register(Upgrade("checkout", "selfcheck", ((250_00, 50_00), (700_00, 150_00), (1800_00, 400_00)), needs="retail", icon="🧾"))
register(Upgrade("staffroom", "slots_staff", ((300_00, 1), (800_00, 2)), icon="🪑"))
register(Upgrade("workbench", "slots_equip", ((300_00, 1), (800_00, 2)), icon="🧰"))

ORDER = tuple(UPGRADES)


def tier_index(tier: str) -> int:
    for i, row in enumerate(C.PROPERTY_TIERS):
        if row[0] == tier:
            return i
    return 0


def installed(db, branch_id: int) -> dict:
    """{upgrade id: level} installed at the branch."""
    return {r["kind"]: r["level"] for r in db.qa("SELECT kind, level FROM branch_upgrades WHERE branch_id=?", (branch_id,))}


def level_of(db, branch_id: int, kind: str) -> int:
    r = db.q1("SELECT level FROM branch_upgrades WHERE branch_id=? AND kind=?", (branch_id, kind))
    return r["level"] if r else 0


def effects(db, branch_id: int) -> dict:
    """The sum of what the branch's installed upgrades do (see the module text)."""
    out = {"cap_wh": 0, "cap_disp": 0, "speed": 0, "quality": 0, "unlock": set(), "attract": 0, "rep_gain": 0, "selfcheck": 0, "slots_staff": 0, "slots_equip": 0}
    for kind, lvl in installed(db, branch_id).items():
        u = UPGRADES.get(kind)
        if u is None or not 1 <= lvl <= len(u.levels):
            continue
        val = u.levels[lvl - 1][1]
        if u.effect == "speed":
            out["speed"] += val[0]
            out["quality"] += val[1]
        elif u.effect == "unlock":
            out["unlock"].add(val)
        elif u.effect == "selfcheck":
            out["selfcheck"] = max(out["selfcheck"], val)
        else:
            out[u.effect] += val
    return out


def equipment_used(db, branch_id: int) -> int:
    return sum(1 for kind in installed(db, branch_id) if kind in UPGRADES and UPGRADES[kind].slot)


def equipment_slots(db, branch) -> int:
    prop = estate.get(db, branch["property_id"])
    return prop["equip_slots"] + effects(db, branch["id"])["slots_equip"]


def staff_slots(db, branch) -> int:
    prop = estate.get(db, branch["property_id"])
    return prop["staff_slots"] + effects(db, branch["id"])["slots_staff"]


def warehouse_cap(db, branch) -> int:
    prop = estate.get(db, branch["property_id"])
    return prop["warehouse_cap"] * (10000 + effects(db, branch["id"])["cap_wh"]) // 10000


def display_cap(db, branch) -> int:
    prop = estate.get(db, branch["property_id"])
    return prop["display_cap"] * (10000 + effects(db, branch["id"])["cap_disp"]) // 10000


def sign_style(db, branch_id: int) -> str:
    return SIGN_STYLES.get(level_of(db, branch_id, "sign"), "plain")


def problem(db, branch, kind: str) -> str | None:
    """Why upgrade `kind` cannot be installed at this branch right now (None = it can; the next level is what is meant). A short code the screens turn into words."""
    u = UPGRADES.get(kind)
    if u is None:
        return "unknown"
    prop = estate.get(db, branch["property_id"])
    have = level_of(db, branch["id"], kind)
    if have >= len(u.levels):
        return "maxed"
    if u.needs is not None and not estate.mode_allows(prop["mode"], u.needs):
        return "mode"
    if tier_index(prop["tier"]) < tier_index(u.min_tier):
        return "tier"
    if u.slot and have == 0 and equipment_used(db, branch["id"]) >= equipment_slots(db, branch):
        return "slots"
    return None


def next_cost(db, branch_id: int, kind: str) -> int | None:
    u = UPGRADES.get(kind)
    if u is None:
        return None
    have = level_of(db, branch_id, kind)
    return u.levels[have][0] if have < len(u.levels) else None


def install(db, uid: int, branch_id: int, kind: str, now: int | None = None) -> int:
    """Install the next level of `kind`, paid from the branch's till. Owner, or a manager with the `upgrade` and `spend` permissions. Returns the new level.
    GameError: branch_gone, branch_denied, upg_unknown, upg_maxed, upg_mode, upg_tier, upg_slots, till_low."""
    now = core.now() if now is None else now
    with db.tx():
        b = biz.need(db, uid, branch_id, "upgrade")
        if not (b["owner_id"] == uid or biz.can(db, uid, b, "spend")):
            raise GameError("branch_denied")
        why = problem(db, b, kind)
        if why is not None:
            raise GameError("upg_" + why)
        u = UPGRADES[kind]
        have = level_of(db, branch_id, kind)
        cost = u.levels[have][0]
        ref = f"{kind}:{have + 1}"
        if cost > biz.spendable(db, b, now):
            raise GameError("till_low" if cost > b["till"] else "till_reserved", need=cost, have=b["till"])
        if biz.till_apply(db, branch_id, -cost, "upgrade", ref, actor=uid, note=kind, now=now) is None:
            raise GameError("upg_maxed")
        if have == 0:
            db.x("INSERT INTO branch_upgrades(branch_id,kind,level,installed_at) VALUES(?,?,?,?)", (branch_id, kind, 1, now))
        else:
            if db.x("UPDATE branch_upgrades SET level=?, installed_at=? WHERE branch_id=? AND kind=? AND level=?", (have + 1, now, branch_id, kind, have)).rowcount != 1:
                raise GameError("upg_maxed")
        if kind == "sign":
            db.x("UPDATE branches SET sign=? WHERE id=?", (SIGN_STYLES[have + 1], branch_id))
        estate.event(db, "upgrade_installed", str(branch_id), uid, {"kind": kind, "level": have + 1, "cost": cost}, now)
        return have + 1
