"""v0.21: the inventory core: the item catalog (items.py), the bag and the equipment (inventory.py) and their tables (schema 13), without Telegram.

Covered: the catalog and what a bad definition does; creating and reading an inventory; adding and removing; stackable and non-stackable items; the largest stack; the bag's capacity
(a grant that does not fit gives nothing); wearing, taking off and replacing; the acquisition record; persistence across a restart; ownership (one player can never reach another's items);
repeated requests (a repeated reference gives or takes once, two connections racing included); using an item (only with a registered effect); atomicity inside an outer transaction;
the books (`audit`); and the 12 -> 13 migration of an existing database (nobody gets an item, every gift code survives the rebuild exactly). A real Telegram client was not used."""
import re
import sqlite3
import threading

import pytest

pytestmark = pytest.mark.usefixtures("legacy_catalog")      # v0.211 seeds no item: these suites bring the 17 old starter items themselves

from hypemarket import config as C, core, gifts, inventory as I, items
from hypemarket.core import GameError
from hypemarket.db import Database

P1, P2 = 885001, 885002
NOW = 1_700_000_000


@pytest.fixture()
def db():
    d = Database(":memory:")
    core.set_clock(lambda: NOW)
    core.ensure_user(d, P1, None, "One")
    core.ensure_user(d, P2, None, "Two")
    return d


@pytest.fixture()
def registry():
    """A scratch copy of the catalog registries that is put back afterwards (a test may register what it needs)."""
    saved = (dict(items.CATEGORIES), dict(items.SLOTS), dict(items.ITEMS), dict(items.EFFECTS))
    yield items
    for reg, old in zip((items.CATEGORIES, items.SLOTS, items.ITEMS, items.EFFECTS), saved):
        reg.clear()
        reg.update(old)


def stacks(db, uid, item_id=None):
    sql, args = "SELECT quantity FROM item_inventory WHERE user_id=?", [uid]
    if item_id:
        sql += " AND item_id=?"
        args.append(item_id)
    return sorted((r["quantity"] for r in db.qa(sql + " ORDER BY id", tuple(args))), reverse=True)


def log(db, uid=None):
    return [tuple(r) for r in db.qa("SELECT user_id,item_id,delta,source,ref FROM item_log" + (" WHERE user_id=%d" % uid if uid else "") + " ORDER BY id")]


def state(db, uid):
    return ([tuple(r) for r in db.qa("SELECT * FROM item_inventory WHERE user_id=? ORDER BY id", (uid,))],
            [tuple(r) for r in db.qa("SELECT * FROM item_equipment WHERE user_id=? ORDER BY slot", (uid,))],
            log(db, uid))


# ============================================================================================== the catalog
def test_the_starting_catalog_is_complete_and_valid():
    assert list(items.CATEGORIES) == ["clothing", "footwear", "accessories", "food", "personal", "gifts", "misc"]
    assert list(items.SLOTS) == ["headwear", "upper", "lower", "footwear", "accessory"]
    for cid in items.CATEGORIES:
        assert any(d.category == cid for d in items.ITEMS.values()), cid                          # every category has something to show
    assert len(items.ITEMS) >= 15
    for d in items.ITEMS.values():
        assert items.ID_RE.fullmatch(d.id) and d.name_fa.strip() and d.description_fa.strip() and d.name_en and d.description_en
        assert d.category in items.CATEGORIES and (d.slot is None or d.slot in items.SLOTS)
        assert d.equippable == (d.slot is not None) and (d.stackable or d.max_stack == 1) and (not d.stackable or d.max_stack >= 2)
    assert {d.slot for d in items.ITEMS.values() if d.equippable} == set(items.SLOTS)             # every slot can really be filled
    assert any(d.stackable for d in items.ITEMS.values()) and any(not d.stackable for d in items.ITEMS.values())


def test_no_starting_item_pretends_to_have_an_effect():
    """No hunger, thirst or health system exists, so no item is usable and no effect is registered by the game."""
    assert items.EFFECTS == {}
    assert not any(d.usable or d.effect for d in items.ITEMS.values())
    assert not any(items.can_use(d) for d in items.ITEMS.values())


@pytest.mark.parametrize("fields,why", [
    (dict(id="Bad-Id", name_fa="x", category="misc"), "id"),
    (dict(id="ab", name_fa="x", category="misc"), "id"),
    (dict(id="a" * 25, name_fa="x", category="misc"), "id"),
    (dict(id="thing_a", name_fa="  ", category="misc"), "name"),
    (dict(id="thing_a", name_fa="x", name_en="  ", category="misc"), "english"),
    (dict(id="thing_a", name_fa="x", category="nowhere"), "category"),
    (dict(id="thing_a", name_fa="x", category="misc", stackable=True, max_stack=1), "stackable"),
    (dict(id="thing_a", name_fa="x", category="misc", stackable=False, max_stack=5), "non-stackable"),
    (dict(id="thing_a", name_fa="x", category="misc", stackable=True, max_stack=10 ** 6), "too big"),
    (dict(id="thing_a", name_fa="x", category="misc", max_stack=True), "bool"),
    (dict(id="thing_a", name_fa="x", category="misc", equippable=True), "equippable without a slot"),
    (dict(id="thing_a", name_fa="x", category="misc", slot="upper"), "slot without equippable"),
    (dict(id="thing_a", name_fa="x", category="misc", equippable=True, slot="nowhere"), "unknown slot"),
    (dict(id="thing_a", name_fa="x", category="misc", usable=True), "usable without an effect"),
    (dict(id="thing_a", name_fa="x", category="misc", effect="heal"), "effect without usable"),
    (dict(id="thing_a", name_fa="x", category="misc", usable=True, effect="Bad Effect"), "effect name"),
])
def test_a_bad_definition_is_refused_when_it_is_registered(registry, fields, why):
    before = dict(items.ITEMS)
    with pytest.raises(ValueError):
        items.register_item(**fields)
    assert items.ITEMS == before, why


def test_a_new_category_slot_and_item_need_only_registration(registry):
    items.register_category("tools", "ابزار", "Tools", "🔧")
    items.register_slot("belt", "کمربند", "Belt", "🥋")
    d = items.register_item(id="toolbelt", name_fa="کمربند ابزار", category="tools", equippable=True, slot="belt")
    assert items.get("toolbelt") is d and list(items.CATEGORIES)[-1] == "tools" and list(items.SLOTS)[-1] == "belt"
    assert items.category_of("toolbelt") == "tools" and items.can_equip(d)
    with pytest.raises(ValueError):
        items.register_category("tools", "x", "x", "x")
    with pytest.raises(ValueError):
        items.register_slot("belt", "x", "x", "x")
    with pytest.raises(ValueError):
        items.register_item(id="toolbelt", name_fa="x", category="tools")


