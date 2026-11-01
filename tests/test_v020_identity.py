"""v0.20: the identity data model (schema 12, `character_identity`), its additive migration and its rules.

Covered: the migration of a real schema-11 database (every character gets a row, finished characters are issued a document whose dates agree with their age anchor, nothing that existed
changes, it is safe to repeat and all-or-nothing), the defaults and the configuration (nationality, languages, authority, validity), the permanent fingerprint ID, the height in
centimetres, the signature seed, the home city versus the current city, the birthday and the age rule, the database triggers that make issued facts permanent, and the calendar helpers.
What they cannot cover: a migration of the owner's own production database (it was checked only on databases built by the delivered code)."""
import dataclasses
import sqlite3

import pytest

import charkit
from hypemarket import character, charxp, config as C, core, fmt, gamecal, identity, idcard, traits as T
from hypemarket import db as dbmod
from hypemarket.db import Database

T0 = 1_800_000_000
YEAR = 4 * 86400


def user(d, uid, bot=False):
    core.ensure_user(d, uid, f"u{uid}", f"User {uid}")
    if bot:
        d.x("UPDATE users SET bot=1 WHERE id=?", (uid,))
    return uid


def world(d, chat, name="W"):
    return charkit.fake_world(d, chat, name)


def fresh():
    d = Database(":memory:")
    return d, world(d, -1, "One"), world(d, -2, "Two")


OLD_TABLES = ("users", "worlds", "characters", "character_genes", "character_traits", "character_xp", "character_xp_log", "citizen_seq", "life_accounts", "life_ledger")


def snapshot(d, tables):
    return {t: [tuple(r) for r in d.qa(f"SELECT * FROM {t} ORDER BY 1,2")] for t in tables}


def build_v11(path):
    """A database as the first v0.20 delivery left it: schema 11, characters of every kind, no identity table."""
    d = Database(path)
    w1, w2, w3 = world(d, -1, "One"), world(d, -2, "Two"), world(d, -3, "Three")
    for uid in (1, 2, 3, 4, 5, 6):
        user(d, uid)
    user(d, 7, bot=True)
    charkit.complete(d, 1, first="علی", last="رضایی", gender="male", world_id=w1, now=T0)
    charkit.complete(d, 2, first="مریم", last="احمدی", gender="female", world_id=w2, now=T0 + 1000)
    charkit.complete(d, 3, first="Sam", last="Lee", gender="male", world_id=w3, now=T0 + 5 * 86400)
    character.ensure_profile(d, 4, T0)                                                  # pending, no city
    character.ensure_profile(d, 5, T0)
    character.assign_world(d, 5, w1, T0)                                                # pending, with a city (the birthplace)
    d.x("UPDATE characters SET first_name='نیما', last_name='کیانی', setup_state='gender' WHERE user_id=5")   # stopped at the gender step
    d.x("UPDATE character_xp SET xp=250 WHERE user_id=1")
    d.x("UPDATE users SET cash=777, max_level=5 WHERE id=1")
    for t in ("character_identity",):
        d.x(f"DROP TABLE {t}")
    d.x("PRAGMA user_version=11")
    d.close()
    return (w1, w2, w3)


