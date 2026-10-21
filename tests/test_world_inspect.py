"""World Interaction v1: inspecting any location of a group's city (`مپ P14`, `مپ L2`, `مپ D3`).

What must hold: every parcel, public place and district has a stable ID that is only ever looked up in its own group's saved city; the
panels say only what the layout really contains; the picture is the SAME map with one place marked; and inspecting never changes the
city, the players' money or progress, or the stock market."""
import asyncio
import copy
import io
import json
import re
import sqlite3
from collections import Counter

import pytest
from aiogram.methods import AnswerCallbackQuery, EditMessageText, SendMessage, SendPhoto
from PIL import Image, ImageChops

from hypemarket import core, docs, handlers, social, textparse, views, world, worldgeom as G, worldinspect as WI, worldmap
from hypemarket.db import Database
from hypemarket.texts import EN, FA, t

import mapkit
import test_bot as tb

CH1, CH2 = -100606, -100777
SEEDS = [1, 7, 42, 99, 123456789]


@pytest.fixture()
def h():
    worldmap.cache_clear()
    return tb.Harness()


@pytest.fixture(scope="module")
def city():
    return world.build(7)


def photos(calls):
    return [c for c in calls if isinstance(c, SendPhoto)]


def png(data):
    return Image.open(io.BytesIO(data)).convert("RGB")


def layout_of(h, chat=CH1):
    return world.load_layout(world.get_world(h.db, chat))


def snapshot(db, skip=("map_sessions",)):
    """Every table's rows: if inspecting changes anything anywhere, this changes. (Only the bookkeeping of the map message itself, the
    `map_sessions` rows that make its buttons work, is left out; v0.17 opens a session for every place looked at.)"""
    out = {}
    for r in db.qa("SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%' ORDER BY name"):
        if r["name"] in skip:
            continue
        out[r["name"]] = [tuple(x) for x in db.qa(f"SELECT * FROM {r['name']}")]
    return out


def first(L, use):
    return next(p for p in L["parcels"] if p["use"] == use)


# ----------------------------------------------------------------------------------------------------- identifiers
@pytest.mark.parametrize("seed", SEEDS)
def test_every_inspectable_object_has_a_valid_unique_id_that_resolves_to_itself(seed):
    L = world.build(seed)
    for key, prefix in (("parcels", "p"), ("landmarks", "l"), ("districts", "d")):
        ids = [o["id"] for o in L[key]]
        assert len(ids) == len(set(ids)) and ids, key
        assert all(re.fullmatch(prefix + r"[1-9]\d*", i) for i in ids), key
        assert ids == [f"{prefix}{n}" for n in range(1, len(ids) + 1)]                   # IDs count up with no gaps: nothing renumbered, nothing missing
        for o in L[key]:
            target = WI.resolve(L, o["id"])
            assert target is not None and target.obj is o and target.kind == {"p": "parcel", "l": "landmark", "d": "district"}[prefix]
            assert WI.parse_ref([WI.display_id(o["id"])]) == o["id"] == WI.normalize(o["id"].upper())
    assert {m["kind"] for m in L["landmarks"]} == set(world.LANDMARKS)                    # hospital, school, city hall, station: each has its own ID


def test_ids_do_not_depend_on_how_the_map_is_drawn(city, monkeypatch):
    before = {o["id"]: WI.facts(city, WI.resolve(city, o["id"])) for key in ("parcels", "landmarks", "districts") for o in city[key]}
    monkeypatch.setattr(worldmap, "SCALE", 0.6)
    monkeypatch.setattr(worldmap, "HEADER", 120)
    monkeypatch.setattr(worldmap, "LAND", (90, 40, 40))
    monkeypatch.setattr(worldmap, "ROAD_PX", {"main": 9.0, "street": 5.0})
    worldmap.render(city, "en", WI.highlight(city, WI.resolve(city, "p5")))                     # drawn differently, even highlighted
    after = {o["id"]: WI.facts(city, WI.resolve(city, o["id"])) for key in ("parcels", "landmarks", "districts") for o in city[key]}
    assert before == after


def test_the_same_id_resolves_to_the_same_object_after_a_restart(tmp_path):
    path = str(tmp_path / "w.db")
    d = Database(path)
    w, _ = world.ensure_world(d, CH1)
    L = world.load_layout(w)
    ids = [o["id"] for k in ("parcels", "landmarks", "districts") for o in L[k]]
    texts = {i: WI.inspect("fa", w, L, i).text for i in ids}
    d.close()
    d = Database(path)                                                                          # "the bot restarted"
    w2, created = world.ensure_world(d, CH1)
    L2 = world.load_layout(w2)
    assert not created and L2 == L and dict(w2) == dict(w)
    for i in ids:
        assert WI.resolve(L2, i).obj == WI.resolve(L, i).obj
        assert WI.inspect("fa", w2, L2, i).text == texts[i]


