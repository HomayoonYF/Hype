"""Milestone B (v0.17): where players stand, who can be seen, and walking along the roads.

Real handlers and a real (in-memory or temporary-file) database; Telegram itself is faked. Cities are saved from fixed seeds so results
repeat."""
import math

import pytest

import mapkit
import test_bot as tb
from hypemarket import character, config as C, core, fmt, presence as P, world, worldgeom as G, worldmap, worldnet as N, worldplaces
from hypemarket.db import Database
from hypemarket.texts import t

CH, CH2 = -100606, -100777
A, B, C3, D4 = 777001, 777002, 777003, 777004
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


def table(db, name):
    return [tuple(r) for r in db.qa(f"SELECT * FROM {name}")]


def econ_snapshot(db):
    out = {n: table(db, n) for n in ECON}
    out["users"] = [tuple(r) for r in db.qa("SELECT id,cash,pending_div,pending_inc,rep,max_level,spent_points,rebirths,legacy_pts,legacy_total,nw,trades FROM users ORDER BY id")]
    return out


def put(h, uid, pt, at=None, chat=CH):
    """Stand player `uid` at point `pt` of the city (a direct edit of their row: what a finished walk would have written)."""
    c = city(h, chat)
    h.db.x("UPDATE world_players SET x=?, y=?, at_place=? WHERE world_id=? AND user_id=?", (pt[0], pt[1], at, c.id, uid))


def at_distance(c, a, ref, dist):
    """The road point `dist` units from `a` along the shortest walk towards place `ref`."""
    r = c.net.route(a, c.spot(ref).door.pt)
    return N.point_along(r.pts, dist)


def trip_row(h, uid, chat=CH):
    return h.db.q1("SELECT * FROM world_trips WHERE world_id=? AND user_id=? ORDER BY id DESC LIMIT 1", (city(h, chat).id, uid))


def open_obj(d, uid, view, ref):
    """From the main screen to the screen of place `ref` (the casino has its own button)."""
    return d.press(uid, f"mp:{view.sid}:o:{ref}", view)


# ============================================================================================== positions
def test_first_map_gives_a_stable_spawn_on_a_road_and_it_is_written_once(h, d):
    c = city(h)
    assert h.db.q1("SELECT COUNT(*) c FROM world_players")["c"] == 0
    d.open(A)
    row = P.get_player(h.db, c.id, A)
    assert row and row["at_place"] is None and (row["x"], row["y"]) == (row["spawn_x"], row["spawn_y"])
    s = c.net.snap((row["x"], row["y"]))
    assert s.off < 0.05 and c.net.land[c.net.segs[s.seg].a] and not c.net.segs[s.seg].bridge        # on a road, on dry land
    st = c.spot(next(m["id"] for m in c.base["landmarks"] if m["kind"] == "station"))
    assert c.net.field(st.door.pt).to_point((row["x"], row["y"])) <= P.SPAWN_RADIUS + 1             # near the station: players start together
    before = tuple(row)
    for _ in range(4):
        d.open(A)                                                                                    # more map requests never move or recreate them
        h.advance(500)
    assert tuple(P.get_player(h.db, c.id, A)) == before and h.db.q1("SELECT COUNT(*) c FROM world_players")["c"] == 1


def test_spawn_is_deterministic_per_city_and_player_and_ignores_the_economy(h, d):
    c = city(h)
    pts = {u: P.spawn_point(c, u) for u in range(1, 40)}
    assert pts == {u: P.spawn_point(c, u) for u in range(1, 40)}                                     # pure
    assert len(set(pts.values())) > 5                                                                # players are spread over the spawn spots, not stacked
    other = Database(":memory:")
    world.save_world(other, CH, world.build(7))
    assert {u: P.spawn_point(worldplaces.load_city(other, world.get_world(other, CH)), u) for u in range(1, 40)} == pts   # same city, same spots, any database
    d.open(A)
    first = P.get_player(h.db, c.id, A)
    assert (first["x"], first["y"]) == tuple(round(v, 2) for v in P.spawn_point(c, A))
    h.db.x("UPDATE users SET cash=cash*7+13, rebirths=3, legacy_pts=9, max_level=12, nw=99999 WHERE id=?", (A,))
    h.db.x("DELETE FROM world_players")                                                              # even if the row is lost and made again
    d.open(A)
    again = P.get_player(h.db, c.id, A)
    assert (again["x"], again["y"]) == (first["x"], first["y"])
    w2 = Database(":memory:")
    world.save_world(w2, CH, world.build(42))
    c2 = worldplaces.load_city(w2, world.get_world(w2, CH))
    assert any(P.spawn_point(c2, u) != pts[u] for u in range(1, 40))                                 # another city (another seed) spreads them differently


