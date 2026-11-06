"""v0.211, part 1: nothing is seeded, the admin's item templates and the extended item gift flow, item data (quality / brand), the skills architecture and the McDonald's skill.

Covered: the catalog has no starter item and a new player's bag is empty (and no game code grants a starter item); the 13 -> 14 migration keeps every item a player owns and every item gift code
(legacy templates) and deletes nothing; template creation (a fashion product, a custom item) and what a bad template does; the admin's walk category -> template -> quantity -> quality -> brand ->
players -> confirm, in both languages, with stale and foreign buttons; quality and brand reaching the bag (a separate stack per variant, wearing keeps the data); the skill curve (checkpoints,
monotonic, capped), idempotent and race-safe grants, the triggers, the titles; the McDonald's skill (terms fixed at the start, XP and skill once per shift, old shifts compatible).
A real Telegram client was not used."""
import json
import re
import sqlite3
import threading
from pathlib import Path

import pytest
from aiogram.methods import AnswerCallbackQuery, EditMessageText, SendMessage

import test_bot as tb
import test_life_jobs as LJ
from hypemarket import cash, charxp, config as C, core, fashion, fmt, gifts, inventory as I, itemtpl, items, legacy_items, skills, work
from hypemarket.core import GameError
from hypemarket.db import Database
from hypemarket.texts import EN, FA, t

ADMIN = 1
P1, P2, P3 = 889001, 889002, 889003
NOW = 1_700_000_000
ADMINS = frozenset({ADMIN})
ROOT = Path(__file__).resolve().parent.parent
CODE_RE = r"HM-[A-HJ-NP-Z2-9]{4}-[A-HJ-NP-Z2-9]{4}-[A-HJ-NP-Z2-9]{4}"
layout = LJ.layout                                                                                      # (the real generated city of the job suites, for the McDonald's tests)


@pytest.fixture()
def db():
    d = Database(":memory:")
    core.set_clock(lambda: NOW)
    for uid in (P1, P2, P3, ADMIN):
        core.ensure_user(d, uid, None, f"U{uid}")
    return d


@pytest.fixture()
def h():
    return tb.Harness()


# ============================================================================================== nothing is seeded
def test_the_catalog_has_no_starter_item_and_a_new_player_owns_nothing(db):
    legacy_ids = {x[0] for x in legacy_items.LEGACY}
    assert not legacy_ids & set(items.ITEMS)                                                          # none of the 17 v0.21 items is registered
    assert list(items.CATEGORIES) == ["clothing", "footwear", "accessories", "food", "personal", "gifts", "misc"]
    assert list(items.SLOTS) == ["headwear", "upper", "lower", "footwear", "accessory"]
    assert set(items.ITEMS) == set(fashion.PRODUCTS) | {"fabric"}                                      # only the product TYPES of the fashion catalog and the fabric material exist as definitions
    assert I.bag(db, P1) == [] and I.total(db, P1) == 0 and I.worn(db, P1) == {} and I.audit(db, P1) == []
    assert db.q1("SELECT COUNT(*) c FROM item_inventory")["c"] == 0 and db.q1("SELECT COUNT(*) c FROM item_log")["c"] == 0
    assert db.q1("SELECT COUNT(*) c FROM item_templates")["c"] == 0                                    # and the admin's catalog of giftable items is empty


def test_no_game_code_hands_out_items_and_no_starter_id_is_in_the_code():
    """The only callers of `inventory.give` are the gift code redemption and the modules that move REAL items (crafting, trading, fulfilment). No module hands out starter items."""
    allowed = {"gifts.py", "inventory.py", "tailor.py", "market.py", "store.py", "textile.py", "bizcore.py", "biz.py", "bizrun.py", "stock.py"}
    offenders = [p.name for p in (ROOT / "hypemarket").glob("*.py") if re.search(r"\b(?:inventory|I)\s*\.\s*give\s*\(", p.read_text(encoding="utf-8")) and p.name not in allowed]
    assert offenders == []
    legacy_ids = {x[0] for x in legacy_items.LEGACY}
    for p in (ROOT / "hypemarket").glob("*.py"):
        if p.name in ("legacy_items.py", "db.py"):
            continue
        s = p.read_text(encoding="utf-8")
        assert not any(f"'{i}'" in s or f'"{i}"' in s for i in legacy_ids), p.name                      # the old ids appear nowhere in game code


def test_a_player_who_sends_everything_still_has_an_empty_backpack(h):
    h.say(P1, "/start")
    out = tb.Harness.texts(h.say(P1, "کوله‌پشتی"))
    assert "کوله‌پشتی" in out
    assert I.total(h.db, P1) == 0 and h.db.q1("SELECT COUNT(*) c FROM item_inventory")["c"] == 0


# ============================================================================================== the migration
def v13_database(path):
    """A v0.21 database: schema 13, players that own starter items (one worn), a gift code that names one. Items are written as v0.21 wrote them (rows only: the registry is not needed)."""
    d = Database(path)
    core.set_clock(lambda: NOW)
    for uid in (P1, P2, ADMIN):
        core.ensure_user(d, uid, None, f"U{uid}")
    ins = "INSERT INTO item_inventory(user_id,item_id,quantity,acquired_at,source,data) VALUES(?,?,?,?,?,NULL)"
    log = "INSERT INTO item_log(user_id,item_id,delta,source,ref,created_at) VALUES(?,?,?,?,?,?)"
    for uid, item, qty, src, ref in ((P1, "sandwich", 3, "gift", "HM-OLD1"), (P1, "tshirt_white", 1, "system", "seed"), (P2, "old_key", 1, "admin", "x")):
        d.x(ins, (uid, item, qty, NOW, src))
        d.x(log, (uid, item, qty, src, ref, NOW))
    d.x("INSERT INTO item_equipment(user_id,slot,item_id,acquired_at,source,data,equipped_at) VALUES(?,?,?,?,?,NULL,?)", (P2, "upper", "hoodie_green", NOW, "gift", NOW))
    d.x(log, (P2, "hoodie_green", 1, "gift", "HM-OLD2", NOW))
    d.x("INSERT INTO gift_codes(code,reward_type,amount,max_uses,used_count,created_by,created_at,item_id) VALUES('HM-AAAA-BBBB-CCCC','item',2,5,0,?,?,'umbrella')", (ADMIN, NOW))
    for tbl in ("item_templates", "gift_item_data", "character_skills", "skill_log"):                  # a v0.21 database does not have the v0.211 tables
        for r in d.qa("SELECT name FROM sqlite_master WHERE type='trigger' AND tbl_name=?", (tbl,)):
            d.x(f"DROP TRIGGER {r['name']}")
        d.x(f"DROP TABLE {tbl}")
    d.x("DELETE FROM kv WHERE key='migration14'")
    d.x("PRAGMA user_version=13")
    d.close()
    return path