def test_names_fall_back_and_an_unknown_item_is_still_named():
    d = items.get("tshirt_white")
    assert items.name("fa", "tshirt_white") == d.name_fa and items.name("en", "tshirt_white") == d.name_en
    assert items.name("fa", "gone_item") == items.UNKNOWN_NAME["fa"] and items.name("en", "gone_item") == items.UNKNOWN_NAME["en"]
    assert items.category_of("gone_item") == items.FALLBACK_CATEGORY and items.icon("gone_item") == "❔"
    assert items.order_of("gone_item") == len(items.ITEMS)


def test_the_english_name_falls_back_to_persian(registry):
    items.register_item(id="plain_one", name_fa="چیز ساده", category="misc")
    assert items.name("en", "plain_one") == "چیز ساده" and items.description("en", "plain_one") == ""


# ============================================================================================== creating and reading
def test_a_new_inventory_is_empty_and_every_category_counts_zero(db):
    assert I.bag(db, P1) == [] and I.total(db, P1) == 0 and I.stack_count(db, P1) == 0 and I.worn(db, P1) == {}
    assert I.counts_by_category(db, P1) == {cid: 0 for cid in items.CATEGORIES}
    assert I.count(db, P1, "sandwich") == 0 and I.entry(db, P1, "sandwich") is None and I.audit(db, P1) == []


def test_giving_items_and_reading_them_back(db):
    assert I.give(db, P1, "tshirt_white", 1, "gift", "a", now=NOW + 5) == 1
    assert I.give(db, P1, "sandwich", 7, "gift", "b") == 7
    bag = I.bag(db, P1)
    assert [(e.item_id, e.qty, e.stacks) for e in bag] == [("tshirt_white", 1, 1), ("sandwich", 7, 1)]          # catalog order
    assert I.total(db, P1) == 8 and I.count(db, P1, "sandwich") == 7
    assert I.counts_by_category(db, P1)["clothing"] == 1 and I.counts_by_category(db, P1)["food"] == 7
    assert [e.item_id for e in I.entries_in(db, P1, "food")] == ["sandwich"] and I.entries_in(db, P1, "gifts") == []
    row = I.stack(db, P1, bag[0].stack_id)
    assert (row["acquired_at"], row["source"], row["quantity"]) == (NOW + 5, "gift", 1)
    assert I.audit(db, P1) == [] and I.stack(db, P2, bag[0].stack_id) is None


def test_non_stackable_items_are_one_stack_each(db):
    assert I.give(db, P1, "tshirt_white", 3, "gift", "x") == 3
    assert stacks(db, P1) == [1, 1, 1]
    e = I.entry(db, P1, "tshirt_white")
    assert (e.qty, e.stacks) == (3, 3)


def test_stackable_items_fill_a_stack_then_start_a_new_one(db):
    d = items.get("soda_can")
    assert d.stackable and d.max_stack == 24
    I.give(db, P1, "soda_can", 10, "gift", "a")
    I.give(db, P1, "soda_can", 10, "gift", "b")
    assert stacks(db, P1, "soda_can") == [20]                                         # merged into one
    I.give(db, P1, "soda_can", 30, "gift", "c")
    assert stacks(db, P1, "soda_can") == [24, 24, 2] and I.count(db, P1, "soda_can") == 50
    I.give(db, P1, "soda_can", 22, "gift", "d")
    assert stacks(db, P1, "soda_can") == [24, 24, 24] and I.audit(db, P1) == []        # the 2 filled up, nothing overflowed
    assert all(q <= d.max_stack for q in stacks(db, P1, "soda_can"))


def test_a_merged_stack_keeps_the_record_of_when_it_started(db):
    I.give(db, P1, "sandwich", 2, "gift", "a", now=NOW + 1)
    I.give(db, P1, "sandwich", 2, "system", "b", now=NOW + 99)
    r = db.q1("SELECT * FROM item_inventory WHERE user_id=?", (P1,))
    assert (r["quantity"], r["acquired_at"], r["source"]) == (4, NOW + 1, "gift")
    assert [(x[2], x[3]) for x in log(db, P1)] == [(2, "gift"), (2, "system")]         # the ledger keeps every grant with its own source


@pytest.mark.parametrize("qty", [0, -1, 1.5, "2", None, True, C.INV_MAX_GIVE + 1])
def test_a_bad_quantity_is_refused_and_nothing_changes(db, qty):
    with pytest.raises(ValueError):
        I.give(db, P1, "sandwich", qty, "gift", "q")
    with pytest.raises(ValueError):
        I.take(db, P1, "sandwich", qty, "use", "q")
    assert I.total(db, P1) == 0 and log(db) == []


@pytest.mark.parametrize("source", ["", "Gift", "9x", "a b", "x" * 41, None, 5])
def test_a_bad_source_is_refused(db, source):
    with pytest.raises(ValueError):
        I.give(db, P1, "sandwich", 1, source, "r")


@pytest.mark.parametrize("ref", ["", "  ", "x" * 81, 5])
def test_a_bad_reference_is_refused(db, ref):
    with pytest.raises(ValueError):
        I.give(db, P1, "sandwich", 1, "gift", ref)


def test_unknown_items_and_players_are_refused(db):
    with pytest.raises(GameError) as e:
        I.give(db, P1, "no_such_item", 1, "gift", "r")
    assert e.value.code == "item_unknown"
    with pytest.raises(GameError) as e:
        I.give(db, 999_999, "sandwich", 1, "gift", "r")
    assert e.value.code == "no_user"
    db.x("UPDATE users SET bot=1 WHERE id=?", (P2,))
    with pytest.raises(GameError) as e:
        I.give(db, P2, "sandwich", 1, "gift", "r")
    assert e.value.code == "no_user" and log(db) == [] and I.total(db, P2) == 0


# ============================================================================================== removing
def test_taking_items_out_partly_and_completely(db):
    I.give(db, P1, "sandwich", 15, "gift", "a")
    assert I.take(db, P1, "sandwich", 4, "use", "t1") == 4 and I.count(db, P1, "sandwich") == 11
    assert I.take(db, P1, "sandwich", 11, "use", "t2") == 11 and I.count(db, P1, "sandwich") == 0 and I.entry(db, P1, "sandwich") is None
    assert stacks(db, P1) == [] and I.audit(db, P1) == []
    assert [(x[2], x[3], x[4]) for x in log(db, P1)] == [(15, "gift", "a"), (-4, "use", "t1"), (-11, "use", "t2")]


def test_taking_more_than_there_is_takes_nothing(db):
    I.give(db, P1, "sandwich", 3, "gift", "a")
    with pytest.raises(GameError) as e:
        I.take(db, P1, "sandwich", 4, "use", "t")
    assert e.value.code == "inv_few" and e.value.kw == {"have": 3, "need": 4}
    assert I.count(db, P1, "sandwich") == 3 and len(log(db)) == 1
    with pytest.raises(GameError):
        I.take(db, P1, "tshirt_white", 1, "use", "t")                                  # never owned


def test_removal_empties_the_smallest_stacks_first_so_full_stacks_stay_full(db):
    I.give(db, P1, "soda_can", 24 + 24 + 5, "gift", "a")
    assert stacks(db, P1) == [24, 24, 5]
    I.take(db, P1, "soda_can", 7, "use", "t")
    assert stacks(db, P1) == [24, 22]                                                  # the 5 went, then 2 more from one full stack