def test_ids_are_scoped_to_their_world(h):
    A0, B0 = world.build(5), world.build(6)                                                    # two known cities in two groups
    world.save_world(h.db, CH1, A0)
    world.save_world(h.db, CH2, B0)
    A, B = layout_of(h, CH1), layout_of(h, CH2)
    assert A == A0 and B == B0 and A["seed"] != B["seed"]
    for ident in ("p5", "l1", "d2"):                                                           # the same local ID exists in both cities...
        assert WI.resolve(A, ident) and WI.resolve(B, ident)
    a = photos(h.say(1, "مپ L1", chat=CH1))[0]
    b = photos(h.say(1, "مپ L1", chat=CH2))[0]
    wa, wb = world.get_world(h.db, CH1), world.get_world(h.db, CH2)
    assert wa["name_fa"] in a.caption and wb["name_fa"] in b.caption and a.caption != b.caption      # ...but each answer is its own city's
    fa = WI.facts(A, WI.resolve(A, "l1"))
    fb = WI.facts(B, WI.resolve(B, "l1"))
    assert fa["at"] != fb["at"] and fa["near"] != fb["near"]
    assert WI._n(fa["at"][0], "fa") in a.caption and f"<code>{fa['near'][0].upper()}</code>" in a.caption
    assert WI._n(fb["at"][0], "fa") in b.caption and f"<code>{fb['near'][0].upper()}</code>" in b.caption
    assert a.photo.data == mapkit.shown(h, CH1, 1, "fa", WI.highlight(A, WI.resolve(A, "l1")))         # and each picture is drawn from its own geometry
    assert b.photo.data == mapkit.shown(h, CH2, 1, "fa", WI.highlight(B, WI.resolve(B, "l1")))      # (plus its permanent places and the viewer's markers)
    na, nb = len(A["parcels"]), len(B["parcels"])
    assert na != nb                                                                              # (these two cities differ in size, so one ID exists in only one)
    small, big = (CH1, CH2) if na < nb else (CH2, CH1)
    calls = h.say(1, f"مپ P{max(na, nb)}", chat=small)
    assert not photos(calls) and "پیدا نشد" in h.texts(calls)
    assert photos(h.say(1, f"مپ P{max(na, nb)}", chat=big))
    # a group that never opened its map has no city to inspect yet: asking creates its own, never borrows another's
    before = h.db.q1("SELECT COUNT(*) c FROM worlds")["c"]
    c = photos(h.say(1, "مپ L1", chat=-100888))
    assert len(c) == 1 and h.db.q1("SELECT COUNT(*) c FROM worlds")["c"] == before + 1
    wc = world.get_world(h.db, -100888)
    assert wc["seed"] not in (wa["seed"], wb["seed"]) and wc["name_fa"] in c[0].caption


def test_repeated_use_never_creates_or_changes_objects(h):
    h.say(1, "مپ P3", chat=CH1)
    w = dict(world.get_world(h.db, CH1))
    L = layout_of(h)
    for text in ("مپ P3", "مپ p3", "مپ L2", "مپ D1", "مپ"):
        h.say(2, text, chat=CH1)
    assert h.db.q1("SELECT COUNT(*) c FROM worlds")["c"] == 1 and dict(world.get_world(h.db, CH1)) == w
    L2 = layout_of(h)
    assert L2 == L and len(L2["parcels"]) == len(L["parcels"]) and [p["id"] for p in L2["parcels"]] == [p["id"] for p in L["parcels"]]


# ----------------------------------------------------------------------------------------------------- reading what was typed
@pytest.mark.parametrize("typed,stored", [
    ("P14", "p14"), ("p14", "p14"), ("P-014", "p14"), ("p_14", "p14"), ("#P14", "p14"), ("P۱۴", "p14"), ("P١٤", "p14"), ("<P14>", "p14"),
    ("«L2»", "l2"), ("D3.", "d3"), ("P14؟", "p14"), ("p0", "p0"), ("B3", "b3"), ("p000014", "p14"),
])
def test_ids_are_read_leniently(typed, stored):
    assert WI.normalize(typed) == stored and WI.parse_ref([typed]) == stored


def test_a_letter_and_a_number_are_read_as_one_id():
    assert WI.parse_ref(["P", "14"]) == "p14" and WI.parse_ref(["l", "۲"]) == "l2"