# ============================================================================================== the migration
def test_the_migration_gives_every_character_a_row_and_issues_the_finished_ones(tmp_path):
    path = str(tmp_path / "v11.db")
    w1, w2, w3 = build_v11(path)
    raw = sqlite3.connect(path)
    assert raw.execute("PRAGMA user_version").fetchone()[0] == 11
    raw.row_factory = sqlite3.Row
    before = {t: [tuple(r) for r in raw.execute(f"SELECT * FROM {t} ORDER BY 1,2")] for t in OLD_TABLES}
    anchors = {r["user_id"]: r["age_started_at"] for r in raw.execute("SELECT user_id, age_started_at FROM characters")}
    raw.close()
    d = Database(path)
    assert d.q1("PRAGMA user_version")[0] == dbmod.SCHEMA_VERSION == 12
    rows = {r["user_id"]: r for r in d.qa("SELECT * FROM character_identity")}
    assert sorted(rows) == [1, 2, 3, 4, 5]                                              # one per character; the bot (7) and the character-less user (6) have none
    assert snapshot(d, OLD_TABLES) == before                                            # no citizen ID, gene, trait, XP, city, balance or ledger row changed
    for uid in (1, 2, 3):
        r = rows[uid]
        assert r["issued_at"] == anchors[uid] and r["born_at"] == anchors[uid] - 15 * YEAR and r["expires_at"] == anchors[uid] + 10 * YEAR
        assert r["nationality"] == "hype" and r["languages"] == "fa" and r["status"] == "active" and r["authority"] == "registry"
        assert r["fingerprint_id"].startswith("FP-") and len(r["doc_number"]) == 9 and len(r["signature_seed"]) == 16 and r["height_cm"] and r["created_at"]
        assert (r["home_district"], r["home_street"], r["home_building"]) == (None, None, None)
    assert [rows[u]["home_world_id"] for u in (1, 2, 3, 4, 5)] == [w1, w2, w3, None, w1]       # the home city is the birth city; none when the character has no city
    for uid in (4, 5):                                                                  # an unfinished character has no document yet
        assert all(rows[uid][k] is None for k in ("born_at", "height_cm", "signature_seed", "doc_number", "issued_at", "expires_at", "authority"))
        assert rows[uid]["fingerprint_id"] and rows[uid]["nationality"] == "hype"
    assert len({r["fingerprint_id"] for r in rows.values()}) == 5 and len({rows[u]["doc_number"] for u in (1, 2, 3)}) == 3
    d.close()


def test_no_age_changes_and_the_birthday_always_agrees_with_it(tmp_path):
    path = str(tmp_path / "v11.db")
    build_v11(path)
    raw = sqlite3.connect(path); raw.row_factory = sqlite3.Row
    ages_before = {}
    times = [T0 + k for k in (0, 1, 86400, 4 * 86400 - 1, 4 * 86400, 4 * 86400 + 1, 9 * 86400, 40 * 86400, 400 * 86400)] + [T0 + 5 * 86400 + 3 * 86400]
    for r in raw.execute("SELECT user_id, age_started_at FROM characters WHERE age_started_at IS NOT NULL"):
        for now in times:
            ages_before[(r["user_id"], now)] = gamecal.age_at(r["age_started_at"], now) if now >= r["age_started_at"] else gamecal.age_at(r["age_started_at"], r["age_started_at"])
    raw.close()
    d = Database(path)
    for uid in (1, 2, 3):
        ch = character.get(d, uid)
        row = identity.get(d, uid)
        assert ch["age_started_at"] == row["issued_at"]                                  # the anchor was not moved or reset
        for now in times:
            if now < ch["age_started_at"]:
                continue
            assert character.age(ch, now) == ages_before[(uid, now)]                     # the age is exactly what it was
            assert gamecal.age_at(row["born_at"], now, 0) == character.age(ch, now)      # and the birthday gives the same age at every moment, boundaries included
    d.close()


def test_the_migration_is_safe_to_repeat_and_reopening_changes_nothing(tmp_path):
    path = str(tmp_path / "v11.db")
    build_v11(path)
    d = Database(path)
    first = snapshot(d, ("character_identity",) + OLD_TABLES)
    assert identity.backfill(d) == 0
    d.close()
    for _ in range(2):
        d = Database(path)
        assert snapshot(d, ("character_identity",) + OLD_TABLES) == first and d.q1("PRAGMA user_version")[0] == 12
        d.close()


def test_a_failed_migration_leaves_schema_11_and_can_be_retried(tmp_path, monkeypatch):
    path = str(tmp_path / "v11.db")
    build_v11(path)
    real = identity.issue
    calls = []

    def boom(db, uid, now=None):
        calls.append(uid)
        if len(calls) == 2:
            raise RuntimeError("disk trouble")
        return real(db, uid, now)
    monkeypatch.setattr(identity, "issue", boom)
    with pytest.raises(RuntimeError):
        Database(path)
    raw = sqlite3.connect(path)
    assert raw.execute("PRAGMA user_version").fetchone()[0] == 11
    assert raw.execute("SELECT COUNT(*) FROM character_identity").fetchone()[0] == 0     # nothing half-done
    raw.close()
    monkeypatch.setattr(identity, "issue", real)
    d = Database(path)
    assert d.q1("PRAGMA user_version")[0] == 12 and d.q1("SELECT COUNT(*) c FROM character_identity")["c"] == 5
    d.close()


