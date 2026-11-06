"""v0.211, part 4: stock and display, tailoring, customers and the accountant, staff and payroll, reputation, reports and style.

Covered: the warehouse and the display as two places with their capacities, the owner's instructions (price, target, refill, the game's buyback only when the owner switched it on and never above the game's
own value), a log that adds up; tailoring (quality is the weakest of four factors, no randomness, never five stars in a business, a database rule behind it, the real time, the materials used up, XP and
skill once, the production history on the pieces, a bot worker, a recipe that has no material yet); the economy has no loop that turns the game's buyback into profit; customers (near, not the staff,
one purchase however many presses, a changed price voids the quote, approval when there is no accountant, an accountant's limit, the checkout upgrade, everything atomic); contracts (terms that never
change, accept and decline, slots and percent caps, pay from recorded sales, wages reserved, closing pays them); reputation that cannot be farmed; reports; style and outfits. A real Telegram client
was not used."""
import threading

import pytest

import bizkit as K
import charkit
import test_life_jobs as LJ
from hypemarket import biz, brands, cash, charxp, config as C, core, estate, fashion, inventory, items, presence, rep, reports, skills, staff, stock, store, style, tailor, textile, upgrades, work
from hypemarket.core import GameError

layout = LJ.layout
layout2 = LJ.layout2
A, B, CUST, CUST2, EXTRA = K.OWNER, K.WORKER, K.CUSTOMER, K.CUST2, 51005
NIKE = {"q": 2, "b": "nike"}


def fabric(E, uid, q, n, tag=""):
    inventory.give(E.db, uid, "fabric", n, "test:fab", f"{uid}:{q}:{n}:{tag}:{E.now[0]}:{len(E.ledger(uid))}", {"q": q, "src": "cotton"}, E.now[0])


def piece(E, uid, item="tank_top", n=1, data=None, tag=""):
    inventory.give(E.db, uid, item, n, "test:piece", f"{uid}:{item}:{n}:{tag}:{E.now[0]}:{len(E.ledger(uid))}:{len(str(data))}", data, E.now[0])


def give_skill(E, uid, skill, level):
    skills.grant_work(E.db, uid, skill, skills.need(skill, level), "test:setup", f"{skill}:{level}:{uid}", E.now[0])


def shop(E, uid=A, fund=500, **kw):
    return K.make_branch(E, uid, mode="retail", fund=fund, **kw)


def workshop(E, uid=A, fund=8000, machines=3, cutting=True, **kw):
    """An industrial lot fitted for production, a nike branch on it, machines (and a cutting table) installed."""
    b = K.make_branch(E, uid, zone="industrial", mode="production", fund=fund, **kw)
    K.install(E, uid, b, "machines", machines)
    if cutting:
        K.install(E, uid, b, "cutting_table")
    return biz.get(E.db, b["id"])


def prop_of(E, b):
    return estate.get(E.db, b["property_id"])


def at_prop(E, uid, b):
    return K.teleport(E, uid, prop_of(E, b)["parcel_id"])


def stocked(E, b, n=10, price=300, data=NIKE, item="tank_top", target=5, refill=True, uid=A, display=True):
    piece(E, uid, item, n, data, tag=f"st{b['id']}")
    stock.deposit(E.db, uid, b["id"], item, data, n, E.now[0])
    stock.set_rule(E.db, uid, b["id"], item, data, price=price, target=target, refill=refill, now=E.now[0])
    if display:
        stock.move(E.db, uid, b["id"], item, data, min(n, target), stock.WH, E.now[0])
    return b


# ============================================================================================== the quality rule
@pytest.mark.parametrize("m,s,e,p,q,limit", [
    (5, 5, 4, 4, 4, "equipment"), (5, 5, 5, 4, 4, "property"), (3, 5, 4, 4, 3, "material"), (5, 2, 4, 4, 2, "skill"), (5, 5, 2, 4, 2, "equipment"),
    (1, 5, 4, 4, 1, "material"), (5, 5, 5, 5, 4, "property"), (4, 4, 4, 4, 4, "material"), (5, 1, 1, 1, 1, "skill"), (2, 2, 2, 2, 2, "material"),
])
def test_the_final_quality_is_the_weakest_factor_and_never_above_four(m, s, e, p, q, limit):
    got = tailor.final_quality(m, s, e, p)
    assert got == (q, limit)
    assert got == tailor.final_quality(m, s, e, p)                                                    # no randomness: the same every time
    assert got[0] <= C.COMMERCIAL_MAX_QUALITY == 4


def test_no_combination_of_factors_gives_five_stars_in_a_business():
    for m in range(1, 6):
        for s in range(1, 6):
            for e in range(1, 6):
                for p in range(1, 6):
                    assert tailor.final_quality(m, s, e, p)[0] <= 4


@pytest.mark.parametrize("level,q", [(0, 1), (149, 1), (150, 2), (399, 2), (400, 3), (699, 3), (700, 4), (899, 4), (900, 5), (1000, 5)])
def test_the_skill_allows_stars_by_threshold(level, q):
    assert tailor.skill_quality(level) == q


def test_skill_makes_pieces_faster_and_saves_a_little_material_within_limits():
    r = fashion.recipe_of("tank_top")
    s0, s1 = tailor.piece_seconds(r, 0, 0), tailor.piece_seconds(r, 1000, 0)
    assert s0 == r.seconds and s1 >= r.seconds * (10000 - C.TAILOR_SPEED_BPS_MAX) // 10000 and s1 < s0
    assert tailor.piece_seconds(r, 0, 3500) < s0 and tailor.piece_seconds(r, 1000, 3500) >= 60
    assert tailor.material_units(2, 10, 0) == 20 and 18 <= tailor.material_units(2, 10, 1000) < 20
    assert tailor.material_units(1, 7, 1000) == 7                                                      # never fewer than one unit a piece


# ============================================================================================== the game's buyback is never a profit loop
def test_selling_to_the_game_after_crafting_never_beats_selling_the_fabric():
    for pid, r in fashion.RECIPES.items():
        if not r.craftable:
            continue
        for q in range(1, 5):
            pieces = 20
            units = tailor.material_units(r.materials[0][1], pieces, 1000)                             # even the cheapest case: all the savings
            cost = units * C.FABRIC_GAME_CENTS[q]                                                      # the fabric of that quality sold to the game instead
            back = stock.buyback_price(pid, {"q": q}) * pieces
            assert back < cost, (pid, q, back, cost)
            assert stock.buyback_price(pid, {"q": q}) < stock.value_of(pid, {"q": q})
    assert stock.buyback_price("tank_top", None) == 0 and stock.buyback_price("fabric", {"q": 3}) < C.FABRIC_GAME_CENTS[3]


def test_values_follow_the_quality_and_an_impossible_one_has_none():
    vals = [fashion.base_value("tshirt", q) for q in range(1, 6)]
    assert vals == sorted(vals) and len(set(vals)) == 5 and all(v > 0 for v in vals)
    assert fashion.base_value("tank_top", 9) == 0 and fashion.base_value("tank_top", 0) == 0 and fashion.base_value("nothing", 3) == 0
    chain = fashion.recipe_of("chain")
    assert chain is not None and not chain.craftable and fashion.base_value("chain", 3) > 0           # an admin-gifted chain can still be priced; nobody can make one


# ============================================================================================== stock: warehouse and display
def test_a_new_branch_has_two_empty_places_with_capacities_from_the_property_and_upgrades(layout):
    E = K.env(layout)
    b = shop(E)
    prop = prop_of(E, b)
    assert stock.total(E.db, b["id"], stock.WH) == 0 and stock.total(E.db, b["id"], stock.DISP) == 0
    assert upgrades.warehouse_cap(E.db, b) == prop["warehouse_cap"] and stock.display_slots(E.db, b) == prop["display_cap"] // C.DISPLAY_SLOT_ITEMS
    assert prop["tier"] == "small" and stock.display_slots(E.db, b) == 2                                # «a small property has two display slots»
    biz.fund(E.db, A, b["id"], 3000, "f", E.now[0])
    K.install(E, A, b, "racks")
    assert stock.display_slots(E.db, biz.get(E.db, b["id"])) == prop["display_cap"] * 12000 // 10000 // C.DISPLAY_SLOT_ITEMS


def test_goods_in_the_warehouse_are_not_for_sale_until_they_are_moved(layout):
    E = K.env(layout)
    b = shop(E)
    stocked(E, b, 8, display=False)
    assert store.shelf(E.db, b["id"]) == [] and stock.qty(E.db, b["id"], stock.WH, "tank_top", NIKE) == 8
    stock.move(E.db, A, b["id"], "tank_top", NIKE, 3, stock.WH, E.now[0])
    assert store.shelf(E.db, b["id"]) == [{"item_id": "tank_top", "data": stock.key(NIKE), "qty": 3, "price": 300}]
    assert stock.qty(E.db, b["id"], stock.WH, "tank_top", NIKE) == 5 and stock.audit(E.db, b["id"]) == []


def test_only_goods_of_the_clothing_business_can_be_deposited_and_only_from_your_own_bag(layout):
    E = K.env(layout)
    b = shop(E)
    piece(E, A, "tank_top", 2, NIKE)
    for item, code in (("water", "stock_item"), ("nothing", "stock_item")):
        with pytest.raises(GameError) as e:
            stock.deposit(E.db, A, b["id"], item, None, 1, E.now[0])
        assert e.value.code == code
    with pytest.raises(GameError) as e:
        stock.deposit(E.db, A, b["id"], "tank_top", NIKE, 3, E.now[0])
    assert e.value.code == "inv_few" and inventory.count(E.db, A, "tank_top") == 2
    for bad in (0, -1, True, 1.5):
        with pytest.raises(GameError) as e:
            stock.deposit(E.db, A, b["id"], "tank_top", NIKE, bad, E.now[0])
        assert e.value.code == "bad_amount"
    assert stock.deposit(E.db, A, b["id"], "tank_top", NIKE, 2, E.now[0]) == 2 and inventory.count(E.db, A, "tank_top") == 0


def test_the_warehouse_and_the_display_have_capacity_limits(layout):
    E = K.env(layout)
    b = shop(E)
    cap = upgrades.warehouse_cap(E.db, b)
    piece(E, A, "tank_top", cap + 5, NIKE)
    with pytest.raises(GameError) as e:
        stock.deposit(E.db, A, b["id"], "tank_top", NIKE, cap + 1, E.now[0])
    assert e.value.code == "wh_full" and stock.total(E.db, b["id"], stock.WH) == 0
    stock.deposit(E.db, A, b["id"], "tank_top", NIKE, cap, E.now[0])
    assert stock.free_warehouse(E.db, b) == 0
    slots = stock.display_slots(E.db, b)
    with pytest.raises(GameError) as e:
        stock.move(E.db, A, b["id"], "tank_top", NIKE, slots * C.DISPLAY_SLOT_ITEMS + 1, stock.WH, E.now[0])
    assert e.value.code == "disp_full" and stock.total(E.db, b["id"], stock.DISP) == 0 and stock.qty(E.db, b["id"], stock.WH, "tank_top", NIKE) == cap
    stock.move(E.db, A, b["id"], "tank_top", NIKE, C.DISPLAY_SLOT_ITEMS + 1, stock.WH, E.now[0])        # one variant over a slot's size uses a second slot
    assert stock.slots_used(E.db, b["id"]) == 2
    assert stock.move(E.db, A, b["id"], "tank_top", NIKE, 5, stock.DISP, E.now[0]) == cap - C.DISPLAY_SLOT_ITEMS - 1 + 5


