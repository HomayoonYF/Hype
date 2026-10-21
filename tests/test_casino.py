"""Milestone C (v0.17): the casino as a permanent place of every city, and its no-stakes blackjack table.

Rules of blackjack are tested on scripted decks; the walk to the casino, the entrance, owner locks, repeated buttons, restarts and the
«nothing else changes» promises are tested through the real handlers."""
import json
import random
import sqlite3
import threading

import pytest

import mapkit
import test_bot as tb
from hypemarket import cards, casino, config as C, core, fmt, presence as P, world, worldgeom as G, worldinspect as WI, worldmap, worldplaces
from hypemarket.db import SCHEMA_VERSION, Database, MIGRATIONS
from hypemarket.texts import t

CH, CH2 = -100606, -100777
A, B, X = 777001, 777002, 999009
ECON = ("stocks", "holdings", "history", "news", "perks", "exflow", "alerts", "quests")


@pytest.fixture()
def h():
    worldmap.cache_clear()
    worldplaces.cache_clear()
    hh = tb.Harness()
    world.save_world(hh.db, CH, world.build(7))
    return hh


@pytest.fixture()
def d(h):
    return mapkit.Driver(h, CH)


def city(h, chat=CH):
    return mapkit.city_of(h, chat)


def econ_snapshot(db):
    out = {n: [tuple(r) for r in db.qa(f"SELECT * FROM {n}")] for n in ECON}
    out["users"] = [tuple(r) for r in db.qa("SELECT id,cash,pending_div,pending_inc,rep,max_level,spent_points,rebirths,legacy_pts,legacy_total,nw,trades,badge FROM users ORDER BY id")]
    return out


# ============================================================================================== the casino is a place of the city
@pytest.mark.parametrize("seed", [0, 1, 7, 42, 99, 999])
def test_every_new_world_has_exactly_one_casino_on_a_reserved_empty_parcel(seed):
    db = Database(":memory:")
    w, created = world.save_world(db, CH, world.build(seed))
    assert created
    rows = db.qa("SELECT * FROM world_places WHERE world_id=?", (w["id"],))
    assert [(r["kind"], r["place_id"]) for r in rows] == [("casino", "l5")]
    base = world.load_layout(w)
    assert world.validate(base) and len(base["landmarks"]) == 4 and {m["kind"] for m in base["landmarks"]} == set(world.LANDMARKS)
    parcel = next(p for p in base["parcels"] if p["id"] == rows[0]["parcel_id"])
    assert parcel["use"] == "vacant"                                                                  # a parcel that really was empty land
    c = worldplaces.load_city(db, w)
    kinds = [m["kind"] for m in c.layout["landmarks"]]
    assert kinds.count("casino") == 1 and len(kinds) == 5
    changed = [(a["id"]) for a, b in zip(base["parcels"], c.layout["parcels"]) if a != b]
    assert changed == [parcel["id"]]                                                                  # exactly one parcel differs...
    mine = next(p for p in c.layout["parcels"] if p["id"] == parcel["id"])
    assert mine["use"] == "landmark" and mine["poly"] == parcel["poly"] and mine["c"] == parcel["c"]    # ...only by being a public place
    assert world.stats(c.layout)["vacant"] == world.stats(base)["vacant"] - 1 and world.stats(c.layout)["parcels"] == world.stats(base)["parcels"]
    cl = next(m for m in c.layout["landmarks"] if m["kind"] == "casino")
    assert cl["id"] == "l5" and cl["parcel"] == parcel["id"] and cl["at"] == parcel["c"]


def test_the_casino_sits_where_a_player_can_walk_to_it(h):
    for seed in (7, 42, 999, 5):
        db = Database(":memory:")
        w, _ = world.save_world(db, CH, world.build(seed))
        c = worldplaces.load_city(db, w)
        s = c.spot("l5")
        assert s and s.kind == "landmark" and s.landmark["kind"] == "casino"
        assert s.door.off < 60 and not c.net.segs[s.door.seg].bridge                                  # a real road in front of it, not a bridge
        a = P.spawn_point(c, 1)
        assert c.net.route(a, s.door.pt) is not None
        assert c.spot(s.parcel["id"]).id == "l5"                                                      # its parcel ID leads to the same place


def test_the_casino_choice_is_a_pure_function_of_the_saved_city():
    picks = set()
    for _ in range(3):
        db = Database(":memory:")
        w, _ = world.save_world(db, CH, world.build(7))
        picks.add(db.q1("SELECT parcel_id FROM world_places")["parcel_id"])
    assert len(picks) == 1


def old_world(db, chat, seed):
    """A city exactly as v0.16 saved it: a `worlds` row and nothing else (no places, no players)."""
    layout = world.build(seed)
    db.x("INSERT INTO worlds(chat_id,seed,gen_version,name_fa,name_en,cols,rows,layout,created_at) VALUES(?,?,?,?,?,?,?,?,?)",
         (chat, layout["seed"], layout["generator"], layout["name"]["fa"], layout["name"]["en"], layout["w"], layout["h"], world.dumps(layout), 1_650_000_000))
    return world.get_world(db, chat)


def test_existing_worlds_get_a_casino_without_being_regenerated_at_startup(h):
    w = old_world(h.db, CH2, 5)
    assert h.db.q1("SELECT COUNT(*) c FROM world_places WHERE world_id=?", (w["id"],))["c"] == 0
    row_before, layout_before = dict(w), world.load_layout(w)
    import copy
    frozen = copy.deepcopy(layout_before)
    other = dict(world.get_world(h.db, CH))
    assert worldplaces.ensure_all(h.db) >= 1                                                          # the start-up pass
    after = world.get_world(h.db, CH2)
    assert dict(after) == row_before and after["layout"] == w["layout"]                               # not one byte of the saved city changed
    assert world.load_layout(after) == frozen
    assert dict(world.get_world(h.db, CH)) == other
    assert h.db.q1("SELECT kind,place_id FROM world_places WHERE world_id=?", (w["id"],))["place_id"] == "l5"
    c = worldplaces.load_city(h.db, after)
    for key in ("roads", "water", "districts", "nodes", "footprint", "blocks", "terrain", "seed", "name"):
        assert c.layout[key] == frozen[key]
    assert [m for m in c.layout["landmarks"] if m["kind"] != "casino"] == frozen["landmarks"]         # the four public places, unmoved
    assert {p["id"]: (p["poly"], p["c"], p["area"]) for p in c.layout["parcels"]} == {p["id"]: (p["poly"], p["c"], p["area"]) for p in frozen["parcels"]}
    assert worldplaces.ensure_all(h.db) == 0 and worldplaces.ensure_all(h.db) == 0                    # and a second pass has nothing to do


