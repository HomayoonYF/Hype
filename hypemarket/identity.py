"""The identity document of a character (v0.20): everything a national ID shows besides what the v0.19 profile already holds.

One row of `character_identity` per character. What it holds and when it is decided:

* AT BIRTH (the row is created together with the character): the nationality and the known languages (game-world defaults from `config`, never read from Telegram or from a
  location), the citizen status (Active), the fictional FINGERPRINT ID (random, unique, permanent: an in-game reference, not biometric data) and, once the character has a birth
  city, the HOME CITY (the birth city; it does not move when the character travels). The district, street and building of the home address stay empty: no property system
  exists yet, so nothing is invented and the screens say «not assigned».
* WHEN THE PROFILE IS FINISHED (`issue`, in the same transaction that finishes it; backfilled for characters that were already finished): the birthday, the height in
  centimetres, the signature seed, the document number, the issue and expiry dates and the issuing authority. Each is written once and then locked by triggers.

THE TIME MODEL. The age is still worked out from the one saved anchor `characters.age_started_at` (v0.19: 15 at the anchor, one more year per four complete real days). The
birthday is the anchor minus 15 game years, so «now − birthday» in game years IS the age, and a character's age never changes because of this module (the backfill of an
existing character reads its anchor and nothing else). The issue date is the anchor too: the document was issued the moment the profile was finished.

HEIGHT. The saved height trait (very_short .. very_tall) picks a band of `config.HEIGHT_BAND_WIDTH_CM` centimetres per gender, and a fixed offset inside it comes from a hash of
the citizen ID: the same character always has the same height, and it always agrees with the trait the appearance screens show. Weight and body condition do not exist yet (a later body
system) and nothing here pretends otherwise.

THE SIGNATURE. A stylised signature is written (idcard.signature_text) from the saved name and `signature_seed`, a hash of the saved names and the citizen ID, so it never changes.

Everything is read from saved rows; `dossier` repairs a missing row (idempotent) but never changes anything that exists.
"""
from __future__ import annotations

import hashlib
import secrets
import sqlite3
from dataclasses import dataclass

from . import character, charxp, config as C, core, gamecal, traits as T
from .core import GameError

FP_ALPHABET = "ABCDEFGHJKLMNPQRSTUVWXYZ23456789"


# =====================================================================================================================
#                                                   things that are derived or drawn once
# =====================================================================================================================
def new_fingerprint() -> str:
    """A fictional fingerprint ID, `FP-XXXX-XXXX-XXXX` (60 random bits from the operating system's generator)."""
    raw = "".join(secrets.choice(FP_ALPHABET) for _ in range(12))
    return "FP-" + "-".join(raw[i:i + 4] for i in range(0, 12, 4))


def new_doc_number() -> str:
    """A document number, nine random digits (`482193507`); shown grouped by three."""
    return f"{secrets.randbelow(10 ** 9):09d}"


def height_cm_for(citizen_id: int, gender: str, height_code: str) -> int:
    """The height in centimetres of a character with this citizen ID, gender and saved height trait: the band of the trait plus a fixed offset from a hash of the ID."""
    base = C.HEIGHT_BANDS_CM[gender][height_code]
    h = hashlib.sha256(f"height:{int(citizen_id)}".encode()).digest()
    return base + int.from_bytes(h[:4], "big") % C.HEIGHT_BAND_WIDTH_CM


def signature_seed_for(first: str, last: str, citizen_id: int) -> str:
    """16 hex digits that fix a character's signature: a hash of the saved names and the citizen ID."""
    return hashlib.sha256(f"signature:{first}|{last}|{int(citizen_id)}".encode("utf-8")).hexdigest()[:16]


def born_at_for(issued_at: int) -> int:
    """The birthday (a timestamp) of a character whose age anchor is `issued_at` (the calendar module owns the formula)."""
    return gamecal.birth_at(issued_at)


def expires_at_for(issued_at: int) -> int:
    return gamecal.after_years(issued_at, C.card_valid_years())


# =====================================================================================================================
#                                                       the row
# =====================================================================================================================
def get(db, uid: int):
    return db.q1("SELECT * FROM character_identity WHERE user_id=?", (uid,))


