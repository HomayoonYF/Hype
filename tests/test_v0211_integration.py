"""v0.211 integration: the periodic business work (bizjobs), the private messages of the business world, the outfit's effect on Snapp tips, and the map's place panel."""
import pytest

import bizkit
import test_v0211_shop as SH
from aiogram.methods import SendMessage
from test_life_jobs import E, layout, ride_to_the_end  # noqa: F401 (fixtures)
from test_life_flow import CH
from test_v0211_ui import A, B, D, _at_door, _make_ad, _stocked_shop, hb, press, view  # noqa: F401 (fixtures)
from hypemarket import bizjobs, biz, cash, config as C, core, estate, inventory, jobs, market, rep, staff, stock, store, style, tailor, textile, views, work, worldinspect
from hypemarket.core import GameError
from hypemarket.texts import EN, FA


# ======================================================================== the periodic work
def test_the_tick_closes_an_expired_ad_and_gives_the_budget_back_once(hb):
    E = hb.E
    w = SH.workshop(E, fund=2000, machines=0, cutting=False)
    bid = w["id"]
    till0 = biz.get(E.db, bid)["till"]
    _make_ad(hb, bid)
    ad = market.of_branch(E.db, bid, open_only=True)[0]
    assert biz.get(E.db, bid)["till"] < till0
    assert bizjobs.tick(E.db)["ads"] == 0                                                                    # not yet
    hb.advance(ad["expires_at"] - core.now() + 1)
    assert bizjobs.tick(E.db)["ads"] == 1
    assert market.get(E.db, ad["id"])["state"] == "expired" and biz.get(E.db, bid)["till"] == till0 and biz.audit_till(E.db, bid) == []
    assert bizjobs.tick(E.db)["ads"] == 0 and biz.get(E.db, bid)["till"] == till0                            # the second tick finds nothing to do


def test_an_offer_nobody_answers_stops_blocking_its_slot_and_role(hb):
    E = hb.E
    b = SH.shop(E)
    bid = b["id"]
    c1 = staff.offer(E.db, A, bid, B, "sales", "fixed", 100, None, core.now())
    with pytest.raises(GameError) as e:
        staff.offer(E.db, A, bid, B, "sales", "fixed", 100, None, core.now())
    assert e.value.code == "staff_dup"
    hb.advance(C.STAFF_OFFER_TTL + 1)
    with pytest.raises(GameError) as e:
        staff.accept(E.db, B, c1["id"], core.now())
    assert e.value.code == "staff_gone"
    assert bizjobs.tick(E.db)["offers"] == 1 and staff.get(E.db, c1["id"])["state"] == "declined" and staff.slots_taken(E.db, bid) == 0
    c2 = staff.offer(E.db, A, bid, B, "sales", "fixed", 100, None, core.now())                               # the same person, the same role: free again
    assert c2["id"] != c1["id"] and c2["state"] == "offered"


def test_an_expired_offer_is_also_cleared_by_the_next_offer_without_the_tick(hb):
    E = hb.E
    b = SH.shop(E)
    c1 = staff.offer(E.db, A, b["id"], B, "sales", "fixed", 100, None, core.now())
    hb.advance(C.STAFF_OFFER_TTL + 1)
    c2 = staff.offer(E.db, A, b["id"], B, "sales", "fixed", 100, None, core.now())
    assert staff.get(E.db, c1["id"])["state"] == "declined" and c2["state"] == "offered"


def test_the_tick_voids_old_quotes_and_waiting_orders(hb):
    E = hb.E
    b = _stocked_shop(hb)
    bid = b["id"]
    tok = __import__("hypemarket.shopui", fromlist=["x"]).vtok(*__import__("hypemarket.shopui", fromlist=["x"]).variants(E.db, bid)[0])
    bizkit.rich(E, B, 1000)
    _at_door(hb, b, B)
    press(hb, B, f"bz:sq:{bid}:{tok}:{B}")
    press(hb, B, f"bz:sQ:{bid}:{tok}:1:{B}")
    assert E.db.q1("SELECT COUNT(*) c FROM store_orders WHERE state='quote'")["c"] == 1
    assert bizjobs.tick(E.db)["quotes"] == 0
    hb.advance(C.QUOTE_TTL + 1)
    assert bizjobs.tick(E.db)["quotes"] == 1 and E.db.q1("SELECT COUNT(*) c FROM store_orders WHERE state='quote'")["c"] == 0


