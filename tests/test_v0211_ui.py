"""v0.211 business UI through the real handlers: every screen and button of the business hub (properties, brands, branches, till, upgrades, stock, production, ads, shops, staff, reports,
reputation, style) and of the cotton job, with the guarantees that matter: owner-only buttons, stale and repeated presses, hand-made buttons, the server-side position, 64-byte callbacks,
and texts that exist in both languages with matching placeholders."""
import ast
import inspect
import re
import string

import pytest

import bizkit
import charkit
import test_bot as tb
from test_life_flow import CH, alerts, data_of, edits, labels_of, layout, screen, sent, text_of, toasts  # noqa: F401 (fixtures)
from aiogram.methods import AnswerCallbackQuery, EditMessageMedia, SendPhoto
from hypemarket import skills, bizui, biz, bizrouter, brands, cash, config as C, core, estate, inventory, market, presence, rep, shopui, staff, stock, store, style, tailor, textile, upgrades, work, world, worldmap, worldplaces
from hypemarket.texts import EN, FA, t
import test_v0211_shop as SH

A, B, D = SH.A, SH.B, SH.CUST


class NowRef:
    def __getitem__(self, i):
        return core.now()


class Env:
    """The adapter that lets bizkit (built for the logic tests) drive the Harness's own database and clock."""

    def __init__(self, h, layout):
        self.db, self.now, self.h = h.db, NowRef(), h
        self.city = worldplaces.load_city(h.db, world.get_world(h.db, CH))

    def ledger(self, uid):
        return [r for r in self.db.qa("SELECT kind,ref,delta FROM life_ledger WHERE user_id=?", (uid,))]


@pytest.fixture()
def hb(layout):
    worldmap.cache_clear()
    worldplaces.cache_clear()
    hh = tb.Harness()
    world.save_world(hh.db, CH, __import__("copy").deepcopy(layout))
    hh.E = Env(hh, layout)
    for u in (A, B, D):
        hh.say(u, "شروع")
        charkit.complete(hh.db, u, world_id=hh.E.city.id)
    estate.ensure(hh.db, hh.E.city, core.now())
    return hh


def say(h, uid, text, chat=CH):
    return h.say(uid, text, chat=chat)


def press(h, uid, data, chat=CH, photo=True):
    return h.press(uid, data, chat=chat, photo=photo)


def last(calls):
    out = edits(calls) or sent(calls)
    assert out, "nothing was drawn"
    return out[-1]


def view(calls):
    """(text, [callback data]) of the last screen; every button must be a real, well-formed one (<= 64 bytes, a known action, the presser's id at the end)."""
    text, datas = screen(calls)
    for d in datas:
        assert len(d.encode()) <= 64, d
        if d.startswith("bz:"):
            parts = d.split(":")
            assert bizrouter.parse(parts[1], parts[2:-1]) is not None, d
    return text, datas


def buttons(calls):
    c = last(calls)
    mk = getattr(c, "reply_markup", None) or getattr(getattr(c, "media", None), "reply_markup", None)
    return {b.callback_data: b.text for row in mk.inline_keyboard for b in row} if mk else {}


def pick(h, calls, prefix, uid=A, chat=CH):
    """Press the first button of the last screen whose data starts with `prefix`."""
    datas = [d for d in data_of(last(calls)) if d.startswith(prefix)]
    assert datas, (prefix, data_of(last(calls)))
    return press(h, uid, datas[0], chat=chat)


def err_alert(calls):
    a = alerts(calls)
    assert a, "no alert"
    return a[-1]


def money(h, uid, dollars=50_000):
    bizkit.rich(h.E, uid, dollars)


def near_props(h, zone="commercial", mode="both"):
    return bizkit.free_props(h.E, zone, mode)


# ======================================================================== texts and structure (no harness)
UI_FILES = ("bizui", "shopui", "peopleui", "cottonui", "bizrouter")
DYN_DOMAINS = {}


def _static_calls():
    """[(file, key, {kwarg names}, line)] of every `t(lang, "<key>", name=...)` in the business UI sources."""
    out = []
    for f in UI_FILES + ("workui",):
        tree = ast.parse(open(f"hypemarket/{f}.py", encoding="utf-8").read())
        for n in ast.walk(tree):
            if isinstance(n, ast.Call) and isinstance(n.func, ast.Name) and n.func.id == "t" and len(n.args) >= 2 and isinstance(n.args[1], ast.Constant) and isinstance(n.args[1].value, str):
                out.append((f, n.args[1].value, {k.arg for k in n.keywords if k.arg}, n.lineno))
    return out


def _fields(s):
    return {f for _lit, f, _fmt, _conv in string.Formatter().parse(s) if f}


def test_every_text_key_the_business_ui_uses_exists_in_both_languages():
    missing = [(f, k, line) for f, k, _kw, line in _static_calls() if k not in FA or k not in EN]
    assert not missing, missing


def test_every_placeholder_of_a_text_is_passed_by_every_screen_that_uses_it():
    bad = []
    for f, k, kw, line in _static_calls():
        if k in FA and k in EN:
            need = _fields(FA[k]) | _fields(EN[k])
            if need - kw:
                bad.append((f, line, k, sorted(need - kw)))
    assert not bad, bad


def test_both_languages_use_the_same_placeholders_for_every_new_text():
    from hypemarket.texts_biz import EN_BIZ, FA_BIZ
    assert set(FA_BIZ) == set(EN_BIZ), sorted(set(FA_BIZ) ^ set(EN_BIZ))
    bad = [k for k in FA_BIZ if _fields(FA_BIZ[k]) != _fields(EN_BIZ[k])]
    assert not bad, bad


def test_every_dynamic_text_family_is_complete():
    """Keys built at run time (zone, tier, mode, role, permission, state, upgrade, sign, title, problem, limit...) exist for every value the game can produce."""
    from hypemarket import tailor
    keys = [f"bz.zone.{z}" for z in C.PROPERTY_ZONE_MODES] + [f"bz.tier.{row[0]}" for row in C.PROPERTY_TIERS]
    keys += [f"bz.mode.{m}" for m in (*C.BUSINESS_MODES, "none")] + [f"bz.mode.hint.{m}" for m in C.BUSINESS_MODES]
    keys += [f"bz.role.{r}" for r in C.ROLES] + [f"bz.role.{r}.d" for r in C.ROLES] + [f"bz.role.{r}.bot" for r in C.BOT_ROLES]
    keys += [f"bz.perm.{p}" for p in biz.PERMS] + [f"bz.perm.{p}.d" for p in biz.PERMS]
    keys += [f"bz.cstate.{s}" for s in ("offered", "active", "ended", "declined")] + [f"bz.ostate.{s}" for s in ("working", "ready", "done", "cancelled")]
    keys += [f"bz.upg.{k}" for k in upgrades.UPGRADES] + [f"bz.upg.{k}.d" for k in upgrades.UPGRADES]
    keys += [f"bz.upg.fx.{u.effect}" for u in upgrades.UPGRADES.values()]
    keys += [f"bz.sign.{s}" for s in upgrades.SIGN_STYLES.values()] + [f"bz.rep.{tid}" for _lo, tid in C.REP_TITLES]
    keys += [f"bz.sy.tier.{tid}" for _lo, tid in C.STYLE_TIERS] + [f"bz.sy.k.{k}" for k in C.OUTFIT_KINDS]
    keys += [f"bz.limit.{x}" for x in ("material", "skill", "equipment", "property")]
    keys += [f"bz.prob.{x}" for x in ("recipe", "material_kind", "pieces", "mode", "skill", "machine", "equipment", "fabric")]
    keys += [f"bz.rp.p{d}" for d in (0, 1, 7)] + [f"e.upg_{w}" for w in ("mode", "tier", "slots", "maxed", "unknown")]
    keys += [f"bz.ledger.{k}" for k in ("sale", "buyback", "fund", "withdraw", "upgrade", "wage", "ad_reserve", "ad_refund", "close")]
    keys += [f"job.{j}.name" for j in ("cotton", "tailor")] + ["job.desc.cotton"]
    gone = [k for k in keys if k not in FA or k not in EN]
    assert not gone, gone


def test_every_error_code_the_business_modules_raise_has_a_message_in_both_languages():
    src = "".join(open(f"hypemarket/{m}.py", encoding="utf-8").read() for m in ("estate", "brands", "biz", "upgrades", "stock", "staff", "rep", "textile", "market", "tailor", "store", "reports", "style", "bizui", "shopui",
                                                                              "peopleui", "cottonui", "bizrouter"))
    codes = set(re.findall(r'GameError\(\s*"([a-z_0-9]+)"', src)) | {f"upg_{w}" for w in ("mode", "tier", "slots", "maxed", "unknown")}
    gone = [c for c in sorted(codes) if c != "upg_" and (f"e.{c}" not in FA or f"e.{c}" not in EN)]
    assert not gone, gone


def test_error_messages_with_numbers_fill_in_their_values():
    from hypemarket.handlers import err_text
    from hypemarket.core import GameError
    for e, needle in ((GameError("till_low", need=25_000, have=1_000), "$۲۵۰٫۰۰"), (GameError("price_range", max=12_000), "$۱۲۰٫۰۰"), (GameError("owed_wages", need=500), "$۵٫۰۰"),
                      (GameError("cotton_locked", need=100, have=3), "۱۰۰"), (GameError("wh_full", free=4), "۴"), (GameError("ad_left", left=7), "۷"), (GameError("mode_busy", why="order"), "سفارش")):
        for lang in ("fa", "en"):
            text = err_text(lang, e)
            assert "{" not in text, (e.code, text)
        assert needle in err_text("fa", e), (e.code, err_text("fa", e))