@pytest.mark.parametrize("bad", ["", " ", "p", "14", "12", "map", "of5", "p1x", "p-", "ppp1", "p1234567", "p٫5", "پ14", "P14P15", "p 14 15"])
def test_things_that_are_not_ids_are_not_read_as_ids(bad):
    assert WI.normalize(bad) is None
    assert WI.parse_ref(bad.split()) is None


def test_odd_inputs_never_raise():
    for junk in (None, 5, [], [None], ["‌"], ["p" * 5000], ["p1", "p2", "p3"], [b"p1"], [1], [["p1"]]):
        assert WI.parse_ref(junk) is None
    for junk in (None, 5, b"p1", "", "\n", "💥", "p\u0000"):
        assert WI.normalize(junk) is None
    L = world.build(7)
    for ref in ("", "x", "p", "p0", "p-1", "b1", "r1", "w1", "p999999", "l5", "d99", None, 5):
        assert WI.resolve(L, ref) is None


def test_roads_blocks_and_water_are_not_inspectable(city):
    for ref in ("r1", "b1", "w1"):
        assert city and any(o["id"] == ref for o in city["roads"] + city["blocks"] + city["water"])
        assert WI.resolve(city, ref) is None


# ----------------------------------------------------------------------------------------------------- what the panels say
def test_a_vacant_parcel_panel_states_only_real_facts(city):
    w = {"name_fa": "شهر", "name_en": "Town"}
    p = first(city, "vacant")
    r = WI.inspect("fa", w, city, p["id"])
    f = WI.facts(city, WI.resolve(city, p["id"]))
    assert r.kind == "parcel" and r.id == p["id"]
    assert f"قطعه {p['id'].upper()}" in r.text and "زمین خالی" in r.text and "متصل" in r.text
    assert f"{t('fa', 'map.zone.' + f['district_zone'])} (<code>{f['district'].upper()}</code>)" in r.text
    assert WI._n(p["c"][0], "fa") in r.text and WI._n(p["c"][1], "fa") in r.text
    assert "هنوز فعال نیست" in r.text
    for invented in ("قیمت", "اجاره", "سود", "مجوز", "هزینه", "مالک", "Price", "Rent", "Owner", "permit", "profit"):
        assert invented not in r.text, invented                                                # no prices, rent, permits, owners: none of that exists yet


def test_a_built_parcel_a_park_and_a_public_place_are_each_called_what_they_are(city):
    w = {"name_fa": "شهر", "name_en": "Town"}
    b = WI.inspect("fa", w, city, first(city, "building")["id"]).text
    assert "ساخته‌شده" in b and "زمین خالی" not in b
    park = next(p for p in city["parcels"] if p["use"] == "green" and p["zone"] == "park")
    pk = WI.inspect("fa", w, city, park["id"]).text
    assert "پارک" in pk and "فضای سبز" in pk
    for lm in city["landmarks"]:
        for lang in ("fa", "en"):
            name = t(lang, "map.lm." + lm["kind"])
            lt = WI.inspect(lang, w, city, lm["id"]).text
            assert name in lt and lm["id"].upper() in lt and lm["parcel"].upper() in lt
            pt = WI.inspect(lang, w, city, lm["parcel"]).text                                  # the landmark's own lot says the same thing
            assert name in pt and lm["id"].upper() in pt


def test_a_district_panel_counts_its_own_parcels(city):
    w = {"name_fa": "شهر", "name_en": "Town"}
    blocks = {b["id"]: b for b in city["blocks"]}
    for d in city["districts"]:
        f = WI.facts(city, WI.resolve(city, d["id"]))
        inside = [p for p in city["parcels"] if blocks[p["block"]]["district"] == d["id"]]
        assert f["use"] == {u: sum(p["use"] == u for p in inside) for u in ("vacant", "building", "green", "landmark")}
        assert f["lots"] == f["use"]["vacant"] + f["use"]["building"] + f["use"]["landmark"]
        txt = WI.inspect("en", w, city, d["id"]).text
        assert f"District {d['id'].upper()}" in txt and t("en", "map.zone." + d["kind"]) in txt
    total = sum(WI.facts(city, WI.resolve(city, d["id"]))["lots"] for d in city["districts"])
    assert total == world.stats(city)["parcels"]                                              # every lot is in exactly one district
    inside_lm = [m["id"] for d in city["districts"] for m in WI.facts(city, WI.resolve(city, d["id"]))["places"]]
    assert sorted(inside_lm) == sorted(m["id"] for m in city["landmarks"])                  # and every public place in exactly one