def test_spawn_spots_are_never_on_a_bridge_or_in_the_water(h):
    for seed in (7, 42, 999):
        db = Database(":memory:")
        world.save_world(db, CH, world.build(seed))
        c = worldplaces.load_city(db, world.get_world(db, CH))
        cands = P.spawn_candidates(c)
        assert len(cands) >= P.MIN_SPAWN_SPOTS
        water = [wt["poly"] for wt in c.base["water"]]
        for p in cands:
            assert not any(G.point_in_poly(p, poly) for poly in water), (seed, p)
            assert not c.net.segs[c.net.snap(p).seg].bridge


def test_a_players_row_is_unique_and_another_group_adds_none(h, d):
    world.save_world(h.db, CH2, world.build(5))
    d.open(A)
    mapkit.Driver(h, CH2).open(A)                                                                     # the same player, typing in another group
    rows = h.db.qa("SELECT * FROM world_players WHERE user_id=? ORDER BY world_id", (A,))
    assert len(rows) == 1 and rows[0]["world_id"] == city(h).id                                       # one row, in their own city only
    import sqlite3
    with pytest.raises(sqlite3.IntegrityError):
        h.db.x("INSERT INTO world_players(world_id,user_id,x,y,spawn_x,spawn_y,created_at,updated_at) VALUES(?,?,0,0,0,0,0,0)", (rows[0]["world_id"], A))
    mine = h.db.q1("SELECT * FROM world_players WHERE world_id=? AND user_id=?", (city(h).id, A))
    assert tuple(P.ensure_player(h.db, city(h), A)) == tuple(mine)                                    # asking again returns the same row
    assert h.db.q1("SELECT COUNT(*) c FROM world_players WHERE user_id=?", (A,))["c"] == 1


def test_positions_survive_a_restart(tmp_path):
    path = str(tmp_path / "game.db")
    db = Database(path)
    core.set_clock(lambda: 1_700_000_000)
    world.save_world(db, CH, world.build(7))
    c = worldplaces.load_city(db, world.get_world(db, CH))
    core.ensure_user(db, A, "a", "A", lang="fa")
    row = tuple(P.ensure_player(db, c, A))
    db.close()
    db2 = Database(path)
    worldplaces.cache_clear()
    c2 = worldplaces.load_city(db2, world.get_world(db2, CH))
    assert tuple(P.get_player(db2, c2.id, A)) == row                                                  # the saved row, untouched by reopening
    assert tuple(round(v, 2) for v in P.spawn_point(c2, A)) == (row[2], row[3])                       # and it is still where the rule puts this player
    assert tuple(P.ensure_player(db2, c2, A)) == row


