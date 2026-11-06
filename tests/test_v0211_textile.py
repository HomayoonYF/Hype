"""v0.211, part 3: the cotton job, fabric, selling it to the game, and the purchase ads, on real generated cities.

Covered: one cotton site per city (the park parcel, pure); grades unlocked by the Textile skill and longer for better fabric; the numbers fixed at the start; real arrival and real time (no output
before the server clock says so, none from repeated presses, the worker held at the site); collecting once (the fabric with its quality and source, XP and skill once each, a full bag leaves the batch
waiting); giving up pays nothing; selling to the game at the fixed price atomically; purchase ads (the budget reserved from the till, nobody can fill more than is wanted, two connections racing for the
same pieces, expiry and cancelling refund, the branch's own people refused, only a workshop may ask for fabric). A real Telegram client was not used."""
import copy
import threading

import pytest

import bizkit as K
import charkit
import test_life_jobs as LJ
from hypemarket import biz, brands, cash, charxp, config as C, core, estate, inventory, items, market, presence, skills, stock, textile, work, world, worldplaces
from hypemarket.core import GameError
from hypemarket.db import Database

layout = LJ.layout
layout2 = LJ.layout2
A, B, CUST = K.OWNER, K.WORKER, K.CUSTOMER


def at_park(E, uid):
    return K.teleport(E, uid, textile.site_id(E.city))


def work_batch(E, uid, grade=1):
    at_park(E, uid)
    task, started = textile.start(E.db, E.city, uid, grade, E.now[0])
    assert started
    return task


def give_skill(E, uid, skill, level):
    """Put a player at `level` of a skill through the real grant (the log keeps the books)."""
    w = skills.need(skill, level)
    skills.grant_work(E.db, uid, skill, w, "test:setup", f"{skill}:{level}", E.now[0])
    assert skills.level_of(E.db, uid, skill) >= level


# ============================================================================================== the site
def test_every_city_has_one_cotton_site_and_it_is_its_park(layout, layout2):
    for lay in (layout, layout2):
        E = K.env(lay)
        sid = textile.site_id(E.city)
        parks = [p for p in E.city.layout["parcels"] if p["zone"] == "park"]
        assert len(parks) == 1 and sid == parks[0]["id"] and textile.site(E.city)["id"] == sid
        assert E.city.spot(sid) is not None
        assert textile.site_id(E.city) == sid                                                       # pure: the same every time


def test_the_site_is_never_a_property_and_the_layout_is_untouched(layout):
    E = K.env(layout)
    sid = textile.site_id(E.city)
    assert sid not in {p["parcel_id"] for p in estate.listing(E.db, E.city.id)}
    w = E.db.q1("SELECT layout FROM worlds WHERE id=?", (E.city.id,))["layout"]
    assert textile.site_id(E.city) == sid and E.db.q1("SELECT layout FROM worlds WHERE id=?", (E.city.id,))["layout"] == w


def test_a_city_without_a_park_has_no_site(layout, monkeypatch):
    E = K.env(layout)
    E.city.layout["parcels"] = [dict(p, zone="commercial") if p["zone"] == "park" else p for p in E.city.layout["parcels"]]
    assert textile.site(E.city) is None
    at = K.teleport(E, A, E.city.mcd_id)
    with pytest.raises(GameError) as e:
        textile.start(E.db, E.city, A, 1, E.now[0])
    assert e.value.code == "cotton_nosite"


# ============================================================================================== what a batch is worth
def test_the_grades_need_skill_and_better_fabric_takes_much_longer(layout):
    E = K.env(layout)
    secs = [textile.terms(E.db, A, g)["base_seconds"] for g in textile.grades()]
    units = [textile.terms(E.db, A, g)["base_units"] for g in textile.grades()]
    assert secs == sorted(secs) and len(set(secs)) == 5 and secs[-1] >= 8 * secs[0]
    assert units == sorted(units)
    assert textile.unlocked(E.db, A) == (1,)
    for g in textile.grades():
        t = textile.terms(E.db, A, g)
        assert (t["open"], t["seconds"], t["units"]) == (g == 1, t["base_seconds"], t["base_units"])           # level 0: exactly the base terms
        assert t["price"] == C.FABRIC_GAME_CENTS[g] and t["xp"] == C.COTTON_XP[g]
    needs = [C.COTTON_GRADES[g][0] for g in textile.grades()]
    assert needs == sorted(needs) and needs[0] == 0 and needs[-1] < C.SKILL_MAX