@pytest.mark.parametrize("seed", SEEDS)
def test_road_access_comes_from_the_real_roads(seed):
    L = world.build(seed)
    direct = inner = 0
    for p in L["parcels"]:
        f = WI.facts(L, WI.resolve(L, p["id"]))
        best = min(G.polyline_dist(r["pts"], pt) for r in L["roads"] for pt in WI._edge_samples(p["poly"]))
        assert abs(best - f["road_dist"]) < 1e-6
        assert f["direct"] == (best <= WI.FRONTAGE)
        if p["use"] in ("green", "landmark"):
            assert f["direct"], p["id"]                                                       # parks and public places always touch a road
        direct += f["direct"]
        inner += not f["direct"]
        assert f["road"] in ("main", "street")
    assert direct / (direct + inner) > 0.9                                                    # nearly every lot fronts a road; the rest are honest about it


def test_a_lot_inside_a_block_is_not_called_connected():
    for seed in range(1, 40):
        L = world.build(seed * 31)
        for p in L["parcels"]:
            f = WI.facts(L, WI.resolve(L, p["id"]))
            if not f["direct"]:
                w = {"name_fa": "شهر", "name_en": "Town"}
                assert "بدون اتصال مستقیم" in WI.inspect("fa", w, L, p["id"]).text and "No direct connection" in WI.inspect("en", w, L, p["id"]).text
                assert "متصل —" not in WI.inspect("fa", w, L, p["id"]).text
                return
    pytest.skip("none of these cities has a lot inside a block")


def test_nearby_parcels_are_close_real_and_exclude_the_object_itself(city):
    for p in city["parcels"][::7]:
        f = WI.facts(city, WI.resolve(city, p["id"]))
        near = f["near"]
        assert 0 < len(near) <= WI.NEARBY and p["id"] not in near and len(set(near)) == len(near)
        pid = {q["id"]: q for q in city["parcels"]}
        ds = [G.dist(p["c"], pid[i]["c"]) for i in near]
        assert ds == sorted(ds) and all(pid[i]["use"] != "green" for i in near)
        rest = [G.dist(p["c"], q["c"]) for q in city["parcels"] if q["id"] != p["id"] and q["id"] not in near and q["use"] != "green"]
        assert not rest or min(rest) >= max(ds) - 1e-9


def test_panels_fit_a_photo_caption_in_both_languages_for_every_object():
    w = {"name_fa": "مروارید‌آباد‌گرد", "name_en": "Morvaridabad"}
    for seed in SEEDS:
        L = world.build(seed)
        for key in ("parcels", "landmarks", "districts"):
            for o in L[key]:
                for lang in ("fa", "en"):
                    text = WI.inspect(lang, w, L, o["id"]).text
                    assert 0 < len(text) <= 1024, (seed, o["id"], lang, len(text))
                    assert text.count("<b>") == text.count("</b>") == 1 and text.count("<code>") == text.count("</code>")


def test_every_text_inspection_needs_exists_in_both_languages():
    keys = [k for k in FA if k.startswith("wi.")] + ["map.hint"]
    assert len(keys) > 30
    for k in keys:
        assert k in EN, k
    assert {k for k in EN if k.startswith("wi.")} == {k for k in FA if k.startswith("wi.")}
    for z in world.DISTRICTS:
        assert f"wi.type.lot.{z}" in FA and f"wi.type.lot.{z}" in EN
    for u in world.USES:
        assert f"wi.status.{u}" in FA and f"wi.status.{u}" in EN
    for dens in ("dense", "open"):
        assert f"wi.density.{dens}" in FA and f"wi.density.{dens}" in EN


# ----------------------------------------------------------------------------------------------------- the highlighted picture
def _diff(a, b):
    return ImageChops.difference(a.convert("RGB"), b.convert("RGB"))


def _spec(L, ref):
    return WI.highlight(L, WI.resolve(L, ref))


def test_highlight_is_the_same_map_with_one_place_marked(city):
    plain = png(worldmap.render(city, "en"))
    frame = (worldmap.HEADER - 6, worldmap.HEADER + int(city["h"] * worldmap.SCALE) + 6)
    for ref in ("p5", "l2", first(city, "vacant")["id"], first(city, "building")["id"]):
        marked = png(worldmap.render(city, "en", _spec(city, ref)))
        assert marked.size == plain.size
        diff = _diff(plain, marked)
        box = diff.getbbox()
        assert box is not None, ref
        p = WI.resolve(city, ref).obj
        c = p["c"] if "c" in p else next(q for q in city["parcels"] if q["id"] == p["parcel"])["c"]
        cx, cy = c[0] * worldmap.SCALE, worldmap.HEADER + c[1] * worldmap.SCALE
        assert box[0] <= cx <= box[2] and box[1] <= cy <= box[3], ref                             # the change is where the place is
        assert box[2] - box[0] < 260 and box[3] - box[1] < 260, (ref, box)                         # ...and only there: the rest of the map is untouched
        assert frame[0] <= box[1] and box[3] <= frame[1]                                          # inside the map frame, never over the title or legend


