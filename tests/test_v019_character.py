"""v0.19: the character as data: citizen IDs, names, the age formula, traits and genes, character XP, the database's own guards and the schema-9 -> 10 migration."""
import os
import random
import re
import sqlite3
import threading
import time
from pathlib import Path

import pytest

import charkit
from hypemarket import character, charxp, config as C, core, gamecal, traits
from hypemarket.core import GameError
from hypemarket.db import Database, SCHEMA_VERSION

ROOT = Path(__file__).resolve().parent.parent
DAY = 86400
T0 = 1_760_000_000


@pytest.fixture()
def db():
    d = Database(":memory:")
    yield d
    d.close()


def user(db, uid, bot=False):
    core.ensure_user(db, uid, f"u{uid}", f"User {uid}")
    if bot:
        db.x("UPDATE users SET bot=1 WHERE id=?", (uid,))
    return uid


def make(db, uid, *, now=T0, rng=None, first="علی", last="رضایی", gender="male"):
    user(db, uid)
    character.ensure_profile(db, uid, now, rng)
    charkit.complete(db, uid, first=first, last=last, gender=gender, now=now)
    return character.get(db, uid)


# ================================================================================================================ schema
def test_a_fresh_database_has_every_v019_table_and_the_counter_at_zero(db):
    names = {r["name"] for r in db.qa("SELECT name FROM sqlite_master WHERE type='table'")}
    assert {"characters", "character_genes", "character_traits", "character_xp", "character_xp_log", "citizen_seq"} <= names
    assert db.q1("PRAGMA user_version")[0] == SCHEMA_VERSION == 11
    assert dict(db.q1("SELECT * FROM citizen_seq")) == {"id": 1, "next_id": 0}


# ================================================================================================================ identity & citizen IDs
def test_one_character_per_user_and_ensure_is_idempotent(db):
    user(db, 500)
    a = character.ensure_profile(db, 500, T0)
    b = character.ensure_profile(db, 500, T0 + 99)
    assert a["citizen_id"] == b["citizen_id"] == 0 and a["created_at"] == b["created_at"] == T0
    assert db.q1("SELECT COUNT(*) c FROM characters")["c"] == 1
    assert db.q1("SELECT next_id FROM citizen_seq")["next_id"] == 1                 # the counter moved once, not twice
    assert db.q1("SELECT COUNT(*) c FROM character_genes WHERE user_id=500")["c"] == len(traits.TRAIT_NAMES)
    assert db.q1("SELECT COUNT(*) c FROM character_traits WHERE user_id=500")["c"] == len(traits.TRAIT_NAMES)
    assert charxp.get(db, 500) == 0 and charxp.level_of(db, 500) == 1


def test_citizen_ids_start_at_zero_are_sequential_and_not_the_telegram_id(db):
    got = {}
    for uid in (900_000_007, 3, 41, 12, 777_777_777):                               # creation order, deliberately not sorted
        user(db, uid)
        got[uid] = character.ensure_profile(db, uid, T0)["citizen_id"]
    assert list(got.values()) == [0, 1, 2, 3, 4]
    assert all(cid != uid for uid, cid in got.items())
    assert character.by_citizen(db, 3)["user_id"] == 12 and character.by_citizen(db, 99) is None


def test_bots_and_unknown_users_get_no_character_and_do_not_use_up_an_id(db):
    user(db, 1, bot=True)
    assert character.ensure_profile(db, 1, T0) is None and character.ensure_profile(db, 123, T0) is None
    assert db.q1("SELECT COUNT(*) c FROM characters")["c"] == 0 and db.q1("SELECT next_id FROM citizen_seq")["next_id"] == 0
    with pytest.raises(sqlite3.IntegrityError, match="bots have none"):             # the database refuses it as well
        db.x("INSERT INTO characters(user_id,citizen_id,created_at,updated_at) VALUES(1,0,1,1)")
    user(db, 2)
    assert character.ensure_profile(db, 2, T0)["citizen_id"] == 0                   # the first human is still citizen 0


def test_a_failed_creation_gives_its_number_back(db):
    user(db, 10)
    user(db, 11)
    with pytest.raises(RuntimeError):
        with db.tx():
            character.ensure_profile(db, 10, T0)
            assert db.q1("SELECT next_id FROM citizen_seq")["next_id"] == 1
            raise RuntimeError("the surrounding action failed")
    assert character.get(db, 10) is None and db.q1("SELECT next_id FROM citizen_seq")["next_id"] == 0
    assert character.ensure_profile(db, 11, T0)["citizen_id"] == 0                    # no gap, no waste
    assert character.ensure_profile(db, 10, T0)["citizen_id"] == 1