def test_skill_shortens_the_time_and_raises_the_yield_within_the_limits(layout):
    E = K.env(layout)
    base = textile.terms(E.db, A, 1)
    give_skill(E, A, "textile", 500)
    mid = textile.terms(E.db, A, 1)
    give_skill(E, A, "textile", 1000)
    top = textile.terms(E.db, A, 1)
    assert top["level"] == 1000 and base["seconds"] > mid["seconds"] > top["seconds"] >= base["base_seconds"] * (10000 - C.COTTON_SPEED_BPS_MAX) // 10000
    assert base["units"] <= mid["units"] <= top["units"] <= base["base_units"] * (10000 + C.COTTON_YIELD_BPS_MAX) // 10000
    assert textile.unlocked(E.db, A) == (1, 2, 3, 4, 5)
    with pytest.raises(GameError) as e:
        textile.terms(E.db, A, 9)
    assert e.value.code == "cotton_grade"


def test_a_cotton_hour_is_worth_about_what_the_other_jobs_pay(layout):
    for g in textile.grades():
        need, secs, units = C.COTTON_GRADES[g]
        per_hour = units * C.FABRIC_GAME_CENTS[g] * 3600 / secs
        mcd = C.MCD_WAGE_CENTS * 3600 / C.MCD_SHIFT_SECONDS
        assert 0.8 * mcd <= per_hour <= 1.4 * mcd, (g, per_hour, mcd)


# ============================================================================================== starting
def test_the_batch_needs_standing_in_the_park(layout):
    E = K.env(layout)
    K.teleport(E, A, E.city.mcd_id)
    with pytest.raises(GameError) as e:
        textile.start(E.db, E.city, A, 1, E.now[0])
    assert e.value.code == "job_far" and work.current(E.db, A) is None
    at_park(E, A)
    task, started = textile.start(E.db, E.city, A, 1, E.now[0])
    assert started and task["job"] == "cotton" and task["stage"] == "working" and task["due_at"] == E.now[0] + C.COTTON_GRADES[1][1] and int(task["reward"]) == C.COTTON_GRADES[1][2]
    d = work.data_of(task)
    assert d["place"] == textile.site_id(E.city) and d["grade"] == 1 and d["units"] == task["reward"] and d["secs"] == C.COTTON_GRADES[1][1]


def test_a_locked_grade_is_refused_and_a_second_press_changes_nothing(layout):
    E = K.env(layout)
    at_park(E, A)
    with pytest.raises(GameError) as e:
        textile.start(E.db, E.city, A, 5, E.now[0])
    assert e.value.code == "cotton_locked" and work.current(E.db, A) is None
    t1, s1 = textile.start(E.db, E.city, A, 1, E.now[0])
    E.adv(30)
    t2, s2 = textile.start(E.db, E.city, A, 1, E.now[0])
    assert (s1, s2) == (True, False) and t1["id"] == t2["id"] and t2["due_at"] == t1["due_at"]
    t3, s3 = textile.start(E.db, E.city, A, 2, E.now[0])                                             # another grade while one runs: still the same batch
    assert not s3 and t3["id"] == t1["id"]
    assert E.db.q1("SELECT COUNT(*) c FROM work_tasks WHERE user_id=?", (A,))["c"] == 1


def test_a_running_batch_blocks_every_other_job(layout):
    E = K.env(layout)
    work_batch(E, A)
    with pytest.raises(GameError) as e:
        work.mcd_start(E.db, E.city, A, E.now[0])
    assert e.value.code == "job_busy"
    with pytest.raises(GameError) as e:
        work.snapp_request(E.db, E.city, A, E.now[0])
    assert e.value.code == "job_busy"


def test_the_worker_stays_at_the_site_until_the_batch_is_done(layout):
    E = K.env(layout)
    work_batch(E, A)
    out = presence.start_trip(E.db, E.city, A, E.city.mcd_id, E.now[0])
    assert out.status == "shift"
    held = work.shift_hold(E.db, A, E.now[0])
    assert held[0] == textile.site_id(E.city) and held[1] == C.COTTON_GRADES[1][1]
    E.adv(C.COTTON_GRADES[1][1])
    assert work.shift_hold(E.db, A, E.now[0]) is None                                                # done: free to go
    assert presence.start_trip(E.db, E.city, A, E.city.mcd_id, E.now[0]).status in ("started",)


