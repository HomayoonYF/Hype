"""The job screens (v0.18): the job board and the Snapp, McDonald's and municipal screens, and every `jb:` button under them.

Buttons carry only `jb:<action>[:<task id | kind:round>]:<player id>`. The dispatcher refuses any press whose trailing player id is not the
presser's (see handlers.BOUND); after that each action re-reads the saved task and the saved trip, so a stale, repeated or hand-made button
changes nothing and never pays twice (work.py has the guarded updates). All amounts shown are read from the saved task; none comes from a button.
"""
from __future__ import annotations

import logging
import math
import re

from . import config as C, core, fmt, jobviz, mapui, presence, work, world, worldinspect, worldplaces
from .core import GameError
from .texts import t
from .views import Reply, kb

log = logging.getLogger("hypemarket")

EMOJI = {"snapp": "🚕", "mcd": "🍔", "muni": "🧹"}
KIND_EMOJI = {"sweep": "🧹", "waste": "🗑", "green": "🌳"}
_ID = re.compile(r"^\d{1,12}$")


class NoCity(Exception):
    """The group's city could not be loaded (damaged or still failing to generate)."""
    def __init__(self, key: str):
        super().__init__(key)
        self.key = key


def R(c, text: str, rows: list, toast: str | None = None, alert: bool = False, pic=None) -> Reply:
    """A job screen. `pic` (jobviz.scene_pic / jobviz.map_pic) is the picture the screen shows above its text; `fresh` makes a screen without
    one replace a message that has one (and the other way round) instead of leaving a stale picture behind."""
    return Reply(text=text, kb=kb(rows), edit=c.cb is not None, toast=toast, alert=alert, pic=pic, fresh=True)


def _scene(c, kind: str, title: str, sub: str = "", **kw):
    return jobviz.scene_pic(kind, c.lang, title=title, sub=sub, **kw)


def _car_of_receipt(rc: dict) -> dict:
    car = rc.get("car")
    lo, hi = work.CAR_YEARS
    if isinstance(car, dict) and isinstance(car.get("y"), int) and lo <= car["y"] <= hi and car.get("c") in work.CAR_COLORS:
        return car
    return {"y": lo, "c": "white"}                                  # a receipt saved before v0.18.1 has no car: the plainest one


async def city_of(c):
    """The chat's city (created on first use, exactly like «مپ»), or None in a private chat."""
    if not c.is_group:
        return None
    try:
        w, _created = await world.aensure_world(c.db, c.chat.id)
        return await worldplaces.aload_city(c.db, w)
    except world.WorldError as e:
        log.error("jobs: city of chat %s unavailable: %s", c.chat.id, e.code)
        raise NoCity("map.failed" if e.code == "generation_failed" else "map.corrupt")


def _eta(dist: float, speed: float) -> int:
    return max(1, math.ceil(dist / speed - 1e-9))


def _pct(st) -> str:
    return fmt.num(round(st.progress * 100), "en")


def _place(lang: str, city, ref: str) -> str:
    spot = city.spot(ref)
    return mapui.place_html(lang, spot) if spot else fmt.esc(worldinspect.display_id(ref))


def _name(lang: str, job: str) -> str:
    return f"{EMOJI[job]} {t(lang, f'job.{job}.name')}"


def _id(ref: str) -> str:
    return f"<code>{worldinspect.display_id(ref)}</code>"


def _city_name(db, lang: str, world_id: int) -> str:
    w = db.q1("SELECT name_fa, name_en FROM worlds WHERE id=?", (world_id,))
    return fmt.esc(w["name_fa" if lang == "fa" else "name_en"]) if w else "?"


# =====================================================================================================================
#                                                       the job board
# =====================================================================================================================
def _stage_text(lang: str, db, task, now: int) -> str:
    if task["job"] == work.MCD:
        return t(lang, "job.stage.mcd." + ("ready" if work.shift_state(task, now) == "ready" else "working"))
    return t(lang, f"job.stage.{task['job']}.{task['stage']}")


def _history_lines(db, lang: str, uid: int) -> list:
    rows = work.history(db, uid, 4)
    if not rows:
        return []
    out = [t(lang, "job.hist.head")]
    for r in rows:
        rc = work.receipt_of(r)
        amount = rc.get("net", rc.get("wage", rc.get("reward", 0)))
        out.append(t(lang, "job.hist.row", job=_name(lang, r["job"]), amount=fmt.usd(amount, lang)))
    return out


