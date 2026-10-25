"""v0.18.1: the daily bonus resets at 00:00 Tehran time (UTC+03:30), not N hours after the player's last claim."""
import datetime as dt

import pytest

from hypemarket import config as C, core, progress, quests
from hypemarket.core import GameError
from hypemarket.db import Database

TEHRAN = dt.timezone(dt.timedelta(hours=3, minutes=30))


def at(y, m, d, hh=0, mm=0, ss=0) -> int:
    """The epoch second of a Tehran wall-clock time."""
    return int(dt.datetime(y, m, d, hh, mm, ss, tzinfo=TEHRAN).timestamp())


@pytest.fixture()
def clock():
    box = [at(2026, 10, 22, 12)]
    core.set_clock(lambda: box[0])
    return box


@pytest.fixture()
def db(clock):
    d = Database(":memory:")
    progress.ensure_season(d)
    core.ensure_user(d, 1, "u1", "U1")
    yield d
    d.close()


def test_the_day_number_changes_exactly_at_midnight_in_tehran():
    assert C.daily_day(at(2026, 10, 22, 23, 59, 59)) + 1 == C.daily_day(at(2026, 10, 23, 0, 0, 0))
    assert C.daily_day(at(2026, 10, 23, 0, 0, 0)) == C.daily_day(at(2026, 10, 23, 23, 59, 59))
    assert C.daily_next_reset(at(2026, 10, 22, 13, 30)) == at(2026, 10, 23, 0, 0, 0)
    assert C.daily_next_reset(at(2026, 10, 23, 0, 0, 0)) == at(2026, 10, 24, 0, 0, 0)          # exactly at midnight the NEXT reset is a day away
    # 00:00 Tehran is 20:30 UTC of the previous day
    assert at(2026, 10, 23, 0, 0, 0) == int(dt.datetime(2026, 10, 22, 20, 30, tzinfo=dt.timezone.utc).timestamp())


def test_one_claim_per_tehran_day_whatever_the_hour(db, clock):
    clock[0] = at(2026, 10, 22, 23, 59, 50)                       # claim 10 seconds before midnight
    assert progress.daily(db, 1)["streak"] == 1
    assert progress.daily_preview(db, 1) is None
    clock[0] = at(2026, 10, 22, 23, 59, 59)
    with pytest.raises(GameError) as e:
        progress.daily(db, 1)
    assert e.value.code == "daily_wait" and e.value.kw["secs"] == 1
    clock[0] = at(2026, 10, 23, 0, 0, 0)                          # the very second the new day starts the bonus is open again (only 10 s later!)
    assert progress.daily_preview(db, 1)["streak"] == 2
    assert progress.daily(db, 1)["streak"] == 2


def test_a_late_claim_does_not_push_the_next_one_later(db, clock):
    clock[0] = at(2026, 10, 22, 23, 0)                            # claimed late in the evening ...
    progress.daily(db, 1)
    clock[0] = at(2026, 10, 23, 0, 5)                             # ... and again 65 minutes later, which is a new calendar day (the old 20 h rule said no)
    assert progress.daily(db, 1)["streak"] == 2
    clock[0] = at(2026, 10, 23, 23, 59)                           # the same day: nothing
    with pytest.raises(GameError) as e:
        progress.daily(db, 1)
    assert e.value.code == "daily_wait" and e.value.kw["secs"] == 60
    clock[0] = at(2026, 10, 24, 0, 0)
    assert progress.daily(db, 1)["streak"] == 3


def test_the_wait_is_the_time_left_until_midnight(db, clock):
    clock[0] = at(2026, 10, 22, 9, 0)
    progress.daily(db, 1)
    for hh, mm, expect in ((9, 0, 15 * 3600), (14, 30, 9 * 3600 + 30 * 60), (23, 0, 3600)):
        clock[0] = at(2026, 10, 22, hh, mm)
        with pytest.raises(GameError) as e:
            progress.daily(db, 1)
        assert e.value.kw["secs"] == expect


def test_skipping_a_whole_day_ends_the_streak(db, clock):
    clock[0] = at(2026, 10, 20, 8)
    progress.daily(db, 1)
    clock[0] = at(2026, 10, 21, 22)
    assert progress.daily(db, 1)["streak"] == 2
    clock[0] = at(2026, 10, 23, 0, 1)                             # the 22nd was skipped completely
    assert progress.daily(db, 1)["streak"] == 1


def test_a_claim_made_under_the_old_rules_is_read_by_the_new_ones(db, clock):
    """Existing players keep their `last_daily` and `streak`: only the way they are read changed."""
    db.x("UPDATE users SET last_daily=?, streak=? WHERE id=1", (at(2026, 10, 22, 6), 4))     # claimed yesterday morning, 4 days in a row
    clock[0] = at(2026, 10, 22, 12)
    with pytest.raises(GameError):
        progress.daily(db, 1)                                     # that was today (Tehran), still claimed
    db.x("UPDATE users SET last_daily=?, streak=? WHERE id=1", (at(2026, 10, 21, 6), 4))
    assert progress.daily(db, 1)["streak"] == 5                   # yesterday's claim: the streak goes on
    db.x("UPDATE users SET last_daily=?, streak=? WHERE id=1", (at(2026, 10, 19, 6), 9))
    clock[0] = at(2026, 10, 23, 12)
    assert progress.daily(db, 1)["streak"] == 1                   # older than yesterday: starts again


def test_a_never_claimed_player_can_claim_and_the_quest_streak_follows(db, clock):
    assert progress.daily_preview(db, 1) is not None
    u = core.need_user(db, 1)
    assert quests._snapshot_value(db, u, "streak") == 0
    progress.daily(db, 1)
    clock[0] = at(2026, 10, 23, 15)
    assert quests._snapshot_value(db, core.need_user(db, 1), "streak") == 1          # claimed yesterday: the streak is still alive today
    clock[0] = at(2026, 10, 24, 15)
    assert quests._snapshot_value(db, core.need_user(db, 1), "streak") == 0          # a day was missed: it is over


def test_the_zone_is_adjustable_and_defaults_to_tehran(monkeypatch):
    assert C.daily_utc_offset() == 210
    monkeypatch.setenv("DAILY_UTC_OFFSET_MIN", "0")
    assert C.daily_next_reset(at(2026, 10, 22, 12)) == int(dt.datetime(2026, 10, 23, tzinfo=dt.timezone.utc).timestamp())
    monkeypatch.setenv("DAILY_UTC_OFFSET_MIN", "garbage")
    assert C.daily_utc_offset() == 210                            # junk falls back to the default, never crashes
    monkeypatch.setenv("DAILY_UTC_OFFSET_MIN", "99999")
    assert C.daily_utc_offset() == 840                            # and out-of-range values are clamped


def test_the_wait_message_names_midnight_in_both_languages(db, clock):
    from hypemarket import handlers, fmt
    progress.daily(db, 1)
    clock[0] = at(2026, 10, 22, 22, 30)
    with pytest.raises(GameError) as e:
        progress.daily(db, 1)
    fa, en = handlers.err_text("fa", e.value), handlers.err_text("en", e.value)
    assert "۰۰:۰۰" in fa and "تهران" in fa and "1" in en and "00:00" in en and "Tehran" in en
    assert fmt.dur(e.value.kw["secs"], "en") in en