def test_nothing_is_made_before_the_server_clock_says_so(layout):
    E = K.env(layout)
    t = work_batch(E, A)
    for later in (0, 1, C.COTTON_GRADES[1][1] - 1):
        E.now[0] = LJ.T0 + later
        with pytest.raises(GameError) as e:
            textile.claim(E.db, A, t["id"], E.now[0])
        assert e.value.code == "job_notready"
    assert textile.fabric_count(E.db, A) == 0 and textile.state(work.get_task(E.db, t["id"], A), E.now[0]) == "working"
    assert skills.work_of(E.db, A, "textile") == 0 and charxp.get(E.db, A) == 0


# ============================================================================================== collecting
def test_collecting_gives_the_fabric_xp_and_skill_once(layout):
    E = K.env(layout)
    t = work_batch(E, A)
    E.adv(C.COTTON_GRADES[1][1])
    assert textile.state(work.get_task(E.db, t["id"], A), E.now[0]) == "ready"
    xp0 = charxp.get(E.db, A)
    rc = textile.claim(E.db, A, t["id"], E.now[0])
    units = C.COTTON_GRADES[1][2]
    assert rc["units"] == units and rc["grade"] == 1 and rc["xp"] == C.COTTON_XP[1] and rc["fabric"] == units
    assert charxp.get(E.db, A) == xp0 + C.COTTON_XP[1]
    assert skills.work_of(E.db, A, "textile") == skills.milli(C.COTTON_GRADES[1][1])
    v = textile.fabric_variants(E.db, A)
    assert v == [{"data": '{"q":1,"src":"cotton"}', "q": 1, "qty": units}]
    assert items.display_name("fa", "fabric", v[0]["data"]) == "پارچه" and "⭐☆☆☆☆" in items.quality_line("fa", v[0]["data"]).replace("کیفیت: ", "")
    assert E.db.q1("SELECT source, ref, delta FROM item_log WHERE user_id=? AND item_id='fabric'", (A,))["source"] == "job:cotton"
    with pytest.raises(GameError) as e:
        textile.claim(E.db, A, t["id"], E.now[0] + 5)
    assert e.value.code == "job_claimed"
    assert textile.fabric_count(E.db, A) == units and charxp.get(E.db, A) == xp0 + C.COTTON_XP[1] and skills.work_of(E.db, A, "textile") == skills.milli(C.COTTON_GRADES[1][1])
    assert work.current(E.db, A) is None and work.get_task(E.db, t["id"], A)["state"] == "done"


def test_only_the_player_whose_batch_it_is_can_collect_it(layout):
    E = K.env(layout)
    t = work_batch(E, A)
    E.adv(C.COTTON_GRADES[1][1])
    with pytest.raises(GameError) as e:
        textile.claim(E.db, B, t["id"], E.now[0])
    assert e.value.code == "job_stale"
    mcd = work.get_task(E.db, work.mcd_start(E.db, K.teleport(E, B, E.city.mcd_id) and E.city, B, E.now[0])[0]["id"], B)
    with pytest.raises(GameError) as e:                                                              # another job's task is not a batch
        textile.claim(E.db, B, mcd["id"], E.now[0])
    assert e.value.code == "job_stale"
    assert textile.fabric_count(E.db, A) == 0


def test_a_full_bag_leaves_the_batch_waiting_and_nothing_changes(layout, monkeypatch):
    E = K.env(layout)
    t = work_batch(E, A)
    E.adv(C.COTTON_GRADES[1][1])
    monkeypatch.setattr(C, "INV_MAX_STACKS", 0)
    xp0, w0 = charxp.get(E.db, A), skills.work_of(E.db, A, "textile")
    with pytest.raises(GameError) as e:
        textile.claim(E.db, A, t["id"], E.now[0])
    assert e.value.code == "inv_full"
    assert work.get_task(E.db, t["id"], A)["state"] == "open" and charxp.get(E.db, A) == xp0 and skills.work_of(E.db, A, "textile") == w0 and textile.fabric_count(E.db, A) == 0
    monkeypatch.undo()
    assert textile.claim(E.db, A, t["id"], E.now[0])["units"] == C.COTTON_GRADES[1][2]


def test_the_terms_are_fixed_at_the_start_whatever_happens_to_the_skill_later(layout):
    E = K.env(layout)
    t = work_batch(E, A)
    units = int(t["reward"])
    give_skill(E, A, "textile", 1000)                                                                # a level-up in the middle of the batch (from other work)
    E.adv(C.COTTON_GRADES[1][1])
    rc = textile.claim(E.db, A, t["id"], E.now[0])
    assert rc["units"] == units == C.COTTON_GRADES[1][2]