def test_a_third_variant_needs_a_third_slot(layout):
    E = K.env(layout)
    b = shop(E)
    for q in (1, 2, 3):
        piece(E, A, "tank_top", 1, {"q": q, "b": "nike"}, tag=str(q))
        stock.deposit(E.db, A, b["id"], "tank_top", {"q": q, "b": "nike"}, 1, E.now[0])
    stock.move(E.db, A, b["id"], "tank_top", {"q": 1, "b": "nike"}, 1, stock.WH, E.now[0])
    stock.move(E.db, A, b["id"], "tank_top", {"q": 2, "b": "nike"}, 1, stock.WH, E.now[0])
    with pytest.raises(GameError) as e:
        stock.move(E.db, A, b["id"], "tank_top", {"q": 3, "b": "nike"}, 1, stock.WH, E.now[0])
    assert e.value.code == "disp_full"


def test_taking_goods_out_of_a_branch_is_the_owners_alone(layout):
    E = K.env(layout)
    b = shop(E)
    stocked(E, b, 6)
    biz.register_perm_hook(lambda db, uid, br: {"stock", "produce", "approve", "ads", "spend", "upgrade", "hire"} if uid == B else set())
    try:
        with pytest.raises(GameError) as e:
            stock.withdraw(E.db, B, b["id"], stock.WH, "tank_top", NIKE, 1, E.now[0])
        assert e.value.code == "branch_denied"
    finally:
        biz._PERM_HOOKS[:] = [h for h in biz._PERM_HOOKS if h is staff.perm_hook]
    assert stock.withdraw(E.db, A, b["id"], stock.DISP, "tank_top", NIKE, 1, E.now[0]) == 4 and inventory.count_variant(E.db, A, "tank_top", NIKE) == 1
    with pytest.raises(GameError) as e:
        stock.withdraw(E.db, A, b["id"], stock.WH, "tank_top", NIKE, 99, E.now[0])
    assert e.value.code == "stock_low"
    assert stock.audit(E.db, b["id"]) == []


def test_an_operation_with_the_same_reference_is_done_once(layout):
    E = K.env(layout)
    b = shop(E)
    piece(E, A, "tank_top", 5, NIKE)
    assert stock.deposit(E.db, A, b["id"], "tank_top", NIKE, 2, E.now[0], ref="r1") == 2
    assert stock.deposit(E.db, A, b["id"], "tank_top", NIKE, 2, E.now[0], ref="r1") == 2
    assert stock.qty(E.db, b["id"], stock.WH, "tank_top", NIKE) == 2 and inventory.count(E.db, A, "tank_top") == 3
    assert stock.audit(E.db, b["id"]) == []


def test_the_instruction_validates_its_price_and_who_may_switch_the_buyback(layout):
    E = K.env(layout)
    b = shop(E)
    piece(E, A, "tank_top", 4, NIKE)
    stock.deposit(E.db, A, b["id"], "tank_top", NIKE, 4, E.now[0])
    ceiling = stock.max_price("tank_top", NIKE)
    assert ceiling == fashion.base_value("tank_top", 2) * C.PRICE_MAX_MULT
    for bad, code in ((0, "bad_amount"), (-5, "bad_amount"), (True, "bad_amount"), (1.5, "bad_amount"), (ceiling + 1, "price_range")):
        with pytest.raises(GameError) as e:
            stock.set_rule(E.db, A, b["id"], "tank_top", NIKE, price=bad, now=E.now[0])
        assert e.value.code == code, bad
    r = stock.set_rule(E.db, A, b["id"], "tank_top", NIKE, price=ceiling, target=3, keep=1, refill=True, now=E.now[0])
    assert (r["price"], r["target"], r["keep"], r["refill"], r["buyback"]) == (ceiling, 3, 1, 1, 0)
    r = stock.set_rule(E.db, A, b["id"], "tank_top", NIKE, price=None, now=E.now[0])
    assert r["price"] is None and r["target"] == 3                                                     # only the named fields change
    with pytest.raises(GameError) as e:
        stock.set_rule(E.db, A, b["id"], "tank_top", NIKE, target=-1, now=E.now[0])
    assert e.value.code == "bad_amount"
    with pytest.raises(GameError) as e:                                                                # the game does not buy a piece that has no value for it
        stock.set_rule(E.db, A, b["id"], "tank_top", None, buyback=True, now=E.now[0])
    assert e.value.code == "buyback_none"
    biz.register_perm_hook(lambda db, uid, br: {"stock"} if uid == B else set())
    try:
        assert stock.set_rule(E.db, B, b["id"], "tank_top", NIKE, price=500, now=E.now[0])["price"] == 500
        with pytest.raises(GameError) as e:
            stock.set_rule(E.db, B, b["id"], "tank_top", NIKE, buyback=True, now=E.now[0])
        assert e.value.code == "branch_denied"
    finally:
        biz._PERM_HOOKS[:] = [h for h in biz._PERM_HOOKS if h is staff.perm_hook]
    assert stock.set_rule(E.db, A, b["id"], "tank_top", NIKE, buyback=True, now=E.now[0])["buyback"] == 1
    biz.register_perm_hook(lambda db, uid, br: {"stock"} if uid == B else set())
    try:
        with pytest.raises(GameError) as e:
            stock.set_rule(E.db, B, b["id"], "tank_top", NIKE, buyback=False, now=E.now[0])
        assert e.value.code == "branch_denied"                                                         # nor can an employee switch it off behind the owner's back
    finally:
        biz._PERM_HOOKS[:] = [h for h in biz._PERM_HOOKS if h is staff.perm_hook]


def test_restock_refills_the_display_from_the_warehouse_up_to_the_target(layout):
    E = K.env(layout)
    b = shop(E)
    stocked(E, b, 12, target=6, display=False)
    out = stock.restock(E.db, b["id"], E.now[0], actor=A)
    assert out == {"moved": 6, "sold": 0, "cents": 0}
    assert stock.qty(E.db, b["id"], stock.DISP, "tank_top", NIKE) == 6 and stock.qty(E.db, b["id"], stock.WH, "tank_top", NIKE) == 6
    assert stock.restock(E.db, b["id"], E.now[0], actor=A) == {"moved": 0, "sold": 0, "cents": 0}      # a second run changes nothing
    store_sell(E, b, 2)
    assert stock.restock(E.db, b["id"], E.now[0] + 1, actor=A)["moved"] == 2
    assert stock.qty(E.db, b["id"], stock.DISP, "tank_top", NIKE) == 6 and stock.audit(E.db, b["id"]) == []


def store_sell(E, b, n):
    """Take `n` pieces off the display as a sale would (a direct stock removal: the sale flow itself is tested below)."""
    stock.remove(E.db, b["id"], stock.DISP, "tank_top", NIKE, n, "sale", f"t{E.now[0]}{n}", now=E.now[0])


def test_a_rule_without_refill_never_moves_anything_by_itself(layout):
    E = K.env(layout)
    b = shop(E)
    stocked(E, b, 6, target=5, refill=False, display=False)
    assert stock.restock(E.db, b["id"], E.now[0]) == {"moved": 0, "sold": 0, "cents": 0} and stock.total(E.db, b["id"], stock.DISP) == 0


def test_the_game_buys_surplus_only_when_the_owner_switched_it_on(layout):
    E = K.env(layout)
    b = shop(E)
    stocked(E, b, 12, target=4, display=False)
    stock.set_rule(E.db, A, b["id"], "tank_top", NIKE, keep=2, now=E.now[0])
    till = biz.get(E.db, b["id"])["till"]
    out = stock.restock(E.db, b["id"], E.now[0], actor=A)
    assert out["sold"] == 0 and biz.get(E.db, b["id"])["till"] == till and stock.qty(E.db, b["id"], stock.WH, "tank_top", NIKE) == 8   # never without the switch
    stock.set_rule(E.db, A, b["id"], "tank_top", NIKE, buyback=True, now=E.now[0])
    out = stock.restock(E.db, b["id"], E.now[0] + 1, actor=A)
    price = stock.buyback_price("tank_top", NIKE)
    assert out == {"moved": 0, "sold": 6, "cents": 6 * price}                                          # the warehouse beyond `keep`, the display is at its target
    assert stock.qty(E.db, b["id"], stock.WH, "tank_top", NIKE) == 2 and stock.qty(E.db, b["id"], stock.DISP, "tank_top", NIKE) == 4
    assert biz.get(E.db, b["id"])["till"] == till + 6 * price and biz.audit_till(E.db, b["id"]) == [] and stock.audit(E.db, b["id"]) == []
    assert price < stock.value_of("tank_top", NIKE) and price > 0
    row = E.db.q1("SELECT kind, qty, cents FROM stock_log WHERE kind='buyback'")
    assert (row["qty"], row["cents"]) == (-6, 6 * price)                                              # logged with its money


def test_the_buyback_waits_for_the_display_to_be_full(layout):
    E = K.env(layout)
    b = shop(E)
    stocked(E, b, 3, target=10, display=False)
    stock.set_rule(E.db, A, b["id"], "tank_top", NIKE, keep=0, buyback=True, refill=False, now=E.now[0])
    assert stock.restock(E.db, b["id"], E.now[0])["sold"] == 0 and stock.qty(E.db, b["id"], stock.WH, "tank_top", NIKE) == 3


def test_a_retail_less_branch_runs_no_restock(layout):
    E = K.env(layout)
    b = workshop(E)
    assert stock.restock(E.db, b["id"], E.now[0]) == {"moved": 0, "sold": 0, "cents": 0}


def test_a_warehouse_can_send_pieces_to_another_branch_of_the_same_owner_in_the_same_city(layout):
    E = K.env(layout)
    w = workshop(E)
    s = shop(E, skip=(w["property_id"],))
    piece(E, A, "tank_top", 5, NIKE)
    stock.deposit(E.db, A, w["id"], "tank_top", NIKE, 5, E.now[0])
    assert stock.transfer(E.db, A, w["id"], s["id"], "tank_top", NIKE, 3, E.now[0]) == 3
    assert stock.qty(E.db, w["id"], stock.WH, "tank_top", NIKE) == 2 and stock.audit(E.db, w["id"]) == [] and stock.audit(E.db, s["id"]) == []
    for to, code in ((w["id"], "transfer_far"), (99999, "branch_gone")):
        with pytest.raises(GameError) as e:
            stock.transfer(E.db, A, w["id"], to, "tank_top", NIKE, 1, E.now[0])
        assert e.value.code == code
    with pytest.raises(GameError) as e:
        stock.transfer(E.db, B, w["id"], s["id"], "tank_top", NIKE, 1, E.now[0])
    assert e.value.code == "branch_denied"
    with pytest.raises(GameError) as e:
        stock.transfer(E.db, A, w["id"], s["id"], "tank_top", NIKE, 3, E.now[0])
    assert e.value.code == "stock_low"


def test_closing_a_branch_gives_everything_back_to_the_owners_bag(layout):
    E = K.env(layout)
    b = shop(E)
    stocked(E, b, 8, target=3)
    biz.close_branch(E.db, A, b["id"], E.now[0] + 1)
    assert inventory.count_variant(E.db, A, "tank_top", NIKE) == 8 and stock.total(E.db, b["id"], stock.WH) == 0 and stock.total(E.db, b["id"], stock.DISP) == 0
    assert stock.audit(E.db, b["id"]) == []


def test_a_full_bag_stops_a_branch_from_closing_and_nothing_is_lost(layout, monkeypatch):
    E = K.env(layout)
    b = shop(E)
    stocked(E, b, 4)
    monkeypatch.setattr(C, "INV_MAX_STACKS", 0)
    with pytest.raises(GameError) as e:
        biz.close_branch(E.db, A, b["id"], E.now[0] + 1)
    assert e.value.code == "inv_full"
    assert biz.get(E.db, b["id"])["state"] == "open" and stock.total(E.db, b["id"], stock.WH) + stock.total(E.db, b["id"], stock.DISP) == 4


