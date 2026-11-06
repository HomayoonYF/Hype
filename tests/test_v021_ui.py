"""v0.21: the inventory as the player sees it: the command `کوله‌پشتی`, the screens and their buttons, driven through the real handlers (a fake Telegram records what the bot sent).

Covered: the way in (command words, the main menu, the character hub), the empty state, the categories, pages, an item's details, wearing / taking off / replacing through buttons, using an item
(only when an effect exists), a repeated or outdated button, somebody else pressing your buttons, hand-made buttons, the unfinished character, English and Persian, number style, button sizes
(Telegram's 64 bytes), and a category / slot / item added only by registering it. What this cannot cover: how Telegram draws the messages and keyboards on a real phone."""
import re

import pytest

pytestmark = pytest.mark.usefixtures("legacy_catalog")      # v0.211 seeds no item: these suites bring the 17 old starter items themselves

import charkit
import test_bot as tb
from hypemarket import character, config as C, core, fmt, inventory as I, invui, items, textparse
from hypemarket.texts import ALIASES, EN, FA, t

REAL_CHARACTER_SETUP = True

P1, P2, P3 = 887001, 887002, 887003
GROUP = -100961


@pytest.fixture()
def h():
    hh = tb.Harness()
    return hh


@pytest.fixture()
def registry():
    saved = (dict(items.CATEGORIES), dict(items.SLOTS), dict(items.ITEMS), dict(items.EFFECTS))
    yield items
    for reg, old in zip((items.CATEGORIES, items.SLOTS, items.ITEMS, items.EFFECTS), saved):
        reg.clear()
        reg.update(old)


def join(h, uid, first="آرمان", last="کیانی", chat=None):
    """The player exists and has a FINISHED character (the set-up itself is covered by the v0.19 / v0.20 suites)."""
    h.say(uid, "منو", chat=chat)
    charkit.complete(h.db, uid, first=first, last=last)


def kinds(calls):
    return [type(c).__name__ for c in calls]


def of(calls, name):
    return [c for c in calls if type(c).__name__ == name]


def buttons(call):
    kbd = getattr(call, "reply_markup", None)
    return [] if kbd is None else [(b.text, b.callback_data) for row in kbd.inline_keyboard for b in row if b.callback_data]


def shown(calls):
    """The last message the bot sent or edited: (text, buttons)."""
    msgs = [c for c in calls if type(c).__name__ in ("SendMessage", "EditMessageText")]
    return (msgs[-1].text, buttons(msgs[-1])) if msgs else (None, [])


def toast(calls):
    a = of(calls, "AnswerCallbackQuery")
    return a[-1].text if a and a[-1].text else None


def open_inv(h, uid, chat=None):
    return h.say(uid, "کوله‌پشتی", chat=chat)


def press(h, uid, *parts, chat=None):
    """A button of the inventory. v0.211: an item's buttons carry the id of ITS STACK (the same item can be several stacks: a plain one, a 4-star one, a branded one ...), so a test that names
    an item by its code gets the player's first stack of it (or a stack id that does not exist, for an item that is no longer in the bag)."""
    parts = [str(p) for p in parts]
    if len(parts) >= 2 and parts[0] in ("o", "e", "x") and not parts[1].isdigit():
        e = I.entry(h.db, uid, parts[1])
        parts[1] = str(e.stack_id) if e is not None else "999999"
    return h.press(uid, ":".join(("iv",) + tuple(parts) + (str(uid),)), chat=chat)


def give(h, uid, item, qty=1, ref=None):
    return I.give(h.db, uid, item, qty, "gift", ref or f"t-{item}-{h.n}-{qty}")


def snapshot(h, uid):
    return ([tuple(r) for r in h.db.qa("SELECT * FROM item_inventory WHERE user_id=? ORDER BY id", (uid,))],
            [tuple(r) for r in h.db.qa("SELECT * FROM item_equipment WHERE user_id=? ORDER BY slot", (uid,))], [tuple(r) for r in h.db.qa("SELECT * FROM item_log WHERE user_id=? ORDER BY id", (uid,))])


# ============================================================================================== the way in
@pytest.mark.parametrize("word", ["کوله‌پشتی", "کوله پشتی", "کولهپشتی", "inventory", "backpack", "/inventory"])
def test_every_word_opens_the_inventory(h, word):
    join(h, P1)
    give(h, P1, "sandwich", 3)
    out = h.say(P1, word)
    text, b = shown(out)
    assert text and "کوله‌پشتی" in text and (t("fa", "iv.b.eq"), f"iv:q:{P1}") in b and not of(out, "SendPhoto")