def test_giving_up_pays_nothing_and_gives_no_skill(layout):
    E = K.env(layout)
    t = work_batch(E, A)
    E.adv(60)
    assert textile.give_up(E.db, A, t["id"], E.now[0]) is True
    assert textile.give_up(E.db, A, t["id"], E.now[0]) is False
    assert work.current(E.db, A) is None and textile.fabric_count(E.db, A) == 0 and skills.work_of(E.db, A, "textile") == 0 and charxp.get(E.db, A) == 0
    with pytest.raises(GameError) as e:
        textile.claim(E.db, A, t["id"], E.now[0] + 99999)
    assert e.value.code == "job_stale"


def test_skill_comes_only_from_finished_work(layout):
    E = K.env(layout)
    for _ in range(5):
        t = work_batch(E, A)
        for _ in range(4):
            with pytest.raises(GameError):
                textile.claim(E.db, A, t["id"], E.now[0])
        textile.give_up(E.db, A, t["id"], E.now[0])
    assert skills.work_of(E.db, A, "textile") == 0
    t = work_batch(E, A)
    E.adv(C.COTTON_GRADES[1][1])
    textile.claim(E.db, A, t["id"], E.now[0])
    assert skills.work_of(E.db, A, "textile") == skills.milli(C.COTTON_GRADES[1][1]) > 0


def test_a_better_grade_gives_better_fabric_and_more_xp(layout):
    E = K.env(layout)
    give_skill(E, A, "textile", 1000)
    t = work_batch(E, A, 5)
    assert t["due_at"] - t["started_at"] == textile.terms(E.db, A, 5)["seconds"] and work.data_of(t)["grade"] == 5
    E.adv(t["due_at"] - t["started_at"])
    rc = textile.claim(E.db, A, t["id"], E.now[0])
    assert rc["xp"] == C.COTTON_XP[5] and textile.fabric_by_quality(E.db, A) == {5: rc["units"]}


def test_a_restart_in_the_middle_of_a_batch_loses_nothing(layout, tmp_path):
    path = str(tmp_path / "cotton.db")
    db = Database(path)
    now = [LJ.T0]
    core.set_clock(lambda: now[0])
    LJ.worldmap_reset()
    for u in (A,):
        core.ensure_user(db, u, f"u{u}", f"User{u}", lang="fa")
        charkit.complete(db, u)
    w, _ = world.save_world(db, LJ.CH, copy.deepcopy(layout))
    city = worldplaces.load_city(db, w)
    presence.stand(db, city, A, now[0])
    spot = city.spot(textile.site_id(city))
    db.x("UPDATE world_players SET x=?, y=?, at_place=? WHERE world_id=? AND user_id=?", (spot.door.pt[0], spot.door.pt[1], spot.id, city.id, A))
    t, _ = textile.start(db, city, A, 1, now[0])
    db.close()
    db2 = Database(path)
    now[0] += C.COTTON_GRADES[1][1]
    assert textile.claim(db2, A, t["id"], now[0])["units"] == C.COTTON_GRADES[1][2]
    assert textile.fabric_count(db2, A) == C.COTTON_GRADES[1][2]


# ============================================================================================== selling to the game
def fabric(E, uid, q, n, src="test"):
    inventory.give(E.db, uid, "fabric", n, f"test:{src}", f"{uid}:{q}:{n}:{E.now[0]}:{len(E.ledger(uid))}:{src}", {"q": q, "src": "cotton"}, E.now[0])


def test_selling_fabric_to_the_game_pays_the_fixed_price_atomically(layout):
    E = K.env(layout)
    fabric(E, A, 3, 10)
    bal = cash.balance(E.db, A)
    rc = textile.sell_to_game(E.db, A, 3, 4, E.now[0])
    assert rc["cents"] == 4 * C.FABRIC_GAME_CENTS[3] and cash.balance(E.db, A) == bal + rc["cents"] == rc["balance"]
    assert textile.fabric_by_quality(E.db, A) == {3: 6}
    assert E.ledger(A, ("fabric_sale",))[0][2] == rc["cents"]