# ============================================================================================== who is near
def test_players_within_road_distance_are_marked_and_far_ones_are_not(h, d):
    va = d.open(A)
    for u in (B, C3, D4):
        d.open(u)
    c = city(h)
    a = P.stand(h.db, c, A).pos
    put(h, B, at_distance(c, a, "l5", 120))
    put(h, C3, at_distance(c, a, "l5", C.nearby_radius() - 5))
    far_ref = max(c.spots("landmark"), key=lambda s: c.net.field(a).to(s.door)).id
    assert c.net.field(a).to(c.spot(far_ref).door) > C.nearby_radius() + 50
    put(h, D4, at_distance(c, a, far_ref, C.nearby_radius() + 40))
    got = P.nearby(h.db, c, A, a)
    assert [o.uid for o in got] == [B, C3] and got[0].dist == pytest.approx(120, abs=0.5) and got[1].dist < C.nearby_radius()
    assert all(isinstance(o.dist, float) for o in got)
    v = d.press(A, f"mp:{va.sid}:rf", va)
    me = mapkit.shown(h, CH, A, "fa")
    assert v.photo == me                                                                             # the picture is exactly what the saved rows say
    plain = worldmap.render(c.layout, "fa")
    for u, expect in ((B, True), (C3, True), (D4, False)):
        pos = P.stand(h.db, c, u).pos
        px = worldmap._px(pos)
        got_px = mapkit.png(v.photo).getpixel((int(round(px[0])), int(round(px[1]))))
        base_px = mapkit.png(plain).getpixel((int(round(px[0])), int(round(px[1]))))
        assert (got_px != base_px) is expect, (u, got_px, base_px)                                  # a marker is drawn for the near players only
    assert t("fa", "mp.others", names="").strip()[:6] in v.caption
    assert v.caption.count("•") == 0 and "👥" in v.caption


def test_road_distance_not_straight_distance_decides(h, d):
    c = city(h)
    net = c.net
    best = None
    nodes = [i for i in range(net.n) if net.land[i]][::7]
    for i in nodes:
        field = net.field(net.pts[i])
        for j in nodes:
            e = G.dist(net.pts[i], net.pts[j])
            r = field.dist.get(j, math.inf)
            if e <= 150 and r >= 450 and (best is None or r - e > best[2]):
                best = (net.pts[i], net.pts[j], r - e)
    assert best, "the test city should have two spots on opposite banks of the river"
    p, q, _ = best
    d.open(A)
    d.open(B)
    put(h, A, p)
    put(h, B, q)
    assert G.dist(p, q) <= 150 < C.nearby_radius()                                                   # a straight line would say «close»
    assert [o.uid for o in P.nearby(h.db, c, A, p)] == []                                            # the roads say «far»
    v = d.open(A)
    assert t("fa", "mp.others.none") in v.caption                                                     # «nobody is near you»


def test_players_of_another_city_are_never_shown(h, d):
    world.save_world(h.db, CH2, world.build(5))
    d2 = mapkit.Driver(h, CH2)
    d.open(A)
    d2.open(B)                                                                                        # B's first life-game entry was in the other group: B lives in the other city
    c1, c2 = city(h), city(h, CH2)
    assert character.get(h.db, B)["current_world_id"] == c2.id
    h.db.x("UPDATE world_players SET x=?, y=? WHERE world_id=? AND user_id=?", (*P.stand(h.db, c1, A).pos, c2.id, B))      # B stands where A stands, in the other city
    assert P.nearby(h.db, c1, A, P.stand(h.db, c1, A).pos) == []
    assert P.nearby(h.db, c2, B, P.stand(h.db, c2, B).pos) == []
    # B moves to A's city (a future «travel» feature would do this; no such feature exists in v0.19, so the test writes the authoritative column directly)
    h.db.x("UPDATE characters SET current_world_id=? WHERE user_id=?", (c1.id, B))
    d.open(B)                                                                                         # B's presence in A's city is created by their next action there
    assert [o.uid for o in P.nearby(h.db, c1, A, P.stand(h.db, c1, A).pos, radius=1e6)] == [B]
    assert P.nearby(h.db, c2, B, P.stand(h.db, c2, B).pos) == []                                      # and the old row in the other city is dormant: nobody there sees B


def test_banned_players_and_uninitialized_players_are_not_markers(h, d):
    d.open(A)
    d.open(B)
    c = city(h)
    a = P.stand(h.db, c, A).pos
    put(h, B, at_distance(c, a, "l5", 50))
    assert [o.uid for o in P.nearby(h.db, c, A, a)] == [B]
    h.db.x("UPDATE users SET banned=1 WHERE id=?", (B,))
    assert P.nearby(h.db, c, A, a) == []
    h.db.x("UPDATE users SET banned=0 WHERE id=?", (B,))
    h.say(C3, "سلام", chat=CH)                                                                       # chats in the group but never opened the map: no row, no marker
    assert [o.uid for o in P.nearby(h.db, c, A, a)] == [B] and P.get_player(h.db, c.id, C3) is None