def ensure(db, uid: int, now: int | None = None):
    """The character's identity row, created (once) if it is missing: defaults, a new fingerprint, the birth city as the home city. None when the player has no character.
    Idempotent and race-safe (it runs inside a write transaction)."""
    row = get(db, uid)
    if row is not None:
        return row
    now = core.now() if now is None else now
    with db.tx():
        row = get(db, uid)
        if row is not None:
            return row
        ch = character.get(db, uid)
        if ch is None:
            return None
        for _ in range(12):
            try:
                db.x("INSERT INTO character_identity(user_id,nationality,languages,fingerprint_id,home_world_id,created_at,updated_at) VALUES(?,?,?,?,?,?,?)",
                     (uid, C.char_nationality(), ",".join(C.char_languages()), new_fingerprint(), ch["birth_world_id"], now, now))
                break
            except sqlite3.IntegrityError:                                       # the (astronomically unlikely) fingerprint clash: draw again
                if get(db, uid) is not None:
                    break
                continue
        else:
            raise RuntimeError("could not draw an unused fingerprint ID")
        return get(db, uid)


def set_home_city(db, uid: int, world_id: int, now: int | None = None) -> None:
    """The home city of a character that has none yet: the city it was born in. A home city once set is never replaced here (a trigger also refuses clearing it)."""
    now = core.now() if now is None else now
    with db.tx():
        ensure(db, uid, now)
        db.x("UPDATE character_identity SET home_world_id=?, updated_at=? WHERE user_id=? AND home_world_id IS NULL", (world_id, now, uid))


def issue(db, uid: int, now: int | None = None):
    """Issue the document of a FINISHED character, once: birthday, height, signature, document number, issue / expiry dates and the authority. A repeat changes nothing.
    The issue date is the age anchor. None when the character is not finished (nothing is issued before the profile exists)."""
    now = core.now() if now is None else now
    with db.tx():
        ch = character.get(db, uid)
        if not character.is_done(ch):
            return None
        row = ensure(db, uid, now)
        if row["issued_at"] is not None:
            return row
        tr = T.traits_of(db, uid)
        if tr is None:
            raise GameError("card_traits")
        issued = ch["age_started_at"]
        for _ in range(12):
            try:
                db.x("UPDATE character_identity SET born_at=?, height_cm=?, signature_seed=?, doc_number=?, issued_at=?, expires_at=?, authority=?, updated_at=? "
                     "WHERE user_id=? AND issued_at IS NULL",
                     (born_at_for(issued), height_cm_for(ch["citizen_id"], ch["gender"], tr["height"]), signature_seed_for(ch["first_name"], ch["last_name"], ch["citizen_id"]),
                      new_doc_number(), issued, expires_at_for(issued), C.card_authority(), now, uid))
                break
            except sqlite3.IntegrityError:                                       # a document number clash: draw another
                continue
        else:
            raise RuntimeError("could not draw an unused document number")
        return get(db, uid)


def backfill(db, now: int | None = None) -> int:
    """The schema-12 migration: an identity row for every character that has none (ascending user ID), issued when the character is finished. Returns how many rows were
    created. Writes only new rows; a character that has a row is skipped, so it is safe to repeat."""
    now = core.now() if now is None else now
    made = 0
    for r in db.qa("SELECT user_id FROM characters WHERE user_id NOT IN (SELECT user_id FROM character_identity) ORDER BY user_id"):
        ensure(db, r["user_id"], now)
        issue(db, r["user_id"], now)
        made += 1
    return made


# =====================================================================================================================
#                                                     names for display
# =====================================================================================================================
def nationality_name(lang: str, code: str) -> str:
    return _name(lang, "nat." + code, code)


def trait_name(lang: str, trait: str) -> str:
    """The word for a trait («skin tone»)."""
    return _name(lang, "tr.n." + trait, trait)


def trait_value(lang: str, trait: str, code: str) -> str:
    """The word for one value of a trait («olive»)."""
    return _name(lang, f"tr.{trait}.{code}", code)


def language_names(lang: str, codes) -> list:
    return [_name(lang, "ln." + c, c) for c in codes]


def authority_name(lang: str, code: str) -> str:
    """The issuing authority's name: env CARD_AUTHORITY_FA / CARD_AUTHORITY_EN when set (any name, no code edit), else the text `auth.<code>`."""
    import os
    over = os.environ.get("CARD_AUTHORITY_" + ("FA" if lang == "fa" else "EN"), "").strip()
    return over[:60] if over else _name(lang, "auth." + code, code)


