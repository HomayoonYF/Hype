"""The business world as the player sees it (v0.211), part 3: people and records. Staff and hiring (owner), the contracts a player holds as an employee, the reports (one branch, all of them), the
reputation directory and the style screen (rating, worn pieces, saved outfits). Conventions and rules: see bizui.py; the rules themselves are staff.py, reports.py, rep.py and style.py.

HIRING. The owner offers a contract to a player who is standing NEAR them on the city map (nobody is offered anything by a typed number: the list is the server's own `presence.nearby`, and the offer
is checked against that list again when it is made, so a hand-made button cannot send a stranger a message). The terms are chosen from presets: the role, a fixed wage per day or a share of the
branch's recorded sales, and, for a manager, the permissions. The player accepts or declines from «قراردادهای من»; nothing changes behind anybody's back (staff.py has no way to edit a contract).
Hiring a bot needs no one: the wage is the game's cost.
"""
from __future__ import annotations

from . import biz, brands, cash, config as C, core, fmt, items, presence, rep, reports, staff, style, tailor
from .bizui import R, _cb, back_hub, branch_text, brand_text, city_name, num, plain, usd, where, who
from .core import GameError
from .texts import t

# what a manager can be allowed to do (a reserved 'hire' stays with the owner: contracts are the owner's alone) and the presets the owner chooses the pay from
MANAGER_PERMS = tuple(p for p in biz.PERMS if p != "hire")
FIXED_PRESETS = (5_00, 10_00, 25_00, 50_00)                     # cents a day
PERCENT_PRESETS = (200, 500, 1000, 1500)                        # basis points of the branch's recorded sales
PAGE = 6


def _role(i: int) -> str:
    if not 0 <= i < len(C.ROLES):
        raise GameError("staff_role")
    return C.ROLES[i]


def _mask(perms) -> int:
    return sum(1 << MANAGER_PERMS.index(p) for p in perms if p in MANAGER_PERMS)


def _perms(mask: int) -> tuple:
    return tuple(p for i, p in enumerate(MANAGER_PERMS) if mask >> i & 1)


def pay_text(lang: str, pay_type: str, value: int) -> str:
    if pay_type == "fixed":
        return t(lang, "bz.pay.fixed", amount=usd(value, lang))
    return t(lang, "bz.pay.percent", pct=fmt.loc(f"{value / 100:g}", lang))


def dm_text(lang: str, key: str, kw: dict) -> str:
    """The private messages of the business world (`core.notify` keys `store_pending`, `staff_offer`): the numbers and names are formatted here, in the reader's language."""
    branch = branch_text({"brand": kw["brand"], "seq": kw["seq"]})
    if key == "store_pending":
        return t(lang, "n.store_pending", branch=branch, name=_item(lang, kw["item_id"], kw.get("data") or None), n=num(kw["qty"], lang), total=usd(kw["cents"], lang))
    if key == "staff_offer":
        return t(lang, "n.staff_offer", who=fmt.esc(kw["who"]), role=t(lang, f"bz.role.{kw['role']}"), branch=branch, pay=pay_text(lang, kw["pay_type"], kw["pay_value"]))
    raise KeyError(key)


def _holder(db, lang: str, ct) -> str:
    return t(lang, "bz.stf.bot") if ct["employee_id"] is None else who(db, ct["employee_id"])


def _owner_of(db, uid: int, bid: int):
    """The open branch `bid` when `uid` OWNS it (contracts are the owner's alone)."""
    b = biz.get(db, bid)
    if b is None or b["state"] != "open":
        raise GameError("branch_gone")
    if b["owner_id"] != uid:
        raise GameError("branch_denied")
    return b