def test_the_words_are_commands_in_a_group_only_as_the_whole_message(h):
    join(h, P1, chat=GROUP)
    for word in ALIASES["inventory"]:
        p = textparse.parse(word)
        assert p is not None and p.cmd == "inventory", word
    assert "کوله‌پشتی" in shown(open_inv(h, P1, GROUP))[0]
    assert not tb.Harness.texts(h.say(P1, "کوله‌پشتی من کجاست؟", chat=GROUP))                     # a sentence is just chat


def test_the_main_menu_and_the_hub_have_one_button_each(h):
    join(h, P1)
    menu = shown(h.say(P1, "منو"))[1]
    assert (t("fa", "menu.inventory"), f"mn:go:inventory:{P1}") in menu and len(menu) == 13 and (t("fa", "menu.business"), f"mn:go:business:{P1}") in menu        # v0.211: one more button («کسب‌وکار»)
    out = h.press(P1, f"mn:go:inventory:{P1}")
    text, b = shown(out)
    assert "کوله‌پشتی" in text and len(of(out, "EditMessageText")) == 1 and (t("fa", "menu.back"), f"mn:menu:{P1}") in b and (t("fa", "iv.b.hub"), f"cg:hub:{P1}") in b
    hub = shown(h.say(P1, "شخصیت"))[1]
    assert (t("fa", "cg.b.inv"), f"iv:m:{P1}") in hub
    out = h.press(P1, f"iv:m:{P1}")
    assert "کوله‌پشتی" in shown(out)[0] and (t("fa", "iv.b.hub"), f"cg:hub:{P1}") in shown(out)[1]
    back = h.press(P1, f"cg:hub:{P1}")
    assert "شخصیت" in shown(back)[0] and (t("fa", "cg.b.inv"), f"iv:m:{P1}") in shown(back)[1]


# ============================================================================================== empty and the main screen
def test_an_empty_inventory_says_so_and_only_offers_the_way_back(h):
    join(h, P1)
    text, b = shown(open_inv(h, P1))
    assert t("fa", "iv.empty", name="آرمان کیانی") == text and b == [(t("fa", "iv.b.hub"), f"cg:hub:{P1}")]
    assert "آرمان کیانی" in text


def test_the_main_screen_shows_totals_categories_and_quantities(h):
    join(h, P1)
    give(h, P1, "tshirt_white", 2)
    give(h, P1, "sandwich", 7)
    give(h, P1, "soda_can", 30)
    give(h, P1, "gift_box", 1)
    text, b = shown(open_inv(h, P1))
    n = lambda x: fmt.num(x, "fa")
    assert f"<b>{n(40)}</b> عدد ({n(4)} نوع)" in text                                              # 2 + 7 + 30 + 1 items of 4 kinds
    assert I.stack_count(h.db, P1) == 6 and f"<b>{n(6)}</b> از {n(C.INV_MAX_STACKS)}" in text      # 2 shirts, 1 sandwich stack, 2 soda stacks (24 + 6), 1 gift box
    assert f"پوشاک: <b>{n(2)}</b>" in text and f"خوراکی و نوشیدنی: <b>{n(37)}</b>" in text and f"هدایا: <b>{n(1)}</b>" in text and f"کفش: <b>{n(0)}</b>" in text
    assert [c for c in b if c[1].startswith("iv:c:")] == [(f"{items.CATEGORIES[cid].icon} {items.category_name('fa', cid)}" + (f" ({n(q)})" if q else ""), f"iv:c:{cid}:1:{P1}")
                                                         for cid, q in zip(items.CATEGORIES, (2, 0, 0, 37, 0, 1, 0))]
    assert (t("fa", "iv.b.eq"), f"iv:q:{P1}") in b and b[-1] == (t("fa", "iv.b.hub"), f"cg:hub:{P1}")


def test_only_the_owners_character_name_is_shown_and_escaped(h):
    join(h, P1, first="A<b>", last="Z&Q")
    give(h, P1, "sandwich", 1)
    text, _ = shown(open_inv(h, P1))
    assert "A&lt;b&gt; Z&amp;Q" in text                                                             # (the fake Telegram also rejects broken HTML)


# ============================================================================================== categories and items
def test_every_category_screen_opens_and_has_a_way_back(h):
    join(h, P1)
    for cid in items.CATEGORIES:
        give(h, P1, next(d.id for d in items.ITEMS.values() if d.category == cid), 1)
    for cid, c in items.CATEGORIES.items():
        out = press(h, P1, "c", cid, 1)
        text, b = shown(out)
        assert c.name_fa in text and len(of(out, "EditMessageText")) == 1
        assert b[-1] == (t("fa", "iv.b.back"), f"iv:m:{P1}") and sum(1 for x in b if x[1].startswith("iv:o:")) == 1