def test_a_mode_cannot_be_converted_while_stock_or_orders_are_in_the_way(layout):
    E = K.env(layout)
    b = shop(E)
    stocked(E, b, 2)
    with pytest.raises(GameError) as e:
        estate.set_mode(E.db, A, b["property_id"], "both", E.now[0] + 1)
    assert e.value.code == "mode_busy" and estate.get(E.db, b["property_id"])["mode"] == "retail"


# ============================================================================================== tailoring
def test_the_plan_names_what_is_in_the_way(layout):
    E = K.env(layout)
    w = workshop(E, machines=0, cutting=False)
    pl = tailor.plan(E.db, w, "tank_top", 2, 3, A)
    assert not pl["ok"] and set(pl["problems"]) == {"machine", "fabric"}
    pl = tailor.plan(E.db, w, "trousers", 2, 3, A)
    assert {"skill", "equipment", "machine"} <= set(pl["problems"]) and pl["missing"] == ["cutting_table"]
    pl = tailor.plan(E.db, w, "chain", 1, 3, A)
    assert "material_kind" in pl["problems"]
    for bad in (0, 21, -1, True, 1.5, None):
        assert "pieces" in tailor.plan(E.db, w, "tank_top", bad, 3, A)["problems"]
    assert "recipe" in tailor.plan(E.db, w, "spaceship", 1, 3, A)["problems"]
    assert "fabric" in tailor.plan(E.db, w, "tank_top", 1, 9, A)["problems"]
    s = shop(E, skip=(w["property_id"],))
    assert "mode" in tailor.plan(E.db, s, "tank_top", 1, 3, A)["problems"]


def stock_fabric(E, b, q, n):
    fabric(E, A, q, n, tag=f"sf{b['id']}{q}")
    stock.deposit(E.db, A, b["id"], "fabric", {"q": q, "src": "cotton"}, n, E.now[0])


def test_an_order_takes_the_fabric_at_the_start_and_the_time_is_real(layout):
    E = K.env(layout)
    w = workshop(E)
    stock_fabric(E, w, 4, 30)
    at_prop(E, A, w)
    o = tailor.start(E.db, E.city, A, w["id"], "tank_top", 5, 4, E.now[0])
    pl_units = tailor.material_units(2, 5, 0)
    assert pl_units == 10 and stock.qty(E.db, w["id"], stock.WH, "fabric", {"q": 4, "src": "cotton"}) == 20
    r = fashion.recipe_of("tank_top")
    per = tailor.piece_seconds(r, 0, 3500)
    assert (o["state"], o["pieces"], o["seconds"], o["due_at"], o["worker_id"], o["bot"], o["brand"]) == ("working", 5, 5 * per, E.now[0] + 5 * per, A, 0, "nike")
    assert o["quality"] == 4 and '"limit":"skill"' in o["factors"] or o["quality"] == 1
    t = work.current(E.db, A)
    assert t["job"] == "tailor" and t["due_at"] == o["due_at"] and work.data_of(t)["order"] == o["id"]
    with pytest.raises(GameError) as e:
        tailor.claim(E.db, A, o["id"], E.now[0] + 5 * per - 1)
    assert e.value.code == "job_notready" and stock.total(E.db, w["id"], stock.WH) == 20
    assert presence.start_trip(E.db, E.city, A, E.city.mcd_id, E.now[0]).status == "shift"           # the worker is held at the workshop


def test_a_beginner_with_good_fabric_and_a_good_machine_still_makes_one_star(layout):
    E = K.env(layout)
    w = workshop(E)
    stock_fabric(E, w, 4, 12)
    at_prop(E, A, w)
    o = tailor.start(E.db, E.city, A, w["id"], "tank_top", 2, 4, E.now[0])
    assert o["quality"] == 1 and '"limit":"skill"' in o["factors"]


def test_the_finished_pieces_land_in_the_warehouse_with_their_data_and_history(layout):
    E = K.env(layout)
    w = workshop(E)
    stock_fabric(E, w, 3, 12)
    at_prop(E, A, w)
    o = tailor.start(E.db, E.city, A, w["id"], "tank_top", 4, 3, E.now[0])
    E.now[0] = o["due_at"]
    xp0 = charxp.get(E.db, A)
    out = tailor.claim(E.db, A, o["id"], E.now[0])
    assert out["pieces"] == 4 and out["quality"] == 1
    data = {"q": 1, "b": "nike", "ph": o["id"]}
    assert stock.qty(E.db, w["id"], stock.WH, "tank_top", data) == 4
    assert charxp.get(E.db, A) == xp0 + out["xp"] and out["xp"] >= 1
    assert skills.work_of(E.db, A, "tailoring") == skills.milli(o["seconds"])
    done = tailor.get(E.db, o["id"])
    assert done["state"] == "done" and done["finished_at"] == E.now[0] and tailor.history_of(E.db, o["id"])["id"] == o["id"]
    lines = tailor.history_lines(E.db, "fa", data)
    assert lines and "NIKE" in lines[1] and "⭐☆☆☆☆" in lines[1] and f"#{o['id']}" in lines[0]
    assert tailor.history_lines(E.db, "fa", {"q": 1}) == [] and tailor.history_lines(E.db, "en", None) == []
    for again in (lambda: tailor.claim(E.db, A, o["id"], E.now[0] + 9), ):
        with pytest.raises(GameError) as e:
            again()
        assert e.value.code == "job_claimed"
    assert stock.qty(E.db, w["id"], stock.WH, "tank_top", data) == 4 and skills.work_of(E.db, A, "tailoring") == skills.milli(o["seconds"])
    assert work.current(E.db, A) is None and stock.audit(E.db, w["id"]) == []


def test_the_history_stays_with_the_piece_when_it_is_withdrawn_and_sold(layout):
    E = K.env(layout)
    w = workshop(E)
    stock_fabric(E, w, 3, 6)
    at_prop(E, A, w)
    o = tailor.start(E.db, E.city, A, w["id"], "tank_top", 2, 3, E.now[0])
    E.now[0] = o["due_at"]
    tailor.claim(E.db, A, o["id"], E.now[0])
    data = {"q": 1, "b": "nike", "ph": o["id"]}
    stock.withdraw(E.db, A, w["id"], stock.WH, "tank_top", data, 1, E.now[0])
    e = inventory.entry(E.db, A, "tank_top")
    assert items.parse(e.data)["ph"] == o["id"] and tailor.history_lines(E.db, "fa", e.data)


def test_the_worker_must_stand_at_the_property_and_have_no_other_job(layout):
    E = K.env(layout)
    w = workshop(E)
    stock_fabric(E, w, 3, 6)
    K.teleport(E, A, E.city.mcd_id)
    with pytest.raises(GameError) as e:
        tailor.start(E.db, E.city, A, w["id"], "tank_top", 1, 3, E.now[0])
    assert e.value.code == "job_far" and stock.total(E.db, w["id"], stock.WH) == 6
    work.mcd_start(E.db, E.city, A, E.now[0])
    at = K.teleport(E, A, prop_of(E, w)["parcel_id"])
    with pytest.raises(GameError) as e:
        tailor.start(E.db, E.city, A, w["id"], "tank_top", 1, 3, E.now[0])
    assert e.value.code == "job_busy"


def test_only_one_order_works_in_a_branch_at_a_time(layout):
    E = K.env(layout)
    w = workshop(E)
    stock_fabric(E, w, 3, 20)
    at_prop(E, A, w)
    tailor.start(E.db, E.city, A, w["id"], "tank_top", 1, 3, E.now[0])
    with pytest.raises(GameError) as e:
        tailor.start(E.db, E.city, A, w["id"], "tank_top", 1, 3, E.now[0])
    assert e.value.code == "prod_busy" and stock.total(E.db, w["id"], stock.WH) == 18


def test_recipes_that_need_skill_equipment_or_a_material_nobody_makes_are_refused(layout):
    E = K.env(layout)
    w = workshop(E, cutting=False)
    stock_fabric(E, w, 5, 40)
    at_prop(E, A, w)
    cases = (("shirt", "recipe_locked"), ("trousers", "recipe_locked"), ("chain", "recipe_materials"), ("spaceship", "recipe_none"))
    for product, code in cases:
        with pytest.raises(GameError) as e:
            tailor.start(E.db, E.city, A, w["id"], product, 1, 5, E.now[0])
        assert e.value.code == code, product
    give_skill(E, A, "tailoring", 450)
    with pytest.raises(GameError) as e:
        tailor.start(E.db, E.city, A, w["id"], "trousers", 1, 5, E.now[0])
    assert e.value.code == "recipe_equipment"                                                          # the skill is there, the cutting table is not
    assert stock.total(E.db, w["id"], stock.WH) == 40 and work.current(E.db, A) is None
    biz.fund(E.db, A, w["id"], 1000, "more", E.now[0])
    K.install(E, A, w, "cutting_table")
    o = tailor.start(E.db, E.city, A, w["id"], "trousers", 1, 5, E.now[0])
    assert o["quality"] == 3 and '"limit":"skill"' in o["factors"]


def test_a_better_machine_and_skill_raise_the_quality_up_to_the_property_limit(layout):
    E = K.env(layout)
    w = workshop(E, machines=3)
    give_skill(E, A, "tailoring", 950)
    stock_fabric(E, w, 5, 20)
    at_prop(E, A, w)
    prop = prop_of(E, w)
    assert prop["max_quality"] == 4
    o = tailor.start(E.db, E.city, A, w["id"], "tank_top", 1, 5, E.now[0])
    assert o["quality"] == 4                                                                           # fabric 5, skill 5, machine 4, property 4: the best a business can make
    assert tailor.final_quality(5, 5, 4, 4) == (4, "equipment")


def test_a_small_property_limits_the_quality_whatever_the_rest(layout):
    E = K.env(layout)
    b = K.make_branch(E, A, mode="both", fund=5000)
    K.install(E, A, b, "machines", 3)
    give_skill(E, A, "tailoring", 950)
    stock_fabric(E, b, 5, 20)
    at_prop(E, A, b)
    o = tailor.start(E.db, E.city, A, b["id"], "tank_top", 1, 5, E.now[0])
    assert prop_of(E, b)["max_quality"] == 2 and o["quality"] == 2 and '"limit":"property"' in o["factors"]


def test_the_database_itself_refuses_a_quality_the_property_cannot_make(layout):
    E = K.env(layout)
    b = K.make_branch(E, A, mode="both", fund=100)
    for q in (3, 4, 5):
        with pytest.raises(Exception, match="highest quality"):
            E.db.x("INSERT INTO prod_orders(branch_id,property_id,world_id,brand,product,pieces,worker_id,bot,requested_by,state,quality,factors,materials,seconds,started_at,due_at) "
                   "VALUES(?,?,?,?,?,?,NULL,1,?,'working',?,'{}','[]',60,0,60)", (b["id"], b["property_id"], E.city.id, "nike", "tank_top", 1, A, q))
    E.db.x("INSERT INTO prod_orders(branch_id,property_id,world_id,brand,product,pieces,worker_id,bot,requested_by,state,quality,factors,materials,seconds,started_at,due_at) "
           "VALUES(?,?,?,?,?,?,NULL,1,?,'working',?,'{}','[]',60,0,60)", (b["id"], b["property_id"], E.city.id, "nike", "tank_top", 1, A, 2))


