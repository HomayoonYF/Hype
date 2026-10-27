"""The job MAPS (v0.18.1, kept in v0.18.2). The Snapp ride is drawn on the city map (blue to the passenger, yellow to the destination, pins, and the player
as a car), the rental car is a Pride with a model year, walking to a workplace is drawn like every walk. v0.18.2 removed the illustrated scenes of a
character doing a job: only maps are pictures now, every other job screen is text. Also the message handling around pictures (a screen of the other
kind replaces the old message) and the guarantee that a picture that cannot be drawn never costs the player their screen."""
import io
import math

import pytest
from PIL import Image
from aiogram.exceptions import TelegramBadRequest
from aiogram.methods import DeleteMessage, EditMessageMedia, SendMessage, SendPhoto

import test_bot as tb
from test_life_flow import (A, B, CH, alerts, arrive, bal, city, data_of, edits, find, h, layout, press, screen, sent, text_of,  # noqa: F401 (fixtures)
                            walk_to_mcd)
from hypemarket import cash, config as C, core, fmt, handlers, jobviz, mapui, presence, work, worldmap, worldnet as N

PNG = b"\x89PNG\r\n\x1a\n"


def photo_of(calls):
    """The bytes of the picture the bot sent or swapped in, or None when the screen went out without one."""
    for c in reversed(calls):
        if isinstance(c, SendPhoto):
            return c.photo.data
        if isinstance(c, EditMessageMedia):
            return c.media.media.data
    return None


def size_of(png) -> tuple:
    return Image.open(io.BytesIO(png)).size


def is_map(png) -> bool:
    """A city map picture (the renderer's output), as opposed to a hand-drawn scene (there are none any more)."""
    return png is not None and png.startswith(PNG) and size_of(png)[0] >= 600


@pytest.fixture()
def caps(monkeypatch):
    """Every caption the bot cuts to fit Telegram's 1024 characters is a screen that lost lines: record the untrimmed lengths."""
    seen = []
    real = handlers.fit_caption

    def spy(text, limit=1024):
        if text is not None:
            seen.append(len(text))
        return real(text, limit)
    monkeypatch.setattr(handlers, "fit_caption", spy)
    return seen


def count_near(img, rgb, tol=46):
    r0, g0, b0 = rgb
    return sum(1 for (r, g, b) in img.getdata() if abs(r - r0) <= tol and abs(g - g0) <= tol and abs(b - b0) <= tol)


def ride(h, uid=A):
    """Take a ride and return its task."""
    h.say(uid, "اسنپ", chat=CH)
    press(h, uid, f"jb:sr:{uid}")
    return work.current(h.db, uid)


# ================================================================================================ the rental car
def test_the_rental_car_is_a_pride_with_a_model_year_between_1380_and_1395(h):
    task = ride(h)
    car = work.data_of(task)["car"]
    assert 1380 <= car["y"] <= 1395 and car["c"] in work.CAR_COLORS and work.car_of(task) == car
    text, _ = screen(press(h, A, f"jb:o:snapp:{A}"))
    assert "پراید " + fmt.loc(str(car["y"]), "fa") in text and "🚗" in text
    assert t_color(car["c"]) in text
    for other in ("پژو", "سمند", "تیبا", "پرشیا", "Peugeot", "Samand", "Tiba", "Toyota", "BMW"):
        assert other not in text


def t_color(key):
    from hypemarket.texts import t
    return t("fa", "snapp.color." + key)


def test_the_car_depends_only_on_the_saved_ride(h):
    a = work.car_for(7, A, 1)
    assert a == work.car_for(7, A, 1)
    seen = {work.car_for(7, u, k)["y"] for u in range(1, 40) for k in range(1, 6)}
    assert min(seen) >= 1380 and max(seen) <= 1395 and len(seen) > 8                                  # the years are spread over the whole range
    assert {work.car_for(7, u, k)["c"] for u in range(1, 40) for k in range(1, 6)} <= set(work.CAR_COLORS)