async def board(c, toast: str | None = None) -> Reply:
    from . import cash
    lang, db = c.lang, c.db
    now = core.now()
    try:
        city = await city_of(c)
    except NoCity as e:
        return Reply(text=t(lang, e.key))
    if city is not None:
        rc = work.sync(db, city, c.uid, now)
        if rc is not None:                                           # a ride that ended while nobody was looking: show its receipt first
            return _receipt(c, rc)
    task = work.current(db, c.uid)
    active = t(lang, "job.none") if task is None else t(lang, "job.active", job=_name(lang, task["job"]), stage=_stage_text(lang, db, task, now))
    lines = [t(lang, "job.board", cash=fmt.usd(cash.balance(db, c.uid), lang), active=active)]
    hist = _history_lines(db, lang, c.uid)
    if hist:
        lines += [""] + hist
    uid = c.uid
    rows = [[(t(lang, "job.b.snapp"), f"jb:o:snapp:{uid}"), (t(lang, "job.b.mcd"), f"jb:o:mcd:{uid}")],
            [(t(lang, "job.b.muni"), f"jb:o:muni:{uid}")],
            [(t(lang, "job.b.wallet"), f"cs:w:{uid}"), (t(lang, "job.b.refresh"), f"jb:h:{uid}")]]
    return R(c, "\n".join(lines), rows, toast=toast, pic=_scene(c, "board", t(lang, "scene.board.t"), t(lang, "scene.board.s")))


def _other_city(c, task) -> Reply:
    """The player's open task belongs to another city (or to a city they are not in right now): say so, and let them give it up."""
    lang = c.lang
    text = t(lang, "job.othercity", job=_name(lang, task["job"]), city=_city_name(c.db, lang, task["world_id"]))
    rows = []
    if task["job"] != work.MCD:
        rows.append([(t(lang, "job.b.cancel"), f"jb:x:{task['id']}:{c.uid}")])
    rows.append([(t(lang, "job.b.board"), f"jb:h:{c.uid}")])
    return R(c, text, rows)


def _private(c, job: str, cmd: str) -> Reply:
    return R(c, t(c.lang, "job.private", job=_name(c.lang, job), cmd=cmd), [[(t(c.lang, "job.b.board"), f"jb:h:{c.uid}")]])


# =====================================================================================================================
#                                                        Snapp
# =====================================================================================================================
def _terms(lang: str) -> str:
    return t(lang, "snapp.terms", fee=fmt.usd(C.SNAPP_FEE_CENTS, lang), base=fmt.usd(C.SNAPP_BASE_FARE_CENTS, lang),
             per=fmt.usd(C.SNAPP_PER_100_CENTS, lang), tipmax=fmt.usd(C.SNAPP_TIP_MAX_CENTS, lang), speed=fmt.loc(f"{C.car_speed() / C.walk_speed():g}", lang))


def _receipt(c, rc: dict) -> Reply:
    lang = c.lang
    car = _car_of_receipt(rc)
    text = t(lang, "snapp.receipt", pax=t(lang, f"snapp.pax.{rc.get('pax', 0)}"), car=jobviz.car_label(lang, car), fare=fmt.usd(rc["fare"], lang),
             tip=fmt.usd(rc["tip"], lang), fee=fmt.usd(rc["fee"], lang), net=fmt.usd(rc["net"], lang), balance=fmt.usd(rc["balance"], lang))
    rows = [[(t(lang, "snapp.b.next"), f"jb:sr:{c.uid}")], [(t(lang, "job.b.board"), f"jb:h:{c.uid}"), (t(lang, "job.b.wallet"), f"cs:w:{c.uid}")]]
    pic = _scene(c, "snapp_done", t(lang, "scene.snapp.done.t"), "+" + fmt.usd(rc["net"], lang), car=car["c"], plate_text=jobviz.plate_year(lang, car))
    return R(c, text, rows, pic=pic)