def test_the_quantity_can_never_go_negative(db):
    I.give(db, P1, "soda_can", 2, "gift", "a")
    for n in (3, 99):
        with pytest.raises(GameError):
            I.take(db, P1, "soda_can", n, "use", f"t{n}")
    with pytest.raises(sqlite3.IntegrityError):
        db.x("UPDATE item_inventory SET quantity=0 WHERE user_id=?", (P1,))
    with pytest.raises(sqlite3.IntegrityError):
        db.x("INSERT INTO item_inventory(user_id,item_id,quantity,acquired_at,source) VALUES(?,?,?,?,?)", (P1, "sandwich", -1, NOW, "x"))
    assert stacks(db, P1) == [2]


def test_an_old_stack_cannot_be_given_to_another_owner_or_another_item(db):
    I.give(db, P1, "sandwich", 2, "gift", "a")
    for sql in ("UPDATE item_inventory SET user_id=%d" % P2, "UPDATE item_inventory SET item_id='soda_can'", "UPDATE item_inventory SET id=99"):
        with pytest.raises(sqlite3.IntegrityError):
            db.x(sql)


# ============================================================================================== capacity
def test_a_grant_that_does_not_fit_gives_nothing(db, monkeypatch):
    monkeypatch.setattr(C, "INV_MAX_STACKS", 3)
    I.give(db, P1, "tshirt_white", 2, "gift", "a")                                      # two stacks used
    snap = state(db, P1)
    with pytest.raises(GameError) as e:
        I.give(db, P1, "soda_can", 49, "gift", "b")                                    # would need 3 new stacks (24 + 24 + 1)
    assert e.value.code == "inv_full" and e.value.kw == {"max": 3}
    with pytest.raises(GameError):
        I.give(db, P1, "jeans_blue", 2, "gift", "c")                                   # two single stacks do not fit either: not even one of them is given
    assert state(db, P1) == snap and I.audit(db, P1) == []
    assert I.fits(db, P1, "soda_can", 24) and not I.fits(db, P1, "soda_can", 25) and I.fits(db, P1, "jeans_blue", 1) and not I.fits(db, P1, "jeans_blue", 2)
    assert I.give(db, P1, "soda_can", 24, "gift", "d") == 24 and I.stack_count(db, P1) == 3


def test_topping_up_a_stack_needs_no_new_place(db, monkeypatch):
    monkeypatch.setattr(C, "INV_MAX_STACKS", 1)
    I.give(db, P1, "sandwich", 5, "gift", "a")
    assert I.give(db, P1, "sandwich", 15, "gift", "b") == 15 and stacks(db, P1) == [20]
    with pytest.raises(GameError):
        I.give(db, P1, "sandwich", 1, "gift", "c")                                     # the stack is full and there is no place for a second one


def test_capacity_is_per_player(db, monkeypatch):
    monkeypatch.setattr(C, "INV_MAX_STACKS", 1)
    I.give(db, P1, "old_key", 1, "gift", "a")
    with pytest.raises(GameError):
        I.give(db, P1, "old_key", 1, "gift", "b")
    assert I.give(db, P2, "old_key", 1, "gift", "c") == 1


# ============================================================================================== item data
def test_item_specific_data_is_stored_canonically_and_kept_apart(db):
    I.give(db, P1, "sandwich", 2, "gift", "a", data={"y": 1, "a": "é"})
    I.give(db, P1, "sandwich", 2, "gift", "b", data={"a": "é", "y": 1})              # the same data in another order merges
    I.give(db, P1, "sandwich", 2, "gift", "c")                                         # no data: another stack
    I.give(db, P1, "sandwich", 2, "gift", "d", data={"a": "other"})
    assert stacks(db, P1) == [4, 2, 2]
    r = db.qa("SELECT data FROM item_inventory WHERE user_id=? ORDER BY id", (P1,))
    assert r[0]["data"] == '{"a":"é","y":1}' and r[1]["data"] is None
    for bad in ({}, [1], "x", {"k": "v" * 500}):
        with pytest.raises(ValueError):
            I.give(db, P1, "sandwich", 1, "gift", "bad", data=bad)


def test_item_data_and_the_acquisition_record_survive_wearing_and_taking_off(db):
    I.give(db, P1, "tshirt_white", 1, "gift", "a", data={"colour": "red"}, now=NOW + 7)
    sid = I.entry(db, P1, "tshirt_white").stack_id
    I.equip(db, P1, sid, now=NOW + 50)
    w = I.worn(db, P1)["upper"]
    assert (w.item_id, w.data, w.acquired_at, w.source, w.equipped_at) == ("tshirt_white", '{"colour":"red"}', NOW + 7, "gift", NOW + 50)
    I.unequip(db, P1, "upper")
    r = db.q1("SELECT * FROM item_inventory WHERE user_id=?", (P1,))
    assert (r["data"], r["acquired_at"], r["source"], r["quantity"]) == ('{"colour":"red"}', NOW + 7, "gift", 1)


# ============================================================================================== wearing
def stack_of(db, uid, item_id):
    return I.entry(db, uid, item_id).stack_id


def test_wearing_moves_one_item_out_of_the_bag_into_its_slot(db):
    I.give(db, P1, "tshirt_white", 1, "gift", "a")
    I.give(db, P1, "jeans_blue", 1, "gift", "b")
    r = I.equip(db, P1, stack_of(db, P1, "tshirt_white"), now=NOW + 3)
    assert r == {"item": "tshirt_white", "slot": "upper", "replaced": None, "data": None, "replaced_data": None}
    assert [e.item_id for e in I.bag(db, P1)] == ["jeans_blue"] and I.total(db, P1) == 1
    assert set(I.worn(db, P1)) == {"upper"} and I.worn(db, P1)["upper"].item.id == "tshirt_white"
    assert I.audit(db, P1) == []


def test_every_slot_can_be_worn_at_the_same_time(db):
    for item in ("cap_black", "tshirt_white", "jeans_blue", "sneakers_white", "sunglasses_black"):
        I.give(db, P1, item, 1, "gift", item)
        I.equip(db, P1, stack_of(db, P1, item))
    assert set(I.worn(db, P1)) == set(items.SLOTS) and I.total(db, P1) == 0 and I.audit(db, P1) == []


def test_wearing_in_an_occupied_slot_returns_the_old_item_to_the_bag(db):
    I.give(db, P1, "tshirt_white", 1, "gift", "a", now=NOW + 1)
    I.give(db, P1, "hoodie_green", 1, "system", "b", now=NOW + 2)
    I.equip(db, P1, stack_of(db, P1, "tshirt_white"))
    r = I.equip(db, P1, stack_of(db, P1, "hoodie_green"))
    assert r == {"item": "hoodie_green", "slot": "upper", "replaced": "tshirt_white", "data": None, "replaced_data": None}
    assert I.worn(db, P1)["upper"].item_id == "hoodie_green" and len(I.worn(db, P1)) == 1             # one item per slot
    assert [(e.item_id, e.qty) for e in I.bag(db, P1)] == [("tshirt_white", 1)] and I.total(db, P1) == 1
    row = db.q1("SELECT * FROM item_inventory WHERE user_id=?", (P1,))
    assert (row["acquired_at"], row["source"]) == (NOW + 1, "gift")                                    # it came back with its own record
    assert I.audit(db, P1) == []