def test_migration_14_keeps_every_owned_item_and_gift_code_and_names_them_again(tmp_path):
    path = v13_database(str(tmp_path / "v13.db"))
    raw = sqlite3.connect(path)
    before = {t_: raw.execute(f"SELECT * FROM {t_} ORDER BY 1").fetchall() for t_ in ("item_inventory", "item_equipment", "item_log", "gift_codes", "gift_redemptions", "users")}
    raw.close()
    d = Database(path)                                                                                  # the upgrade
    assert d.q1("PRAGMA user_version")[0] == 14
    for t_, rows in before.items():
        assert [tuple(r) for r in d.qa(f"SELECT * FROM {t_} ORDER BY 1")] == rows, t_                 # nothing owned, logged or coded was changed or removed
    rows = {r["item_id"]: r for r in d.qa("SELECT * FROM item_templates")}
    assert set(rows) == {"sandwich", "tshirt_white", "old_key", "hoodie_green", "umbrella"}             # exactly the referenced ones; the other 12 get no row
    assert all(r["kind"] == "legacy" and r["created_by"] is None for r in rows.values())
    items.ensure(d)
    try:
        assert items.name("fa", "sandwich") == "ساندویچ" and items.name("en", "hoodie_green") == "Green hoodie" and items.category_of("umbrella") == "personal"
        assert I.count(d, P1, "sandwich") == 3 and I.worn(d, P2)["upper"].item_id == "hoodie_green"      # still listed by name and still worn
        assert I.audit(d, P1) == [] and I.audit(d, P2) == []
    finally:
        items.DYNAMIC.clear()
        items._dyn_key = None
    report = json.loads(d.q1("SELECT value FROM kv WHERE key='migration14'")["value"])
    assert report["legacy_items_kept"] == ["hoodie_green", "old_key", "sandwich", "tshirt_white", "umbrella"]
    assert report["stacks_by_source"] == {"admin": 1, "gift": 1, "system": 1} and report["unknown_ids"] == []
    d.close()
    d2 = Database(path)                                                                                 # reopening changes nothing
    assert d2.q1("SELECT COUNT(*) c FROM item_templates")["c"] == 5 and d2.q1("PRAGMA user_version")[0] == 14


def test_the_old_item_gift_code_is_still_redeemable_after_the_migration(tmp_path):
    d = Database(v13_database(str(tmp_path / "v13b.db")))
    core.set_clock(lambda: NOW)
    try:
        out = gifts.redeem(d, P1, "HM-AAAA-BBBB-CCCC", now=NOW + 5)
        assert out["item"] == "umbrella" and out["qty"] == 2 and I.count(d, P1, "umbrella") == 2
        assert I.audit(d, P1) == []
    finally:
        items.DYNAMIC.clear()
        items._dyn_key = None


def test_a_database_with_unknown_item_ids_keeps_them_and_reports_them(tmp_path):
    path = v13_database(str(tmp_path / "v13c.db"))
    raw = sqlite3.connect(path)
    raw.execute("INSERT INTO item_inventory(user_id,item_id,quantity,acquired_at,source,data) VALUES(?,?,?,?,?,NULL)", (P1, "mystery_thing", 2, NOW, "admin"))
    raw.commit()
    raw.close()
    d = Database(path)
    assert d.q1("SELECT quantity FROM item_inventory WHERE item_id='mystery_thing'")["quantity"] == 2          # kept
    assert json.loads(d.q1("SELECT value FROM kv WHERE key='migration14'")["value"])["unknown_ids"] == ["mystery_thing"]
    assert d.q1("SELECT 1 FROM item_templates WHERE item_id='mystery_thing'") is None                          # it is reported, not invented
    items.DYNAMIC.clear()
    items._dyn_key = None


def test_a_fresh_database_gets_no_migration_report_rows(db):
    assert db.q1("SELECT COUNT(*) c FROM item_templates")["c"] == 0 and db.q1("PRAGMA user_version")[0] == 14


# ============================================================================================== templates
def test_a_product_template_is_a_real_item_with_quality_and_brand(db):
    row = itemtpl.create_product(db, ADMIN, "tank_top", NOW)
    assert (row["kind"], row["item_id"], row["category"], row["slot"], row["has_quality"], row["brandable"]) == ("product", "tank_top", "clothing", "upper", 1, 1)
    assert itemtpl.create_product(db, ADMIN, "tank_top", NOW + 1)["id"] == row["id"]                   # the same template, not a second one
    assert db.q1("SELECT COUNT(*) c FROM item_templates")["c"] == 1
    d = items.get("tank_top")
    assert d.quality and d.brandable and d.equippable and d.slot == "upper" and items.has_quality("tank_top") and items.is_brandable("tank_top")
    with pytest.raises(ValueError):
        itemtpl.create_product(db, ADMIN, "not_a_product", NOW)


def test_a_custom_template_gets_an_id_a_definition_and_the_right_rules(db):
    r = itemtpl.create_custom(db, ADMIN, "  کلید   طلایی ", "Golden key", "misc", "یه کلید خاص.", None, False, NOW)
    assert r["item_id"] == f"tpl_{r['id']}" and r["name_fa"] == "کلید طلایی" and r["kind"] == "custom" and (r["has_quality"], r["brandable"], r["max_stack"]) == (0, 0, 1)
    try:
        d = items.get(r["item_id"])
        assert d.category == "misc" and not d.equippable and not d.stackable and items.name("en", r["item_id"]) == "Golden key" and items.name("fa", r["item_id"]) == "کلید طلایی"
        s = itemtpl.create_custom(db, ADMIN, "کلاه", None, "clothing", "", "headwear", True, NOW)
        d2 = items.get(s["item_id"])
        assert d2.equippable and d2.slot == "headwear" and d2.stackable and d2.max_stack == itemtpl.DEFAULT_STACK and d2.quality and d2.brandable and s["description_fa"] == ""
        assert [x["id"] for x in itemtpl.listing(db, "clothing")] == [s["id"]] and [x["id"] for x in itemtpl.listing(db, "misc")] == [r["id"]]
        assert I.give(db, P1, r["item_id"], 1, "gift", "k") == 1 and I.count(db, P1, r["item_id"]) == 1
    finally:
        items.DYNAMIC.clear()
        items._dyn_key = None