def test_every_new_text_is_valid_telegram_html_once_filled_in():
    from hypemarket.texts_biz import EN_BIZ, FA_BIZ
    for table in (FA_BIZ, EN_BIZ):
        for k, text in table.items():
            filled = text.format(**{p: "7" for p in _fields(text)}) if "{" in text else text
            assert tb.assert_valid_telegram_html(filled) is None, (k, filled)
            assert len(filled) < 1000, (k, len(filled))


def test_every_route_has_a_matching_function_signature():
    for act, (shape, fn, fixed) in bizrouter.ACTS.items():
        sig = inspect.signature(fn)
        required = [p for p in list(sig.parameters.values())[1:] if p.default is p.empty]
        for opt in (shape if isinstance(shape, tuple) else (shape,)):
            n = len(opt) + len(fixed)
            assert len(required) <= n <= len(sig.parameters) - 1, (act, opt, sig)


def test_the_router_refuses_malformed_buttons():
    for act, pieces in (("zz", []), ("n", []), ("n", ["x"]), ("pi", ["1", "2"]), ("pi", ["-1"]), ("pi", ["1.5"]), ("oB", ["1", "xyz"]), ("oB", ["1", "ABCDEF12"]), ("pm", ["1", "Retail"]),
                        ("pm", ["1", "a" * 40]), ("hf", ["1", "2", "3", "4", "f", "x"]), ("t", ["1"]), ("pi", ["9" * 20])):
        assert bizrouter.parse(act, pieces) is None, (act, pieces)
    for act, pieces in (("h", []), ("n", ["1"]), ("s", ["3"]), ("s", ["3", "2"]), ("t", []), ("t", ["g"]), ("oB", ["12", "0a1b2c3d"])):
        assert bizrouter.parse(act, pieces) is not None, (act, pieces)


# ======================================================================== hub and properties
def test_the_hub_opens_from_the_word_and_every_button_is_well_formed(hb):
    out = say(hb, A, "کسب‌وکار")
    text, datas = view(out)
    assert "کسب‌وکار" in text and f"bz:n:1:{A}" in datas and f"bz:b:{A}" in datas and f"bz:l:{A}" in datas
    for word in ("بیزنس", "business", "املاک"):
        view(say(hb, A, word))


def test_the_hub_is_in_the_main_menu_and_opens_in_place(hb):
    out = press(hb, A, f"mn:go:business:{A}", photo=False)
    text, datas = view(out)
    assert "کسب‌وکار" in text and f"mn:menu:{A}" in datas


def test_the_nearby_list_marks_what_is_in_reach_and_the_buy_button_needs_the_door(hb):
    E = hb.E
    money(hb, A)
    text, datas = view(press(hb, A, f"bz:n:1:{A}"))
    assert "$" in text and any(d.startswith("bz:pi:") for d in datas)
    prop = near_props(hb)[0]
    out = press(hb, A, f"bz:pi:{prop['id']}:{A}")
    text, datas = view(out)
    assert not any(d.startswith("bz:pb:") for d in datas) and any(d.startswith("bz:pg:") for d in datas)      # far: only the walk
    out = press(hb, A, f"bz:pb:{prop['id']}:{A}")
    assert err_alert(out)                                                                                     # a hand-made buy from afar is refused by the server
    assert estate.get(hb.db, prop["id"])["owner_id"] is None
    bizkit.teleport(E, A, prop["parcel_id"])
    text, datas = view(press(hb, A, f"bz:pi:{prop['id']}:{A}"))
    assert f"bz:pb:{prop['id']}:{A}" in datas


def test_walking_to_a_property_is_the_ordinary_server_side_trip(hb):
    prop = near_props(hb)[0]
    out = press(hb, A, f"bz:pg:{prop['id']}:{A}")
    assert presence.walking_trip(hb.db, hb.E.city.id, A) is not None
    view(out)


def test_buying_asks_first_charges_once_and_a_repeated_press_is_refused(hb):
    E = hb.E
    money(hb, A)
    prop = near_props(hb)[0]
    bizkit.teleport(E, A, prop["parcel_id"])
    before = cash.balance(hb.db, A)
    text, datas = view(press(hb, A, f"bz:pb:{prop['id']}:{A}"))
    assert cash.balance(hb.db, A) == before and f"bz:pd:{prop['id']}:{A}" in datas                             # the question changes nothing
    out = press(hb, A, f"bz:pd:{prop['id']}:{A}")
    assert estate.get(hb.db, prop["id"])["owner_id"] == A and cash.balance(hb.db, A) == before - prop["price"]
    view(out)
    again = press(hb, A, f"bz:pd:{prop['id']}:{A}")
    assert cash.balance(hb.db, A) == before - prop["price"] and alerts(again)                                  # not charged twice
    # somebody else cannot press my confirm button, and cannot buy what is taken
    other = press(hb, B, f"bz:pd:{prop['id']}:{A}")
    assert estate.get(hb.db, prop["id"])["owner_id"] == A and toasts(other) and not edits(other)


def test_somebody_elses_button_is_refused_for_every_action(hb):
    prop = near_props(hb)[0]
    for data in (f"bz:h:{A}", f"bz:n:1:{A}", f"bz:pi:{prop['id']}:{A}", f"bz:b:{A}", f"bz:k:{A}", f"bz:y:{A}", f"bz:t:{A}"):
        out = press(hb, B, data)
        assert toasts(out) and not edits(out), data


@pytest.mark.parametrize("data", ["bz:{u}", "bz:zz:{u}", "bz:pi:{u}", "bz:pi:abc:{u}", "bz:pi:1:2:{u}", "bz:oB:1:zzzz:{u}", "bz:pm:1:evil:{u}", "bz:s:1:2:3:{u}", "bz:hf:1:2:3:4:x:5:{u}",
                                  "bz:pi:99999999:{u}", "bz:sv:1:deadbeef:{u}", "bz:sC:99999:{u}", "bz:ka:99999:{u}", "bz:se:99999:{u}", "bz:ys:evil:{u}", "bz:rp:99999:{u}", "bz:rp:1:5:{u}"])
def test_hand_made_business_buttons_are_harmless(hb, data):
    before = (cash.balance(hb.db, A), hb.db.q1("SELECT COUNT(*) n FROM properties WHERE owner_id IS NOT NULL")["n"])
    out = press(hb, A, data.format(u=A))
    assert alerts(out) or screen(out), data
    assert (cash.balance(hb.db, A), hb.db.q1("SELECT COUNT(*) n FROM properties WHERE owner_id IS NOT NULL")["n"]) == before


def _owned(hb, uid=A, mode="both", zone="commercial"):
    E = hb.E
    money(hb, uid)
    prop = near_props(hb, zone, mode)[0]
    bizkit.teleport(E, uid, prop["parcel_id"])
    estate.buy(hb.db, E.city, bizkit.stand(E, uid), uid, prop["id"], core.now())
    return estate.get(hb.db, prop["id"])


def test_fitting_a_property_asks_charges_once_and_changes_the_type(hb):
    p = _owned(hb)
    text, datas = view(press(hb, A, f"bz:po:{p['id']}:{A}"))
    assert any(d.startswith("bz:pm:") for d in datas)
    before = cash.balance(hb.db, A)
    ask = press(hb, A, f"bz:pm:{p['id']}:both:{A}")
    view(ask)
    assert cash.balance(hb.db, A) == before
    out = press(hb, A, f"bz:pM:{p['id']}:both:{A}")
    assert estate.get(hb.db, p["id"])["mode"] == "both" and cash.balance(hb.db, A) < before
    after = cash.balance(hb.db, A)
    again = press(hb, A, f"bz:pM:{p['id']}:both:{A}")
    assert alerts(again) and cash.balance(hb.db, A) == after
    view(out)


def test_my_properties_list_and_a_stranger_cannot_manage_my_property(hb):
    p = _owned(hb)
    text, datas = view(press(hb, A, f"bz:p:1:{A}"))
    assert f"bz:po:{p['id']}:{A}" in datas
    out = press(hb, B, f"bz:po:{p['id']}:{B}")
    assert alerts(out)
    out = press(hb, B, f"bz:pM:{p['id']}:retail:{B}")
    assert alerts(out) and estate.get(hb.db, p["id"])["mode"] != "retail"


# ======================================================================== brands and branches
def _fitted(hb, uid=A, mode="both"):
    p = _owned(hb, uid, mode)
    estate.set_mode(hb.db, uid, p["id"], mode, core.now())
    return estate.get(hb.db, p["id"])


def test_a_brand_is_registered_through_the_buttons_and_has_one_owner(hb):
    money(hb, A)
    money(hb, B)
    text, datas = view(press(hb, A, f"bz:b:{A}"))
    assert "NIKE" in text.upper()
    tok = next(d for d in datas if d.startswith("bz:br:"))
    ask = press(hb, A, tok)
    view(ask)
    assert brands.get(hb.db, "nike") is None
    before = cash.balance(hb.db, A)
    do = press(hb, A, tok.replace("bz:br:", "bz:bR:", 1))
    assert brands.get(hb.db, "nike")["owner_id"] == A and cash.balance(hb.db, A) < before
    after = cash.balance(hb.db, A)
    assert alerts(press(hb, A, tok.replace("bz:br:", "bz:bR:", 1))) and cash.balance(hb.db, A) == after
    # the same brand for somebody else: refused, and not charged
    bb = cash.balance(hb.db, B)
    steal = press(hb, B, tok.replace(f":{A}", f":{B}").replace("bz:br:", "bz:bR:", 1))
    assert alerts(steal) and cash.balance(hb.db, B) == bb and brands.get(hb.db, "nike")["owner_id"] == A
    view(do)