def test_wearing_the_same_kind_again_swaps_one_for_the_other(db):
    I.give(db, P1, "tshirt_white", 2, "gift", "a")
    I.equip(db, P1, stack_of(db, P1, "tshirt_white"))
    r = I.equip(db, P1, stack_of(db, P1, "tshirt_white"))
    assert r["replaced"] == "tshirt_white" and I.count(db, P1, "tshirt_white") == 1 and len(I.worn(db, P1)) == 1 and I.audit(db, P1) == []


def test_a_slot_holds_one_item_even_if_someone_writes_a_second_row(db):
    I.give(db, P1, "tshirt_white", 1, "gift", "a")
    I.equip(db, P1, stack_of(db, P1, "tshirt_white"))
    with pytest.raises(sqlite3.IntegrityError):
        db.x("INSERT INTO item_equipment(user_id,slot,item_id,acquired_at,source,equipped_at) VALUES(?,?,?,?,?,?)", (P1, "upper", "hoodie_green", NOW, "x", NOW))


def test_things_that_cannot_be_worn_are_refused_and_nothing_changes(db):
    I.give(db, P1, "sandwich", 2, "gift", "a")
    I.give(db, P1, "tshirt_white", 1, "gift", "b")
    snap = state(db, P1)
    with pytest.raises(GameError) as e:
        I.equip(db, P1, stack_of(db, P1, "sandwich"))
    assert e.value.code == "inv_noequip"
    for bad in (999_999, 0, -5, True, "3", None):
        with pytest.raises(GameError) as e:
            I.equip(db, P1, bad)
        assert e.value.code == "inv_gone"
    assert state(db, P1) == snap


def test_an_item_the_catalog_no_longer_knows_stays_but_cannot_be_worn_or_used(db):
    db.x("INSERT INTO item_inventory(user_id,item_id,quantity,acquired_at,source) VALUES(?,?,?,?,?)", (P1, "retired_hat", 2, NOW, "gift"))
    e = I.entry(db, P1, "retired_hat")
    assert e.item is None and e.category == items.FALLBACK_CATEGORY and I.counts_by_category(db, P1)["misc"] == 2
    with pytest.raises(GameError) as err:
        I.equip(db, P1, e.stack_id)
    assert err.value.code == "inv_noequip"
    with pytest.raises(GameError) as err:
        I.use(db, P1, e.stack_id, 2)
    assert err.value.code == "inv_nouse"
    assert I.take(db, P1, "retired_hat", 2, "system", "t") == 2                          # it can still be taken out
    assert I.audit(db, P1) == ["retired_hat: owns 0 but the log adds up to -2"]            # (the seed row above bypassed the ledger: the books notice)


def test_taking_off_returns_the_item_and_an_empty_slot_is_refused(db):
    I.give(db, P1, "boots_brown", 1, "gift", "a")
    I.equip(db, P1, stack_of(db, P1, "boots_brown"))
    assert I.unequip(db, P1, "footwear") == "boots_brown"
    assert I.worn(db, P1) == {} and I.count(db, P1, "boots_brown") == 1 and I.audit(db, P1) == []
    for slot in ("footwear", "upper", "no_slot"):
        with pytest.raises(GameError) as e:
            I.unequip(db, P1, slot)
        assert e.value.code == "equip_empty"


def test_candidates_are_the_bag_items_that_fit_the_slot(db):
    for item in ("tshirt_white", "hoodie_green", "jeans_blue", "sandwich"):
        I.give(db, P1, item, 1, "gift", item)
    assert [e.item_id for e in I.candidates(db, P1, "upper")] == ["tshirt_white", "hoodie_green"]
    assert I.candidates(db, P1, "lower")[0].item_id == "jeans_blue" and I.candidates(db, P1, "headwear") == []


def test_a_worn_item_is_not_in_the_bag_so_take_cannot_spend_it(db):
    I.give(db, P1, "tshirt_white", 1, "gift", "a")
    I.equip(db, P1, stack_of(db, P1, "tshirt_white"))
    with pytest.raises(GameError) as e:
        I.take(db, P1, "tshirt_white", 1, "use", "t")
    assert e.value.code == "inv_few" and I.worn(db, P1)["upper"].item_id == "tshirt_white"


# ---------- wearing and a full bag: all or nothing
def _stackable_wearable(registry):
    items.register_item(id="badge_pin", name_fa="سنجاق", category="accessories", stackable=True, max_stack=5, equippable=True, slot="accessory")


def test_wearing_into_a_full_bag_changes_nothing_when_the_old_item_has_no_room(db, registry, monkeypatch):
    _stackable_wearable(registry)
    I.give(db, P1, "watch_steel", 1, "gift", "w")
    I.equip(db, P1, stack_of(db, P1, "watch_steel"))                                    # the watch is worn
    I.give(db, P1, "badge_pin", 2, "gift", "b")                                          # one stack of 2 badges in the bag
    monkeypatch.setattr(C, "INV_MAX_STACKS", 1)
    snap = state(db, P1)
    with pytest.raises(GameError) as e:
        I.equip(db, P1, stack_of(db, P1, "badge_pin"))                                  # the badge stack keeps 1, the watch needs a second place
    assert e.value.code == "inv_full" and state(db, P1) == snap and I.audit(db, P1) == []


def test_the_place_freed_by_the_worn_item_is_available_to_the_one_it_replaces(db, monkeypatch):
    monkeypatch.setattr(C, "INV_MAX_STACKS", 1)
    I.give(db, P1, "tshirt_white", 1, "gift", "a")
    I.equip(db, P1, stack_of(db, P1, "tshirt_white"))
    I.give(db, P1, "hoodie_green", 1, "gift", "b")                                       # the bag is exactly full
    r = I.equip(db, P1, stack_of(db, P1, "hoodie_green"))
    assert r["replaced"] == "tshirt_white" and I.worn(db, P1)["upper"].item_id == "hoodie_green" and I.stack_count(db, P1) == 1 and I.audit(db, P1) == []


def test_taking_off_into_a_full_bag_keeps_it_worn(db, monkeypatch):
    I.give(db, P1, "cap_black", 1, "gift", "a")
    I.equip(db, P1, stack_of(db, P1, "cap_black"))
    I.give(db, P1, "old_key", 1, "gift", "b")
    monkeypatch.setattr(C, "INV_MAX_STACKS", 1)
    snap = state(db, P1)
    with pytest.raises(GameError) as e:
        I.unequip(db, P1, "headwear")
    assert e.value.code == "inv_full" and state(db, P1) == snap and I.worn(db, P1)["headwear"].item_id == "cap_black"