@pytest.mark.parametrize("fa,en,cat,desc,slot", [
    ("", None, "misc", "", None), ("x" * 41, None, "misc", "", None), ("<b>x</b>", None, "misc", "", None), ("نام", "bad\x00name", "misc", "", None),
    ("نام", None, "nope", "", None), ("نام", None, "food", "", "upper"), ("نام", None, "clothing", "", "footwear"), ("نام", None, "misc", "d" * 201, None), (None, None, "misc", "", None),
])
def test_a_bad_template_is_refused_and_stores_nothing(db, fa, en, cat, desc, slot):
    with pytest.raises(ValueError):
        itemtpl.create_custom(db, ADMIN, fa, en, cat, desc, slot, False, NOW)
    assert db.q1("SELECT COUNT(*) c FROM item_templates")["c"] == 0


def test_a_template_made_by_another_connection_appears_without_a_restart(tmp_path):
    path = str(tmp_path / "t.db")
    a, b = Database(path), Database(path)
    core.set_clock(lambda: NOW)
    r = itemtpl.create_custom(a, ADMIN, "جعبه", None, "misc", "", None, False, NOW)
    try:
        items.ensure(b)
        assert items.get(r["item_id"]) is not None and items.name("fa", r["item_id"]) == "جعبه"
    finally:
        items.DYNAMIC.clear()
        items._dyn_key = None


# ============================================================================================== item data in codes and bags
def test_a_code_carries_quality_and_brand_and_they_reach_the_bag(db):
    row = gifts.create(db, ADMIN, "item", 2, 1, ADMINS, item_id="tank_top", item_data={"q": 4, "b": "nike"}, now=NOW)
    assert gifts.item_data(db, row["code"]) == '{"b":"nike","q":4}'
    out = gifts.redeem(db, P1, row["code"], now=NOW + 1)
    assert out["data"] == '{"b":"nike","q":4}' and out["qty"] == 2
    e = I.entry(db, P1, "tank_top")
    assert (e.qty, e.data) == (2, '{"b":"nike","q":4}')
    assert items.display_name("fa", "tank_top", e.data) == "تاپ ⁦NIKE⁩" and items.display_name("en", "tank_top", e.data) == "NIKE Tank top"
    assert items.stars(4) == "⭐⭐⭐⭐☆" and items.quality_of(e.data) == 4
    with pytest.raises(sqlite3.DatabaseError):                                                          # what a code gives never changes
        db.x("UPDATE gift_item_data SET data='{\"q\":5}' WHERE code=?", (row["code"],))
    with pytest.raises(sqlite3.DatabaseError):
        db.x("DELETE FROM gift_item_data WHERE code=?", (row["code"],))


@pytest.mark.parametrize("item,data", [
    ("tank_top", {"q": 0}), ("tank_top", {"q": 6}), ("tank_top", {"q": 2.5}), ("tank_top", {"q": True}), ("tank_top", {"b": "unknown_brand"}), ("tank_top", {"b": "NIKE"}),
    ("tank_top", {"b": 1}), ("fabric", {"b": "nike"}), ("tank_top", {"ph": 0}),
])
def test_a_code_with_bad_item_data_is_refused(db, item, data):
    with pytest.raises(ValueError):
        gifts.create(db, ADMIN, "item", 1, 1, ADMINS, item_id=item, item_data=data, now=NOW)
    assert db.q1("SELECT COUNT(*) c FROM gift_codes")["c"] == 0 and db.q1("SELECT COUNT(*) c FROM gift_item_data")["c"] == 0


def test_item_data_belongs_to_item_codes_only_and_to_items_that_can_carry_it(db):
    with pytest.raises(ValueError):
        gifts.create(db, ADMIN, "life_cash", 100, 1, ADMINS, item_data={"q": 3}, now=NOW)
    r = itemtpl.create_custom(db, ADMIN, "جعبه", None, "gifts", "", None, True, NOW)
    try:
        with pytest.raises(ValueError):
            gifts.create(db, ADMIN, "item", 1, 1, ADMINS, item_id=r["item_id"], item_data={"q": 3}, now=NOW)       # a gift box has no quality
        gifts.create(db, ADMIN, "item", 1, 1, ADMINS, item_id="fabric", item_data={"q": 3}, now=NOW)                # fabric has one (but no brand)
    finally:
        items.DYNAMIC.clear()
        items._dyn_key = None


def test_variants_are_separate_stacks_and_equal_data_merges(db):
    for ref, qty, data in (("a", 30, {"q": 3}), ("b", 10, {"q": 4}), ("c", 20, {"q": 3}), ("d", 5, None)):
        I.give(db, P1, "fabric", qty, "gift", ref, data=data)
    stacks = sorted((r["data"] or "", r["quantity"]) for r in db.qa("SELECT data,quantity FROM item_inventory WHERE user_id=?", (P1,)))
    assert stacks == [("", 5), ('{"q":3}', 50), ('{"q":4}', 10)]                                         # same data merges (30+20), another quality or none is another stack
    assert I.count(db, P1, "fabric") == 65 and I.audit(db, P1) == []
    assert {b.data: b.qty for b in I.bag(db, P1)} == {None: 5, '{"q":3}': 50, '{"q":4}': 10}
    for i, q in enumerate((1, 2)):                                                                      # a single (non-stackable) item is always its own place
        I.give(db, P2, "tank_top", 1, "gift", f"t{i}", data={"q": q})
    I.give(db, P2, "tank_top", 1, "gift", "t9", data={"q": 1})
    assert I.stack_count(db, P2) == 3 and I.count(db, P2, "tank_top") == 3 and I.audit(db, P2) == []
    assert I.take_variant(db, P1, "fabric", '{"q":3}', 20, "use", "u1", NOW) == 20
    assert I.count_variant(db, P1, "fabric", '{"q":3}') == 30 and I.count_variant(db, P1, "fabric", '{"q":4}') == 10 and I.audit(db, P1) == []


