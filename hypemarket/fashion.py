"""The fashion catalog (v0.211): what a clothing business can MAKE, and what it is made of. Two separate tables:

* PRODUCTS: the product TYPES (a tank top, jeans, a cap, a chain ...). They are registered as item definitions (`items.register_item`) so a crafted, bought or gifted item is a real item in the
  bag; every instance carries a quality (1 to 5 stars) and may carry a brand (`items.display_name`). Nothing here is ever given to anybody: a product type is a word of the game, not stock.
* RECIPES: how a product is made: which MATERIALS (by KIND: `fabric`, `leather`, `metal` ...), how many, the tailoring skill it needs, its time, the equipment it needs. A recipe never names an
  item id, so «a metal chain made of fabric» cannot even be written. A recipe is CRAFTABLE only when every material kind it asks for has a system in the game (`MATERIALS[kind]`): v0.211 has
  fabric (the cotton job); leather, rubber and metal have no source yet, so shoes, boots, belts, chains, rings, bracelets and watches exist as products (an admin can gift them, a future
  update can make them) but have no way to be crafted, and the screens say so.

A later update adds a material by registering its kind here (`MATERIALS["leather"] = True` once something produces it) and adds recipes: no other file changes.

The base value of a product (`base_value`) is what the game thinks it is worth: the materials at the game's own price times a markup, scaled by quality. The game's buyback pays a fraction of THAT (never a
fraction of a price an owner typed), which is lower than what the same fabric sells for, so selling crafted stock to the game can never beat selling the fabric.
"""
from __future__ import annotations

from dataclasses import dataclass

from . import config as C, items

# --- materials ------------------------------------------------------------------------------------------------------------------------
FABRIC = "fabric"
MATERIALS = {FABRIC: True, "leather": False, "rubber": False, "metal": False}          # kind -> does a system produce it yet
MATERIAL_NAMES = {FABRIC: ("پارچه", "fabric"), "leather": ("چرم", "leather"), "rubber": ("لاستیک", "rubber"), "metal": ("فلز", "metal")}
MATERIAL_UNIT_CENTS = {FABRIC: 110, "leather": 260, "rubber": 140, "metal": 400}        # what the game values ONE unit at (fabric: its q3 game price, see config.FABRIC_GAME_CENTS)
MARKUP_BPS = 18000                                                                      # a product is valued at its materials x 1.8 (the labour)
QUALITY_VALUE_BPS = {1: 5500, 2: 8000, 3: 10000, 4: 13500, 5: 19000}                   # the value of a product by its quality, as a share of the q3 value


@dataclass(frozen=True)
class Product:
    id: str
    fa: str
    en: str
    category: str            # clothing | footwear | accessories
    slot: str                # the equipment slot it is worn in
    icon: str


@dataclass(frozen=True)
class Recipe:
    id: str
    product: str
    materials: tuple         # ((kind, units per piece), ...)
    min_skill: int           # tailoring skill needed (0 to 1000)
    seconds: int             # time per piece at skill 0 with a basic machine
    needs: tuple = ()        # equipment kinds that must be installed (`upgrades.py`): e.g. ("cutting_table",)

    @property
    def craftable(self) -> bool:
        return all(MATERIALS.get(kind, False) for kind, _ in self.materials)


PRODUCTS: dict = {}
RECIPES: dict = {}


def register_product(pid: str, fa: str, en: str, category: str, slot: str, icon: str) -> Product:
    p = Product(pid, fa, en, category, slot, icon)
    items.register_item(id=pid, name_fa=fa, name_en=en, category=category, equippable=True, slot=slot, icon=icon, quality=True, brandable=True,
                        description_fa=f"{fa}؛ کیفیت و برندش روی خودش ثبت شده.", description_en=f"{en}; its quality and brand are recorded on the item itself.")
    PRODUCTS[pid] = p
    return p


def register_recipe(product: str, materials: tuple, min_skill: int, seconds: int, needs: tuple = ()) -> Recipe:
    if product not in PRODUCTS:
        raise ValueError(f"recipe for an unknown product {product!r}")
    if product in RECIPES:
        raise ValueError(f"product {product!r} already has a recipe")
    if not materials or any(kind not in MATERIALS or not isinstance(n, int) or n < 1 for kind, n in materials):
        raise ValueError("a recipe asks for known material kinds and whole quantities")
    r = Recipe(f"r_{product}"[:24], product, tuple(materials), min_skill, seconds, tuple(needs))
    RECIPES[product] = r
    return r


def recipe_of(product: str) -> Recipe | None:
    return RECIPES.get(product)


def unit_value(kind: str, quality: int) -> int:
    """What the game values ONE unit of material `kind` of this quality at (cents). Fabric: the game's own purchase price for that quality (`config.FABRIC_GAME_CENTS`); a material nobody produces yet: a
    flat value scaled by the quality's share."""
    if kind == FABRIC:
        return C.FABRIC_GAME_CENTS.get(quality, 0)
    return MATERIAL_UNIT_CENTS.get(kind, 0) * QUALITY_VALUE_BPS.get(quality, 0) // 10000