# ============================================================================================== ownership
def test_one_player_cannot_reach_another_players_items(db):
    I.give(db, P1, "tshirt_white", 1, "gift", "a")
    I.give(db, P1, "sandwich", 3, "gift", "b")
    sid = stack_of(db, P1, "tshirt_white")
    snap1 = state(db, P1)
    with pytest.raises(GameError) as e:
        I.equip(db, P2, sid)                                                           # P2 pressing P1's stack id
    assert e.value.code == "inv_gone"
    with pytest.raises(GameError):
        I.use(db, P2, sid, 1)
    with pytest.raises(GameError):
        I.take(db, P2, "sandwich", 1, "use", "t")
    assert state(db, P1) == snap1 and state(db, P2) == ([], [], []) and I.total(db, P2) == 0
    assert I.stack(db, P2, sid) is None and I.bag(db, P2) == [] and I.worn(db, P2) == {}


def test_each_player_has_their_own_bag_and_equipment(db):
    I.give(db, P1, "tshirt_white", 1, "gift", "a")
    I.give(db, P2, "tshirt_white", 1, "gift", "a")                                      # the same source and reference for another player is its own grant
    I.equip(db, P1, stack_of(db, P1, "tshirt_white"))
    assert set(I.worn(db, P1)) == {"upper"} and I.worn(db, P2) == {} and I.count(db, P2, "tshirt_white") == 1
    assert I.audit(db, P1) == [] and I.audit(db, P2) == []


# ============================================================================================== repeated requests
def test_the_same_reference_gives_only_once(db):
    assert I.give(db, P1, "gift_box", 2, "gift", "HM-ABCD") == 2
    for _ in range(3):
        assert I.give(db, P1, "gift_box", 2, "gift", "HM-ABCD") is None
    assert I.count(db, P1, "gift_box") == 2 and len(log(db)) == 1
    assert I.give(db, P1, "gift_box", 2, "gift", "HM-OTHER") == 2                        # another reference is another grant
    assert I.give(db, P1, "greeting_card", 1, "gift", "HM-ABCD") == 1                    # the same reference for another item is its own grant
    assert I.give(db, P1, "gift_box", 1, "system", "HM-ABCD") == 1                       # and so is another source
    assert I.count(db, P1, "gift_box") == 5


def test_without_a_reference_every_call_gives(db):
    I.give(db, P1, "greeting_card", 1, "system")
    I.give(db, P1, "greeting_card", 1, "system")
    assert I.count(db, P1, "greeting_card") == 2


def test_the_same_reference_takes_only_once(db):
    I.give(db, P1, "sandwich", 10, "gift", "a")
    assert I.take(db, P1, "sandwich", 3, "use", "meal-1") == 3
    assert I.take(db, P1, "sandwich", 3, "use", "meal-1") is None and I.count(db, P1, "sandwich") == 7
    assert I.take(db, P1, "sandwich", 3, "use", "meal-2") == 3 and I.count(db, P1, "sandwich") == 4


def test_the_ledger_itself_refuses_a_second_grant_of_the_same_reference(db):
    I.give(db, P1, "gift_box", 1, "gift", "R")
    with pytest.raises(sqlite3.IntegrityError):
        db.x("INSERT INTO item_log(user_id,item_id,delta,source,ref,created_at) VALUES(?,?,?,?,?,?)", (P1, "gift_box", 1, "gift", "R", NOW))
    db.x("INSERT INTO item_log(user_id,item_id,delta,source,ref,created_at) VALUES(?,?,?,?,?,?)", (P1, "gift_box", 1, "gift", None, NOW))      # no reference: no such rule
    db.x("INSERT INTO item_log(user_id,item_id,delta,source,ref,created_at) VALUES(?,?,?,?,?,?)", (P1, "gift_box", 1, "gift", None, NOW))


def test_the_ledger_is_append_only(db):
    I.give(db, P1, "gift_box", 1, "gift", "R")
    with pytest.raises(sqlite3.IntegrityError):
        db.x("UPDATE item_log SET delta=5")
    with pytest.raises(sqlite3.IntegrityError):
        db.x("DELETE FROM item_log")
    assert len(log(db)) == 1


def test_two_connections_racing_for_the_same_grant_give_it_once(tmp_path):
    path = str(tmp_path / "race.db")
    boot = Database(path)
    core.set_clock(lambda: NOW)
    core.ensure_user(boot, P1, None, "One")
    barrier, out, lock = threading.Barrier(8), [], threading.Lock()

    def worker():
        d = Database(path)
        barrier.wait()
        try:
            res = I.give(d, P1, "gift_box", 3, "gift", "RACE")
        except GameError as e:
            res = e.code
        with lock:
            out.append(res)

    ts = [threading.Thread(target=worker) for _ in range(8)]
    [t_.start() for t_ in ts]
    [t_.join() for t_ in ts]
    assert sorted(out, key=str).count(3) == 1 and out.count(None) == 7
    chk = Database(path)
    assert I.count(chk, P1, "gift_box") == 3 and len(log(chk)) == 1 and I.audit(chk, P1) == []


def test_two_connections_racing_to_wear_the_same_stack_wear_it_once(tmp_path):
    path = str(tmp_path / "wear.db")
    boot = Database(path)
    core.set_clock(lambda: NOW)
    core.ensure_user(boot, P1, None, "One")
    I.give(boot, P1, "tshirt_white", 1, "gift", "a")
    sid = I.entry(boot, P1, "tshirt_white").stack_id
    barrier, out, lock = threading.Barrier(6), [], threading.Lock()

    def worker():
        d = Database(path)
        barrier.wait()
        try:
            I.equip(d, P1, sid)
            res = "ok"
        except GameError as e:
            res = e.code
        with lock:
            out.append(res)

    ts = [threading.Thread(target=worker) for _ in range(6)]
    [t_.start() for t_ in ts]
    [t_.join() for t_ in ts]
    assert out.count("ok") == 1 and out.count("inv_gone") == 5
    chk = Database(path)
    assert set(I.worn(chk, P1)) == {"upper"} and I.total(chk, P1) == 0 and I.audit(chk, P1) == []


# ============================================================================================== persistence
def test_everything_survives_a_restart(tmp_path):
    path = str(tmp_path / "keep.db")
    d = Database(path)
    core.set_clock(lambda: NOW)
    core.ensure_user(d, P1, None, "One")
    I.give(d, P1, "tshirt_white", 2, "gift", "a", now=NOW + 1)
    I.give(d, P1, "soda_can", 30, "gift", "b")
    I.give(d, P1, "cap_black", 1, "system", "c")
    I.equip(d, P1, I.entry(d, P1, "tshirt_white").stack_id, now=NOW + 9)
    I.equip(d, P1, I.entry(d, P1, "cap_black").stack_id)
    before = state(d, P1)
    d.close()
    d2 = Database(path)
    assert state(d2, P1) == before
    assert [(e.item_id, e.qty) for e in I.bag(d2, P1)] == [("tshirt_white", 1), ("soda_can", 30)]
    assert set(I.worn(d2, P1)) == {"upper", "headwear"} and I.worn(d2, P1)["upper"].equipped_at == NOW + 9
    assert I.give(d2, P1, "soda_can", 5, "gift", "b") is None and I.audit(d2, P1) == []            # the ledger of before still guards the reference