def _branch_through_buttons(hb, uid=A):
    p = _fitted(hb, uid)
    if brands.get(hb.db, "nike") is None:
        brands.register(hb.db, uid, "nike", core.now())
    ask = press(hb, uid, f"bz:ob:{p['id']}:{uid}")
    text, datas = view(ask)
    do = next(d for d in datas if d.startswith("bz:oB:"))
    out = press(hb, uid, do)
    b = hb.db.q1("SELECT * FROM branches WHERE property_id=? AND state='open'", (p["id"],))
    assert b is not None
    return p, b, out


def test_opening_a_branch_through_the_buttons_shows_the_branch_and_its_pixel_storefront(hb):
    p, b, out = _branch_through_buttons(hb)
    text, datas = view(out)
    assert "NIKE" in text.upper()
    assert any(isinstance(c, (SendPhoto, EditMessageMedia)) for c in out), [type(c).__name__ for c in out]
    assert f"bz:bf:{b['id']}:{A}" in datas or any(d.startswith("bz:bf:") for d in datas)
    again = press(hb, A, f"bz:oB:{p['id']}:{bizui._tok('nike')}:{A}")
    assert alerts(again) and hb.db.q1("SELECT COUNT(*) n FROM branches WHERE property_id=? AND state='open'", (p["id"],))["n"] == 1


def test_the_branch_list_and_screen_belong_to_the_owner_only(hb):
    p, b, _ = _branch_through_buttons(hb)
    text, datas = view(press(hb, A, f"bz:l:{A}"))
    assert f"bz:bo:{b['id']}:{A}" in datas
    for data in (f"bz:bo:{b['id']}:{B}", f"bz:bf:{b['id']}:{B}", f"bz:u:{b['id']}:{B}", f"bz:s:{b['id']}:{B}", f"bz:pr:{b['id']}:{B}", f"bz:sf:{b['id']}:{B}", f"bz:rp:{b['id']}:{B}", f"bz:cl:{b['id']}:{B}"):
        out = press(hb, B, data)
        assert alerts(out), data
    out = press(hb, B, f"bz:cL:{b['id']}:{B}")
    assert alerts(out) and biz.get(hb.db, b["id"])["state"] == "open"


def test_the_till_is_funded_and_withdrawn_through_the_buttons(hb):
    p, b, _ = _branch_through_buttons(hb)
    text, datas = view(press(hb, A, f"bz:bf:{b['id']}:{A}"))
    fund = next(d for d in datas if d.startswith("bz:fm:"))
    amount = int(fund.split(":")[3])
    before = cash.balance(hb.db, A)
    press(hb, A, fund)
    assert biz.get(hb.db, b["id"])["till"] == amount and cash.balance(hb.db, A) == before - amount
    text, datas = view(press(hb, A, f"bz:bf:{b['id']}:{A}"))
    wd = next(d for d in datas if d.startswith("bz:wd:"))
    press(hb, A, wd)
    assert biz.get(hb.db, b["id"])["till"] < amount
    assert alerts(press(hb, A, f"bz:wd:{b['id']}:{10 ** 9}:{A}"))                                              # more than there is


def test_an_upgrade_is_asked_installed_once_and_paid_from_the_till(hb):
    p, b, _ = _branch_through_buttons(hb)
    biz.fund(hb.db, A, b["id"], 3_000_000, "t-upg", core.now())
    text, datas = view(press(hb, A, f"bz:u:{b['id']}:{A}"))
    assert any(d.startswith("bz:ui:") for d in datas)
    kind = "racks"
    text, datas = view(press(hb, A, f"bz:ui:{b['id']}:{kind}:{A}"))
    till = biz.get(hb.db, b["id"])["till"]
    assert f"bz:uI:{b['id']}:{kind}:1:{A}" in datas
    press(hb, A, f"bz:uI:{b['id']}:{kind}:1:{A}")
    assert upgrades.level_of(hb.db, b["id"], kind) == 1 and biz.get(hb.db, b["id"])["till"] < till
    paid = biz.get(hb.db, b["id"])["till"]
    assert alerts(press(hb, A, f"bz:uI:{b['id']}:{kind}:1:{A}")) and upgrades.level_of(hb.db, b["id"], kind) == 1 and biz.get(hb.db, b["id"])["till"] == paid      # a double tap installs once
    assert alerts(press(hb, A, f"bz:uI:{b['id']}:nonsense:1:{A}"))
    assert alerts(press(hb, A, f"bz:uI:{b['id']}:{kind}:9:{A}")) and upgrades.level_of(hb.db, b["id"], kind) == 1
    assert alerts(press(hb, B, f"bz:uI:{b['id']}:{kind}:2:{B}")) and upgrades.level_of(hb.db, b["id"], kind) == 1


def test_closing_a_branch_asks_then_returns_the_till_and_ends_it(hb):
    p, b, _ = _branch_through_buttons(hb)
    biz.fund(hb.db, A, b["id"], 500_00, "t-cl", core.now())
    before = cash.balance(hb.db, A)
    view(press(hb, A, f"bz:cl:{b['id']}:{A}"))
    assert biz.get(hb.db, b["id"])["state"] == "open"
    out = press(hb, A, f"bz:cL:{b['id']}:{A}")
    assert biz.get(hb.db, b["id"])["state"] == "closed" and cash.balance(hb.db, A) == before + 500_00
    assert alerts(press(hb, A, f"bz:cL:{b['id']}:{A}"))
    view(out)


# ======================================================================== stock and display
def _tok(hb, bid, i=0):
    return shopui.vtok(*shopui.variants(hb.db, bid)[i])


def _deposit_all(hb, bid, uid=A):
    """Put every piece the owner carries into the branch's warehouse through the real deposit buttons."""
    text, datas = view(press(hb, uid, f"bz:sd:{bid}:1:{uid}"))
    for d in [x for x in datas if x.startswith(f"bz:sdi:{bid}:")]:
        view(press(hb, uid, d))
        press(hb, uid, d.replace("bz:sdi:", "bz:sdn:").rsplit(":", 1)[0] + f":0:{uid}")


def test_goods_are_deposited_priced_displayed_and_taken_back_through_the_buttons(hb):
    E = hb.E
    b = SH.shop(E)
    bid = b["id"]
    SH.piece(E, A, "tank_top", 10, SH.NIKE, tag="ui1")
    text, datas = view(press(hb, A, f"bz:s:{bid}:{A}"))
    assert f"bz:sd:{bid}:1:{A}" in datas
    _deposit_all(hb, bid)
    assert stock.qty(E.db, bid, stock.WH, "tank_top", SH.NIKE) == 10 and stock.audit(E.db, bid) == []
    tok = _tok(hb, bid)
    text, datas = view(press(hb, A, f"bz:sv:{bid}:{tok}:{A}"))
    prices = [d for d in datas if d.startswith(f"bz:sp:{bid}:{tok}:") and int(d.split(":")[4]) > 0]
    assert prices
    press(hb, A, prices[0])
    cents = int(prices[0].split(":")[4])
    assert stock.rule(E.db, bid, "tank_top", stock.key(SH.NIKE))["price"] == cents
    # a price nobody offered is refused and changes nothing
    assert alerts(press(hb, A, f"bz:sp:{bid}:{tok}:7:{A}")) and stock.rule(E.db, bid, "tank_top", stock.key(SH.NIKE))["price"] == cents
    # warehouse -> display: as many as fit
    press(hb, A, f"bz:sm:{bid}:{tok}:wh:0:{A}")
    wh, dp = stock.qty(E.db, bid, stock.WH, "tank_top", SH.NIKE), stock.qty(E.db, bid, stock.DISP, "tank_top", SH.NIKE)
    assert dp > 0 and wh + dp == 10
    press(hb, A, f"bz:sm:{bid}:{tok}:disp:1:{A}")
    assert stock.qty(E.db, bid, stock.DISP, "tank_top", SH.NIKE) == dp - 1
    # the instructions
    press(hb, A, f"bz:st:{bid}:{tok}:5:{A}")
    press(hb, A, f"bz:sk:{bid}:{tok}:10:{A}")
    ru = stock.rule(E.db, bid, "tank_top", stock.key(SH.NIKE))
    assert (ru["target"], ru["keep"]) == (5, 10)
    assert alerts(press(hb, A, f"bz:st:{bid}:{tok}:7:{A}")) and stock.rule(E.db, bid, "tank_top", stock.key(SH.NIKE))["target"] == 5
    was = ru["refill"]
    press(hb, A, f"bz:sr:{bid}:{tok}:{A}")
    assert stock.rule(E.db, bid, "tank_top", stock.key(SH.NIKE))["refill"] != was
    # the owner takes some out: it goes back to the bag, the log still adds up
    press(hb, A, f"bz:sm:{bid}:{tok}:disp:0:{A}")
    before = stock.total(E.db, bid, stock.WH) + stock.total(E.db, bid, stock.DISP)
    assert stock.qty(E.db, bid, stock.WH, "tank_top", SH.NIKE) >= 3
    press(hb, A, f"bz:sw:{bid}:{tok}:wh:2:{A}")
    assert stock.total(E.db, bid, stock.WH) + stock.total(E.db, bid, stock.DISP) == before - 2 and stock.audit(E.db, bid) == []


