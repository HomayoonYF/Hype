"""Stable physical traits of a character: the catalog, the genes and the visible phenotype (v0.19).

Two kinds of saved data, kept apart on purpose:

* **genes** (`character_genes`): for every trait in the catalog two alleles, each one a code of that trait. This is the INHERITABLE part: a future
  family system will build a child's genes from one allele of each parent, probabilistically (it needs nothing but these two columns).
* **traits** (`character_traits`): the visible phenotype, one code per trait, derived from the genes by the catalog's rule and saved with them.
  This is what a future portrait renderer reads. It is saved, not recomputed per view, so it can never change by reading it.

Both are written ONCE, in the transaction that creates the character, and the database refuses every later change (triggers in db.py): a retry, a
restart, a migration run twice or a read can never reroll an appearance. Changing body condition (weight, fitness, ...) is a different, future
system and will live in its own table; nothing here is meant to change.

The catalog is plain data. To add a trait or a value later: add it to `CATALOG` (values are stable codes, never display text, and they are never renamed
or removed once saved) and backfill existing characters in a migration. Rules: `dominant` = the allele listed first wins; `scale` = an ordered scale where
the phenotype is the middle of the two alleles (an odd sum goes to the value nearer the centre of the scale).

Nothing here depends on a Telegram name, a group or a clock.
"""
from __future__ import annotations

import random

CATALOG: dict = {
    "height":     {"rule": "scale",    "values": ["very_short", "short", "average", "tall", "very_tall"], "weights": [1, 3, 6, 3, 1]},
    "build":      {"rule": "scale",    "values": ["slim", "average", "sturdy"],                           "weights": [3, 5, 3]},
    "skin_tone":  {"rule": "scale",    "values": ["very_light", "light", "medium", "olive", "tan", "brown", "dark"], "weights": [1, 3, 5, 5, 4, 2, 1]},
    "hair_color": {"rule": "dominant", "values": ["black", "dark_brown", "brown", "blond", "red"],       "weights": [10, 8, 5, 2, 1]},
    "hair_style": {"rule": "scale",    "values": ["straight", "wavy", "curly"],                           "weights": [4, 4, 3]},
    "eye_color":  {"rule": "dominant", "values": ["brown", "hazel", "green", "blue"],                     "weights": [10, 4, 2, 2]},
    "face_shape": {"rule": "dominant", "values": ["oval", "round", "square", "heart"],                    "weights": [4, 4, 3, 2]},
}
TRAIT_NAMES = tuple(CATALOG)                      # a stable order: used wherever the traits are listed or saved


class TraitError(ValueError):
    """A trait or an allele that is not in the catalog (a damaged or hand-edited row)."""


def check_catalog() -> None:
    """The catalog itself is valid (run by the tests, and cheap enough to run at import time of anything that edits it)."""
    for name, spec in CATALOG.items():
        vals = spec["values"]
        if spec["rule"] not in ("dominant", "scale") or len(vals) < 2 or len(set(vals)) != len(vals):
            raise TraitError(name)
        if len(spec["weights"]) != len(vals) or any((not isinstance(w, int)) or w <= 0 for w in spec["weights"]):
            raise TraitError(name)
        if not all(isinstance(v, str) and v and v == v.lower() and v.replace("_", "").isalnum() and v.isascii() for v in vals):
            raise TraitError(name)


def _spec(trait: str) -> dict:
    spec = CATALOG.get(trait)
    if spec is None:
        raise TraitError(f"unknown trait {trait!r}")
    return spec


def _index(trait: str, allele) -> int:
    vals = _spec(trait)["values"]
    if not isinstance(allele, str) or allele not in vals:
        raise TraitError(f"{allele!r} is not a value of {trait}")
    return vals.index(allele)


# --- rolling and deriving -------------------------------------------------------------------------------------------------------------------
def roll_genes(rng: random.Random | None = None) -> dict:
    """A new character's genes: {trait: (allele1, allele2)}, each allele drawn independently by the catalog's weights. `rng` defaults to the operating
    system's generator (never the global `random`, never seeded by anything about the player); tests pass their own."""
    rng = rng or random.SystemRandom()
    out = {}
    for name in TRAIT_NAMES:
        spec = CATALOG[name]
        a, b = rng.choices(spec["values"], weights=spec["weights"], k=2)
        out[name] = (a, b)
    return out


def phenotype_of(trait: str, a1: str, a2: str) -> str:
    """The visible value for the alleles (a1, a2) of one trait. Symmetric: the order of the two alleles never matters."""
    spec = _spec(trait)
    i, j = _index(trait, a1), _index(trait, a2)
    if spec["rule"] == "dominant":
        return spec["values"][min(i, j)]
    total = i + j
    low = total // 2
    high = low + total % 2
    centre = (len(spec["values"]) - 1) / 2
    pick = low if abs(low - centre) <= abs(high - centre) else high
    return spec["values"][pick]


def phenotype(genes: dict) -> dict:
    """{trait: code} for a full set of genes (every trait of the catalog, no more, no less)."""
    if set(genes) != set(TRAIT_NAMES):
        raise TraitError("genes must cover exactly the catalog")
    return {name: phenotype_of(name, *genes[name]) for name in TRAIT_NAMES}


# --- saving and reading ---------------------------------------------------------------------------------------------------------------------
def save(db, uid: int, genes: dict) -> dict:
    """Write the genes and the phenotype of character `uid`, once. Raises sqlite3.IntegrityError if they already exist (never overwrites) and
    TraitError if anything is not in the catalog. Returns the phenotype."""
    ph = phenotype(genes)
    for name in TRAIT_NAMES:
        _index(name, genes[name][0])
        _index(name, genes[name][1])
    with db.tx():
        for name in TRAIT_NAMES:
            a1, a2 = genes[name]
            db.x("INSERT INTO character_genes(user_id,trait,allele1,allele2) VALUES(?,?,?,?)", (uid, name, a1, a2))
            db.x("INSERT INTO character_traits(user_id,trait,code) VALUES(?,?,?)", (uid, name, ph[name]))
    return ph


def genes_of(db, uid: int) -> dict | None:
    """The saved genes {trait: (a1, a2)} (validated against the catalog), or None when the character has none. Never creates anything."""
    rows = db.qa("SELECT trait, allele1, allele2 FROM character_genes WHERE user_id=?", (uid,))
    if not rows:
        return None
    out = {}
    for r in rows:
        _index(r["trait"], r["allele1"])
        _index(r["trait"], r["allele2"])
        out[r["trait"]] = (r["allele1"], r["allele2"])
    return out


def traits_of(db, uid: int) -> dict | None:
    """The saved visible traits {trait: code} (validated against the catalog: a code that is not in it raises TraitError), or None. Never creates anything."""
    rows = db.qa("SELECT trait, code FROM character_traits WHERE user_id=?", (uid,))
    if not rows:
        return None
    out = {}
    for r in rows:
        _index(r["trait"], r["code"])
        out[r["trait"]] = r["code"]
    return out