def test_an_empty_category_has_its_own_message(h):
    join(h, P1)
    give(h, P1, "sandwich", 1)
    text, b = shown(press(h, P1, "c", "footwear", 1))
    assert t("fa", "iv.cat.none") in text and b == [(t("fa", "iv.b.back"), f"iv:m:{P1}")]


def test_an_item_shows_its_details_and_only_the_actions_that_exist(h):
    join(h, P1)
    give(h, P1, "tshirt_white", 2)
    give(h, P1, "soda_can", 30)
    text, b = shown(press(h, P1, "o", "tshirt_white", 1))
    assert "تی‌شرت سفید" in text and "تی‌شرت نخی ساده‌ی سفید برای هر روز." in text and "پوشاک" in text and "White T-shirt" in text
    assert f"تعداد در کوله: <b>{fmt.num(2, 'fa')}</b>" in text and "بالاتنه" in text and "هدیه" in text and fmt.date(tb.T[0], "fa") in text
    sid = I.entry(h.db, P1, "tshirt_white").stack_id
    assert b == [(t("fa", "iv.b.equip"), f"iv:e:{sid}:1:{P1}"), (t("fa", "iv.b.cat", cat="پوشاک"), f"iv:c:clothing:1:{P1}")]
    text, b = shown(press(h, P1, "o", "soda_can", 1))
    assert f"حداکثر هر پشته: <b>{fmt.num(24, 'fa')}</b>" in text and f"تعداد در کوله: <b>{fmt.num(30, 'fa')}</b>" in text
    assert [x[1] for x in b] == [f"iv:c:food:1:{P1}"]                                              # not wearable, no effect: no equip button and no use button
    assert "قابل پوشیدن" not in text


def test_an_item_that_left_the_bag_sends_you_back_to_its_category(h):
    join(h, P1)
    give(h, P1, "sandwich", 1)
    I.take(h.db, P1, "sandwich", 1, "use", "gone")
    out = press(h, P1, "o", "sandwich", 1)
    text, b = shown(out)
    assert t("fa", "iv.stale") in text and t("fa", "iv.cat.none") in text and kinds(out).count("EditMessageText") == 1


def test_the_source_of_an_item_is_named(h):
    join(h, P1)
    I.give(h.db, P1, "old_key", 1, "system", "s")
    I.give(h.db, P1, "umbrella", 1, "quest", "q")
    assert "سیستم" in shown(press(h, P1, "o", "old_key", 1))[0]
    assert "quest" in shown(press(h, P1, "o", "umbrella", 1))[0]                                    # a source without its own name shows as its code


# ============================================================================================== pages
def many(registry, n=15):
    for i in range(n):
        items.register_item(id=f"extra_{i:02d}", name_fa=f"چیز شماره {i + 1}", category="misc")


def test_a_big_category_is_split_into_pages_and_every_item_is_reachable_once(h, registry):
    many(registry)
    join(h, P1)
    for i in range(15):
        give(h, P1, f"extra_{i:02d}", 1)
    size = C.INV_PAGE_SIZE
    seen = []
    for page in (1, 2, 3):
        text, b = shown(press(h, P1, "c", "misc", page))
        item_btns = [x for x in b if x[1].startswith("iv:o:")]
        seen += [h.db.q1("SELECT item_id FROM item_inventory WHERE id=?", (int(x[1].split(":")[2]),))["item_id"] for x in item_btns]
        nav = [x[0] for x in b if x[1].startswith("iv:c:")]
        assert len(item_btns) == (size if page < 3 else 15 - 2 * size)
        assert nav == ([t("fa", "iv.b.next")] if page == 1 else [t("fa", "iv.b.prev"), t("fa", "iv.b.next")] if page == 2 else [t("fa", "iv.b.prev")])
        assert t("fa", "iv.cat.pages", page=fmt.num(page, "fa"), pages=fmt.num(3, "fa")) in text
        assert b[-1] == (t("fa", "iv.b.back"), f"iv:m:{P1}")
    assert sorted(seen) == sorted(f"extra_{i:02d}" for i in range(15)) and len(seen) == len(set(seen))
    # the buttons really lead to the next page, and an item's back button returns to the page it came from
    _, b = shown(press(h, P1, "c", "misc", 1))
    nxt = next(x for x in b if x[0] == t("fa", "iv.b.next"))
    text2, b2 = shown(h.press(P1, nxt[1]))
    assert t("fa", "iv.cat.pages", page=fmt.num(2, "fa"), pages=fmt.num(3, "fa")) in text2
    first = next(x for x in b2 if x[1].startswith("iv:o:"))
    _, bi = shown(h.press(P1, first[1]))
    assert (t("fa", "iv.b.cat", cat="متفرقه"), f"iv:c:misc:2:{P1}") in bi