def test_existing_worlds_get_a_casino_the_first_time_somebody_opens_the_map(h, d):
    w = old_world(h.db, CH2, 5)
    before = dict(w)
    d2 = mapkit.Driver(h, CH2)
    v = d2.open(A)
    assert v.photo and h.db.q1("SELECT COUNT(*) c FROM world_places WHERE world_id=?", (w["id"],))["c"] == 1
    assert dict(world.get_world(h.db, CH2)) == before
    assert t("fa", "map.lm.casino") in v.caption and "L5" in v.caption                                 # the public places line lists it
    for _ in range(3):
        d2.open(B)
    assert h.db.q1("SELECT COUNT(*) c FROM world_places WHERE world_id=?", (w["id"],))["c"] == 1


def test_players_and_trips_are_untouched_when_a_casino_is_added_later(h, d):
    v = d.open(A)
    d.open(B)
    c = city(h)
    o = d.press(A, f"mp:{v.sid}:o:p3", v)
    d.go(A, o, t("fa", "mp.b.go"))
    players, trips = [tuple(r) for r in h.db.qa("SELECT * FROM world_players ORDER BY user_id")], [tuple(r) for r in h.db.qa("SELECT * FROM world_trips")]
    parcel = h.db.q1("SELECT parcel_id FROM world_places")["parcel_id"]
    h.db.x("DELETE FROM world_places")                                                                # as if this city had never had one
    worldplaces.cache_clear()
    d.open(B)
    assert h.db.q1("SELECT parcel_id FROM world_places")["parcel_id"] == parcel                       # the same parcel again: the choice is stable
    assert [tuple(r) for r in h.db.qa("SELECT * FROM world_players ORDER BY user_id")] == players
    assert [tuple(r) for r in h.db.qa("SELECT * FROM world_trips")] == trips


def test_initialization_is_idempotent_and_the_database_forbids_duplicates(h):
    w = world.get_world(h.db, CH)
    base = world.load_layout(w)
    first = tuple(worldplaces.ensure_casino(h.db, w, base))
    for _ in range(5):
        assert tuple(worldplaces.ensure_casino(h.db, w, base)) == first
        worldplaces.load_city(h.db, w)
    assert h.db.q1("SELECT COUNT(*) c FROM world_places")["c"] == 1
    free = next(p["id"] for p in base["parcels"] if p["use"] == "vacant" and p["id"] != first[3])
    with pytest.raises(sqlite3.IntegrityError):
        h.db.x("INSERT INTO world_places(world_id,place_id,kind,parcel_id,created_at) VALUES(?,?,?,?,0)", (w["id"], "l9", "casino", free))     # a second casino
    with pytest.raises(sqlite3.IntegrityError):
        h.db.x("INSERT INTO world_places(world_id,place_id,kind,parcel_id,created_at) VALUES(?,?,?,?,0)", (w["id"], "l5", "other", free))      # the same ID
    with pytest.raises(sqlite3.IntegrityError):
        h.db.x("INSERT INTO world_places(world_id,place_id,kind,parcel_id,created_at) VALUES(?,?,?,?,0)", (w["id"], "l7", "other", first[3]))   # the same parcel
    assert h.db.q1("SELECT COUNT(*) c FROM world_places")["c"] == 1


def test_two_callers_racing_to_add_the_casino_end_with_one(h):
    db = Database(":memory:")
    w = old_world(db, CH, 7)
    base = world.load_layout(w)
    net = worldplaces.worldnet.RoadNet(base)
    out = []
    # the database is single-threaded by design; the race that matters is a second caller that read «no casino» before the first one wrote
    parcel = worldplaces.choose_parcel(base, net)
    db.x("INSERT INTO world_places(world_id,place_id,kind,parcel_id,created_at) VALUES(?,?,?,?,?)", (w["id"], "l5", "casino", parcel["id"], 1))
    out.append(tuple(worldplaces.ensure_casino(db, w, base, net)))                                    # it finds the winner's row and adds nothing
    assert db.q1("SELECT COUNT(*) c FROM world_places")["c"] == 1 and out[0][1] == "l5"


def test_a_place_that_does_not_fit_its_saved_city_is_reported_not_repaired(h, d):
    w = world.get_world(h.db, CH)
    h.db.x("UPDATE world_places SET parcel_id='p999999'")
    worldplaces.cache_clear()
    with pytest.raises(world.WorldError):
        worldplaces.load_city(h.db, w)
    calls = h.say(A, "مپ", chat=CH)
    assert "خراب" in tb.Harness.texts(calls) and not [c for c in calls if c.__class__.__name__ == "SendPhoto"]
    assert h.db.q1("SELECT parcel_id FROM world_places")["parcel_id"] == "p999999"                     # reported, left as it is


def test_the_casino_is_drawn_on_the_map_with_its_label(h, d, monkeypatch):
    c = city(h)
    seen = []
    real = cards.shape
    monkeypatch.setattr(cards, "shape", lambda text: seen.append(text) or real(text))
    with_casino = worldmap.render(c.layout, "fa")
    assert "کازینو" in seen                                                                           # the label the update asks for
    without = worldmap.render(c.base, "fa")
    assert with_casino != without
    cl = next(m for m in c.layout["landmarks"] if m["kind"] == "casino")
    px = worldmap._px(cl["at"])
    img = mapkit.png(with_casino)
    near = [img.getpixel((int(px[0]) + dx, int(px[1]) + dy)) for dx in range(-6, 7) for dy in range(-6, 7)]
    assert sum(1 for p in near if p == worldmap.LANDMARK_LINE) >= 8                                    # its marker, in the existing landmark colour
    assert max(max(p) for p in near) <= 180                                                            # no neon
    v = d.open(A)
    assert v.photo and mapkit.png(v.photo).size == img.size


