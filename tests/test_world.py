"""World Engine v1: one persistent, procedurally generated city per group, the «مپ» command and the menu button.

The generator draws organic cities (bending roads, curved neighbourhoods, a river, parks); the renderer draws them in a dark,
muted forest-green style. Everything here is about behaviour: determinism, validity, persistence, isolation, concurrency,
rendering and the Telegram integration."""
import asyncio
import io
import json
import math
import random
import sqlite3
import threading
from collections import Counter

import pytest
from aiogram.methods import AnswerCallbackQuery, EditMessageText, SendMessage, SendPhoto
from PIL import Image

from hypemarket import character, cards, core, db as dbmod, docs, handlers, sections, social, textparse, views, world, worldgeom as G, worldmap
from hypemarket.db import SCHEMA_VERSION, Database
from hypemarket.texts import ALIASES, EN, FA

import mapkit
import test_bot as tb

CH1, CH2 = -100606, -100777


@pytest.fixture()
def h():
    worldmap.cache_clear()
    return tb.Harness()


def photos(calls):
    return [c for c in calls if isinstance(c, SendPhoto)]


SEEDS = [0, 1, 7, 42, 99, 999, 2 ** 40 + 3, 123456789]


# ----------------------------------------------------------------------------------------------------- generator
@pytest.mark.parametrize("seed", SEEDS)
def test_generated_cities_are_valid_and_complete(seed):
    L = world.build(seed)
    assert L["version"] == world.LAYOUT_VERSION and L["generator"] == world.GENERATOR_VERSION
    assert 0 < L["w"] <= world.MAX_SIZE and 0 < L["h"] <= world.MAX_SIZE
    assert {d["kind"] for d in L["districts"]} == set(world.DISTRICTS) and len(L["districts"]) >= 8
    assert {r["kind"] for r in L["roads"]} == {"main", "street"} and sum(r["kind"] == "main" for r in L["roads"]) >= 3
    assert {lm["kind"] for lm in L["landmarks"]} == set(world.LANDMARKS)                                 # all four public places exist
    st = world.stats(L)
    assert st["vacant"] >= world.MIN_VACANT_SHARE * (st["vacant"] + st["built"]) and st["built"] > 0 and st["green"] > 0
    assert 60 <= st["parcels"] <= 400                                                                     # a real city, not a hamlet
    assert L["water"] and L["terrain"]["contours"]
    assert L["name"]["fa"] and L["name"]["en"]
    assert len(world.dumps(L)) < 400_000                                                                  # small enough to store and parse


def test_ids_are_stable_logical_strings_and_unique():
    L = world.build(5)
    for key, prefix in (("roads", "r"), ("districts", "d"), ("blocks", "b"), ("parcels", "p"), ("landmarks", "l"), ("water", "w")):
        ids = [x["id"] for x in L[key]]
        assert len(set(ids)) == len(ids) and all(isinstance(i, str) and i.startswith(prefix) for i in ids), key
    parcels = {p["id"] for p in L["parcels"]}
    assert all(lm["parcel"] in parcels for lm in L["landmarks"])
    blocks = {b["id"] for b in L["blocks"]}
    assert all(p["block"] in blocks for p in L["parcels"])
    assert all(b["district"] in {d["id"] for d in L["districts"]} for b in L["blocks"])
    again = world.build(5)
    assert [p["id"] for p in again["parcels"]] == [p["id"] for p in L["parcels"]]                        # same ids on every generation


def test_same_seed_same_city_and_different_seeds_differ():
    a, b = world.generate(31), world.generate(31)
    assert a == b and world.dumps(a) == world.dumps(b)
    others = [world.build(s) for s in range(32, 38)]
    assert all(o != a for o in others)
    assert len({world.dumps(o) for o in others}) == len(others)
    assert len({len(o["roads"]) for o in others}) > 2 and len({len(o["parcels"]) for o in others}) > 2    # real structural variety
    assert len({o["name"]["en"] for o in others + [a]}) > 1
    with pytest.raises(world.LayoutError):
        world.generate(31, version=world.GENERATOR_VERSION + 1)