def test_a_finished_order_and_its_materials_can_never_be_rewritten(layout):
    E = K.env(layout)
    w = workshop(E)
    stock_fabric(E, w, 3, 6)
    at_prop(E, A, w)
    o = tailor.start(E.db, E.city, A, w["id"], "tank_top", 1, 3, E.now[0])
    for col, val in (("quality", 2), ("pieces", 9), ("brand", "givenchy"), ("product", "cap"), ("worker_id", B), ("seconds", 1), ("materials", "[]")):
        with pytest.raises(Exception):
            E.db.x(f"UPDATE prod_orders SET {col}=? WHERE id=?", (val, o["id"]))
    E.now[0] = o["due_at"]
    tailor.claim(E.db, A, o["id"], E.now[0])
    for sql in ("UPDATE prod_orders SET state='working'", "UPDATE prod_orders SET finished_at=1", "DELETE FROM prod_orders"):
        with pytest.raises(Exception):
            E.db.x(sql)


def test_cancelling_an_order_forfeits_the_materials_and_frees_the_worker(layout):
    E = K.env(layout)
    w = workshop(E)
    stock_fabric(E, w, 3, 6)
    at_prop(E, A, w)
    o = tailor.start(E.db, E.city, A, w["id"], "tank_top", 2, 3, E.now[0])
    assert tailor.cancel_order(E.db, A, o["id"], E.now[0] + 5) is True and tailor.cancel_order(E.db, A, o["id"], E.now[0] + 6) is False
    assert tailor.get(E.db, o["id"])["state"] == "cancelled" and work.current(E.db, A) is None and stock.total(E.db, w["id"], stock.WH) == 2
    assert skills.work_of(E.db, A, "tailoring") == 0
    with pytest.raises(GameError) as e:
        tailor.claim(E.db, A, o["id"], E.now[0] + 99999)
    assert e.value.code == "job_stale"


def test_a_bot_production_worker_makes_pieces_without_a_player_skill_or_xp(layout):
    E = K.env(layout)
    w = workshop(E)
    stock_fabric(E, w, 3, 10)
    with pytest.raises(GameError) as e:
        tailor.start(E.db, E.city, A, w["id"], "tank_top", 2, 3, E.now[0], bot=True)
    assert e.value.code == "prod_bot"
    staff.hire_bot(E.db, A, w["id"], "production", E.now[0])
    o = tailor.start(E.db, E.city, A, w["id"], "tank_top", 3, 3, E.now[0], bot=True)                   # the owner is not at the workshop: the bot works
    assert o["bot"] == 1 and o["worker_id"] is None and work.current(E.db, A) is None
    assert o["quality"] == tailor.final_quality(3, tailor.skill_quality(C.BOT_WORKER_SKILL), 4, 4)[0]
    assert tailor.collect_due(E.db, w["id"], o["due_at"] - 1) == 0
    xp0, sk0 = charxp.get(E.db, A), skills.work_of(E.db, A, "tailoring")
    assert tailor.collect_due(E.db, w["id"], o["due_at"]) == 1 and tailor.collect_due(E.db, w["id"], o["due_at"] + 1) == 0
    assert stock.qty(E.db, w["id"], stock.WH, "tank_top", {"q": o["quality"], "b": "nike", "ph": o["id"]}) == 3
    assert charxp.get(E.db, A) == xp0 and skills.work_of(E.db, A, "tailoring") == sk0
    assert core.get_user(E.db, A)["bot"] == 0


def test_the_same_inputs_always_give_the_same_order(layout):
    qs = []
    for _ in range(3):
        E = K.env(layout)
        w = workshop(E)
        give_skill(E, A, "tailoring", 420)
        stock_fabric(E, w, 4, 10)
        at_prop(E, A, w)
        o = tailor.start(E.db, E.city, A, w["id"], "tshirt", 3, 4, E.now[0])
        qs.append((o["quality"], o["seconds"], o["factors"], o["materials"]))
    assert qs[0] == qs[1] == qs[2]


def test_a_manager_with_produce_can_order_but_a_stranger_cannot(layout):
    E = K.env(layout)
    w = workshop(E)
    stock_fabric(E, w, 3, 10)
    at_prop(E, B, w)
    with pytest.raises(GameError) as e:
        tailor.start(E.db, E.city, B, w["id"], "tank_top", 1, 3, E.now[0])
    assert e.value.code == "branch_denied"
    c = staff.offer(E.db, A, w["id"], B, "production", "fixed", 100, None, E.now[0])
    staff.accept(E.db, B, c["id"], E.now[0])
    o = tailor.start(E.db, E.city, B, w["id"], "tank_top", 1, 3, E.now[0])
    assert o["worker_id"] == B and o["requested_by"] == B
    E.now[0] = o["due_at"]
    out = tailor.claim(E.db, B, o["id"], E.now[0])
    assert out["xp"] >= 1 and skills.work_of(E.db, B, "tailoring") > 0 and skills.work_of(E.db, A, "tailoring") == 0


def test_closing_is_refused_while_an_order_is_working(layout):
    E = K.env(layout)
    w = workshop(E)
    stock_fabric(E, w, 3, 6)
    at_prop(E, A, w)
    o = tailor.start(E.db, E.city, A, w["id"], "tank_top", 1, 3, E.now[0])
    with pytest.raises(GameError) as e:
        biz.close_branch(E.db, A, w["id"], E.now[0])
    assert e.value.code == "close_busy" and biz.get(E.db, w["id"])["state"] == "open"
    with pytest.raises(GameError) as e:
        estate.set_mode(E.db, A, w["property_id"], "production", E.now[0] + 1)
    assert e.value.code in ("mode_same", "mode_bad")


def test_the_whole_cycle_cotton_to_fabric_to_clothes_to_the_shop_to_a_customer(layout):
    """The cycle of the update, end to end: a player's cotton, an ad, a workshop, a delivery to a shop, a customer's purchase."""
    E = K.env(layout)
    # the producer's cotton
    at = K.teleport(E, B, textile.site_id(E.city))
    t, _ = textile.start(E.db, E.city, B, 1, E.now[0])
    E.now[0] += C.COTTON_GRADES[1][1]
    textile.claim(E.db, B, t["id"], E.now[0])
    got = textile.fabric_by_quality(E.db, B)[1]
    # the owner's workshop and shop
    w = workshop(E)
    s = shop(E, skip=(w["property_id"],))
    ad = market.create(E.db, A, w["id"], 1, 80, 10, C.AD_TTL_CHOICES[1], E.now[0])
    res = market.fill(E.db, B, ad["id"], 1, got, E.now[0])
    assert res["qty"] == got and stock.qty(E.db, w["id"], stock.WH, "fabric", {"q": 1, "src": "cotton"}) == got
    at_prop(E, A, w)
    o = tailor.start(E.db, E.city, A, w["id"], "tank_top", 1, 1, E.now[0])
    E.now[0] = o["due_at"]
    tailor.claim(E.db, A, o["id"], E.now[0])
    data = {"q": 1, "b": "nike", "ph": o["id"]}
    stock.transfer(E.db, A, w["id"], s["id"], "tank_top", data, 1, E.now[0])
    stock.set_rule(E.db, A, s["id"], "tank_top", data, price=250, target=1, refill=True, now=E.now[0])
    stock.move(E.db, A, s["id"], "tank_top", data, 1, stock.WH, E.now[0])
    staff.hire_bot(E.db, A, s["id"], "accountant", E.now[0])
    K.teleport(E, CUST, prop_of(E, s)["parcel_id"])
    q = store.quote(E.db, E.city, K.stand(E, CUST), CUST, s["id"], "tank_top", data, 1, E.now[0])
    out = store.confirm(E.db, E.city, K.stand(E, CUST), CUST, q["id"], E.now[0])
    assert out["state"] == "done" and inventory.count_variant(E.db, CUST, "tank_top", data) == 1
    assert biz.get(E.db, s["id"])["till"] == 500_00 + 250 and rep.score(E.db, s["id"]) >= 1
    assert items.parse(inventory.entry(E.db, CUST, "tank_top").data)["ph"] == o["id"]


from hypemarket import market  # noqa: E402  (used by the cycle test above)


# ============================================================================================== customers and sales
def open_shop(E, accountant="bot", n=10, price=300, target=5, **kw):
    b = shop(E, **kw)
    stocked(E, b, n, price=price, target=target)
    if accountant == "bot":
        staff.hire_bot(E.db, A, b["id"], "accountant", E.now[0])
    return b


def buyer_at(E, uid, b):
    return K.teleport(E, uid, prop_of(E, b)["parcel_id"])


def buy_one(E, uid, b, n=1, now=None, data=NIKE):
    st = buyer_at(E, uid, b)
    q = store.quote(E.db, E.city, st, uid, b["id"], "tank_top", data, n, now or E.now[0])
    return q, store.confirm(E.db, E.city, st, uid, q["id"], now or E.now[0])


def test_a_customer_far_from_the_shop_sees_and_buys_nothing(layout):
    E = K.env(layout)
    b = open_shop(E)
    K.teleport(E, CUST, E.city.mcd_id)
    for _ in (0, 1):
        with pytest.raises(GameError) as e:
            store.quote(E.db, E.city, K.stand(E, CUST), CUST, b["id"], "tank_top", NIKE, 1, E.now[0])
        assert e.value.code == "store_far"
    assert E.db.q1("SELECT COUNT(*) c FROM store_orders")["c"] == 0


def test_a_purchase_moves_the_item_the_money_the_ledger_and_the_stock_together(layout):
    E = K.env(layout)
    b = open_shop(E)
    bal, till = cash.balance(E.db, CUST), biz.get(E.db, b["id"])["till"]
    q, out = buy_one(E, CUST, b, 2)
    assert out["state"] == "done" and out["how"] == "accountant" and out["balance"] == bal - 600
    assert inventory.count_variant(E.db, CUST, "tank_top", NIKE) == 2 and cash.balance(E.db, CUST) == bal - 600
    assert biz.get(E.db, b["id"])["till"] == till + 600 and biz.audit_till(E.db, b["id"]) == []
    assert stock.qty(E.db, b["id"], stock.DISP, "tank_top", NIKE) == 3 and stock.qty(E.db, b["id"], stock.WH, "tank_top", NIKE) == 5 and stock.audit(E.db, b["id"]) == []   # no warehouse worker: nobody refills
    o = store.get(E.db, q["id"])
    assert (o["state"], o["settled_by"], o["done_at"], o["total_cents"]) == ("done", "accountant", E.now[0], 600)
    assert E.ledger(CUST, ("store_buy",)) == [("store_buy", f"o{q['id']}", -600)]


def test_a_warehouse_worker_refills_the_display_after_a_sale(layout):
    E = K.env(layout)
    b = open_shop(E)
    staff.hire_bot(E.db, A, b["id"], "warehouse", E.now[0])
    buy_one(E, CUST, b, 2)
    assert stock.qty(E.db, b["id"], stock.DISP, "tank_top", NIKE) == 5 and stock.qty(E.db, b["id"], stock.WH, "tank_top", NIKE) == 3 and stock.audit(E.db, b["id"]) == []


def test_pressing_confirm_again_never_buys_twice_or_charges_twice(layout):
    E = K.env(layout)
    b = open_shop(E)
    st = buyer_at(E, CUST, b)
    q = store.quote(E.db, E.city, st, CUST, b["id"], "tank_top", NIKE, 1, E.now[0])
    store.confirm(E.db, E.city, st, CUST, q["id"], E.now[0])
    bal = cash.balance(E.db, CUST)
    for _ in range(3):
        with pytest.raises(GameError) as e:
            store.confirm(E.db, E.city, st, CUST, q["id"], E.now[0])
        assert e.value.code == "order_done"
    assert cash.balance(E.db, CUST) == bal and inventory.count_variant(E.db, CUST, "tank_top", NIKE) == 1
    assert E.db.q1("SELECT COUNT(*) c FROM biz_ledger WHERE kind='sale'")["c"] == 1