def test_markers_never_change_the_saved_city(h, d):
    w = dict(world.get_world(h.db, CH))
    base = world.load_layout(world.get_world(h.db, CH))
    import copy
    frozen = copy.deepcopy(base)
    for u in (A, B, C3):
        v = d.open(u)
        d.press(u, v.find(t("fa", "mp.b.near")), v)
    c = city(h)
    assert dict(world.get_world(h.db, CH)) == w and base == frozen and c.base == frozen
    for key in ("roads", "water", "districts", "blocks", "footprint", "nodes", "terrain", "name", "seed", "w", "h"):
        assert c.layout[key] == frozen[key]
    assert [p["id"] for p in c.layout["parcels"]] == [p["id"] for p in frozen["parcels"]]
    assert {p["id"]: (p["poly"], p["c"]) for p in c.layout["parcels"]} == {p["id"]: (p["poly"], p["c"]) for p in frozen["parcels"]}


def test_no_marker_work_runs_in_the_background(h, d):
    import asyncio
    import hypemarket.handlers as H
    assert not [n for n in dir(H) if "marker" in n.lower() and "job" in n.lower()]
    from hypemarket import jobs
    src = open(jobs.__file__, encoding="utf-8").read()
    assert "presence" not in src and "mapui" not in src and "world_players" not in src


# ============================================================================================== routes
def route_ok(c, pts):
    net = c.net
    water = [wt["poly"] for wt in c.base["water"]]
    segs = list(zip(pts, pts[1:]))
    for a, b in segs:
        mid = ((a[0] + b[0]) / 2, (a[1] + b[1]) / 2)
        s = net.snap(mid)
        assert s.off < 0.05, ("a step that leaves the roads", a, b)                                   # every step lies on a road polyline
        if any(G.point_in_poly(mid, poly) for poly in water):
            assert net.segs[s.seg].bridge, ("walked through the river", a, b)                         # water is only crossed on a bridge road
    assert all(net.snap(p).off < 0.05 for p in pts)
    return True


def test_routes_follow_connected_roads_and_cross_the_river_only_on_bridges(h, d):
    for seed in (7, 42, 999):
        db = Database(":memory:")
        world.save_world(db, CH, world.build(seed))
        c = worldplaces.load_city(db, world.get_world(db, CH))
        st = next(m for m in c.base["landmarks"] if m["kind"] == "station")
        a = c.spot(st["id"]).door.pt
        crossed = 0
        for s in c.spots():
            r = c.net.route(a, s.door.pt)
            assert r is not None and route_ok(c, r.pts)
            assert r.length >= G.dist(a, s.door.pt) - 0.01 and G.dist(r.pts[0], c.net.snap(a).pt) < 1e-6 and G.dist(r.pts[-1], s.door.pt) < 1e-6
            assert r.length == pytest.approx(N.path_length(r.pts))
            crossed += any(c.net.segs[c.net.snap(((p[0] + q[0]) / 2, (p[1] + q[1]) / 2)).seg].bridge for p, q in zip(r.pts, r.pts[1:]))
        assert crossed > 0                                                                           # some destinations really need a bridge
        # shortest: no route can be beaten by the straight road-graph distance from the field
        far = max(c.spots(), key=lambda s: G.dist(a, s.door.pt))
        assert c.net.field(a).to(far.door) == pytest.approx(c.net.route(a, far.door.pt).length, abs=1e-6)