def test_citizen_ids_are_unique_and_never_change_or_disappear(db):
    for uid in (1, 2):
        user(db, uid)
        character.ensure_profile(db, uid, T0)
    with pytest.raises(sqlite3.IntegrityError):
        db.x("UPDATE characters SET citizen_id=0 WHERE user_id=2")                   # taken
    with pytest.raises(sqlite3.IntegrityError, match="immutable"):
        db.x("UPDATE characters SET citizen_id=50 WHERE user_id=2")                  # free, but still not changeable
    with pytest.raises(sqlite3.IntegrityError, match="never reused"):
        db.x("DELETE FROM characters WHERE user_id=2")
    with pytest.raises(sqlite3.IntegrityError):
        db.x("UPDATE characters SET user_id=99 WHERE user_id=2")
    with pytest.raises(sqlite3.IntegrityError):
        db.x("INSERT INTO characters(user_id,citizen_id,created_at,updated_at) VALUES(2,7,1,1)")      # a second character for the same user
    with pytest.raises(sqlite3.IntegrityError):
        db.x("INSERT INTO characters(user_id,citizen_id,created_at,updated_at) VALUES(404,7,1,1)")    # a character for nobody
    with pytest.raises(sqlite3.IntegrityError):
        db.x("UPDATE citizen_seq SET next_id=-1")
    with pytest.raises(sqlite3.IntegrityError):
        db.x("INSERT INTO citizen_seq(id,next_id) VALUES(2,5)")                      # one counter only
    assert [r["citizen_id"] for r in db.qa("SELECT citizen_id FROM characters ORDER BY user_id")] == [0, 1]


def test_the_citizen_id_does_not_change_with_names_state_or_city(db):
    ch = make(db, 5)
    wid = charkit.fake_world(db, -1)
    character.assign_world(db, 5, wid, T0)
    db.x("UPDATE characters SET updated_at=updated_at+1")
    assert character.get(db, 5)["citizen_id"] == ch["citizen_id"] == 0


def test_a_restart_keeps_ids_and_the_counter_going(tmp_path):
    path = str(tmp_path / "game.db")
    d1 = Database(path)
    for uid in (1, 2, 3):
        user(d1, uid)
        character.ensure_profile(d1, uid, T0)
    d1.close()
    d2 = Database(path)
    assert [r["citizen_id"] for r in d2.qa("SELECT citizen_id FROM characters ORDER BY user_id")] == [0, 1, 2]
    user(d2, 4)
    assert character.ensure_profile(d2, 4, T0)["citizen_id"] == 3
    assert character.ensure_profile(d2, 1, T0)["citizen_id"] == 0
    d2.close()


def test_concurrent_creation_from_two_connections_never_repeats_or_skips_an_id(tmp_path):
    path = str(tmp_path / "game.db")
    boot = Database(path)
    for uid in range(1, 61):
        user(boot, uid)
    boot.close()
    errors = []

    def worker(ids):
        d = Database(path)
        try:
            for uid in ids:
                character.ensure_profile(d, uid, T0)
        except BaseException as e:                                                  # pragma: no cover - reported below
            errors.append(e)
        finally:
            d.close()

    threads = [threading.Thread(target=worker, args=(range(1, 61),)), threading.Thread(target=worker, args=(range(60, 0, -1),)),
               threading.Thread(target=worker, args=(range(1, 61, 2),))]
    for th in threads:
        th.start()
    for th in threads:
        th.join()
    assert not errors
    d = Database(path)
    rows = d.qa("SELECT user_id, citizen_id FROM characters")
    assert len(rows) == 60 and sorted(r["citizen_id"] for r in rows) == list(range(60))
    assert d.q1("SELECT next_id FROM citizen_seq")["next_id"] == 60
    assert d.q1("SELECT COUNT(*) c FROM character_genes")["c"] == 60 * len(traits.TRAIT_NAMES)       # one set of genes per character: no double rolls
    d.close()


# ================================================================================================================ names
@pytest.mark.parametrize("raw,want", [
    ("علی", "علی"), ("  علی  ", "علی"), ("عليرضا", "علیرضا"), ("كريم", "کریم"), ("علی  رضا", "علی رضا"), ("موسوی‌نژاد", "موسوی‌نژاد"),
    ("Ali", "Ali"), ("O'Brien", "O'Brien"), ("Smith-Jones", "Smith-Jones"), ("Mary Ann", "Mary Ann"), ("محـمد", "محمد"), ("عَلی", "علی"), ("علی\u200c", "علی"), ("\u200cعلی", "علی"),
])
def test_acceptable_names(raw, want):
    assert character.clean_name(raw) == want


@pytest.mark.parametrize("raw", [
    "", " ", "ع", "A", "a" * 21, "ع" * 21, "<b>x</b>", "Ali<", "علی\nرضا", "علی\rرضا", "علی\tرضا", "Ali1", "علی۱", "٣٣", "😀😀", "Ali😀", "علی Ali", "@admin", "ali@x",
    "http://x.io", "-Ali", "Ali-", "Al--i", "Al  'i", "'Ali", "علی‎رضا", "علی‏رضا", "ع‍لی", "‮ila", "Al‌ex",
    "Привет", "علی_رضا", "Ali.", "a/b", None, 12, b"ali", ["Ali"],
])
def test_unacceptable_names(raw):
    with pytest.raises(GameError) as e:
        character.clean_name(raw)
    assert e.value.code == "char_name"