# ============================================================================================== using
def test_use_exists_only_with_a_registered_effect(db, registry):
    items.register_item(id="tonic", name_fa="تونیک", category="food", stackable=True, max_stack=5, usable=True, effect="tonic_fx")
    d = items.get("tonic")
    assert d.usable and not items.can_use(d)                                            # declared, but no system has built the effect
    I.give(db, P1, "tonic", 3, "gift", "a")
    sid = I.entry(db, P1, "tonic").stack_id
    with pytest.raises(GameError) as e:
        I.use(db, P1, sid, 3)
    assert e.value.code == "inv_nouse" and I.count(db, P1, "tonic") == 3 and len(log(db)) == 1
    calls = []
    items.register_effect("tonic_fx", lambda db_, uid, item, now: calls.append((uid, item.id, now)) or {"fa": "حالت بهتر شد", "en": "Feeling better"})
    assert items.can_use(d)
    r = I.use(db, P1, sid, 3, now=NOW + 4)
    assert r == {"item": "tonic", "message": {"fa": "حالت بهتر شد", "en": "Feeling better"}, "data": None} and calls == [(P1, "tonic", NOW + 4)]
    assert I.count(db, P1, "tonic") == 2 and log(db)[-1][2:4] == (-1, "use") and I.audit(db, P1) == []


def test_a_repeated_use_button_cannot_use_a_second_unit(db, registry):
    items.register_item(id="tonic", name_fa="تونیک", category="food", stackable=True, max_stack=5, usable=True, effect="tonic_fx")
    items.register_effect("tonic_fx", lambda *a: None)
    I.give(db, P1, "tonic", 3, "gift", "a")
    sid = I.entry(db, P1, "tonic").stack_id
    I.use(db, P1, sid, 3)
    with pytest.raises(GameError) as e:
        I.use(db, P1, sid, 3)                                                           # the same button again: it was drawn with 3, now there are 2
    assert e.value.code == "inv_stale" and I.count(db, P1, "tonic") == 2


def test_an_effect_that_fails_consumes_nothing(db, registry):
    items.register_item(id="tonic", name_fa="تونیک", category="food", usable=True, effect="tonic_fx")

    def broken(db_, uid, item, now):
        db_.x("INSERT INTO item_log(user_id,item_id,delta,source,ref,created_at) VALUES(?,?,?,?,?,?)", (uid, "tonic", 1, "side", None, now))     # a write the effect made...
        raise GameError("cash_low", need=1, have=0)                                       # ...and then it refused

    items.register_effect("tonic_fx", broken)
    I.give(db, P1, "tonic", 1, "gift", "a")
    snap = state(db, P1)
    with pytest.raises(GameError) as e:
        I.use(db, P1, I.entry(db, P1, "tonic").stack_id, 1)
    assert e.value.code == "cash_low" and state(db, P1) == snap                          # the effect's own write was undone with the rest


def test_using_the_last_unit_removes_the_stack(db, registry):
    items.register_item(id="tonic", name_fa="تونیک", category="food", usable=True, effect="tonic_fx")
    items.register_effect("tonic_fx", lambda *a: None)
    I.give(db, P1, "tonic", 1, "gift", "a")
    I.use(db, P1, I.entry(db, P1, "tonic").stack_id, 1)
    assert I.entry(db, P1, "tonic") is None and stacks(db, P1) == [] and I.audit(db, P1) == []


def test_effects_are_registered_once_and_must_be_callable(registry):
    with pytest.raises(ValueError):
        items.register_effect("Bad Name", lambda *a: None)
    with pytest.raises(ValueError):
        items.register_effect("ok_effect", "not callable")
    items.register_effect("ok_effect", lambda *a: None)
    with pytest.raises(ValueError):
        items.register_effect("ok_effect", lambda *a: None)


# ============================================================================================== atomicity
def test_a_failed_grant_inside_a_bigger_transaction_undoes_only_itself(db, monkeypatch):
    monkeypatch.setattr(C, "INV_MAX_STACKS", 2)
    with db.tx():
        I.give(db, P1, "old_key", 1, "gift", "a")
        I.give(db, P1, "umbrella", 1, "gift", "b")
        with pytest.raises(GameError):
            I.give(db, P1, "notebook", 1, "gift", "c")                                 # full: undone
        assert I.total(db, P1) == 2
    assert I.total(db, P1) == 2 and len(log(db)) == 2 and I.audit(db, P1) == []


def test_a_failed_wear_inside_a_bigger_transaction_undoes_only_itself(db, registry, monkeypatch):
    """Wearing removes the unit from its stack BEFORE it finds out the replaced item has no room: inside the caller's transaction that half step must be undone on its own."""
    _stackable_wearable(registry)
    I.give(db, P1, "watch_steel", 1, "gift", "w")
    I.equip(db, P1, stack_of(db, P1, "watch_steel"))
    I.give(db, P1, "badge_pin", 2, "gift", "b")
    monkeypatch.setattr(C, "INV_MAX_STACKS", 1)
    with db.tx():
        I.give(db, P1, "badge_pin", 3, "gift", "more")                                   # an earlier step of the same transaction (tops the stack up to 5)
        with pytest.raises(GameError) as e:
            I.equip(db, P1, stack_of(db, P1, "badge_pin"))
        assert e.value.code == "inv_full"
        assert I.count(db, P1, "badge_pin") == 5 and I.worn(db, P1)["accessory"].item_id == "watch_steel"        # the failed wear left no half step
    assert I.count(db, P1, "badge_pin") == 5 and I.worn(db, P1)["accessory"].item_id == "watch_steel" and I.audit(db, P1) == []


def test_a_grant_is_undone_with_the_transaction_around_it(db):
    with pytest.raises(RuntimeError):
        with db.tx():
            I.give(db, P1, "old_key", 1, "gift", "a")
            raise RuntimeError("later step failed")
    assert I.total(db, P1) == 0 and log(db) == []
    assert I.give(db, P1, "old_key", 1, "gift", "a") == 1                               # the reference was not burned


def test_savepoint_outside_a_transaction_is_a_transaction(db):
    with pytest.raises(RuntimeError):
        with db.savepoint():
            db.x("INSERT INTO item_log(user_id,item_id,delta,source,ref,created_at) VALUES(?,?,?,?,?,?)", (P1, "old_key", 1, "x", None, NOW))
            raise RuntimeError("boom")
    assert log(db) == [] and db._depth == 0