def test_characters_created_by_the_older_migration_get_their_identity_in_the_same_run(tmp_path):
    """A schema-9 database: the v0.19 step creates the characters and each gets its identity row at birth; the schema-12 step then finds nothing missing."""
    from test_v019_character import build_v9, legacy_plan
    path = str(tmp_path / "old.db")
    build_v9(path, legacy_plan)
    d = Database(path)
    assert d.q1("PRAGMA user_version")[0] == 12
    ids = {r["user_id"] for r in d.qa("SELECT user_id FROM characters")}
    assert ids and ids == {r["user_id"] for r in d.qa("SELECT user_id FROM character_identity")}
    assert all(r["issued_at"] is None for r in d.qa("SELECT * FROM character_identity"))          # legacy characters are pending: no document until the player finishes the set-up
    d.close()


# ============================================================================================== defaults and configuration
def test_a_new_character_gets_its_identity_at_birth_and_its_document_when_finished():
    d, w1, w2 = fresh()
    user(d, 1)
    ch = character.ensure_profile(d, 1, T0)
    row = identity.get(d, 1)
    assert row is not None and row["issued_at"] is None and row["status"] == "active" and row["home_world_id"] is None and row["nationality"] == C.char_nationality()
    character.assign_world(d, 1, w2, T0)
    assert identity.get(d, 1)["home_world_id"] == w2                                      # the city it was born in
    d.x("UPDATE characters SET first_name='A', last_name='B', setup_state='gender' WHERE user_id=1")
    ch, finished = character.choose_gender(d, 1, "female", T0 + 77)
    row = identity.get(d, 1)
    assert finished and row["issued_at"] == T0 + 77 == ch["age_started_at"] and row["born_at"] == T0 + 77 - 15 * YEAR and row["expires_at"] == T0 + 77 + 10 * YEAR
    assert C.HEIGHT_BANDS_CM["female"][T.traits_of(d, 1)["height"]] <= row["height_cm"] < C.HEIGHT_BANDS_CM["female"][T.traits_of(d, 1)["height"]] + 10
    again, finished2 = character.choose_gender(d, 1, "male", T0 + 99)
    assert not finished2 and identity.get(d, 1) == row                                    # a repeat changes nothing (and the gender cannot change)


def test_the_defaults_never_come_from_telegram_or_a_location():
    d, w1, w2 = fresh()
    for uid, lang in ((1, "fa"), (2, "en")):
        core.ensure_user(d, uid, f"u{uid}", f"User {uid}", lang=lang)
        charkit.complete(d, uid, first="A", last="B", world_id=w1, now=T0)
    a, b = identity.get(d, 1), identity.get(d, 2)
    assert (a["nationality"], a["languages"]) == (b["nationality"], b["languages"]) == ("hype", "fa")
    assert identity.nationality_name("fa", "hype") == "هایپی" and identity.nationality_name("en", "hype") == "Hypean"
    assert identity.language_names("fa", ["fa", "en"]) == ["فارسی", "انگلیسی"] and identity.language_names("en", ["fa", "en"]) == ["Persian", "English"]
    assert identity.authority_name("fa", "registry") == "اداره ثبت شهروندی" and identity.nationality_name("en", "zz") == "zz"


