"""v0.18.2: the jobs are a compact, text-first list, and every job dashboard shows only what the game really tracks: no invented XP, skill levels,
percentages of unknown things or task steps; pictures only where a map or a route is the point."""
import re

import pytest

from test_life_flow import (A, B, CH, alerts, arrive, bal, city, data_of, find, h, layout, press, screen, sent, text_of, walk_to_mcd)  # noqa: F401 (fixtures)
from test_v0181_pictures import is_map, photo_of
from hypemarket import config as C, fmt, work
from hypemarket.texts import FA, EN, t

INVENTED = re.compile(r"XP|تجربه|مهارت|سطح|رتبه‌ی شغل|level|skill|experience|rank", re.I)


def collect_everything(h):
    """Walk every job through all its states and return [(name, text, buttons, photo)] of each screen the player saw."""
    seen = []

    def see(name, out):
        text, datas = screen(out)
        seen.append((name, text, datas, photo_of(out)))
        return out
    see("board idle", h.say(A, "مشاغل", chat=CH))
    see("snapp desk", h.say(A, "اسنپ", chat=CH))
    see("snapp assigned", press(h, A, f"jb:sr:{A}"))
    task = work.current(h.db, A)
    see("snapp to pickup", press(h, A, f"jb:sd:{task['id']}:{A}"))
    arrive(h, A)
    see("snapp at pickup", press(h, A, f"jb:o:snapp:{A}"))
    press(h, A, f"jb:sp:{task['id']}:{A}")
    see("snapp to dest", press(h, A, f"jb:o:snapp:{A}"))
    see("board on a ride", h.say(A, "مشاغل", chat=CH))
    arrive(h, A)
    see("snapp receipt", press(h, A, f"jb:o:snapp:{A}"))
    see("board after a ride", h.say(A, "مشاغل", chat=CH))
    walk_to_mcd(h, B)
    see("mcd door", h.say(B, "مک دونالد", chat=CH))
    see("mcd confirm", press(h, B, f"jb:ms:{B}"))
    see("mcd working", press(h, B, f"jb:my:{B}"))
    see("board on a shift", h.say(B, "مشاغل", chat=CH))
    h.advance(4 * 3600)
    see("mcd half", press(h, B, f"jb:o:mcd:{B}"))
    h.advance(4 * 3600)
    see("mcd ready", press(h, B, f"jb:o:mcd:{B}"))
    see("board shift ready", h.say(B, "مشاغل", chat=CH))
    task = work.current(h.db, B)
    see("mcd collected", press(h, B, f"jb:mc:{task['id']}:{B}"))
    return seen


def test_no_job_screen_shows_an_invented_statistic(h):
    seen = collect_everything(h)
    assert len(seen) == 17
    for name, text, datas, photo in seen:
        assert not INVENTED.search(text), (name, text)
        assert text.count("▰") + text.count("▱") in (0, 10), name                                               # a bar is always the same ten cells
        if "٪" in text or "%" in text:                                                                          # a percentage only ever belongs to a trip the game is tracking
            assert name in ("snapp to dest", "snapp to pickup"), (name, text)


def test_pictures_are_maps_only(h):
    for name, text, datas, photo in collect_everything(h):
        if photo is not None:
            assert name in ("snapp assigned", "snapp to pickup", "snapp at pickup", "snapp to dest") and is_map(photo), name
    out = h.say(A, "مک دونالد", chat=CH)                                                                       # walking to McDonald's is a route: a map
    assert is_map(photo_of(out))


def test_the_board_is_a_compact_text_list(h):
    out = h.say(A, "مشاغل", chat=CH)
    text, datas = screen(out)
    assert photo_of(out) is None and len(text) < 600
    blocks = text.split("\n\n")
    assert blocks[0].startswith("💼 <b>مشاغل</b>") and fmt.usd(C.START_CASH_CENTS if hasattr(C, "START_CASH_CENTS") else bal(h), "fa") in blocks[0]
    jobs = blocks[1:4]
    for block, icon, name in zip(jobs, ("🚕", "🍔", "🧹"), ("اسنپ", "مک‌دونالد", "شهرداری")):
        lines = block.split("\n")
        assert lines[0] == f"{icon} <b>{name}</b>" and len(lines) == 3 and lines[2].startswith("وضعیت:")      # icon + name, a short description, the status
        assert len(lines[1]) < 80
    assert datas == [f"jb:o:snapp:{A}", f"jb:o:mcd:{A}", f"jb:o:muni:{A}", f"cs:w:{A}", f"jb:h:{A}"]