def test_a_page_past_the_end_shows_the_last_page_and_page_zero_the_first(h, registry):
    many(registry)
    join(h, P1)
    for i in range(15):
        give(h, P1, f"extra_{i:02d}", 1)
    assert t("fa", "iv.cat.pages", page=fmt.num(3, "fa"), pages=fmt.num(3, "fa")) in shown(press(h, P1, "c", "misc", 99))[0]
    assert t("fa", "iv.cat.pages", page=fmt.num(1, "fa"), pages=fmt.num(3, "fa")) in shown(press(h, P1, "c", "misc", 0))[0]


def test_a_short_category_has_no_page_buttons_and_no_page_line(h):
    join(h, P1)
    for item in ("tshirt_white", "cap_black", "jeans_blue"):
        give(h, P1, item)
    text, b = shown(press(h, P1, "c", "clothing", 1))
    assert not any(x[0] in (t("fa", "iv.b.next"), t("fa", "iv.b.prev")) for x in b) and "صفحه" not in text


def test_the_slot_screen_pages_its_candidates_too(h, registry):
    for i in range(8):
        items.register_item(id=f"scarf_{i}", name_fa=f"شال {i + 1}", category="accessories", equippable=True, slot="accessory")
    join(h, P1)
    for i in range(8):
        give(h, P1, f"scarf_{i}")
    text, b = shown(press(h, P1, "s", "accessory", 1))
    assert sum(1 for x in b if x[1].startswith("iv:e:")) == C.INV_PAGE_SIZE and (t("fa", "iv.b.next"), f"iv:s:accessory:2:{P1}") in b
    text, b = shown(press(h, P1, "s", "accessory", 2))
    assert sum(1 for x in b if x[1].startswith("iv:e:")) == 2 and (t("fa", "iv.b.prev"), f"iv:s:accessory:1:{P1}") in b


# ============================================================================================== wearing through buttons
def eq_state(h, uid):
    return {s: w.item_id for s, w in I.worn(h.db, uid).items()}


def test_wearing_an_item_from_its_screen_puts_it_in_the_equipment(h):
    join(h, P1)
    give(h, P1, "tshirt_white")
    give(h, P1, "jeans_blue")
    _, b = shown(press(h, P1, "o", "tshirt_white", 1))
    out = h.press(P1, next(x for x in b if x[1].startswith("iv:e:"))[1])
    text, b2 = shown(out)
    assert t("fa", "iv.ok.equip", item="تی‌شرت سفید") in text and "بالاتنه: <b>تی‌شرت سفید</b>" in text and len(of(out, "EditMessageText")) == 1
    assert eq_state(h, P1) == {"upper": "tshirt_white"} and I.count(h.db, P1, "tshirt_white") == 0
    assert (t("fa", "iv.b.back"), f"iv:m:{P1}") in b2
    assert f"پوشیده: <b>{fmt.num(1, 'fa')}</b>" in shown(press(h, P1, "m"))[0]                       # the main screen counts it


def test_wearing_in_an_occupied_slot_swaps_and_says_where_the_old_one_went(h):
    join(h, P1)
    give(h, P1, "tshirt_white")
    give(h, P1, "hoodie_green")
    h.press(P1, f"iv:e:{I.entry(h.db, P1, 'tshirt_white').stack_id}:1:{P1}")
    out = h.press(P1, f"iv:e:{I.entry(h.db, P1, 'hoodie_green').stack_id}:1:{P1}")
    text, _ = shown(out)
    assert t("fa", "iv.ok.equip", item="هودی سبز") in text and t("fa", "iv.ok.replaced", old="تی‌شرت سفید").strip() in text
    assert eq_state(h, P1) == {"upper": "hoodie_green"} and I.count(h.db, P1, "tshirt_white") == 1 and I.audit(h.db, P1) == []


def test_the_slot_screen_shows_the_worn_item_and_takes_it_off(h):
    join(h, P1)
    give(h, P1, "cap_black")
    give(h, P1, "tshirt_white")
    h.press(P1, f"iv:e:{I.entry(h.db, P1, 'cap_black').stack_id}:1:{P1}")
    text, b = shown(press(h, P1, "s", "headwear", 1))
    assert "کلاه لبه‌دار مشکی" in text and (t("fa", "iv.b.unequip"), f"iv:u:headwear:{P1}") in b and t("fa", "iv.slot.nothing") in text
    assert not any("تی‌شرت" in x[0] for x in b)                                                       # only items that fit this slot are offered
    out = h.press(P1, f"iv:u:headwear:{P1}")
    assert t("fa", "iv.ok.unequip", item="کلاه لبه‌دار مشکی") in shown(out)[0] and eq_state(h, P1) == {} and I.count(h.db, P1, "cap_black") == 1