def test_the_buyback_switch_is_the_owners_and_the_game_never_buys_unless_it_is_on(hb):
    E = hb.E
    b = SH.shop(E)
    bid = b["id"]
    SH.piece(E, A, "tank_top", 4, SH.NIKE, tag="bb")
    _deposit_all(hb, bid)
    tok = _tok(hb, bid)
    assert not (stock.rule(E.db, bid, "tank_top", stock.key(SH.NIKE)) or {"buyback": 0})["buyback"]
    text, datas = view(press(hb, A, f"bz:sv:{bid}:{tok}:{A}"))
    assert f"bz:sbb:{bid}:{tok}:{A}" in datas
    press(hb, A, f"bz:sbb:{bid}:{tok}:{A}")
    assert stock.rule(E.db, bid, "tank_top", stock.key(SH.NIKE))["buyback"]
    press(hb, A, f"bz:sbb:{bid}:{tok}:{A}")
    assert not stock.rule(E.db, bid, "tank_top", stock.key(SH.NIKE))["buyback"]


def test_a_stranger_cannot_touch_somebody_elses_stock_even_with_a_hand_made_button(hb):
    E = hb.E
    b = SH.shop(E)
    bid = b["id"]
    SH.piece(E, A, "tank_top", 4, SH.NIKE, tag="str")
    _deposit_all(hb, bid)
    tok = _tok(hb, bid)
    before = (stock.qty(E.db, bid, stock.WH, "tank_top", SH.NIKE), stock.qty(E.db, bid, stock.DISP, "tank_top", SH.NIKE))
    for data in (f"bz:s:{bid}:{B}", f"bz:sv:{bid}:{tok}:{B}", f"bz:sm:{bid}:{tok}:wh:0:{B}", f"bz:sw:{bid}:{tok}:wh:0:{B}", f"bz:sp:{bid}:{tok}:0:{B}", f"bz:st:{bid}:{tok}:5:{B}", f"bz:sbb:{bid}:{tok}:{B}",
                 f"bz:sd:{bid}:1:{B}", f"bz:sR:{bid}:{B}", f"bz:pr:{bid}:{B}", f"bz:am:{bid}:{B}", f"bz:ap:{bid}:{B}", f"bz:ac1:{bid}:{B}", f"bz:sf:{bid}:{B}", f"bz:hp:{bid}:{B}", f"bz:hb:{bid}:{B}"):
        assert alerts(press(hb, B, data)), data
    assert before == (stock.qty(E.db, bid, stock.WH, "tank_top", SH.NIKE), stock.qty(E.db, bid, stock.DISP, "tank_top", SH.NIKE))
    assert alerts(press(hb, A, f"bz:sv:{bid}:deadbeef:{A}"))                                                # a token that names nothing


def test_goods_move_between_two_branches_of_the_owner_through_the_buttons(hb):
    E = hb.E
    b1 = SH.shop(E)
    b2 = SH.shop(E)
    SH.piece(E, A, "tank_top", 6, SH.NIKE, tag="tx")
    _deposit_all(hb, b1["id"])
    tok = _tok(hb, b1["id"])
    text, datas = view(press(hb, A, f"bz:sv:{b1['id']}:{tok}:{A}"))
    assert f"bz:sx:{b1['id']}:{tok}:{A}" in datas
    text, datas = view(press(hb, A, f"bz:sx:{b1['id']}:{tok}:{A}"))
    go = next(d for d in datas if d.startswith(f"bz:sX:{b1['id']}:{tok}:{b2['id']}:"))
    press(hb, A, go.rsplit(":", 2)[0] + f":0:{A}")
    assert stock.qty(E.db, b2["id"], stock.WH, "tank_top", SH.NIKE) == 6 and stock.qty(E.db, b1["id"], stock.WH, "tank_top", SH.NIKE) == 0
    assert stock.audit(E.db, b1["id"]) == [] and stock.audit(E.db, b2["id"]) == []


# ======================================================================== production
def test_a_whole_production_order_through_the_buttons(hb):
    E = hb.E
    w = SH.workshop(E)
    bid = w["id"]
    SH.stock_fabric(E, w, 3, 12)
    SH.at_prop(E, A, w)
    text, datas = view(press(hb, A, f"bz:pr:{bid}:{A}"))
    assert f"bz:pp:{bid}:tank_top:{A}" in datas
    text, datas = view(press(hb, A, f"bz:pp:{bid}:tank_top:{A}"))
    assert f"bz:pq:{bid}:tank_top:3:{A}" in datas
    text, datas = view(press(hb, A, f"bz:pq:{bid}:tank_top:3:{A}"))
    assert f"bz:pn:{bid}:tank_top:3:4:{A}" in [d for d in datas] or any(d.startswith(f"bz:pn:{bid}:tank_top:3:") for d in datas)
    text, datas = view(press(hb, A, f"bz:pn:{bid}:tank_top:3:4:{A}"))
    start = f"bz:pS:{bid}:tank_top:3:4:0:{A}"
    assert start in datas
    left = stock.qty(E.db, bid, stock.WH, "fabric", {"q": 3, "src": "cotton"})
    out = press(hb, A, start)
    o = tailor.working(E.db, bid)
    assert o is not None and o["pieces"] == 4 and o["quality"] == 1 and stock.qty(E.db, bid, stock.WH, "fabric", {"q": 3, "src": "cotton"}) < left
    view(out)
    taken = stock.qty(E.db, bid, stock.WH, "fabric", {"q": 3, "src": "cotton"})
    assert alerts(press(hb, A, start)) and stock.qty(E.db, bid, stock.WH, "fabric", {"q": 3, "src": "cotton"}) == taken     # one order at a time; nothing more is spent
    assert alerts(press(hb, A, f"bz:pc:{bid}:{o['id']}:{A}"))                                                               # not ready yet
    text, datas = view(press(hb, A, f"bz:pr:{bid}:{A}"))
    assert not any(d.startswith("bz:pc:") for d in datas)
    hb.advance(o["due_at"] - core.now() + 1)
    text, datas = view(press(hb, A, f"bz:pr:{bid}:{A}"))
    assert f"bz:pc:{bid}:{o['id']}:{A}" in datas
    out = press(hb, A, f"bz:pc:{bid}:{o['id']}:{A}")
    view(out)
    assert stock.qty(E.db, bid, stock.WH, "tank_top", {"q": 1, "b": "nike", "ph": o["id"]}) == 4
    assert alerts(press(hb, A, f"bz:pc:{bid}:{o['id']}:{A}")) and stock.qty(E.db, bid, stock.WH, "tank_top", {"q": 1, "b": "nike", "ph": o["id"]}) == 4
    assert stock.audit(E.db, bid) == [] and tailor.working(E.db, bid) is None


def test_a_far_worker_cannot_start_and_a_cancelled_order_forfeits_the_material(hb):
    E = hb.E
    w = SH.workshop(E)
    bid = w["id"]
    SH.stock_fabric(E, w, 3, 12)
    start = f"bz:pS:{bid}:tank_top:3:2:0:{A}"
    K_far = bizkit.teleport(E, A, E.city.mcd_id)
    assert alerts(press(hb, A, start)) and tailor.working(E.db, bid) is None
    SH.at_prop(E, A, w)
    press(hb, A, start)
    o = tailor.working(E.db, bid)
    assert o is not None
    text, datas = view(press(hb, A, f"bz:pXa:{bid}:{o['id']}:{A}"))
    assert tailor.working(E.db, bid) is not None and f"bz:pX:{bid}:{o['id']}:{A}" in datas              # asking changes nothing
    press(hb, A, f"bz:pX:{bid}:{o['id']}:{A}")
    assert tailor.working(E.db, bid) is None and tailor.get(E.db, o["id"])["state"] == "cancelled"
    assert alerts(press(hb, A, f"bz:pX:{bid}:{o['id']}:{A}"))


def test_the_plan_screen_names_what_is_missing_and_offers_no_start(hb):
    E = hb.E
    w = SH.workshop(E, machines=0, cutting=False)
    bid = w["id"]
    SH.at_prop(E, A, w)
    text, datas = view(press(hb, A, f"bz:pn:{bid}:tank_top:3:4:{A}"))
    assert not any(d.startswith("bz:pS:") for d in datas)
    for bad in (f"bz:pn:{bid}:nothing:3:4:{A}", f"bz:pp:{bid}:nothing:{A}", f"bz:pq:{bid}:tank_top:9:{A}"):
        assert alerts(press(hb, A, bad)), bad


def test_a_retail_only_branch_has_no_workshop_screen(hb):
    b = SH.shop(hb.E)
    assert alerts(press(hb, A, f"bz:pr:{b['id']}:{A}"))


# ======================================================================== material ads
def _make_ad(hb, bid, uid=A):
    text, datas = view(press(hb, uid, f"bz:am:{bid}:{uid}"))
    assert f"bz:ac1:{bid}:{uid}" in datas
    text, datas = view(press(hb, uid, f"bz:ac1:{bid}:{uid}"))
    d = next(x for x in datas if x.startswith(f"bz:ac2:{bid}:1:"))
    text, datas = view(press(hb, uid, d))
    d = next(x for x in datas if x.startswith(f"bz:ac3:{bid}:1:"))
    unit = int(d.split(":")[4])
    text, datas = view(press(hb, uid, d))
    d = next(x for x in datas if x.startswith(f"bz:ac4:{bid}:1:{unit}:"))
    qty = int(d.split(":")[5])
    text, datas = view(press(hb, uid, d))
    d = next(x for x in datas if x.startswith(f"bz:acD:{bid}:1:{unit}:{qty}:"))
    out = press(hb, uid, d)
    return out, unit, qty, d