def test_generation_leaves_the_global_random_state_alone():
    random.seed(12345)
    before = random.getstate()
    expected = random.Random(12345).random()
    for s in range(6):
        world.build(s)
    assert random.getstate() == before and random.random() == expected


def test_generation_is_independent_of_call_order_and_threads():
    solo = world.dumps(world.build(77))
    world.build(78)
    results = []
    ts = [threading.Thread(target=lambda: results.append(world.dumps(world.build(77)))) for _ in range(3)]
    for t in ts:
        t.start()
    for t in ts:
        t.join()
    assert results == [solo] * 3 and world.dumps(world.build(77)) == solo


def test_the_street_network_is_organic_not_a_grid():
    shares, dead_ends, curved = [], 0, 0
    for seed in (3, 11, 21, 34, 55):
        L = world.build(seed)
        total = axis = 0.0
        deg: Counter = Counter()
        foot = [tuple(p) for p in L["footprint"]]
        for r in L["roads"]:
            deg[tuple(r["pts"][0])] += 1
            deg[tuple(r["pts"][-1])] += 1
            for p, q in zip(r["pts"], r["pts"][1:]):
                ln = math.dist(p, q)
                total += ln
                ang = math.degrees(math.atan2(q[1] - p[1], q[0] - p[0])) % 90
                axis += ln if min(ang, 90 - ang) < 4 else 0
            if len(r["pts"]) > 4:
                curved += 1
        shares.append(axis / total)
        dead_ends += sum(1 for n, v in deg.items() if v == 1 and G.polyline_dist(foot + [foot[0]], n) > 14)
        # block shapes differ a lot: no repeated cell
        areas = sorted(round(abs(G.area(b["poly"]))) for b in L["blocks"])
        assert len(set(areas)) > 0.9 * len(areas) and areas[-1] > 6 * areas[0]
    assert max(shares) < 0.25                                                                             # a grid would be near 1.0
    assert dead_ends == 0                                                                                 # every street leads somewhere
    assert curved > 100                                                                                   # roads bend


def test_roads_connect_lots_stay_off_them_and_nothing_overlaps():
    L = world.build(5)
    # connected network (the validator checks it too; verify independently with a union-find)
    parent: dict = {}

    def find(a):
        while parent.setdefault(a, a) != a:
            parent[a] = parent[parent[a]]
            a = parent[a]
        return a
    for r in L["roads"]:
        parent[find(tuple(r["pts"][0]))] = find(tuple(r["pts"][-1]))
    assert len({find(k) for k in list(parent)}) == 1
    # every lot polygon is simple, clear of every road, and lots do not overlap one another
    lots = [p for p in L["parcels"] if p["use"] != "green"]
    for i, p in enumerate(lots):
        assert abs(G.area(p["poly"])) > 80
        for r in L["roads"]:
            half = world.ROAD_HALF[r["kind"]]
            for a, b in zip(r["pts"], r["pts"][1:]):
                assert G.poly_seg_clearance(p["poly"], a, b) >= half, (p["id"], r["id"])
    for i in range(len(lots)):
        for j in range(i + 1, len(lots)):
            a, b = lots[i], lots[j]
            if math.dist(a["c"], b["c"]) < 120:
                assert not G.polys_overlap(a["poly"], b["poly"], 0.02), (a["id"], b["id"])
    # nothing is built in the river and every lot is inside the city
    foot = [tuple(p) for p in L["footprint"]]
    for p in lots:
        assert G.point_in_poly(tuple(p["c"]), foot), p["id"]
        assert G.polyline_dist(L["water"][0]["pts"], p["c"]) > max(L["water"][0]["hw"]), p["id"]
    # every block touches the network: nobody is landlocked
    for b in L["blocks"]:
        assert any(G.polyline_dist(r["pts"], G.centroid(b["poly"])) < 400 for r in L["roads"])


def test_landmarks_are_whole_places_on_their_own_lots():
    for seed in (2, 8, 13):
        L = world.build(seed)
        pid = {p["id"]: p for p in L["parcels"]}
        assert {lm["kind"] for lm in L["landmarks"]} == set(world.LANDMARKS)
        for lm in L["landmarks"]:
            p = pid[lm["parcel"]]
            assert p["use"] == "landmark" and G.point_in_poly(tuple(lm["at"]), p["poly"])
        assert sum(p["use"] == "landmark" for p in L["parcels"]) == len(L["landmarks"])


