"""The item catalog (v0.21): WHAT an item is. What a player owns is in inventory.py; how it looks on screen is in invui.py.

The catalog is DATA. An item is one `ItemDef` registered with `register_item`; nothing else in the game names a particular item, so a new item, a new category or a new equipment slot
is one entry here and no change anywhere else. Three registries, all filled at the bottom of this file and open to later systems:

* CATEGORIES: how the bag is sorted (clothing, footwear, accessories, food and drinks, personal items, gifts, miscellaneous). `register_category` adds one; a category is only a
  sort key and a label, so the inventory screens list whatever is registered.
* SLOTS: the places a character can wear an item (headwear, upper body, lower body, footwear, accessories). `register_slot` adds one. A slot holds ONE item.
* ITEMS: the definitions. Every field of an `ItemDef` is checked when it is registered (a bad definition fails at import, never in front of a player).

An item definition says:
    id            a stable code (`tshirt_white`): lower-case Latin letters, digits and `_`, 3 to 24 characters. It is what the database stores and what a button carries, never a name.
    name_fa       the Persian display name (required); name_en is optional (the English screens fall back to the Persian name)
    description_fa / description_en    one or two plain sentences (no markup: the screens escape them)
    category      a registered category id
    stackable / max_stack    a stackable item piles up to `max_stack` per stack (a full stack starts a new one); a non-stackable item is always one item per stack (max_stack 1)
    usable / effect          `usable` says the item is meant to be used; `effect` names the registered handler that does it. The Use button exists ONLY when a handler is registered
                             for that effect (`can_use`): an item whose effect no system has built yet shows no Use button and nothing is invented.
    equippable / slot        an equippable item names a registered slot
    droppable / transferable flags kept for the systems that will need them (nothing can be dropped or handed to another player in this version: there is no such system yet; the
                             flags are stored in the definition so a later shop, trade or gift-to-player feature does not have to change the catalog format)

Effects: `register_effect("name", handler)`. A handler is called as `handler(db, user_id, item, now)` INSIDE the transaction that consumes one unit of the item, may raise GameError (then
nothing is consumed) and may return `{"fa": "...", "en": "..."}`, a short message to show. No effect is registered by the game in v0.21: no hunger, thirst, health or other system exists yet.

An item that is in a database but no longer in the catalog is still shown (as an unknown item, in the miscellaneous category) and can neither be used nor equipped; nothing is ever deleted
because a definition went away.

v0.211: the catalog HAS NO SEEDED ITEMS. `items.py` registers the seven categories and the five slots, nothing else; `fashion.py` (imported at the bottom) registers the product TYPES that
the clothing business makes (a tank top, a cap ...) and the fabric material: definitions the game needs to exist, never given to anybody. Giftable items made by an admin are rows of
`item_templates` (db.py), loaded here by `ensure(db)`; they are the only other source of definitions. An item INSTANCE carries `data` (json on its stack): `q` quality 1-5, `b` brand key, `ph` production
history id ... (see `parse` / `display_name`).
"""
from __future__ import annotations

import json
import re
from dataclasses import dataclass
from typing import Callable

ID_RE = re.compile(r"[a-z][a-z0-9_]{2,23}")            # short on purpose: the id sits inside Telegram's 64-byte callback data
MAX_STACK_LIMIT = 9999
FALLBACK_CATEGORY = "misc"                              # where an item that is no longer in the catalog is shown
UNKNOWN_NAME = {"fa": "آیتم ناشناخته", "en": "Unknown item"}
UNKNOWN_DESC = {"fa": "این آیتم دیگه توی فهرست بازی نیست؛ همین‌جا نگهش داشتیم.", "en": "This item is no longer in the game's list; it is kept here."}


@dataclass(frozen=True)
class CategoryDef:
    id: str
    name_fa: str
    name_en: str
    icon: str


@dataclass(frozen=True)
class SlotDef:
    id: str
    name_fa: str
    name_en: str
    icon: str