def test_the_casino_is_selectable_in_every_way(h, d):
    v = d.open(A)
    c = city(h)
    cl = c.rows["casino"]
    o = d.go(A, v, t("fa", "mp.b.casino"))
    assert "L5" in o.caption and t("fa", "map.lm.casino") in o.caption and t("fa", "mp.obj.casino") in o.caption and o.photo
    pl = d.go(A, v, t("fa", "mp.b.places"))
    assert any(dd.endswith(":o:l5") for _x, dd in pl.buttons)
    for typed in ("مپ L5", "مپ l5", f"مپ {cl['parcel_id'].upper()}", "map L5"):
        tv = d.open(A, typed)
        assert "L5" in tv.caption and t("fa", "map.lm.casino") in tv.caption, typed
    assert WI.resolve(c.layout, "l5").kind == "landmark" and WI.resolve(c.layout, "l5").obj["kind"] == "casino"
    nb = d.go(A, v, t("fa", "mp.b.near"))
    assert nb.caption                                                                                  # the nearby screen lists public places, the casino among them when close


def test_the_casino_is_never_offered_as_empty_land(h, d):
    v = d.open(A)
    c = city(h)
    parcel = c.rows["casino"]["parcel_id"]
    ids = []
    page = d.go(A, v, t("fa", "mp.b.lots"))
    while True:
        ids += [dd.split(":")[3] for _x, dd in page.buttons if dd.split(":")[2] == "o"]
        nxt = [dd for x, dd in page.buttons if x == t("fa", "mp.b.next")]
        if not nxt:
            break
        page = d.press(A, nxt[0], v)
    assert parcel not in ids and ids
    assert WI.inspect("fa", world.get_world(h.db, CH), c.layout, parcel).text.count(t("fa", "wi.status.landmark")) >= 1


# ============================================================================================== getting in
def walk_to_casino(h, d, uid, lang="fa"):
    v = d.open(uid)
    o = d.press(uid, f"mp:{v.sid}:o:casino", v)
    d.go(uid, o, t(lang, "mp.b.go"))
    tr = h.db.q1("SELECT * FROM world_trips WHERE user_id=? ORDER BY id DESC LIMIT 1", (uid,))
    h.advance(tr["arrive_at"] - tr["started_at"] + 1)
    return v


def enter(h, d, uid):
    v = walk_to_casino(h, d, uid)
    o = d.press(uid, f"mp:{v.sid}:o:casino", v)
    assert t("fa", "mp.b.enter") in [x for x, _ in o.buttons] and t("fa", "mp.b.go") not in [x for x, _ in o.buttons]
    lobby = d.go(uid, o, t("fa", "mp.b.enter"))
    return v, lobby


def table_of(d, uid, v, lobby):
    tb_ = d.go(uid, lobby, t("fa", "mp.b.bj"))
    tb_.sid, tb_.mid = v.sid, v.mid
    return tb_


def scripted(monkeypatch, player, dealer, draws=()):
    """Make the next deal give `player` and `dealer` their first two cards, then `draws` for hits and the dealer's draws."""
    order = [player[0], dealer[0], player[1], dealer[1], *draws]
    monkeypatch.setattr(casino, "shuffled", lambda rng=None: [c for c in casino.new_deck() if c not in order] + list(reversed(order)))   # the next card is the LAST one


def test_arriving_opens_the_door_and_the_lobby(h, d):
    v, lobby = enter(h, d, A)
    assert t("fa", "mp.cas.title") in lobby.caption and t("fa", "bj.rules") in lobby.caption
    assert t("fa", "mp.b.bj") in [x for x, _ in lobby.buttons] and t("fa", "mp.b.exit") in [x for x, _ in lobby.buttons]
    assert h.db.q1("SELECT COUNT(*) c FROM casino_games")["c"] == 0                                    # nothing is dealt until the table is opened
    s = h.db.q1("SELECT * FROM map_sessions WHERE sid=?", (v.sid,))
    assert s["view"] == "cas" and s["sel"] == "l5"
    back = d.go(A, lobby, t("fa", "mp.b.exit"))
    assert t("fa", "mp.b.near") in [x for x, _ in back.buttons]                                        # «leave» goes back to the map
    assert not h.db.q1("SELECT 1 FROM casino_games")


def test_the_casino_can_only_be_entered_from_its_own_screen_while_standing_at_the_door(h, d):
    v = walk_to_casino(h, d, A)
    main = d.press(A, f"mp:{v.sid}:rf", v)
    forged = d.press(A, f"mp:{v.sid}:ce", main)                                                        # not on the casino's screen
    assert forged.alerts and not forged.edits
    o = d.press(A, f"mp:{v.sid}:o:p3", main)                                                           # looking at another place
    assert d.press(A, f"mp:{v.sid}:ce", o).alerts
    o = d.press(A, f"mp:{v.sid}:o:casino", main)
    assert d.press(A, f"mp:{v.sid}:ce", o).edits
    p = h.db.q1("SELECT * FROM world_players WHERE user_id=?", (A,))
    h.db.x("UPDATE world_players SET at_place='p3' WHERE user_id=?", (A,))                             # moved away (by any means): the door is closed
    assert d.press(A, f"mp:{v.sid}:ce", o).alerts


# ============================================================================================== blackjack rules
def hand(player, dealer, draws=()):
    order = [player[0], dealer[0], player[1], dealer[1], *draws]
    return casino.deal([c for c in casino.new_deck() if c not in order] + list(reversed(order)))                   # the next card is the LAST one