def test_the_length_limits_are_the_configured_ones():
    assert character.clean_name("ع" * C.CHAR_NAME_MAX) and character.clean_name("ع" * C.CHAR_NAME_MIN)
    with pytest.raises(GameError):
        character.clean_name("ع" * (C.CHAR_NAME_MAX + 1))
    with pytest.raises(GameError):
        character.clean_name("ع" * (C.CHAR_NAME_MIN - 1))
    with pytest.raises(GameError):
        character.clean_name("ع" * 10_000)                                           # a huge text is refused before any work is done on it


def test_the_set_up_steps_and_their_guards(db):
    user(db, 1)
    character.ensure_profile(db, 1, T0)
    with pytest.raises(GameError) as e:
        character.choose_gender(db, 1, "male", T0)                                   # too early
    assert e.value.code == "char_state"
    with pytest.raises(GameError) as e:
        character.submit_name(db, 1, "12", T0)
    assert e.value.code == "char_name" and character.get(db, 1)["setup_state"] == "first"
    assert character.submit_name(db, 1, "علی", T0)["setup_state"] == "last"
    assert character.submit_name(db, 1, "رضایی", T0)["setup_state"] == "gender"
    with pytest.raises(GameError) as e:
        character.submit_name(db, 1, "سوم", T0)                                      # no third name
    assert e.value.code == "char_state"
    for bad in ("", None, "MALE", "other", "female ", 1):
        with pytest.raises(GameError) as e:
            character.choose_gender(db, 1, bad, T0)
        assert e.value.code == "char_gender"
    assert character.get(db, 1)["setup_state"] == "gender"
    row, finished = character.choose_gender(db, 1, "female", T0 + 5)
    assert finished and row["gender"] == "female" and row["age_started_at"] == T0 + 5
    row2, finished2 = character.choose_gender(db, 1, "male", T0 + 9)                 # a repeat changes nothing
    assert not finished2 and row2["gender"] == "female" and row2["age_started_at"] == T0 + 5
    with pytest.raises(GameError) as e:
        character.submit_name(db, 1, "نام", T0)
    assert e.value.code == "char_state"
    assert character.restart_names(db, 1, T0 + 10)["setup_state"] == "done"        # a finished character keeps its names
    assert character.get(db, 1)["first_name"] == "علی"
    for fn, args in ((character.submit_name, ("x1",)), (character.restart_names, ()), (character.choose_gender, ("male",))):
        with pytest.raises(GameError) as e:
            fn(db, 404, *args)
        assert e.value.code in ("char_none", "char_name")


def test_a_finished_profile_is_locked_by_the_database(db):
    make(db, 1)
    for sql in ("UPDATE characters SET first_name='x'", "UPDATE characters SET last_name='x'", "UPDATE characters SET gender='female'",
                "UPDATE characters SET setup_state='first'", "UPDATE characters SET age_started_at=1", "UPDATE characters SET age_started_at=NULL",
                "UPDATE characters SET created_at=1", "UPDATE characters SET origin='legacy'"):
        with pytest.raises(sqlite3.IntegrityError):
            db.x(sql)
    row = character.get(db, 1)
    assert (row["first_name"], row["last_name"], row["gender"], row["setup_state"], row["age_started_at"]) == ("علی", "رضایی", "male", "done", T0)


def test_the_row_can_not_be_made_inconsistent(db):
    user(db, 1)
    character.ensure_profile(db, 1, T0)
    for sql in ("UPDATE characters SET setup_state='gender'",                       # no names yet
                "UPDATE characters SET setup_state='done', first_name='a', last_name='b'",              # done without a gender / anchor
                "UPDATE characters SET age_started_at=5",                                               # an age before the profile exists
                "UPDATE characters SET gender='robot'", "UPDATE characters SET setup_state='weird'",
                "UPDATE characters SET current_world_id=3"):                                            # a city that does not exist (and half a pair)
        with pytest.raises(sqlite3.IntegrityError):
            db.x(sql)


# ================================================================================================================ age
START = C.CHARACTER_START_AGE


def test_the_game_year_is_four_real_days():
    assert START == 15 and C.GAME_YEAR_DAYS == 4 and gamecal.year_seconds() == 4 * DAY


@pytest.mark.parametrize("dt,age", [(0, 15), (1, 15), (4 * DAY - 1, 15), (4 * DAY, 16), (4 * DAY + 1, 16), (8 * DAY - 1, 16), (8 * DAY, 17), (12 * DAY, 18),
                                    (40 * DAY, 25), (400 * DAY, 115), (-5, 15), (-100 * DAY, 15)])
def test_age_boundaries(dt, age):
    assert gamecal.age_at(T0, T0 + dt) == age
    assert character.age({"age_started_at": T0}, T0 + dt) == age