@dataclass(frozen=True)
class ItemDef:
    id: str
    name_fa: str
    category: str
    description_fa: str = ""
    name_en: str | None = None
    description_en: str | None = None
    stackable: bool = False
    max_stack: int = 1
    usable: bool = False
    effect: str | None = None
    equippable: bool = False
    slot: str | None = None
    droppable: bool = True
    transferable: bool = True
    icon: str = "📦"
    quality: bool = False            # v0.211: instances carry a quality of 1 to 5 stars in their data (`q`)
    brandable: bool = False          # v0.211: instances may carry a brand (`b`)
    material: str | None = None      # v0.211: the kind of material this item is when it is used as one (`fabric`); a recipe asks for a kind, never for an item id

    def __post_init__(self):
        def bad(why: str):
            raise ValueError(f"item {self.id!r}: {why}")
        if not isinstance(self.id, str) or not ID_RE.fullmatch(self.id):
            raise ValueError(f"item id {self.id!r} must match {ID_RE.pattern}")
        if not (isinstance(self.name_fa, str) and self.name_fa.strip()):
            bad("a Persian name is required")
        if self.name_en is not None and not (isinstance(self.name_en, str) and self.name_en.strip()):
            bad("the English name is empty")
        if self.category not in CATEGORIES:
            bad(f"unknown category {self.category!r}")
        if not isinstance(self.max_stack, int) or isinstance(self.max_stack, bool) or not 1 <= self.max_stack <= MAX_STACK_LIMIT:
            bad("max_stack must be a whole number from 1 to %d" % MAX_STACK_LIMIT)
        if self.stackable and self.max_stack < 2:
            bad("a stackable item needs a max_stack of at least 2")
        if not self.stackable and self.max_stack != 1:
            bad("a non-stackable item has a max_stack of 1")
        if self.equippable != (self.slot is not None):
            bad("an equippable item names a slot and nothing else does")
        if self.slot is not None and self.slot not in SLOTS:
            bad(f"unknown slot {self.slot!r}")
        if self.usable and not self.effect:
            bad("a usable item names its effect")
        if not self.usable and self.effect:
            bad("an effect belongs to a usable item")
        if self.effect is not None and not ID_RE.fullmatch(self.effect):
            bad("the effect name must be a code like an item id")
        if self.material is not None and not ID_RE.fullmatch(self.material):
            bad("the material kind must be a code like an item id")


# =====================================================================================================================
#                                                       the registries
# =====================================================================================================================
CATEGORIES: dict[str, CategoryDef] = {}                 # insertion order = the order the screens list them in
SLOTS: dict[str, SlotDef] = {}
ITEMS: dict[str, ItemDef] = {}
EFFECTS: dict[str, Callable] = {}
DYNAMIC: dict[str, ItemDef] = {}                        # v0.211: the definitions of the admin's templates (rows of item_templates), refreshed by `ensure`
_dyn_key: tuple | None = None


def register_category(cid: str, name_fa: str, name_en: str, icon: str) -> CategoryDef:
    if not ID_RE.fullmatch(cid or ""):
        raise ValueError(f"category id {cid!r} must match {ID_RE.pattern}")
    if cid in CATEGORIES:
        raise ValueError(f"category {cid!r} is already registered")
    CATEGORIES[cid] = CategoryDef(cid, name_fa, name_en, icon)
    return CATEGORIES[cid]


def register_slot(sid: str, name_fa: str, name_en: str, icon: str) -> SlotDef:
    if not ID_RE.fullmatch(sid or ""):
        raise ValueError(f"slot id {sid!r} must match {ID_RE.pattern}")
    if sid in SLOTS:
        raise ValueError(f"slot {sid!r} is already registered")
    SLOTS[sid] = SlotDef(sid, name_fa, name_en, icon)
    return SLOTS[sid]


def register_item(**fields) -> ItemDef:
    """Register one item definition (keyword arguments of `ItemDef`). ValueError for a bad or repeated one."""
    d = ItemDef(**fields)
    if d.id in ITEMS:
        raise ValueError(f"item {d.id!r} is already registered")
    ITEMS[d.id] = d
    return d