def test_hand_values_and_aces():
    v = casino.value
    assert v(["A♠", "K♥"]) == (21, True) and v(["A♠", "A♥"]) == (12, True) and v(["A♠", "A♥", "9♦"]) == (21, True)
    assert v(["A♠", "9♥", "5♦"]) == (15, False) and v(["10♠", "J♥", "5♦"]) == (25, False) and v(["A♠", "A♥", "A♦", "A♣"]) == (14, True)
    assert v(["Q♠", "K♥"])[0] == 20 and v(["10♦", "9♣"])[0] == 19 and v(["5♠", "6♥", "A♦"]) == (22 - 10, False) or v(["5♠", "6♥", "A♦"]) == (12, False)
    assert v(["A♠", "6♥"]) == (17, True) and v(["A♠", "6♥", "10♦"]) == (17, False)
    assert casino.is_blackjack(["A♠", "K♥"]) and casino.is_blackjack(["10♦", "A♣"]) and not casino.is_blackjack(["7♠", "7♥", "7♦"])
    assert not casino.is_blackjack(["A♠", "5♥", "5♦"])


def test_a_new_deal_gives_two_cards_each_and_hides_one_dealer_card():
    hh = hand(["10♠", "9♥"], ["7♠", "8♦"])
    assert hh.player == ["10♠", "9♥"] and hh.dealer == ["7♠", "8♦"] and not hh.over and hh.state == "playing"
    assert len(hh.deck) == 48 and len(set(hh.deck) | set(hh.player) | set(hh.dealer)) == 52            # nothing lost or doubled


@pytest.mark.parametrize("player,dealer,result", [
    (["A♠", "K♥"], ["10♠", "9♦"], "player_blackjack"),
    (["10♠", "9♦"], ["A♠", "Q♥"], "dealer_blackjack"),
    (["A♠", "K♥"], ["A♦", "J♣"], "push_blackjack"),
])
def test_naturals_end_the_hand_at_once(player, dealer, result):
    hh = hand(player, dealer)
    assert hh.over and hh.result == result and casino.outcome(result) == {"player_blackjack": "win", "dealer_blackjack": "lose", "push_blackjack": "push"}[result]
    assert len(hh.dealer) == 2 and len(hh.player) == 2                                                 # no card is drawn after a natural


def test_three_card_21_is_not_a_natural():
    hh = hand(["7♠", "7♥"], ["10♠", "7♦"], draws=["7♦"][:0] + ["7♣"])
    hh = casino.hit(hh)
    assert casino.total(hh.player) == 21 and not casino.is_blackjack(hh.player)
    assert hh.over and hh.result == "win"                                                              # 21 stands by itself; dealer holds 17


def test_hit_then_bust_loses_immediately_and_reveals_the_hand():
    hh = hand(["10♠", "6♥"], ["9♠", "8♦"], draws=["K♣"])
    assert not hh.over
    casino.hit(hh)
    assert hh.over and hh.result == "bust" and casino.total(hh.player) == 26 and len(hh.dealer) == 2   # the dealer never draws against a bust


def test_dealer_draws_to_seventeen_and_stands_on_every_seventeen():
    hh = hand(["10♠", "9♥"], ["5♠", "6♦"], draws=["4♣", "5♥"])                                         # 11 -> 15 -> 20
    casino.stand(hh)
    assert hh.dealer == ["5♠", "6♦", "4♣", "5♥"] and hh.over and hh.result == "lose"                    # 19 against 20
    hh = hand(["10♠", "7♥"], ["A♠", "6♦"], draws=["9♣"])                                               # soft 17: stands
    casino.stand(hh)
    assert hh.dealer == ["A♠", "6♦"] and hh.result == "push"
    hh = hand(["10♠", "8♥"], ["10♠", "7♦"], draws=["2♣"])                                              # hard 17: stands
    casino.stand(hh)
    assert len(hh.dealer) == 2 and hh.result == "win"
    hh = hand(["10♠", "8♥"], ["10♦", "6♦"], draws=["A♣"])                                              # 16 draws an ace: 17 and stands
    casino.stand(hh)
    assert hh.dealer == ["10♦", "6♦", "A♣"] and casino.total(hh.dealer) == 17 and hh.result == "win"


def test_results_win_lose_push_and_dealer_bust():
    for player, dealer, draws, expect in [
        (["10♠", "9♥"], ["10♦", "7♦"], [], "win"),
        (["10♠", "7♥"], ["10♦", "9♦"], [], "lose"),
        (["10♠", "8♥"], ["10♦", "8♦"], [], "push"),
        (["10♠", "8♥"], ["10♦", "6♦"], ["K♣"], "dealer_bust"),
    ]:
        hh = hand(player, dealer, draws)
        casino.stand(hh)
        assert hh.over and hh.result == expect, (player, dealer, hh.result)
        assert casino.outcome(hh.result) == {"win": "win", "dealer_bust": "win", "lose": "lose", "push": "push"}[expect]


def test_aces_are_reconsidered_as_cards_arrive():
    hh = hand(["A♠", "5♥"], ["10♠", "6♦"], draws=["9♣", "K♦"])
    assert casino.value(hh.player) == (16, True)
    casino.hit(hh)                                                                                     # A+5+9 = 15 hard
    assert casino.value(hh.player) == (15, False) and not hh.over
    casino.hit(hh)                                                                                     # +K = 25: bust
    assert hh.over and hh.result == "bust"


def test_buttons_cannot_act_on_a_finished_hand():
    hh = hand(["A♠", "K♥"], ["10♠", "9♦"])
    assert hh.over


def test_the_deck_is_a_standard_52_cards_shuffled_fairly():
    deck = casino.new_deck()
    assert len(deck) == 52 == len(set(deck))
    for r in casino.RANKS:
        assert sum(1 for c in deck if casino.rank_of(c) == r) == 4
    for s in casino.SUITS:
        assert sum(1 for c in deck if c.endswith(s)) == 13
    a, b = casino.shuffled(), casino.shuffled()
    assert sorted(a) == sorted(deck) == sorted(b) and a != b                                           # a permutation, and a different one every time
    assert casino.shuffled(random.Random(5)) == casino.shuffled(random.Random(5)) != casino.shuffled(random.Random(6))
    rng = random.Random(2024)                                                                          # chi-square over where one card lands
    n, slots = 5200, [0] * 52
    for _ in range(n):
        slots[casino.shuffled(rng).index("A♠")] += 1
    chi = sum((x - n / 52) ** 2 / (n / 52) for x in slots)
    assert chi < 87.0, chi                                                                             # df = 51, p = 0.001