def test_only_the_buyer_can_confirm_their_quote_and_a_made_up_id_does_nothing(layout):
    E = K.env(layout)
    b = open_shop(E)
    st = buyer_at(E, CUST, b)
    q = store.quote(E.db, E.city, st, CUST, b["id"], "tank_top", NIKE, 1, E.now[0])
    st2 = buyer_at(E, CUST2, b)
    for who, st_, oid in ((CUST2, st2, q["id"]), (CUST, st, 9999), (CUST, st, 0), (CUST, st, -1), (CUST, st, None), (CUST, st, "1")):
        with pytest.raises(GameError) as e:
            store.confirm(E.db, E.city, st_, who, oid, E.now[0])
        assert e.value.code == "store_gone", oid
    assert inventory.count(E.db, CUST, "tank_top") == 0 and store.get(E.db, q["id"])["state"] == "quote"


def test_a_quote_expires_and_a_changed_price_voids_it(layout):
    E = K.env(layout)
    b = open_shop(E)
    st = buyer_at(E, CUST, b)
    q = store.quote(E.db, E.city, st, CUST, b["id"], "tank_top", NIKE, 1, E.now[0])
    with pytest.raises(GameError) as e:
        store.confirm(E.db, E.city, st, CUST, q["id"], E.now[0] + C.QUOTE_TTL)
    assert e.value.code == "store_expired" and store.get(E.db, q["id"])["state"] == "expired"
    q2 = store.quote(E.db, E.city, st, CUST, b["id"], "tank_top", NIKE, 1, E.now[0])
    stock.set_rule(E.db, A, b["id"], "tank_top", NIKE, price=250, now=E.now[0])
    with pytest.raises(GameError) as e:
        store.confirm(E.db, E.city, st, CUST, q2["id"], E.now[0])
    assert e.value.code == "store_changed" and store.get(E.db, q2["id"])["state"] == "cancelled" and inventory.count(E.db, CUST, "tank_top") == 0


def test_a_new_quote_replaces_the_buyers_old_one(layout):
    E = K.env(layout)
    b = open_shop(E)
    st = buyer_at(E, CUST, b)
    q1 = store.quote(E.db, E.city, st, CUST, b["id"], "tank_top", NIKE, 1, E.now[0])
    q2 = store.quote(E.db, E.city, st, CUST, b["id"], "tank_top", NIKE, 2, E.now[0])
    assert store.get(E.db, q1["id"])["state"] == "cancelled" and store.get(E.db, q2["id"])["state"] == "quote"
    with pytest.raises(GameError):
        store.confirm(E.db, E.city, st, CUST, q1["id"], E.now[0])


def test_quotes_for_what_is_not_on_sale_or_not_enough_are_refused(layout):
    E = K.env(layout)
    b = open_shop(E)
    st = buyer_at(E, CUST, b)
    with pytest.raises(GameError) as e:
        store.quote(E.db, E.city, st, CUST, b["id"], "tank_top", NIKE, 6, E.now[0])                    # 5 on display
    assert e.value.code == "store_none"
    with pytest.raises(GameError) as e:                                                                # in the warehouse only
        store.quote(E.db, E.city, st, CUST, b["id"], "tank_top", {"q": 3, "b": "nike"}, 1, E.now[0])
    assert e.value.code == "store_none"
    for bad in (0, -1, 100, True, 1.5):
        with pytest.raises(GameError) as e:
            store.quote(E.db, E.city, st, CUST, b["id"], "tank_top", NIKE, bad, E.now[0])
        assert e.value.code == "bad_amount", bad
    # a piece with no price is not for sale even when it is on display
    piece(E, A, "tank_top", 2, {"q": 4, "b": "nike"}, tag="np")
    stock.deposit(E.db, A, b["id"], "tank_top", {"q": 4, "b": "nike"}, 2, E.now[0])
    stock.move(E.db, A, b["id"], "tank_top", {"q": 4, "b": "nike"}, 2, stock.WH, E.now[0])            # on display (it shows) but with no price in the instructions
    assert stock.qty(E.db, b["id"], stock.DISP, "tank_top", {"q": 4, "b": "nike"}) == 2 and store.shelf(E.db, b["id"])[0]["data"] == stock.key(NIKE) and len(store.shelf(E.db, b["id"])) == 1
    with pytest.raises(GameError) as e:
        store.quote(E.db, E.city, st, CUST, b["id"], "tank_top", {"q": 4, "b": "nike"}, 1, E.now[0])
    assert e.value.code == "store_none"


def test_the_owner_and_the_staff_cannot_buy_from_their_own_shop(layout):
    E = K.env(layout)
    b = open_shop(E)
    for uid in (A,):
        st = buyer_at(E, uid, b)
        with pytest.raises(GameError) as e:
            store.quote(E.db, E.city, st, uid, b["id"], "tank_top", NIKE, 1, E.now[0])
        assert e.value.code == "store_self"
    c = staff.offer(E.db, A, b["id"], B, "sales", "fixed", 100, None, E.now[0])
    staff.accept(E.db, B, c["id"], E.now[0])
    st = buyer_at(E, B, b)
    with pytest.raises(GameError) as e:
        store.quote(E.db, E.city, st, B, b["id"], "tank_top", NIKE, 1, E.now[0])
    assert e.value.code == "store_self"
    assert rep.score(E.db, b["id"]) == 0


def test_a_buyer_without_the_cash_or_a_bag_room_gets_nothing_and_loses_nothing(layout, monkeypatch):
    E = K.env(layout)
    b = open_shop(E, price=300)
    st = buyer_at(E, CUST, b)
    cash.apply(E.db, CUST, -(cash.balance(E.db, CUST) - 299), "test_drain", "d", now=E.now[0])           # $2.99: one cent short
    q = store.quote(E.db, E.city, st, CUST, b["id"], "tank_top", NIKE, 1, E.now[0])
    with pytest.raises(GameError) as e:
        store.confirm(E.db, E.city, st, CUST, q["id"], E.now[0])
    assert e.value.code == "cash_low"
    assert store.get(E.db, q["id"])["state"] == "quote" and stock.qty(E.db, b["id"], stock.DISP, "tank_top", NIKE) == 5 and biz.get(E.db, b["id"])["till"] == 500_00
    cash.apply(E.db, CUST, C.CASH_START_CENTS - 299, "grant", "back", now=E.now[0])
    q = store.quote(E.db, E.city, st, CUST, b["id"], "tank_top", NIKE, 1, E.now[0])
    monkeypatch.setattr(C, "INV_MAX_STACKS", 0)
    with pytest.raises(GameError) as e:
        store.confirm(E.db, E.city, st, CUST, q["id"], E.now[0])
    assert e.value.code == "inv_full"
    monkeypatch.undo()
    assert store.get(E.db, q["id"])["state"] == "quote" and cash.balance(E.db, CUST) == C.CASH_START_CENTS and stock.qty(E.db, b["id"], stock.DISP, "tank_top", NIKE) == 5


def test_without_an_accountant_a_purchase_waits_for_approval_and_takes_nothing(layout):
    E = K.env(layout)
    b = open_shop(E, accountant=None)
    st = buyer_at(E, CUST, b)
    q = store.quote(E.db, E.city, st, CUST, b["id"], "tank_top", NIKE, 1, E.now[0])
    out = store.confirm(E.db, E.city, st, CUST, q["id"], E.now[0])
    assert out["state"] == "pending" and store.get(E.db, q["id"])["state"] == "pending"
    assert cash.balance(E.db, CUST) == C.CASH_START_CENTS and inventory.count(E.db, CUST, "tank_top") == 0 and stock.qty(E.db, b["id"], stock.DISP, "tank_top", NIKE) == 5
    assert store.confirm(E.db, E.city, st, CUST, q["id"], E.now[0])["state"] == "pending"            # pressing again only says it is waiting
    assert [o["id"] for o in store.pending(E.db, b["id"], E.now[0])] == [q["id"]]
    assert (A, "store_pending") in [(u, k) for u, k, kw in E.db.outbox]
    res = store.approve(E.db, A, q["id"], E.now[0] + 5)
    assert res["how"] == f"approved:{A}" and inventory.count(E.db, CUST, "tank_top") == 1 and cash.balance(E.db, CUST) == C.CASH_START_CENTS - 300
    with pytest.raises(GameError) as e:
        store.approve(E.db, A, q["id"], E.now[0] + 6)
    assert e.value.code == "store_gone" and cash.balance(E.db, CUST) == C.CASH_START_CENTS - 300


def test_approval_needs_the_right_and_rechecks_the_stock_and_the_cash(layout):
    E = K.env(layout)
    b = open_shop(E, accountant=None)
    st = buyer_at(E, CUST, b)
    q = store.quote(E.db, E.city, st, CUST, b["id"], "tank_top", NIKE, 1, E.now[0])
    store.confirm(E.db, E.city, st, CUST, q["id"], E.now[0])
    for who in (CUST, CUST2, B):
        with pytest.raises(GameError) as e:
            store.approve(E.db, who, q["id"], E.now[0])
        assert e.value.code == "branch_denied"
    cash.apply(E.db, CUST, -(cash.balance(E.db, CUST)), "test_drain", "d", now=E.now[0])                # the buyer spent their money meanwhile
    with pytest.raises(GameError) as e:
        store.approve(E.db, A, q["id"], E.now[0])
    assert e.value.code == "cash_low" and store.get(E.db, q["id"])["state"] == "pending"
    assert store.decline(E.db, A, q["id"], E.now[0]) is True and store.get(E.db, q["id"])["state"] == "declined"
    assert E.db.q1("SELECT COUNT(*) c FROM biz_ledger WHERE kind='sale'")["c"] == 0


def test_a_waiting_order_expires(layout):
    E = K.env(layout)
    b = open_shop(E, accountant=None)
    st = buyer_at(E, CUST, b)
    q = store.quote(E.db, E.city, st, CUST, b["id"], "tank_top", NIKE, 1, E.now[0])
    store.confirm(E.db, E.city, st, CUST, q["id"], E.now[0])
    with pytest.raises(GameError) as e:
        store.approve(E.db, A, q["id"], E.now[0] + C.APPROVAL_TTL)
    assert e.value.code == "store_gone" and store.get(E.db, q["id"])["state"] == "expired" and store.pending(E.db, b["id"], E.now[0] + C.APPROVAL_TTL) == []


def test_a_manager_with_approve_can_approve_and_the_buyer_can_withdraw(layout):
    E = K.env(layout)
    b = open_shop(E, accountant=None)
    c = staff.offer(E.db, A, b["id"], B, "manager", "fixed", 0, ("approve",), E.now[0])
    staff.accept(E.db, B, c["id"], E.now[0])
    st = buyer_at(E, CUST, b)
    q = store.quote(E.db, E.city, st, CUST, b["id"], "tank_top", NIKE, 1, E.now[0])
    store.confirm(E.db, E.city, st, CUST, q["id"], E.now[0])
    assert (B, "store_pending") in [(u, k) for u, k, kw in E.db.outbox]
    q2 = store.quote(E.db, E.city, st, CUST, b["id"], "tank_top", NIKE, 1, E.now[0])
    assert store.cancel(E.db, CUST, q["id"]) is True and store.get(E.db, q["id"])["state"] == "cancelled"
    with pytest.raises(GameError):
        store.approve(E.db, B, q["id"], E.now[0])


