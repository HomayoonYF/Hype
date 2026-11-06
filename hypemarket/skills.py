"""Character skills (v0.211): ONE reusable progression for every job, present and future.

A skill goes from 0 to 1000 (`config.SKILL_MAX`). It is earned only by WORK THAT WAS COMPLETED: a job calls `grant_work` when a shift, a cotton session or a tailoring order is finished
and collected, with a stable source and reference (the task id), so pressing a button again, a retry or a race can never pay a second time. Nothing else raises a skill.

* The unit of progress is WORK, stored in thousandths (`work`, an integer). What one unit means is per skill (`config.SKILL_WORK`): one completed McDonald's shift, ten minutes of finished cotton
  work, one settled sale ... and `SKILL_WORK[skill]` units reach 1000.
* The level is NOT stored; one curve turns work into a level: `level = 1000 * (work / SKILL_WORK) ** (1 / 1.8)`: the first levels come quickly, each next one takes more, and the last ones take most of
  the time (diminishing returns). With 1000 shifts as the McDonald's benchmark: level 100 needs about 16 shifts, 500 about 290, 800 about 670, 900 about 830, 1000 all 1000.
* Titles are milestones of the level (`config.SKILL_TITLES`): beginner, trainee, skilled, experienced, professional, expert, master.
* Character XP is a different thing (charxp.py) and is granted next to the skill by the same job, with its own (source, reference); stock XP, net worth and levels of the stock game never meet this module.
* `character_skills.work` can only go up (a trigger); `skill_log` is append-only and UNIQUE(player, skill, source, reference).
"""
from __future__ import annotations

import math
import sqlite3
from dataclasses import dataclass

from . import config as C, core
from .core import GameError

MAX_WORK_GRANT = 10 ** 7                       # thousandths: one grant is at most 10,000 units, so no value can overflow a total


@dataclass(frozen=True)
class SkillDef:
    id: str
    fa: str
    en: str
    icon: str


SKILLS: dict = {}


def register(sid: str, fa: str, en: str, icon: str) -> SkillDef:
    if sid in SKILLS:
        raise ValueError(f"skill {sid!r} is already registered")
    if sid not in C.SKILL_WORK:
        raise ValueError(f"skill {sid!r} has no entry in config.SKILL_WORK")
    SKILLS[sid] = SkillDef(sid, fa, en, icon)
    return SKILLS[sid]


register("mcdonalds", "مک‌دونالد", "McDonald's", "🍔")
register("textile", "نساجی", "Textile", "🧵")
register("tailoring", "خیاطی", "Tailoring", "🪡")
register("accounting", "حسابداری", "Accounting", "🧾")
register("warehouse", "انبارداری", "Warehouse", "📦")
register("production", "تولید", "Production", "🏭")

TITLES_FA = {"beginner": "مبتدی", "trainee": "کارآموز", "skilled": "ماهر", "experienced": "باتجربه", "professional": "حرفه‌ای", "expert": "متخصص", "master": "استاد"}
TITLES_EN = {"beginner": "Beginner", "trainee": "Trainee", "skilled": "Skilled", "experienced": "Experienced", "professional": "Professional", "expert": "Expert", "master": "Master"}


def _def(skill: str) -> SkillDef:
    d = SKILLS.get(skill)
    if d is None:
        raise ValueError(f"unknown skill {skill!r}")
    return d


# =====================================================================================================================
#                                                       the curve
# =====================================================================================================================
def total_work(skill: str) -> int:
    """The work (thousandths of a unit) that reaches level 1000."""
    return C.SKILL_WORK[_def(skill).id] * 1000


def need(skill: str, level: int) -> int:
    """The work at which `level` begins (level 0 begins at 0, level 1000 at the full amount)."""
    if not isinstance(level, int) or isinstance(level, bool) or not 0 <= level <= C.SKILL_MAX:
        raise ValueError("a level is a whole number from 0 to 1000")
    if level == 0:
        return 0
    return math.ceil(total_work(skill) * (level / C.SKILL_MAX) ** C.SKILL_CURVE_POWER - 1e-9)