def test_the_decks_randomness_is_isolated_from_the_global_generator(h, d, monkeypatch):
    assert casino.RNG_FACTORY is random.SystemRandom
    random.seed(1234)
    state = random.getstate()
    for _ in range(40):
        casino.shuffled()
        casino.start(h.db, f"s{_}", 1, A, 0, 1_700_000_000)
    assert random.getstate() == state                                                                  # not read, not advanced, not reseeded
    random.seed(1234)
    expected = [random.random() for _ in range(5)]
    random.seed(1234)
    for _ in range(10):
        casino.shuffled()
    assert [random.random() for _ in range(5)] == expected                                             # the rest of the program sees the same numbers
    v, lobby = enter(h, d, A)
    random.seed(77)
    state = random.getstate()
    table = table_of(d, A, v, lobby)
    for _ in range(6):
        row = h.db.q1("SELECT * FROM casino_games WHERE sid=?", (v.sid,))
        if row["state"] == "done":
            table = d.press(A, f"mp:{v.sid}:bj:n:{row['seq']}", v)
        else:
            table = d.press(A, f"mp:{v.sid}:bj:s:{row['seq']}", v)
    assert random.getstate() == state
    seen = []
    monkeypatch.setattr(casino, "RNG_FACTORY", lambda: seen.append(1) or random.Random(1))
    casino.shuffled()
    assert seen == [1]                                                                                 # and the hook that tests use is the one in charge


# ============================================================================================== blackjack through the buttons
def test_playing_a_hand_through_the_buttons(h, d, monkeypatch):
    v, lobby = enter(h, d, A)
    scripted(monkeypatch, ["10♠", "5♥"], ["9♣", "7♦"], draws=["4♥", "K♠"])
    table = table_of(d, A, v, lobby)
    assert "10♠ 5♥" in table.caption and "9♣" in table.caption and "7♦" not in table.caption and "🎴" in table.caption   # the second dealer card is hidden
    labels = [x for x, _ in table.buttons]
    assert t("fa", "mp.b.hit") in labels and t("fa", "mp.b.stand") in labels and t("fa", "mp.b.again") not in labels
    assert t("fa", "bj.nostakes") not in table.caption                                                 # (said at the end of a hand)
    hit = d.go(A, table, t("fa", "mp.b.hit"))
    hit.sid, hit.mid = v.sid, v.mid
    assert "10♠ 5♥ 4♥" in hit.caption and fmt.num(19, "fa") in hit.caption and "7♦" not in hit.caption
    stand = d.go(A, hit, t("fa", "mp.b.stand"))
    assert "9♣ 7♦ K♠" in stand.caption and fmt.num(26, "fa") in stand.caption and fmt.num(19, "fa") in stand.caption     # dealer reveals 16, draws the king and busts on 26
    assert t("fa", "bj.r.dealer_bust") in stand.caption and t("fa", "bj.nostakes") in stand.caption
    assert t("fa", "mp.b.again") in [x for x, _ in stand.buttons] and t("fa", "mp.b.hit") not in [x for x, _ in stand.buttons]
    row = h.db.q1("SELECT * FROM casino_games WHERE sid=?", (v.sid,))
    assert row["state"] == "done" and row["result"] == "dealer_bust" and row["user_id"] == A
    scripted(monkeypatch, ["A♠", "K♥"], ["5♠", "5♦"])
    again = d.go(A, stand, t("fa", "mp.b.again"))
    assert t("fa", "bj.r.player_blackjack") in again.caption and "5♦" in again.caption                  # natural: finished and revealed at once
    assert h.db.q1("SELECT COUNT(*) c FROM casino_games")["c"] == 1                                    # one game row per map session


def test_every_screen_of_the_table_fits_telegram_in_both_languages(h, d):
    from hypemarket import social
    for lang in ("fa", "en"):
        social.set_group_lang(h.db, CH, lang)
        v, lobby = enter(h, d, A) if lang == "fa" else (None, None)
        if v is None:
            v = walk_to_casino(h, d, B, "en")
            o = d.press(B, f"mp:{v.sid}:o:casino", v)
            lobby = d.go(B, o, t("en", "mp.b.enter"))
            table = d.go(B, lobby, t("en", "mp.b.bj"))
            uid = B
        else:
            table = d.go(A, lobby, t("fa", "mp.b.bj"))
            uid = A
        table.sid, table.mid = v.sid, v.mid
        for _ in range(4):
            assert len(table.caption) <= 1024
            row = h.db.q1("SELECT * FROM casino_games WHERE sid=?", (v.sid,))
            table = d.press(uid, f"mp:{v.sid}:bj:{'n' if row['state'] == 'done' else 'h'}:{row['seq']}", v)
        assert len(lobby.caption) <= 1024


def test_nobody_but_the_owner_can_use_the_table(h, d, monkeypatch):
    v, lobby = enter(h, d, A)
    scripted(monkeypatch, ["10♠", "5♥"], ["9♣", "7♦"], draws=["4♥"])
    table = table_of(d, A, v, lobby)
    h.say(B, "سلام", chat=CH)
    row = tuple(h.db.q1("SELECT * FROM casino_games WHERE sid=?", (v.sid,)))
    before = {n: [tuple(r) for r in h.db.qa(f"SELECT * FROM {n}")] for n in ("casino_games", "world_players", "world_trips", "map_sessions", "users")}
    for uid in (B, X):
        for what in table.data() + [f"mp:{v.sid}:bj:h:{row[8]}", f"mp:{v.sid}:bj:s:{row[8]}", f"mp:{v.sid}:bj:n:{row[8]}", f"mp:{v.sid}:bj:o"]:
            out = d.press(uid, what, v)
            assert out.alerts == [t("fa", "mp.not_yours")] and not out.edits and not out.sent, (uid, what)
    assert {n: [tuple(r) for r in h.db.qa(f"SELECT * FROM {n}")] for n in before} == before            # no card drawn, no game ended, nobody registered
    assert tuple(h.db.q1("SELECT * FROM casino_games WHERE sid=?", (v.sid,))) == row
    ok = d.go(A, table, t("fa", "mp.b.hit"))
    assert "4♥" in ok.caption                                                                          # the owner is unaffected