def test_the_ad_wizard_reserves_the_budget_a_seller_fills_it_and_a_repeat_is_refused(hb):
    E = hb.E
    b = SH.workshop(E, fund=2000, machines=0, cutting=False)
    bid = b["id"]
    till0 = biz.get(E.db, bid)["till"]
    out, unit, qty, create = _make_ad(hb, bid)
    ad = market.of_branch(E.db, bid, open_only=True)[0]
    assert ad["qty_total"] == qty and ad["unit_cents"] == unit and biz.get(E.db, bid)["till"] == till0 - unit * qty
    view(out)
    assert alerts(press(hb, A, create)) and len(market.of_branch(E.db, bid, open_only=True)) == 1             # a double tap on the same button makes ONE ad and charges once
    assert biz.get(E.db, bid)["till"] == till0 - unit * qty
    # the board shows it to somebody else, who sells fabric into it
    SH.fabric(E, B, 1, 30, tag="ad")
    text, datas = view(press(hb, B, f"bz:ab:1:{B}"))
    assert any(d.startswith("bz:af:") for d in datas)
    text, datas = view(press(hb, B, next(d for d in datas if d.startswith("bz:af:"))))
    sell = next(d for d in datas if d.startswith("bz:afn:") and d.split(":")[3] == "1" and d.split(":")[4] == "1")
    cash0 = cash.balance(E.db, B)
    adid = int(sell.split(":")[2])
    left0 = market.get(E.db, adid)["qty_left"]
    press(hb, B, sell)
    n = int(sell.split(":")[4])
    assert n == 1 and cash.balance(E.db, B) == cash0 + n * market.get(E.db, adid)["unit_cents"] and market.get(E.db, adid)["qty_left"] == left0 - n
    assert stock.qty(E.db, bid, stock.WH, "fabric", {"q": 1, "src": "cotton"}) == n and stock.audit(E.db, bid) == []
    # the owner cannot sell into the branch's own ad; a hand-made amount beyond the want is refused
    SH.fabric(E, A, 1, 5, tag="own")
    assert alerts(press(hb, A, f"bz:afn:{adid}:1:1:{A}"))
    assert alerts(press(hb, B, f"bz:afn:{adid}:1:{10 ** 6}:{B}"))
    assert alerts(press(hb, B, f"bz:afn:{adid}:5:1:{B}"))                                                    # a quality he does not have
    assert market.get(E.db, adid)["qty_left"] == left0 - n                                                  # none of those moved anything

    # sell the rest in one press: the ad is then fulfilled, and a repeat of the button finds it gone
    cash1, left1 = cash.balance(E.db, B), market.get(E.db, adid)["qty_left"]
    press(hb, B, f"bz:afn:{adid}:1:0:{B}")
    assert market.get(E.db, adid)["qty_left"] == 0 and cash.balance(E.db, B) == cash1 + left1 * market.get(E.db, adid)["unit_cents"] and market.get(E.db, adid)["state"] != "open"
    assert alerts(press(hb, B, f"bz:afn:{adid}:1:0:{B}")) and alerts(press(hb, B, f"bz:af:{adid}:{B}"))
    assert stock.audit(E.db, bid) == [] and biz.audit_till(E.db, bid) == []

def test_cancelling_an_ad_returns_the_unused_budget_once(hb):
    E = hb.E
    b = SH.workshop(E, fund=2000, machines=0, cutting=False)
    bid = b["id"]
    till0 = biz.get(E.db, bid)["till"]
    _make_ad(hb, bid)
    ad = market.of_branch(E.db, bid, open_only=True)[0]
    assert biz.get(E.db, bid)["till"] < till0
    press(hb, A, f"bz:ax:{bid}:{ad['id']}:{A}")
    assert biz.get(E.db, bid)["till"] == till0 and market.get(E.db, ad["id"])["state"] != "open"
    assert alerts(press(hb, A, f"bz:ax:{bid}:{ad['id']}:{A}")) and biz.get(E.db, bid)["till"] == till0
    assert alerts(press(hb, B, f"bz:ax:{bid}:{ad['id']}:{B}"))
    assert alerts(press(hb, A, f"bz:af:{ad['id']}:{A}"))                                                     # a closed ad is gone


def test_an_ad_that_the_till_cannot_pay_is_refused_and_a_shop_has_no_ads(hb):
    E = hb.E
    b = SH.workshop(E, fund=1, machines=0, cutting=False)
    bid = b["id"]
    biz.withdraw(E.db, A, bid, biz.get(E.db, bid)["till"], "emptied", core.now())
    out = press(hb, A, f"bz:acD:{bid}:5:15000:250:{C.AD_TTL_CHOICES[0]}:0:{A}")
    assert alerts(out) and not market.of_branch(E.db, bid, open_only=True)
    assert alerts(press(hb, A, f"bz:acD:{bid}:1:40:10:12345:0:{A}"))                                         # a lifetime that was never offered
    shop = SH.shop(E)
    for data in (f"bz:am:{shop['id']}:{A}", f"bz:ac1:{shop['id']}:{A}", f"bz:ac2:{shop['id']}:1:{A}", f"bz:acD:{shop['id']}:1:40:10:{C.AD_TTL_CHOICES[0]}:0:{A}"):
        assert alerts(press(hb, A, data)), data
    text, datas = view(press(hb, A, f"bz:bo:{shop['id']}:{A}"))
    assert not any(d.startswith("bz:am:") for d in datas)


# ======================================================================== the shop floor, accountants and approvals
def _stocked_shop(hb, price=300, n=6, fund=500):
    E = hb.E
    b = SH.shop(E, fund=fund)
    SH.stocked(E, b, n=n, price=price, target=n)
    return b


def _at_door(hb, b, uid):
    SH.at_prop(hb.E, uid, b)


def test_a_customer_sees_the_shop_far_away_but_can_buy_only_at_the_door(hb):
    E = hb.E
    b = _stocked_shop(hb)
    bid = b["id"]
    text, datas = view(press(hb, B, f"bz:sh:{B}"))
    assert f"bz:sb:{bid}:{B}" in datas
    text, datas = view(press(hb, B, f"bz:sb:{bid}:{B}"))
    assert not any(d.startswith("bz:sq:") for d in datas) and any(d.startswith("bz:pg:") for d in datas)
    tok = shopui.vtok(*shopui.variants(E.db, bid)[0])
    assert alerts(press(hb, B, f"bz:sq:{bid}:{tok}:{B}")) and alerts(press(hb, B, f"bz:sQ:{bid}:{tok}:1:{B}"))
    _at_door(hb, b, B)
    text, datas = view(press(hb, B, f"bz:sb:{bid}:{B}"))
    assert f"bz:sq:{bid}:{tok}:{B}" in datas
    # the owner is never a customer of their own shop
    _at_door(hb, b, A)
    text, datas = view(press(hb, A, f"bz:sb:{bid}:{A}"))
    assert not any(d.startswith("bz:sq:") for d in datas)
    assert alerts(press(hb, A, f"bz:sQ:{bid}:{tok}:1:{A}"))


def test_without_an_accountant_a_purchase_waits_for_the_owner_and_nothing_is_taken_before(hb):
    E = hb.E
    b = _stocked_shop(hb)
    bid = b["id"]
    tok = shopui.vtok(*shopui.variants(E.db, bid)[0])
    bizkit.rich(E, B, 1000)
    _at_door(hb, b, B)
    text, datas = view(press(hb, B, f"bz:sq:{bid}:{tok}:{B}"))
    q = next(d for d in datas if d.startswith(f"bz:sQ:{bid}:{tok}:1:"))
    text, datas = view(press(hb, B, q))
    confirm = next(d for d in datas if d.startswith("bz:sC:"))
    cash0, till0, disp0 = cash.balance(E.db, B), biz.get(E.db, bid)["till"], stock.qty(E.db, bid, stock.DISP, "tank_top", SH.NIKE)
    text, datas = view(press(hb, B, confirm))
    oid = int(confirm.split(":")[2])
    assert store.get(E.db, oid)["state"] == "pending" and cash.balance(E.db, B) == cash0 and stock.qty(E.db, bid, stock.DISP, "tank_top", SH.NIKE) == disp0
    again = press(hb, B, confirm)                                                                            # a repeated press shows it is still waiting: no second order, no charge
    assert store.get(E.db, oid)["state"] == "pending" and cash.balance(E.db, B) == cash0
    # the owner sees it and approves it
    text, datas = view(press(hb, A, f"bz:ap:{bid}:{A}"))
    assert f"bz:aa:{oid}:{A}" in datas and f"bz:an:{oid}:{A}" in datas
    assert alerts(press(hb, B, f"bz:aa:{oid}:{B}"))                                                          # the buyer cannot approve their own order
    assert store.get(E.db, oid)["state"] == "pending"
    press(hb, A, f"bz:aa:{oid}:{A}")
    o = store.get(E.db, oid)
    assert o["state"] == "done" and cash.balance(E.db, B) == cash0 - o["total_cents"] and biz.get(E.db, bid)["till"] == till0 + o["total_cents"]
    assert stock.qty(E.db, bid, stock.DISP, "tank_top", SH.NIKE) == disp0 - 1 and biz.audit_till(E.db, bid) == []
    assert alerts(press(hb, A, f"bz:aa:{oid}:{A}")) and cash.balance(E.db, B) == cash0 - o["total_cents"]   # approving twice buys once
    assert alerts(press(hb, A, f"bz:an:{oid}:{A}")) and store.get(E.db, oid)["state"] == "done"