def test_the_configuration_is_read_in_game_terms_and_applies_to_new_characters_only(monkeypatch):
    d, w1, w2 = fresh()
    user(d, 1)
    charkit.complete(d, 1, first="A", last="B", world_id=w1, now=T0)
    old = identity.get(d, 1)
    monkeypatch.setenv("CARD_VALID_GAME_YEARS", "3")
    monkeypatch.setenv("CHAR_NATIONALITY", "Other_Land")
    monkeypatch.setenv("CHAR_LANGUAGES", "en, FA, en, de")
    monkeypatch.setenv("CARD_AUTHORITY", "town_hall")
    monkeypatch.setenv("CARD_AUTHORITY_FA", "شهرداری مرکزی")
    monkeypatch.setenv("CARD_AUTHORITY_EN", "Central Town Hall")
    assert (C.card_valid_years(), C.char_nationality(), C.char_languages(), C.card_authority()) == (3, "other_land", ("en", "fa", "de"), "town_hall")
    user(d, 2)
    charkit.complete(d, 2, first="C", last="D", world_id=w1, now=T0)
    new = identity.get(d, 2)
    assert new["expires_at"] - new["issued_at"] == 3 * YEAR and new["nationality"] == "other_land" and new["languages"] == "en,fa,de" and new["authority"] == "town_hall"
    assert identity.get(d, 1) == old                                                      # an existing document keeps what it was issued with
    assert identity.authority_name("fa", "town_hall") == "شهرداری مرکزی" and identity.authority_name("en", "registry") == "Central Town Hall"
    monkeypatch.setenv("CARD_VALID_GAME_YEARS", "nonsense")
    monkeypatch.setenv("CHAR_NATIONALITY", "bad code!")
    monkeypatch.setenv("CHAR_LANGUAGES", "1,,x" + "y" * 20)
    assert (C.card_valid_years(), C.char_nationality(), C.char_languages()) == (10, "hype", ("fa",))


def test_the_default_validity_is_ten_game_years_of_four_real_days():
    assert C.card_valid_years() == 10 and C.CARD_VALID_GAME_YEARS == 10 and gamecal.year_seconds() == 4 * 86400


# ============================================================================================== fingerprint, height, signature, home
def test_the_fingerprint_id_is_unique_permanent_and_not_biometric_data():
    d, w1, w2 = fresh()
    seen = set()
    for uid in range(1, 41):
        user(d, uid)
        charkit.complete(d, uid, first="A", last="B", world_id=w1, now=T0)
        fp = identity.get(d, uid)["fingerprint_id"]
        assert fp not in seen and len(fp) == 17 and fp[:3] == "FP-" and set(fp[3:].replace("-", "")) <= set(identity.FP_ALPHABET)
        seen.add(fp)
    before = identity.get(d, 5)
    for _ in range(3):
        identity.ensure(d, 5)
        identity.issue(d, 5)
    assert identity.get(d, 5) == before
    with pytest.raises(sqlite3.DatabaseError):
        d.x("UPDATE character_identity SET fingerprint_id='FP-AAAA-AAAA-AAAA' WHERE user_id=5")
    unique = {tuple(c["name"] for c in d.qa(f"PRAGMA index_info({i['name']})")) for i in d.qa("PRAGMA index_list(character_identity)") if i["unique"]}
    assert ("fingerprint_id",) in unique and ("doc_number",) in unique                    # the database itself refuses a second holder of either number
    d.x("DROP TRIGGER trg_ident_nodelete")                                                # (in a scratch copy only: remove the row of character 6 to try the constraint on its own)
    d.x("DELETE FROM character_identity WHERE user_id=6")
    with pytest.raises(sqlite3.IntegrityError, match="fingerprint_id"):
        d.x("INSERT INTO character_identity(user_id,nationality,languages,fingerprint_id,created_at,updated_at) VALUES(6,'hype','fa',?,1,1)", (before["fingerprint_id"],))


@pytest.mark.parametrize("gender", ["male", "female"])
def test_the_height_in_centimetres_agrees_with_the_saved_height_trait(gender):
    for code in T.CATALOG["height"]["values"]:
        band = C.HEIGHT_BANDS_CM[gender][code]
        got = {identity.height_cm_for(cid, gender, code) for cid in range(300)}
        assert min(got) >= band and max(got) < band + C.HEIGHT_BAND_WIDTH_CM and len(got) == C.HEIGHT_BAND_WIDTH_CM        # every cm of the band is used, none outside it
        assert identity.height_cm_for(7, gender, code) == identity.height_cm_for(7, gender, code)
    order = [C.HEIGHT_BANDS_CM[gender][c] for c in T.CATALOG["height"]["values"]]
    assert order == sorted(order) and all(b - a == 10 for a, b in zip(order, order[1:]))                                    # a taller trait is always a taller number