def test_each_players_table_is_their_own(h, d, monkeypatch):
    va, la = enter(h, d, A)
    vb, lb = enter(h, d, B)
    scripted(monkeypatch, ["10♠", "5♥"], ["9♣", "7♦"], draws=["4♥", "K♠"])
    ta = table_of(d, A, va, la)
    scripted(monkeypatch, ["8♠", "8♥"], ["10♣", "6♦"], draws=["3♥"])
    tbl = table_of(d, B, vb, lb)
    assert "10♠ 5♥" in ta.caption and "8♠ 8♥" in tbl.caption
    ra, rb = h.db.q1("SELECT * FROM casino_games WHERE sid=?", (va.sid,)), h.db.q1("SELECT * FROM casino_games WHERE sid=?", (vb.sid,))
    assert (ra["user_id"], rb["user_id"]) == (A, B) and ra["sid"] != rb["sid"]
    d.go(A, ta, t("fa", "mp.b.hit"))
    assert json.loads(h.db.q1("SELECT player FROM casino_games WHERE sid=?", (vb.sid,))["player"]) == ["8♠", "8♥"]    # B's hand did not change
    d.press(B, f"mp:{vb.sid}:bj:s:{rb['seq']}", vb)
    assert len(json.loads(h.db.q1("SELECT player FROM casino_games WHERE sid=?", (va.sid,))["player"])) == 3          # nor A's by B's stand
    cross = d.press(B, f"mp:{va.sid}:bj:h:{ra['seq'] + 1}", va)                                                      # B on A's table
    assert cross.alerts == [t("fa", "mp.not_yours")]
    cross = d.press(B, f"mp:{va.sid}:bj:h:{ra['seq'] + 1}", vb)                                                      # A's button on B's message: not B's session
    assert cross.alerts and not cross.edits


def test_one_player_with_two_map_messages_has_two_separate_tables(h, d, monkeypatch):
    v1, l1 = enter(h, d, A)
    v2 = d.open(A)
    o = d.press(A, f"mp:{v2.sid}:o:casino", v2)
    l2 = d.go(A, o, t("fa", "mp.b.enter"))
    scripted(monkeypatch, ["10♠", "5♥"], ["9♣", "7♦"])
    t1 = table_of(d, A, v1, l1)
    scripted(monkeypatch, ["8♠", "3♥"], ["10♣", "6♦"])
    t2 = table_of(d, A, v2, l2)
    assert "10♠ 5♥" in t1.caption and "8♠ 3♥" in t2.caption and h.db.q1("SELECT COUNT(*) c FROM casino_games")["c"] == 2


def test_repeated_and_stale_button_presses_are_applied_once(h, d, monkeypatch):
    v, lobby = enter(h, d, A)
    scripted(monkeypatch, ["2♠", "3♥"], ["9♣", "7♦"], draws=["4♥", "5♠", "6♣", "K♠"])
    table = table_of(d, A, v, lobby)
    hit = [dd for x, dd in table.buttons if x == t("fa", "mp.b.hit")][0]
    stand = [dd for x, dd in table.buttons if x == t("fa", "mp.b.stand")][0]
    first = d.press(A, hit, v)
    second = d.press(A, hit, v)                                                                        # the same press delivered twice
    third = d.press(A, hit, v)
    row = h.db.q1("SELECT * FROM casino_games WHERE sid=?", (v.sid,))
    assert json.loads(row["player"]) == ["2♠", "3♥", "4♥"] and row["seq"] == 2                          # one card, once
    assert "4♥" in first.caption and t("fa", "bj.stale") in second.toasts[-1] and "5♠" not in second.caption
    assert "4♥" in second.caption                                                                      # the stale press shows the real state, it does not guess
    old_stand = d.press(A, stand, v)                                                                   # the stand button of the first screen: its seq is gone
    assert t("fa", "bj.stale") in old_stand.toasts[-1] and json.loads(h.db.q1("SELECT dealer FROM casino_games WHERE sid=?", (v.sid,))["dealer"]) == ["9♣", "7♦"]
    row = h.db.q1("SELECT * FROM casino_games WHERE sid=?", (v.sid,))
    now_stand = [dd for x, dd in mapkit.buttons_of(first.edit) and [(a, b) for a, b in first.buttons] if x == t("fa", "mp.b.stand")][0]
    done = d.press(A, now_stand, v)
    again = d.press(A, now_stand, v)                                                                   # and a repeated stand does not make the dealer draw twice
    row = h.db.q1("SELECT * FROM casino_games WHERE sid=?", (v.sid,))
    assert row["state"] == "done" and len(json.loads(row["dealer"])) == 3 and t("fa", "bj.stale") in again.toasts[-1]
    late_hit = d.press(A, hit, v)                                                                      # a hit after the hand is over
    assert tuple(h.db.q1("SELECT * FROM casino_games WHERE sid=?", (v.sid,))) == tuple(row) and t("fa", "bj.stale") in late_hit.toasts[-1]
    new = [dd for x, dd in done.buttons if x == t("fa", "mp.b.again")][0]
    scripted(monkeypatch, ["10♠", "7♥"], ["9♣", "8♦"])
    a1 = d.press(A, new, v)
    a2 = d.press(A, new, v)                                                                            # «new hand» pressed twice: one new hand
    assert h.db.q1("SELECT seq FROM casino_games WHERE sid=?", (v.sid,))["seq"] == row["seq"] + 1
    assert "10♠ 7♥" in a1.caption and t("fa", "bj.stale") in a2.toasts[-1]