def test_wearing_a_variant_keeps_its_quality_and_brand_and_takes_off_the_same_one(db):
    I.give(db, P1, "tshirt", 1, "gift", "a", data={"q": 5, "b": "givenchy"})
    sid = I.entry(db, P1, "tshirt").stack_id
    r = I.equip(db, P1, sid, now=NOW + 3)
    assert r["data"] == '{"b":"givenchy","q":5}' and I.worn(db, P1)["upper"].data == '{"b":"givenchy","q":5}' and I.count(db, P1, "tshirt") == 0
    I.give(db, P1, "tshirt", 1, "gift", "b", data={"q": 2})
    r2 = I.equip(db, P1, I.entry(db, P1, "tshirt").stack_id, now=NOW + 4)                              # replaces: the old shirt returns with ITS data
    assert r2["replaced_data"] == '{"b":"givenchy","q":5}'
    assert {b.data for b in I.bag(db, P1)} == {'{"b":"givenchy","q":5}'} and I.worn(db, P1)["upper"].data == '{"q":2}'
    assert I.audit(db, P1) == []


def test_a_bad_variant_cannot_be_stored_in_a_bag_directly(db):
    for bad in ({"q": 9}, {"b": "x y"}, {"ph": -1}):
        with pytest.raises(ValueError):
            I.give(db, P1, "tank_top", 1, "gift", "x", data=bad)
    with pytest.raises(ValueError):                                                                     # a brand on an item that cannot carry one (a programming mistake, not a player's)
        I.give(db, P2, "fabric", 1, "gift", "x", data={"b": "nike"})


# ============================================================================================== the admin's walk
def panel(calls):
    return [c for c in calls if isinstance(c, (EditMessageText, SendMessage))][-1]


def cbs(msg):
    return [b.callback_data for row in msg.reply_markup.inline_keyboard for b in row] if getattr(msg, "reply_markup", None) else []


def labels(msg):
    return [b.text for row in msg.reply_markup.inline_keyboard for b in row] if getattr(msg, "reply_markup", None) else []


def toasts(calls):
    return [c.text for c in calls if isinstance(c, AnswerCallbackQuery) and c.text]


def code_of(msg):
    m = re.search(CODE_RE, msg.text)
    assert m, msg.text
    return m.group(0)


def start_walk(h, cat):
    h.say(ADMIN, "/start")
    h.press(ADMIN, f"ad:gift:{ADMIN}")
    h.press(ADMIN, f"ad:gt:item:{ADMIN}")
    return panel(h.press(ADMIN, f"ad:gc:{cat}:{ADMIN}"))


def test_the_walk_for_a_branded_four_star_tank_top_ends_in_the_right_bag(h):
    tpl_screen = start_walk(h, "clothing")
    assert f"ad:gn:{ADMIN}" in cbs(tpl_screen) and not [c for c in cbs(tpl_screen) if c.startswith("ad:gp:")]          # nothing seeded: an empty list and the «new template» button
    assert FA["gift.tpl.none"].split("{")[0] in tpl_screen.text
    base = panel(h.press(ADMIN, f"ad:gn:{ADMIN}"))
    assert f"ad:gb:tank_top:{ADMIN}" in cbs(base) and f"ad:gb:custom:{ADMIN}" in cbs(base) and f"ad:gb:ring:{ADMIN}" not in cbs(base)       # clothing products only
    ask = panel(h.press(ADMIN, f"ad:gb:tank_top:{ADMIN}"))
    assert h.app.pending[ADMIN][0] == "giftamt" and "تاپ" in ask.text
    q = panel(h.say(ADMIN, "2"))
    assert [c for c in cbs(q) if c.startswith("ad:gq:")] == [f"ad:gq:{i}:{ADMIN}" for i in range(1, 6)] and ADMIN not in [k for k, v in h.app.pending.items() if v[0] == "giftuses"]
    b = panel(h.press(ADMIN, f"ad:gq:4:{ADMIN}"))
    brands = [c for c in cbs(b) if c.startswith("ad:gr:")]
    assert brands == [f"ad:gr:{k}:{ADMIN}" for k in C.brand_names()] + [f"ad:gr:none:{ADMIN}"]
    u = panel(h.press(ADMIN, f"ad:gr:nike:{ADMIN}"))
    assert h.app.pending[ADMIN][0] == "giftuses" and "NIKE" in u.text and "⭐⭐⭐⭐☆" in u.text
    conf = panel(h.say(ADMIN, "2"))
    assert "NIKE" in conf.text and gifts.item_data(h.db, "x") is None and h.db.q1("SELECT COUNT(*) c FROM gift_codes")["c"] == 0         # nothing stored before the confirmation
    made = panel(h.press(ADMIN, next(c for c in cbs(conf) if c.startswith("ad:gok:"))))
    code = code_of(made)
    row = gifts.get(h.db, code)
    assert (row["reward_type"], row["amount"], row["item_id"], row["max_uses"]) == ("item", 2, "tank_top", 2) and gifts.item_data(h.db, code) == '{"b":"nike","q":4}'
    assert h.db.q1("SELECT COUNT(*) c FROM item_templates")["c"] == 1                                    # the template the admin made stays in the catalog
    out = tb.Harness.texts(h.say(P1, f"کد هدیه {code}"))
    assert FA["gift.ok.item"].split("{")[0] in out or "تاپ" in out
    e = I.entry(h.db, P1, "tank_top")
    assert (e.qty, e.data) == (2, '{"b":"nike","q":4}')
    inv = tb.Harness.texts(h.say(P1, "کوله‌پشتی"))
    assert "پوشاک" in inv


def test_the_walk_with_no_brand_and_the_english_screens(h):
    h.say(ADMIN, "/start")
    h.say(P1, "/start")
    h.db.x("UPDATE users SET lang='en' WHERE id IN (?, ?)", (ADMIN, P1))
    start_walk(h, "footwear")
    h.press(ADMIN, f"ad:gn:{ADMIN}")
    h.press(ADMIN, f"ad:gb:sneakers:{ADMIN}")
    h.say(ADMIN, "1")
    h.press(ADMIN, f"ad:gq:5:{ADMIN}")
    b = panel(h.press(ADMIN, f"ad:gr:none:{ADMIN}"))
    for m in (panel(h.say(ADMIN, "1")),):
        assert not re.search(r"[؀-ۿ]", m.text + " ".join(labels(m))), m.text
        made = panel(h.press(ADMIN, next(c for c in cbs(m) if c.startswith("ad:gok:"))))
    assert gifts.item_data(h.db, code_of(made)) == '{"q":5}'
    out = tb.Harness.texts(h.say(P1, f"gift code {code_of(made)}"))
    assert "Sneakers" in out and "⭐⭐⭐⭐⭐" in out and I.entry(h.db, P1, "sneakers").data == '{"q":5}'