# =====================================================================================================================
#                                                       staff (owner)
# =====================================================================================================================
def staff_screen(c, bid: int, toast: str | None = None):
    lang, db, uid = c.lang, c.db, c.uid
    b = _owner_of(db, uid, bid)
    now = core.now()
    cts = staff.of_branch(db, bid)
    from . import upgrades
    lines = [t(lang, "bz.stf.head", branch=branch_text(b), n=num(len(cts), lang), cap=num(upgrades.staff_slots(db, b), lang), owed=usd(staff.reserved(db, bid, now), lang))]
    rows = []
    for ct in cts:
        owed = staff.owed_total(db, ct, now)
        lines.append(t(lang, "bz.stf.line", role=t(lang, f"bz.role.{ct['role']}"), who=_holder(db, lang, ct), pay=pay_text(lang, ct["pay_type"], ct["pay_value"]),
                       state=t(lang, f"bz.cstate.{ct['state']}"), owed=usd(owed, lang)))
        rows.append([(t(lang, "bz.stf.btn", role=t(lang, f"bz.role.{ct['role']}"), who=plain(_holder(db, lang, ct)), state=t(lang, f"bz.cstate.{ct['state']}")), _cb("sc", ct["id"], uid=uid))])
    if not cts:
        lines.append(t(lang, "bz.stf.none"))
    rows.append([(t(lang, "bz.b.hirep"), _cb("hp", bid, uid=uid)), (t(lang, "bz.b.hirebot"), _cb("hb", bid, uid=uid))])
    rows.append([(t(lang, "bz.b.branch", branch=plain(branch_text(b))), _cb("bo", bid, uid=uid))])
    return R(c, "\n".join(lines), rows, toast=toast)


def contract_screen(c, cid: int, toast: str | None = None):
    """One contract: its terms as written, what is owed now, and what the viewer may do with it (the owner: end / withdraw; the employee: accept / decline / leave)."""
    lang, db, uid = c.lang, c.db, c.uid
    ct = staff.get(db, cid)
    if ct is None:
        raise GameError("staff_gone")
    b = biz.get(db, ct["branch_id"])
    if b is None or (uid != b["owner_id"] and uid != ct["employee_id"]):
        raise GameError("staff_gone")
    owner = uid == b["owner_id"]
    now = core.now()
    lines = [t(lang, "bz.ct.head", branch=branch_text(b), role=t(lang, f"bz.role.{ct['role']}")), t(lang, "bz.ct.who", who=_holder(db, lang, ct), state=t(lang, f"bz.cstate.{ct['state']}")),
             t(lang, "bz.ct.pay", pay=pay_text(lang, ct["pay_type"], ct["pay_value"]))]
    perms = staff.perms_of(ct)
    if perms:
        lines.append(t(lang, "bz.ct.perms", perms=("، " if lang == "fa" else ", ").join(t(lang, f"bz.perm.{p}") for p in perms)))
    if ct["state"] in ("active", "ended"):
        fx, pc = staff.owed(db, ct, now)
        lines.append(t(lang, "bz.ct.since", date=fmt.date(ct["starts_at"], lang)))
        lines.append(t(lang, "bz.ct.owed", owed=usd(fx + pc, lang), paid=usd(ct["paid_fixed"] + ct["paid_pct"], lang)))
    if ct["state"] == "offered" and ct["expires_at"]:
        lines.append(t(lang, "bz.ct.expires", left=fmt.dur(max(0, ct["expires_at"] - now), lang)))
    rows = []
    if ct["state"] == "offered":
        if ct["employee_id"] == uid:
            rows.append([(t(lang, "bz.b.accept"), _cb("ka", cid, uid=uid)), (t(lang, "bz.b.decline"), _cb("kd", cid, uid=uid))])
        if owner:
            rows.append([(t(lang, "bz.b.withdraw"), _cb("ow", cid, uid=uid))])
    elif ct["state"] == "active":
        rows.append([(t(lang, "bz.b.endc" if owner else "bz.b.leave"), _cb("se", cid, uid=uid))])
        if ct["employee_id"] == uid:
            rows.append([(t(lang, "bz.b.getwage"), _cb("kw", cid, uid=uid))])
    rows.append([(t(lang, "bz.b.staff") if owner else t(lang, "bz.b.contracts"), _cb("sf", b["id"], uid=uid) if owner else _cb("k", uid=uid))])
    return R(c, "\n".join(lines), rows, toast=toast)


def end_ask(c, cid: int):
    lang, db, uid = c.lang, c.db, c.uid
    ct = staff.get(db, cid)
    if ct is None or ct["state"] != "active":
        raise GameError("staff_gone")
    b = biz.get(db, ct["branch_id"])
    if b is None or (uid != b["owner_id"] and uid != ct["employee_id"]):
        raise GameError("staff_gone")
    fx, pc = staff.owed(db, ct, core.now())
    text = t(lang, "bz.ct.endask", branch=branch_text(b), role=t(lang, f"bz.role.{ct['role']}"), who=_holder(db, lang, ct), owed=usd(fx + pc, lang))
    return R(c, text, [[(t(lang, "bz.b.confirm_end"), _cb("sE", cid, uid=uid))], [(t(lang, "bz.b.cancel"), _cb("sc", cid, uid=uid))]])