def test_routing_is_deterministic_and_symmetric_in_length(h):
    c = city(h)
    s1, s2 = c.spots()[3], c.spots()[40]
    r1, r2 = c.net.route(s1.door.pt, s2.door.pt), c.net.route(s1.door.pt, s2.door.pt)
    assert r1 == r2
    assert c.net.route(s2.door.pt, s1.door.pt).length == pytest.approx(r1.length, abs=1e-6)
    mid = N.point_along(r1.pts, r1.length / 3)                                                        # a start in the middle of a road works too
    r3 = c.net.route(mid, s2.door.pt)
    assert r3.length <= r1.length + 1e-6 and route_ok(c, r3.pts)


# ============================================================================================== walking
def start_walk(h, d, uid, ref="l5"):
    v = d.open(uid)
    o = open_obj(d, uid, v, ref)
    out = d.go(uid, o, t("fa", "mp.b.go"))
    out.sid, out.mid = v.sid, v.mid
    return v, o, out


def test_go_starts_a_journey_and_does_not_teleport(h, d):
    v = d.open(A)
    c = city(h)
    before = tuple(P.get_player(h.db, c.id, A))
    o = open_obj(d, A, v, "l5")
    assert t("fa", "mp.b.go") in [x for x, _ in o.buttons] and t("fa", "mp.obj.dist", d="", eta="")[:10] in o.caption
    out = d.go(A, o, t("fa", "mp.b.go"))
    tr = trip_row(h, A)
    assert tr and tr["state"] == "walking" and tr["dest_id"] == "l5" and tr["dest_kind"] == "landmark"
    after = P.get_player(h.db, c.id, A)
    assert (after["x"], after["y"]) == (before[2], before[3]) and after["at_place"] is None              # still where they were
    assert tr["started_at"] == core.now() and tr["arrive_at"] > tr["started_at"] and tr["speed"] == C.walk_speed()
    assert out.edits and t("fa", "mp.tr.title") in out.caption and "L5" in out.caption
    assert out.toasts and "کازینو" in out.toasts[0]                                                    # «you set off for the casino»
    assert t("fa", "mp.b.go") not in [x for x, _ in out.buttons]                                       # no second start offered
    assert not out.alerts


def test_duration_is_route_length_over_walking_speed(h, d, monkeypatch):
    for speed in ("10", "4", "25.5"):
        db = Database(":memory:")
        monkeypatch.setenv("WALK_SPEED", speed)
        world.save_world(db, CH, world.build(7))
        c = worldplaces.load_city(db, world.get_world(db, CH))
        core.ensure_user(db, A, "a", "A", lang="fa")
        out = P.start_trip(db, c, A, "l5", now=1_700_000_000)
        assert out.status == "started"
        tr = out.trip
        route = P.route_of(tr)
        assert tr["length"] == pytest.approx(N.path_length(route), abs=0.05)
        assert tr["speed"] == float(speed)
        assert tr["arrive_at"] - tr["started_at"] == max(1, math.ceil(tr["length"] / float(speed) - 1e-9))
        assert P.stand(db, c, A, now=tr["started_at"]).walking and not P.stand(db, c, A, now=tr["arrive_at"]).walking


def test_no_randomness_in_travel(h, d):
    c = city(h)
    runs = []
    for _ in range(3):
        db = Database(":memory:")
        world.save_world(db, CH, world.build(7))
        cc = worldplaces.load_city(db, world.get_world(db, CH))
        core.ensure_user(db, A, "a", "A", lang="fa")
        tr = P.start_trip(db, cc, A, "p20", now=1_700_000_000).trip
        runs.append((tr["route"], tr["length"], tr["arrive_at"]))
    assert runs[0] == runs[1] == runs[2]