def _snapp_home(c, city, toast=None, alert=False) -> Reply:
    from . import cash
    lang, db = c.lang, c.db
    now = core.now()
    bal = cash.balance(db, c.uid)
    text = t(lang, "snapp.home", terms=_terms(lang), cash=fmt.usd(bal, lang))
    left = work.cooldown_left(db, c.uid, work.SNAPP, now)
    rows = []
    if left > 0:
        text += t(lang, "snapp.cool", time=fmt.dur(left, lang))
        rows.append([(t(lang, "job.b.refresh"), f"jb:o:snapp:{c.uid}")])
    elif bal < C.SNAPP_FEE_CENTS:
        text += t(lang, "snapp.poor", need=fmt.usd(C.SNAPP_FEE_CENTS, lang))
        rows.append([(t(lang, "job.b.muni"), f"jb:o:muni:{c.uid}"), (t(lang, "job.b.mcd"), f"jb:o:mcd:{c.uid}")])
    else:
        rows.append([(t(lang, "snapp.b.request"), f"jb:sr:{c.uid}")])
    rows.append([(t(lang, "job.b.board"), f"jb:h:{c.uid}"), (t(lang, "job.b.wallet"), f"cs:w:{c.uid}")])
    return R(c, text, rows, toast=toast, alert=alert, pic=_scene(c, "snapp_home", t(lang, "scene.snapp.home.t"), t(lang, "scene.snapp.home.s"), car="white"))


def _snapp_task(c, city, task, toast=None, alert=False) -> Reply:
    lang, db, uid = c.lang, c.db, c.uid
    d = work.data_of(task)
    pax = t(lang, f"snapp.pax.{d.get('pax', 0)}")
    car = work.car_of(task)
    carl = jobviz.car_label(lang, car)
    st = presence.stand(db, city, uid)
    fare, fee = int(task["reward"]), int(task["cost"])
    tid = task["id"]
    cancel = (t(lang, "snapp.b.cancel"), f"jb:x:{tid}:{uid}")
    refresh = (t(lang, "job.b.refresh"), f"jb:o:snapp:{uid}")
    stage = task["stage"]
    pickup, dest = d["pickup"], d["dest"]

    def route_map(legend: str):
        """The map of the ride: the car, the person pin at the pickup, the flag pin at the destination, blue to the passenger, yellow to the destination."""
        return jobviz.map_pic(city, lang, lambda: jobviz.snapp_marks(lang, city, task, st, car)), "\n\n" + t(lang, legend)

    if stage == "assigned":
        text = t(lang, "snapp.ride", pax=pax, car=carl, pickup=_place(lang, city, pickup), d1=fmt.num(d["d1"], lang), eta1=fmt.dur(_eta(d["d1"], C.car_speed()), lang),
                 dest=_place(lang, city, dest), d2=fmt.num(d["d2"], lang), fare=fmt.usd(fare, lang), fee=fmt.usd(fee, lang),
                 net=fmt.usd(fare - fee, lang), tipmax=fmt.usd(C.SNAPP_TIP_MAX_CENTS, lang))
        rows = [[(t(lang, "snapp.b.drive"), f"jb:sd:{tid}:{uid}")], [cancel]]
        pic, legend = route_map("snapp.legend")
        return R(c, text + legend, rows, toast=toast, alert=alert, pic=pic)
    here = st.row["at_place"] if st.trip is None else None
    car_mode = st.trip is not None and st.trip["mode"] == "car"
    if stage == "to_pickup":
        if here == pickup:
            text = t(lang, "snapp.atPickup", pax=pax, car=carl, place=_place(lang, city, pickup))
            rows = [[(t(lang, "snapp.b.pickup"), f"jb:sp:{tid}:{uid}")], [cancel]]
            pic = _scene(c, "snapp_pickup", t(lang, "scene.snapp.pickup.t"), pax, chip=t(lang, "scene.snapp.pickup.chip"), car=car["c"], plate_text=jobviz.plate_year(lang, car), pax=d.get("pax", 0))
            return R(c, text, rows, toast=toast, alert=alert, pic=pic)
        if st.trip is not None:
            text = t(lang, "snapp.toPickup", pax=pax, car=carl, place=_place(lang, city, pickup), pct=fmt.loc(_pct(st), lang), eta=fmt.dur(st.eta, lang),
                     mode=t(lang, "snapp.mode.car" if car_mode else "snapp.mode.walk"))
            rows = [[refresh], [cancel]]
        else:
            text = t(lang, "snapp.lost", pax=pax, place=_place(lang, city, pickup))
            rows = [[(t(lang, "snapp.b.resume"), f"jb:sd:{tid}:{uid}")], [cancel]]
        pic, legend = route_map("snapp.legend")
        return R(c, text + legend, rows, toast=toast, alert=alert, pic=pic)
    if stage == "to_dest":
        if st.trip is not None:
            text = t(lang, "snapp.toDest", pax=pax, car=carl, place=_place(lang, city, dest), pct=fmt.loc(_pct(st), lang), eta=fmt.dur(st.eta, lang),
                     fare=fmt.usd(fare, lang), fee=fmt.usd(fee, lang), mode=t(lang, "snapp.mode.car" if car_mode else "snapp.mode.walk"))
            rows = [[refresh], [cancel]]
        else:
            text = t(lang, "snapp.lostDest", pax=pax, place=_place(lang, city, dest))
            rows = [[(t(lang, "snapp.b.resume"), f"jb:sd:{tid}:{uid}")], [cancel]]
        pic, legend = route_map("snapp.legend.dest")
        return R(c, text + legend, rows, toast=toast, alert=alert, pic=pic)
    return _snapp_home(c, city, toast, alert)