# ============================================================================================== the books
def test_the_books_balance_after_a_long_mixed_sequence(db):
    import random
    rng = random.Random(21)
    ids = list(items.ITEMS)
    for n in range(400):
        op = rng.choice(["give", "give", "take", "equip", "unequip"])
        item = rng.choice(ids)
        try:
            if op == "give":
                I.give(db, P1, item, rng.randint(1, 30), "gift", f"r{n}")
            elif op == "take":
                I.take(db, P1, item, rng.randint(1, 10), "use", f"t{n}")
            elif op == "equip":
                es = [e for e in I.bag(db, P1) if items.can_equip(e.item)]
                if es:
                    I.equip(db, P1, rng.choice(es).stack_id)
            else:
                I.unequip(db, P1, rng.choice(list(items.SLOTS)))
        except GameError:
            pass
        if n % 40 == 0:
            assert I.audit(db, P1) == [], (n, I.audit(db, P1))
    assert I.audit(db, P1) == [] and all(q >= 1 for q in stacks(db, P1))
    assert all(len(set(w)) == len(w) for w in [list(I.worn(db, P1))])


def test_the_audit_notices_a_wrong_state(db):
    I.give(db, P1, "tshirt_white", 1, "gift", "a")
    db.x("INSERT INTO item_equipment(user_id,slot,item_id,acquired_at,source,equipped_at) VALUES(?,?,?,?,?,?)", (P1, "lower", "tshirt_white", NOW, "gift", NOW))   # a shirt in the trousers slot
    problems = I.audit(db, P1)
    assert any("cannot be worn there" in p for p in problems) and any("log adds up" in p for p in problems)
    I.give(db, P1, "sandwich", 5, "gift", "b")
    db.x("UPDATE item_inventory SET quantity=99 WHERE user_id=? AND item_id='sandwich'", (P1,))
    assert any("over the stack size" in p for p in I.audit(db, P1))


def test_pagination_clamps_and_never_returns_less_than_one_page():
    assert I.paginate([], 1) == ([], 1, 1) and I.paginate([], 9) == ([], 1, 1)
    seq = list(range(14))
    assert I.paginate(seq, 1, 6) == (seq[:6], 1, 3) and I.paginate(seq, 3, 6) == (seq[12:], 3, 3)
    assert I.paginate(seq, 99, 6)[1] == 3 and I.paginate(seq, 0, 6)[1] == 1 and I.paginate(seq, -4, 6)[1] == 1
    assert I.paginate(seq[:6], 1, 6)[2] == 1 and I.paginate(seq[:7], 1, 6)[2] == 2


# ============================================================================================== the migration 12 -> 13
OLD_GIFT_TABLE = """CREATE TABLE gift_codes(
  code TEXT PRIMARY KEY CHECK(length(code) >= 8),
  reward_type TEXT NOT NULL CHECK(reward_type IN ('stock_money','life_cash','char_xp')),
  amount INTEGER NOT NULL CHECK(amount > 0), max_uses INTEGER NOT NULL CHECK(max_uses > 0),
  used_count INTEGER NOT NULL DEFAULT 0 CHECK(used_count >= 0 AND used_count <= max_uses),
  created_by INTEGER NOT NULL, created_at INTEGER NOT NULL
)"""
OLD_GIFT_TRIGGERS = (
    """CREATE TRIGGER trg_gift_codes_update BEFORE UPDATE ON gift_codes
BEGIN
  SELECT RAISE(ABORT, 'gift code: only the use counter moves, and only up') WHERE NEW.code IS NOT OLD.code OR NEW.reward_type IS NOT OLD.reward_type OR NEW.amount IS NOT OLD.amount
                                                                              OR NEW.max_uses IS NOT OLD.max_uses OR NEW.created_by IS NOT OLD.created_by
                                                                              OR NEW.created_at IS NOT OLD.created_at OR NEW.used_count < OLD.used_count;
END""",
    "CREATE TRIGGER trg_gift_codes_nodelete BEFORE DELETE ON gift_codes BEGIN SELECT RAISE(ABORT, 'gift codes are kept'); END",
    """CREATE TRIGGER trg_gift_redeem_insert BEFORE INSERT ON gift_redemptions
BEGIN
  SELECT RAISE(ABORT, 'gift redemption: unknown code') WHERE NOT EXISTS(SELECT 1 FROM gift_codes WHERE code = NEW.code AND reward_type = NEW.reward_type AND amount = NEW.amount);
END""",
)
GIFT_ROWS = "SELECT code,reward_type,amount,max_uses,used_count,created_by,created_at FROM gift_codes ORDER BY rowid"


def make_v12(path):
    """A database as v0.20 left it: players, gift codes with their redemptions (and some other data), the OLD gift_codes definition and its old triggers, schema 12, no item tables."""
    d = Database(path)
    core.set_clock(lambda: NOW)
    for uid in (P1, P2, 885003):
        core.ensure_user(d, uid, None, f"U{uid}")
    codes = []
    for i, (reward, n, uses) in enumerate([("stock_money", 5, 3), ("life_cash", 7, 2), ("char_xp", 9, 4), ("stock_money", 1, 1)]):
        core.set_clock(lambda i=i: NOW + i)
        codes.append(gifts.create(d, 1, reward, gifts.to_units(reward, n), uses, frozenset({1}))["code"])
    core.set_clock(lambda: NOW + 50)
    gifts.redeem(d, P1, codes[0])
    gifts.redeem(d, P2, codes[0])
    gifts.redeem(d, P1, codes[3])
    for trg in ("trg_gift_codes_update", "trg_gift_codes_nodelete", "trg_gift_redeem_insert"):
        d.x(f"DROP TRIGGER {trg}")
    d.x(OLD_GIFT_TABLE.replace("gift_codes(", "gift_codes_old(", 1))
    d.x("INSERT INTO gift_codes_old(code,reward_type,amount,max_uses,used_count,created_by,created_at) SELECT code,reward_type,amount,max_uses,used_count,created_by,created_at FROM gift_codes ORDER BY rowid")
    d.x("DROP TABLE gift_codes")
    d.x("ALTER TABLE gift_codes_old RENAME TO gift_codes")
    for sql in OLD_GIFT_TRIGGERS:
        d.x(sql)
    for tbl in ("item_inventory", "item_equipment", "item_log"):
        d.x(f"DROP TABLE {tbl}")
    d.x("PRAGMA user_version=12")
    d.close()
    return codes


def schema_of(d, names):
    q = ",".join("?" * len(names))
    # (SQLite writes the name of a renamed table in double quotes: «CREATE TABLE "gift_codes"»; the definition is otherwise compared word for word)
    return {r["name"]: re.sub(r"\s+", " ", r["sql"].replace('"', "")).strip() for r in d.qa(f"SELECT name, sql FROM sqlite_master WHERE name IN ({q})", tuple(names))}