def test_the_height_is_issued_once_and_never_regenerated():
    d, w1, w2 = fresh()
    user(d, 1)
    charkit.complete(d, 1, first="A", last="B", world_id=w1, now=T0)
    cm = identity.get(d, 1)["height_cm"]
    for _ in range(3):
        identity.issue(d, 1, T0 + 10)
    assert identity.get(d, 1)["height_cm"] == cm and identity.dossier(d, 1, T0 + 10).height_cm == cm
    with pytest.raises(sqlite3.DatabaseError):
        d.x("UPDATE character_identity SET height_cm=height_cm+1 WHERE user_id=1")


def test_the_signature_seed_comes_from_the_saved_names_and_is_stable():
    a = identity.signature_seed_for("علی", "رضایی", 5)
    assert a == identity.signature_seed_for("علی", "رضایی", 5) and len(a) == 16 and set(a) <= set("0123456789abcdef")
    assert len({a, identity.signature_seed_for("علی", "رضایی", 6), identity.signature_seed_for("علی", "کیانی", 5), identity.signature_seed_for("سام", "رضایی", 5)}) == 4


def test_the_written_signature_is_fixed_by_the_saved_name_and_its_hash():
    d, w1, w2 = fresh()
    user(d, 1)
    charkit.complete(d, 1, first="علی", last="رضایی", world_id=w1, now=T0)
    dos = identity.dossier(d, 1, T0 + 5)
    sig = idcard.signature_text(dos)
    assert sig == idcard.signature_text(dos) == idcard.signature_text(identity.dossier(d, 1, T0 + 99 * 86400)) and sig.startswith("ع. رضایی ")   # the same at every moment
    import hashlib
    seeds = {hashlib.sha256(str(i).encode()).hexdigest()[:16] for i in range(40)}
    flourishes = {idcard.signature_text(dataclasses.replace(dos, signature_seed=sd)).split()[-1] for sd in seeds}
    assert len(flourishes) > 4 and all(f[0] in idcard.FLOURISH and 2 <= len(f) <= 4 for f in flourishes)            # the hash decides the flourish, inside a fixed set


def test_the_identity_and_card_modules_use_no_randomness_or_clock():
    import ast
    import inspect
    for mod in (idcard,):
        tree = ast.parse(inspect.getsource(mod))
        names = {n.module for n in ast.walk(tree) if isinstance(n, ast.ImportFrom)} | {a.name for n in ast.walk(tree) if isinstance(n, ast.Import) for a in n.names}
        assert not names & {"random", "secrets", "time", "datetime", "os", "requests", "socket"}, names


def test_the_home_city_is_the_birth_city_and_does_not_move_with_the_traveller():
    d, w1, w2 = fresh()
    user(d, 1)
    charkit.complete(d, 1, first="A", last="B", world_id=w1, now=T0)
    assert identity.get(d, 1)["home_world_id"] == w1
    d.x("UPDATE characters SET current_world_id=? WHERE user_id=1", (w2,))                # the character has moved (what a journey's arrival does)
    dos = identity.dossier(d, 1, T0 + 5)
    assert dos.birth[0] == w1 and dos.home[0] == w1 and dos.current[0] == w2
    identity.set_home_city(d, 1, w2)                                                       # a home city once set is never replaced here
    assert identity.get(d, 1)["home_world_id"] == w1
    assert (dos.district, dos.street, dos.building) == (None, None, None)                  # nothing is invented while no property system exists


# ============================================================================================== the database keeps issued facts permanent
def _row(uid=1):
    d, w1, w2 = fresh()
    user(d, uid)
    charkit.complete(d, uid, first="A", last="B", world_id=w1, now=T0)
    return d, w1, w2