async def snapp(c, toast=None, alert=False) -> Reply:
    lang, db = c.lang, c.db
    try:
        city = await city_of(c)
    except NoCity as e:
        return Reply(text=t(lang, e.key))
    if city is None:
        return _private(c, work.SNAPP, "اسنپ" if lang == "fa" else "snapp")
    rc = work.sync(db, city, c.uid)
    if rc is not None:
        return _receipt(c, rc)
    task = work.current(db, c.uid)
    if task is None:
        return _snapp_home(c, city, toast, alert)
    if task["job"] != work.SNAPP:
        return R(c, t(lang, "job.busy_other", job=_name(lang, task["job"])), [[(t(lang, "job.b.board"), f"jb:h:{c.uid}")]], toast=toast, alert=alert)
    if task["world_id"] != city.id:
        return _other_city(c, task)
    return _snapp_task(c, city, task, toast, alert)


# =====================================================================================================================
#                                                     McDonald's
# =====================================================================================================================
def _shift_block(c, task, now: int) -> str:
    lang = c.lang
    ready = work.shift_state(task, now) == "ready"
    return t(lang, "mcd.status", state=t(lang, "mcd.state.ready" if ready else "mcd.state.working"), wage=fmt.usd(task["reward"], lang),
             start=fmt.clock(task["started_at"], lang), end=fmt.clock(task["due_at"], lang),
             left=t(lang, "mcd.left.none") if ready else fmt.dur(task["due_at"] - now, lang),
             collected=t(lang, "mcd.collected.ready" if ready else "mcd.collected.no"))


def _mcd_task(c, task, toast=None, alert=False) -> Reply:
    lang, now, uid = c.lang, core.now(), c.uid
    text = _shift_block(c, task, now)
    if work.shift_state(task, now) == "ready":
        rows = [[(t(lang, "mcd.b.claim"), f"jb:mc:{task['id']}:{uid}")]]
        pic = _scene(c, "mcd_ready", t(lang, "scene.mcd.ready.t"), t(lang, "scene.mcd.ready.s"), chip=t(lang, "scene.mcd.ready.chip"))
    else:
        rows = [[(t(lang, "job.b.refresh"), f"jb:o:mcd:{uid}")]]
        span = max(1, int(task["due_at"]) - int(task["started_at"]))
        frac = max(0.0, min(1.0, (now - int(task["started_at"])) / span))
        pic = _scene(c, "mcd_work", t(lang, "scene.mcd.work.t"), t(lang, "scene.mcd.work.s"), chip=t(lang, "scene.mcd.work.chip"), frac=frac,
                     left_text=t(lang, "scene.mcd.left", left=fmt.dur(max(0, int(task["due_at"]) - now), lang)))
    rows.append([(t(lang, "job.b.board"), f"jb:h:{uid}"), (t(lang, "job.b.wallet"), f"cs:w:{uid}")])
    return R(c, text, rows, toast=toast, alert=alert, pic=pic)