def test_the_walk_for_a_custom_item_in_another_category(h):
    start_walk(h, "misc")
    n = panel(h.press(ADMIN, f"ad:gn:{ADMIN}"))                                                         # misc has no fashion products: straight to the name
    assert h.app.pending[ADMIN][0] == "giftname" and FA["gift.ask_name"][:12] in n.text
    assert FA["gift.bad_text"][:10] in panel(h.say(ADMIN, "<b>x</b>")).text and h.app.pending[ADMIN][0] == "giftname"        # a bad name asks again
    assert FA["gift.bad_text"][:10] in panel(h.say(ADMIN, "-")).text                                    # the Persian name cannot be skipped
    h.say(ADMIN, "کلید طلایی")
    assert h.app.pending[ADMIN][0] == "gifteng"
    h.say(ADMIN, "Golden key")
    assert h.app.pending[ADMIN][0] == "giftdesc"
    st = panel(h.say(ADMIN, "کلیدی که در قدیمی رو باز می‌کنه."))                                         # misc items cannot be worn: no slot question, straight to stacking
    assert f"ad:gk:1:{ADMIN}" in cbs(st) and not [c for c in cbs(st) if c.startswith("ad:gs:")]
    amount = panel(h.press(ADMIN, f"ad:gk:1:{ADMIN}"))
    assert FA["gift.tpl.made"].split("{")[0] in amount.text and h.app.pending[ADMIN][0] == "giftamt"
    tpl = h.db.q1("SELECT * FROM item_templates")
    assert (tpl["kind"], tpl["name_fa"], tpl["name_en"], tpl["category"], tpl["max_stack"], tpl["has_quality"], tpl["created_by"]) == ("custom", "کلید طلایی", "Golden key", "misc", itemtpl.DEFAULT_STACK, 0, ADMIN)
    uses = panel(h.say(ADMIN, "5"))                                                                     # no quality or brand question for a plain item
    assert h.app.pending[ADMIN][0] == "giftuses"
    made = panel(h.press(ADMIN, next(c for c in cbs(panel(h.say(ADMIN, "1"))) if c.startswith("ad:gok:"))))
    code = code_of(made)
    assert gifts.item_data(h.db, code) is None
    h.say(P1, f"کد هدیه {code}")
    assert I.count(h.db, P1, tpl["item_id"]) == 5 and I.entry(h.db, P1, tpl["item_id"]).stacks == 1 and items.category_of(tpl["item_id"]) == "misc"
    items.DYNAMIC.clear()
    items._dyn_key = None


def test_the_walk_for_a_custom_wearable_asks_where_it_is_worn(h):
    start_walk(h, "accessories")
    base = panel(h.press(ADMIN, f"ad:gn:{ADMIN}"))
    assert f"ad:gb:ring:{ADMIN}" in cbs(base)
    h.press(ADMIN, f"ad:gb:custom:{ADMIN}")
    h.say(ADMIN, "عینک")
    h.say(ADMIN, "-")
    slots = panel(h.say(ADMIN, "-"))
    assert [c for c in cbs(slots) if c.startswith("ad:gs:")] == [f"ad:gs:headwear:{ADMIN}", f"ad:gs:accessory:{ADMIN}", f"ad:gs:none:{ADMIN}"]
    assert toasts(h.press(ADMIN, f"ad:gs:footwear:{ADMIN}")) == [FA["e.bad_usage"]]                       # a slot this category cannot use
    h.press(ADMIN, f"ad:gs:accessory:{ADMIN}")
    h.press(ADMIN, f"ad:gk:0:{ADMIN}")
    tpl = h.db.q1("SELECT * FROM item_templates")
    assert (tpl["slot"], tpl["max_stack"], tpl["has_quality"], tpl["brandable"], tpl["name_en"]) == ("accessory", 1, 1, 1, None)
    h.say(ADMIN, "1")
    assert [c for c in cbs(panel(h.press(ADMIN, f"ad:gq:3:{ADMIN}"))) if c.startswith("ad:gr:")]          # the new accessory can carry a quality and a brand
    items.DYNAMIC.clear()
    items._dyn_key = None


def test_buttons_of_the_wrong_step_or_of_a_player_do_nothing(h):
    h.say(ADMIN, "/start")
    h.say(P1, "/start")
    for data in (f"ad:gq:3:{ADMIN}", f"ad:gr:nike:{ADMIN}", f"ad:gs:upper:{ADMIN}", f"ad:gk:1:{ADMIN}", f"ad:gb:tank_top:{ADMIN}", f"ad:gn:{ADMIN}", f"ad:gp:1:{ADMIN}", f"ad:gc:food:{ADMIN}"):
        assert FA["gift.stale"] in panel(h.press(ADMIN, data)).text, data                                # no draft at all
    start_walk(h, "clothing")
    for data in (f"ad:gq:3:{ADMIN}", f"ad:gr:nike:{ADMIN}", f"ad:gs:upper:{ADMIN}", f"ad:gk:1:{ADMIN}", f"ad:gb:tank_top:{ADMIN}"):                      # the draft is at the template list
        assert FA["gift.stale"] in panel(h.press(ADMIN, data)).text, data
    for data in (f"ad:gq:3:{P1}", f"ad:gr:nike:{P1}", f"ad:gn:{P1}", f"ad:gb:tank_top:{P1}", f"ad:gs:upper:{P1}", f"ad:gk:1:{P1}"):
        assert toasts(h.press(P1, data)) == [FA["ad.denied"]] and h.db.q1("SELECT COUNT(*) c FROM item_templates")["c"] == 0, data
    assert toasts(h.press(P1, f"ad:gq:3:{ADMIN}")) == [FA["not_yours"]]