def test_an_empty_slot_offers_what_can_be_worn_there(h):
    join(h, P1)
    give(h, P1, "boots_brown")
    give(h, P1, "sneakers_white")
    text, b = shown(press(h, P1, "s", "footwear", 1))
    assert t("fa", "iv.slot.none") in text and t("fa", "iv.slot.pick", pages="") in text
    assert [x[0] for x in b if x[1].startswith("iv:e:")] == [t("fa", "iv.b.wear", item="👟 کفش ورزشی سفید"), t("fa", "iv.b.wear", item="🥾 بوت قهوه‌ای")]       # in catalog order
    assert b[-1] == (t("fa", "iv.b.eq_back"), f"iv:q:{P1}")


def test_the_equipment_screen_lists_every_slot_and_every_slot_opens(h):
    join(h, P1)
    give(h, P1, "tshirt_white")
    h.press(P1, f"iv:e:{I.entry(h.db, P1, 'tshirt_white').stack_id}:1:{P1}")
    text, b = shown(press(h, P1, "q"))
    for sid, s in items.SLOTS.items():
        assert s.name_fa in text
        assert (f"{s.icon} {s.name_fa}", f"iv:s:{sid}:1:{P1}") in b
        assert shown(press(h, P1, "s", sid, 1))[1][-1] == (t("fa", "iv.b.eq_back"), f"iv:q:{P1}")                    # every slot opens and leads back to the equipment
    assert "خالی" in text and b[-1] == (t("fa", "iv.b.back"), f"iv:m:{P1}")


# ============================================================================================== repeated / old / foreign buttons
def test_pressing_wear_twice_wears_it_once_and_the_second_press_only_refreshes(h):
    join(h, P1)
    give(h, P1, "tshirt_white")
    btn = f"iv:e:{I.entry(h.db, P1, 'tshirt_white').stack_id}:1:{P1}"
    h.press(P1, btn)
    snap = snapshot(h, P1)
    out = h.press(P1, btn)                                                                           # the same button again (a duplicate delivery or a second tap)
    assert snapshot(h, P1) == snap and eq_state(h, P1) == {"upper": "tshirt_white"} and I.audit(h.db, P1) == []
    assert t("fa", "iv.stale") in shown(out)[0] and toast(out) == "این آیتم دیگه توی کوله‌پشتیت نیست."


def test_taking_off_twice_returns_it_once(h):
    join(h, P1)
    give(h, P1, "cap_black")
    h.press(P1, f"iv:e:{I.entry(h.db, P1, 'cap_black').stack_id}:1:{P1}")
    h.press(P1, f"iv:u:headwear:{P1}")
    snap = snapshot(h, P1)
    out = h.press(P1, f"iv:u:headwear:{P1}")
    assert snapshot(h, P1) == snap and I.count(h.db, P1, "cap_black") == 1 and toast(out) == "توی این اسلات چیزی نپوشیدی."


def test_nobody_else_can_press_your_buttons(h):
    join(h, P1)
    join(h, P2, first="بابک", last="رضایی")
    give(h, P1, "tshirt_white")
    snap1, snap2 = snapshot(h, P1), snapshot(h, P2)
    sid = I.entry(h.db, P1, "tshirt_white").stack_id
    for data in (f"iv:m:{P1}", f"iv:c:clothing:1:{P1}", f"iv:o:tshirt_white:1:{P1}", f"iv:e:{sid}:1:{P1}", f"iv:q:{P1}", f"iv:u:upper:{P1}", f"iv:s:upper:1:{P1}", f"iv:x:{sid}:1:1:{P1}"):
        out = h.press(P2, data)
        assert toast(out) == t("fa", "not_yours") and not of(out, "EditMessageText") and not of(out, "SendMessage"), data
    assert snapshot(h, P1) == snap1 and snapshot(h, P2) == snap2


def test_a_forged_button_with_your_id_and_somebody_elses_stack_changes_nothing(h):
    join(h, P1)
    join(h, P2, first="بابک", last="رضایی")
    give(h, P1, "tshirt_white")
    give(h, P2, "sandwich", 4)
    sid = I.entry(h.db, P1, "tshirt_white").stack_id
    snap1, snap2 = snapshot(h, P1), snapshot(h, P2)
    out = h.press(P2, f"iv:e:{sid}:1:{P2}")                                                          # P2 signs the button with their own id but names P1's stack
    assert snapshot(h, P1) == snap1 and snapshot(h, P2) == snap2 and I.worn(h.db, P1) == {} and I.worn(h.db, P2) == {}
    assert t("fa", "iv.stale") in shown(out)[0]
    out = h.press(P2, f"iv:x:{sid}:1:1:{P2}")
    assert snapshot(h, P1) == snap1 and snapshot(h, P2) == snap2