def test_age_is_a_pure_function_of_the_saved_anchor(db):
    ch = make(db, 1, now=T0)
    assert character.age(ch, T0) == 15
    assert character.age(ch, T0 + 4 * DAY - 1) == 15 and character.age(ch, T0 + 4 * DAY) == 16
    for _ in range(3):                                                              # asking again, or after a restart, gives the same answer
        assert character.age(character.get(db, 1), T0 + 9 * DAY) == 17
    assert db.q1("SELECT age_started_at FROM characters WHERE user_id=1")["age_started_at"] == T0     # reading never moves the anchor


def test_nobody_ages_before_the_profile_is_finished(db):
    user(db, 1)
    ch = character.ensure_profile(db, 1, T0)
    assert character.age(ch, T0 + 100 * DAY) is None and character.age(None, T0) is None
    character.submit_name(db, 1, "علی", T0 + 50 * DAY)
    character.submit_name(db, 1, "رضایی", T0 + 60 * DAY)
    assert character.age(character.get(db, 1), T0 + 70 * DAY) is None               # still pending
    row, _ = character.choose_gender(db, 1, "male", T0 + 80 * DAY)
    assert character.age(row, T0 + 80 * DAY) == 15 and character.age(row, T0 + 84 * DAY) == 16     # counted from the finish, not from the creation


def test_age_does_not_depend_on_the_local_time_zone(db):
    ch = make(db, 1, now=T0)
    old = os.environ.get("TZ")
    try:
        seen = set()
        for tz in ("UTC", "Asia/Tehran", "America/Los_Angeles", "Pacific/Kiritimati"):
            os.environ["TZ"] = tz
            time.tzset()
            seen.add((character.age(ch, T0 + 4 * DAY - 1), character.age(ch, T0 + 4 * DAY), character.age(ch, T0 + 4 * DAY * 3 + 7)))
        assert seen == {(15, 16, 18)}
    finally:
        if old is None:
            os.environ.pop("TZ", None)
        else:
            os.environ["TZ"] = old
        time.tzset()


def test_age_rejects_non_integer_timestamps():
    with pytest.raises(TypeError):
        gamecal.whole_years(1.5, 10)
    with pytest.raises(TypeError):
        gamecal.whole_years(1, True)


def test_the_age_formula_lives_in_one_place():
    hits = []
    for p in (ROOT / "hypemarket").glob("*.py"):
        if p.name in ("gamecal.py", "character.py", "config.py"):
            continue
        s = p.read_text(encoding="utf-8")
        if "year_seconds" in s or "GAME_YEAR_DAYS" in s or "CHARACTER_START_AGE" in s and p.name not in ("characterui.py",):
            hits.append(p.name)
    assert hits == []


# ================================================================================================================ traits & genes
def test_the_catalog_is_valid_and_complete():
    traits.check_catalog()
    assert set(traits.TRAIT_NAMES) == set(traits.CATALOG) and len(traits.TRAIT_NAMES) >= 6
    for spec in traits.CATALOG.values():
        assert spec["rule"] in ("dominant", "scale") and len(spec["values"]) == len(spec["weights"])


def test_check_catalog_rejects_broken_entries(monkeypatch):
    for broken in ({"rule": "scale", "values": ["a"], "weights": [1]}, {"rule": "scale", "values": ["a", "a"], "weights": [1, 1]},
                   {"rule": "scale", "values": ["a", "b"], "weights": [1]}, {"rule": "x", "values": ["a", "b"], "weights": [1, 1]},
                   {"rule": "scale", "values": ["a", "B"], "weights": [1, 1]}, {"rule": "scale", "values": ["a", "b"], "weights": [1, 0]},
                   {"rule": "scale", "values": ["a", "ب"], "weights": [1, 1]}):
        monkeypatch.setitem(traits.CATALOG, "broken", broken)
        with pytest.raises(traits.TraitError):
            traits.check_catalog()
        monkeypatch.delitem(traits.CATALOG, "broken")
    traits.check_catalog()


def test_rolling_is_reproducible_with_a_seeded_rng_and_covers_the_catalog():
    a = traits.roll_genes(random.Random(5))
    b = traits.roll_genes(random.Random(5))
    assert a == b and set(a) == set(traits.TRAIT_NAMES)
    for name, (x, y) in a.items():
        assert x in traits.CATALOG[name]["values"] and y in traits.CATALOG[name]["values"]
    assert traits.roll_genes(random.Random(6)) != a
    assert set(traits.roll_genes()) == set(traits.TRAIT_NAMES)                      # the default generator (the operating system's) works without a seed