def test_quality_and_brand_buttons_are_validated(h):
    start_walk(h, "clothing")
    h.press(ADMIN, f"ad:gn:{ADMIN}")
    h.press(ADMIN, f"ad:gb:hoodie:{ADMIN}")
    h.say(ADMIN, "1")
    for bad in ("0", "6", "x", "٣", "3.5", "-1"):
        assert toasts(h.press(ADMIN, f"ad:gq:{bad}:{ADMIN}")) == [FA["e.bad_usage"]], bad
    assert h.app.gift_drafts[ADMIN]["step"] == "quality" and "q" not in h.app.gift_drafts[ADMIN]["data"]
    h.press(ADMIN, f"ad:gq:2:{ADMIN}")
    for bad in ("NIKE", "adidas", "", "nike;x"):
        assert toasts(h.press(ADMIN, f"ad:gr:{bad}:{ADMIN}")) == [FA["e.bad_usage"]], bad
    assert h.app.gift_drafts[ADMIN]["step"] == "brand"
    assert FA["gift.stale"] in panel(h.press(ADMIN, f"ad:gq:5:{ADMIN}")).text                              # the quality button of an earlier step is stale now


def test_cancel_after_a_template_was_made_leaves_the_template_but_no_code(h):
    start_walk(h, "clothing")
    h.press(ADMIN, f"ad:gn:{ADMIN}")
    h.press(ADMIN, f"ad:gb:jeans:{ADMIN}")
    out = h.press(ADMIN, f"ad:gx:{ADMIN}")
    assert FA["gift.cancelled"] in panel(out).text
    assert h.db.q1("SELECT COUNT(*) c FROM gift_codes")["c"] == 0 and h.db.q1("SELECT COUNT(*) c FROM item_templates")["c"] == 1
    assert ADMIN not in h.app.gift_drafts and ADMIN not in h.app.pending and h.say(ADMIN, "3") == []
    again = start_walk(h, "clothing")                                                                   # the template is pickable next time
    assert [c for c in cbs(again) if c.startswith("ad:gp:")] == [f"ad:gp:{h.db.q1('SELECT id FROM item_templates')['id']}:{ADMIN}"]


def test_a_double_tap_on_the_final_confirm_makes_one_code_with_its_data(h):
    start_walk(h, "clothing")
    h.press(ADMIN, f"ad:gn:{ADMIN}")
    h.press(ADMIN, f"ad:gb:shirt:{ADMIN}")
    h.say(ADMIN, "1")
    h.press(ADMIN, f"ad:gq:3:{ADMIN}")
    h.press(ADMIN, f"ad:gr:louis_vuitton:{ADMIN}")
    conf = panel(h.say(ADMIN, "1"))
    ok = next(c for c in cbs(conf) if c.startswith("ad:gok:"))
    assert code_of(panel(h.press(ADMIN, ok)))
    assert FA["gift.stale"] in panel(h.press(ADMIN, ok)).text
    assert h.db.q1("SELECT COUNT(*) c FROM gift_codes")["c"] == 1 and h.db.q1("SELECT COUNT(*) c FROM gift_item_data")["c"] == 1


# ============================================================================================== skills
def test_the_curve_hits_its_benchmarks_and_only_goes_up():
    s = "mcdonalds"
    shifts = lambda n: n * 1000                                                                          # work is in thousandths: one shift is 1000
    assert skills.level_for(s, 0) == 0 and skills.level_for(s, shifts(1000)) == 1000 and skills.level_for(s, shifts(5000)) == 1000
    for n, lo, hi in ((16, 95, 105), (100, 270, 320), (290, 495, 505), (670, 795, 805), (830, 895, 905), (999, 990, 999)):
        assert lo <= skills.level_for(s, shifts(n)) <= hi, n
    prev = -1
    for w in range(0, skills.total_work(s) + 1, 997):
        lvl = skills.level_for(s, w)
        assert lvl >= prev and 0 <= lvl <= 1000
        prev = lvl
    for lvl in range(1, 1001):
        assert skills.need(s, lvl) > skills.need(s, lvl - 1) and skills.level_for(s, skills.need(s, lvl)) == lvl and skills.level_for(s, skills.need(s, lvl) - 1) == lvl - 1
    assert skills.need(s, 1000) == skills.total_work(s)
    gaps = [skills.need(s, i + 1) - skills.need(s, i) for i in (1, 100, 500, 900, 999)]
    assert gaps == sorted(gaps)                                                                          # diminishing returns: every later level costs more than the one before


def test_nobody_masters_a_skill_quickly():
    """The benchmark: a thousand completed shifts, each 8 real hours at best 6.4 hours: months of real time, never a handful of presses."""
    assert C.SKILL_WORK["mcdonalds"] == 1000 and skills.level_for("mcdonalds", 100 * 1000) < 350
    best_shift = C.MCD_SHIFT_SECONDS * (10000 - C.MCD_SHIFT_CUT_BPS_MAX) // 10000
    assert 1000 * best_shift / 86400 > 200                                                               # >200 days of shifts for level 1000 (one at a time: a single open task per player)
    for sid, units in C.SKILL_WORK.items():
        assert units >= 1000 and sid in skills.SKILLS


@pytest.mark.parametrize("level,title", [(0, "beginner"), (49, "beginner"), (50, "trainee"), (149, "trainee"), (150, "skilled"), (299, "skilled"), (300, "experienced"), (499, "experienced"),
                                         (500, "professional"), (749, "professional"), (750, "expert"), (899, "expert"), (900, "master"), (1000, "master")])
def test_titles_are_milestones_of_the_level(level, title):
    assert skills.title_id(level) == title and skills.title("fa", level) == skills.TITLES_FA[title] and skills.title("en", level) == skills.TITLES_EN[title]


def test_a_grant_counts_once_per_source_and_reference(db):
    assert skills.grant_work(db, P1, "textile", 5000, "job:cotton", "t1", NOW) == (0, skills.level_for("textile", 5000))
    assert skills.grant_work(db, P1, "textile", 5000, "job:cotton", "t1", NOW + 1) is None                # the same job again: nothing
    assert skills.work_of(db, P1, "textile") == 5000
    assert skills.grant_work(db, P1, "textile", 5000, "job:cotton", "t2", NOW + 2) is not None and skills.work_of(db, P1, "textile") == 10000
    assert skills.grant_work(db, P1, "textile", 1000, "other", "t1", NOW + 3) is not None                 # another source with the same reference is another grant
    assert skills.work_of(db, P2, "textile") == 0 and skills.level_of(db, P2, "textile") == 0
    rows = db.qa("SELECT delta, work_after FROM skill_log WHERE user_id=? ORDER BY id", (P1,))
    assert [(r["delta"], r["work_after"]) for r in rows] == [(5000, 5000), (5000, 10000), (1000, 11000)]