def test_validator_catches_broken_layouts():
    def broken(mutate):
        L = world.build(9)
        mutate(L)
        with pytest.raises(world.LayoutError):
            world.validate(L)
    broken(lambda L: L.__setitem__("w", 0))
    broken(lambda L: L.__setitem__("h", float("inf")))
    broken(lambda L: L["districts"].pop())
    broken(lambda L: L["parcels"].append(dict(L["parcels"][0])))                                           # duplicate id
    broken(lambda L: L["parcels"][0].__setitem__("poly", [[0, 0], [1, 0], [1, 1]]) or L["parcels"][0].__setitem__("c", [L["w"] + 5, 1]))
    broken(lambda L: L.__setitem__("landmarks", []))
    broken(lambda L: [p.__setitem__("use", "building") for p in L["parcels"] if p["use"] == "vacant"])    # no land left for the future
    broken(lambda L: L.__setitem__("version", 99))
    broken(lambda L: L.__setitem__("name", {"fa": ""}))
    broken(lambda L: L.__setitem__("roads", [r for r in L["roads"] if r["kind"] != "main"]))
    broken(lambda L: L["roads"][0]["pts"].append([float("nan"), 1.0]))
    broken(lambda L: L["roads"].append({"id": "rZ", "kind": "street", "bridge": False, "pts": [[1.0, 1.0], [30.0, 30.0]]}))   # floats off the network
    broken(lambda L: L["landmarks"].pop())                                                                  # a public place is missing
    broken(lambda L: L["roads"].__delitem__(slice(0, len(L["roads"]) // 2)))                               # network split / lots on nothing
    broken(lambda L: L["parcels"].__setitem__(0, {**L["parcels"][0], "id": float("nan")}))
    broken(lambda L: L["parcels"][1].__setitem__("poly", L["parcels"][2]["poly"]))                       # two lots in one place
    broken(lambda L: L["blocks"][0].__setitem__("district", "dZ"))
    broken(lambda L: L["terrain"].__setitem__("contours", [[[1, 1]]]))
    for notalayout in (None, [], "x", {}, {"version": world.LAYOUT_VERSION}):
        with pytest.raises((world.LayoutError, TypeError, KeyError)):
            world.validate(notalayout)


def test_a_street_that_ends_in_the_middle_of_nowhere_is_rejected():
    L = world.build(9)
    c = [round(v, 1) for v in G.centroid([tuple(p) for p in L["footprint"]])]
    L["nodes"].append(c)
    L["roads"].append({"id": "rD", "kind": "street", "bridge": False, "pts": [list(L["roads"][0]["pts"][0]), c]})
    with pytest.raises(world.LayoutError, match="middle of nowhere"):
        world.validate(L)


def test_build_is_bounded(monkeypatch):
    calls = []

    def always_bad(seed, version=world.GENERATOR_VERSION):
        calls.append(seed)
        raise world.LayoutError("nope")
    monkeypatch.setattr(world, "generate", always_bad)
    with pytest.raises(world.WorldError) as e:
        world.build(1)
    assert e.value.code == "generation_failed" and len(calls) == world.MAX_ATTEMPTS


def test_build_recovers_from_a_bad_first_attempt(monkeypatch):
    real = world.generate
    seen = []

    def flaky(seed, version=world.GENERATOR_VERSION):
        seen.append(seed)
        if len(seen) == 1:
            raise world.LayoutError("first try fails")
        return real(seed, version)
    monkeypatch.setattr(world, "generate", flaky)
    L = world.build(4)
    assert len(seen) == 2 and L["seed"] == 5 and world.validate(L)                                       # the layout records the seed that made it


# ----------------------------------------------------------------------------------------------------- renderer
def _png(L, lang="en"):
    return Image.open(io.BytesIO(worldmap.render(L, lang))).convert("RGB")


@pytest.mark.parametrize("lang", ["fa", "en"])
def test_render_is_a_real_png_and_does_not_touch_the_layout(lang):
    L = world.build(7)
    frozen = json.dumps(L, sort_keys=True)
    png = worldmap.render(L, lang)
    assert json.dumps(L, sort_keys=True) == frozen
    img = Image.open(io.BytesIO(png))
    assert img.format == "PNG" and 800 <= img.width <= 1280 and 700 <= img.height <= 1280          # fits Telegram's photo limits
    assert 5_000 < len(png) < worldmap.MAX_PNG
    assert worldmap.render(L, lang) == png                                                        # deterministic


def test_render_uses_the_dark_forest_palette_and_nothing_bright():
    img = _png(world.build(7))
    assert img.getpixel((3, 3)) == worldmap.BG == (8, 11, 9)                                      # #080B09
    assert worldmap.LAND == (16, 24, 18) and worldmap.STREET == (36, 59, 42) and worldmap.MAIN == (69, 101, 74)
    counts = Counter(zip(*[iter(img.tobytes())] * 3))
    for color in (worldmap.LAND, worldmap.STREET, worldmap.MAIN):
        assert counts[color] > 400, color                                                         # the specified colours are really used
    brightest = 0
    for (r, g, b), n in counts.items():
        if max(r, g, b) >= 20:
            assert g >= r - 3 and g >= b - 3, (r, g, b)                                           # green-dominant everywhere: no red/blue/neon
        assert max(r, g, b) <= 180, (r, g, b)                                                     # nothing glows
        brightest = max(brightest, max(r, g, b))
    assert brightest >= 120                                                                       # but the labels can still be read
    dark = sum(n for (r, g, b), n in counts.items() if max(r, g, b) < 60)
    assert dark / sum(counts.values()) > 0.9                                                      # almost-black overall
    assert len(counts) < 6000                                                                     # no gradients or photographic noise


def test_render_labels_and_titles_in_both_languages():
    L = world.build(7)
    fa, en = worldmap.render(L, "fa"), worldmap.render(L, "en")
    assert fa != en
    box = (0, 0, 1200, worldmap.HEADER)
    for png in (fa, en):
        header = Image.open(io.BytesIO(png)).convert("RGB").crop(box)
        assert len(set(zip(*[iter(header.tobytes())] * 3))) > 5                                    # the title is really drawn


def test_render_falls_back_without_the_bundled_font(monkeypatch, tmp_path):
    monkeypatch.setattr(cards, "FONT_DIR", tmp_path / "missing")                                  # Vazirmatn gone: Pillow's default font steps in
    cards.font.cache_clear()
    try:
        for lang in ("fa", "en"):
            assert Image.open(io.BytesIO(worldmap.render(world.build(3), lang))).format == "PNG"
    finally:
        cards.font.cache_clear()


def test_every_text_the_map_needs_exists_in_both_languages():
    for lang, d in (("fa", FA), ("en", EN)):
        for k in ("map.caption", "map.private", "map.corrupt", "map.failed", "map.title", "map.subtitle", "map.legend.vacant", "map.legend.landmark",
                  "map.legend.main", "map.legend.street", "map.legend.building", "map.legend.park", "menu.map", "cat.city"):
            assert k in d, (lang, k)
        for z in world.DISTRICTS:
            assert f"map.zone.{z}" in d
        for lm in world.LANDMARKS:
            assert f"map.lm.{lm}" in d


def test_picture_cache_draws_once_and_never_changes_the_city(monkeypatch):
    worldmap.cache_clear()
    L = world.build(7)
    calls = []
    real = worldmap.render
    monkeypatch.setattr(worldmap, "render", lambda layout, lang="fa": calls.append(lang) or real(layout, lang))

    async def go():
        a = await worldmap.render_cached(1, L, "en")
        b = await worldmap.render_cached(1, L, "en")
        c = await worldmap.render_cached(1, L, "fa")
        return a, b, c
    a, b, c = asyncio.run(go())
    assert a == b and a != c and calls == ["en", "fa"]
    for i in range(worldmap.CACHE_MAX + 5):
        asyncio.run(worldmap.render_cached(100 + i, L, "en"))
    assert len(worldmap._cache) <= worldmap.CACHE_MAX                                              # bounded memory


def test_drawing_does_not_block_the_event_loop():
    L = world.build(7)
    worldmap.cache_clear()
    ticks = []

    async def go():
        async def ticker():
            for _ in range(200):
                ticks.append(1)
                await asyncio.sleep(0.002)
        task = asyncio.create_task(ticker())
        await worldmap.render_cached(555, L, "en")
        before = len(ticks)
        await task
        return before
    assert asyncio.run(go()) >= 3                                                                  # the loop kept running while the picture was drawn


# ----------------------------------------------------------------------------------------------------- persistence
def test_first_use_creates_one_world_and_later_uses_reuse_it():
    d = Database(":memory:")
    assert world.get_world(d, CH1) is None
    w, created = world.ensure_world(d, CH1)
    assert created and w["chat_id"] == CH1 and w["gen_version"] == world.GENERATOR_VERSION
    L = world.load_layout(w)
    assert L["name"] == {"fa": w["name_fa"], "en": w["name_en"]} and (L["w"], L["h"]) == (w["cols"], w["rows"])
    for _ in range(3):
        w2, created2 = world.ensure_world(d, CH1)
        assert not created2 and dict(w2) == dict(w)
    assert d.q1("SELECT COUNT(*) c FROM worlds")["c"] == 1
    assert world.generate(w["seed"], w["gen_version"]) == L                                          # the saved layout is what that seed generates


def test_the_slow_generation_never_runs_inside_a_write_transaction(monkeypatch):
    d = Database(":memory:")
    seen = []
    real = world.build

    def watch(seed):
        seen.append(d.conn.in_transaction)
        return real(seed)
    monkeypatch.setattr(world, "build", watch)
    world.ensure_world(d, CH1)
    asyncio.run(world.aensure_world(d, CH2))
    assert seen == [False, False]                                                                   # the database was not locked while drawing up the city


def test_worlds_survive_a_restart_and_groups_are_isolated(tmp_path):
    path = str(tmp_path / "w.db")
    d = Database(path)
    w1, _ = world.ensure_world(d, CH1)
    w2, _ = world.ensure_world(d, CH2)
    assert w1["id"] != w2["id"] and w1["seed"] != w2["seed"] and w1["layout"] != w2["layout"]
    d.close()
    d = Database(path)                                                                              # "the bot restarted"
    a, created = world.ensure_world(d, CH1)
    b, created2 = world.ensure_world(d, CH2)
    assert not created and not created2 and dict(a) == dict(w1) and dict(b) == dict(w2)
    assert world.get_world(d, -100999) is None
    assert d.q1("SELECT COUNT(*) c FROM worlds")["c"] == 2
    with pytest.raises(sqlite3.IntegrityError):                                                     # the database itself refuses a second world
        d.x("INSERT INTO worlds(chat_id,seed,gen_version,name_fa,name_en,cols,rows,layout,created_at) VALUES(?,1,1,'a','b',1,1,'{}',0)", (CH1,))


def test_racing_threads_end_up_with_the_same_world(tmp_path):
    path = str(tmp_path / "race.db")
    Database(path).close()
    results, errors = [], []

    def worker():
        try:
            d = Database(path)
            results.append(dict(world.ensure_world(d, CH1)[0]))
            d.close()
        except Exception as e:                                                                      # pragma: no cover - reported below
            errors.append(e)
    threads = [threading.Thread(target=worker) for _ in range(6)]
    for th in threads:
        th.start()
    for th in threads:
        th.join()
    assert not errors and len(results) == 6
    assert len({r["id"] for r in results}) == 1 and len({r["layout"] for r in results}) == 1
    assert Database(path).q1("SELECT COUNT(*) c FROM worlds")["c"] == 1


def test_racing_requests_on_the_loop_build_the_city_once(monkeypatch):
    d = Database(":memory:")
    built = []
    real = world.build
    monkeypatch.setattr(world, "build", lambda seed: built.append(seed) or real(seed))

    async def go():
        return await asyncio.gather(*[world.aensure_world(d, CH1) for _ in range(8)], *[world.aensure_world(d, CH2) for _ in range(3)])
    out = asyncio.run(go())
    ones = [dict(w) for w, _ in out[:8]]
    assert len({o["id"] for o in ones}) == 1 and sum(created for _, created in out[:8]) == 1
    assert len(built) == 2                                                                          # once per group, never per request
    assert d.q1("SELECT COUNT(*) c FROM worlds")["c"] == 2 and world._locks == {}


def test_two_loops_that_both_missed_keep_the_first_city(monkeypatch):
    """Even when two requests both decided to build, the database keeps exactly one and both get that one."""
    d = Database(":memory:")
    a = world.build(11)
    b = world.build(12)
    w1, c1 = world.save_world(d, CH1, a)
    w2, c2 = world.save_world(d, CH1, b)
    assert c1 and not c2 and dict(w1) == dict(w2) and w2["seed"] == a["seed"]
    assert d.q1("SELECT COUNT(*) c FROM worlds")["c"] == 1


def test_a_corrupt_world_is_reported_not_replaced():
    d = Database(":memory:")
    w, _ = world.ensure_world(d, CH1)
    good = w["layout"]
    for bad in ('{"version": 2', "garbage", "[]", "null", '{"version": 1}', json.dumps({**json.loads(good), "parcels": []}),
                json.dumps({**json.loads(good), "seed": 999999})):                                  # broken JSON, wrong shape, empty, mismatched metadata
        d.x("UPDATE worlds SET layout=? WHERE id=?", (bad, w["id"]))
        with pytest.raises(world.WorldError) as e:
            world.load_layout(world.get_world(d, CH1))
        assert e.value.code == "corrupt", bad
        w2, created = world.ensure_world(d, CH1)
        assert not created and w2["layout"] == bad                                                  # still the saved row: nothing regenerated
        with pytest.raises(world.WorldError):
            asyncio.run(world.aload_layout(w2))
    assert d.q1("SELECT COUNT(*) c FROM worlds")["c"] == 1


def test_schema_upgrade_adds_the_worlds_table_and_keeps_everything(tmp_path):
    path = str(tmp_path / "old.db")
    d = Database(path)
    core.ensure_user(d, 5, "alice", "Alice")
    d.x("UPDATE users SET cash=777 WHERE id=5")
    d.close()
    con = sqlite3.connect(path)
    con.execute("DROP TABLE worlds")
    con.execute("PRAGMA user_version=5")
    con.commit()
    con.close()
    d = Database(path)
    assert d.q1("PRAGMA user_version")[0] == SCHEMA_VERSION
    assert d.q1("SELECT 1 FROM sqlite_master WHERE type='table' AND name='worlds'")
    assert core.get_user(d, 5)["cash"] == 777
    world.ensure_world(d, CH1)
    d.close()
    d = Database(path)
    assert d.q1("PRAGMA user_version")[0] == SCHEMA_VERSION and d.q1("SELECT COUNT(*) c FROM worlds")["c"] == 1   # reopening is a no-op
    assert 6 in dbmod.MIGRATIONS and "worlds" in dbmod.SCHEMA


def test_backup_includes_worlds(tmp_path):
    d = Database(":memory:")
    w, _ = world.ensure_world(d, CH1)
    d.backup_to(str(tmp_path / "b.db"))
    con = sqlite3.connect(str(tmp_path / "b.db"))
    assert con.execute("SELECT COUNT(*) FROM worlds").fetchone()[0] == 1
    assert con.execute("SELECT layout FROM worlds").fetchone()[0] == w["layout"]


# ----------------------------------------------------------------------------------------------------- command and menu
def test_map_is_a_global_exact_command():
    assert "map" in sections.GLOBAL_CMDS and "map" not in sections.STOCK_CMDS and "map" in handlers.HANDLERS
    for word in ("مپ", "نقشه", "map", "/map", "مپ@TestBot", "!مپ", "Map"):
        p = textparse.parse(word, "TestBot")
        assert p is not None and p.cmd == "map" and p.section is None and not p.args, word
    for text in ("مپ کجاست؟", "اون مپ رو دیدی", "map of the world", "مپها", "نقشه‌ی گنج", "سلام"):
        p = textparse.parse(text, "TestBot")
        assert p is None or p.cmd != "map" or p.args, text
    assert "map" in docs.DOCS and any("map" in cmds for cid, cmds in docs.CATEGORIES if cid == "city")
    assert ALIASES["map"][0] == "مپ"


def test_group_map_sends_the_city_once_and_reuses_it(h):
    calls = h.say(1, "مپ", chat=CH1)
    ph = photos(calls)
    assert len(ph) == 1 and isinstance(ph[0].photo.data, bytes) and Image.open(io.BytesIO(ph[0].photo.data)).format == "PNG"
    w = world.get_world(h.db, CH1)
    assert w and w["name_fa"] in ph[0].caption and "فعال نیست" in ph[0].caption and len(ph[0].caption) <= 1024
    assert "خرید" in ph[0].caption and "هنوز" in ph[0].caption                                        # says clearly: no buying yet
    assert ph[0].chat_id == CH1
    before = {k: h.db.q1(f"SELECT COUNT(*) c FROM {k}")["c"] for k in ("stocks", "holdings", "history")}
    cash = core.get_user(h.db, 1)["cash"]
    again = photos(h.say(2, "نقشه", chat=CH1))
    assert len(again) == 1 and mapkit.same_city(again[0].photo.data, ph[0].photo.data)                  # same saved city, same picture (each player's own markers aside)
    assert h.db.q1("SELECT COUNT(*) c FROM worlds")["c"] == 1 and dict(world.get_world(h.db, CH1)) == dict(w)
    assert core.get_user(h.db, 1)["cash"] == cash                                                       # no money moves
    assert {k: h.db.q1(f"SELECT COUNT(*) c FROM {k}")["c"] for k in before} == before


def test_the_picture_follows_the_groups_language(h):
    fa = photos(h.say(1, "مپ", chat=CH1))[0]
    social.set_group_lang(h.db, CH1, "en")
    en = photos(h.say(1, "map", chat=CH1))[0]
    assert en.photo.data != fa.photo.data
    w = world.get_world(h.db, CH1)
    assert w["name_en"] in en.caption and w["name_fa"] in fa.caption and w["name_fa"] not in en.caption
    assert Image.open(io.BytesIO(en.photo.data)).size == Image.open(io.BytesIO(fa.photo.data)).size
    assert h.db.q1("SELECT COUNT(*) c FROM worlds")["c"] == 1                                       # one city, two languages


def test_a_player_has_one_city_whatever_group_they_type_in(h):
    a = photos(h.say(1, "مپ", chat=CH1))[0]
    b = photos(h.say(1, "مپ", chat=CH2))[0]
    assert world.get_world(h.db, CH2) is None and h.db.q1("SELECT COUNT(*) c FROM worlds")["c"] == 1            # the second group did not get a city for this player
    assert mapkit.same_city(a.photo.data, b.photo.data) and a.caption == b.caption


def test_every_player_gets_the_city_of_the_group_of_their_first_life_game_entry(h):
    a = photos(h.say(1, "مپ", chat=CH1))[0]
    b = photos(h.say(2, "مپ", chat=CH2))[0]
    wa, wb = world.get_world(h.db, CH1), world.get_world(h.db, CH2)
    assert wa["id"] != wb["id"] and a.photo.data != b.photo.data and a.caption != b.caption
    assert h.db.q1("SELECT COUNT(*) c FROM worlds")["c"] == 2
    assert character.get(h.db, 1)["current_world_id"] == wa["id"] and character.get(h.db, 2)["current_world_id"] == wb["id"]


def test_private_chat_explains_and_creates_nothing(h):
    calls = h.say(1, "مپ")
    assert not photos(calls) and "گروه" in h.texts(calls)
    assert h.db.q1("SELECT COUNT(*) c FROM worlds")["c"] == 0
    h.say(2, "شروع")
    h.db.x("UPDATE users SET lang='en' WHERE id=2")
    assert "group" in h.texts(h.say(2, "map")).lower()
    assert h.db.q1("SELECT COUNT(*) c FROM worlds")["c"] == 0


def test_chat_and_bad_arguments_never_build_a_city(h):
    for text in ("مپ کجاست؟", "اون مپ قشنگه", "مپ 12", "نقشه گنج", "سلام بچه‌ها", "map me"):
        assert h.say(1, text, chat=CH1) == [], text
    assert h.db.q1("SELECT COUNT(*) c FROM worlds")["c"] == 0


def test_the_menu_button_opens_the_same_city(h):
    h.say(1, "!منو", chat=CH1)
    menu = h.session.sent(SendMessage)[-1]
    cb = [b.callback_data for row in menu.reply_markup.inline_keyboard for b in row]
    assert "mn:go:map:1" in cb and all(len(b.encode()) <= 64 for b in cb)
    calls = h.press(1, "mn:go:map:1", chat=CH1)
    ph = photos(calls)
    assert len(ph) == 1 and ph[0].chat_id == CH1
    w = world.get_world(h.db, CH1)
    assert w and w["name_fa"] in ph[0].caption
    assert mapkit.same_city(photos(h.say(2, "مپ", chat=CH1))[0].photo.data, ph[0].photo.data)           # text command: same world (a player's own markers aside)
    assert h.db.q1("SELECT COUNT(*) c FROM worlds")["c"] == 1
    other = h.press(2, "mn:go:map:1", chat=CH1)                                                         # not the owner of the menu
    assert not photos(other) and any(isinstance(c, AnswerCallbackQuery) for c in other)
    assert h.db.q1("SELECT COUNT(*) c FROM worlds")["c"] == 1
    for target in ("profile", "daily", "invite"):                                                       # the old buttons still work
        e = [c for c in h.press(1, f"mn:go:{target}:1") if isinstance(c, EditMessageText)]
        assert e and e[-1].text


def test_menu_map_button_in_private_chat_explains_with_a_way_back(h):
    h.say(1, "منو")
    calls = h.press(1, "mn:go:map:1")
    e = [c for c in calls if isinstance(c, EditMessageText)][-1]
    assert "گروه" in e.text and [b.callback_data for row in e.reply_markup.inline_keyboard for b in row][-1] == "mn:menu:1"
    assert h.db.q1("SELECT COUNT(*) c FROM worlds")["c"] == 0


def test_telegram_or_render_failures_keep_the_world_and_allow_a_retry(h, monkeypatch):
    h.session.forbidden.add(CH1)
    calls = h.say(1, "مپ", chat=CH1)
    assert photos(calls) and h.db.q1("SELECT COUNT(*) c FROM worlds")["c"] == 1                         # Telegram rejected it; the world is saved
    w = dict(world.get_world(h.db, CH1))
    h.session.forbidden.discard(CH1)
    worldmap.cache_clear()

    def boom(layout, lang="fa"):
        raise RuntimeError("renderer exploded")
    monkeypatch.setattr(worldmap, "render", boom)
    calls = h.say(1, "مپ", chat=CH1)
    assert not photos(calls) and "جواب نداد" in h.texts(calls) and dict(world.get_world(h.db, CH1)) == w    # reported, not regenerated
    monkeypatch.undo()
    ph = photos(h.say(1, "مپ", chat=CH1))
    assert len(ph) == 1 and dict(world.get_world(h.db, CH1)) == w


def test_generation_failure_is_reported_and_leaves_no_half_city(h, monkeypatch):
    monkeypatch.setattr(world, "build", lambda seed: (_ for _ in ()).throw(world.WorldError("generation_failed")))
    calls = h.say(1, "مپ", chat=CH1)
    assert not photos(calls) and "جواب نداد" in h.texts(calls)
    assert h.db.q1("SELECT COUNT(*) c FROM worlds")["c"] == 0
    monkeypatch.undo()
    assert len(photos(h.say(1, "مپ", chat=CH1))) == 1                                                  # and it works on the next try


def test_corrupt_saved_world_is_reported_in_chat(h):
    h.say(1, "مپ", chat=CH1)
    h.db.x("UPDATE worlds SET layout='garbage' WHERE chat_id=?", (CH1,))
    calls = h.say(1, "مپ", chat=CH1)
    assert not photos(calls) and "خراب" in h.texts(calls)
    assert h.db.q1("SELECT layout FROM worlds WHERE chat_id=?", (CH1,))["layout"] == "garbage"         # untouched


def test_caption_fits_telegram_in_both_languages():
    d = Database(":memory:")
    w, _ = world.ensure_world(d, CH1)
    L = world.load_layout(w)
    for lang in ("fa", "en"):
        cap = views.map_caption(lang, w, L)
        assert len(cap) <= 1024 and (w["name_fa"] if lang == "fa" else w["name_en"]) in cap