def test_selling_more_than_you_have_or_a_wrong_quality_changes_nothing(layout):
    E = K.env(layout)
    fabric(E, A, 3, 5)
    bal = cash.balance(E.db, A)
    for q, n, code in ((3, 6, "inv_few"), (2, 1, "inv_few"), (7, 1, "cotton_grade"), (3, 0, "bad_amount"), (3, -2, "bad_amount"), (3, True, "bad_amount"), (3, 1.5, "bad_amount")):
        with pytest.raises(GameError) as e:
            textile.sell_to_game(E.db, A, q, n, E.now[0])
        assert e.value.code == code, (q, n)
    assert cash.balance(E.db, A) == bal and textile.fabric_by_quality(E.db, A) == {3: 5}


def test_selling_takes_from_every_variant_of_that_quality_and_leaves_the_others(layout):
    E = K.env(layout)
    fabric(E, A, 2, 3)
    inventory.give(E.db, A, "fabric", 4, "test:gift", "g1", {"q": 2, "src": "gift"}, E.now[0])
    fabric(E, A, 4, 2)
    textile.sell_to_game(E.db, A, 2, 6, E.now[0])
    assert textile.fabric_by_quality(E.db, A) == {2: 1, 4: 2}


def test_a_failed_payout_gives_the_fabric_back(layout, monkeypatch):
    E = K.env(layout)
    fabric(E, A, 3, 5)

    def boom(*a, **k):
        raise GameError("cash_cap")
    monkeypatch.setattr(cash, "apply", boom)
    with pytest.raises(GameError):
        textile.sell_to_game(E.db, A, 3, 5, E.now[0])
    monkeypatch.undo()
    assert textile.fabric_by_quality(E.db, A) == {3: 5}


# ============================================================================================== purchase ads
def shop_with_workshop(E, uid=A, fund=2000, **kw):
    b = K.make_branch(E, uid, mode="both", fund=fund, **kw)
    return b


def test_an_ad_reserves_its_whole_budget_out_of_the_till(layout):
    E = K.env(layout)
    b = shop_with_workshop(E)
    till = biz.get(E.db, b["id"])["till"]
    ad = market.create(E.db, A, b["id"], 3, 90, 20, C.AD_TTL_CHOICES[1], E.now[0])
    assert (ad["min_q"], ad["unit_cents"], ad["qty_total"], ad["qty_left"], ad["reserved"], ad["state"]) == (3, 90, 20, 20, 1800, "open")
    assert biz.get(E.db, b["id"])["till"] == till - 1800 and biz.audit_till(E.db, b["id"]) == []
    assert ad["expires_at"] == E.now[0] + C.AD_TTL_CHOICES[1] and ad["material"] == "fabric"
    with pytest.raises(Exception):
        E.db.x("UPDATE material_ads SET reserved=5 WHERE id=?", (ad["id"],))                          # the table itself keeps reserved = left x price while open


def test_an_ad_is_refused_for_bad_terms_no_money_no_room_no_workshop_or_too_many(layout):
    E = K.env(layout)
    b = shop_with_workshop(E, fund=100)
    ttl = C.AD_TTL_CHOICES[0]
    for args, code in (((0, 90, 5, ttl), "ad_terms"), ((6, 90, 5, ttl), "ad_terms"), ((3, 0, 5, ttl), "ad_terms"), ((3, 10 ** 9, 5, ttl), "ad_terms"), ((3, 90, 0, ttl), "ad_terms"),
                       ((3, 90, 10 ** 6, ttl), "ad_terms"), ((3, 90, 5, 12345), "ad_terms"), ((True, 90, 5, ttl), "ad_terms"), ((3, 90.5, 5, ttl), "ad_terms")):
        with pytest.raises(GameError) as e:
            market.create(E.db, A, b["id"], *args, E.now[0])
        assert e.value.code == code, args
    with pytest.raises(GameError) as e:                                                              # $100 in the till: 5 x $30 does not fit
        market.create(E.db, A, b["id"], 3, 30_00, 5, ttl, E.now[0])
    assert e.value.code == "till_low"
    with pytest.raises(GameError) as e:
        market.create(E.db, A, b["id"], 3, 1, stock.free_warehouse(E.db, b) + 1, ttl, E.now[0])
    assert e.value.code == "wh_full"
    for _ in range(C.AD_MAX_OPEN):
        market.create(E.db, A, b["id"], 1, 10, 1, ttl, E.now[0])
    with pytest.raises(GameError) as e:
        market.create(E.db, A, b["id"], 1, 10, 1, ttl, E.now[0])
    assert e.value.code == "ad_open"
    assert E.db.q1("SELECT COUNT(*) c FROM material_ads")["c"] == C.AD_MAX_OPEN