def test_the_tick_collects_a_bots_finished_order_but_leaves_a_humans_for_the_human(hb):
    E = hb.E
    w = SH.workshop(E)
    bid = w["id"]
    SH.stock_fabric(E, w, 3, 24)
    staff.hire_bot(E.db, A, bid, "production", core.now())
    SH.at_prop(E, A, w)
    o1 = tailor.start(E.db, E.city, A, bid, "tank_top", 4, 3, core.now(), bot=True)
    hb.advance(o1["due_at"] - core.now() + 1)
    assert bizjobs.tick(E.db)["orders"] == 1
    assert tailor.get(E.db, o1["id"])["state"] == "done" and bizjobs.tick(E.db)["orders"] == 0
    assert sum(r["qty"] for r in stock.contents(E.db, bid, stock.WH) if r["item_id"] == "tank_top") == 4 and stock.audit(E.db, bid) == []
    o2 = tailor.start(E.db, E.city, A, bid, "tank_top", 4, 3, core.now())
    hb.advance(o2["due_at"] - core.now() + 1)
    assert bizjobs.tick(E.db)["orders"] == 0 and tailor.get(E.db, o2["id"])["state"] == "working"            # a person's order waits for the person
    tailor.claim(E.db, A, o2["id"], core.now())
    assert tailor.get(E.db, o2["id"])["state"] == "done"


def _employ(hb, b, who=B, role="sales", value=100):
    c = staff.offer(hb.E.db, A, b["id"], who, role, "fixed", value, None, core.now())
    return staff.accept(hb.E.db, who, c["id"], core.now())


def test_payroll_runs_every_few_hours_pays_what_accrued_once_and_is_safe_to_repeat(hb):
    E = hb.E
    b = SH.shop(E, fund=2000)
    bid = b["id"]
    ct = _employ(hb, b)
    hb.advance(86400)
    owed = staff.owed_total(E.db, staff.get(E.db, ct["id"]), core.now())
    assert owed > 0
    cash0, till0 = cash.balance(E.db, B), biz.get(E.db, bid)["till"]
    out = bizjobs.tick(E.db)
    assert out["payroll"] is not None and out["payroll"]["paid"] == owed and cash.balance(E.db, B) == cash0 + owed and biz.get(E.db, bid)["till"] == till0 - owed
    again = bizjobs.tick(E.db)                                                                               # not due for hours: nothing is even looked at
    assert again["payroll"] is None and cash.balance(E.db, B) == cash0 + owed
    forced = bizjobs.tick(E.db, force_payroll=True)                                                          # looked at again: nothing new has accrued
    assert forced["payroll"]["paid"] == 0 or forced["payroll"]["paid"] < owed
    hb.advance(bizjobs.PAYROLL_EVERY)
    nxt = bizjobs.tick(E.db)
    assert nxt["payroll"] is not None and nxt["payroll"]["paid"] > 0 and biz.audit_till(E.db, bid) == []


def test_payroll_with_an_empty_till_pays_nothing_and_loses_nothing(hb):
    E = hb.E
    b = SH.shop(E, fund=1)
    bid = b["id"]
    biz.withdraw(E.db, A, bid, biz.get(E.db, bid)["till"], "empty", core.now())
    ct = _employ(hb, b)
    hb.advance(2 * 86400)
    out = bizjobs.tick(E.db)
    assert out["payroll"]["paid"] == 0 and out["payroll"]["left"] > 0
    assert staff.owed_total(E.db, staff.get(E.db, ct["id"]), core.now()) > 0
    biz.fund(E.db, A, bid, 5_000_00, "t-late", core.now())                                                  # the owner tops up: the next run pays what was owed
    before = cash.balance(E.db, B)
    bizjobs.tick(E.db, force_payroll=True)
    assert cash.balance(E.db, B) > before