def test_the_board_statuses_follow_the_real_state(h):
    def status(uid, job):
        text = screen(h.say(uid, "مشاغل", chat=CH))[0]
        return {"snapp": "🚕", "mcd": "🍔", "muni": "🧹"}[job], [b for b in text.split("\n\n") if b.startswith({"snapp": "🚕", "mcd": "🍔", "muni": "🧹"}[job])][0].split("\n")[2]
    assert all("🟢 آماده" in status(A, j)[1] for j in ("snapp", "mcd", "muni"))
    h.say(A, "اسنپ", chat=CH)
    press(h, A, f"jb:sr:{A}")
    assert "سفر" in status(A, "snapp")[1] or "در حال" in status(A, "snapp")[1]
    assert "کار دیگه‌ای فعاله" in status(A, "mcd")[1] and "کار دیگه‌ای فعاله" in status(A, "muni")[1]         # one job at a time, said once per job
    walk_to_mcd(h, B)
    press(h, B, f"jb:ms:{B}")
    press(h, B, f"jb:my:{B}")
    assert "در حال کار" in status(B, "mcd")[1] and "۸ ساعت مونده" in status(B, "mcd")[1]
    h.advance(8 * 3600)
    assert "دستمزد آماده‌ست" in status(B, "mcd")[1]


def test_the_mcdonalds_dashboard_is_the_saved_shift(h):
    walk_to_mcd(h, A)
    press(h, A, f"jb:ms:{A}")
    press(h, A, f"jb:my:{A}")
    task = work.current(h.db, A)
    text = screen(press(h, A, f"jb:o:mcd:{A}"))[0]
    for part in ("وضعیت:", "در حال کار", "دستمزد", fmt.usd(C.MCD_WAGE_CENTS, "fa"), fmt.clock(task["started_at"], "fa"), fmt.clock(task["due_at"], "fa"), "باقی‌مونده", "دریافت‌شده"):
        assert part in text, part
    h.advance(2 * 3600)
    text = screen(press(h, A, f"jb:o:mcd:{A}"))[0]
    assert "▰▰▱▱▱▱▱▱▱▱" in text and "۲ ساعت گذشته" in text and "۶ ساعت" in text                              # 2 of 8 hours: the bar is elapsed ÷ total of the saved shift
    assert bal(h) == 10_000                                                                                    # nothing is paid before the shift is over and collected


def test_the_snapp_dashboard_shows_estimate_and_earnings_apart(h):
    h.say(A, "اسنپ", chat=CH)
    text = screen(press(h, A, f"jb:sr:{A}"))[0]
    task = work.current(h.db, A)
    assert "کرایه‌ی پایه" in text and "(بعد از رسیدن)" in text and "اجاره‌ی ماشین" in text and "خالص بدون انعام" in text   # an estimate, labelled as one
    assert "پول نقد حالا" not in text and "سود خالص" not in text                                               # nothing is claimed as earned yet
    press(h, A, f"jb:sd:{task['id']}:{A}")
    arrive(h, A)
    press(h, A, f"jb:sp:{task['id']}:{A}")
    arrive(h, A)
    receipt = screen(press(h, A, f"jb:o:snapp:{A}"))[0]
    rc = work.receipt_of(h.db.q1("SELECT * FROM work_tasks WHERE id=?", (task["id"],)))
    assert "سود خالص" in receipt and fmt.usd(rc["net"], "fa") in receipt and "پول نقد حالا" in receipt and fmt.usd(rc["balance"], "fa") in receipt
    assert "(بعد از رسیدن)" not in receipt


def test_the_city_hall_dashboard_lists_real_tasks_and_their_rewards(h):
    text, datas = screen(h.say(A, "شهرداری", chat=CH))
    offers = [d for d in datas if d.startswith("jb:na:")]
    assert len(offers) == 3 and text.count("📏") == 3 and text.count("💰") == 3
    for kind, cents in C.MUNI_TASKS.items():
        assert fmt.usd(cents, "fa") in text
    accepted = screen(press(h, A, offers[0]))[0]
    task = work.current(h.db, A)
    assert fmt.usd(task["reward"], "fa") in accepted and "km/h" in accepted and "متر" in accepted
    press(h, A, f"jb:ng:{task['id']}:{A}")
    arrive(h, A)
    at_site = screen(press(h, A, f"jb:o:muni:{A}"))[0]
    assert "رسیدی" in at_site
    done = screen(press(h, A, f"jb:nd:{task['id']}:{A}"))[0]
    assert fmt.usd(task["reward"], "fa") in done and fmt.usd(bal(h), "fa") in done


def test_job_texts_have_no_scene_or_character_keys_left():
    for table in (FA, EN):
        assert not [k for k in table if k.startswith("scene.")]
    assert FA["job.busy_other"] and "{job}" in FA["job.busy_other"]