def test_a_shop_without_a_workshop_cannot_ask_for_fabric(layout):
    E = K.env(layout)
    b = K.make_branch(E, A, mode="retail", fund=500)
    with pytest.raises(GameError) as e:
        market.create(E.db, A, b["id"], 1, 10, 1, C.AD_TTL_CHOICES[0], E.now[0])
    assert e.value.code == "ad_mode" and biz.get(E.db, b["id"])["till"] == 500_00


def test_a_seller_fills_an_ad_money_fabric_warehouse_and_reputation_move_together(layout):
    E = K.env(layout)
    b = shop_with_workshop(E)
    ad = market.create(E.db, A, b["id"], 3, 90, 20, C.AD_TTL_CHOICES[1], E.now[0])
    fabric(E, CUST, 4, 12)
    bal = cash.balance(E.db, CUST)
    res = market.fill(E.db, CUST, ad["id"], 4, 8, E.now[0])
    assert (res["qty"], res["cents"], res["left"]) == (8, 720, 12) and cash.balance(E.db, CUST) == bal + 720
    assert textile.fabric_by_quality(E.db, CUST) == {4: 4}
    assert stock.qty(E.db, b["id"], stock.WH, "fabric", {"q": 4, "src": "cotton"}) == 8 and stock.audit(E.db, b["id"]) == []
    got = market.get(E.db, ad["id"])
    assert (got["qty_left"], got["reserved"], got["state"]) == (12, 1080, "open")
    assert market.fills(E.db, ad["id"])[0]["total_cents"] == 720
    from hypemarket import rep
    assert rep.score(E.db, b["id"]) == C.REP_FILL
    assert E.ledger(CUST, ("ad_sale",))[0][2] == 720


def test_the_last_pieces_close_the_ad_and_a_filled_ad_takes_no_more(layout):
    E = K.env(layout)
    b = shop_with_workshop(E)
    ad = market.create(E.db, A, b["id"], 1, 50, 5, C.AD_TTL_CHOICES[0], E.now[0])
    fabric(E, CUST, 1, 9)
    market.fill(E.db, CUST, ad["id"], 1, 5, E.now[0])
    got = market.get(E.db, ad["id"])
    assert (got["state"], got["qty_left"], got["reserved"]) == ("filled", 0, 0)
    with pytest.raises(GameError) as e:
        market.fill(E.db, CUST, ad["id"], 1, 1, E.now[0])
    assert e.value.code == "ad_gone" and textile.fabric_by_quality(E.db, CUST) == {1: 4}


def test_an_ad_cannot_be_filled_with_too_little_quality_too_much_or_missing_fabric(layout):
    E = K.env(layout)
    b = shop_with_workshop(E)
    ad = market.create(E.db, A, b["id"], 3, 90, 10, C.AD_TTL_CHOICES[0], E.now[0])
    fabric(E, CUST, 2, 10)
    fabric(E, CUST, 4, 3)
    bal = cash.balance(E.db, CUST)
    for q, n, code in ((2, 5, "ad_quality"), (4, 11, "ad_left"), (4, 4, "inv_few"), (4, 0, "bad_amount"), (4, -1, "bad_amount"), (4, True, "bad_amount"), (9, 1, "ad_quality")):
        with pytest.raises(GameError) as e:
            market.fill(E.db, CUST, ad["id"], q, n, E.now[0])
        assert e.value.code == code, (q, n)
    assert cash.balance(E.db, CUST) == bal and market.get(E.db, ad["id"])["qty_left"] == 10 and stock.total(E.db, b["id"], stock.WH) == 0
    for bad in (999, 0, -1, None, "1", True):
        with pytest.raises(GameError) as e:
            market.fill(E.db, CUST, bad, 4, 1, E.now[0])
        assert e.value.code in ("ad_gone", "bad_amount")


def test_the_owner_and_the_staff_can_never_fill_their_own_ads(layout):
    from hypemarket import staff
    E = K.env(layout)
    b = shop_with_workshop(E)
    ad = market.create(E.db, A, b["id"], 1, 50, 5, C.AD_TTL_CHOICES[0], E.now[0])
    fabric(E, A, 3, 5)
    fabric(E, B, 3, 5)
    with pytest.raises(GameError) as e:
        market.fill(E.db, A, ad["id"], 3, 1, E.now[0])
    assert e.value.code == "ad_self"
    c = staff.offer(E.db, A, b["id"], B, "warehouse", "fixed", 100, None, E.now[0])
    with pytest.raises(GameError) as e:                                                              # even an offered contract is a tie to the branch
        market.fill(E.db, B, ad["id"], 3, 1, E.now[0])
    assert e.value.code == "ad_self"