def test_refresh_shows_progress_from_elapsed_time(h, d):
    v, o, out = start_walk(h, d, A)
    c = city(h)
    tr = trip_row(h, A)
    span = tr["arrive_at"] - tr["started_at"]
    pics = []
    for frac in (0.0, 0.25, 0.5, 0.75):
        core_now = tr["started_at"] + int(span * frac)
        h.advance(max(0, core_now - core.now()))
        r = d.press(A, f"mp:{v.sid}:rf", v)
        st = P.stand(h.db, c, A)
        assert st.walking and st.pos == P.trip_position(tr, core.now())
        assert f"{fmt.loc(str(round(st.progress * 100)), 'fa')}٪" in r.caption, (frac, r.caption)             # the progress line says what the clock says
        assert t("fa", "mp.b.trip") in [x for x, _ in r.buttons]
        pics.append(r.photo)
    assert len(set(pics)) == 4                                                                       # the marker really moves between refreshes
    h.advance(span)                                                                                  # now past the arrival time
    r = d.press(A, f"mp:{v.sid}:rf", v)
    row = P.get_player(h.db, c.id, A)
    assert trip_row(h, A)["state"] == "arrived" and row["at_place"] == "l5"
    assert (row["x"], row["y"]) == tuple(round(q, 2) for q in P.route_of(tr)[-1])
    assert "رسیدی" in r.caption and any("رسیدی" in x for x in r.toasts)                               # the first look after arrival says so
    assert t("fa", "mp.b.trip") not in [x for x, _ in r.buttons]


def test_arrival_is_applied_lazily_once_and_not_by_any_timer(h, d):
    v, o, out = start_walk(h, d, A)
    c = city(h)
    tr = trip_row(h, A)
    h.advance(tr["arrive_at"] - tr["started_at"] + 3600)                                              # nobody looks for an hour
    assert trip_row(h, A)["state"] == "walking"                                                      # nothing ran in the background
    assert P.settle(h.db, c.id, A) is True and P.settle(h.db, c.id, A) is False                      # applied once, the repeat changes nothing
    assert P.get_player(h.db, c.id, A)["at_place"] == "l5" and h.db.q1("SELECT COUNT(*) c FROM world_trips WHERE state='arrived'")["c"] == 1
    st = P.stand(h.db, c, A)
    assert not st.walking and st.arrived is None


def test_a_second_journey_cannot_start_while_walking(h, d):
    v, o, out = start_walk(h, d, A)
    c = city(h)
    tr = trip_row(h, A)
    o2 = open_obj(d, A, v, "l1")                                                                      # looking at another place is fine
    assert t("fa", "mp.b.go") not in [x for x, _ in o2.buttons] and t("fa", "mp.obj.busy", dest="")[:10] in o2.caption
    forged = d.press(A, f"mp:{v.sid}:go:l1", v)                                                      # a hand-made «go» for it
    assert forged.edits and not forged.alerts and "کازینو" in forged.toasts[-1]                      # told «you are still on the way to the casino»
    assert h.db.q1("SELECT COUNT(*) c FROM world_trips")["c"] == 1 and trip_row(h, A)["id"] == tr["id"] and trip_row(h, A)["dest_id"] == "l5"
    assert P.start_trip(h.db, c, A, "l1").status == "busy"
    again = P.start_trip(h.db, c, A, "l5")
    assert again.status == "busy" and again.same                                                     # same place twice: the same single trip
    import sqlite3
    with pytest.raises(sqlite3.IntegrityError):                                                      # and the database itself refuses two walks
        h.db.x("INSERT INTO world_trips(world_id,user_id,dest_id,dest_kind,route,length,speed,started_at,arrive_at,state) VALUES(?,?,?,?,?,?,?,?,?,'walking')",
               (c.id, A, "l2", "landmark", "[[0,0],[1,1]]", 1.0, 10.0, 0, 1))


def test_a_repeated_go_press_starts_one_trip(h, d):
    v = d.open(A)
    o = open_obj(d, A, v, "l5")
    data = o.find(t("fa", "mp.b.go"))
    first = d.press(A, data, o)
    second = d.press(A, data, o)                                                                      # the same button delivered twice
    assert h.db.q1("SELECT COUNT(*) c FROM world_trips")["c"] == 1
    assert first.edits and not first.alerts
    assert len(second.alerts) == 1 and not second.edits                                               # the repeat is a stale button: refused, not applied again