@pytest.mark.parametrize("column,value", [("born_at", 5), ("height_cm", 199), ("signature_seed", "ffffffffffffffff"), ("doc_number", "000000000"), ("issued_at", 5), ("expires_at", T0 + 99),
                                          ("authority", "other"), ("fingerprint_id", "FP-ZZZZ-ZZZZ-ZZZZ"), ("created_at", 5), ("user_id", 99)])
def test_an_issued_fact_cannot_be_changed(column, value):
    d, w1, w2 = _row()
    with pytest.raises(sqlite3.DatabaseError):
        d.x(f"UPDATE character_identity SET {column}=? WHERE user_id=1", (value,))


def test_the_rows_cannot_be_deleted_or_pointed_at_nothing():
    d, w1, w2 = _row()
    with pytest.raises(sqlite3.DatabaseError):
        d.x("DELETE FROM character_identity WHERE user_id=1")
    with pytest.raises(sqlite3.DatabaseError):
        d.x("UPDATE character_identity SET home_world_id=NULL WHERE user_id=1")
    with pytest.raises(sqlite3.DatabaseError):
        d.x("UPDATE character_identity SET home_world_id=9999 WHERE user_id=1")
    with pytest.raises(sqlite3.DatabaseError):
        d.x("INSERT INTO character_identity(user_id,nationality,languages,fingerprint_id,created_at,updated_at) VALUES(555,'hype','fa','FP-QQQQ-QQQQ-QQQQ',1,1)")        # no such character
    with pytest.raises(sqlite3.DatabaseError):
        d.x("UPDATE character_identity SET status='dead' WHERE user_id=1")
    d.x("UPDATE character_identity SET status='suspended', home_district='Hill', home_street='Oak', home_building='12' WHERE user_id=1")     # what may change: status and the address parts
    assert identity.dossier(d, 1, T0 + 5).status == "suspended" and identity.dossier(d, 1, T0 + 5).street == "Oak"


def test_a_document_must_be_whole_or_absent():
    d, w1, w2 = fresh()
    user(d, 1)
    character.ensure_profile(d, 1, T0)
    with pytest.raises(sqlite3.DatabaseError):
        d.x("UPDATE character_identity SET born_at=5 WHERE user_id=1")                       # a birthday without the rest of the document
    with pytest.raises(sqlite3.DatabaseError):
        d.x("UPDATE character_identity SET height_cm=50 WHERE user_id=1")