def test_an_accountants_limit_decides_what_completes_by_itself(layout):
    E = K.env(layout)
    price = C.BOT_AUTO_LIMIT_CENTS // 4 - 1                                                              # $24.99 a piece
    assert price <= stock.max_price("tank_top", NIKE)
    b = open_shop(E, price=price, n=12, target=6)
    staff.hire_bot(E.db, A, b["id"], "warehouse", E.now[0])
    st = buyer_at(E, CUST, b)
    K.rich(E, CUST, 1000)
    q = store.quote(E.db, E.city, st, CUST, b["id"], "tank_top", NIKE, 4, E.now[0])
    assert q["total_cents"] == 4 * price <= C.BOT_AUTO_LIMIT_CENTS
    assert store.confirm(E.db, E.city, st, CUST, q["id"], E.now[0])["state"] == "done"                 # within the bot's limit
    q = store.quote(E.db, E.city, st, CUST, b["id"], "tank_top", NIKE, 5, E.now[0])                    # over it: the owner approves
    assert q["total_cents"] > C.BOT_AUTO_LIMIT_CENTS and store.confirm(E.db, E.city, st, CUST, q["id"], E.now[0])["state"] == "pending"


def test_a_player_accountants_limit_grows_with_their_skill_and_they_earn_it_slowly(layout):
    E = K.env(layout)
    b = open_shop(E, accountant=None, price=500)
    c = staff.offer(E.db, A, b["id"], B, "accountant", "percent", 1000, None, E.now[0])
    staff.accept(E.db, B, c["id"], E.now[0])
    assert staff.auto_limit(E.db, staff.get(E.db, c["id"])) == C.ACCOUNTANT_AUTO_BASE_CENTS
    give_skill(E, B, "accounting", 400)
    assert staff.auto_limit(E.db, staff.get(E.db, c["id"])) == C.ACCOUNTANT_AUTO_BASE_CENTS + C.ACCOUNTANT_AUTO_PER_LEVEL_CENTS * 400
    w0, xp0 = skills.work_of(E.db, B, "accounting"), charxp.get(E.db, B)
    buy_one(E, CUST, b)
    assert skills.work_of(E.db, B, "accounting") == w0 + 1000 and charxp.get(E.db, B) == xp0 + C.ACCOUNTING_XP
    buy_one(E, CUST, b)                                                                                # the same customer again the same day: no more
    assert skills.work_of(E.db, B, "accounting") == w0 + 1000 and charxp.get(E.db, B) == xp0 + C.ACCOUNTING_XP
    buy_one(E, CUST2, b)
    assert skills.work_of(E.db, B, "accounting") == w0 + 2000
    E.now[0] += 86400
    buy_one(E, CUST, b, now=E.now[0])
    assert skills.work_of(E.db, B, "accounting") == w0 + 3000


def test_a_bot_accountant_earns_no_skill_and_the_game_takes_its_wage(layout):
    E = K.env(layout)
    b = open_shop(E)
    buy_one(E, CUST, b)
    assert skills.work_of(E.db, A, "accounting") == 0
    till = biz.get(E.db, b["id"])["till"]
    E.now[0] += 86400
    res = staff.settle(E.db, b["id"], E.now[0])
    day = C.BOT_WORKER_WAGE_DAY_CENTS["accountant"] + C.BOT_WORKER_WAGE_DAY_CENTS.get("warehouse", 0) * 0
    assert res["paid"] == day and res["left"] == 0 and biz.get(E.db, b["id"])["till"] == till - day
    assert biz.audit_till(E.db, b["id"]) == []


def test_the_checkout_upgrade_lets_a_small_purchase_complete_without_an_accountant(layout):
    E = K.env(layout)
    b = open_shop(E, accountant=None, price=400, fund=500, n=16, target=16)
    assert upgrades.effects(E.db, b["id"])["selfcheck"] == 0
    q, out = buy_one(E, CUST, b)
    assert out["state"] == "pending"                                                                    # no accountant and no checkout: the owner approves
    K.install(E, A, b, "checkout")
    limit = upgrades.effects(E.db, b["id"])["selfcheck"]
    assert limit == 50_00
    q, out = buy_one(E, CUST, b)
    assert out["state"] == "done" and out["how"] == "checkout"
    q, out = buy_one(E, CUST, b, 13)                                                                    # $52: over the checkout's limit
    assert q["total_cents"] == 5200 > limit and out["state"] == "pending"


def test_reputation_comes_from_real_sales_and_cannot_be_farmed_by_one_buyer(layout):
    E = K.env(layout)
    b = open_shop(E, price=400, n=30, target=14)
    K.rich(E, CUST, 1000)
    got = []
    for i in range(10):
        buy_one(E, CUST, b)
        got.append(rep.score(E.db, b["id"]))
    stars = 2
    assert got[0] == C.REP_SALE_PER_STAR * stars
    assert got[-1] == C.REP_PER_BUYER_DAILY                                                            # one customer a day gives at most this much
    assert rep.get(E.db, b["id"])["sales"] == C.REP_PER_BUYER_DAILY // (C.REP_SALE_PER_STAR * stars)
    K.rich(E, CUST2, 1000)
    buy_one(E, CUST2, b)
    assert rep.score(E.db, b["id"]) == C.REP_PER_BUYER_DAILY + 2
    E.now[0] += 86400
    buy_one(E, CUST, b, now=E.now[0])
    assert rep.score(E.db, b["id"]) == C.REP_PER_BUYER_DAILY + 4
    assert rep.add(E.db, b["id"], "sale", f"o{store.history(E.db, b['id'])[0]['id']}", 5, CUST, E.now[0]) == 0        # the same sale again: nothing


def test_tiny_sales_give_no_reputation_or_skill(layout):
    E = K.env(layout)
    b = open_shop(E, price=C.ACCOUNTING_MIN_SALE_CENTS - 1)
    buy_one(E, CUST, b)
    assert rep.score(E.db, b["id"]) == 0


