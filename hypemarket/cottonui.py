"""The cotton job as the player sees it (v0.211): the fourth entry of the job board («مشاغل»), `jb:o:cotton`, and its buttons `jb:cs|cg|cc|cf|cq`.

    home ─┬─ at the park: one button per unlocked grade (the confirmation IS the saved terms: time and pieces at the player's current skill)
          ├─ elsewhere: 🚶 the ordinary walk to the park (a server-side trip, the picture is the walk like every other job's)
          ├─ a batch running: bar + time left, 🔄 refresh, ❌ give up   ·   a batch due: 🧺 collect
          └─ 🧵 my fabric ── sell to the game at the fixed price of each quality / 📢 the ad board

The rules are textile.py's (the site, the terms, start and claim, the fabric and its sale); the position is read from the SERVER (presence), never from a button.
"""
from __future__ import annotations

from . import bizui, cash, config as C, core, fmt, items, jobviz, presence, skills, textile, work, workui
from .core import GameError
from .texts import t
from .views import Reply

JOB = work.COTTON


def _site_text(lang: str, city) -> str:
    sid = textile.site_id(city)
    spot = city.spot(sid) if sid else None
    return workui._place(lang, city, sid) if spot else (fmt.esc(sid) if sid else "—")


def _row_grade(lang: str, db, uid: int, g: int) -> str:
    tm = textile.terms(db, uid, g)
    return t(lang, "cot.grade", stars=items.stars(g), time=fmt.dur(tm["seconds"], lang), units=fmt.num(tm["units"], lang), need=fmt.num(tm["need"], lang),
             lock="" if tm["open"] else " 🔒")


def _head(c, city) -> str:
    lang, db, uid = c.lang, c.db, c.uid
    lvl = skills.level_of(db, uid, "textile")
    return t(lang, "cot.head", place=_site_text(lang, city), level=fmt.num(lvl, lang), title=skills.title(lang, lvl), fabric=fmt.num(textile.fabric_count(db, uid), lang))


async def screen(c, toast: str | None = None, alert: bool = False) -> Reply:
    """`jb:o:cotton` and «پنبه»: the batch if there is one (a status needs no city), else the home with the grades."""
    lang, db, uid = c.lang, c.db, c.uid
    now = core.now()
    task = textile.current(db, uid)
    if task is not None:
        try:
            city, st = await bizui.where(c)
        except bizui.NoWhere:
            city = st = None
        return _task(c, task, city, st, toast, alert, now)
    other = work.current(db, uid)
    if other is not None:
        return workui.R(c, t(lang, "job.busy_other", job=workui._name(lang, other["job"])), [[(t(lang, "job.b.board"), f"jb:h:{uid}")]], toast=toast, alert=alert)
    try:
        city, st = await bizui.where(c)
    except bizui.NoWhere as e:
        return e.reply
    sid = textile.site_id(city)
    head = _head(c, city)
    if sid is None:
        return workui.R(c, head + "\n\n" + t(lang, "cot.nosite"), [[(t(lang, "job.b.board"), f"jb:h:{uid}")]], toast=toast, alert=alert)
    lines = [head, "", t(lang, "cot.grades")]
    lines += [_row_grade(lang, db, uid, g) for g in textile.grades()]
    rows = []
    pic = None
    here = st.trip is None and st.row["at_place"] == sid
    if here:
        lines += ["", t(lang, "cot.here")]
        opened = textile.unlocked(db, uid)
        for g in opened:
            tm = textile.terms(db, uid, g)
            rows.append([(t(lang, "cot.b.start", stars=items.stars(g), time=fmt.dur(tm["seconds"], lang), units=fmt.num(tm["units"], lang)), f"jb:cs:{g}:{uid}")])
    elif st.trip is not None and st.trip["dest_id"] == sid:
        lines += ["", t(lang, "cot.walking", bar=fmt.bar(st.progress), pct=fmt.num(round(st.progress * 100), lang), left=fmt.dist(workui._left(st), lang), eta=fmt.dur(st.eta, lang))]
        rows.append([(t(lang, "job.b.refresh"), f"jb:o:cotton:{uid}")])
        pic = jobviz.map_pic(city, lang, lambda: jobviz.walk_marks(lang, city, st, sid))
    else:
        spot = city.spot(sid)
        d = city.net.field(st.pos).to(spot.door) if spot else 0
        lines += ["", t(lang, "cot.far", d=fmt.dist(d, lang), eta=fmt.dur(workui._eta(d, C.walk_speed()), lang), speed=fmt.kmh(C.walk_speed(), lang))]
        if st.trip is None:
            rows.append([(t(lang, "cot.b.go"), f"jb:cg:{uid}")])
        else:
            lines.append(t(lang, "mcd.busy_walk"))
            rows.append([(t(lang, "job.b.refresh"), f"jb:o:cotton:{uid}")])
        pic = jobviz.map_pic(city, lang, lambda: jobviz.walk_marks(lang, city, st, sid))
    rows.append([(t(lang, "cot.b.fabric"), f"jb:cf:{uid}"), (t(lang, "bz.b.ads"), f"bz:ab:1:{uid}")])
    rows.append([(t(lang, "job.b.board"), f"jb:h:{uid}"), (t(lang, "bz.b.hub"), f"bz:h:{uid}")])
    return workui.R(c, "\n".join(lines), rows, toast=toast, alert=alert, pic=pic)