def base_value(product: str, quality: int) -> int:
    """The game's value of one piece of that quality, in cents (0 for a product with no recipe or an impossible quality): the materials AT THAT QUALITY at the game's own price times a markup. A piece's
    quality can never be above the quality of the material it was made from, so this is always more than the cheapest material it could have been made of and the game's buyback (a share of this) is
    always below what that same material would have fetched sold to the game directly."""
    r = RECIPES.get(product)
    if r is None or quality not in QUALITY_VALUE_BPS:
        return 0
    raw = sum(unit_value(kind, quality) * n for kind, n in r.materials)
    return max(1, raw * MARKUP_BPS // 10000) if raw > 0 else 0


def product_ids(category: str | None = None) -> list:
    return [p.id for p in PRODUCTS.values() if category is None or p.category == category]


# --- the catalog -----------------------------------------------------------------------------------------------------------------------
register_product("tank_top", "تاپ", "Tank top", "clothing", "upper", "👕")
register_product("tshirt", "تی‌شرت", "T-shirt", "clothing", "upper", "👕")
register_product("shirt", "پیراهن", "Shirt", "clothing", "upper", "👔")
register_product("hoodie", "هودی", "Hoodie", "clothing", "upper", "🧥")
register_product("sweatshirt", "سوئی‌شرت", "Sweatshirt", "clothing", "upper", "🧥")
register_product("jacket", "کت", "Jacket", "clothing", "upper", "🧥")
register_product("coat", "پالتو", "Coat", "clothing", "upper", "🧥")
register_product("shorts", "شلوارک", "Shorts", "clothing", "lower", "🩳")
register_product("trousers", "شلوار", "Trousers", "clothing", "lower", "👖")
register_product("jeans", "شلوار جین", "Jeans", "clothing", "lower", "👖")
register_product("sneakers", "کفش ورزشی", "Sneakers", "footwear", "footwear", "👟")
register_product("boots", "بوت", "Boots", "footwear", "footwear", "🥾")
register_product("shoes", "کفش", "Shoes", "footwear", "footwear", "👞")
register_product("cap", "کلاه لبه‌دار", "Cap", "accessories", "headwear", "🧢")
register_product("hat", "کلاه", "Hat", "accessories", "headwear", "🎩")
register_product("belt", "کمربند", "Belt", "accessories", "accessory", "〰️")
register_product("chain", "زنجیر", "Chain", "accessories", "accessory", "⛓")
register_product("ring", "انگشتر", "Ring", "accessories", "accessory", "💍")
register_product("bracelet", "دستبند", "Bracelet", "accessories", "accessory", "📿")
register_product("watch", "ساعت مچی", "Wristwatch", "accessories", "accessory", "⌚")

# recipes: (materials per piece, tailoring skill needed, seconds per piece, equipment kinds needed)
register_recipe("tank_top", ((FABRIC, 2),), 0, 600)
register_recipe("tshirt", ((FABRIC, 3),), 20, 720)
register_recipe("cap", ((FABRIC, 1),), 60, 600)
register_recipe("shorts", ((FABRIC, 3),), 90, 900)
register_recipe("hat", ((FABRIC, 2),), 130, 780)
register_recipe("shirt", ((FABRIC, 4),), 180, 1200)
register_recipe("sweatshirt", ((FABRIC, 5),), 250, 1500)
register_recipe("hoodie", ((FABRIC, 6),), 330, 1800)
register_recipe("trousers", ((FABRIC, 5),), 400, 1500, ("cutting_table",))
register_recipe("jeans", ((FABRIC, 6),), 470, 1800, ("cutting_table",))
register_recipe("jacket", ((FABRIC, 8),), 580, 2700, ("cutting_table",))
register_recipe("coat", ((FABRIC, 10),), 700, 3600, ("cutting_table",))
# no source of leather, rubber or metal exists yet: these products have recipes that cannot be crafted (see Recipe.craftable)
register_recipe("sneakers", (("rubber", 2), (FABRIC, 2)), 300, 2400)
register_recipe("boots", (("leather", 3),), 500, 3000)
register_recipe("shoes", (("leather", 2),), 400, 2400)
register_recipe("belt", (("leather", 1),), 250, 900)
register_recipe("chain", (("metal", 2),), 350, 1500)
register_recipe("ring", (("metal", 1),), 450, 1200)
register_recipe("bracelet", (("metal", 2),), 400, 1500)
register_recipe("watch", (("metal", 3), (FABRIC, 1)), 600, 3600)

# the fabric itself: a material item with a quality, made by the cotton job (textile.py), traded through purchase ads (market.py) and consumed by tailoring (tailor.py)
items.register_item(id="fabric", name_fa="پارچه", name_en="Fabric", category="misc", stackable=True, max_stack=100, icon="🧵", quality=True, material=FABRIC,
                    description_fa="پارچه‌ی نخی؛ کیفیتش روی خودش ثبت شده و مادهٔ خیاطی است.", description_en="Cotton fabric; its quality is recorded on it and tailoring uses it as material.")