def status_name(lang: str, code: str) -> str:
    return _name(lang, "status." + code, code)


def _name(lang: str, key: str, fallback: str) -> str:
    from .texts import STR
    return STR.get(lang, STR["en"]).get(key) or STR["en"].get(key) or fallback


# =====================================================================================================================
#                                                  everything a screen needs, in one read
# =====================================================================================================================
@dataclass(frozen=True)
class Dossier:
    """The saved facts about one finished character, as one immutable value (what the ID card, the profile, the appearance, the genetics, the personal-information and the
    progression screens all draw from). Cities are (id, name_fa, name_en) or None."""
    user_id: int
    first: str
    last: str
    citizen_id: int
    gender: str
    age: int
    born_at: int
    height_cm: int
    traits: tuple                      # ((trait, code), ...) in catalog order: the visible phenotype
    genes: tuple                       # ((trait, allele1, allele2), ...) in catalog order
    birth: tuple | None
    current: tuple | None              # None while the character is on a journey (see `to`) or has no city yet
    to: tuple | None                   # the destination of a journey in progress
    home: tuple | None
    district: str | None
    street: str | None
    building: str | None
    nationality: str
    languages: tuple
    fingerprint: str
    signature_seed: str
    doc_number: str
    issued_at: int
    expires_at: int
    authority: str
    status: str
    xp: int
    level: int
    xp_floor: int                      # the XP at which the current level began
    xp_next: int                       # the XP at which the next level begins

    @property
    def name(self) -> str:
        return f"{self.first} {self.last}"

    @property
    def xp_into(self) -> int:
        return self.xp - self.xp_floor

    @property
    def xp_span(self) -> int:
        return self.xp_next - self.xp_floor

    @property
    def xp_left(self) -> int:
        return self.xp_next - self.xp

    @property
    def xp_fraction(self) -> float:
        return min(1.0, max(0.0, self.xp_into / self.xp_span))

    @property
    def trait_map(self) -> dict:
        return dict(self.traits)


def _city(db, city_id) -> tuple | None:
    if city_id is None:
        return None
    w = db.q1("SELECT id, name_fa, name_en FROM worlds WHERE id=?", (city_id,))
    return (w["id"], w["name_fa"], w["name_en"]) if w is not None else (city_id, "?", "?")


def dossier(db, uid: int, now: int | None = None) -> Dossier:
    """The dossier of player `uid`. GameError: char_none (no character), card_char (not finished: the caller sends them to the set-up), card_traits (a finished character
    without saved traits or genes: a damaged database, reported and never drawn as somebody else). `intercity.active` decides `current` / `to`: during a journey the
    character is on the road and the current city is not claimed. A missing identity row is repaired first."""
    from . import intercity
    now = core.now() if now is None else now
    ch = character.get(db, uid)
    if ch is None:
        raise GameError("char_none")
    if not character.is_done(ch):
        raise GameError("card_char")
    row = issue(db, uid, now)
    tr, ge = T.traits_of(db, uid), T.genes_of(db, uid)
    if tr is None or ge is None or row is None:
        raise GameError("card_traits")
    xp = charxp.get(db, uid) or 0
    level = charxp.level_for(xp)
    trip = intercity.active(db, uid)
    return Dossier(
        user_id=uid, first=ch["first_name"], last=ch["last_name"], citizen_id=ch["citizen_id"], gender=ch["gender"], age=character.age(ch, now), born_at=row["born_at"],
        height_cm=row["height_cm"], traits=tuple((n, tr[n]) for n in T.TRAIT_NAMES), genes=tuple((n, ge[n][0], ge[n][1]) for n in T.TRAIT_NAMES),
        birth=_city(db, ch["birth_world_id"]), current=None if trip is not None else _city(db, ch["current_world_id"]), to=_city(db, trip["to_world_id"]) if trip is not None else None,
        home=_city(db, row["home_world_id"]), district=row["home_district"], street=row["home_street"], building=row["home_building"],
        nationality=row["nationality"], languages=tuple(p for p in row["languages"].split(",") if p), fingerprint=row["fingerprint_id"], signature_seed=row["signature_seed"],
        doc_number=row["doc_number"], issued_at=row["issued_at"], expires_at=row["expires_at"], authority=row["authority"], status=row["status"],
        xp=xp, level=level, xp_floor=charxp.xp_for_level(level), xp_next=charxp.xp_for_level(level + 1))