def test_a_ride_saved_before_this_version_still_has_a_valid_car(h):
    task = ride(h)
    d = work.data_of(task)
    d.pop("car")
    import json
    h.db.x("UPDATE work_tasks SET data=? WHERE id=?", (json.dumps(d), task["id"]))
    old = work.current(h.db, A)
    car = work.car_of(old)
    assert 1380 <= car["y"] <= 1395 and car["c"] in work.CAR_COLORS and work.car_of(old) == car       # worked out from the driver and the task: always the same
    assert "پراید" in screen(press(h, A, f"jb:o:snapp:{A}"))[0]
    d["car"] = {"y": 1200, "c": "pink"}                                                                # a damaged value is replaced by a valid one
    h.db.x("UPDATE work_tasks SET data=? WHERE id=?", (json.dumps(d), task["id"]))
    assert work.car_of(work.current(h.db, A)) == car


def test_the_receipt_remembers_the_car(h):
    task = ride(h)
    car = work.car_of(task)
    press(h, A, f"jb:sd:{task['id']}:{A}")
    arrive(h, A)
    press(h, A, f"jb:sp:{task['id']}:{A}")
    arrive(h, A)
    out = press(h, A, f"jb:o:snapp:{A}")
    assert photo_of(out) is None                                                                       # the receipt is text since v0.18.2
    rc = work.receipt_of(h.db.q1("SELECT * FROM work_tasks WHERE id=?", (task["id"],)))
    assert rc["car"] == car
    assert "پراید " + fmt.loc(str(car["y"]), "fa") in screen(out)[0]


# ================================================================================================ the Snapp map
def stand(h, uid=A):
    return presence.stand(h.db, city(h), uid)


def marks_of(h, uid=A):
    task = work.current(h.db, uid)
    return jobviz.snapp_marks("fa", city(h), task, stand(h, uid), work.car_of(task))


def near(p, q, tol=1.5):
    return abs(p[0] - q[0]) <= tol and abs(p[1] - q[1]) <= tol


def test_the_ride_is_drawn_blue_to_the_passenger_and_yellow_to_the_destination(h):
    task = ride(h)
    cty, d = city(h), work.data_of(task)
    pick, dest = cty.spot(d["pickup"]), cty.spot(d["dest"])
    m = marks_of(h)
    assert [leg["color"] for leg in m["legs"]] == ["blue", "yellow"]
    blue, yellow = m["legs"]
    assert near(blue["pts"][0], stand(h).pos) and near(blue["pts"][-1], pick.door.pt)                 # me -> the passenger
    assert near(yellow["pts"][0], pick.door.pt) and near(yellow["pts"][-1], dest.door.pt)             # the passenger -> the destination
    assert [(s["kind"], s["color"]) for s in m["stops"]] == [("pickup", "blue"), ("dest", "yellow")]
    assert near(m["stops"][0]["pt"], pick.door.pt) and near(m["stops"][1]["pt"], dest.door.pt)
    assert m["me"]["vehicle"]["kind"] == "car" and m["me"]["vehicle"]["color"] == work.car_of(task)["c"]
    assert {s["tag"] for s in m["stops"]} == {mapui.worldinspect.display_id(pick.id), mapui.worldinspect.display_id(dest.id)}


