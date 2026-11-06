"""The admin's giftable item templates (v0.211): rows of `item_templates`, EMPTY by default (nothing is seeded, nobody is given anything).

A template says WHAT an admin may put into an item gift code:
* `product`: a preset for one of the product types of fashion.py (a tank top, a cap ...). The inventory item IS that product type (so a gifted NIKE cap is exactly the kind of item a store
  makes and sells: same slot, same quality and brand data); the template just makes it pickable.
* `custom`: a new item the admin defines (Persian name, optional English name, category, description, whether it can be worn and where, whether it stacks). Its definition is the row itself
  (item id `tpl_<n>`, assigned here); `items.ensure(db)` turns it into an `ItemDef`.
* `legacy`: written only by migration 14 for the v0.21 starter items that a database already referenced (see legacy_items.py). Never created here.

Templates are catalog entries, not grants: creating one gives nobody anything. The only way an admin puts an item into a player's bag is an item gift code (gifts.py).
Clothing, footwear and accessories may carry a quality (1 to 5 stars) and a brand (the code decides which, per code); other categories carry neither.
"""
from __future__ import annotations

import re
import secrets

from . import core, fashion, items

QUALITY_CATEGORIES = ("clothing", "footwear", "accessories")                    # the categories whose items carry a quality and may carry a brand
SLOTS_OF = {"clothing": ("headwear", "upper", "lower"), "footwear": ("footwear",), "accessories": ("headwear", "accessory")}
MAX_NAME = 40
MAX_DESC = 200
DEFAULT_STACK = 20                                                                 # a stackable custom item piles up to this many per stack
_CTRL = re.compile(r"[\x00-\x1f\x7f<>&]")


def clean_text(s, limit: int) -> str | None:
    """A typed name or description as it is stored: spaces collapsed, no control characters or markup characters, 1 to `limit` characters; None when it is not acceptable."""
    if not isinstance(s, str):
        return None
    s = " ".join(s.replace("‏", "").replace("‎", "").split())
    if not s or len(s) > limit or _CTRL.search(s):
        return None
    return s


def get(db, template_id) -> object | None:
    if not isinstance(template_id, int) or isinstance(template_id, bool):
        return None
    return db.q1("SELECT * FROM item_templates WHERE id=?", (template_id,))


def by_item(db, item_id: str):
    return db.q1("SELECT * FROM item_templates WHERE item_id=?", (item_id,))


def slots_for(category: str) -> tuple:
    return SLOTS_OF.get(category, ())


def create_product(db, by: int, product_id: str, now: int | None = None):
    """The template of product type `product_id` (made now, or the one that already exists). ValueError for an unknown product."""
    p = fashion.PRODUCTS.get(product_id)
    if p is None:
        raise ValueError("unknown product type")
    now = core.now() if now is None else now
    with db.tx():
        row = by_item(db, product_id)
        if row is None:
            db.x("INSERT INTO item_templates(item_id,kind,name_fa,name_en,category,description_fa,description_en,slot,max_stack,icon,has_quality,brandable,created_by,created_at) "
                 "VALUES(?,?,?,?,?,?,?,?,?,?,1,1,?,?)", (p.id, "product", p.fa, p.en, p.category, "", None, p.slot, 1, p.icon, by, now))
            row = by_item(db, product_id)
    items.ensure(db)
    return row


def create_custom(db, by: int, name_fa: str, name_en: str | None, category: str, description_fa: str, slot: str | None, stackable: bool, now: int | None = None):
    """A new custom template. The item id `tpl_<n>` is assigned here. ValueError for a bad name, category, slot or description (nothing is stored)."""
    fa = clean_text(name_fa, MAX_NAME)
    en = clean_text(name_en, MAX_NAME) if name_en else None
    desc = clean_text(description_fa, MAX_DESC) if description_fa else ""
    if fa is None or (name_en and en is None) or desc is None:
        raise ValueError("a name or description is not acceptable")
    if category not in items.CATEGORIES:
        raise ValueError("unknown category")
    if slot is not None and (slot not in items.SLOTS or slot not in slots_for(category)):
        raise ValueError("this category's items cannot be worn there")
    quality = category in QUALITY_CATEGORIES
    now = core.now() if now is None else now
    with db.tx():
        cur = db.x("INSERT INTO item_templates(item_id,kind,name_fa,name_en,category,description_fa,description_en,slot,max_stack,icon,has_quality,brandable,created_by,created_at) "
                   "VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                   (f"tmp_{secrets.token_hex(6)}", "custom", fa, en, category, desc, None, slot, DEFAULT_STACK if stackable else 1,
                    items.CATEGORIES[category].icon, int(quality), int(quality), by, now))
        tid = cur.lastrowid
        db.x("UPDATE item_templates SET item_id=? WHERE id=?", (f"tpl_{tid}", tid))
        row = get(db, tid)
    items.ensure(db)
    return row


def listing(db, category: str) -> list:
    """The templates of a category, oldest first (the picker's list)."""
    return items.templates(db, category)