def test_phenotype_rules():
    assert traits.phenotype_of("hair_color", "red", "black") == traits.phenotype_of("hair_color", "black", "red") == "black"          # dominant: the first listed wins
    assert traits.phenotype_of("eye_color", "blue", "blue") == "blue"
    assert traits.phenotype_of("height", "short", "tall") == "average"                                  # scale: the middle
    assert traits.phenotype_of("height", "very_short", "very_tall") == "average"
    assert traits.phenotype_of("height", "tall", "tall") == "tall"
    assert traits.phenotype_of("build", "slim", "average") == "average" == traits.phenotype_of("build", "average", "slim")    # an odd sum goes toward the centre
    assert traits.phenotype_of("skin_tone", "very_light", "light") == "light"
    for name, spec in traits.CATALOG.items():                                       # every pair is symmetric and lands inside the scale
        for a in spec["values"]:
            for b in spec["values"]:
                p = traits.phenotype_of(name, a, b)
                assert p == traits.phenotype_of(name, b, a) and p in spec["values"]
                if a == b:
                    assert p == a


def test_phenotype_validation():
    with pytest.raises(traits.TraitError):
        traits.phenotype_of("nope", "a", "b")
    with pytest.raises(traits.TraitError):
        traits.phenotype_of("height", "short", "huge")
    with pytest.raises(traits.TraitError):
        traits.phenotype({"height": ("short", "tall")})                              # incomplete
    g = traits.roll_genes(random.Random(1))
    g["extra"] = ("a", "b")
    with pytest.raises(traits.TraitError):
        traits.phenotype(g)


def test_traits_are_saved_once_with_the_character_and_match_their_genes(db):
    user(db, 1)
    ch = character.ensure_profile(db, 1, T0, random.Random(11))
    genes, saved = traits.genes_of(db, 1), traits.traits_of(db, 1)
    assert genes == traits.roll_genes(random.Random(11)) and saved == traits.phenotype(genes)
    assert set(saved) == set(traits.TRAIT_NAMES)


def test_traits_never_change_on_repeat_restart_or_reading(db):
    user(db, 1)
    character.ensure_profile(db, 1, T0)
    before = (traits.genes_of(db, 1), traits.traits_of(db, 1))
    for _ in range(5):
        character.ensure_profile(db, 1, T0 + 1)
        traits.genes_of(db, 1), traits.traits_of(db, 1)
    charkit.complete(db, 1, now=T0 + 5)
    assert (traits.genes_of(db, 1), traits.traits_of(db, 1)) == before
    with pytest.raises(sqlite3.IntegrityError):
        traits.save(db, 1, traits.roll_genes(random.Random(1)))                      # a second save is refused...
    assert (traits.genes_of(db, 1), traits.traits_of(db, 1)) == before               # ...and leaves nothing behind (the whole save is one transaction)


def test_the_database_refuses_to_edit_or_remove_genes_and_traits(db):
    user(db, 1)
    character.ensure_profile(db, 1, T0)
    for sql in ("UPDATE character_genes SET allele1='x'", "DELETE FROM character_genes", "UPDATE character_traits SET code='x'", "DELETE FROM character_traits",
                "INSERT INTO character_genes(user_id,trait,allele1,allele2) VALUES(404,'height','short','tall')",
                "INSERT INTO character_traits(user_id,trait,code) VALUES(404,'height','tall')"):
        with pytest.raises(sqlite3.IntegrityError):
            db.x(sql)


def test_a_damaged_saved_value_is_reported_not_trusted(db):
    user(db, 1)
    character.ensure_profile(db, 1, T0)
    db.x("DROP TRIGGER trg_traits_update")
    db.x("UPDATE character_traits SET code='purple' WHERE trait='height'")
    with pytest.raises(traits.TraitError):
        traits.traits_of(db, 1)
    assert traits.traits_of(db, 404) is None and traits.genes_of(db, 404) is None


def test_two_characters_do_not_share_traits_by_accident(db):
    rolled = set()
    for uid in range(1, 31):
        user(db, uid)
        character.ensure_profile(db, uid, T0)
        rolled.add(tuple(sorted(traits.traits_of(db, uid).items())))
    assert len(rolled) > 10                                                          # 30 random faces are not all alike


# ================================================================================================================ character XP
def test_a_new_character_has_zero_xp_and_level_one(db):
    make(db, 1)
    assert charxp.get(db, 1) == 0 and charxp.level_of(db, 1) == 1
    assert charxp.get(db, 404) is None and charxp.level_of(db, 404) is None
    assert db.q1("SELECT COUNT(*) c FROM character_xp_log")["c"] == 0


@pytest.mark.parametrize("xp,level", [(0, 1), (99, 1), (100, 2), (399, 2), (400, 3), (899, 3), (900, 4), (10_000, 11), (10**9, 3163)])
def test_the_level_curve(xp, level):
    assert charxp.level_for(xp) == level
    assert charxp.level_for(charxp.xp_for_level(level)) == level and charxp.xp_for_level(level) <= xp
    if level > 1:
        assert charxp.level_for(charxp.xp_for_level(level) - 1) == level - 1