def end_do(c, cid: int):
    lang, db, uid = c.lang, c.db, c.uid
    ct = staff.end(db, uid, cid, core.now())
    b = biz.get(db, ct["branch_id"])
    if b is not None and uid == b["owner_id"]:
        return staff_screen(c, b["id"], toast=plain(t(lang, "bz.ct.ended")))
    return my_contracts(c, toast=plain(t(lang, "bz.ct.ended")))


def withdraw_do(c, cid: int):
    lang, db, uid = c.lang, c.db, c.uid
    ct = staff.withdraw_offer(db, uid, cid, core.now())
    return staff_screen(c, ct["branch_id"], toast=plain(t(lang, "bz.ct.withdrawn")))


# --- hiring a bot ----------------------------------------------------------------------------------------------------
def hire_bot_screen(c, bid: int):
    lang, db, uid = c.lang, c.db, c.uid
    b = _owner_of(db, uid, bid)
    lines = [t(lang, "bz.hb.head", branch=branch_text(b))]
    rows = []
    for i, role in enumerate(C.ROLES):
        if role not in C.BOT_ROLES:
            continue
        wage = C.BOT_WORKER_WAGE_DAY_CENTS[role]
        have = staff.bot_role(db, bid, role) is not None
        lines.append(t(lang, "bz.hb.line", role=t(lang, f"bz.role.{role}"), wage=usd(wage, lang), what=t(lang, f"bz.role.{role}.bot"), have=t(lang, "bz.hb.have") if have else ""))
        if not have:
            rows.append([(t(lang, "bz.hb.btn", role=t(lang, f"bz.role.{role}"), wage=usd(wage, lang)), _cb("hB", bid, i, uid=uid))])
    lines.append(t(lang, "bz.hb.note"))
    rows.append([(t(lang, "bz.b.staff"), _cb("sf", bid, uid=uid))])
    return R(c, "\n".join(lines), rows)


def hire_bot_do(c, bid: int, ri: int):
    lang, db, uid = c.lang, c.db, c.uid
    role = _role(ri)
    staff.hire_bot(db, uid, bid, role, core.now())
    return staff_screen(c, bid, toast=plain(t(lang, "bz.hb.done", role=t(lang, f"bz.role.{role}"))))


# --- hiring a player -------------------------------------------------------------------------------------------------
async def hire_list(c, bid: int):
    lang, db, uid = c.lang, c.db, c.uid
    b = _owner_of(db, uid, bid)
    city, st = await where(c)
    near = presence.nearby(db, city, uid, st.pos, core.now(), limit=8)
    lines = [t(lang, "bz.hp.head", branch=branch_text(b))]
    rows = []
    for o in near:
        lines.append(t(lang, "bz.hp.line", who=who(db, o.uid), d=fmt.dist(o.dist, lang)))
        rows.append([(t(lang, "bz.hp.btn", who=plain(who(db, o.uid))), _cb("hq", bid, o.uid, uid=uid))])
    if not near:
        lines.append(t(lang, "bz.hp.none"))
    rows.append([(t(lang, "bz.b.staff"), _cb("sf", bid, uid=uid))])
    return R(c, "\n".join(lines), rows)


def _target(db, tuid: int) -> str:
    u = core.get_user(db, tuid)
    if u is None or u["bot"]:
        raise GameError("staff_noone")
    return who(db, tuid)


def hire_role(c, bid: int, tuid: int):
    lang, db, uid = c.lang, c.db, c.uid
    b = _owner_of(db, uid, bid)
    name = _target(db, tuid)
    lines = [t(lang, "bz.hq.head", branch=branch_text(b), who=name)]
    rows = []
    for i, role in enumerate(C.ROLES):
        lines.append(t(lang, "bz.hq.line", role=t(lang, f"bz.role.{role}"), what=t(lang, f"bz.role.{role}.d")))
        rows.append([(t(lang, "bz.hq.btn", role=t(lang, f"bz.role.{role}")), _cb("hr", bid, tuid, i, uid=uid))])
    rows.append([(t(lang, "bz.b.back"), _cb("hp", bid, uid=uid))])
    return R(c, "\n".join(lines), rows)