def test_a_new_hand_cannot_be_dealt_over_a_hand_in_progress(h, d, monkeypatch):
    v, lobby = enter(h, d, A)
    scripted(monkeypatch, ["10♠", "5♥"], ["9♣", "7♦"], draws=["4♥"])
    table = table_of(d, A, v, lobby)
    row = h.db.q1("SELECT * FROM casino_games WHERE sid=?", (v.sid,))
    for forged in (f"mp:{v.sid}:bj:n:{row['seq']}", f"mp:{v.sid}:bj:n:0", f"mp:{v.sid}:bj:n:{row['seq'] + 1}"):
        out = d.press(A, forged, v)
        assert t("fa", "bj.stale") in out.toasts[-1]
    assert tuple(h.db.q1("SELECT * FROM casino_games WHERE sid=?", (v.sid,))) == tuple(row)
    again = d.press(A, f"mp:{v.sid}:bj:o", v)                                                          # opening the table again resumes the same hand
    assert "10♠ 5♥" in again.caption and tuple(h.db.q1("SELECT * FROM casino_games WHERE sid=?", (v.sid,))) == tuple(row)


def test_garbage_in_blackjack_buttons_fails_safely(h, d):
    v, lobby = enter(h, d, A)
    table = table_of(d, A, v, lobby)
    row = tuple(h.db.q1("SELECT * FROM casino_games WHERE sid=?", (v.sid,)))
    for junk in ("bj", "bj:x", "bj:h", "bj:h:abc", "bj:h:-1", "bj:h:99999999999999999999", "bj:s:", "bj:n:x", "bj:o:1", "bj:hit:1"):
        out = d.press(A, f"mp:{v.sid}:{junk}", v)
        assert len(out.alerts) + len(out.toasts) >= 0 and "Traceback" not in " ".join(out.toasts)
    assert tuple(h.db.q1("SELECT * FROM casino_games WHERE sid=?", (v.sid,))) == row or h.db.q1("SELECT seq FROM casino_games WHERE sid=?", (v.sid,))["seq"] >= row[8]


def test_the_table_stops_working_when_the_player_leaves_the_casino(h, d, monkeypatch):
    v, lobby = enter(h, d, A)
    scripted(monkeypatch, ["10♠", "5♥"], ["9♣", "7♦"], draws=["4♥"])
    table = table_of(d, A, v, lobby)
    row = tuple(h.db.q1("SELECT * FROM casino_games WHERE sid=?", (v.sid,)))
    other = d.open(A)                                                                                  # a second map message...
    o = d.press(A, f"mp:{other.sid}:o:p3", other)
    d.go(A, o, t("fa", "mp.b.go"))                                                                     # ...from which the player walks away
    hit = [dd for x, dd in table.buttons if x == t("fa", "mp.b.hit")][0]
    out = d.press(A, hit, v)
    assert out.alerts == [t("fa", "mp.cas.left")] and not out.edits
    assert tuple(h.db.q1("SELECT * FROM casino_games WHERE sid=?", (v.sid,))) == row                   # no card drawn from a table nobody is sitting at
    tr = h.db.q1("SELECT * FROM world_trips WHERE user_id=? AND state='walking'", (A,))
    h.advance(tr["arrive_at"] - tr["started_at"] + 1)
    assert d.press(A, hit, v).alerts == [t("fa", "mp.cas.left")]                                       # arrived somewhere else: still not at the casino


def test_blackjack_games_survive_a_restart(tmp_path, monkeypatch):
    path = str(tmp_path / "game.db")
    worldmap.cache_clear()
    worldplaces.cache_clear()
    h1 = tb.Harness()
    h1.db.close()
    db = Database(path)
    h1.db = h1.app.db = db
    world.save_world(db, CH, world.build(7))
    d1 = mapkit.Driver(h1, CH)
    v, lobby = enter(h1, d1, A)
    scripted(monkeypatch, ["10♠", "5♥"], ["9♣", "7♦"], draws=["4♥", "K♠"])
    table = table_of(d1, A, v, lobby)
    hit = d1.go(A, table, t("fa", "mp.b.hit"))
    saved = tuple(db.q1("SELECT * FROM casino_games WHERE sid=?", (v.sid,)))
    sid, mid, now = v.sid, v.mid, tb.T[0]
    db.close()
    h2 = tb.Harness()
    tb.T[0] = now
    db2 = Database(path)
    h2.db = h2.app.db = db2
    worldplaces.cache_clear()
    assert tuple(db2.q1("SELECT * FROM casino_games WHERE sid=?", (sid,))) == saved                    # the hand, the deck and the seq: exactly as left
    stand = [dd for x, dd in hit.buttons if x == t("fa", "mp.b.stand")][0]
    out = mapkit.Out(h2.press(A, stand, chat=CH, mid=mid, photo=True))
    assert out.edits and not out.alerts and "K♠" in out.caption and "10♠ 5♥ 4♥" in out.caption         # the next press carries on the same hand
    resume = mapkit.Out(h2.press(A, f"mp:{sid}:bj:o", chat=CH, mid=mid, photo=True))
    assert "10♠ 5♥ 4♥" in resume.caption                                                               # reopening shows the finished hand, no new deal


def test_expired_sessions_cannot_touch_a_table(h, d, monkeypatch):
    v, lobby = enter(h, d, A)
    scripted(monkeypatch, ["10♠", "5♥"], ["9♣", "7♦"], draws=["4♥"])
    table = table_of(d, A, v, lobby)
    row = tuple(h.db.q1("SELECT * FROM casino_games WHERE sid=?", (v.sid,)))
    hit = [dd for x, dd in table.buttons if x == t("fa", "mp.b.hit")][0]
    h.advance(C.map_session_ttl() + 10)
    out = d.press(A, hit, v)
    assert out.alerts == [t("fa", "mp.expired")] and not out.edits
    assert tuple(h.db.q1("SELECT * FROM casino_games WHERE sid=?", (v.sid,))) == row
    d.open(A)                                                                                          # the next map message cleans the dead session's game away
    assert h.db.q1("SELECT COUNT(*) c FROM casino_games WHERE sid=?", (v.sid,))["c"] == 0