def test_district_highlight_marks_the_district_and_nothing_else(city):
    plain = png(worldmap.render(city, "en"))
    marked = png(worldmap.render(city, "en", _spec(city, "d3")))
    diff = _diff(plain, marked)
    box = diff.getbbox()
    d = WI.resolve(city, "d3").obj
    xs = [p[0] * worldmap.SCALE for p in d["poly"]]
    ys = [worldmap.HEADER + p[1] * worldmap.SCALE for p in d["poly"]]
    assert box[0] >= min(xs) - 12 and box[2] <= max(xs) + 12 and box[1] >= min(ys) - 12 and box[3] <= max(ys) + 12


def test_highlight_stays_dark_green_and_is_clearly_visible(city):
    marked = png(worldmap.render(city, "en", _spec(city, first(city, "vacant")["id"])))
    plain = png(worldmap.render(city, "en"))
    counts = Counter(zip(*[iter(marked.tobytes())] * 3))
    for (r, g, b), n in counts.items():
        if max(r, g, b) >= 20:
            assert g >= r - 3 and g >= b - 3, (r, g, b)                                           # still green-dominant: no red, no blue, no neon
    assert max(max(c) for c in counts) <= 245 and len(counts) < 7000                              # no glow, no gradients
    mint = sum(n for (r, g, b), n in counts.items() if abs(r - worldmap.HILITE[0]) < 14 and abs(g - worldmap.HILITE[1]) < 14 and abs(b - worldmap.HILITE[2]) < 14)
    assert mint > 400                                                                             # the ring and outline are really drawn in the highlight colour
    base = Counter(zip(*[iter(plain.tobytes())] * 3))
    assert sum(n for (r, g, b), n in base.items() if abs(r - worldmap.HILITE[0]) < 14 and abs(g - worldmap.HILITE[1]) < 14 and abs(b - worldmap.HILITE[2]) < 14) < 30


def test_highlight_works_for_every_kind_of_place_in_every_language(city):
    for lang in ("fa", "en"):
        for key in ("parcels", "landmarks", "districts"):
            for o in city[key][::5]:
                data = worldmap.render(city, lang, _spec(city, o["id"]))
                img = Image.open(io.BytesIO(data))
                assert img.format == "PNG" and len(data) < worldmap.MAX_PNG


def test_the_tag_never_leaves_the_frame_even_at_the_edge_of_the_city():
    L = world.build(42)
    frame = (17 + 6, worldmap.HEADER, int(L["w"] * worldmap.SCALE) - 17 - 6, worldmap.HEADER + int(L["h"] * worldmap.SCALE))
    plain = png(worldmap.render(L, "en"))
    edge = sorted(L["parcels"], key=lambda p: (p["c"][0], p["c"][1]))
    for p in (edge[0], edge[-1], min(L["parcels"], key=lambda q: q["c"][1]), max(L["parcels"], key=lambda q: q["c"][1])):
        box = _diff(plain, png(worldmap.render(L, "en", _spec(L, p["id"])))).getbbox()
        assert box[0] >= 17 and box[2] <= int(L["w"] * worldmap.SCALE) - 17 and box[1] >= worldmap.HEADER - 6 and box[3] <= worldmap.HEADER + int(L["h"] * worldmap.SCALE) + 6, p["id"]


def test_rendering_and_highlighting_do_not_mutate_anything(city):
    frozen = copy.deepcopy(city)
    specs = [_spec(city, o["id"]) for key in ("parcels", "landmarks", "districts") for o in city[key][::6]]
    specs_frozen = copy.deepcopy(specs)
    for s in specs:
        worldmap.render(city, "fa", s)
    assert city == frozen and specs == specs_frozen
    for key in ("parcels", "landmarks", "districts"):
        for o in city[key]:
            WI.inspect("en", {"name_fa": "ا", "name_en": "A"}, city, o["id"])
    assert city == frozen and json.dumps(city, sort_keys=True) == json.dumps(frozen, sort_keys=True)
    assert worldmap.render(city, "en", specs[0]) == worldmap.render(city, "en", specs[0])           # deterministic


def test_the_plain_map_is_exactly_what_it_was(city):
    plain = worldmap.render(city, "en")
    assert worldmap.render(city, "en", None) == plain and worldmap.render(city, "en", {}) == plain
    assert png(plain).getpixel((3, 3)) == worldmap.BG
    counts = Counter(zip(*[iter(png(plain).tobytes())] * 3))
    assert max(max(c) for c in counts) <= 180                                                     # the plain map still has nothing bright in it


