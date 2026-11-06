"""Brands, headquarters and the commercial capital (v0.211).

THE REGISTRY. The brands that can be registered are a small configured list (`config.brand_names()`: NIKE, BALENCIAGA, GIVENCHY, LOUIS VUITTON by default; the environment variable BRAND_NAMES replaces it,
no code changes). A brand has ONE owner in the whole game: the `brands` table's primary key is the brand key, so two players claiming one name at the same time cannot both win, whatever the code does
(the loser's INSERT fails and the registration fee that was charged with it is rolled back). Registering costs `config.BRAND_FEE_CENTS`, nothing else is given with it: no property, no branch, no
stock. A player owns at most `config.BRAND_MAX_PER_PLAYER` brands (checked inside the same transaction). The owner never changes and a brand is never deleted (triggers).

BRAND, PROPERTY, BRANCH: three different things. The brand is a registered NAME. A property is a lot a player bought. A branch is a business of one brand at one property. Having one never gives another.

HEADQUARTERS. A brand's headquarters is the CITY of its first active store: the oldest OPEN branch of the brand whose property is fitted for retail (retail only, or retail and production). It is not stored; it is worked
out from the branches, so it follows every opening and closing by itself: when the first store closes, the headquarters moves to the next oldest open store, and a brand with no open store has none.
Further branches (in the same city or another) never change it and never count twice.

THE COMMERCIAL CAPITAL. The city that is the headquarters of the most DIFFERENT brands. One capital at most; a tie keeps the current capital when it is among the tied cities (otherwise the tied city whose
oldest headquarters is older, then the lower city id); no headquarters at all means no capital. It is saved in `kv` ('commercial_capital') so that «keep the current one» has something to keep, and
`refresh_capital` runs after every registration-relevant change (a branch opening or closing).
"""
from __future__ import annotations

from . import cash, character, config as C, core
from .core import GameError

CAPITAL_KEY = "commercial_capital"


def catalog() -> dict:
    """{brand key: display name} of the brands that can be registered now."""
    return C.brand_names()


def name_of(key: str) -> str:
    return catalog().get(key) or (key or "").replace("_", " ").upper()


def get(db, key: str):
    return db.q1("SELECT * FROM brands WHERE brand=?", (key,))


def of_player(db, uid: int) -> list:
    return db.qa("SELECT * FROM brands WHERE owner_id=? ORDER BY registered_at, brand", (uid,))


def registered(db) -> dict:
    """{brand key: owner id} of every registered brand."""
    return {r["brand"]: r["owner_id"] for r in db.qa("SELECT brand, owner_id FROM brands")}


def register(db, uid: int, key: str, now: int | None = None):
    """Claim brand `key` for player `uid`. GameError: no_user, no_character, brand_unknown (not on the list), brand_taken (somebody owns it), brand_limit, cash_low.
    The claim (an INSERT into the table whose primary key is the brand) and the fee are ONE transaction: whoever loses a race gets brand_taken and is never charged."""
    now = core.now() if now is None else now
    if key not in catalog():
        raise GameError("brand_unknown")
    with db.tx():
        u = core.get_user(db, uid)
        if u is None or u["bot"]:
            raise GameError("no_user")
        ch = character.get(db, uid)
        if ch is None or not character.is_done(ch):
            raise GameError("no_character")
        if get(db, key) is not None:
            raise GameError("brand_taken")
        limit = C.BRAND_MAX_PER_PLAYER
        if limit and db.q1("SELECT COUNT(*) c FROM brands WHERE owner_id=?", (uid,))["c"] >= limit:
            raise GameError("brand_limit")
        try:
            db.x("INSERT INTO brands(brand,owner_id,fee,registered_at) VALUES(?,?,?,?)", (key, uid, C.BRAND_FEE_CENTS, now))
        except Exception as e:                                                    # sqlite3.IntegrityError: another connection won the name between the check and here
            if "UNIQUE" in str(e) or "constraint" in str(e).lower():
                raise GameError("brand_taken") from None
            raise
        if C.BRAND_FEE_CENTS:
            if cash.apply(db, uid, -C.BRAND_FEE_CENTS, "brand_fee", key, now=now) is None:
                raise GameError("brand_taken")
        from . import estate
        estate.event(db, "brand_registered", key, uid, {"fee": C.BRAND_FEE_CENTS}, now)
    return get(db, key)


# =====================================================================================================================
#                                                  headquarters and the capital
# =====================================================================================================================
def stores(db, brand: str) -> list:
    """The open branches of `brand` that are STORES (their property is fitted for retail), oldest first."""
    return db.qa("SELECT b.* FROM branches b JOIN properties p ON p.id=b.property_id WHERE b.brand=? AND b.state='open' AND p.mode IN ('retail','both') ORDER BY b.opened_at, b.id", (brand,))


def hq(db, brand: str) -> int | None:
    """The world (city) id of the brand's headquarters, or None when it has no open store."""
    s = stores(db, brand)
    return s[0]["world_id"] if s else None


def hq_branch(db, brand: str):
    s = stores(db, brand)
    return s[0] if s else None


def headquarters(db) -> dict:
    """{brand: world id} of every brand that has a headquarters."""
    out = {}
    for r in db.qa("SELECT DISTINCT brand FROM branches WHERE state='open'"):
        w = hq(db, r["brand"])
        if w is not None:
            out[r["brand"]] = w
    return out


def counts(db) -> dict:
    """{world id: number of DIFFERENT brands headquartered there}."""
    out: dict = {}
    for _, w in headquarters(db).items():
        out[w] = out.get(w, 0) + 1
    return out


def capital(db) -> int | None:
    """The saved commercial capital (a world id) or None."""
    r = db.q1("SELECT value FROM kv WHERE key=?", (CAPITAL_KEY,))
    try:
        return int(r["value"]) if r and r["value"] not in (None, "") else None
    except (TypeError, ValueError):
        return None


def refresh_capital(db, now: int | None = None) -> int | None:
    """Recompute the commercial capital and save it. One capital; a tie keeps the current one when it is among the leaders. Returns the capital (a world id) or None."""
    cnt = counts(db)
    cur = capital(db)
    if not cnt:
        new = None
    else:
        top = max(cnt.values())
        leaders = sorted(w for w, n in cnt.items() if n == top)
        if len(leaders) == 1:
            new = leaders[0]
        elif cur in leaders:
            new = cur
        else:
            oldest = {}
            for brand, w in headquarters(db).items():
                if w in leaders:
                    b = hq_branch(db, brand)
                    oldest[w] = min(oldest.get(w, (b["opened_at"], b["id"])), (b["opened_at"], b["id"]))
            new = min(leaders, key=lambda w: (oldest.get(w, (0, 0)), w))
    if new != cur:
        with db.tx():
            if new is None:
                db.x("DELETE FROM kv WHERE key=?", (CAPITAL_KEY,))
            else:
                db.x("INSERT OR REPLACE INTO kv(key,value) VALUES(?,?)", (CAPITAL_KEY, str(new)))
            from . import estate
            estate.event(db, "capital_changed", str(new), None, {"from": cur, "to": new}, core.now() if now is None else now)
    return new