@pytest.mark.parametrize("data", ["iv:zz:{u}", "iv:{u}", "iv:c:nope:1:{u}", "iv:c:clothing:x:{u}", "iv:c:clothing:{u}", "iv:o:../x:1:{u}", "iv:o:UPPER:1:{u}", "iv:o:tshirt_white:{u}",
                                  "iv:e:abc:1:{u}", "iv:e:1:{u}", "iv:e:1:2:3:{u}", "iv:u:UP:{u}", "iv:u:upper:extra:{u}", "iv:x:1:2:{u}", "iv:x:1:2:3:4:{u}", "iv:s:nope:1:{u}",
                                  "iv:q:extra:{u}", "iv:m:extra:{u}", "iv:c:clothing:" + "9" * 30 + ":{u}", "iv:e:" + "9" * 30 + ":1:{u}"])
def test_hand_made_buttons_do_nothing_harmful(h, data):
    join(h, P1)
    give(h, P1, "tshirt_white")
    snap = snapshot(h, P1)
    out = h.press(P1, data.format(u=P1))
    assert snapshot(h, P1) == snap and not of(out, "SendPhoto")
    assert not any("crash" in (getattr(c, "text", "") or "").lower() for c in out)


def test_the_unfinished_character_gets_the_set_up_and_no_inventory(h):
    h.say(P3, "منو")                                                                                   # the player exists; the character is not finished
    I.give(h.db, P3, "sandwich", 1, "gift", "early")
    out = h.say(P3, "کوله‌پشتی")
    assert t("fa", "char.s.first", min=C.CHAR_NAME_MIN, max=C.CHAR_NAME_MAX) in tb.Harness.texts(out) and "کوله‌پشتی" not in tb.Harness.texts(out)
    grp = h.say(P3, "کوله‌پشتی", chat=GROUP)
    assert t("fa", "char.need") in tb.Harness.texts(grp)
    out = h.press(P3, f"iv:m:{P3}")
    assert "🎒 <b>کوله‌پشتی</b>" not in tb.Harness.texts(out) and not any(d.startswith("iv:") for _, d in shown(out)[1])
    assert I.count(h.db, P3, "sandwich") == 1                                                          # (what the player already got is kept for after the set-up)


# ============================================================================================== using
def test_use_appears_only_when_an_effect_is_built_and_works_once_per_press(h, registry):
    items.register_item(id="tonic", name_fa="تونیک", category="food", stackable=True, max_stack=5, usable=True, effect="tonic_fx", icon="🧪")
    join(h, P1)
    give(h, P1, "tonic", 3)
    text, b = shown(press(h, P1, "o", "tonic", 1))
    assert not any(x[1].startswith("iv:x:") for x in b)                                              # declared usable, but nothing has built the effect: no button
    items.register_effect("tonic_fx", lambda db, uid, item, now: {"fa": "حالت بهتر شد.", "en": "Feeling better."})
    text, b = shown(press(h, P1, "o", "tonic", 1))
    use = next(x for x in b if x[1].startswith("iv:x:"))
    assert use[0] == t("fa", "iv.b.use") and use[1] == f"iv:x:{I.entry(h.db, P1, 'tonic').stack_id}:3:1:{P1}"
    out = h.press(P1, use[1])
    text, b = shown(out)
    assert t("fa", "iv.ok.use", item="تونیک") in text and "حالت بهتر شد." in text and I.count(h.db, P1, "tonic") == 2
    out = h.press(P1, use[1])                                                                        # the same button again: it was drawn with 3
    assert I.count(h.db, P1, "tonic") == 2 and toast(out) == "این صفحه قدیمی شده؛ دوباره از کوله‌پشتی باز کنش."
    sid = I.entry(h.db, P1, "tonic").stack_id
    h.press(P1, f"iv:x:{sid}:2:1:{P1}")
    out = h.press(P1, f"iv:x:{sid}:1:1:{P1}")
    assert I.count(h.db, P1, "tonic") == 0 and t("fa", "iv.ok.use", item="تونیک") in shown(out)[0] and t("fa", "iv.cat.none") in shown(out)[0]      # the last one: back to the (now empty) category


def test_an_effect_that_refuses_shows_its_error_and_uses_nothing(h, registry):
    from hypemarket.core import GameError
    items.register_item(id="tonic", name_fa="تونیک", category="food", usable=True, effect="tonic_fx")

    def refuse(db, uid, item, now):
        raise GameError("cash_low", need=500, have=1)

    items.register_effect("tonic_fx", refuse)
    join(h, P1)
    give(h, P1, "tonic", 1)
    out = h.press(P1, f"iv:x:{I.entry(h.db, P1, 'tonic').stack_id}:1:1:{P1}")
    assert I.count(h.db, P1, "tonic") == 1 and toast(out) and not of(out, "EditMessageText")