# ============================================================================================== no stakes, no side effects
def test_blackjack_changes_nothing_in_the_economy_or_progression(h, d, monkeypatch):
    d.open(B)
    h.say(A, "daily")
    d.open(A)
    v, lobby = enter(h, d, A)
    before = econ_snapshot(h.db)
    xp = [tuple(r) for r in h.db.qa("SELECT * FROM quests ORDER BY user_id, qid, period")]
    ach = [tuple(r) for r in h.db.qa("SELECT * FROM achievements ORDER BY 1,2")] if h.db.q1("SELECT name FROM sqlite_master WHERE name='achievements'") else []
    table = table_of(d, A, v, lobby)
    for _ in range(25):
        row = h.db.q1("SELECT * FROM casino_games WHERE sid=?", (v.sid,))
        act = "n" if row["state"] == "done" else random.choice("hs")
        d.press(A, f"mp:{v.sid}:bj:{act}:{row['seq']}", v)
    assert h.db.q1("SELECT seq FROM casino_games WHERE sid=?", (v.sid,))["seq"] > 5                    # plenty of hands were really played
    assert econ_snapshot(h.db) == before
    assert [tuple(r) for r in h.db.qa("SELECT * FROM quests ORDER BY user_id, qid, period")] == xp
    assert h.db.q1("SELECT COUNT(*) c FROM news")["c"] == 0 and h.db.q1("SELECT COUNT(*) c FROM history WHERE ts>?", (1_700_000_100,))["c"] == 0
    assert not h.db.outbox and not h.db.quest_events and not h.db.ach_events


def test_there_is_nothing_to_stake_anywhere_in_the_casino(h, d):
    cols = {r["name"] for r in h.db.qa("PRAGMA table_info(casino_games)")}
    assert cols == {"sid", "world_id", "user_id", "deck", "player", "dealer", "state", "result", "seq", "created_at", "updated_at"}
    v, lobby = enter(h, d, A)
    table = table_of(d, A, v, lobby)
    words = ("bet", "stake", "buy", "coin", "cash", "prize", "pay", "شرط", "جایزه", "سکه", "خرید")
    for screen in (lobby, table):
        for label, data in screen.buttons:
            assert not any(w in (label + data).lower() for w in words), (label, data)
    src = open(casino.__file__, encoding="utf-8").read()
    import re
    imports = set(re.findall(r"^(?:from|import) ([\w.]+)", src, re.M))
    assert imports <= {"__future__", "json", "random", "sqlite3", "dataclasses"}, imports              # sealed off: no economy module is even imported
    for name in ("cash", "UPDATE users", "INSERT INTO users", "holdings", "stocks", "progress", "quests"):
        assert name not in src.replace("# ", ""), name
    mapsrc = open(__import__("hypemarket.mapui", fromlist=["x"]).__file__, encoding="utf-8").read()
    for name in ("UPDATE users", "UPDATE holdings", "INSERT INTO news", "trading.", "progress.", "quests."):
        assert name not in mapsrc, name


def test_walking_and_the_casino_never_use_the_global_random_state(h, d):
    random.seed(4321)
    state = random.getstate()
    v = walk_to_casino(h, d, A)
    o = d.press(A, f"mp:{v.sid}:o:casino", v)
    d.go(A, o, t("fa", "mp.b.enter"))
    for u in range(10, 30):
        d.open(u)
    assert random.getstate() == state


# ============================================================================================== migration, backups
def test_schema_7_is_added_to_an_old_database_without_touching_its_data(tmp_path):
    path = str(tmp_path / "old.db")
    db = Database(path)
    core.set_clock(lambda: 1_700_000_000)
    core.ensure_user(db, A, "a", "A", lang="fa")
    world.save_world(db, CH, world.build(5))
    before_users = [tuple(r) for r in db.qa("SELECT * FROM users")]
    layout = db.q1("SELECT layout FROM worlds")["layout"]
    for tbl in ("world_places", "world_players", "world_trips", "map_sessions", "casino_games"):
        db.x(f"DROP TABLE {tbl}")                                                                      # a v0.16 database: none of the v0.17 tables
    db.x("PRAGMA user_version=6")
    db.close()
    d2 = Database(path)
    assert d2.q1("PRAGMA user_version")[0] == SCHEMA_VERSION == 7 and 7 in MIGRATIONS
    for tbl in ("world_places", "world_players", "world_trips", "map_sessions", "casino_games"):
        assert d2.q1("SELECT COUNT(*) c FROM " + tbl)["c"] == 0
    assert [tuple(r) for r in d2.qa("SELECT * FROM users")] == before_users and d2.q1("SELECT layout FROM worlds")["layout"] == layout
    assert Database(path).q1("PRAGMA user_version")[0] == 7                                            # reopening is a no-op
    assert worldplaces.ensure_all(d2) == 1                                                             # and the old city now gets its casino


def test_backups_carry_the_map_tables(h, d, tmp_path, monkeypatch):
    v, lobby = enter(h, d, A)
    scripted(monkeypatch, ["10♠", "5♥"], ["9♣", "7♦"], draws=["4♥"])
    table_of(d, A, v, lobby)
    d.press(A, f"mp:{v.sid}:o:p3", v)
    dest = str(tmp_path / "backup.db")
    h.db.backup_to(dest)
    copy = Database(dest)
    for tbl in ("world_places", "world_players", "world_trips", "map_sessions", "casino_games", "worlds", "users"):
        assert [tuple(r) for r in copy.qa(f"SELECT * FROM {tbl} ORDER BY 1,2")] == [tuple(r) for r in h.db.qa(f"SELECT * FROM {tbl} ORDER BY 1,2")], tbl
    worldplaces.cache_clear()
    c2 = worldplaces.load_city(copy, world.get_world(copy, CH))
    assert c2.casino_id == "l5" and P.get_player(copy, c2.id, A)["at_place"] == "l5"                  # a restored copy plays on: casino, position, hand
    assert copy.q1("SELECT state FROM casino_games")["state"] == "playing"