def _task(c, task, city, st, toast, alert, now: int) -> Reply:
    lang, uid = c.lang, c.uid
    d = work.data_of(task)
    grade, units, tid = int(d.get("grade", 1)), int(task["reward"]), task["id"]
    s = textile.state(task, now)
    total = max(1, int(task["due_at"]) - int(task["started_at"]))
    frac = min(1.0, max(0.0, (now - int(task["started_at"])) / total))
    head = t(lang, "cot.task", stars=items.stars(grade), units=fmt.num(units, lang))
    rows = []
    if s == "ready":
        text = head + "\n\n" + t(lang, "cot.ready", units=fmt.num(units, lang), stars=items.stars(grade))
        rows.append([(t(lang, "cot.b.collect"), f"jb:cc:{tid}:{uid}")])
    else:
        text = head + "\n\n" + t(lang, "cot.working", bar=fmt.bar(frac), pct=fmt.num(round(frac * 100), lang), left=fmt.dur(max(0, int(task["due_at"]) - now), lang))
        if city is not None and st is not None and (st.trip is not None or st.row["at_place"] != d.get("place")):
            text += "\n" + t(lang, "cot.stay")
        rows.append([(t(lang, "job.b.refresh"), f"jb:o:cotton:{uid}"), (t(lang, "cot.b.giveup"), f"jb:x:{tid}:{uid}")])
    rows.append([(t(lang, "job.b.board"), f"jb:h:{uid}"), (t(lang, "bz.b.hub"), f"bz:h:{uid}")])
    return workui.R(c, text, rows, toast=toast, alert=alert)


async def start(c, grade: int) -> Reply:
    lang = c.lang
    city, st = await bizui.where(c)
    task, started = textile.start(c.db, city, c.uid, grade, core.now())
    r = _task(c, task, city, presence.stand(c.db, city, c.uid), None, False, core.now())
    r.toast = workui._plain(t(lang, "cot.started" if started else "cot.already"))
    return r


async def walk(c) -> Reply:
    lang = c.lang
    city, st = await bizui.where(c)
    sid = textile.site_id(city)
    if sid is None:
        raise GameError("cotton_nosite")
    out = presence.start_trip(c.db, city, c.uid, sid, core.now())
    if out.status == "busy" and not out.same:
        raise GameError("job_walking")
    if out.status == "unreachable":
        raise GameError("job_unreachable")
    return await screen(c, toast=workui._plain(t(lang, "cot.walk_started")))


def claim(c, tid: int) -> Reply:
    lang, db, uid = c.lang, c.db, c.uid
    t0 = work.get_task(db, tid, uid)
    grade = int(work.data_of(t0).get("grade", 1)) if t0 is not None and t0["job"] == JOB else 1
    rc = textile.claim(db, uid, tid, core.now())
    text = t(lang, "cot.claimed", units=fmt.num(rc["units"], lang), stars=items.stars(grade), total=fmt.num(rc["fabric"], lang))
    if rc.get("xp"):
        text += "\n" + t(lang, "cot.claimed.xp", xp=fmt.num(rc["xp"], lang))
    if rc.get("level_to", 0) > rc.get("level_from", 0):
        text += "\n" + t(lang, "cot.levelup", level=fmt.num(rc["level_to"], lang), title=skills.title(lang, rc["level_to"]))
    rows = [[(t(lang, "cot.b.again"), f"jb:o:cotton:{uid}"), (t(lang, "cot.b.fabric"), f"jb:cf:{uid}")], [(t(lang, "job.b.board"), f"jb:h:{uid}"), (t(lang, "bz.b.hub"), f"bz:h:{uid}")]]
    return workui.R(c, text, rows)


def fabric(c, toast: str | None = None) -> Reply:
    """The player's fabric by quality and what the game pays for each (a fixed price): sell some, or take it to the ad board."""
    lang, db, uid = c.lang, c.db, c.uid
    have = textile.fabric_by_quality(db, uid)
    lines = [t(lang, "cot.fab.head", cash=fmt.usd(cash.balance(db, uid), lang), n=fmt.num(sum(have.values()), lang))]
    rows = []
    for q in sorted(have, reverse=True):
        n = have[q]
        if n <= 0:
            continue
        price = C.FABRIC_GAME_CENTS.get(q)
        lines.append(t(lang, "cot.fab.line", stars=items.stars(q) if q else t(lang, "cot.fab.noq"), n=fmt.num(n, lang), price=fmt.usd(price, lang) if price else "—"))
        if q in C.FABRIC_GAME_CENTS:
            row = [(t(lang, "cot.b.sell", n=fmt.num(k, lang), stars=items.stars(q)), f"jb:cq:{q}:{k}:{uid}") for k in (1, 10) if k < n]
            row.append((t(lang, "cot.b.sellall", n=fmt.num(n, lang), stars=items.stars(q)), f"jb:cq:{q}:0:{uid}"))
            rows.append(row)
    if not sum(have.values()):
        lines.append(t(lang, "cot.fab.none"))
    rows.append([(t(lang, "bz.b.ads"), f"bz:ab:1:{uid}"), (t(lang, "cot.b.back"), f"jb:o:cotton:{uid}")])
    return workui.R(c, "\n".join(lines), rows, toast=toast)


def sell(c, q: int, n: int) -> Reply:
    lang, db, uid = c.lang, c.db, c.uid
    have = textile.fabric_by_quality(db, uid).get(q, 0)
    n = have if n == 0 else n
    if n <= 0:
        raise GameError("inv_few", have=have, need=1)
    out = textile.sell_to_game(db, uid, q, n, core.now())
    return fabric(c, toast=workui._plain(t(lang, "cot.sold", n=fmt.num(out["qty"], lang), stars=items.stars(q), cents=fmt.usd(out["cents"], lang), balance=fmt.usd(out["balance"], lang))))