def level_for(skill: str, work: int) -> int:
    """The level `work` reaches: the ONLY curve (never above 1000)."""
    if not isinstance(work, int) or isinstance(work, bool) or work < 0:
        raise ValueError("work is a non-negative integer")
    if work >= total_work(skill):
        return C.SKILL_MAX
    lvl = int(C.SKILL_MAX * (work / total_work(skill)) ** (1.0 / C.SKILL_CURVE_POWER))
    lvl = min(C.SKILL_MAX, max(0, lvl))
    while lvl < C.SKILL_MAX and need(skill, lvl + 1) <= work:
        lvl += 1
    while lvl > 0 and need(skill, lvl) > work:
        lvl -= 1
    return lvl


def title_id(level: int) -> str:
    out = C.SKILL_TITLES[0][1]
    for start, tid in C.SKILL_TITLES:
        if level >= start:
            out = tid
    return out


def title(lang: str, level: int) -> str:
    return (TITLES_EN if lang == "en" else TITLES_FA).get(title_id(level), title_id(level))


def name(lang: str, skill: str) -> str:
    d = _def(skill)
    return d.en if lang == "en" else d.fa


def next_title_level(level: int) -> int | None:
    for start, _ in C.SKILL_TITLES:
        if start > level:
            return start
    return None


def bonus_bps(level: int, max_bps: int) -> int:
    """A bonus that grows linearly with the level: 0 at level 0, `max_bps` at 1000 (whole basis points, rounded down)."""
    return max(0, min(C.SKILL_MAX, level)) * max_bps // C.SKILL_MAX


# =====================================================================================================================
#                                                       reading and granting
# =====================================================================================================================
def work_of(db, uid: int, skill: str) -> int:
    _def(skill)
    r = db.q1("SELECT work FROM character_skills WHERE user_id=? AND skill=?", (uid, skill))
    return r["work"] if r else 0


def level_of(db, uid: int, skill: str) -> int:
    return level_for(skill, work_of(db, uid, skill))


def card(db, uid: int, skill: str) -> dict:
    """Everything a screen shows of one skill: level, title, work, progress to the next level (0..1) and the next title's level."""
    w = work_of(db, uid, skill)
    lvl = level_for(skill, w)
    lo = need(skill, lvl)
    hi = need(skill, lvl + 1) if lvl < C.SKILL_MAX else lo
    frac = 1.0 if lvl >= C.SKILL_MAX else (w - lo) / max(1, hi - lo)
    return {"skill": skill, "level": lvl, "title": title_id(lvl), "work": w, "progress": frac, "next_title": next_title_level(lvl)}


def all_cards(db, uid: int) -> list:
    return [card(db, uid, s) for s in SKILLS]


def milli(seconds: int) -> int:
    """The work (thousandths) of `seconds` of timed work: ten minutes are one unit."""
    return max(1, round(seconds * 1000 / C.WORK_UNIT_SECONDS))


def grant_work(db, uid: int, skill: str, work: int, source: str, ref: str, now: int | None = None) -> tuple | None:
    """Add `work` (thousandths of a unit, > 0) to the player's skill, once per (source, ref). Returns (level before, level after), or None when this (source, ref) was already granted (nothing changes).
    GameError('no_user') for an unknown player or a bot account; ValueError for a bad amount, source or reference."""
    _def(skill)
    if not isinstance(work, int) or isinstance(work, bool) or not 0 < work <= MAX_WORK_GRANT:
        raise ValueError("a work grant is a positive whole number of thousandths")
    if not (isinstance(source, str) and isinstance(ref, str) and source.strip() and ref.strip() and len(source) <= 40 and len(ref) <= 80):
        raise ValueError("a grant needs a source and a reference")
    now = core.now() if now is None else now
    with db.savepoint():
        u = core.get_user(db, uid)
        if u is None or u["bot"]:
            raise GameError("no_user")
        before = work_of(db, uid, skill)
        cap = total_work(skill)
        after = min(cap, before + work)
        try:
            db.x("INSERT INTO skill_log(user_id,skill,source,ref,delta,work_after,created_at) VALUES(?,?,?,?,?,?,?)", (uid, skill, source, ref, after - before, after, now))
        except sqlite3.IntegrityError:                                   # the same grant again: already applied
            return None
        db.x("INSERT INTO character_skills(user_id,skill,work,updated_at) VALUES(?,?,?,?) ON CONFLICT(user_id,skill) DO UPDATE SET work=excluded.work, updated_at=excluded.updated_at", (uid, skill, after, now))
        return level_for(skill, before), level_for(skill, after)