def test_the_payroll_reputation_cannot_be_farmed_by_asking_often(hb):
    E = hb.E
    b = SH.shop(E, fund=2000)
    bid = b["id"]
    _employ(hb, b, value=500_00)
    days = set()
    for _ in range(6):
        hb.advance(1800)
        staff.settle(E.db, bid, core.now())
        days.add(core.now() // 86400)
    got = E.db.q1("SELECT COUNT(*) c FROM rep_log WHERE branch_id=? AND kind='payroll'", (bid,))["c"]
    assert 1 <= got <= len(days) <= 2


def test_the_background_loop_runs_the_tick_and_flushes_the_messages(hb):
    E = hb.E
    b = SH.shop(E)
    staff.offer(E.db, A, b["id"], B, "sales", "fixed", 100, None, core.now())
    got = _tick(hb)
    assert len(got.get(B, [])) == 1 and "{" not in got[B][0] and not E.db.outbox
    assert _tick(hb) == {}                                                                                   # nothing is sent twice


def test_the_job_loop_list_has_the_business_tick():
    import inspect
    assert "business_tick" in inspect.getsource(jobs.start_jobs) or '"business"' in inspect.getsource(jobs.start_jobs)


# ======================================================================== private messages
def _dms(hb, before):
    out: dict = {}
    for c in hb.session.calls[before:]:
        if isinstance(c, SendMessage) and c.chat_id > 0:                                                     # private messages only (a group's own replies are not DMs)
            out.setdefault(c.chat_id, []).append(c.text)
    return out


def _tick(hb):
    """Run the business loop once; returns the messages it sent as {chat id: [text]}."""
    before = len(hb.session.calls)
    hb.run(jobs.business_tick(hb.app))
    return _dms(hb, before)


def test_an_offer_reaches_the_employee_with_the_branch_the_role_and_the_pay(hb):
    E = hb.E
    b = SH.shop(E)
    staff.offer(E.db, A, b["id"], B, "sales", "fixed", 150, None, core.now())
    got = _tick(hb).get(B, [])
    assert len(got) == 1
    text = got[0]
    assert "NIKE #1" in text.upper() and FA["bz.role.sales"] in text and "$۱٫۵۰" in text and "{" not in text
    E.db.x("UPDATE users SET lang='en' WHERE id=?", (B,))
    staff.offer(E.db, A, b["id"], D, "warehouse", "percent", 250, None, core.now())
    E.db.x("UPDATE users SET lang='en' WHERE id=?", (D,))
    en = _tick(hb)[D][0]
    assert EN["bz.role.warehouse"] in en and "2.5" in en.replace("٫", ".") and "{" not in en


def test_a_waiting_purchase_reaches_the_owner_and_the_approvers_but_not_the_buyer(hb):
    E = hb.E
    b = _stocked_shop(hb, price=300)
    bid = b["id"]
    c = staff.offer(E.db, A, bid, D, "manager", "fixed", 0, ("approve",), core.now())
    staff.accept(E.db, D, c["id"], core.now())
    tok = __import__("hypemarket.shopui", fromlist=["x"]).vtok(*__import__("hypemarket.shopui", fromlist=["x"]).variants(E.db, bid)[0])
    bizkit.rich(E, B, 1000)
    _at_door(hb, b, B)
    _tick(hb)                                                                                                # (flush the offer message first)
    press(hb, B, f"bz:sq:{bid}:{tok}:{B}")
    text, datas = view(press(hb, B, f"bz:sQ:{bid}:{tok}:2:{B}"))
    before = len(hb.session.calls)
    press(hb, B, next(d for d in datas if d.startswith("bz:sC:")))                                           # (the handler sends the messages itself; the loop would find nothing left)
    msgs = _dms(hb, before)
    assert _tick(hb) == {}
    for who in (A, D):
        got = msgs.get(who, [])
        assert len(got) == 1 and "NIKE #1" in got[0].upper() and "$۶٫۰۰" in got[0] and "{" not in got[0], (who, got)
    assert B not in msgs
    before = len(hb.session.calls)
    press(hb, B, next(d for d in datas if d.startswith("bz:sC:")))                                           # pressing confirm again does not message anybody again
    assert _dms(hb, before) == {} and _tick(hb) == {}


def test_every_business_message_formats_in_both_languages_without_a_stray_placeholder():
    kw_pending = {"brand": "nike", "seq": 2, "item_id": "tank_top", "data": '{"b":"nike","q":3}', "qty": 3, "cents": 90000}
    kw_offer = {"who": "Ali <b>x</b>", "role": "manager", "brand": "balenciaga", "seq": 1, "pay_type": "percent", "pay_value": 1250}
    for lang in ("fa", "en"):
        for key, kw in (("store_pending", kw_pending), ("staff_offer", kw_offer)):
            text = views.dm_text(lang, key, kw)
            assert "{" not in text and "}" not in text and "<b>x</b>" not in text, (lang, key, text)


# ======================================================================== the outfit and the tips
def test_the_style_bonus_only_adds_tips_and_never_takes_one_away():
    seeds = [(7, 51001, i) for i in range(1, 4001)]
    base = [work.snapp_tip(*s) for s in seeds]
    same = [work.snapp_tip(*s, 0) for s in seeds]
    assert base == same                                                                                      # no bonus: exactly the old behaviour
    top = [work.snapp_tip(*s, C.STYLE_TIP_BPS_MAX) for s in seeds]
    assert all(t2 >= 1 for t0, t2 in zip(base, top) if t0) and all(t2 == t0 for t0, t2 in zip(base, top) if t0)   # a ride that tipped tips the same
    assert all(0 <= x <= C.SNAPP_TIP_MAX_CENTS for x in top)
    num, den = C.SNAPP_TIP_ODDS
    share = sum(1 for x in top if x) / len(top)
    assert abs(share - (num / den + C.STYLE_TIP_BPS_MAX / 10000)) < 0.04
    half = [work.snapp_tip(*s, C.STYLE_TIP_BPS_MAX // 2) for s in seeds]
    assert sum(1 for x in base if x) < sum(1 for x in half if x) < sum(1 for x in top if x)
    assert all(h >= 0 for h in half) and all(not t0 or h == t0 for t0, h in zip(base, half))
    assert {work.snapp_tip(*s, 10 ** 9) for s in seeds[:50]} <= set(range(0, C.SNAPP_TIP_MAX_CENTS + 1))   # an absurd bonus is still bounded


def _dress(E, uid, q=5):
    for item in ("tshirt", "jeans", "sneakers", "cap"):
        SH.wear(E, uid, item, q)


def test_the_outfit_is_saved_with_the_ride_and_the_tip_follows_the_saved_rating(E):
    A_ = 51001
    _dress(E, A_)
    pts = style.rating(E.db, A_)["points"]
    assert pts > 50
    task = work.snapp_request(E.db, E.city, A_, E.now[0])
    assert work.data_of(task)["style"] == pts
    # taking the clothes off afterwards changes nothing about this ride's tip
    for slot in list(inventory.worn(E.db, A_)):
        inventory.unequip(E.db, A_, slot)
    work.snapp_go(E.db, E.city, A_, task["id"], E.now[0])
    E.arrive(A_)
    work.snapp_pickup(E.db, E.city, A_, task["id"], E.now[0])
    E.arrive(A_)
    rc = work.snapp_settle(E.db, E.city, A_, task["id"], E.now[0])
    assert rc["tip"] == work.snapp_tip(E.city.base["seed"], A_, task["id"], style.tip_bonus_bps(pts))


def test_a_player_in_plain_clothes_gets_the_old_tip_and_no_style_is_saved(E):
    task, rc = ride_to_the_end(E)
    assert "style" not in work.data_of(task) and rc["tip"] == work.snapp_tip(E.city.base["seed"], 51001, task["id"])


# ======================================================================== the map's place panel
def test_the_place_panel_names_the_cotton_park_a_lot_for_sale_an_owned_lot_and_its_branch(hb):
    from hypemarket import bizmap
    E = hb.E
    park = E.city.spot(textile.site_id(E.city))
    for lang, key in (("fa", FA), ("en", EN)):
        lines = bizmap.inspect_lines(lang, E.db, E.city, park)
        assert lines and key["bz.ins.cotton"] in lines
    free = SH.K.free_props(E, "commercial", "both")[0]
    spot = E.city.spot(free["parcel_id"])
    line = bizmap.inspect_lines("fa", E.db, E.city, spot)
    assert len(line) == 1 and "$" in line[0] and "{" not in line[0]
    b = SH.shop(E)
    prop = estate.get(E.db, b["property_id"])
    lines = bizmap.inspect_lines("fa", E.db, E.city, E.city.spot(prop["parcel_id"]))
    assert len(lines) == 2 and "NIKE #1" in lines[1].upper() and "{" not in "".join(lines)
    assert bizmap.inspect_lines("fa", E.db, E.city, None) == []


def test_the_map_object_screen_shows_those_lines_and_still_works_without_a_database(hb):
    from hypemarket import mapui, presence, world
    E = hb.E
    w = world.get_world(E.db, CH)
    st = presence.stand(E.db, E.city, A, core.now())
    b = SH.shop(E)
    prop = estate.get(E.db, b["property_id"])
    ref = prop["parcel_id"]
    with_db = mapui.screen_object("fa", w, E.city, "sid", st, ref, "m", db=E.db)
    without = mapui.screen_object("fa", w, E.city, "sid", st, ref, "m")
    assert with_db is not None and without is not None
    assert "NIKE #1" in with_db.text.upper() and "NIKE #1" not in without.text.upper()