def test_a_declined_order_costs_the_buyer_nothing(hb):
    E = hb.E
    b = _stocked_shop(hb)
    bid = b["id"]
    tok = shopui.vtok(*shopui.variants(E.db, bid)[0])
    bizkit.rich(E, B, 1000)
    _at_door(hb, b, B)
    press(hb, B, f"bz:sq:{bid}:{tok}:{B}")
    text, datas = view(press(hb, B, f"bz:sQ:{bid}:{tok}:2:{B}"))
    oid = int(next(d for d in datas if d.startswith("bz:sC:")).split(":")[2])
    press(hb, B, f"bz:sC:{oid}:{B}")
    cash0 = cash.balance(E.db, B)
    press(hb, A, f"bz:an:{oid}:{A}")
    assert store.get(E.db, oid)["state"] == "declined" and cash.balance(E.db, B) == cash0 and alerts(press(hb, B, f"bz:sC:{oid}:{B}"))


def test_an_accountant_bot_completes_the_sale_at_once_and_the_receipt_has_the_numbers(hb):
    E = hb.E
    b = _stocked_shop(hb, price=300)
    bid = b["id"]
    text, datas = view(press(hb, A, f"bz:hb:{bid}:{A}"))
    hire = next(d for d in datas if d.startswith(f"bz:hB:{bid}:{C.ROLES.index('accountant')}:"))
    press(hb, A, hire)
    assert staff.bot_role(E.db, bid, "accountant") is not None
    assert alerts(press(hb, A, hire))                                                                        # the same bot twice
    tok = shopui.vtok(*shopui.variants(E.db, bid)[0])
    bizkit.rich(E, B, 1000)
    _at_door(hb, b, B)
    cash0, till0 = cash.balance(E.db, B), biz.get(E.db, bid)["till"]
    press(hb, B, f"bz:sq:{bid}:{tok}:{B}")
    text, datas = view(press(hb, B, f"bz:sQ:{bid}:{tok}:1:{B}"))
    confirm = next(d for d in datas if d.startswith("bz:sC:"))
    oid = int(confirm.split(":")[2])
    out = press(hb, B, confirm)
    o = store.get(E.db, oid)
    assert o["state"] == "done" and o["settled_by"] == "accountant" and cash.balance(E.db, B) == cash0 - 300 and biz.get(E.db, bid)["till"] == till0 + 300
    assert sum(e.qty for e in inventory.bag(E.db, B) if e.item_id == "tank_top") == 1
    again = press(hb, B, confirm)                                                                            # a repeat of the paid button buys nothing
    assert alerts(again) and cash.balance(E.db, B) == cash0 - 300
    view(out)


def test_a_changed_price_voids_the_quote_and_somebody_elses_quote_is_not_yours(hb):
    E = hb.E
    b = _stocked_shop(hb)
    bid = b["id"]
    tok = shopui.vtok(*shopui.variants(E.db, bid)[0])
    bizkit.rich(E, B, 1000)
    bizkit.rich(E, D, 1000)
    _at_door(hb, b, B)
    _at_door(hb, b, D)
    press(hb, B, f"bz:sq:{bid}:{tok}:{B}")
    text, datas = view(press(hb, B, f"bz:sQ:{bid}:{tok}:1:{B}"))
    oid = int(next(d for d in datas if d.startswith("bz:sC:")).split(":")[2])
    assert alerts(press(hb, D, f"bz:sC:{oid}:{D}")) and store.get(E.db, oid)["state"] == "quote"           # not D's quote
    assert alerts(press(hb, D, f"bz:sK:{oid}:{D}")) and store.get(E.db, oid)["state"] == "quote"
    stock.set_rule(E.db, A, bid, "tank_top", SH.NIKE, price=999, now=core.now())
    cash0 = cash.balance(E.db, B)
    assert alerts(press(hb, B, f"bz:sC:{oid}:{B}")) and cash.balance(E.db, B) == cash0 and store.get(E.db, oid)["state"] != "done"


def test_a_quote_expires_and_cancelling_is_final(hb):
    E = hb.E
    b = _stocked_shop(hb)
    bid = b["id"]
    tok = shopui.vtok(*shopui.variants(E.db, bid)[0])
    bizkit.rich(E, B, 1000)
    _at_door(hb, b, B)
    press(hb, B, f"bz:sq:{bid}:{tok}:{B}")
    text, datas = view(press(hb, B, f"bz:sQ:{bid}:{tok}:1:{B}"))
    oid = int(next(d for d in datas if d.startswith("bz:sC:")).split(":")[2])
    press(hb, B, f"bz:sK:{oid}:{B}")
    assert store.get(E.db, oid)["state"] == "cancelled" and alerts(press(hb, B, f"bz:sC:{oid}:{B}"))
    text, datas = view(press(hb, B, f"bz:sQ:{bid}:{tok}:1:{B}"))
    oid2 = int(next(d for d in datas if d.startswith("bz:sC:")).split(":")[2])
    hb.advance(C.QUOTE_TTL + 5)
    cash0 = cash.balance(E.db, B)
    assert alerts(press(hb, B, f"bz:sC:{oid2}:{B}")) and cash.balance(E.db, B) == cash0 and store.get(E.db, oid2)["state"] != "done"


def test_a_customer_without_the_money_is_told_and_nothing_moves(hb):
    E = hb.E
    b = _stocked_shop(hb, price=300)
    bid = b["id"]
    tok = shopui.vtok(*shopui.variants(E.db, bid)[0])
    bizkit.rich(E, B, 1)
    cash.apply(E.db, B, -cash.balance(E.db, B) + 100, "burn", "ui-poor", now=core.now())
    _at_door(hb, b, B)
    press(hb, B, f"bz:sq:{bid}:{tok}:{B}")
    text, datas = view(press(hb, B, f"bz:sQ:{bid}:{tok}:1:{B}"))
    oid = int(next(d for d in datas if d.startswith("bz:sC:")).split(":")[2])
    press(hb, A, f"bz:hB:{bid}:{C.ROLES.index('accountant')}:{A}")                                           # an accountant: the purchase would complete by itself, so only the money can stop it
    cash0, disp0 = cash.balance(E.db, B), stock.qty(E.db, bid, stock.DISP, "tank_top", SH.NIKE)
    assert alerts(press(hb, B, f"bz:sC:{oid}:{B}"))
    assert cash.balance(E.db, B) == cash0 and stock.qty(E.db, bid, stock.DISP, "tank_top", SH.NIKE) == disp0 and store.get(E.db, oid)["state"] != "done"



# ======================================================================== staff and contracts
def _hire_through_buttons(hb, b, role="sales", who=B, pay="f", value=None, owner=A):
    """The owner's whole hiring path by buttons, ending with the sent offer; both stand at the door (an offer goes only to somebody near)."""
    bid = b["id"]
    _at_door(hb, b, owner)
    _at_door(hb, b, who)
    text, datas = view(press(hb, owner, f"bz:sf:{bid}:{owner}"))
    assert f"bz:hp:{bid}:{owner}" in datas
    text, datas = view(press(hb, owner, f"bz:hp:{bid}:{owner}"))
    assert f"bz:hq:{bid}:{who}:{owner}" in datas
    text, datas = view(press(hb, owner, f"bz:hq:{bid}:{who}:{owner}"))
    ri = C.ROLES.index(role)
    assert f"bz:hr:{bid}:{who}:{ri}:{owner}" in datas
    text, datas = view(press(hb, owner, f"bz:hr:{bid}:{who}:{ri}:{owner}"))
    if role == "manager":
        mask = max(int(d.split(":")[5]) for d in datas if d.startswith(f"bz:hy:{bid}:{who}:{ri}:"))
        text, datas = view(press(hb, owner, f"bz:hy:{bid}:{who}:{ri}:{mask}:{owner}"))
    pays = [d for d in datas if d.startswith(f"bz:hf:{bid}:{who}:{ri}:") and d.split(":")[-3] == pay]
    assert pays, datas
    d = pays[0] if value is None else next(x for x in pays if int(x.split(":")[-2]) == value)
    text, datas = view(press(hb, owner, d))
    send = next(x for x in datas if x.startswith(f"bz:hO:{bid}:{who}:{ri}:"))
    return press(hb, owner, send), send


def test_a_hiring_offer_is_accepted_paid_by_the_till_and_ended_through_the_buttons(hb):
    E = hb.E
    b = SH.shop(E, fund=2000)
    bid = b["id"]
    out, send = _hire_through_buttons(hb, b, "sales")
    view(out)
    ct = staff.of_branch(E.db, bid, states=("offered",))[0]
    assert ct["employee_id"] == B and ct["state"] == "offered"
    assert alerts(press(hb, A, send)) and len(staff.of_branch(E.db, bid)) == 1                              # a second send of the same offer: refused
    # the employee sees it and decides; nobody else can
    text, datas = view(press(hb, B, f"bz:k:{B}"))
    assert f"bz:sc:{ct['id']}:{B}" in datas
    text, datas = view(press(hb, B, f"bz:sc:{ct['id']}:{B}"))
    assert f"bz:ka:{ct['id']}:{B}" in datas and f"bz:kd:{ct['id']}:{B}" in datas
    for who in (A, D):
        assert alerts(press(hb, who, f"bz:ka:{ct['id']}:{who}")) and staff.get(E.db, ct["id"])["state"] == "offered"
    assert alerts(press(hb, D, f"bz:sc:{ct['id']}:{D}"))
    out = press(hb, B, f"bz:ka:{ct['id']}:{B}")
    assert staff.get(E.db, ct["id"])["state"] == "active"
    view(out)
    assert alerts(press(hb, B, f"bz:ka:{ct['id']}:{B}")) and alerts(press(hb, B, f"bz:kd:{ct['id']}:{B}")) and staff.get(E.db, ct["id"])["state"] == "active"
    # a day passes: the wage is asked for, paid from the till once, and asking again pays nothing more
    hb.advance(86400)
    owed = staff.owed_total(E.db, staff.get(E.db, ct["id"]), core.now())
    assert owed > 0
    cash0, till0 = cash.balance(E.db, B), biz.get(E.db, bid)["till"]
    press(hb, B, f"bz:kw:{ct['id']}:{B}")
    assert cash.balance(E.db, B) == cash0 + owed and biz.get(E.db, bid)["till"] == till0 - owed
    press(hb, B, f"bz:kw:{ct['id']}:{B}")
    assert cash.balance(E.db, B) == cash0 + owed and biz.audit_till(E.db, bid) == []
    assert alerts(press(hb, D, f"bz:kw:{ct['id']}:{D}"))
    # the employee leaves: asked first, then ended once
    text, datas = view(press(hb, B, f"bz:se:{ct['id']}:{B}"))
    assert staff.get(E.db, ct["id"])["state"] == "active" and f"bz:sE:{ct['id']}:{B}" in datas
    assert alerts(press(hb, D, f"bz:sE:{ct['id']}:{D}")) and staff.get(E.db, ct["id"])["state"] == "active"
    press(hb, B, f"bz:sE:{ct['id']}:{B}")
    assert staff.get(E.db, ct["id"])["state"] == "ended"
    assert alerts(press(hb, B, f"bz:sE:{ct['id']}:{B}"))