def test_level_helpers_reject_bad_input():
    for bad in (-1, 1.5, "5", None, True):
        with pytest.raises(ValueError):
            charxp.level_for(bad)
    for bad in (0, -3, 1.5, None):
        with pytest.raises(ValueError):
            charxp.xp_for_level(bad)


def test_grants_are_atomic_and_idempotent(db):
    make(db, 1)
    assert charxp.grant(db, 1, 150, "job:test", "task-1", T0) == 150
    assert charxp.grant(db, 1, 150, "job:test", "task-1", T0 + 1) is None            # the same grant again pays nothing
    assert charxp.grant(db, 1, 5, "job:test", "task-2", T0) == 155
    assert charxp.grant(db, 1, 5, "other", "task-1", T0) == 160                      # another source with the same reference is another grant
    assert charxp.get(db, 1) == 160 and charxp.level_of(db, 1) == 2
    log = db.qa("SELECT source, ref, delta, xp_after FROM character_xp_log WHERE user_id=1 ORDER BY id")
    assert [tuple(r) for r in log] == [("job:test", "task-1", 150, 150), ("job:test", "task-2", 5, 155), ("other", "task-1", 5, 160)]


def test_grant_validation(db):
    make(db, 1)
    for amount in (0, -5, 1.5, "10", None, True, 10 ** 9 + 1):
        with pytest.raises(ValueError):
            charxp.grant(db, 1, amount, "s", "r", T0)
    for source, ref in (("", "r"), ("s", ""), ("  ", "r"), ("s", None), (None, "r"), ("x" * 41, "r"), ("s", "y" * 81)):
        with pytest.raises(ValueError):
            charxp.grant(db, 1, 5, source, ref, T0)
    with pytest.raises(GameError) as e:
        charxp.grant(db, 404, 5, "s", "r", T0)
    assert e.value.code == "no_character"
    assert charxp.get(db, 1) == 0 and db.q1("SELECT COUNT(*) c FROM character_xp_log")["c"] == 0


def test_xp_can_only_go_up_and_is_capped(db):
    make(db, 1)
    charxp.grant(db, 1, 10, "s", "a", T0)
    with pytest.raises(sqlite3.IntegrityError):
        db.x("UPDATE character_xp SET xp=xp-1 WHERE user_id=1")
    with pytest.raises(sqlite3.IntegrityError):
        db.x("UPDATE character_xp SET xp=-1 WHERE user_id=1")
    db.x("UPDATE character_xp SET xp=? WHERE user_id=1", (charxp.MAX_XP - 5,))
    assert charxp.grant(db, 1, 100, "s", "big", T0) == charxp.MAX_XP
    assert db.q1("SELECT delta FROM character_xp_log WHERE ref='big'")["delta"] == 5


def test_concurrent_identical_grants_pay_once(tmp_path):
    path = str(tmp_path / "xp.db")
    boot = Database(path)
    make(boot, 1)
    boot.close()
    out = []

    def go():
        d = Database(path)
        try:
            out.append(charxp.grant(d, 1, 50, "job:x", "same", T0))
        finally:
            d.close()

    ts = [threading.Thread(target=go) for _ in range(6)]
    for t in ts:
        t.start()
    for t in ts:
        t.join()
    assert sorted(x for x in out if x is not None) == [50] and out.count(None) == 5
    d = Database(path)
    assert charxp.get(d, 1) == 50
    d.close()


def test_character_xp_is_separate_from_the_stock_game(db):
    make(db, 1)
    before = {t: [tuple(r) for r in db.qa(f"SELECT * FROM {t} ORDER BY 1,2")] for t in ("users", "ledger", "life_accounts", "life_ledger", "holdings")
              if db.q1("SELECT 1 FROM sqlite_master WHERE name=?", (t,))}
    charxp.grant(db, 1, 5000, "x", "y", T0)
    after = {t: [tuple(r) for r in db.qa(f"SELECT * FROM {t} ORDER BY 1,2")] for t in before}
    assert after == before                                                           # not a cent, a stock level, a rebirth or a ledger line moved
    assert core.get_user(db, 1)["max_level"] == 0


def test_no_game_code_grants_character_xp_except_gift_codes():
    """Character XP is still infrastructure: no job, task, casino hand or greeting awards it. The one v0.20 exception is an admin's gift code (gifts.py, source `gift`,
    reference = the code), which goes through the same idempotent `charxp.grant`."""
    offenders = []
    for p in (ROOT / "hypemarket").rglob("*.py"):
        if p.name == "charxp.py":
            continue
        s = p.read_text(encoding="utf-8")
        if re.search(r"charxp\s*\.\s*grant\s*\(", s) or re.search(r"from\s+\.?charxp\s+import[^\n]*\bgrant\b", s) or "character_xp_log" in s and p.name != "db.py":
            offenders.append(p.name)
    assert offenders == ["gifts.py"]
    assert "charxp.grant(db, uid, amount, \"gift\", code, now)" in (ROOT / "hypemarket" / "gifts.py").read_text(encoding="utf-8")
    for p in (ROOT / "hypemarket").glob("*.py"):                                     # ...and nothing writes the table directly either
        if p.name not in ("charxp.py", "db.py", "character.py"):
            assert "character_xp" not in p.read_text(encoding="utf-8"), p.name