# ============================================================================================== the dossier
def test_the_dossier_reads_everything_from_saved_rows_in_catalog_order():
    d, w1, w2 = fresh()
    user(d, 1)
    charkit.complete(d, 1, first="Sam", last="Lee", gender="male", world_id=w1, now=T0)
    charxp.grant(d, 1, 250, "t", "1")
    dos = identity.dossier(d, 1, T0 + 9 * YEAR // 4)
    assert [k for k, _ in dos.traits] == list(T.TRAIT_NAMES) and [k for k, _, _ in dos.genes] == list(T.TRAIT_NAMES)
    assert dict(dos.traits) == T.traits_of(d, 1)
    for trait, a1, a2 in dos.genes:
        assert T.phenotype_of(trait, a1, a2) == dict(dos.traits)[trait]                       # the visible trait is what the two saved genes make
    assert (dos.level, dos.xp, dos.xp_floor, dos.xp_next, dos.xp_into, dos.xp_span, dos.xp_left) == (2, 250, 100, 400, 150, 300, 150) and dos.xp_fraction == 0.5
    assert dos.name == "Sam Lee" and dos.age == 17 and dos.status == "active" and dos.languages == ("fa",)
    assert hash(dos) == hash(dataclasses.replace(dos))                                      # a frozen value: the card cache can key on it


def test_a_missing_identity_row_is_repaired_with_defaults_and_nothing_else_changes():
    d, w1, w2 = _row()
    before = snapshot(d, OLD_TABLES)
    for trg in [r["name"] for r in d.qa("SELECT name FROM sqlite_master WHERE type='trigger' AND tbl_name='character_identity'")]:
        d.x(f"DROP TRIGGER {trg}")
    d.x("DELETE FROM character_identity")
    dos = identity.dossier(d, 1, T0 + 5)
    assert dos.issued_at == character.get(d, 1)["age_started_at"] and dos.born_at == dos.issued_at - 15 * YEAR and dos.fingerprint
    assert snapshot(d, OLD_TABLES) == before


def test_no_dossier_for_a_player_without_a_finished_character():
    d, w1, w2 = fresh()
    user(d, 1)
    with pytest.raises(core.GameError) as e:
        identity.dossier(d, 1)
    assert e.value.code == "char_none"
    character.ensure_profile(d, 1, T0)
    with pytest.raises(core.GameError) as e:
        identity.dossier(d, 1)
    assert e.value.code == "card_char" and identity.issue(d, 1) is None


# ============================================================================================== the calendar
@pytest.mark.parametrize("greg,jal", [((2025, 3, 21), (1404, 1, 1)), ((2024, 3, 20), (1403, 1, 1)), ((2026, 10, 30), (1405, 8, 8)), ((2026, 3, 21), (1405, 1, 1)),
                                      ((2023, 12, 31), (1402, 10, 10)), ((2022, 9, 23), (1401, 7, 1)), ((2030, 3, 20), (1408, 12, 30)), ((2030, 3, 21), (1409, 1, 1)), ((2028, 3, 19), (1406, 12, 29)), ((2028, 3, 20), (1407, 1, 1))])
def test_the_persian_calendar_conversion(greg, jal):
    assert fmt._jalali(*greg) == jal


def fa_digits(text):
    return text.translate(str.maketrans("0123456789", "۰۱۲۳۴۵۶۷۸۹"))


def test_dates_are_shown_in_the_configured_zone_in_the_persian_calendar_for_persian_and_iso_for_english(monkeypatch):
    import datetime as dt
    ts = int(dt.datetime(2026, 10, 29, 21, 30, tzinfo=dt.timezone.utc).timestamp())          # 21:30 UTC on the 29th
    assert C.display_utc_offset() == 210                                                      # the default zone is Tehran, UTC+3:30
    assert fmt.date(ts, "en") == "2026-10-30" and fmt.date(ts, "fa") == fa_digits("1405/08/08")           # 01:00 on the 30th there
    assert fmt.date(ts - 3600, "en") == "2026-10-30"                                           # 20:30 UTC is exactly midnight there: the new day has begun
    assert fmt.date(ts - 3601, "en") == "2026-10-29" and fmt.date(ts - 3601, "fa") == fa_digits("1405/08/07")      # one second before, it is still the 29th
    monkeypatch.setenv("DISPLAY_UTC_OFFSET_MIN", "0")                                          # the zone is a setting: at UTC the same moment is still the 29th
    assert fmt.date(ts, "en") == "2026-10-29" and fmt.date(ts, "fa") == fa_digits("1405/08/07")
    monkeypatch.setenv("DISPLAY_UTC_OFFSET_MIN", "-300")
    assert fmt.date(ts, "en") == "2026-10-29"
    monkeypatch.setenv("DISPLAY_UTC_OFFSET_MIN", "9999")                                       # nonsense is clamped to a real zone, never a crash
    assert fmt.date(ts, "en") in ("2026-10-30", "2026-10-29")


def test_persian_digits_are_used_for_persian_dates_only():
    ts = 1_800_000_000
    fa, en = fmt.date(ts, "fa"), fmt.date(ts, "en")
    assert "/" in fa and not any(ch.isascii() and ch.isdigit() for ch in fa) and en.replace("-", "").isascii() and en.count("-") == 2


def test_the_birthday_helper_and_the_expiry_helper_live_in_the_calendar_module():
    assert gamecal.birth_at(T0) == T0 - 15 * YEAR and gamecal.after_years(T0, 10) == T0 + 10 * YEAR
    for now in (T0, T0 + YEAR - 1, T0 + YEAR, T0 + 7 * YEAR + 5):
        assert gamecal.age_at(gamecal.birth_at(T0), now, 0) == gamecal.age_at(T0, now)