def test_a_declined_or_withdrawn_offer_gives_nothing_and_cannot_be_accepted_later(hb):
    E = hb.E
    b = SH.shop(E)
    bid = b["id"]
    _hire_through_buttons(hb, b, "warehouse")
    ct = staff.of_branch(E.db, bid, states=("offered",))[0]
    press(hb, B, f"bz:kd:{ct['id']}:{B}")
    assert staff.get(E.db, ct["id"])["state"] == "declined" and alerts(press(hb, B, f"bz:ka:{ct['id']}:{B}"))
    assert not biz.can(E.db, B, biz.get(E.db, bid), "stock")
    # a second offer the owner takes back before it is answered
    _hire_through_buttons(hb, b, "warehouse")
    ct2 = staff.of_branch(E.db, bid, states=("offered",))[0]
    text, datas = view(press(hb, A, f"bz:sc:{ct2['id']}:{A}"))
    assert f"bz:ow:{ct2['id']}:{A}" in datas
    assert alerts(press(hb, B, f"bz:ow:{ct2['id']}:{B}"))
    press(hb, A, f"bz:ow:{ct2['id']}:{A}")
    assert alerts(press(hb, B, f"bz:ka:{ct2['id']}:{B}")) and staff.get(E.db, ct2["id"])["state"] != "active"
    assert alerts(press(hb, A, f"bz:ow:{ct2['id']}:{A}"))


def test_an_offer_goes_only_to_somebody_standing_near_whatever_the_button_says(hb):
    E = hb.E
    b = SH.shop(E)
    bid = b["id"]
    _at_door(hb, b, A)
    bizkit.teleport(E, B, E.city.mcd_id)
    ri = C.ROLES.index("sales")
    out = press(hb, A, f"bz:hO:{bid}:{B}:{ri}:0:f:100:{A}")
    assert alerts(out) and not staff.of_branch(E.db, bid)
    assert alerts(press(hb, A, f"bz:hO:{bid}:{A}:{ri}:0:f:100:{A}"))                                        # nobody hires themselves
    assert alerts(press(hb, A, f"bz:hO:{bid}:{B}:{ri}:0:f:999999999:{A}")) and alerts(press(hb, A, f"bz:hO:{bid}:{B}:{ri}:0:x:100:{A}"))
    assert alerts(press(hb, B, f"bz:hp:{bid}:{B}")) and alerts(press(hb, B, f"bz:hO:{bid}:{B}:{ri}:0:f:100:{B}"))


def test_a_manager_gets_exactly_the_permissions_the_owner_ticked_and_never_the_owners_own(hb):
    E = hb.E
    b = SH.shop(E, fund=1000)
    bid = b["id"]
    SH.stocked(E, b, n=4, price=300, target=4)
    bid = b["id"]
    ri = C.ROLES.index("manager")
    _at_door(hb, b, A)
    _at_door(hb, b, B)
    text, datas = view(press(hb, A, f"bz:hr:{bid}:{B}:{ri}:{A}"))
    toggles = [d for d in datas if d.startswith(f"bz:hm:{bid}:{B}:{ri}:")]
    assert len(toggles) == len([p for p in biz.PERMS if p != "hire"])
    # untick `stock`: it is the toggle whose perm label is on the button
    i_stock = [p for p in biz.PERMS if p != "hire"].index("stock")
    mask0 = int(toggles[0].split(":")[5]) ^ (1 << i_stock)
    text, datas = view(press(hb, A, toggles[i_stock]))
    ok = next(d for d in datas if d.startswith(f"bz:hy:{bid}:{B}:{ri}:"))
    mask = int(ok.split(":")[5])
    assert not (mask >> i_stock) & 1
    text, datas = view(press(hb, A, ok))
    fixed = next(d for d in datas if d.startswith(f"bz:hf:{bid}:{B}:{ri}:{mask}:f:"))
    text, datas = view(press(hb, A, fixed))
    press(hb, A, next(d for d in datas if d.startswith("bz:hO:")))
    ct = staff.of_branch(E.db, bid, states=("offered",))[0]
    press(hb, B, f"bz:ka:{ct['id']}:{B}")
    b2 = biz.get(E.db, bid)
    ticked = [p for i, p in enumerate([p for p in biz.PERMS if p != "hire"]) if (mask >> i) & 1]
    assert "stock" not in ticked and set(ticked) == set(staff.MANAGER_DEFAULT) - {"stock"}
    assert {p for p in biz.PERMS if biz.can(E.db, B, b2, p)} == set(ticked)                                  # exactly the ticked ones, never `hire` (the owner's alone)
    assert not biz.can(E.db, B, b2, "hire")
    # what the manager can open and what only the owner can
    assert alerts(press(hb, B, f"bz:s:{bid}:{B}"))
    text, datas = view(press(hb, B, f"bz:bo:{bid}:{B}"))
    assert f"bz:s:{bid}:{B}" not in datas and not any(d.startswith(f"bz:sf:") or d.startswith("bz:cl:") for d in datas)
    for data in (f"bz:sf:{bid}:{B}", f"bz:hp:{bid}:{B}", f"bz:hb:{bid}:{B}", f"bz:cl:{bid}:{B}", f"bz:cL:{bid}:{B}", f"bz:wd:{bid}:100:{B}", f"bz:ow:{ct['id']}:{B}"):
        assert alerts(press(hb, B, data)), data
    assert biz.get(E.db, bid)["state"] == "open"


def test_the_owner_ends_a_contract_and_what_was_owed_stays_owed_when_the_till_is_empty(hb):
    E = hb.E
    b = SH.shop(E, fund=1)
    bid = b["id"]
    biz.withdraw(E.db, A, bid, biz.get(E.db, bid)["till"], "empty", core.now())
    _hire_through_buttons(hb, b, "sales")
    ct = staff.of_branch(E.db, bid, states=("offered",))[0]
    press(hb, B, f"bz:ka:{ct['id']}:{B}")
    hb.advance(2 * 86400)
    cash0 = cash.balance(E.db, B)
    press(hb, B, f"bz:kw:{ct['id']}:{B}")
    assert cash.balance(E.db, B) == cash0 and staff.owed_total(E.db, staff.get(E.db, ct["id"]), core.now()) > 0           # nothing in the till: nothing paid, nothing lost
    press(hb, A, f"bz:sE:{ct['id']}:{A}")
    assert staff.get(E.db, ct["id"])["state"] == "ended"


# ======================================================================== reports, reputation, style
def test_the_report_shows_the_sales_and_belongs_to_the_owner_and_staff_only(hb):
    E = hb.E
    b = _stocked_shop(hb, price=300)
    bid = b["id"]
    bizkit.rich(E, B, 1000)
    press(hb, A, f"bz:hB:{bid}:{C.ROLES.index('accountant')}:{A}")
    tok = shopui.vtok(*shopui.variants(E.db, bid)[0])
    _at_door(hb, b, B)
    press(hb, B, f"bz:sq:{bid}:{tok}:{B}")
    text, datas = view(press(hb, B, f"bz:sQ:{bid}:{tok}:2:{B}"))
    press(hb, B, next(d for d in datas if d.startswith("bz:sC:")))
    text, datas = view(press(hb, A, f"bz:rp:{bid}:{A}"))
    assert "$۶٫۰۰" in text and f"bz:rp:{bid}:7:{A}" in datas and f"bz:rp:{bid}:1:{A}" in datas
    for d in (1, 7):
        view(press(hb, A, f"bz:rp:{bid}:{d}:{A}"))
    assert alerts(press(hb, A, f"bz:rp:{bid}:3:{A}")) and alerts(press(hb, B, f"bz:rp:{bid}:{B}")) and alerts(press(hb, B, f"bz:rp:{bid}:7:{B}"))
    text, datas = view(press(hb, A, f"bz:ro:{A}"))
    assert f"bz:rp:{bid}:0:{A}" in datas
    text, datas = view(press(hb, B, f"bz:ro:{B}"))                                                          # somebody with no branch gets an empty overview, not an error
    assert not any(d.startswith("bz:rp:") for d in datas)