# ================================================================================================================ the schema-9 -> 10 migration
def build_v9(path, plan):
    """A database as v0.18.2 left it: schema 9, no character tables. `plan(db)` fills the old per-group rows."""
    d = Database(path)
    plan(d)
    for t in ("characters", "character_genes", "character_traits", "character_xp", "character_xp_log", "citizen_seq"):
        d.x(f"DROP TABLE {t}")
    d.x("PRAGMA user_version=9")
    d.close()


def legacy_plan(d):
    for uid in (10, 11, 12, 13, 14, 16, 17, 18, 19):
        user(d, uid)
    user(d, 15, bot=True)
    w = {n: charkit.fake_world(d, -n, f"W{n}") for n in (1, 2, 3)}
    assert w == {1: 1, 2: 2, 3: 3}

    def presence(uid, wid, at):
        d.x("INSERT INTO world_players(world_id,user_id,x,y,at_place,spawn_x,spawn_y,created_at,updated_at) VALUES(?,?,1,1,NULL,1,1,?,?)", (wid, uid, at, at))

    def trip(uid, wid, started, state="walking"):
        d.x("INSERT INTO world_trips(world_id,user_id,dest_id,dest_kind,route,length,speed,started_at,arrive_at,state) VALUES(?,?,'p','bank','[]',1,1,?,?,?)",
            (wid, uid, started, started + 100, state))

    def task(uid, wid, at, state="open"):
        d.x("INSERT INTO work_tasks(user_id,world_id,job,stage,state,created_at) VALUES(?,?,'snapp','go',?,?)", (uid, wid, state, at))

    presence(10, 1, 100); presence(10, 2, 300); task(10, 2, 400); trip(10, 3, 500)                  # an open task beats a walking trip beats the oldest presence
    presence(11, 1, 100); presence(11, 2, 200); trip(11, 3, 1000); trip(11, 2, 2000)                 # the most recent walking trip (world 2)
    presence(12, 2, 100); presence(12, 1, 200)                                                       # the oldest presence (world 2)
    presence(13, 3, 100); presence(13, 1, 100)                                                       # an exact tie: the lower city id
    presence(16, 2, 50)
    d.x("DELETE FROM world_players WHERE user_id=16")                                                 # no presence at all, only a casino hand
    d.x("INSERT INTO casino_games(sid,world_id,user_id,deck,player,dealer,state,created_at,updated_at) VALUES('s',2,16,'[]','[]','[]','done',60,60)")
    presence(17, 99, 10)                                                                              # a city that no longer exists: ignored
    trip(18, 3, 700); trip(18, 2, 700)                                                               # two walking trips started the same second: the lower city id
    presence(18, 3, 600)
    presence(19, 1, 100); trip(19, 3, 50, state="arrived"); task(19, 2, 40, state="done")            # finished trips / tasks are history, not a current city
    presence(15, 1, 1)                                                                                # a bot: no character
    d.x("UPDATE users SET cash=777, rebirths=2, max_level=5 WHERE id=10")


def snapshot(d, tables):
    return {t: [tuple(r) for r in d.qa(f"SELECT * FROM {t} ORDER BY 1,2")] for t in tables}


OLD_TABLES = ("users", "worlds", "world_players", "world_trips", "work_tasks", "casino_games", "map_sessions", "life_accounts", "life_ledger", "stocks")


def test_the_migration_reconstructs_legacy_characters_by_the_documented_rule(tmp_path):
    path = str(tmp_path / "old.db")
    build_v9(path, legacy_plan)
    raw = sqlite3.connect(path)
    assert raw.execute("PRAGMA user_version").fetchone()[0] == 9
    raw.close()
    d = Database(path)
    assert d.q1("PRAGMA user_version")[0] == SCHEMA_VERSION                           # (v0.20 then added schema 11: nothing else changes)
    rows = {r["user_id"]: r for r in d.qa("SELECT * FROM characters")}
    assert sorted(rows) == [10, 11, 12, 13, 16, 18, 19]                                # 14: nothing saved; 15: a bot; 17: only a vanished city
    assert [rows[u]["citizen_id"] for u in sorted(rows)] == list(range(7))             # ascending Telegram ID, zero-based, no gaps
    cur = {u: r["current_world_id"] for u, r in rows.items()}
    birth = {u: (r["birth_world_id"], r["birth_basis"]) for u, r in rows.items()}
    assert cur == {10: 2, 11: 2, 12: 2, 13: 1, 16: 2, 18: 2, 19: 1}
    assert birth == {10: (1, "legacy_presence"), 11: (1, "legacy_presence"), 12: (2, "legacy_presence"), 13: (1, "legacy_presence"),
                     16: (2, "legacy_evidence"), 18: (3, "legacy_presence"), 19: (1, "legacy_presence")}
    assert all(r["origin"] == "legacy" and r["setup_state"] == "first" and r["first_name"] == "" and r["age_started_at"] is None for r in rows.values())
    assert d.q1("SELECT next_id FROM citizen_seq")["next_id"] == 7
    for u in rows:
        assert charxp.get(d, u) == 0 and traits.traits_of(d, u) and traits.genes_of(d, u)
    for u in (14, 15, 17):
        assert character.get(d, u) is None
    d.close()