def _last_shift(c) -> str:
    row = c.db.q1("SELECT * FROM work_tasks WHERE user_id=? AND job=? AND state='done' ORDER BY finished_at DESC, id DESC LIMIT 1", (c.uid, work.MCD))
    if row is None:
        return ""
    lang = c.lang
    return "\n\n" + t(lang, "mcd.last", start=fmt.clock(row["started_at"], lang), end=fmt.clock(row["due_at"], lang), wage=fmt.usd(row["reward"], lang))


def _mcd_idle(c, city, st, toast=None, alert=False) -> Reply:
    lang, uid = c.lang, c.uid
    head = t(lang, "mcd.home", wage=fmt.usd(C.MCD_WAGE_CENTS, lang), hours=fmt.num(C.MCD_SHIFT_SECONDS // 3600, lang))
    mid = city.mcd_id
    if mid is None:
        return R(c, head + "\n\n" + t(lang, "mcd.none"), [[(t(lang, "job.b.board"), f"jb:h:{uid}")]], toast=toast, alert=alert)
    place = _place(lang, city, mid)
    rows = []
    door = _scene(c, "mcd_door", t(lang, "scene.mcd.door.t"), t(lang, "scene.mcd.door.s", hours=fmt.num(C.MCD_SHIFT_SECONDS // 3600, lang)))
    walk = jobviz.map_pic(city, lang, lambda: jobviz.walk_marks(lang, city, st, mid))        # the way there, drawn like every walk, with a bag pin at the McDonald's
    pic = walk
    if st.trip is None and st.row["at_place"] == mid:
        text = head + "\n\n" + t(lang, "mcd.here", place=place)
        rows.append([(t(lang, "mcd.b.apply"), f"jb:ms:{uid}")])
        pic = door
    elif st.trip is not None and st.trip["dest_id"] == mid:
        text = head + "\n\n" + t(lang, "mcd.walking", place=place, pct=fmt.loc(_pct(st), lang), eta=fmt.dur(st.eta, lang))
        rows.append([(t(lang, "job.b.refresh"), f"jb:o:mcd:{uid}")])
    else:
        spot = city.spot(mid)
        d = city.net.field(st.pos).to(spot.door)
        text = head + "\n\n" + t(lang, "mcd.far", place=place, d=fmt.num(round(d), lang), eta=fmt.dur(_eta(d, C.walk_speed()), lang))
        if st.trip is None:
            rows.append([(t(lang, "mcd.b.go"), f"jb:mg:{uid}")])
        else:
            text += "\n" + t(lang, "mcd.busy_walk")
            rows.append([(t(lang, "job.b.refresh"), f"jb:o:mcd:{uid}")])
    text += _last_shift(c)
    rows.append([(t(lang, "job.b.board"), f"jb:h:{uid}"), (t(lang, "job.b.wallet"), f"cs:w:{uid}")])
    return R(c, text, rows, toast=toast, alert=alert, pic=pic)


async def mcd(c, toast=None, alert=False) -> Reply:
    lang, db = c.lang, c.db
    task = work.current(db, c.uid)
    if task is not None and task["job"] == work.MCD:                  # a shift's status and wage need no city: they work in a private chat too
        return _mcd_task(c, task, toast, alert)
    try:
        city = await city_of(c)
    except NoCity as e:
        return Reply(text=t(lang, e.key))
    if city is None:
        return _private(c, work.MCD, "مک دونالد" if lang == "fa" else "mcdonalds")
    rc = work.sync(db, city, c.uid)
    if rc is not None:
        return _receipt(c, rc)
    task = work.current(db, c.uid)
    if task is not None:
        if task["world_id"] != city.id:
            return _other_city(c, task)
        return R(c, t(lang, "job.busy_other", job=_name(lang, task["job"])), [[(t(lang, "job.b.board"), f"jb:h:{c.uid}")]], toast=toast, alert=alert)
    st = presence.stand(db, city, c.uid)
    return _mcd_idle(c, city, st, toast, alert)


def _mcd_confirm(c) -> Reply:
    lang = c.lang
    now = core.now()
    text = t(lang, "mcd.confirm", wage=fmt.usd(C.MCD_WAGE_CENTS, lang), hours=fmt.num(C.MCD_SHIFT_SECONDS // 3600, lang),
             start=fmt.clock(now, lang), end=fmt.clock(now + C.MCD_SHIFT_SECONDS, lang))
    pic = _scene(c, "mcd_door", t(lang, "scene.mcd.door.t"), t(lang, "scene.mcd.door.s", hours=fmt.num(C.MCD_SHIFT_SECONDS // 3600, lang)))
    return R(c, text, [[(t(lang, "mcd.b.yes"), f"jb:my:{c.uid}"), (t(lang, "mcd.b.no"), f"jb:o:mcd:{c.uid}")]], pic=pic)


# =====================================================================================================================
#                                                  municipal tasks
# =====================================================================================================================
def _target_text(lang: str, city, kind: str, ref: str) -> str:
    return t(lang, f"muni.target.{kind}", place=_place(lang, city, ref))


def _muni_home(c, city, st, toast=None, alert=False) -> Reply:
    lang, db, uid = c.lang, c.db, c.uid
    now = core.now()
    text = t(lang, "muni.home")
    left = work.cooldown_left(db, uid, work.MUNI, now)
    rows = []
    if left > 0:
        text += "\n\n" + t(lang, "muni.cool", time=fmt.dur(left, lang))
        rows.append([(t(lang, "job.b.refresh"), f"jb:o:muni:{uid}")])
    elif st.trip is not None:
        text += "\n\n" + t(lang, "muni.walking_first")
        rows.append([(t(lang, "job.b.refresh"), f"jb:o:muni:{uid}")])
    else:
        offers = work.muni_offers(db, city, uid, st.pos, now)
        if not offers:
            text += "\n\n" + t(lang, "muni.none")
        else:
            text += "\n\n" + t(lang, "muni.offers")
            for o in offers:
                name = t(lang, f"muni.kind.{o.kind}")
                text += "\n\n" + t(lang, "muni.offer", emoji=KIND_EMOJI[o.kind], name=name, target=_target_text(lang, city, o.kind, o.spot.id),
                                   d=fmt.num(round(o.dist), lang), eta=fmt.dur(_eta(o.dist, C.walk_speed()), lang), reward=fmt.usd(o.reward, lang))
                rows.append([(t(lang, "muni.b.accept", emoji=KIND_EMOJI[o.kind], name=name, reward=fmt.usd(o.reward, lang)), f"jb:na:{o.kind}:{o.round}:{uid}")])
    rows.append([(t(lang, "job.b.board"), f"jb:h:{uid}"), (t(lang, "job.b.wallet"), f"cs:w:{uid}")])
    return R(c, text, rows, toast=toast, alert=alert, pic=_scene(c, "muni_home", t(lang, "scene.muni.home.t"), t(lang, "scene.muni.home.s")))


def _muni_task(c, city, task, toast=None, alert=False) -> Reply:
    lang, db, uid = c.lang, c.db, c.uid
    d = work.data_of(task)
    st = presence.stand(db, city, uid)
    kind, target, tid = task["kind"], d["target"], task["id"]
    head = t(lang, "muni.task", emoji=KIND_EMOJI[kind], name=t(lang, f"muni.kind.{kind}"), target=_target_text(lang, city, kind, target), reward=fmt.usd(task["reward"], lang))
    cancel = (t(lang, "muni.b.cancel"), f"jb:x:{tid}:{uid}")
    walk = jobviz.map_pic(city, lang, lambda: jobviz.walk_marks(lang, city, st, target))
    if st.trip is None and st.row["at_place"] == target:
        text = head + "\n\n" + t(lang, "muni.here")
        rows = [[(t(lang, "muni.b.done"), f"jb:nd:{tid}:{uid}")], [cancel]]
        pic = _scene(c, "muni_" + kind, t(lang, f"muni.kind.{kind}"), fmt.usd(task["reward"], lang), chip=t(lang, "scene.muni.here"))
    elif st.trip is not None:
        text = head + "\n\n" + t(lang, "muni.wait", dest=_place(lang, city, st.trip["dest_id"]), pct=fmt.loc(_pct(st), lang), eta=fmt.dur(st.eta, lang))
        rows = [[(t(lang, "job.b.refresh"), f"jb:o:muni:{uid}")], [cancel]]
        pic = walk
    else:
        spot = city.spot(target)
        dist = city.net.field(st.pos).to(spot.door) if spot else 0
        text = head + "\n\n" + t(lang, "muni.far", d=fmt.num(round(dist), lang), eta=fmt.dur(_eta(dist, C.walk_speed()), lang))
        rows = [[(t(lang, "muni.b.go"), f"jb:ng:{tid}:{uid}")], [cancel]]
        pic = walk
    return R(c, text, rows, toast=toast, alert=alert, pic=pic)


async def muni(c, toast=None, alert=False) -> Reply:
    lang, db = c.lang, c.db
    try:
        city = await city_of(c)
    except NoCity as e:
        return Reply(text=t(lang, e.key))
    if city is None:
        return _private(c, work.MUNI, "شهرداری" if lang == "fa" else "municipal")
    rc = work.sync(db, city, c.uid)
    if rc is not None:
        return _receipt(c, rc)
    task = work.current(db, c.uid)
    if task is None:
        return _muni_home(c, city, presence.stand(db, city, c.uid), toast, alert)
    if task["job"] != work.MUNI:
        return R(c, t(lang, "job.busy_other", job=_name(lang, task["job"])), [[(t(lang, "job.b.board"), f"jb:h:{c.uid}")]], toast=toast, alert=alert)
    if task["world_id"] != city.id:
        return _other_city(c, task)
    return _muni_task(c, city, task, toast, alert)


async def open_job(c, job: str, toast=None, alert=False) -> Reply:
    return await {work.SNAPP: snapp, work.MCD: mcd, work.MUNI: muni}[job](c, toast, alert)


# =====================================================================================================================
#                                                       the buttons
# =====================================================================================================================
def _tid(tok) -> int | None:
    return int(tok) if isinstance(tok, str) and _ID.match(tok) else None


def _plain(text: str) -> str:
    return mapui.plain(text)


ARGS = {"h": 0, "m": 0, "o": 1, "mc": 1, "x": 1, "sr": 0, "sd": 1, "sp": 1, "ms": 0, "my": 0, "mg": 0, "na": 2, "ng": 1, "nd": 1}      # pieces between the action and the player id


async def handle(c) -> Reply:
    """Every `jb:` button. `c.args` = [action, ..., player id]; the presser was already checked against the trailing id."""
    lang, db, uid = c.lang, c.db, c.uid
    rest = c.args[:-1]
    act = rest[0] if rest else "h"
    arg = rest[1:]
    now = core.now()

    def toast(key: str, **kw) -> str:
        return _plain(t(lang, key, **kw))

    if len(arg) != ARGS.get(act, -1):                              # a button has exactly the pieces the bot drew into it: anything else is hand-made
        return Reply(toast=toast("job.stale"), alert=True)
    if act == "h":
        return await board(c)
    if act == "m":                                                # from the map: the board goes in a NEW message under the map (the map picture stays)
        r = await board(c)
        r.edit = False
        return r
    if act == "o":
        job = arg[0] if arg else ""
        if job not in work.JOB_IDS:
            return Reply(toast=toast("job.stale"), alert=True)
        return await open_job(c, job)
    try:
        city = await city_of(c)
    except NoCity as e:
        return Reply(toast=toast(e.key), alert=True)
    if act == "mc":                                               # claiming a wage needs no city (it works in a private chat too)
        tid = _tid(arg[0] if arg else None)
        rc = work.mcd_claim(db, uid, tid, now) if tid is not None else None
        if rc is None:
            raise GameError("job_stale")
        text = t(lang, "mcd.claimed", wage=fmt.usd(rc["wage"], lang), balance=fmt.usd(rc["balance"], lang))
        pic = _scene(c, "mcd_done", t(lang, "scene.mcd.done.t"), "+" + fmt.usd(rc["wage"], lang))
        return R(c, text, [[(t(lang, "mcd.b.again"), f"jb:o:mcd:{uid}")], [(t(lang, "job.b.board"), f"jb:h:{uid}"), (t(lang, "job.b.wallet"), f"cs:w:{uid}")]], pic=pic)
    if act == "x":                                                # giving a task up works from anywhere
        tid = _tid(arg[0] if arg else None)
        task = work.get_task(db, tid, uid) if tid is not None else None
        if task is None or task["state"] != "open":
            return await board(c, toast=toast("job.stale"))
        if work.cancel(db, uid, tid, now):
            return await board(c, toast=toast("job.cancelled"))
        return await board(c, toast=toast("job.stale"))
    if city is None:
        return Reply(toast=toast("job.private_only"), alert=True)

    if act == "sr":                                               # Snapp: take a ride
        task = work.snapp_request(db, city, uid, now)
        return _snapp_task(c, city, task, toast=toast("snapp.assigned"))
    if act in ("sd", "sp"):                                       # Snapp: drive to the next stop / pick the passenger up
        tid = _tid(arg[0] if arg else None)
        if tid is None:
            raise GameError("job_stale")
        if act == "sd":
            try:
                task = work.snapp_go(db, city, uid, tid, now)
            except GameError as e:
                if e.code == "job_unreachable":
                    return await snapp(c, toast=_plain(t(lang, "e.job_unreachable")), alert=True)
                raise
            st = presence.stand(db, city, uid)
            eta = fmt.dur(st.eta, lang) if st.trip is not None else ""
            return _snapp_task(c, city, task, toast=toast("snapp.started", eta=eta) if st.trip is not None else None)
        task = work.snapp_pickup(db, city, uid, tid, now)
        return _snapp_task(c, city, task, toast=toast("snapp.picked"))
    if act == "ms":                                               # McDonald's: the confirmation before an 8-hour shift
        task = work.current(db, uid)
        if task is not None and task["job"] == work.MCD:
            return _mcd_task(c, task, toast=toast("mcd.already"))
        st = presence.stand(db, city, uid)
        if city.mcd_id is None or st.trip is not None or st.row["at_place"] != city.mcd_id:
            raise GameError("job_far" if city.mcd_id else "job_nomcd")
        work.check_start(db, uid, work.MCD, now)
        return _mcd_confirm(c)
    if act == "my":
        task, started = work.mcd_start(db, city, uid, now)
        return _mcd_task(c, task, toast=toast("mcd.started" if started else "mcd.already"))
    if act == "mg":                                               # McDonald's: walk there (the ordinary walk)
        mid = city.mcd_id
        if mid is None:
            raise GameError("job_nomcd")
        out = presence.start_trip(db, city, uid, mid, now)
        if out.status == "busy" and not out.same:
            raise GameError("job_walking")
        if out.status == "unreachable":
            raise GameError("job_unreachable")
        return await mcd(c, toast=toast("mcd.walk_started"))
    if act == "na":                                               # municipal: accept an offer (kind + round are the only things the button carries)
        kind = arg[0] if arg else ""
        rnd = _tid(arg[1] if len(arg) > 1 else None)
        task = work.muni_accept(db, city, uid, kind, rnd, now)
        return _muni_task(c, city, task, toast=toast("muni.accepted"))
    if act in ("ng", "nd"):
        tid = _tid(arg[0] if arg else None)
        if tid is None:
            raise GameError("job_stale")
        if act == "ng":
            try:
                out = work.muni_go(db, city, uid, tid, now)
            except GameError as e:
                if e.code == "job_unreachable":
                    return await muni(c, toast=_plain(t(lang, "e.job_unreachable")), alert=True)
                raise
            task = work.get_task(db, tid, uid)
            if out.status == "busy" and not out.same:
                raise GameError("job_walking")
            return _muni_task(c, city, task, toast=toast("muni.walk_started") if out.status == "started" else None)
        rc = work.muni_complete(db, city, uid, tid, now)
        text = t(lang, "muni.done", emoji=KIND_EMOJI[rc["kind"]], name=t(lang, f"muni.kind.{rc['kind']}"), reward=fmt.usd(rc["reward"], lang), balance=fmt.usd(rc["balance"], lang))
        pic = _scene(c, "muni_done", t(lang, "scene.muni.done.t"), "+" + fmt.usd(rc["reward"], lang), muni_kind=rc["kind"])
        return R(c, text, [[(t(lang, "muni.b.next"), f"jb:o:muni:{uid}")], [(t(lang, "job.b.board"), f"jb:h:{uid}"), (t(lang, "job.b.wallet"), f"cs:w:{uid}")]], pic=pic)
    return Reply(toast=toast("job.stale"), alert=True)