def register_effect(effect: str, handler: Callable) -> None:
    """Make an effect real: from now on every usable item that names `effect` shows a Use button. `handler(db, user_id, item, now)` -> None or {"fa": ..., "en": ...}."""
    if not ID_RE.fullmatch(effect or ""):
        raise ValueError(f"effect name {effect!r} must match {ID_RE.pattern}")
    if not callable(handler):
        raise ValueError("an effect handler must be callable")
    if effect in EFFECTS:
        raise ValueError(f"effect {effect!r} is already registered")
    EFFECTS[effect] = handler


# =====================================================================================================================
#                                                       reading the catalog
# =====================================================================================================================
def get(item_id: str) -> ItemDef | None:
    d = ITEMS.get(item_id)
    return d if d is not None else DYNAMIC.get(item_id)


def category(cid: str) -> CategoryDef | None:
    return CATEGORIES.get(cid)


def slot(sid: str) -> SlotDef | None:
    return SLOTS.get(sid)


def category_of(item_id: str) -> str:
    """The category an item is listed in (the fallback one for an item that is no longer in the catalog)."""
    d = get(item_id)
    return d.category if d is not None else FALLBACK_CATEGORY


def order_of(item_id: str) -> int:
    """The position of an item in the catalog (an unknown item sorts after every known one)."""
    for i, k in enumerate((*ITEMS, *DYNAMIC)):
        if k == item_id:
            return i
    return len(ITEMS) + len(DYNAMIC)


def can_use(item: ItemDef | None) -> bool:
    """Whether the Use button exists for this item: it is usable AND a handler for its effect is registered."""
    return item is not None and item.usable and item.effect in EFFECTS


def can_equip(item: ItemDef | None) -> bool:
    return item is not None and item.equippable and item.slot in SLOTS


def _pick(lang: str, fa, en):
    return en if lang == "en" and en else fa


def name(lang: str, item_id: str) -> str:
    d = get(item_id)
    return UNKNOWN_NAME["en" if lang == "en" else "fa"] if d is None else _pick(lang, d.name_fa, d.name_en)


def description(lang: str, item_id: str) -> str:
    d = get(item_id)
    if d is None:
        return UNKNOWN_DESC["en" if lang == "en" else "fa"]
    return _pick(lang, d.description_fa, d.description_en)


def icon(item_id: str) -> str:
    d = get(item_id)
    return d.icon if d is not None else "❔"


def category_name(lang: str, cid: str) -> str:
    c = CATEGORIES.get(cid)
    return cid if c is None else _pick(lang, c.name_fa, c.name_en)


def slot_name(lang: str, sid: str) -> str:
    s = SLOTS.get(sid)
    return sid if s is None else _pick(lang, s.name_fa, s.name_en)


# =====================================================================================================================
#                                           the starting catalog (categories, slots, items)
# =====================================================================================================================
register_category("clothing", "پوشاک", "Clothing", "👕")
register_category("footwear", "کفش", "Footwear", "👟")
register_category("accessories", "اکسسوری", "Accessories", "🕶")
register_category("food", "خوراکی و نوشیدنی", "Food and drinks", "🍔")
register_category("personal", "وسایل شخصی", "Personal items", "🎒")
register_category("gifts", "هدایا", "Gifts", "🎁")
register_category("misc", "متفرقه", "Miscellaneous", "📦")

register_slot("headwear", "سر", "Headwear", "🧢")
register_slot("upper", "بالاتنه", "Upper body", "👕")
register_slot("lower", "پایین‌تنه", "Lower body", "👖")
register_slot("footwear", "کفش", "Footwear", "👟")
register_slot("accessory", "اکسسوری", "Accessories", "🕶")


# =====================================================================================================================
#                                    item instances: quality, brand and the other data a stack carries (v0.211)
# =====================================================================================================================
QUALITY_MIN, QUALITY_MAX = 1, 5
STAR_ON, STAR_OFF = "⭐", "☆"