def test_highlight_pictures_are_cached_separately_and_bounded(city, monkeypatch):
    worldmap.cache_clear()
    calls = []
    real = worldmap.render
    monkeypatch.setattr(worldmap, "render", lambda layout, lang="fa", highlight=None: calls.append(bool(highlight)) or real(layout, lang, highlight))

    async def go():
        a = await worldmap.render_cached(1, city, "en")
        b = await worldmap.render_cached(1, city, "en", _spec(city, "p5"), "p5")
        c = await worldmap.render_cached(1, city, "en", _spec(city, "p5"), "p5")
        d = await worldmap.render_cached(1, city, "en", _spec(city, "p6"), "p6")
        return a, b, c, d
    a, b, c, d = asyncio.run(go())
    assert b == c and a != b and b != d and calls == [False, True, True]
    for i in range(worldmap.HCACHE_MAX + 4):
        asyncio.run(worldmap.render_cached(1, city, "en", _spec(city, "p5"), f"x{i}"))
    assert len(worldmap._hcache) <= worldmap.HCACHE_MAX and len(worldmap._cache) == 1             # looking at places never evicts the plain maps


# ----------------------------------------------------------------------------------------------------- the command
def test_plain_map_still_works_exactly_as_before(h):
    calls = h.say(1, "مپ", chat=CH1)
    ph = photos(calls)
    assert len(ph) == 1
    L = layout_of(h)
    w = world.get_world(h.db, CH1)
    assert ph[0].photo.data == mapkit.shown(h, CH1, 1, "fa")                                      # the saved city's picture (+ its permanent places and the viewer's markers)
    full = mapkit.city_of(h, CH1).layout                                                           # the city as played: its permanent casino parcel is no longer empty land
    head, _, rest = t("fa", "map.caption", name=w["name_fa"], vacant=views.fmt.num(world.stats(full)["vacant"], "fa"),
                      parcels=views.fmt.num(world.stats(full)["parcels"], "fa")).partition("\n")
    assert world.stats(full)["vacant"] == world.stats(L)["vacant"] - 1 and world.stats(full)["parcels"] == world.stats(L)["parcels"]
    assert ph[0].caption.startswith(head) and rest in ph[0].caption                                # the title, then (new) where you are and who is near, then the same text as before
    assert len(ph[0].caption) <= 1024 and "L1" in ph[0].caption and "مپ" in ph[0].caption          # and now says how to look closer
    for m in L["landmarks"]:
        assert f"<code>{m['id'].upper()}</code>" in ph[0].caption
    for text in ("نقشه", "!مپ", "/map", "map"):
        again = photos(h.say(2, text, chat=CH1))
        assert len(again) == 1 and mapkit.same_city(again[0].photo.data, ph[0].photo.data)           # the same city for everyone (own markers aside)


def test_inspecting_a_parcel_returns_its_own_details_and_a_marked_map(h):
    h.say(1, "مپ", chat=CH1)
    L = layout_of(h)
    plain = photos(h.say(1, "مپ", chat=CH1))[0].photo.data
    for use in ("vacant", "building", "green"):
        p = first(L, use)
        calls = h.say(1, f"مپ {p['id'].upper()}", chat=CH1)
        ph = photos(calls)
        assert len(ph) == 1 and ph[0].chat_id == CH1
        assert ph[0].photo.data != plain and png(ph[0].photo.data).size == png(plain).size
        cap = ph[0].caption
        assert f"قطعه {p['id'].upper()}" in cap and t("fa", f"wi.status.{use}") in cap and WI._n(p["c"][0], "fa") in cap
        assert ph[0].photo.data == mapkit.shown(h, CH1, 1, "fa", WI.highlight(L, WI.resolve(L, p["id"])))  # drawn from the saved geometry
        assert len(cap) <= 1024


def test_inspecting_a_public_place_returns_its_details(h):
    h.say(1, "مپ", chat=CH1)
    L = layout_of(h)
    for m in L["landmarks"]:
        ph = photos(h.say(1, f"مپ {m['id'].upper()}", chat=CH1))
        assert len(ph) == 1
        cap = ph[0].caption
        assert t("fa", "map.lm." + m["kind"]) in cap and m["id"].upper() in cap and m["parcel"].upper() in cap
    social.set_group_lang(h.db, CH1, "en")
    cap = photos(h.say(1, "map L2", chat=CH1))[0].caption
    assert t("en", "map.lm." + L["landmarks"][1]["kind"]) in cap and "Public place" in cap and "منطقه" not in cap