def test_a_warehouse_without_room_refuses_the_delivery_and_keeps_everything(layout):
    E = K.env(layout)
    b = shop_with_workshop(E)
    ad = market.create(E.db, A, b["id"], 1, 10, stock.free_warehouse(E.db, b), C.AD_TTL_CHOICES[0], E.now[0])
    fabric(E, CUST, 3, 4)
    inventory.give(E.db, A, "tank_top", 1, "test:x", "x", {"q": 1}, E.now[0])
    # fill the warehouse by hand so that only 2 pieces fit
    free = stock.free_warehouse(E.db, b)
    stock.add(E.db, b, stock.WH, "tank_top", {"q": 1}, free - 2, "deposit", "fill", now=E.now[0])
    bal = cash.balance(E.db, CUST)
    with pytest.raises(GameError) as e:
        market.fill(E.db, CUST, ad["id"], 3, 3, E.now[0])
    assert e.value.code == "wh_full" and cash.balance(E.db, CUST) == bal and textile.fabric_by_quality(E.db, CUST) == {3: 4}
    assert market.fill(E.db, CUST, ad["id"], 3, 2, E.now[0])["qty"] == 2


def test_cancelling_and_expiry_return_the_unused_budget_once(layout):
    E = K.env(layout)
    b = shop_with_workshop(E)
    till = biz.get(E.db, b["id"])["till"]
    a1 = market.create(E.db, A, b["id"], 1, 100, 10, C.AD_TTL_CHOICES[0], E.now[0])
    a2 = market.create(E.db, A, b["id"], 1, 100, 10, C.AD_TTL_CHOICES[0], E.now[0])
    fabric(E, CUST, 1, 4)
    market.fill(E.db, CUST, a1["id"], 1, 4, E.now[0])
    assert market.cancel(E.db, A, a1["id"], E.now[0]) == 600
    assert biz.get(E.db, b["id"])["till"] == till - 2000 + 600
    with pytest.raises(GameError) as e:
        market.cancel(E.db, A, a1["id"], E.now[0])
    assert e.value.code == "ad_gone"
    E.adv(C.AD_TTL_CHOICES[0] + 1)
    assert market.sweep(E.db, E.now[0]) == 1 and market.sweep(E.db, E.now[0]) == 0
    got = market.get(E.db, a2["id"])
    assert (got["state"], got["reserved"]) == ("expired", 0)
    assert biz.get(E.db, b["id"])["till"] == till - 400 and biz.audit_till(E.db, b["id"]) == []         # the 4 pieces really bought ($4.00) are the only money gone
    with pytest.raises(GameError) as e:
        market.fill(E.db, CUST, a2["id"], 1, 1, E.now[0])
    assert e.value.code == "ad_gone"


def test_only_the_owner_or_an_ads_manager_takes_an_ad_down(layout):
    E = K.env(layout)
    b = shop_with_workshop(E)
    ad = market.create(E.db, A, b["id"], 1, 50, 5, C.AD_TTL_CHOICES[0], E.now[0])
    with pytest.raises(GameError) as e:
        market.cancel(E.db, CUST, ad["id"], E.now[0])
    assert e.value.code == "branch_denied"
    with pytest.raises(GameError) as e:
        market.create(E.db, CUST, b["id"], 1, 50, 5, C.AD_TTL_CHOICES[0], E.now[0])
    assert e.value.code == "branch_denied"


def test_the_board_lists_open_ads_by_city_and_hides_expired_ones(layout, layout2):
    E = K.env(layout)
    b = shop_with_workshop(E)
    a1 = market.create(E.db, A, b["id"], 2, 80, 5, C.AD_TTL_CHOICES[0], E.now[0])
    a2 = market.create(E.db, A, b["id"], 4, 120, 5, C.AD_TTL_CHOICES[1], E.now[0] + 1)
    rows = market.board(E.db, E.now[0] + 1)
    assert [r["id"] for r in rows] == [a2["id"], a1["id"]] and rows[0]["brand"] == "nike" and rows[0]["world_id"] == E.city.id
    assert [r["id"] for r in market.board(E.db, E.now[0] + 1, world_id=E.city.id + 99)] == []
    assert [r["id"] for r in market.board(E.db, E.now[0] + 1, min_q=2)] == [a1["id"]]                 # fabric of 2 stars can fill only the first
    assert [r["id"] for r in market.board(E.db, E.now[0] + 1, min_q=4)] == [a2["id"], a1["id"]]
    assert market.count_open(E.db, E.now[0] + 1) == 2
    assert [r["id"] for r in market.board(E.db, E.now[0] + C.AD_TTL_CHOICES[0] + 5)] == [a2["id"]]      # the first one expired and was swept