def test_the_drive_to_the_passenger_shows_the_part_already_driven(h):
    task = ride(h)
    press(h, A, f"jb:sd:{task['id']}:{A}")
    tr = presence.walking_trip(h.db, city(h).id, A)
    assert [leg["color"] for leg in marks_of(h)["legs"]] == ["blue", "yellow"]                        # just set off: nothing driven yet
    h.advance((tr["arrive_at"] - tr["started_at"]) // 2)
    m = marks_of(h)
    assert [leg["color"] for leg in m["legs"]] == ["blue_done", "blue", "yellow"]
    done, left, _ = m["legs"]
    assert done["style"] == "solid" and left.get("style", "dash") == "dash"
    assert near(done["pts"][-1], left["pts"][0]) and near(done["pts"][-1], stand(h).pos)               # the car is exactly where the two pieces meet
    assert m["stops"][0]["kind"] == "pickup"


def test_with_the_passenger_aboard_only_the_yellow_way_is_left(h):
    task = ride(h)
    press(h, A, f"jb:sd:{task['id']}:{A}")
    arrive(h, A)
    m = marks_of(h)
    assert [leg["color"] for leg in m["legs"]] == ["yellow"] and m["stops"][0]["kind"] == "pickup"    # waiting at the pickup
    press(h, A, f"jb:sp:{task['id']}:{A}")
    tr = presence.walking_trip(h.db, city(h).id, A)
    h.advance((tr["arrive_at"] - tr["started_at"]) // 3)
    m = marks_of(h)
    assert [leg["color"] for leg in m["legs"]] == ["yellow_done", "yellow"]
    assert [s["kind"] for s in m["stops"]] == ["dest"]                                                 # the passenger is in the car: no pickup pin any more
    assert m["me"]["vehicle"]["kind"] == "car"


def test_a_ride_that_stopped_half_way_still_has_a_picture(h):
    task = ride(h)
    press(h, A, f"jb:sd:{task['id']}:{A}")
    presence.cancel_trip(h.db, city(h).id, A)                                                          # the trip is gone (e.g. an old crash) but the ride is open
    m = marks_of(h)
    assert [leg["color"] for leg in m["legs"]] == ["blue", "yellow"] and near(m["legs"][0]["pts"][0], stand(h).pos)
    out = press(h, A, f"jb:o:snapp:{A}")
    assert is_map(photo_of(out))


def test_the_marks_are_pure_and_never_ask_the_database(h):
    task = ride(h)
    cty, st, car = city(h), stand(h), work.car_of(task)
    a = jobviz.snapp_marks("fa", cty, task, st, car)
    assert a == jobviz.snapp_marks("fa", cty, task, st, car)
    assert jobviz.snapp_marks("fa", cty, dict(task, data='{"pickup":"P99999","dest":"P99998"}'), st, car) is None   # places that are not in the city: no picture


def test_blue_yellow_pins_and_a_car_are_really_painted(h):
    task = ride(h)
    cty = city(h)
    base = worldmap.render(cty.layout, "fa")
    m = marks_of(h)
    img = Image.open(io.BytesIO(worldmap.overlay(base, "fa", m))).convert("RGB")
    plain = Image.open(io.BytesIO(base)).convert("RGB")
    for rgb in (worldmap.BLUE, worldmap.YELLOW):
        assert count_near(img, rgb) > count_near(plain, rgb) + 60, rgb                                 # both colours were added to the map
    body = work.car_of(task)["c"]
    red = dict(m, me=dict(m["me"], vehicle={"kind": "car", "heading": 0.0, "color": "red"}))
    disc = dict(m, me={"pt": m["me"]["pt"], "label": m["me"]["label"]})
    a = Image.open(io.BytesIO(worldmap.overlay(base, "fa", red))).convert("RGB")
    b = Image.open(io.BytesIO(worldmap.overlay(base, "fa", disc))).convert("RGB")
    x, y = worldmap._px(m["me"]["pt"])
    box = (int(x) - 16, int(y) - 16, int(x) + 16, int(y) + 16)
    assert count_near(a.crop(box), worldmap.CAR_COLORS["red"], 30) > 30                                # a red car around the marker
    assert count_near(b.crop(box), worldmap.CAR_COLORS["red"], 30) == 0
    assert list(a.crop(box).getdata()) != list(b.crop(box).getdata()) and body in worldmap.CAR_COLORS


def test_the_car_points_the_way_it_drives(h):
    east = {"route": "[[0,0],[100,0]]", "arrive_at": 110, "started_at": 100, "length": 100.0}
    south = {"route": "[[0,0],[0,100]]", "arrive_at": 110, "started_at": 100, "length": 100.0}
    bend = {"route": "[[0,0],[50,0],[50,50]]", "arrive_at": 110, "started_at": 100, "length": 100.0}
    assert presence.trip_heading(east, 105) == pytest.approx(0.0)
    assert presence.trip_heading(south, 105) == pytest.approx(math.pi / 2)
    assert presence.trip_heading(bend, 102) == pytest.approx(0.0) and presence.trip_heading(bend, 108) == pytest.approx(math.pi / 2)
    assert presence.trip_heading({"route": "garbage", "arrive_at": 1, "started_at": 0, "length": 1.0}, 0) == 0.0


# ================================================================================================ the general map
def test_the_general_map_draws_a_driver_as_a_car(h):
    cty = city(h)
    st = stand(h)
    marks = mapui._marks("fa", cty, st, [], True, db=h.db)
    assert "vehicle" not in marks["me"]                                                                # not driving: the usual round marker
    assert "vehicle" not in mapui._marks("fa", cty, st, [], True)["me"]
    task = ride(h)
    marks = mapui._marks("fa", cty, stand(h), [], True, db=h.db)
    assert marks["me"]["vehicle"] == {"kind": "car", "heading": 0.0, "color": work.car_of(task)["c"]}
    press(h, A, f"jb:sd:{task['id']}:{A}")
    st = stand(h)
    marks = mapui._marks("fa", cty, st, [], True, db=h.db)
    assert marks["me"]["vehicle"]["heading"] == pytest.approx(presence.trip_heading(st.trip, st.now))
    assert "walked" in marks or "route" in marks                                                       # the walk drawing is still there
    press(h, A, f"jb:x:{task['id']}:{A}")
    assert "vehicle" not in mapui._marks("fa", cty, stand(h), [], True, db=h.db)["me"]                 # the ride is over: back to the disc


def test_other_players_who_drive_are_cars_and_walkers_are_rings(h):
    cty = city(h)
    ride(h, B)
    tb_task = work.current(h.db, B)
    press(h, B, f"jb:sd:{tb_task['id']}:{B}")
    st = stand(h, A)
    others = presence.nearby(h.db, cty, A, st.pos, radius=10 ** 7)
    assert [o.uid for o in others] == [B] and others[0].mode == "car" and others[0].walking
    assert others[0].heading == pytest.approx(presence.trip_heading(presence.walking_trip(h.db, cty.id, B), st.now))
    marks = mapui._marks("fa", cty, st, others, True, db=h.db)
    assert marks["others"][0]["vehicle"] == {"kind": "car", "heading": others[0].heading, "color": work.car_of(tb_task)["c"]}
    assert "vehicle" not in mapui._marks("fa", cty, st, others, True)["others"][0]                     # without the database nobody can tell: plain rings
    press(h, B, f"jb:x:{tb_task['id']}:{B}")
    other = presence.nearby(h.db, cty, A, st.pos, radius=10 ** 7)[0]
    assert other.mode == "walk" and "vehicle" not in mapui._marks("fa", cty, st, [other], True, db=h.db)["others"][0]


# ================================================================================================ only maps are pictures
def test_no_job_screen_is_an_illustration_any_more():
    import importlib.util
    assert importlib.util.find_spec("hypemarket.scenes") is None                                       # the module of the scenes is gone
    assert not hasattr(jobviz, "scene_pic") and not hasattr(jobviz, "plate_year")
    from hypemarket.texts import EN, FA
    assert not [k for k in list(FA) + list(EN) if k.startswith("scene.")]


def test_every_snapp_screen_is_text_except_the_map_of_the_ride(h, caps):
    out = h.say(A, "اسنپ", chat=CH)
    assert photo_of(out) is None and "پراید" in text_of(sent(out)[-1])                                 # the Snapp desk: text (terms, balance, one button)
    out = press(h, A, f"jb:sr:{A}")
    task = work.current(h.db, A)
    assert is_map(photo_of(out))                                                                       # the ride on the map
    caption = screen(out)[0]
    assert "🔵" in caption and "🟡" in caption                                                           # the legend of the two colours
    out = press(h, A, f"jb:sd:{task['id']}:{A}")
    assert is_map(photo_of(out)) and "🔵" in screen(out)[0]
    arrive(h, A)
    out = press(h, A, f"jb:o:snapp:{A}")
    assert is_map(photo_of(out)) and f"jb:sp:{task['id']}:{A}" in screen(out)[1]                       # at the pickup the map stays (the car at the person pin); no scene of the passenger
    out = press(h, A, f"jb:sp:{task['id']}:{A}")
    assert is_map(photo_of(out)) and "🟡" in screen(out)[0] and "🔵" not in screen(out)[0]            # only the yellow way is left
    arrive(h, A)
    out = press(h, A, f"jb:o:snapp:{A}")
    assert photo_of(out) is None and "سفر تموم شد" in screen(out)[0]                                   # the receipt is text
    assert max(caps) <= 1024


def test_the_snapp_dashboard_shows_only_what_is_saved(h):
    h.say(A, "اسنپ", chat=CH)
    text, _ = screen(press(h, A, f"jb:sr:{A}"))
    task = work.current(h.db, A)
    d = work.data_of(task)
    assert fmt.usd(task["reward"], "fa") in text and "$۰٫۲۰" in text and fmt.usd(task["reward"] - task["cost"], "fa") in text    # fare, rental, net
    assert "کیلومتر" in fmt.dist(1800, "fa") and fmt.dist(d["d1"], "fa") in text and fmt.dist(d["d2"], "fa") in text           # distances in metres, like the speed
    assert fmt.kmh(C.CAR_SPEED_DEFAULT, "fa") in text and "پراید" in text
    press(h, A, f"jb:sd:{task['id']}:{A}")
    h.advance(2)
    text, _ = screen(press(h, A, f"jb:o:snapp:{A}"))
    st = stand(h)
    assert "▰" in text and "📏" in text and fmt.dist(float(st.trip["length"]) * (1 - st.progress), "fa") in text and fmt.kmh(C.CAR_SPEED_DEFAULT, "fa") in text   # progress, distance left and ETA come from the saved trip


def test_the_pictures_follow_the_language_and_the_digit_style(h, monkeypatch, caps):
    monkeypatch.setenv("DIGITS", "en")
    h.say(A, "jobs", chat=CH)                                                                          # the group is registered by its first message
    h.db.x("UPDATE groups SET lang='en' WHERE id=?", (CH,))
    out = h.say(A, "jobs", chat=CH)
    assert photo_of(out) is None and "Jobs" in text_of(sent(out)[-1]) and "$100.00" in text_of(sent(out)[-1])
    out = press(h, A, f"jb:sr:{A}")
    text = screen(out)[0]
    assert "Pride 13" in text or "Pride 14" in text
    assert not any(ch in text for ch in "۰۱۲۳۴۵۶۷۸۹٬٫") and "to the passenger" in text and "40 km/h" in text
    assert is_map(photo_of(out))
    assert max(caps) <= 1024


def test_the_mcdonalds_screens_are_maps_while_walking_and_text_at_the_counter(h, caps):
    out = h.say(A, "مک دونالد", chat=CH)
    far = photo_of(out)
    assert is_map(far)                                                                                 # far away: the walk drawn on the map, like every walk
    assert fmt.kmh(C.walk_speed(), "fa") in text_of(sent(out)[-1])                                                         # the walking speed is stated
    m = jobviz.walk_marks("fa", city(h), stand(h), city(h).mcd_id)
    assert m["stops"][0]["kind"] == "work" and m["legs"][0]["color"] == "mint" and "walked" not in m   # the road you would take, dashed
    out = press(h, A, f"jb:mg:{A}")
    assert is_map(photo_of(out)) and "▱▱▱▱▱▱▱▱▱▱" in screen(out)[0] and "📏" in screen(out)[0]          # a walk that has just begun: the bar is empty, the distance left is real
    h.advance(3)
    m = jobviz.walk_marks("fa", city(h), stand(h), city(h).mcd_id)
    assert m["walked"] and m["route"] and "legs" not in m and m["stops"][0]["kind"] == "work"          # on the way: done and ahead
    arrive(h, A)
    out = h.say(A, "مک دونالد", chat=CH)
    assert photo_of(out) is None and f"jb:ms:{A}" in data_of(sent(out)[-1])                            # there: no route left to draw, so text and the start button
    out = press(h, A, f"jb:ms:{A}")
    assert photo_of(out) is None and "$۱۲٫۰۰" in screen(out)[0]                                       # the confirmation
    out = press(h, A, f"jb:my:{A}")
    text = screen(out)[0]
    assert photo_of(out) is None and "در حال کار" in text and "وضعیت" in text and "▱▱▱▱▱▱▱▱▱▱" in text.replace("▰", "▱") and "$۱۲٫۰۰" in text
    h.advance(4 * 3600)
    half = screen(press(h, A, f"jb:o:mcd:{A}"))[0]
    assert "▰▰▰▰▰▱▱▱▱▱" in half and "۴ ساعت" in half.replace("4", "۴") or "4 ساعت" in half   # the bar moved: half of the real shift
    h.advance(4 * 3600)
    task = work.current(h.db, A)
    ready = screen(press(h, A, f"jb:o:mcd:{A}"))
    assert "تموم شده" in ready[0] and f"jb:mc:{task['id']}:{A}" in ready[1] and "▰▰▰▰▰▰▰▰▰▰" in ready[0]
    done = press(h, A, f"jb:mc:{task['id']}:{A}")
    assert photo_of(done) is None and bal(h) == 10_000 + C.MCD_WAGE_CENTS
    assert max(caps) <= 1024


def test_a_private_chat_shows_the_shift_dashboard_too(h):
    walk_to_mcd(h)
    press(h, A, f"jb:ms:{A}")
    press(h, A, f"jb:my:{A}")
    out = h.say(A, "مک دونالد")                                                                         # asked in private: no city is needed to see a shift
    assert photo_of(out) is None and "شیفت" in text_of(sent(out)[-1])


def test_the_municipal_screens_are_text_with_the_map_of_the_way(h, caps):
    out = h.say(A, "شهرداری", chat=CH)
    assert photo_of(out) is None
    datas = data_of(sent(out)[-1])
    offer = next(d for d in datas if d.startswith("jb:na:"))
    kind = offer.split(":")[2]
    out = press(h, A, offer)
    assert is_map(photo_of(out))                                                                       # far from the site: the map with the bag pin
    task = work.current(h.db, A)
    press(h, A, f"jb:ng:{task['id']}:{A}")
    st = stand(h)
    m = jobviz.walk_marks("fa", city(h), st, work.data_of(task)["target"])
    assert m["stops"][0]["kind"] == "work" and (m.get("walked") or m.get("legs") is None)
    arrive(h, A)
    out = press(h, A, f"jb:o:muni:{A}")
    assert photo_of(out) is None and f"jb:nd:{task['id']}:{A}" in screen(out)[1]                       # at the site: text, no illustration of workers
    out = press(h, A, f"jb:nd:{task['id']}:{A}")
    assert photo_of(out) is None and "✅" in screen(out)[0] and kind in ("sweep", "waste", "green")
    assert max(caps) <= 1024


def test_the_board_and_the_wallet_do_not_leave_a_wrong_picture_behind(h):
    out = h.say(A, "مشاغل", chat=CH)
    assert photo_of(out) is None and "مشاغل" in text_of(sent(out)[-1])
    h.say(A, "اسنپ", chat=CH)
    press(h, A, f"jb:sr:{A}")                                                                          # the ride's map message...
    out = press(h, A, f"cs:w:{A}")                                                                     # ...the wallet is only text: the picture message is replaced
    assert any(isinstance(c, DeleteMessage) for c in out) and sent(out) and photo_of(out) is None
    assert "کیف" in text_of(sent(out)[-1]) or "پول" in text_of(sent(out)[-1])
    out = press(h, A, f"jb:h:{A}")                                                                     # the board pressed on a picture message: the text board replaces it
    assert any(isinstance(c, DeleteMessage) for c in out) and photo_of(out) is None and "مشاغل" in text_of(sent(out)[-1])


def test_when_the_old_message_cannot_be_deleted_the_screen_still_arrives(h, monkeypatch):
    h.say(A, "اسنپ", chat=CH)
    press(h, A, f"jb:sr:{A}")
    real = h.session.make_request

    async def refuse(bot, method, timeout=None):
        if isinstance(method, DeleteMessage):
            h.session.calls.append(method)
            raise TelegramBadRequest(method=method, message="Bad Request: message can't be deleted for everyone")
        return await real(bot, method, timeout)
    monkeypatch.setattr(h.session, "make_request", refuse)
    out = press(h, A, f"cs:w:{A}")                                                                     # text wanted over a picture message
    assert any("EditMessageCaption" in type(c).__name__ for c in out)                                  # the caption carries it


def test_a_picture_that_cannot_be_drawn_never_costs_the_screen(h, monkeypatch):
    def boom(*a, **k):
        raise RuntimeError("no fonts")
    monkeypatch.setattr(worldmap, "overlay", boom)                                                     # the markers fail: the plain city picture (or nothing) but the text is there
    task = ride(h)
    out = press(h, A, f"jb:o:snapp:{A}")
    assert "پراید" in screen(out)[0] and find(data_of((edits(out) or sent(out))[-1]), "jb:sd:")


def test_the_car_names_and_colours_exist_in_both_languages():
    from hypemarket.texts import EN, FA
    for key in [k for k in FA if k.startswith("snapp.color.") or k in ("snapp.car", "snapp.legend", "snapp.legend.dest")]:
        assert key in EN, key
    for c in work.CAR_COLORS:
        assert "snapp.color." + c in FA and "snapp.color." + c in EN