def test_a_district_can_be_inspected_too(h):
    h.say(1, "مپ", chat=CH1)
    ph = photos(h.say(1, "مپ D2", chat=CH1))
    assert len(ph) == 1 and "منطقه D2" in ph[0].caption and "قطعه‌ها" in ph[0].caption


@pytest.mark.parametrize("form", ["مپ P7", "مپ p7", "مپ P-007", "مپ #P7", "مپ P۷", "/map P7", "!مپ p7", "map P7", "نقشه P7", "map p 7", "مپ P 7", "Map P7", "مپ@TestBot P7"])
def test_every_way_of_writing_the_id_inspects_the_same_parcel(h, form):
    h.say(1, "مپ", chat=CH1)
    base = photos(h.say(1, "مپ P7", chat=CH1))[0]
    ph = photos(h.say(1, form, chat=CH1))
    assert len(ph) == 1 and ph[0].caption == base.caption and ph[0].photo.data == base.photo.data


@pytest.mark.parametrize("ref", ["P9999", "X5", "B3", "R2", "W1", "L9", "D99", "P0", "Z1"])
def test_unknown_ids_get_a_short_error_and_change_nothing(h, ref):
    h.say(1, "مپ", chat=CH1)
    before = snapshot(h.db)
    calls = h.say(1, f"مپ {ref}", chat=CH1)
    text = h.texts(calls)
    assert not photos(calls) and "پیدا نشد" in text and ref.upper() in text and len(text) < 260
    assert "مپ L1" in text                                                                         # points at a real ID to try
    assert snapshot(h.db) == before


def test_wrong_input_never_raises_and_never_builds_a_city(h):
    for text in ("/map hello", "/map p", "/map 12", "/map p1 p2 p3", "/map " + "p" * 300, "/map 💥", "/map ‌", "!مپ سلام", "/map ../../etc"):
        calls = h.say(1, text, chat=CH1)
        assert not photos(calls), text
        assert "شناسه" in h.texts(calls) and "مپ P14" in h.texts(calls), text
    assert h.db.q1("SELECT COUNT(*) c FROM worlds")["c"] == 0


def test_ordinary_conversation_is_never_intercepted(h):
    for text in ("مپ کجاست؟", "اون مپ قشنگه", "مپ 12", "نقشه گنج", "map me", "map of 5", "مپ p", "مپ P14 P15", "مپ P14 چطوره", "map p1 is nice", "map 2024"):
        assert h.say(1, text, chat=CH1) == [], text
    assert h.db.q1("SELECT COUNT(*) c FROM worlds")["c"] == 0


def test_the_first_inspection_uses_the_normal_world_creation(h):
    assert h.db.q1("SELECT COUNT(*) c FROM worlds")["c"] == 0
    calls = h.say(1, "مپ L1", chat=CH1)
    assert len(photos(calls)) == 1 and h.db.q1("SELECT COUNT(*) c FROM worlds")["c"] == 1
    w = dict(world.get_world(h.db, CH1))
    h.say(2, "مپ P5", chat=CH1)
    assert dict(world.get_world(h.db, CH1)) == w                                                    # never replaced once it exists


def test_private_chats_get_the_explanation_and_no_world(h):
    calls = h.say(1, "مپ P5")
    assert not photos(calls) and "گروه" in h.texts(calls)
    assert h.say(1, "/map hello") != [] and h.db.q1("SELECT COUNT(*) c FROM worlds")["c"] == 0


def test_inspection_changes_nothing_in_the_database(h):
    h.say(1, "شروع")
    h.say(2, "شروع")
    h.say(1, "مپ", chat=CH1)
    h.say(2, "مپ", chat=CH2)
    L = layout_of(h)
    before = snapshot(h.db)
    cash = core.get_user(h.db, 1)["cash"]
    for ref in [o["id"] for k in ("parcels", "landmarks", "districts") for o in L[k]][::3] + ["P9999", "B1"]:
        h.say(1, f"مپ {ref}", chat=CH1)
    h.say(2, "/map nonsense", chat=CH2)
    assert snapshot(h.db) == before                                                                  # not one row anywhere: worlds, players, stocks, history
    assert core.get_user(h.db, 1)["cash"] == cash


def test_the_menu_button_and_other_commands_are_unaffected(h):
    h.say(1, "!منو", chat=CH1)
    calls = h.press(1, "mn:go:map:1", chat=CH1)
    assert len(photos(calls)) == 1 and "L1" in photos(calls)[0].caption
    assert mapkit.same_city(photos(h.say(2, "مپ", chat=CH1))[0].photo.data, photos(calls)[0].photo.data)
    for text in ("سهام بازار", "سهام پورتفوی", "پروفایل", "راهنما", "سهام"):
        out = h.say(1, text, chat=CH1)
        assert out and not photos(out), text
    assert h.db.q1("SELECT COUNT(*) c FROM worlds")["c"] == 1
    for target in ("profile", "daily", "invite"):
        e = [c for c in h.press(1, f"mn:go:{target}:1") if isinstance(c, EditMessageText)]
        assert e and e[-1].text