def test_the_starting_catalog_shows_no_use_button_anywhere(h):
    join(h, P1)
    for item in items.ITEMS:
        give(h, P1, item)
    for item in items.ITEMS:
        assert not any(x[1].startswith("iv:x:") for x in shown(press(h, P1, "o", item, 1))[1]), item


# ============================================================================================== language, digits, wording
def test_the_english_screens_are_english(h):
    join(h, P1, first="Armin", last="Kiani")                                                         # (a character's own name stays as its player wrote it)
    h.db.x("UPDATE users SET lang='en' WHERE id=?", (P1,))
    for item, q in (("tshirt_white", 1), ("soda_can", 30), ("cap_black", 1)):
        give(h, P1, item, q)
    h.press(P1, f"iv:e:{I.entry(h.db, P1, 'cap_black').stack_id}:1:{P1}")
    screens = [shown(open_inv(h, P1)), shown(press(h, P1, "c", "clothing", 1)), shown(press(h, P1, "o", "tshirt_white", 1)), shown(press(h, P1, "o", "soda_can", 1)),
               shown(press(h, P1, "q")), shown(press(h, P1, "s", "upper", 1)), shown(press(h, P1, "s", "headwear", 1)), shown(press(h, P1, "c", "personal", 1))]
    for text, b in screens:
        assert not re.search(r"[؀-ۿ]", text), text
        assert not any(re.search(r"[؀-ۿ]", label) for label, _ in b), b
    assert "Inventory" in screens[0][0] and "White T-shirt" in screens[2][0] and "Headwear: <b>Black cap</b>" in screens[4][0]


def test_with_english_digits_no_persian_digit_appears(h, monkeypatch):
    monkeypatch.setenv("DIGITS", "en")
    join(h, P1)
    for item, q in (("tshirt_white", 2), ("soda_can", 30)):
        give(h, P1, item, q)
    for text, b in (shown(open_inv(h, P1)), shown(press(h, P1, "c", "food", 1)), shown(press(h, P1, "o", "soda_can", 1)), shown(press(h, P1, "q"))):
        assert not re.search(r"[۰-۹٠-٩]", text) and not any(re.search(r"[۰-۹٠-٩]", label) for label, _ in b)
    assert "<b>32</b>" in shown(open_inv(h, P1))[0]


def test_the_wording_is_clean_persian_and_never_an_out_of_character_disclaimer():
    persian = [v for k, v in FA.items() if k.startswith(("iv.", "e.inv", "e.item", "e.equip", "menu.inventory", "cg.b.inv", "gift.n.item", "gift.ok.item", "e.gift_full"))]
    persian += [d.name_fa for d in items.ITEMS.values()] + [d.description_fa for d in items.ITEMS.values()] + [c.name_fa for c in items.CATEGORIES.values()] + [s.name_fa for s in items.SLOTS.values()]
    for s in persian:
        assert "ي" not in s and "ك" not in s and "  " not in s, s                                       # Arabic letters / double spaces
        assert not re.search(r"(?<!\w)می [آ-ی]", s), s                                                          # «می رود» instead of «می‌رود»
        assert not re.search(r"(?:فقط توی بازیه|in-game only|real identity|هویت واقعی)", s), s
    for s in (d.name_fa for d in items.ITEMS.values()):
        assert not s.startswith(" ") and not s.endswith(" ")


def test_every_text_exists_in_both_languages_and_every_placeholder_matches():
    keys = [k for k in FA if k.startswith(("iv.", "e.inv", "e.item", "e.equip", "gift.n.item", "gift.ok.item", "gift.pick", "e.gift_full", "gift.what.item", "gift.b.item", "gift.unit.item"))]
    assert keys
    for k in keys:
        assert k in EN, k
        assert set(re.findall(r"{(\w+)}", FA[k])) == set(re.findall(r"{(\w+)}", EN[k])), k


# ============================================================================================== sizes, pictures, groups
def test_every_button_fits_telegrams_limit_even_with_the_largest_numbers(h, registry):
    items.register_item(id="x" * 24, name_fa="چیز", category="misc")
    big = 9_999_999_999
    for uid in (big, 12345):
        for data in [invui._cb("m", uid=uid), invui._cb("q", uid=uid)] + [invui._cb("c", cid, 99999, uid=uid) for cid in items.CATEGORIES] + \
                    [invui._cb("o", i, 99999, uid=uid) for i in items.ITEMS] + [invui._cb("s", sid, 99999, uid=uid) for sid in items.SLOTS] + \
                    [invui._cb("e", 10 ** 12 - 1, 99999, uid=uid), invui._cb("u", "accessory", uid=uid), invui._cb("x", 10 ** 12 - 1, 9999, 99999, uid=uid)]:
            assert len(data.encode()) <= 64, data