def hire_role_chosen(c, bid: int, tuid: int, ri: int):
    """A manager first chooses the permissions (all four useful ones are on by default), the other roles go straight to the pay."""
    role = _role(ri)
    if role == "manager":
        return hire_perms(c, bid, tuid, ri, _mask(staff.MANAGER_DEFAULT))
    return hire_pay(c, bid, tuid, ri, 0)


def hire_perms(c, bid: int, tuid: int, ri: int, mask: int):
    lang, db, uid = c.lang, c.db, c.uid
    b = _owner_of(db, uid, bid)
    if _role(ri) != "manager" or not 0 <= mask < 1 << len(MANAGER_PERMS):
        raise GameError("staff_terms")
    lines = [t(lang, "bz.hm.head", branch=branch_text(b), who=_target(db, tuid)), t(lang, "bz.hm.note")]
    rows = []
    for i, p in enumerate(MANAGER_PERMS):
        on = bool(mask >> i & 1)
        lines.append(t(lang, "bz.hm.line", mark="✅" if on else "▫️", perm=t(lang, f"bz.perm.{p}"), what=t(lang, f"bz.perm.{p}.d")))
        rows.append([(t(lang, "bz.hm.btn", mark="✅" if on else "▫️", perm=t(lang, f"bz.perm.{p}")), _cb("hm", bid, tuid, ri, mask ^ (1 << i), uid=uid))])
    rows.append([(t(lang, "bz.b.next"), _cb("hy", bid, tuid, ri, mask, uid=uid))])
    rows.append([(t(lang, "bz.b.back"), _cb("hq", bid, tuid, uid=uid))])
    return R(c, "\n".join(lines), rows)