def test_walking_to_the_place_you_stand_at_is_answered_without_a_trip(h, d):
    v, o, out = start_walk(h, d, A)
    tr = trip_row(h, A)
    h.advance(tr["arrive_at"] - tr["started_at"] + 1)
    o = d.press(A, f"mp:{v.sid}:o:l5", v)
    assert t("fa", "mp.obj.here") in o.caption and t("fa", "mp.b.go") not in [x for x, _ in o.buttons]
    assert t("fa", "mp.b.enter") in [x for x, _ in o.buttons]
    again = d.press(A, f"mp:{v.sid}:go:l5", o)
    assert h.db.q1("SELECT COUNT(*) c FROM world_trips")["c"] == 1


def test_a_new_trip_can_start_after_arriving_and_begins_where_the_last_ended(h, d):
    v, o, out = start_walk(h, d, A)
    c = city(h)
    t1 = trip_row(h, A)
    h.advance(t1["arrive_at"] - t1["started_at"] + 1)
    o = open_obj(d, A, d.press(A, f"mp:{v.sid}:rf", v), "l1")
    d.go(A, o, t("fa", "mp.b.go"))
    t2 = trip_row(h, A)
    assert t2["id"] != t1["id"] and t2["state"] == "walking" and t2["dest_id"] == "l1"
    assert P.route_of(t2)[0] == tuple(round(q, 2) for q in P.route_of(t1)[-1]) or G.dist(P.route_of(t2)[0], P.route_of(t1)[-1]) < 0.1
    assert P.get_player(h.db, c.id, A)["at_place"] is None                                            # on the way again
    assert h.db.q1("SELECT COUNT(*) c FROM world_trips WHERE state='walking'")["c"] == 1


def test_other_players_see_a_walker_where_the_route_and_clock_put_them(h, d):
    v, o, out = start_walk(h, d, A)
    d.open(B)
    c = city(h)
    tr = trip_row(h, A)
    span = tr["arrive_at"] - tr["started_at"]
    for frac in (0.2, 0.6):
        h.advance(max(0, tr["started_at"] + int(span * frac) - core.now()))
        me = P.trip_position(tr, core.now())
        put(h, B, me)                                                                                 # B stands on the walker's spot
        got = P.nearby(h.db, c, B, me)
        assert [x.uid for x in got] == [A] and got[0].pos == me and got[0].walking and got[0].dist < 1
    assert tuple(P.get_player(h.db, c.id, A))[2:4] == tuple(P.get_player(h.db, c.id, A))[2:4]


def test_walkers_persist_across_a_restart_and_arrive_afterwards(tmp_path):
    path = str(tmp_path / "game.db")
    T = [1_700_000_000]
    core.set_clock(lambda: T[0])
    worldmap.cache_clear()
    worldplaces.cache_clear()
    h1 = tb.Harness()
    core.set_clock(lambda: T[0])
    h1.db.close()
    db = Database(path)
    h1.db = h1.app.db = db
    world.save_world(db, CH, world.build(7))
    d1 = mapkit.Driver(h1, CH)
    tb.T[0] = T[0]
    v = d1.open(A)
    o = open_obj(d1, A, v, "l5")
    d1.go(A, o, t("fa", "mp.b.go"))
    tr = db.q1("SELECT * FROM world_trips WHERE user_id=?", (A,))
    saved = tuple(tr)
    sid, mid = v.sid, v.mid
    db.close()                                                                                        # the bot stops here
    h2 = tb.Harness()
    db2 = Database(path)
    h2.db = h2.app.db = db2
    worldplaces.cache_clear()
    worldmap.cache_clear()
    assert tuple(db2.q1("SELECT * FROM world_trips WHERE user_id=?", (A,))) == saved                  # route, speed, times, state: exactly as saved
    tb.T[0] += (tr["arrive_at"] - tr["started_at"]) // 2
    mid_view = mapkit.Out(h2.press(A, f"mp:{sid}:rf", chat=CH, mid=mid, photo=True))
    assert mid_view.edits and not mid_view.alerts                                                    # the old message's buttons still work
    c2 = worldplaces.load_city(db2, world.get_world(db2, CH))
    assert P.stand(db2, c2, A).walking and P.stand(db2, c2, A).pos == P.trip_position(tr, tb.T[0])
    tb.T[0] = tr["arrive_at"] + 5
    done = mapkit.Out(h2.press(A, f"mp:{sid}:rf", chat=CH, mid=mid, photo=True))
    assert db2.q1("SELECT state FROM world_trips WHERE user_id=?", (A,))["state"] == "arrived" and P.get_player(db2, c2.id, A)["at_place"] == "l5"
    assert done.edits