def test_a_skill_is_capped_and_only_goes_up(db):
    cap = skills.total_work("tailoring")
    assert skills.grant_work(db, P1, "tailoring", skills.MAX_WORK_GRANT, "x", "1", NOW)[1] in range(1, 1001)
    while skills.work_of(db, P1, "tailoring") < cap:
        skills.grant_work(db, P1, "tailoring", skills.MAX_WORK_GRANT, "x", f"n{skills.work_of(db, P1, 'tailoring')}", NOW)
    assert skills.work_of(db, P1, "tailoring") == cap and skills.level_of(db, P1, "tailoring") == 1000
    skills.grant_work(db, P1, "tailoring", 1000, "x", "more", NOW)
    assert skills.work_of(db, P1, "tailoring") == cap and db.q1("SELECT delta FROM skill_log WHERE ref='more'")["delta"] == 0
    for sql in ("UPDATE character_skills SET work=0", "UPDATE skill_log SET delta=1", "DELETE FROM skill_log"):
        with pytest.raises(sqlite3.DatabaseError):
            db.x(sql)
    assert skills.work_of(db, P1, "tailoring") == cap


@pytest.mark.parametrize("args", [
    ("textile", 0, "s", "r"), ("textile", -5, "s", "r"), ("textile", 1.5, "s", "r"), ("textile", True, "s", "r"), ("textile", 10 ** 8, "s", "r"), ("textile", 5, "", "r"), ("textile", 5, "s", ""),
    ("textile", 5, "s" * 41, "r"), ("textile", 5, "s", "r" * 81), ("not_a_skill", 5, "s", "r"), ("textile", 5, None, "r"),
])
def test_bad_grants_are_refused_and_change_nothing(db, args):
    with pytest.raises(ValueError):
        skills.grant_work(db, P1, *args, NOW)
    assert db.q1("SELECT COUNT(*) c FROM skill_log")["c"] == 0 and db.q1("SELECT COUNT(*) c FROM character_skills")["c"] == 0


def test_only_real_players_have_skills(db):
    core.ensure_user(db, 777, None, "Bot")
    db.x("UPDATE users SET bot=1 WHERE id=777")
    with pytest.raises(GameError):
        skills.grant_work(db, 777, "textile", 1000, "s", "r", NOW)
    with pytest.raises(GameError):
        skills.grant_work(db, 424242, "textile", 1000, "s", "r", NOW)
    assert db.q1("SELECT COUNT(*) c FROM character_skills")["c"] == 0


def test_two_connections_racing_for_the_same_grant_pay_it_once(tmp_path):
    path = str(tmp_path / "s.db")
    first = Database(path)
    core.set_clock(lambda: NOW)
    core.ensure_user(first, P1, None, "One")
    first.close()
    results, errors = [], []

    def run():
        try:
            d = Database(path)
            for _ in range(5):
                results.append(skills.grant_work(d, P1, "warehouse", 2000, "job:x", "same", NOW))
        except Exception as e:                                                                          # noqa: BLE001
            errors.append(e)
    th = [threading.Thread(target=run) for _ in range(4)]
    [x.start() for x in th]
    [x.join() for x in th]
    assert not errors and sum(r is not None for r in results) == 1
    d = Database(path)
    assert skills.work_of(d, P1, "warehouse") == 2000 and d.q1("SELECT COUNT(*) c FROM skill_log")["c"] == 1


def test_the_card_and_the_milli_conversion(db):
    assert skills.milli(600) == 1000 and skills.milli(1) == 2 and skills.milli(0) == 1 and skills.milli(6000) == 10000
    c0 = skills.card(db, P1, "mcdonalds")
    assert (c0["level"], c0["title"], c0["work"], c0["progress"], c0["next_title"]) == (0, "beginner", 0, 0.0, 50)
    skills.grant_work(db, P1, "mcdonalds", 16_000, "t", "a", NOW)
    c1 = skills.card(db, P1, "mcdonalds")
    assert 95 <= c1["level"] <= 105 and c1["title"] == "trainee" and 0 <= c1["progress"] < 1 and c1["next_title"] == 150
    assert [c["skill"] for c in skills.all_cards(db, P1)] == list(skills.SKILLS) and skills.bonus_bps(0, 2500) == 0 and skills.bonus_bps(1000, 2500) == 2500 and skills.bonus_bps(500, 2500) == 1250


def test_every_skill_name_exists_in_both_languages():
    assert set(skills.TITLES_FA) == set(skills.TITLES_EN) == {tid for _, tid in C.SKILL_TITLES}
    for sid in skills.SKILLS:
        assert skills.name("fa", sid) and skills.name("en", sid)


# ============================================================================================== the McDonald's skill
def mcd_env(layout):
    return LJ.make_env(layout)


def test_a_new_player_gets_the_base_terms_and_a_veteran_gets_better_ones(layout):
    E = mcd_env(layout)
    base = work.mcd_terms(E.db, LJ.A)
    assert (base["level"], base["wage"], base["seconds"], base["wage_bps"], base["cut_bps"]) == (0, C.MCD_WAGE_CENTS, C.MCD_SHIFT_SECONDS, 0, 0)
    skills.grant_work(E.db, LJ.A, "mcdonalds", 500_000, "test", "half", LJ.T0)
    mid = work.mcd_terms(E.db, LJ.A)
    skills.grant_work(E.db, LJ.A, "mcdonalds", 500_000, "test", "full", LJ.T0)
    top = work.mcd_terms(E.db, LJ.A)
    assert base["wage"] < mid["wage"] < top["wage"] and base["seconds"] > mid["seconds"] > top["seconds"]
    assert top["level"] == 1000 and top["wage"] == C.MCD_WAGE_CENTS * 12500 // 10000 == 1500 and top["seconds"] == C.MCD_SHIFT_SECONDS * 8000 // 10000 and top["title"] == "master"
    assert top["wage"] <= C.MCD_WAGE_CENTS * 1.25 and top["seconds"] >= C.MCD_SHIFT_SECONDS * 0.8                                         # modest: never more than +25% pay, never less than 80% of the time