def test_two_connections_racing_for_the_last_pieces_never_oversell(layout, tmp_path):
    path = str(tmp_path / "ads.db")
    db = Database(path)
    core.set_clock(lambda: K.T0)
    LJ.worldmap_reset()
    for u in (A, B, CUST):
        core.ensure_user(db, u, f"u{u}", f"User{u}", lang="fa")
        charkit.complete(db, u)
    w, _ = world.save_world(db, LJ.CH, copy.deepcopy(layout))
    city = worldplaces.load_city(db, w)
    estate.ensure(db, city, K.T0)
    p = [x for x in estate.listing(db, city.id) if x["zone"] == "commercial"][0]
    cash.ensure_account(db, A)
    cash.apply(db, A, 100_000_00, "grant", "r", now=K.T0)
    spot = city.spot(p["parcel_id"])
    presence.stand(db, city, A, K.T0)
    db.x("UPDATE world_players SET x=?, y=?, at_place=? WHERE world_id=? AND user_id=?", (spot.door.pt[0], spot.door.pt[1], spot.id, city.id, A))
    estate.buy(db, city, presence.stand(db, city, A, K.T0), A, p["id"], K.T0)
    estate.set_mode(db, A, p["id"], "both", K.T0)
    brands.register(db, A, "nike", K.T0)
    b = biz.open_branch(db, A, p["id"], "nike", K.T0)
    biz.fund(db, A, b["id"], 1000_00, "f", K.T0)
    ad = market.create(db, A, b["id"], 1, 100, 10, C.AD_TTL_CHOICES[1], K.T0)
    for u in (B, CUST):
        inventory.give(db, u, "fabric", 10, "test:x", f"s{u}", {"q": 2, "src": "cotton"}, K.T0)
    db.close()
    outcomes, errors = {}, []
    barrier = threading.Barrier(2)

    def run(u):
        try:
            d = Database(path)
            barrier.wait()
            try:
                market.fill(d, u, ad["id"], 2, 7, K.T0)
                outcomes[u] = "filled"
            except GameError as e:
                outcomes[u] = e.code
        except Exception as e:                                                                       # noqa: BLE001
            errors.append(repr(e))
    th = [threading.Thread(target=run, args=(u,)) for u in (B, CUST)]
    [x.start() for x in th]
    [x.join() for x in th]
    assert not errors, errors
    assert sorted(outcomes.values()) == ["ad_left", "filled"]                                      # 7 + 7 > 10: exactly one of them gets the pieces
    d = Database(path)
    got = market.get(d, ad["id"])
    assert (got["qty_left"], got["reserved"]) == (3, 300)
    assert d.q1("SELECT COUNT(*) c FROM ad_fills")["c"] == 1 and stock.total(d, b["id"], stock.WH) == 7 and stock.audit(d, b["id"]) == []
    winner = B if outcomes[B] == "filled" else CUST
    loser = CUST if winner == B else B
    assert cash.balance(d, winner) == C.CASH_START_CENTS + 700 and cash.balance(d, loser) == C.CASH_START_CENTS
    assert inventory.count(d, winner, "fabric") == 3 and inventory.count(d, loser, "fabric") == 10


def test_closing_a_branch_returns_the_ads_budget_and_the_warehouse_to_the_owner(layout):
    E = K.env(layout)
    b = shop_with_workshop(E)
    ad = market.create(E.db, A, b["id"], 1, 100, 10, C.AD_TTL_CHOICES[0], E.now[0])
    fabric(E, CUST, 1, 4)
    market.fill(E.db, CUST, ad["id"], 1, 4, E.now[0])
    bal = cash.balance(E.db, A)
    till = biz.get(E.db, b["id"])["till"]
    biz.close_branch(E.db, A, b["id"], E.now[0] + 1)
    assert cash.balance(E.db, A) == bal + till + 600                                                  # the till and the 6 pieces' unused budget
    assert market.get(E.db, ad["id"])["state"] == "cancelled" and textile.fabric_by_quality(E.db, A) == {1: 4}
    assert stock.total(E.db, b["id"], stock.WH) == 0 and stock.audit(E.db, b["id"]) == []