def parse(data) -> dict:
    """The data of a stack or a worn item as a dict ({} for none or a damaged text): the stored form is canonical JSON (inventory._data)."""
    if isinstance(data, dict):
        return data
    if not data:
        return {}
    try:
        v = json.loads(data)
    except (TypeError, ValueError):
        return {}
    return v if isinstance(v, dict) else {}


def quality_of(data) -> int | None:
    q = parse(data).get("q")
    return q if isinstance(q, int) and not isinstance(q, bool) and QUALITY_MIN <= q <= QUALITY_MAX else None


def stars(q: int | None) -> str:
    """«⭐⭐⭐⭐☆» for quality 4 (five marks always: the filled ones and the empty ones); an empty text for an item without a quality."""
    if q is None:
        return ""
    return STAR_ON * q + STAR_OFF * (QUALITY_MAX - q)


def brand_label(key: str | None) -> str:
    """The brand's name as written on signs and labels (the configured name; a brand that left the configuration falls back to its key in capitals)."""
    if not key:
        return ""
    from . import config as C
    return C.brand_names().get(key) or key.replace("_", " ").upper()


def display_name(lang: str, item_id: str, data=None) -> str:
    """The name of one item INSTANCE as plain text: the brand in front of the product («NIKE تی‌شرت» reads as «تی‌شرت NIKE» in Persian: the Latin brand is isolated so the line does not scramble)."""
    base = name(lang, item_id)
    b = parse(data).get("b")
    if not b:
        return base
    label = brand_label(b)
    return f"{base} ⁦{label}⁩" if lang != "en" else f"{label} {base}"


def quality_line(lang: str, data=None) -> str:
    q = quality_of(data)
    if q is None:
        return ""
    return (f"کیفیت: {stars(q)}" if lang != "en" else f"Quality: {stars(q)}")


# =====================================================================================================================
#                                    the admin's templates (rows of item_templates) as definitions
# =====================================================================================================================
def _row_def(r) -> ItemDef | None:
    """The ItemDef a custom / legacy template row describes (None for a product template: its definition lives in fashion.py, or for a row that cannot be a valid definition)."""
    if r["kind"] == "product":
        return None
    try:
        slot = r["slot"] or None
        stack = int(r["max_stack"])
        return ItemDef(id=r["item_id"], name_fa=r["name_fa"], name_en=r["name_en"] or None, category=r["category"] if r["category"] in CATEGORIES else FALLBACK_CATEGORY,
                       description_fa=r["description_fa"] or "", description_en=r["description_en"] or None, stackable=stack > 1, max_stack=stack,
                       equippable=slot is not None and slot in SLOTS, slot=slot if slot in SLOTS else None, icon=r["icon"] or "📦",
                       quality=bool(r["has_quality"]), brandable=bool(r["brandable"]))
    except ValueError:
        return None


def ensure(db) -> None:
    """Make the in-memory definitions match the database's `item_templates` (cheap: the table is small and the check is one query). Called by every inventory function that needs a definition."""
    global _dyn_key
    rows = db.qa("SELECT * FROM item_templates ORDER BY id")
    key = (id(db), tuple((r["id"], r["item_id"], r["name_fa"], r["category"], r["slot"], r["max_stack"], r["has_quality"], r["brandable"]) for r in rows))
    if key == _dyn_key:
        return
    DYNAMIC.clear()
    for r in rows:
        d = _row_def(r)
        if d is not None and d.id not in ITEMS:
            DYNAMIC[d.id] = d
    _dyn_key = key


def templates(db, category: str | None = None) -> list:
    """The admin's giftable templates (rows), newest last; one category when asked."""
    if category is None:
        return db.qa("SELECT * FROM item_templates ORDER BY id")
    return db.qa("SELECT * FROM item_templates WHERE category=? ORDER BY id", (category,))


def has_quality(item_id: str) -> bool:
    d = get(item_id)
    return bool(d and d.quality)


def is_brandable(item_id: str) -> bool:
    d = get(item_id)
    return bool(d and d.brandable)


# the product types of the clothing business and the fabric: definitions, never grants (fashion.py)
from . import fashion as _fashion  # noqa: E402,F401