def test_if_the_picture_cannot_be_drawn_the_panel_is_still_sent(h, monkeypatch):
    h.say(1, "مپ", chat=CH1)
    w = dict(world.get_world(h.db, CH1))
    worldmap.cache_clear()
    real = worldmap.render

    def boom(layout, lang="fa", highlight=None):
        if highlight:
            raise RuntimeError("renderer exploded")
        return real(layout, lang)
    monkeypatch.setattr(worldmap, "render", boom)
    calls = h.say(1, "مپ L1", chat=CH1)
    assert not photos(calls) and "شهرداری" in h.texts(calls) and "L1" in h.texts(calls)
    assert dict(world.get_world(h.db, CH1)) == w
    assert len(photos(h.say(1, "مپ", chat=CH1))) == 1                                              # the plain map is not affected


def test_a_corrupt_world_is_reported_when_inspecting_and_not_replaced(h):
    h.say(1, "مپ", chat=CH1)
    h.db.x("UPDATE worlds SET layout='garbage' WHERE chat_id=?", (CH1,))
    calls = h.say(1, "مپ P5", chat=CH1)
    assert not photos(calls) and "خراب" in h.texts(calls)
    assert h.db.q1("SELECT layout FROM worlds WHERE chat_id=?", (CH1,))["layout"] == "garbage"


def test_inspection_does_not_block_the_event_loop(h):
    h.say(1, "مپ", chat=CH1)
    w = world.get_world(h.db, CH1)
    L = world.load_layout(w)
    ticks = []

    async def go():
        async def ticker():
            for _ in range(300):
                ticks.append(1)
                await asyncio.sleep(0.002)
        task = asyncio.create_task(ticker())
        await asyncio.to_thread(WI.inspect, "fa", w, L, "p5")
        await worldmap.render_cached(w["id"], L, "fa", _spec(L, "p5"), "p5")
        before = len(ticks)
        await task
        return before
    assert asyncio.run(go()) >= 3


# ----------------------------------------------------------------------------------------------------- documentation and plumbing
def test_the_command_reference_shows_how_to_inspect():
    fa, _, en = docs.DOCS["map"][1], None, docs.DOCS["map"][3]
    assert "مپ L1" in fa and "مپ P14" in fa and "map L1" in en and "map P14" in en
    for line in fa.splitlines() + en.splitlines():
        p = textparse.parse(line.strip(), None)
        assert p is not None and p.cmd == "map" and not p.section


def test_map_stays_a_global_command_outside_the_stock_namespace():
    from hypemarket import sections
    assert "map" in sections.GLOBAL_CMDS and "map" not in sections.STOCK_CMDS
    p = textparse.parse("مپ P14", None)
    assert p.cmd == "map" and p.args == ["P14"] and p.section is None and not p.retired
    assert textparse.parse("سهام مپ P14", None) is None or textparse.parse("سهام مپ P14", None).cmd != "map"


def test_a_backup_and_restore_keep_every_id_and_every_answer(tmp_path):
    d = Database(":memory:")
    w, _ = world.ensure_world(d, CH1)
    L = world.load_layout(w)
    for ref in ("p5", "l1", "d2"):
        WI.inspect("fa", w, L, ref)
    d.backup_to(str(tmp_path / "b.db"))
    restored = Database(str(tmp_path / "b.db"))
    w2 = world.get_world(restored, CH1)
    L2 = world.load_layout(w2)
    assert L2 == L and dict(w2) == dict(w)
    for ref in ("p5", "l1", "d2"):
        assert WI.inspect("fa", w2, L2, ref).text == WI.inspect("fa", w, L, ref).text


def test_old_cities_without_any_change_are_inspectable():
    """A city saved by v0.15 (layout version 2) is inspected as it is: no migration, no re-generation, no new keys."""
    d = Database(":memory:")
    L = world.build(2024)
    w, _ = world.save_world(d, CH1, L)
    saved = w["layout"]
    L2 = world.load_layout(w)
    keys = set(L2)
    for key in ("parcels", "landmarks", "districts"):
        for o in L2[key]:
            assert WI.inspect("fa", w, L2, o["id"]) is not None
    assert world.get_world(d, CH1)["layout"] == saved and set(L2) == keys == set(L) and d.q1("PRAGMA user_version")[0] is not None