def test_the_reputation_directory_orders_by_the_real_score_and_the_sign_only_lifts_the_order(hb):
    E = hb.E
    b1 = SH.shop(E, uid=A)
    b2 = SH.shop(E, uid=B, brand="balenciaga")
    rep.add(E.db, b1["id"], "t", "x1", 100, None, core.now())
    rep.add(E.db, b2["id"], "t", "x2", 100, None, core.now())
    text, datas = view(press(hb, A, f"bz:t:{A}"))
    up = text.upper()
    assert 0 <= up.index("NIKE") < up.index("BALENCIAGA")                                                   # the screen shows the same order
    order = lambda: [r["branch"]["id"] for r in rep.directory(E.db, E.city.id, 8)]      # noqa: E731
    assert order() == [b1["id"], b2["id"]]                                                                  # equal: the older branch first
    biz.fund(E.db, B, b2["id"], 5_000_00, "t-sign", core.now())
    for lvl in (1, 2, 3):
        upgrades.install(E.db, B, b2["id"], "sign", core.now())
    assert order() == [b2["id"], b1["id"]] and rep.score(E.db, b2["id"]) == rep.score(E.db, b1["id"])      # the order moved, the score did not
    text, datas = view(press(hb, A, f"bz:t:{A}"))
    up = text.upper()
    assert 0 <= up.index("BALENCIAGA") < up.index("NIKE")
    assert f"bz:t:g:{A}" in datas
    view(press(hb, A, f"bz:t:g:{A}"))
    view(press(hb, A, f"bz:t:c:{A}"))
    view(press(hb, A, f"bz:t:x:{A}"))                                                                       # an unknown scope is the whole game, not an error
    assert alerts(press(hb, A, f"bz:t:Z9:{A}"))                                                             # a scope that is not even a word is a hand-made button


def test_the_style_screen_saves_and_wears_outfits_and_refuses_what_it_cannot(hb):
    E = hb.E
    text, datas = view(press(hb, A, f"bz:y:{A}"))
    assert f"bz:ys:{C.OUTFIT_KINDS[0]}:{A}" in datas and not any(d.startswith("bz:yw:") for d in datas)
    assert alerts(press(hb, A, f"bz:ys:{C.OUTFIT_KINDS[0]}:{A}"))                                           # nothing worn: nothing to save
    for item in ("tshirt", "jeans", "sneakers"):
        SH.wear(E, A, item, 4)
    pts = style.rating(E.db, A)["points"]
    assert pts > 0
    press(hb, A, f"bz:ys:{C.OUTFIT_KINDS[0]}:{A}")
    text, datas = view(press(hb, A, f"bz:y:{A}"))
    assert f"bz:yw:{C.OUTFIT_KINDS[0]}:{A}" in datas
    for slot in list(inventory.worn(E.db, A)):
        inventory.unequip(E.db, A, slot)
    assert style.rating(E.db, A)["points"] == 0
    assert alerts(press(hb, A, f"bz:yw:{C.OUTFIT_KINDS[1]}:{A}"))                                           # an outfit never saved
    assert alerts(press(hb, A, f"bz:ys:nonsense:{A}"))
    view(press(hb, A, f"bz:yw:{C.OUTFIT_KINDS[0]}:{A}"))
    assert style.rating(E.db, A)["points"] == pts


# ======================================================================== the cotton job
def _site(hb):
    return textile.site_id(hb.E.city)


def test_the_job_board_offers_cotton_and_a_far_player_is_given_the_walk(hb):
    E = hb.E
    bizkit.teleport(E, A, E.city.mcd_id)
    text, datas = view(press(hb, A, f"jb:h:{A}"))
    assert f"jb:o:cotton:{A}" in datas
    text, datas = view(press(hb, A, f"jb:o:cotton:{A}"))
    assert f"jb:cg:{A}" in datas and not any(d.startswith("jb:cs:") for d in datas) and f"bz:ab:1:{A}" in datas
    assert alerts(press(hb, A, f"jb:cs:1:{A}")) and textile.current(E.db, A) is None                          # a hand-made start from far away
    out = press(hb, A, f"jb:cg:{A}")
    assert presence.walking_trip(E.db, E.city.id, A) is not None
    view(out)
    view(press(hb, A, f"jb:o:cotton:{A}"))                                                                      # the walk shows its progress


def test_a_whole_cotton_batch_through_the_buttons_and_the_fabric_is_sold_to_the_game(hb):
    E = hb.E
    bizkit.teleport(E, A, _site(hb))
    text, datas = view(press(hb, A, f"jb:o:cotton:{A}"))
    assert f"jb:cs:1:{A}" in datas and not any(d.startswith("jb:cs:5:") for d in datas)                         # only the grades the skill has opened
    assert alerts(press(hb, A, f"jb:cs:5:{A}")) and textile.current(E.db, A) is None
    out = press(hb, A, f"jb:cs:1:{A}")
    task = textile.current(E.db, A)
    assert task is not None and work.current(E.db, A)["job"] == "cotton"
    view(out)
    press(hb, A, f"jb:cs:1:{A}")                                                                                # a second press: the same batch, never another
    assert textile.current(E.db, A)["id"] == task["id"]
    assert alerts(press(hb, A, f"jb:cc:{task['id']}:{A}"))                                                      # not ready
    text, datas = view(press(hb, A, f"jb:o:cotton:{A}"))
    assert f"jb:cc:{task['id']}:{A}" not in datas and f"jb:x:{task['id']}:{A}" in datas
    hb.advance(int(task["due_at"]) - core.now() + 1)
    text, datas = view(press(hb, A, f"jb:o:cotton:{A}"))
    assert f"jb:cc:{task['id']}:{A}" in datas
    assert alerts(press(hb, B, f"jb:cc:{task['id']}:{B}"))                                                      # somebody else's batch
    work0 = skills.work_of(E.db, A, "textile")
    out = press(hb, A, f"jb:cc:{task['id']}:{A}")
    units = int(task["reward"])
    assert textile.fabric_count(E.db, A) == units and textile.current(E.db, A) is None and skills.work_of(E.db, A, "textile") > work0
    view(out)
    assert alerts(press(hb, A, f"jb:cc:{task['id']}:{A}")) and textile.fabric_count(E.db, A) == units          # collected once
    # the fabric screen and the sale at the fixed price
    text, datas = view(press(hb, A, f"jb:cf:{A}"))
    sell_all = next(d for d in datas if d.startswith("jb:cq:") and d.split(":")[3] == "0")
    q = int(sell_all.split(":")[2])
    cash0 = cash.balance(E.db, A)
    press(hb, A, sell_all)
    assert cash.balance(E.db, A) == cash0 + units * C.FABRIC_GAME_CENTS[q] and textile.fabric_count(E.db, A) == 0
    assert alerts(press(hb, A, sell_all)) and cash.balance(E.db, A) == cash0 + units * C.FABRIC_GAME_CENTS[q]
    assert alerts(press(hb, A, f"jb:cq:{q}:5:{A}"))                                                             # more than there is
    assert alerts(press(hb, A, f"jb:cq:9:1:{A}"))                                                               # a quality that does not exist


def test_giving_up_a_batch_frees_the_player_and_gives_no_fabric(hb):
    E = hb.E
    bizkit.teleport(E, A, _site(hb))
    press(hb, A, f"jb:cs:1:{A}")
    task = textile.current(E.db, A)
    stray = press(hb, B, f"jb:x:{task['id']}:{B}")
    assert toasts(stray) and textile.current(E.db, A)["id"] == task["id"] and work.current(E.db, B) is None            # somebody else's batch: told it is stale, nothing happens to it
    press(hb, A, f"jb:x:{task['id']}:{A}")
    assert textile.current(E.db, A) is None and work.current(E.db, A) is None and textile.fabric_count(E.db, A) == 0
    assert alerts(press(hb, A, f"jb:cc:{task['id']}:{A}"))
    view(press(hb, A, f"jb:o:cotton:{A}"))


def test_another_job_running_blocks_cotton_and_the_other_way_round(hb):
    E = hb.E
    bizkit.teleport(E, A, _site(hb))
    press(hb, A, f"jb:cs:1:{A}")
    text, datas = view(press(hb, A, f"jb:o:snapp:{A}"))
    assert textile.current(E.db, A) is not None and work.current(E.db, A)["job"] == "cotton"


def test_cotton_works_in_a_private_chat_with_the_players_own_city(hb):
    E = hb.E
    bizkit.teleport(E, A, _site(hb))
    text, datas = view(press(hb, A, f"jb:o:cotton:{A}", chat=A))
    assert f"jb:cs:1:{A}" in datas
    press(hb, A, f"jb:cs:1:{A}", chat=A)
    assert textile.current(E.db, A) is not None
    text, datas = view(press(hb, A, f"bz:h:{A}", chat=A))
    assert f"bz:n:1:{A}" in datas


def test_the_hub_works_in_a_private_chat_and_a_player_without_a_city_is_told_so(hb):
    E = hb.E
    for chat in (A,):
        view(press(hb, A, f"bz:n:1:{A}", chat=chat))
        view(press(hb, A, f"bz:p:1:{A}", chat=chat))
    out = say(hb, 99001, "کسب‌وکار", chat=99001)
    assert screen(out)


# ======================================================================== the wallet and the labels
def test_every_business_ledger_kind_has_a_readable_wallet_label(hb):
    from hypemarket import lifeui
    kinds = {"property_buy", "property_fit", "property_convert", "brand_fee", "biz_fund", "biz_withdraw", "store_buy", "ad_sale", "fabric_sale", "wage_in", "wage_out"}
    used = set(__import__("re").findall(r'cash\.(?:apply|spend|earn|credit|debit)\([^)]*?"([a-z_]+)"', "".join(open(f"hypemarket/{m}.py", encoding="utf-8").read() for m in ("estate", "brands", "biz", "staff", "market", "store", "textile", "tailor"))))
    for k in sorted(used | kinds):
        label = lifeui._kind_label("fa", k) if hasattr(lifeui, "_kind_label") else None
        assert label and "{" not in label and not label.startswith("cash.k."), k