def test_the_migration_changes_nothing_that_existed_and_is_safe_to_repeat(tmp_path):
    path = str(tmp_path / "old.db")
    build_v9(path, legacy_plan)
    probe = Database(path + ".copy")
    probe.close()
    raw = sqlite3.connect(path)
    raw.row_factory = sqlite3.Row

    class Q:                                                                         # the old content, read straight from the schema-9 file
        qa = staticmethod(lambda sql, a=(): raw.execute(sql, a).fetchall())
    before = snapshot(Q, OLD_TABLES)
    raw.close()
    d = Database(path)
    assert snapshot(d, OLD_TABLES) == before                                          # no balance, ledger, city, position, trip, task or game was touched
    first = snapshot(d, ("characters", "character_genes", "character_traits", "character_xp", "citizen_seq"))
    assert character.migrate_legacy(d) == 0                                           # running the step again creates nothing
    d.close()
    for _ in range(2):
        d = Database(path)                                                            # reopening is not a migration
        assert snapshot(d, ("characters", "character_genes", "character_traits", "character_xp", "citizen_seq")) == first
        assert d.q1("PRAGMA user_version")[0] == SCHEMA_VERSION
        d.close()


def test_a_failed_migration_leaves_the_v9_database_untouched_and_can_be_retried(tmp_path, monkeypatch):
    path = str(tmp_path / "old.db")
    build_v9(path, legacy_plan)
    real = character._insert
    calls = []

    def boom(*a, **k):
        calls.append(1)
        if len(calls) == 3:
            raise RuntimeError("disk trouble")
        return real(*a, **k)

    monkeypatch.setattr(character, "_insert", boom)
    with pytest.raises(RuntimeError):
        Database(path)
    raw = sqlite3.connect(path)
    assert raw.execute("PRAGMA user_version").fetchone()[0] == 9                       # still schema 9: it will be migrated again
    assert raw.execute("SELECT COUNT(*) FROM characters").fetchone()[0] == 0           # nothing half-done
    assert raw.execute("SELECT next_id FROM citizen_seq").fetchone()[0] == 0
    raw.close()
    monkeypatch.setattr(character, "_insert", real)
    d = Database(path)
    assert d.q1("SELECT COUNT(*) c FROM characters")["c"] == 7 and d.q1("PRAGMA user_version")[0] == SCHEMA_VERSION
    d.close()


def test_a_newer_database_is_refused(tmp_path):
    path = str(tmp_path / "new.db")
    Database(path).close()
    raw = sqlite3.connect(path)
    raw.execute(f"PRAGMA user_version={SCHEMA_VERSION + 1}")
    raw.close()
    with pytest.raises(RuntimeError):
        Database(path)


def test_the_migration_gives_each_legacy_player_their_old_city_not_a_new_one(tmp_path):
    path = str(tmp_path / "old.db")
    build_v9(path, legacy_plan)
    d = Database(path)
    before = d.qa("SELECT world_id, user_id, x, y FROM world_players ORDER BY 1,2")
    for u in (10, 12, 13):
        w = character.current_world(d, u)
        assert w is not None and w["id"] == character.get(d, u)["current_world_id"]
    assert d.q1("SELECT COUNT(*) c FROM worlds")["c"] == 3                            # no city was created, regenerated or removed
    assert d.qa("SELECT world_id, user_id, x, y FROM world_players ORDER BY 1,2") == before
    d.close()


def test_legacy_choice_is_a_pure_read(tmp_path):
    path = str(tmp_path / "old.db")
    build_v9(path, legacy_plan)
    raw = Database.__new__(Database)                                                  # open the schema-9 file WITHOUT migrating it
    raw.conn = sqlite3.connect(path)
    raw.conn.row_factory = sqlite3.Row
    raw.conn.execute("PRAGMA foreign_keys=OFF")
    raw._depth = 0
    c = character.legacy_choice(raw, 10)
    assert c == {"current": 2, "birth": 1, "basis": "legacy_presence", "rule": "task"}
    assert character.legacy_choice(raw, 11)["rule"] == "trip" and character.legacy_choice(raw, 12)["rule"] == "presence"
    assert character.legacy_choice(raw, 16) == {"current": 2, "birth": 2, "basis": "legacy_evidence", "rule": "evidence"}
    assert character.legacy_choice(raw, 14) is None and character.legacy_choice(raw, 17) is None
    raw.conn.close()