def hire_pay(c, bid: int, tuid: int, ri: int, mask: int):
    lang, db, uid = c.lang, c.db, c.uid
    b = _owner_of(db, uid, bid)
    role = _role(ri)
    if role != "manager" and mask != 0:
        raise GameError("staff_terms")
    lines = [t(lang, "bz.hy.head", branch=branch_text(b), who=_target(db, tuid), role=t(lang, f"bz.role.{role}")), t(lang, "bz.hy.note", max=num(C.STAFF_PERCENT_MAX_BPS // 100, lang))]
    rows = [[(t(lang, "bz.hy.fixed", amount=usd(v, lang)), _cb("hf", bid, tuid, ri, mask, "f", v, uid=uid)) for v in FIXED_PRESETS[:2]],
            [(t(lang, "bz.hy.fixed", amount=usd(v, lang)), _cb("hf", bid, tuid, ri, mask, "f", v, uid=uid)) for v in FIXED_PRESETS[2:]],
            [(t(lang, "bz.hy.percent", pct=fmt.loc(f"{v / 100:g}", lang)), _cb("hf", bid, tuid, ri, mask, "p", v, uid=uid)) for v in PERCENT_PRESETS[:2]],
            [(t(lang, "bz.hy.percent", pct=fmt.loc(f"{v / 100:g}", lang)), _cb("hf", bid, tuid, ri, mask, "p", v, uid=uid)) for v in PERCENT_PRESETS[2:]],
            [(t(lang, "bz.b.back"), _cb("hq", bid, tuid, uid=uid))]]
    return R(c, "\n".join(lines), rows)


def _terms(ptype: str, value: int) -> tuple:
    if ptype == "f":
        return "fixed", value if value in FIXED_PRESETS else None
    if ptype == "p":
        return "percent", value if value in PERCENT_PRESETS else None
    return None, None


def hire_confirm(c, bid: int, tuid: int, ri: int, mask: int, ptype: str, value: int):
    lang, db, uid = c.lang, c.db, c.uid
    b = _owner_of(db, uid, bid)
    role = _role(ri)
    pay_type, val = _terms(ptype, value)
    if pay_type is None or val is None:
        raise GameError("staff_terms")
    perms = staff.role_perms(role, _perms(mask) if role == "manager" else None)
    lines = [t(lang, "bz.hf.head", branch=branch_text(b), who=_target(db, tuid), role=t(lang, f"bz.role.{role}")), t(lang, "bz.ct.pay", pay=pay_text(lang, pay_type, val)),
             t(lang, "bz.ct.perms", perms=("، " if lang == "fa" else ", ").join(t(lang, f"bz.perm.{p}") for p in perms)), t(lang, "bz.hf.note", ttl=fmt.dur(C.STAFF_OFFER_TTL, lang))]
    return R(c, "\n".join(lines), [[(t(lang, "bz.b.sendoffer"), _cb("hO", bid, tuid, ri, mask, ptype, value, uid=uid))], [(t(lang, "bz.b.cancel"), _cb("sf", bid, uid=uid))]])


async def hire_do(c, bid: int, tuid: int, ri: int, mask: int, ptype: str, value: int):
    lang, db, uid = c.lang, c.db, c.uid
    b = _owner_of(db, uid, bid)
    role = _role(ri)
    pay_type, val = _terms(ptype, value)
    if pay_type is None or val is None:
        raise GameError("staff_terms")
    city, st = await where(c)
    if tuid not in {o.uid for o in presence.nearby(db, city, uid, st.pos, core.now(), limit=C.MAP_MAX_OTHERS)}:
        raise GameError("staff_far")                                                          # an offer goes only to somebody standing near the owner (server-side, whatever the button says)
    ct = staff.offer(db, uid, bid, tuid, role, pay_type, val, _perms(mask) if role == "manager" else None, core.now())
    return staff_screen(c, bid, toast=plain(t(lang, "bz.hf.sent", who=plain(who(db, tuid)))))


# =====================================================================================================================
#                                                       contracts I hold (employee)
# =====================================================================================================================
def my_contracts(c, toast: str | None = None):
    lang, db, uid = c.lang, c.db, c.uid
    now = core.now()
    cts = [x for x in staff.of_employee(db, uid) if x["state"] == "active" or x["expires_at"] is None or x["expires_at"] > now]
    lines = [t(lang, "bz.k.head", n=num(len(cts), lang))]
    rows = []
    for ct in cts:
        b = biz.get(db, ct["branch_id"])
        if b is None:
            continue
        lines.append(t(lang, "bz.k.line", branch=branch_text(b), role=t(lang, f"bz.role.{ct['role']}"), pay=pay_text(lang, ct["pay_type"], ct["pay_value"]), state=t(lang, f"bz.cstate.{ct['state']}"),
                       owed=usd(staff.owed_total(db, ct, now), lang)))
        rows.append([(t(lang, "bz.k.btn", branch=plain(branch_text(b)), role=t(lang, f"bz.role.{ct['role']}"), state=t(lang, f"bz.cstate.{ct['state']}")), _cb("sc", ct["id"], uid=uid))])
    if not cts:
        lines.append(t(lang, "bz.k.none"))
    rows.append(back_hub(lang, uid))
    return R(c, "\n".join(lines), rows, toast=toast)


def contract_accept(c, cid: int):
    lang, uid = c.lang, c.uid
    ct = staff.accept(c.db, uid, cid, core.now())
    return contract_screen(c, cid, toast=plain(t(lang, "bz.k.accepted")))


def contract_decline(c, cid: int):
    lang = c.lang
    staff.decline(c.db, c.uid, cid, core.now())
    return my_contracts(c, toast=plain(t(lang, "bz.k.declined")))


def claim_wage(c, cid: int):
    """💵 ask the branch to pay what it owes: it pays from its till as far as the till holds it (staff.settle is safe to repeat); what it cannot pay stays owed."""
    lang, db, uid = c.lang, c.db, c.uid
    ct = staff.get(db, cid)
    if ct is None or ct["employee_id"] != uid or ct["state"] not in ("active", "ended"):
        raise GameError("staff_gone")
    before = cash.balance(db, uid)
    out = staff.settle(db, ct["branch_id"], core.now())
    got = cash.balance(db, uid) - before
    key = "bz.ct.paid" if got > 0 else "bz.ct.nopay"
    r = contract_screen(c, cid, toast=plain(t(lang, key, amount=usd(got, lang), left=usd(out["left"], lang))))
    return r


# =====================================================================================================================
#                                                       reports
# =====================================================================================================================
_PERIODS = {0: None, 1: 86400, 7: 7 * 86400}


def report_screen(c, bid: int, days: int = 0):
    lang, db, uid = c.lang, c.db, c.uid
    b = biz.need(db, uid, bid, None)
    if days not in _PERIODS:
        raise GameError("bad_amount")
    now = core.now()
    since = 0 if _PERIODS[days] is None else now - _PERIODS[days]
    p = reports.pnl(db, bid, since, now)
    lines = [t(lang, "bz.rp.head", branch=branch_text(b), period=t(lang, f"bz.rp.p{days}"))]
    lines.append(t(lang, "bz.rp.income", revenue=usd(p["revenue"], lang), sales=num(p["sales"], lang), buyback=usd(p["buyback"], lang), income=usd(p["income"], lang)))
    lines.append(t(lang, "bz.rp.costs", materials=usd(p["materials"], lang), wages=usd(p["wages"], lang), upgrades=usd(p["upgrades"], lang), costs=usd(p["costs"], lang)))
    lines.append(t(lang, "bz.rp.profit", profit=usd(p["profit"], lang)))
    lines.append(t(lang, "bz.rp.capital", till=usd(p["till"], lang), funded=usd(p["funded"], lang), withdrawn=usd(p["withdrawn"], lang), wages=usd(p["reserved_wages"], lang), ads=usd(p["reserved_ads"], lang)))
    lines.append(t(lang, "bz.rp.stock", value=usd(p["stock_value"], lang), score=num(p["reputation"], lang), title=t(lang, f"bz.rep.{rep.title_id(p['reputation'])}")))
    best = reports.best_sellers(db, bid, 3)
    if best:
        lines.append(t(lang, "bz.rp.best"))
        for item_id, data, n, cents in best:
            lines.append(t(lang, "bz.rp.bestrow", name=_item(lang, item_id, data), n=num(n, lang), cents=usd(cents, lang)))
    rows = [[(t(lang, f"bz.rp.p{d}"), _cb("rp", bid, d, uid=uid)) for d in (0, 1, 7) if d != days]]
    rows.append([(t(lang, "bz.b.branch", branch=plain(branch_text(b))), _cb("bo", bid, uid=uid))])
    return R(c, "\n".join(lines), rows)


def _item(lang: str, item_id: str, data) -> str:
    q = items.quality_of(data)
    return f"{items.icon(item_id)} {fmt.esc(items.display_name(lang, item_id, data))}" + (f" {items.stars(q)}" if q else "")


def overview_screen(c):
    """The owner's view of every open branch (income, costs, profit, till, staff, work in progress), whatever brand or city."""
    lang, db, uid = c.lang, c.db, c.uid
    ov = reports.overview(db, uid)
    lines = [t(lang, "bz.ro.head", n=num(len(ov), lang))]
    rows = []
    tot = {"income": 0, "costs": 0, "profit": 0}
    for o in ov:
        b, p = o["branch"], o["pnl"]
        for k in tot:
            tot[k] += p[k]
        order = o["order"]
        lines.append(t(lang, "bz.ro.line", branch=branch_text(b), city=city_name(db, lang, b["world_id"]), income=usd(p["income"], lang), costs=usd(p["costs"], lang), profit=usd(p["profit"], lang),
                       till=usd(p["till"], lang), staff=num(len(o["staff"]), lang), ads=num(len(o["ads"]), lang),
                       order=t(lang, "bz.ro.order", n=num(order["pieces"], lang), product=fmt.esc(items.display_name(lang, order["product"], None))) if order else t(lang, "bz.ro.noorder")))
        rows.append([(t(lang, "bz.ro.btn", branch=plain(branch_text(b))), _cb("rp", b["id"], 0, uid=uid))])
    if not ov:
        lines.append(t(lang, "bz.ro.none"))
    else:
        lines.append(t(lang, "bz.ro.total", income=usd(tot["income"], lang), costs=usd(tot["costs"], lang), profit=usd(tot["profit"], lang)))
    rows.append(back_hub(lang, uid))
    return R(c, "\n".join(lines), rows)


# =====================================================================================================================
#                                                       reputation
# =====================================================================================================================
def _rep_lines(db, lang: str, rows: list, with_city: bool) -> list:
    out = []
    for i, r in enumerate(rows, 1):
        b = r["branch"]
        out.append(t(lang, "bz.rtrow", rank=num(i, lang), branch=branch_text(b), owner=who(db, b["owner_id"]), title=t(lang, f"bz.rep.{r['title']}"), score=num(r["score"], lang), sales=num(r["sales"], lang),
                     city=(" • " + city_name(db, lang, b["world_id"])) if with_city else ""))
    return out


async def rep_screen(c, scope: str = "c"):
    """The reputation directory: the open branches of my city (c) or of the whole game (g), best first; my own branches and the brands' headquarters below."""
    lang, db, uid = c.lang, c.db, c.uid
    cap = brands.capital(db)
    lines = []
    rows = []
    wid = None
    if scope == "c":
        try:
            city, _st = await where(c)
            wid = city.id
        except Exception:
            wid = None
    if scope == "c" and wid is not None:
        lines.append(t(lang, "bz.rt.head.city", city=city_name(db, lang, wid)))
        lines += _rep_lines(db, lang, rep.directory(db, wid, 8), False) or [t(lang, "bz.rt.none")]
    else:
        scope = "g"
        lines.append(t(lang, "bz.rt.head.all"))
        lines += _rep_lines(db, lang, rep.ranking(db, 10), True) or [t(lang, "bz.rt.none")]
    mine = biz.of_owner(db, uid, open_only=True)
    if mine:
        lines.append(t(lang, "bz.rt.mine"))
        for b in mine:
            r = rep.get(db, b["id"])
            nxt = rep.next_title(r["score"])
            lines.append(t(lang, "bz.rt.myrow", branch=branch_text(b), title=t(lang, f"bz.rep.{rep.title_id(r['score'])}"), score=num(r["score"], lang),
                           next=t(lang, "bz.rt.next", n=num(nxt[0] - r["score"], lang), title=t(lang, f"bz.rep.{nxt[1]}")) if nxt else t(lang, "bz.rt.top")))
    lines.append(t(lang, "bz.rt.capital", city=city_name(db, lang, cap) if cap else t(lang, "bz.capital.none")))
    rows.append([(t(lang, "bz.rt.btn.all" if scope == "c" else "bz.rt.btn.city"), _cb("t", "g" if scope == "c" else "c", uid=uid))])
    rows.append(back_hub(lang, uid))
    return R(c, "\n".join(lines), rows)


# =====================================================================================================================
#                                                       style
# =====================================================================================================================
def style_screen(c, toast: str | None = None):
    lang, db, uid = c.lang, c.db, c.uid
    r = style.rating(db, uid)
    lines = [t(lang, "bz.sy.head", points=num(r["points"], lang), tier=t(lang, f"bz.sy.tier.{r['tier']}"), bar=fmt.bar(r["points"] / 100))]
    if r["worn"]:
        for slot, (item_id, data) in r["worn"].items():
            s = items.slot(slot)
            lines.append(t(lang, "bz.sy.worn", icon=s.icon if s else "▫️", slot=fmt.esc(items.slot_name(lang, slot)), item=_item(lang, item_id, data)))
        lines.append(t(lang, "bz.sy.complete" if r["complete"] else "bz.sy.incomplete"))
    else:
        lines.append(t(lang, "bz.sy.nothing"))
    lines.append(t(lang, "bz.sy.what", bps=fmt.loc(f"{C.STYLE_TIP_BPS_MAX / 100:g}", lang)))
    saved = style.outfits(db, uid)
    lines.append(t(lang, "bz.sy.outfits"))
    rows = []
    for k in C.OUTFIT_KINDS:
        have = k in saved
        lines.append(t(lang, "bz.sy.outfit", kind=t(lang, f"bz.sy.k.{k}"), state=t(lang, "bz.sy.saved", n=num(len(saved[k]), lang)) if have else t(lang, "bz.sy.empty")))
        row = [(t(lang, "bz.sy.save", kind=t(lang, f"bz.sy.k.{k}")), _cb("ys", k, uid=uid))]
        if have:
            row.append((t(lang, "bz.sy.wear", kind=t(lang, f"bz.sy.k.{k}")), _cb("yw", k, uid=uid)))
        rows.append(row)
    rows.append([(t(lang, "iv.b.eq"), f"iv:q:{uid}"), back_hub(lang, uid)[0]])
    return R(c, "\n".join(lines), rows, toast=toast)


def outfit_save(c, kind: str):
    lang = c.lang
    style.save_outfit(c.db, c.uid, kind, core.now())
    return style_screen(c, toast=plain(t(lang, "bz.sy.savedtoast", kind=t(lang, f"bz.sy.k.{kind}"))))


def outfit_wear(c, kind: str):
    lang = c.lang
    out = style.wear_outfit(c.db, c.uid, kind, core.now())
    msg = t(lang, "bz.sy.weartoast", n=num(len(out["worn"]), lang), kind=t(lang, f"bz.sy.k.{kind}"))
    if out["missing"]:
        msg += "\n" + t(lang, "bz.sy.missing", n=num(len(out["missing"]), lang))
    return style_screen(c, toast=plain(msg))