def test_the_shift_saves_the_terms_of_its_start(layout):
    E = mcd_env(layout)
    skills.grant_work(E.db, LJ.A, "mcdonalds", 300_000, "test", "s", LJ.T0)
    terms = work.mcd_terms(E.db, LJ.A)
    E.go_to(LJ.A, E.city.mcd_id)
    task, started = work.mcd_start(E.db, E.city, LJ.A, E.now[0])
    assert started and task["reward"] == terms["wage"] and task["due_at"] == task["started_at"] + terms["seconds"] and work.data_of(task)["lvl"] == terms["level"] > 0
    skills.grant_work(E.db, LJ.A, "mcdonalds", 600_000, "test", "later", E.now[0])                         # a level-up during the shift changes nothing about THIS shift
    again, started2 = work.mcd_start(E.db, E.city, LJ.A, E.now[0])
    assert not started2 and (again["reward"], again["due_at"]) == (task["reward"], task["due_at"])


def test_a_collected_shift_pays_xp_and_a_unit_of_skill_once(layout):
    E = mcd_env(layout)
    charxp_before = None
    from charkit import complete
    complete(E.db, LJ.A)
    charxp_before = charxp.get(E.db, LJ.A)
    E.go_to(LJ.A, E.city.mcd_id)
    task, _ = work.mcd_start(E.db, E.city, LJ.A, E.now[0])
    E.adv(C.MCD_SHIFT_SECONDS)
    rc = work.mcd_claim(E.db, LJ.A, task["id"], E.now[0])
    assert rc["wage"] == C.MCD_WAGE_CENTS and rc["xp"] == C.MCD_XP and rc["level_from"] == 0 and rc["level_to"] == skills.level_of(E.db, LJ.A, "mcdonalds") > 0
    assert charxp.get(E.db, LJ.A) == charxp_before + C.MCD_XP and skills.work_of(E.db, LJ.A, "mcdonalds") == 1000
    assert E.db.q1("SELECT source, ref FROM character_xp_log WHERE source='job:mcd'")["ref"] == str(task["id"])
    assert E.db.q1("SELECT source, ref, delta FROM skill_log")["ref"] == str(task["id"])
    with pytest.raises(GameError) as e:                                                                  # a second claim: nothing more of anything
        work.mcd_claim(E.db, LJ.A, task["id"], E.now[0])
    assert e.value.code == "job_claimed" and charxp.get(E.db, LJ.A) == charxp_before + C.MCD_XP and skills.work_of(E.db, LJ.A, "mcdonalds") == 1000
    assert E.bal() == 10_000 + C.MCD_WAGE_CENTS and len(E.ledger(kinds=("mcd_wage",))) == 1


def test_a_shift_that_is_not_finished_or_not_yours_gives_no_progress(layout):
    E = mcd_env(layout)
    from charkit import complete
    complete(E.db, LJ.A)
    complete(E.db, LJ.B)
    E.go_to(LJ.A, E.city.mcd_id)
    task, _ = work.mcd_start(E.db, E.city, LJ.A, E.now[0])
    E.adv(C.MCD_SHIFT_SECONDS - 1)
    for who in (LJ.A, LJ.B):
        with pytest.raises(GameError):
            work.mcd_claim(E.db, who, task["id"], E.now[0])
    assert skills.work_of(E.db, LJ.A, "mcdonalds") == 0 and E.db.q1("SELECT COUNT(*) c FROM skill_log")["c"] == 0 and E.db.q1("SELECT COUNT(*) c FROM character_xp_log")["c"] == 0


def test_a_shift_saved_before_v0211_still_collects_and_counts(layout):
    E = mcd_env(layout)
    from charkit import complete
    complete(E.db, LJ.A)
    E.go_to(LJ.A, E.city.mcd_id)
    now = E.now[0]
    E.db.x("INSERT INTO work_tasks(user_id,world_id,job,kind,stage,state,data,reward,cost,paid,receipt,created_at,started_at,due_at) VALUES(?,?,?,?,?,'open',?,?,0,0,'',?,?,?)",
           (LJ.A, E.city.id, "mcd", "", "shift", json.dumps({"place": E.city.mcd_id}), 1200, now, now, now + 28_800))                  # exactly as v0.18 wrote it: no skill level in its data
    tid = E.db.q1("SELECT id FROM work_tasks WHERE user_id=?", (LJ.A,))["id"]
    E.adv(28_800)
    rc = work.mcd_claim(E.db, LJ.A, tid, E.now[0])
    assert rc["wage"] == 1200 and rc["xp"] == C.MCD_XP and skills.work_of(E.db, LJ.A, "mcdonalds") == 1000


def test_the_job_screens_show_the_skill_and_the_real_terms(layout, monkeypatch):
    E = mcd_env(layout)
    from hypemarket import workui
    assert workui._hours(28_800, "fa") == fmt.num(8, "fa") and workui._hours(23_040, "fa") == fmt.loc("6.4", "fa") and workui._hours(23_040, "en") == "6.4"
    line = workui._skill_line("fa", E.db, LJ.A)
    assert "مبتدی" in line and "↳" not in line                                                          # level 0: no perk to talk about
    skills.grant_work(E.db, LJ.A, "mcdonalds", 1_000_000, "test", "all", LJ.T0)
    line = workui._skill_line("en", E.db, LJ.A)
    assert "Master" in line and "level 1,000 of 1,000" in line.replace("1000", "1,000") or "Master" in line
    assert "25%" in line and "20%" in line


def test_new_text_keys_exist_in_both_languages_with_valid_markup():
    for k in ("mcd.skill", "mcd.skill.perk", "mcd.claimed.xp", "mcd.claimed.skill", "gift.cat", "gift.tpl", "gift.tpl.none", "gift.b.newtpl", "gift.b.custom", "gift.b.back", "gift.b.cats",
              "gift.newbase", "gift.ask_name", "gift.ask_en", "gift.ask_desc", "gift.bad_text", "gift.ask_slot", "gift.slot.none", "gift.ask_stack", "gift.b.stack", "gift.b.single",
              "gift.tpl.made", "gift.ask_quality", "gift.ask_brand", "gift.b.nobrand", "iv.l.quality", "iv.l.brand"):
        assert k in FA and k in EN, k
    assert set(FA) == set(EN)