def test_a_damaged_saved_route_recovers_from_the_destination(h, d):
    v, o, out = start_walk(h, d, A)
    c = city(h)
    tr = trip_row(h, A)
    h.db.x("UPDATE world_trips SET route='not json' WHERE id=?", (tr["id"],))
    r = d.press(A, f"mp:{v.sid}:rf", v)                                                               # a refresh does not crash while it is walking
    assert r.edits and not r.alerts
    h.advance(tr["arrive_at"] - tr["started_at"] + 1)
    r = d.press(A, f"mp:{v.sid}:rf", v)
    assert r.edits and trip_row(h, A)["state"] == "arrived" and P.get_player(h.db, c.id, A)["at_place"] == "l5"


def test_a_trips_speed_is_the_one_it_was_planned_with(h, d, monkeypatch):
    v, o, out = start_walk(h, d, A)
    tr = trip_row(h, A)
    monkeypatch.setenv("WALK_SPEED", "100")
    st = P.stand(h.db, city(h), A)
    assert st.trip["arrive_at"] == tr["arrive_at"] and st.trip["speed"] == 10.0


def test_travel_touches_nothing_in_the_economy(h, d):
    d.open(B)                                                                                          # the group (and its index stock) exists once anybody uses a command
    h.say(A, "daily")
    d.open(A)
    before = econ_snapshot(h.db)
    v = d.open(A)
    o = open_obj(d, A, v, "l5")
    d.go(A, o, t("fa", "mp.b.go"))
    tr = trip_row(h, A)
    d.press(A, f"mp:{v.sid}:nb", v)
    d.press(A, f"mp:{v.sid}:ln:1", v)
    h.advance(tr["arrive_at"] - tr["started_at"] + 1)
    d.press(A, f"mp:{v.sid}:rf", v)
    after = econ_snapshot(h.db)
    assert after == before
    assert h.db.q1("SELECT COUNT(*) c FROM news")["c"] == len(before["news"])


# ============================================================================================== the casino needs arrival
def test_nobody_can_enter_the_casino_or_play_before_arriving(h, d):
    v = d.open(A)
    o = d.go(A, v, t("fa", "mp.b.casino"))
    assert "L5" in o.caption and t("fa", "mp.b.enter") not in [x for x, _ in o.buttons] and t("fa", "mp.b.go") in [x for x, _ in o.buttons]
    far = d.press(A, f"mp:{v.sid}:ce", o)                                                             # forged «enter» from far away
    assert far.alerts == [t("fa", "mp.cas.need")] and not far.edits
    refreshed = d.press(A, f"mp:{v.sid}:rf", v)                                                       # a refresh does not replace walking there
    again = d.press(A, f"mp:{v.sid}:ce", refreshed)
    assert again.alerts and not again.edits
    for data in ("bj:o", "bj:n:0", "bj:h:1", "bj:s:1"):
        out = d.press(A, f"mp:{v.sid}:{data}", v)
        assert out.alerts and not out.edits, data
    assert h.db.q1("SELECT COUNT(*) c FROM casino_games")["c"] == 0
    o = d.press(A, f"mp:{v.sid}:o:casino", v)
    d.go(A, o, t("fa", "mp.b.go"))                                                                    # on the way: still not inside
    out = d.press(A, f"mp:{v.sid}:ce", o)
    assert out.alerts and not out.edits and h.db.q1("SELECT COUNT(*) c FROM casino_games")["c"] == 0
    assert d.press(A, f"mp:{v.sid}:bj:o", v).alerts