def test_the_migrations_to_13_and_14_keep_every_gift_code_and_redemption_and_gives_nobody_anything(tmp_path):
    path = str(tmp_path / "v12.db")
    codes = make_v12(path)
    raw = sqlite3.connect(path)
    assert raw.execute("PRAGMA user_version").fetchone()[0] == 12 and "'item'" not in raw.execute("SELECT sql FROM sqlite_master WHERE name='gift_codes'").fetchone()[0]
    codes_before = raw.execute(GIFT_ROWS).fetchall()
    reds_before = raw.execute("SELECT * FROM gift_redemptions ORDER BY id").fetchall()
    others = {t_: raw.execute(f"SELECT * FROM {t_} ORDER BY 1").fetchall() for t_ in ("users", "characters", "life_accounts", "life_ledger", "character_xp", "citizen_seq")}
    raw.close()
    assert len(codes_before) == 4 and len(reds_before) == 3
    d = Database(path)                                                                   # the upgrade
    assert d.q1("PRAGMA user_version")[0] == 14
    assert [tuple(r) for r in d.qa(GIFT_ROWS)] == codes_before                           # every code, in the order it was made, exactly as it was
    assert [tuple(r) for r in d.qa("SELECT * FROM gift_redemptions ORDER BY id")] == reds_before
    assert all(r["item_id"] is None for r in d.qa("SELECT item_id FROM gift_codes"))
    assert gifts.recent(d, 10)[0]["code"] == codes[3] and [r["code"] for r in gifts.recent(d, 10)] == codes[::-1]            # newest first, as before
    for t_, rows in others.items():
        assert [tuple(r) for r in d.qa(f"SELECT * FROM {t_} ORDER BY 1")] == rows, t_    # nothing else changed
    for tbl in ("item_inventory", "item_equipment", "item_log"):
        assert d.q1(f"SELECT COUNT(*) c FROM {tbl}")["c"] == 0, tbl                      # nobody was given any item
    d.close()
    assert Database(path).q1("PRAGMA user_version")[0] == 14                             # reopening is a no-op
    assert [tuple(r) for r in Database(path).qa(GIFT_ROWS)] == codes_before


def test_a_migrated_database_has_exactly_the_gift_tables_and_triggers_of_a_new_one(tmp_path):
    path = str(tmp_path / "v12.db")
    make_v12(path)
    migrated, fresh = Database(path), Database(":memory:")
    names = ("gift_codes", "gift_redemptions", "trg_gift_codes_update", "trg_gift_codes_nodelete", "trg_gift_redeem_insert", "trg_gift_redeem_update", "trg_gift_redeem_delete",
             "item_inventory", "item_equipment", "item_log", "uq_item_log_gain", "uq_item_log_loss", "ix_item_inventory_user", "trg_item_inv_update", "trg_item_log_update", "trg_item_log_delete")
    a, b = schema_of(migrated, names), schema_of(fresh, names)
    assert set(a) == set(names) == set(b)
    for n in names:
        assert a[n] == b[n], n


def test_the_old_rules_still_hold_after_the_rebuild_and_item_codes_work(tmp_path):
    path = str(tmp_path / "v12.db")
    codes = make_v12(path)
    d = Database(path)
    with pytest.raises(sqlite3.IntegrityError):
        d.x("UPDATE gift_codes SET amount=amount+1")                                    # a code's reward never changes
    with pytest.raises(sqlite3.IntegrityError):
        d.x("UPDATE gift_codes SET item_id='tshirt_white' WHERE code=?", (codes[0],))
    with pytest.raises(sqlite3.IntegrityError):
        d.x("DELETE FROM gift_codes")
    with pytest.raises(sqlite3.IntegrityError):
        d.x("INSERT INTO gift_redemptions(code,user_id,reward_type,amount,redeemed_at) VALUES('HM-NOPE-NOPE-NOPE',1,'life_cash',5,1)")
    with pytest.raises(sqlite3.IntegrityError):
        d.x("INSERT INTO gift_codes(code,reward_type,amount,max_uses,created_by,created_at) VALUES('HM-ITEM-ITEM-ITEM','item',1,1,1,1)")               # an item code names its item
    with pytest.raises(sqlite3.IntegrityError):
        d.x("INSERT INTO gift_codes(code,reward_type,amount,max_uses,created_by,created_at,item_id) VALUES('HM-XPXP-XPXP-XPXP','char_xp',1,1,1,1,'sandwich')")   # and no other does
    core.set_clock(lambda: NOW + 99)
    row = gifts.create(d, 1, "item", 2, 3, frozenset({1}), item_id="sandwich")
    assert gifts.redeem(d, P1, row["code"])["item"] == "sandwich" and I.count(d, P1, "sandwich") == 2
    assert gifts.redeem(d, P2, codes[1])["reward"] == "life_cash"                # an old code still pays its old reward


def test_a_failed_migration_changes_nothing_and_can_be_repeated(tmp_path, monkeypatch):
    from hypemarket import db as dbmod
    path = str(tmp_path / "v12.db")
    make_v12(path)
    raw = sqlite3.connect(path)
    before = raw.execute(GIFT_ROWS).fetchall()
    sql_before = raw.execute("SELECT name, sql FROM sqlite_master ORDER BY name").fetchall()
    raw.close()
    monkeypatch.setattr(dbmod, "_GIFT_TRIGGERS_V13", ("CREATE TRIGGER broken THIS IS NOT SQL",))
    with pytest.raises(sqlite3.Error):
        Database(path)
    raw = sqlite3.connect(path)
    assert raw.execute("PRAGMA user_version").fetchone()[0] == 12 and raw.execute(GIFT_ROWS).fetchall() == before          # the half-done rebuild was rolled back
    assert "'item'" not in raw.execute("SELECT sql FROM sqlite_master WHERE name='gift_codes'").fetchone()[0]
    assert {r[0] for r in raw.execute("SELECT name FROM sqlite_master WHERE type='trigger'")} >= {"trg_gift_codes_update", "trg_gift_codes_nodelete", "trg_gift_redeem_insert"}
    assert not raw.execute("SELECT 1 FROM sqlite_master WHERE name='gift_codes_v13'").fetchone()
    raw.close()
    monkeypatch.undo()
    d = Database(path)                                                                   # and the upgrade then works
    assert d.q1("PRAGMA user_version")[0] == 14 and [tuple(r) for r in d.qa(GIFT_ROWS)] == before
    assert sql_before and d.q1("SELECT COUNT(*) c FROM gift_codes")["c"] == 4


def test_a_database_from_before_the_gift_codes_migrates_through_every_step(tmp_path):
    """A v10 database (no gift tables yet): the schema creates them in their new form and the step 13 finds nothing to rebuild."""
    path = str(tmp_path / "v10.db")
    d = Database(path)
    core.set_clock(lambda: NOW)
    core.ensure_user(d, P1, None, "One")
    for tbl in ("gift_redemptions", "gift_codes", "intercity_trips", "character_identity"):
        for r in d.qa("SELECT name FROM sqlite_master WHERE type='trigger' AND tbl_name=?", (tbl,)):
            d.x(f"DROP TRIGGER {r['name']}")
        d.x(f"DROP TABLE {tbl}")
    for tbl in ("item_inventory", "item_equipment", "item_log"):
        d.x(f"DROP TABLE {tbl}")
    d.x("PRAGMA user_version=10")
    d.close()
    d = Database(path)
    assert d.q1("PRAGMA user_version")[0] == 14 and "'item'" in d.q1("SELECT sql FROM sqlite_master WHERE name='gift_codes'")["sql"]
    assert I.give(d, P1, "sandwich", 1, "gift", "r") == 1