def test_cameras_raise_what_a_sale_earns(layout):
    E = K.env(layout)
    b = open_shop(E, price=400, fund=900)
    K.install(E, A, b, "cameras", 2)
    buy_one(E, CUST, b)
    assert rep.score(E.db, b["id"]) == -(-C.REP_SALE_PER_STAR * 2 * (10000 + 3500) // 10000)


def test_the_directory_ranks_open_branches_by_reputation(layout):
    E = K.env(layout)
    s1 = open_shop(E)
    s2 = shop(E, skip=(s1["property_id"],))
    buy_one(E, CUST, s1)
    d = rep.directory(E.db, E.city.id)
    assert [x["branch"]["id"] for x in d] == [s1["id"], s2["id"]] and d[0]["score"] > 0 and d[0]["title"] == "newcomer"
    assert rep.title_id(0) == "newcomer" and rep.title_id(40) == "known" and rep.title_id(10 ** 6) == "renowned" and rep.next_title(10 ** 6) is None
    assert rep.ranking(E.db)[0]["branch"]["id"] == s1["id"]


def test_a_sale_failing_half_way_changes_nothing(layout, monkeypatch):
    E = K.env(layout)
    b = open_shop(E)
    st = buyer_at(E, CUST, b)
    q = store.quote(E.db, E.city, st, CUST, b["id"], "tank_top", NIKE, 1, E.now[0])

    def boom(*a, **k):
        raise RuntimeError("the till broke")
    monkeypatch.setattr(biz, "till_apply", boom)
    with pytest.raises(RuntimeError):
        store.confirm(E.db, E.city, st, CUST, q["id"], E.now[0])
    monkeypatch.undo()
    assert store.get(E.db, q["id"])["state"] == "quote" and cash.balance(E.db, CUST) == C.CASH_START_CENTS and inventory.count(E.db, CUST, "tank_top") == 0
    assert stock.qty(E.db, b["id"], stock.DISP, "tank_top", NIKE) == 5 and stock.audit(E.db, b["id"]) == []
    assert store.confirm(E.db, E.city, st, CUST, q["id"], E.now[0])["state"] == "done"                  # and it can still be bought


def test_two_customers_racing_for_the_last_piece_one_wins_and_pays_once(layout, tmp_path):
    from hypemarket.db import Database
    import copy
    from hypemarket import world, worldplaces
    path = str(tmp_path / "race.db")
    db = Database(path)
    core.set_clock(lambda: K.T0)
    LJ.worldmap_reset()
    for u in (A, CUST, CUST2):
        core.ensure_user(db, u, f"u{u}", f"User{u}", lang="fa")
        charkit.complete(db, u)
    w, _ = world.save_world(db, LJ.CH, copy.deepcopy(layout))
    city = worldplaces.load_city(db, w)
    estate.ensure(db, city, K.T0)
    p = [x for x in estate.listing(db, city.id) if x["zone"] == "commercial"][0]
    cash.ensure_account(db, A)
    cash.apply(db, A, 100_000_00, "grant", "r", now=K.T0)
    spot = city.spot(p["parcel_id"])
    for u in (A, CUST, CUST2):
        presence.stand(db, city, u, K.T0)
        db.x("UPDATE world_players SET x=?, y=?, at_place=? WHERE world_id=? AND user_id=?", (spot.door.pt[0], spot.door.pt[1], spot.id, city.id, u))
    estate.buy(db, city, presence.stand(db, city, A, K.T0), A, p["id"], K.T0)
    estate.set_mode(db, A, p["id"], "retail", K.T0)
    brands.register(db, A, "nike", K.T0)
    b = biz.open_branch(db, A, p["id"], "nike", K.T0)
    inventory.give(db, A, "tank_top", 1, "test:x", "x", NIKE, K.T0)
    stock.deposit(db, A, b["id"], "tank_top", NIKE, 1, K.T0)
    stock.set_rule(db, A, b["id"], "tank_top", NIKE, price=300, target=1, now=K.T0)
    stock.move(db, A, b["id"], "tank_top", NIKE, 1, stock.WH, K.T0)
    staff.hire_bot(db, A, b["id"], "accountant", K.T0)
    quotes = {u: store.quote(db, city, presence.stand(db, city, u, K.T0), u, b["id"], "tank_top", NIKE, 1, K.T0) for u in (CUST, CUST2)}
    db.close()
    outcomes, errors = {}, []
    barrier = threading.Barrier(2)

    def run(u):
        try:
            d = Database(path)
            c = worldplaces.load_city(d, d.q1("SELECT * FROM worlds WHERE id=?", (city.id,)))
            st = presence.stand(d, c, u, K.T0)
            barrier.wait()
            try:
                store.confirm(d, c, st, u, quotes[u]["id"], K.T0)
                outcomes[u] = "bought"
            except GameError as e:
                outcomes[u] = e.code
        except Exception as e:                                                                         # noqa: BLE001
            errors.append(repr(e))
    th = [threading.Thread(target=run, args=(u,)) for u in (CUST, CUST2)]
    [x.start() for x in th]
    [x.join() for x in th]
    assert not errors, errors
    assert sorted(outcomes.values()) == ["bought", "store_none"] or sorted(outcomes.values()) == ["bought", "stock_low"]
    d = Database(path)
    winner = CUST if outcomes[CUST] == "bought" else CUST2
    loser = CUST2 if winner == CUST else CUST
    assert inventory.count(d, winner, "tank_top") == 1 and inventory.count(d, loser, "tank_top") == 0
    assert cash.balance(d, winner) == C.CASH_START_CENTS - 300 and cash.balance(d, loser) == C.CASH_START_CENTS
    assert d.q1("SELECT till FROM branches WHERE id=?", (b["id"],))["till"] == 300 and d.q1("SELECT COUNT(*) c FROM biz_ledger WHERE kind='sale'")["c"] == 1 and stock.audit(d, b["id"]) == []


# ============================================================================================== staff and payroll
def hire(E, b, role="warehouse", pay_type="fixed", pay_value=100, who=B, perms=None):
    c = staff.offer(E.db, A, b["id"], who, role, pay_type, pay_value, perms, E.now[0])
    return staff.accept(E.db, who, c["id"], E.now[0])


def test_an_offer_is_not_a_job_until_the_employee_accepts(layout):
    E = K.env(layout)
    b = shop(E)
    c = staff.offer(E.db, A, b["id"], B, "warehouse", "fixed", 100, None, E.now[0])
    assert (c["state"], c["employee_id"], c["role"], c["pay_type"], c["pay_value"], c["perms"], c["starts_at"]) == ("offered", B, "warehouse", "fixed", 100, "stock", None)
    assert not biz.can(E.db, B, b, "stock") and staff.owed(E.db, c, E.now[0] + 99999) == (0, 0)
    for who in (A, CUST, 999):
        with pytest.raises(GameError) as e:
            staff.accept(E.db, who, c["id"], E.now[0])
        assert e.value.code == "staff_gone"
    c = staff.accept(E.db, B, c["id"], E.now[0] + 10)
    assert c["state"] == "active" and c["starts_at"] == E.now[0] + 10 and biz.can(E.db, B, b, "stock") and not biz.can(E.db, B, b, "produce")
    with pytest.raises(GameError) as e:
        staff.accept(E.db, B, c["id"], E.now[0] + 11)
    assert e.value.code == "staff_gone"


def test_declining_withdrawing_and_expiry(layout):
    E = K.env(layout)
    b = shop(E)
    c1 = staff.offer(E.db, A, b["id"], B, "warehouse", "fixed", 100, None, E.now[0])
    assert staff.decline(E.db, B, c1["id"], E.now[0])["state"] == "declined"
    with pytest.raises(GameError):
        staff.accept(E.db, B, c1["id"], E.now[0])
    c2 = staff.offer(E.db, A, b["id"], B, "warehouse", "fixed", 100, None, E.now[0])
    assert staff.withdraw_offer(E.db, A, c2["id"], E.now[0])["state"] == "declined"
    c3 = staff.offer(E.db, A, b["id"], B, "warehouse", "fixed", 100, None, E.now[0])
    with pytest.raises(GameError) as e:
        staff.accept(E.db, B, c3["id"], E.now[0] + C.STAFF_OFFER_TTL)
    assert e.value.code == "staff_gone"


def test_the_terms_of_a_contract_never_change(layout):
    E = K.env(layout)
    b = shop(E)
    c = hire(E, b)
    for col, val in (("pay_value", 999), ("pay_type", "percent"), ("role", "manager"), ("perms", "stock,hire"), ("employee_id", CUST), ("branch_id", 99), ("offered_by", B)):
        with pytest.raises(Exception, match="keeps its terms"):
            E.db.x(f"UPDATE contracts SET {col}=? WHERE id=?", (val, c["id"]))
    with pytest.raises(Exception):
        E.db.x("DELETE FROM contracts")
    staff.end(E.db, B, c["id"], E.now[0] + 5)
    for sql in ("UPDATE contracts SET state='active'", "UPDATE contracts SET ended_at=1"):
        with pytest.raises(Exception):
            E.db.x(sql)


def test_a_different_deal_is_a_new_contract_the_employee_must_accept(layout):
    E = K.env(layout)
    b = shop(E)
    c1 = hire(E, b, pay_value=100)
    with pytest.raises(GameError) as e:
        staff.offer(E.db, A, b["id"], B, "warehouse", "fixed", 300, None, E.now[0])
    assert e.value.code == "staff_dup"                                                                 # the running one keeps its terms
    staff.end(E.db, A, c1["id"], E.now[0] + 100)
    c2 = staff.offer(E.db, A, b["id"], B, "warehouse", "fixed", 300, None, E.now[0] + 101)
    assert c2["state"] == "offered" and staff.get(E.db, c1["id"])["pay_value"] == 100


def test_the_owners_rules_for_who_can_be_hired_and_on_what_terms(layout):
    E = K.env(layout)
    b = shop(E)
    cases = (((A, "warehouse", "fixed", 100), "staff_self"), ((999, "warehouse", "fixed", 100), "staff_noone"), ((B, "pilot", "fixed", 100), "staff_role"),
             ((B, "warehouse", "monthly", 100), "staff_terms"), ((B, "warehouse", "fixed", -1), "staff_terms"), ((B, "warehouse", "fixed", C.STAFF_FIXED_MAX_DAY_CENTS + 1), "staff_terms"),
             ((B, "warehouse", "percent", C.STAFF_PERCENT_MAX_BPS + 1), "staff_terms"), ((B, "warehouse", "fixed", True), "staff_terms"))
    for args, code in cases:
        with pytest.raises(GameError) as e:
            staff.offer(E.db, A, b["id"], *args, None, E.now[0])
        assert e.value.code == code, args
    with pytest.raises(GameError) as e:
        staff.offer(E.db, B, b["id"], CUST, "warehouse", "fixed", 100, None, E.now[0])
    assert e.value.code == "branch_denied"
    core.ensure_user(E.db, 51090, "nochar", "No", lang="fa")
    with pytest.raises(GameError) as e:
        staff.offer(E.db, A, b["id"], 51090, "warehouse", "fixed", 100, None, E.now[0])
    assert e.value.code == "staff_noone"
    with pytest.raises(GameError) as e:
        staff.offer(E.db, A, b["id"], B, "manager", "fixed", 100, ("everything",), E.now[0])
    assert e.value.code == "staff_terms"


def test_the_staff_slots_the_one_manager_and_the_percent_caps(layout):
    E = K.env(layout)
    b = shop(E, fund=2000)
    assert upgrades.staff_slots(E.db, b) == 2
    hire(E, b, "manager", "percent", 2500, CUST)
    with pytest.raises(GameError) as e:
        staff.offer(E.db, A, b["id"], B, "manager", "fixed", 0, None, E.now[0])
    assert e.value.code == "staff_manager"
    with pytest.raises(GameError) as e:
        staff.offer(E.db, A, b["id"], B, "sales", "percent", 3000, None, E.now[0])
    assert e.value.code == "staff_percent"                                                             # 25% + 30% > 50%
    hire(E, b, "sales", "percent", 1000, B)
    with pytest.raises(GameError) as e:
        staff.offer(E.db, A, b["id"], CUST2, "warehouse", "fixed", 100, None, E.now[0])
    assert e.value.code == "staff_full"
    K.install(E, A, b, "staffroom")
    assert upgrades.staff_slots(E.db, biz.get(E.db, b["id"])) == 3
    assert staff.offer(E.db, A, b["id"], CUST2, "warehouse", "fixed", 100, None, E.now[0])["state"] == "offered"


def test_a_manager_holds_only_the_permissions_in_the_contract(layout):
    E = K.env(layout)
    b = shop(E, fund=800)
    c = hire(E, b, "manager", "fixed", 0, B, perms=("stock", "approve"))
    assert staff.perms_of(c) == ("stock", "approve")
    row = biz.get(E.db, b["id"])
    assert biz.can(E.db, B, row, "stock") and biz.can(E.db, B, row, "approve") and not biz.can(E.db, B, row, "ads") and not biz.can(E.db, B, row, "upgrade") and biz.can(E.db, B, row)
    # and never the owner's own powers
    for fn in (lambda: biz.close_branch(E.db, B, b["id"], E.now[0]), lambda: biz.withdraw(E.db, B, b["id"], 100, "w", E.now[0]), lambda: biz.fund(E.db, B, b["id"], 100, "f", E.now[0]),
               lambda: staff.hire_bot(E.db, B, b["id"], "accountant", E.now[0]), lambda: staff.offer(E.db, B, b["id"], CUST, "sales", "fixed", 1, None, E.now[0]),
               lambda: brands.register(E.db, B, "givenchy", E.now[0]) and brands.get(E.db, "nike")["owner_id"] == B, lambda: upgrades.install(E.db, B, b["id"], "shelving", E.now[0])):
        with pytest.raises(GameError):
            fn()
    assert brands.get(E.db, "nike")["owner_id"] == A
    brands_of_b = brands.of_player(E.db, B)
    assert all(x["brand"] != "nike" for x in brands_of_b)


def test_a_fixed_wage_accrues_by_the_second_and_is_paid_from_the_till(layout):
    E = K.env(layout)
    b = shop(E, fund=500)
    c = hire(E, b, "warehouse", "fixed", 240_00 // 24)                                                  # $10 a day
    c = staff.get(E.db, c["id"])
    assert staff.owed(E.db, c, c["starts_at"] + 43200) == (500, 0) and staff.owed(E.db, c, c["starts_at"]) == (0, 0)
    E.now[0] = c["starts_at"] + 86400
    assert staff.reserved(E.db, b["id"], E.now[0]) == 1000
    bal = cash.balance(E.db, B)
    res = staff.settle(E.db, b["id"], E.now[0])
    assert res == {"paid": 1000, "left": 0} and cash.balance(E.db, B) == bal + 1000 and biz.get(E.db, b["id"])["till"] == 500_00 - 1000
    assert staff.settle(E.db, b["id"], E.now[0]) == {"paid": 0, "left": 0}                             # nothing is paid twice
    assert biz.audit_till(E.db, b["id"]) == [] and E.ledger(B, ("wage_in",))[0][2] == 1000
    assert staff.get(E.db, c["id"])["paid_fixed"] == 1000


def test_a_percentage_is_paid_on_the_recorded_sales_after_the_start_and_nothing_else(layout):
    E = K.env(layout)
    b = open_shop(E, price=1000, n=30, target=14)
    K.rich(E, CUST, 1000)
    buy_one(E, CUST, b)                                                                                 # a sale BEFORE the contract does not count
    E.now[0] += 60
    c = hire(E, b, "sales", "percent", 1500, B)
    E.now[0] += 60
    buy_one(E, CUST, b, 2, now=E.now[0])
    c = staff.get(E.db, c["id"])
    assert staff.owed(E.db, c, E.now[0]) == (0, 2000 * 1500 // 10000)
    bal = cash.balance(E.db, B)
    res = staff.settle(E.db, b["id"], E.now[0])
    assert res["paid"] >= 300 and cash.balance(E.db, B) >= bal + 300
    assert staff.get(E.db, c["id"])["paid_pct"] == 300 and staff.owed(E.db, staff.get(E.db, c["id"]), E.now[0]) == (0, 0)
    buy_one(E, CUST, b, now=E.now[0])
    assert staff.owed(E.db, staff.get(E.db, c["id"]), E.now[0]) == (0, 150)                             # only the new sale
    assert biz.audit_till(E.db, b["id"]) == []


def test_what_the_till_cannot_pay_stays_owed_and_is_reserved_from_the_owner(layout):
    E = K.env(layout)
    b = shop(E, fund=20)
    c = hire(E, b, "warehouse", "fixed", 500_00)
    E.now[0] = staff.get(E.db, c["id"])["starts_at"] + 86400
    assert staff.reserved(E.db, b["id"], E.now[0]) == 500_00
    res = staff.settle(E.db, b["id"], E.now[0])
    assert res == {"paid": 20_00, "left": 480_00} and biz.get(E.db, b["id"])["till"] == 0
    biz.fund(E.db, A, b["id"], 100, "again", E.now[0])
    for fn in (lambda: biz.withdraw(E.db, A, b["id"], 1, "w", E.now[0]), lambda: upgrades.install(E.db, A, b["id"], "shelving", E.now[0]),
               lambda: market.create(E.db, A, b["id"], 1, 10, 1, C.AD_TTL_CHOICES[0], E.now[0])):
        with pytest.raises(GameError) as e:
            fn()
        assert e.value.code in ("till_reserved", "till_low", "ad_mode"), e.value.code
    assert biz.spendable(E.db, biz.get(E.db, b["id"]), E.now[0]) == 0
    assert staff.settle(E.db, b["id"], E.now[0])["paid"] == 100                                          # the reserved money goes to the employee first


def test_a_bot_employees_wage_is_a_sink_and_a_bot_is_never_a_manager(layout):
    E = K.env(layout)
    b = shop(E)
    for role in ("manager", "pilot"):
        with pytest.raises(GameError) as e:
            staff.hire_bot(E.db, A, b["id"], role, E.now[0])
        assert e.value.code == "staff_role"
    c = staff.hire_bot(E.db, A, b["id"], "warehouse", E.now[0])
    assert (c["employee_id"], c["state"], c["pay_type"], c["pay_value"]) == (None, "active", "fixed", C.BOT_WORKER_WAGE_DAY_CENTS["warehouse"])
    with pytest.raises(GameError) as e:
        staff.hire_bot(E.db, A, b["id"], "warehouse", E.now[0])
    assert e.value.code == "staff_dup"
    till = biz.get(E.db, b["id"])["till"]
    E.now[0] += 86400
    res = staff.settle(E.db, b["id"], E.now[0])
    assert res["paid"] == C.BOT_WORKER_WAGE_DAY_CENTS["warehouse"] and biz.get(E.db, b["id"])["till"] == till - res["paid"]
    assert E.db.q1("SELECT COUNT(*) c FROM life_ledger WHERE kind='wage_in'")["c"] == 0


def test_ending_a_contract_settles_what_is_owed_first(layout):
    E = K.env(layout)
    b = shop(E, fund=500)
    c = hire(E, b, "warehouse", "fixed", 1000)
    start = staff.get(E.db, c["id"])["starts_at"]
    staff.end(E.db, A, c["id"], start + 86400)
    done = staff.get(E.db, c["id"])
    assert done["state"] == "ended" and done["ended_by"] == A and done["paid_fixed"] == 1000
    assert staff.owed(E.db, done, start + 10 * 86400) == (0, 0)                                          # it stopped accruing the moment it ended
    assert not biz.can(E.db, B, biz.get(E.db, b["id"]), "stock")
    with pytest.raises(GameError) as e:
        staff.end(E.db, A, c["id"], start + 86401)
    assert e.value.code == "staff_gone"
    c2 = hire(E, b, "sales", "fixed", 100, CUST)
    with pytest.raises(GameError) as e:
        staff.end(E.db, B, c2["id"], E.now[0])
    assert e.value.code == "branch_denied"
    assert staff.end(E.db, CUST, c2["id"], E.now[0] + 5)["state"] == "ended"                             # the employee can always leave


def test_closing_pays_the_wages_first_and_the_owner_makes_up_the_difference(layout):
    E = K.env(layout)
    b = shop(E, fund=20)
    c = hire(E, b, "warehouse", "fixed", 500_00)
    E.now[0] = staff.get(E.db, c["id"])["starts_at"] + 86400
    bal_o, bal_b = cash.balance(E.db, A), cash.balance(E.db, B)
    biz.close_branch(E.db, A, b["id"], E.now[0])
    assert cash.balance(E.db, B) == bal_b + 500_00 and cash.balance(E.db, A) == bal_o - 480_00          # the till's $20 paid first, the owner's own cash the rest
    assert staff.get(E.db, c["id"])["state"] == "ended" and staff.reserved(E.db, b["id"], E.now[0]) == 0 and biz.audit_till(E.db, b["id"]) == []


def test_a_branch_that_cannot_pay_its_wages_does_not_close(layout):
    E = K.env(layout)
    b = shop(E, fund=20)
    c = hire(E, b, "warehouse", "fixed", 500_00)
    E.now[0] = staff.get(E.db, c["id"])["starts_at"] + 86400
    cash.apply(E.db, A, -cash.balance(E.db, A), "test_drain", "d", now=E.now[0])
    with pytest.raises(GameError) as e:
        biz.close_branch(E.db, A, b["id"], E.now[0])
    assert e.value.code == "owed_wages" and biz.get(E.db, b["id"])["state"] == "open" and staff.get(E.db, c["id"])["state"] == "active"


def test_a_warehouse_worker_earns_skill_slowly_and_only_for_real_runs(layout):
    E = K.env(layout)
    b = shop(E)
    hire(E, b, "warehouse", "fixed", 100, B)
    stocked(E, b, 12, display=False, target=100, refill=False)
    w0 = skills.work_of(E.db, B, "warehouse")
    stock.move(E.db, B, b["id"], "tank_top", NIKE, 1, stock.WH, E.now[0])
    assert skills.work_of(E.db, B, "warehouse") == w0 + 1000
    for _ in range(stock.WAREHOUSE_RUNS_PER_DAY + 3):
        stock.move(E.db, B, b["id"], "tank_top", NIKE, 1, stock.WH, E.now[0])
        stock.move(E.db, B, b["id"], "tank_top", NIKE, 1, stock.DISP, E.now[0])
    assert skills.work_of(E.db, B, "warehouse") == w0 + 1000 * stock.WAREHOUSE_RUNS_PER_DAY            # at most this many a day
    assert skills.work_of(E.db, A, "warehouse") == 0                                                    # the owner's own runs earn nothing


# ============================================================================================== reports
def test_the_report_adds_up_from_the_branchs_own_records(layout):
    E = K.env(layout)
    w = workshop(E, fund=9000)
    s = shop(E, skip=(w["property_id"],), fund=1000)
    stocked(E, s, 6, price=500, target=3)
    staff.hire_bot(E.db, A, s["id"], "accountant", E.now[0])
    buy_one(E, CUST, s, 2)
    ad = market.create(E.db, A, w["id"], 1, 100, 10, C.AD_TTL_CHOICES[0], E.now[0])
    fabric(E, CUST2, 1, 4)
    market.fill(E.db, CUST2, ad["id"], 1, 4, E.now[0])
    stock.set_rule(E.db, A, s["id"], "tank_top", NIKE, keep=0, buyback=True, target=3, now=E.now[0])
    E.now[0] += 86400
    staff.settle(E.db, s["id"], E.now[0])
    r = reports.pnl(E.db, s["id"])
    assert r["revenue"] == 1000 and r["sales"] == 1 and r["wages"] == C.BOT_WORKER_WAGE_DAY_CENTS["accountant"] and r["income"] == r["revenue"] + r["buyback"]
    assert r["profit"] == r["income"] - r["costs"] and r["costs"] == r["materials"] + r["wages"] + r["upgrades"]
    assert r["till"] == biz.get(E.db, s["id"])["till"] and r["reputation"] == rep.score(E.db, s["id"]) and r["stock_value"] == stock.stock_value(E.db, s["id"])
    rw = reports.pnl(E.db, w["id"])
    assert rw["materials"] == 400 and rw["revenue"] == 0 and rw["reserved_ads"] == 600 and rw["upgrades"] == 300_00 + 900_00 + 2500_00 + 600_00 and rw["profit"] == -(400 + rw["upgrades"])
    assert rw["funded"] == 9000_00
    assert reports.pnl(E.db, s["id"], since=E.now[0] + 10 ** 6)["revenue"] == 0
    assert reports.best_sellers(E.db, s["id"]) == [("tank_top", stock.key(NIKE), 2, 1000)]
    ov = reports.overview(E.db, A)
    assert [x["branch"]["id"] for x in ov] == [w["id"], s["id"]] and ov[1]["staff"][0]["role"] == "accountant" and len(ov[0]["ads"]) == 1


# ============================================================================================== style
def wear(E, uid, item, q, tag=""):
    inventory.give(E.db, uid, item, 1, "test:wear", f"{uid}:{item}:{q}:{tag}", {"q": q} if q else None, E.now[0])
    sid = E.db.q1("SELECT id FROM item_inventory WHERE user_id=? AND item_id=? ORDER BY id DESC LIMIT 1", (uid, item))["id"]
    inventory.equip(E.db, uid, sid, E.now[0])


def test_the_rating_comes_from_the_worn_pieces_their_quality_and_completeness(layout):
    E = K.env(layout)
    assert style.rating(E.db, A) == {"points": 0, "tier": "plain", "worn": {}, "complete": False}
    wear(E, A, "tshirt", 4)
    r1 = style.rating(E.db, A)
    wear(E, A, "jeans", 4)
    r2 = style.rating(E.db, A)
    wear(E, A, "sneakers", 4)
    r3 = style.rating(E.db, A)
    wear(E, A, "cap", 4)
    r4 = style.rating(E.db, A)
    assert r1["points"] < r2["points"] < r3["points"] < r4["points"] <= 100 and r3["complete"] and not r2["complete"]
    assert r1["tier"] in ("simple", "average") and r4["tier"] == "iconic"
    assert r3["points"] == 3 * C.STYLE_Q_POINTS[4] + C.STYLE_COMPLETE_BONUS


def test_a_plain_complete_outfit_beats_one_expensive_piece(layout):
    E = K.env(layout)
    wear(E, A, "coat", 5)
    lone = style.rating(E.db, A)["points"]
    for item in ("tshirt", "trousers", "shoes"):
        wear(E, B, item, 1)
    assert style.rating(E.db, B)["points"] > 0 and style.rating(E.db, B)["complete"]
    wear(E, CUST, "tshirt", 3), wear(E, CUST, "jeans", 3), wear(E, CUST, "boots", 3)
    assert style.rating(E.db, CUST)["points"] > lone


def test_the_tiers_and_the_tip_bonus_are_capped_and_never_negative():
    assert [style.tier_of(p) for p in (0, 1, 24, 25, 54, 55, 79, 80, 100)] == ["plain", "simple", "simple", "average", "average", "stylish", "stylish", "iconic", "iconic"]
    assert style.tip_bonus_bps(0) == 0 and style.tip_bonus_bps(100) == C.STYLE_TIP_BPS_MAX and style.tip_bonus_bps(10 ** 6) == C.STYLE_TIP_BPS_MAX and style.tip_bonus_bps(-5) == 0
    assert [style.tip_bonus_bps(p) for p in range(0, 101, 10)] == sorted(style.tip_bonus_bps(p) for p in range(0, 101, 10))


def test_outfits_are_saved_per_kind_and_worn_again_from_the_bag(layout):
    E = K.env(layout)
    with pytest.raises(GameError) as e:
        style.save_outfit(E.db, A, "casual", E.now[0])
    assert e.value.code == "outfit_empty"
    wear(E, A, "tshirt", 3)
    wear(E, A, "jeans", 3)
    saved = style.save_outfit(E.db, A, "casual", E.now[0])
    assert set(saved) == {"upper", "lower"} and set(style.outfits(E.db, A)) == {"casual"}
    for bad in ("pajamas", "", None):
        with pytest.raises(GameError) as e:
            style.save_outfit(E.db, A, bad, E.now[0])
        assert e.value.code == "outfit_kind"
    inventory.unequip(E.db, A, "upper")
    res = style.wear_outfit(E.db, A, "casual", E.now[0])
    assert res == {"worn": ["upper"], "kept": ["lower"], "missing": []}
    inventory.unequip(E.db, A, "lower")
    assert inventory.audit(E.db, A) == []
    E.db.x("DELETE FROM item_inventory WHERE user_id=? AND item_id='jeans'", (A,))
    res = style.wear_outfit(E.db, A, "casual", E.now[0])
    assert res["missing"] == ["lower"] and "upper" in (res["kept"] + res["worn"])
    with pytest.raises(GameError) as e:
        style.wear_outfit(E.db, A, "formal", E.now[0])
    assert e.value.code == "outfit_none"