def test_no_screen_is_a_picture_or_a_web_page(h, registry):
    many(registry, 8)
    join(h, P1)
    give(h, P1, "tshirt_white")
    give(h, P1, "extra_00")
    outs = [open_inv(h, P1), press(h, P1, "c", "clothing", 1), press(h, P1, "o", "tshirt_white", 1), press(h, P1, "q"), press(h, P1, "s", "upper", 1), press(h, P1, "m")]
    for out in outs:
        assert not of(out, "SendPhoto") and not of(out, "EditMessageMedia") and not of(out, "EditMessageCaption")
        text, b = shown(out)
        assert "http" not in text and all(data.startswith(("iv:", "cg:", "mn:")) for _, data in b)
        for c in out:
            kbd = getattr(c, "reply_markup", None)
            if kbd:
                assert all(getattr(btn, "url", None) is None for row in kbd.inline_keyboard for btn in row)


def test_every_button_of_every_screen_leads_somewhere(h):
    join(h, P1)
    for item in ("tshirt_white", "cap_black", "sneakers_white", "soda_can", "gift_box", "notebook", "old_key", "sunglasses_black"):
        give(h, P1, item, 2 if item == "soda_can" else 1)
    seen, queue = set(), [f"iv:m:{P1}"]
    while queue and len(seen) < 120:
        data = queue.pop(0)
        if data in seen or data.startswith(("cg:", "mn:")):
            continue
        seen.add(data)
        out = h.press(P1, data)
        text, b = shown(out)
        assert text and not toast(out), (data, text, toast(out))                                        # every button of every screen draws a screen (no dead button, no error)
        queue += [x[1] for x in b if x[1] not in seen]
    assert len(seen) >= 25


def test_in_a_group_the_inventory_is_a_message_whose_buttons_belong_to_the_opener(h):
    join(h, P1, chat=GROUP)
    join(h, P2, first="بابک", last="رضایی", chat=GROUP)
    give(h, P1, "tshirt_white")
    out = open_inv(h, P1, GROUP)
    assert of(out, "SendMessage") and "آرمان کیانی" in shown(out)[0]
    out = press(h, P1, "c", "clothing", 1, chat=GROUP)
    assert len(of(out, "EditMessageText")) == 1
    out = h.press(P2, f"iv:c:clothing:1:{P1}", chat=GROUP)
    assert toast(out) == t("fa", "not_yours") and not of(out, "EditMessageText")
    assert I.count(h.db, P2, "tshirt_white") == 0


# ============================================================================================== extensible
def test_a_registered_category_slot_and_item_appear_without_any_other_change(h, registry):
    items.register_category("tools", "ابزار", "Tools", "🔧")
    items.register_slot("belt", "کمربند", "Belt", "🥋")
    items.register_item(id="toolbelt", name_fa="کمربند ابزار", name_en="Tool belt", category="tools", equippable=True, slot="belt", icon="🥋", description_fa="جای ابزار.")
    join(h, P1)
    give(h, P1, "toolbelt")
    text, b = shown(open_inv(h, P1))
    assert "🔧 ابزار: <b>" in text and ("🔧 ابزار (" + fmt.num(1, "fa") + ")", f"iv:c:tools:1:{P1}") in b
    assert ("🥋 کمربند ابزار", f"iv:o:{I.entry(h.db, P1, 'toolbelt').stack_id}:1:{P1}") in shown(press(h, P1, "c", "tools", 1))[1]
    eq = shown(press(h, P1, "q"))
    assert "🥋 کمربند: <i>خالی</i>" in eq[0] and ("🥋 کمربند", f"iv:s:belt:1:{P1}") in eq[1]
    h.press(P1, f"iv:e:{I.entry(h.db, P1, 'toolbelt').stack_id}:1:{P1}")
    assert eq_state(h, P1) == {"belt": "toolbelt"} and "کمربند: <b>کمربند ابزار</b>" in shown(press(h, P1, "q"))[0]
    assert I.audit(h.db, P1) == []


def test_an_item_the_catalog_lost_is_still_listed_as_unknown(h):
    join(h, P1)
    h.db.x("INSERT INTO item_inventory(user_id,item_id,quantity,acquired_at,source) VALUES(?,?,?,?,?)", (P1, "retired_hat", 3, tb.T[0], "gift"))
    text, b = shown(press(h, P1, "c", "misc", 1))
    assert b[0][0] == f"❔ {items.UNKNOWN_NAME['fa']} ×{fmt.num(3, 'fa')}"
    text, b = shown(press(h, P1, "o", "retired_hat", 1))
    assert items.UNKNOWN_NAME["fa"] in text and [x[1] for x in b] == [f"iv:c:misc:1:{P1}"]
