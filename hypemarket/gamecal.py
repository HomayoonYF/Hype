"""The game's accelerated calendar (v0.19).

One game year is four real days (`config.GAME_YEAR_DAYS`). Time in the game is never counted by a background job or by how often somebody opens
the bot: a thing that ages keeps ONE saved anchor (a UTC timestamp in the repository's usual form, whole seconds) and its age is worked out from the
server's clock whenever somebody asks. That makes the result independent of restarts, of long silences, of Telegram's message timestamps and of the
player's own clock or time zone.

    age = start_age + floor((now - anchor) / (4 days))

This module is the only place that formula lives, so education, work, family and the like can reuse it later. Nothing here touches the database.
"""
from __future__ import annotations

from . import config as C

DAY_SECONDS = 24 * 60 * 60


def year_seconds() -> int:
    """Real seconds in one game year (four days by default)."""
    return int(C.GAME_YEAR_DAYS) * DAY_SECONDS


def whole_years(anchor: int, now: int) -> int:
    """Complete game years between the anchor and `now` (0 while `now` is before the anchor, e.g. after a clock correction)."""
    for v in (anchor, now):
        if not isinstance(v, int) or isinstance(v, bool):
            raise TypeError("timestamps are whole seconds")
    return max(0, now - anchor) // year_seconds()


def age_at(anchor: int, now: int, start_age: int | None = None) -> int:
    """The age of something that began at `start_age` (default: a new character's 15) at `anchor`, as of `now`."""
    return (C.CHARACTER_START_AGE if start_age is None else int(start_age)) + whole_years(anchor, now)

