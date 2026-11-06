"""v0.21: item gifts: an admin gift code can give an ITEM, which goes into the player's bag.

Covered: making an item code (the rules and the admin's walk through «ساخت گیفت کارت» with the item picker), redeeming it (the item lands in the bag with its record, once per player, within
the code's limit), a repeated message or a race never giving it twice, a full bag or an item that left the catalog using nothing up, that the other rewards and balances are untouched, and
the player's answers in both languages. The three older rewards have their own suite (test_v020_gifts.py), which is run unchanged. A real Telegram client was not used."""
import threading

import pytest

pytestmark = pytest.mark.usefixtures("legacy_catalog")      # v0.211 seeds no item: these suites bring the 17 old starter items themselves
from aiogram.methods import AnswerCallbackQuery, EditMessageText, SendMessage

import test_bot as tb
from hypemarket import adminpanel, cash, charxp, config as C, core, fmt, giftui, gifts, inventory as I, items
from hypemarket.core import GameError
from hypemarket.db import Database
from hypemarket.texts import EN, FA, t

ADMIN = 1
P1, P2, P3, P4 = 888001, 888002, 888003, 888004
NOW = 1_700_000_000
CODE_RE = r"HM-[A-HJ-NP-Z2-9]{4}-[A-HJ-NP-Z2-9]{4}-[A-HJ-NP-Z2-9]{4}"
ADMINS = frozenset({ADMIN})


@pytest.fixture()
def h():
    return tb.Harness()


@pytest.fixture()
def db():
    d = Database(":memory:")
    core.set_clock(lambda: NOW)
    for uid in (P1, P2, P3, P4, ADMIN):
        core.ensure_user(d, uid, None, f"U{uid}")
    return d


@pytest.fixture()
def registry():
    saved = (dict(items.CATEGORIES), dict(items.SLOTS), dict(items.ITEMS), dict(items.EFFECTS))
    yield items
    for reg, old in zip((items.CATEGORIES, items.SLOTS, items.ITEMS, items.EFFECTS), saved):
        reg.clear()
        reg.update(old)


def make(db, item="gift_box", qty=2, uses=3, by=ADMIN):
    return gifts.create(db, by, "item", gifts.to_units("item", qty), uses, ADMINS, item_id=item)


def count(db, table, where="1", args=()):
    return db.q1(f"SELECT COUNT(*) c FROM {table} WHERE {where}", args)["c"]


def books(db, uid):
    return ([tuple(r) for r in db.qa("SELECT * FROM item_inventory WHERE user_id=? ORDER BY id", (uid,))], [tuple(r) for r in db.qa("SELECT * FROM item_log WHERE user_id=? ORDER BY id", (uid,))])


# ============================================================================================== making a code
def test_an_item_code_stores_its_item_and_quantity(db):
    row = make(db, "tshirt_white", 3, 5)
    assert (row["reward_type"], row["amount"], row["item_id"], row["max_uses"], row["used_count"], row["created_by"]) == ("item", 3, "tshirt_white", 5, 0, ADMIN)
    assert gifts.get(db, row["code"])["item_id"] == "tshirt_white" and "item" in gifts.REWARDS
    assert gifts.limit("item") == C.GIFT_MAX_ITEM_QTY and gifts.to_units("item", 7) == 7 and gifts.from_units("item", 7) == 7


def test_the_other_rewards_still_have_no_item(db):
    for reward in ("stock_money", "life_cash", "char_xp"):
        assert gifts.create(db, ADMIN, reward, gifts.to_units(reward, 5), 1, ADMINS)["item_id"] is None


@pytest.mark.parametrize("kw", [dict(reward="item", item_id=None), dict(reward="item", item_id="no_such_item"), dict(reward="stock_money", item_id="sandwich"),
                                dict(reward="item", item_id="sandwich", amount=0), dict(reward="item", item_id="sandwich", amount=C.GIFT_MAX_ITEM_QTY + 1),
                                dict(reward="item", item_id="sandwich", amount=True), dict(reward="item", item_id="sandwich", uses=0)])
def test_a_bad_item_code_is_refused_and_nothing_is_stored(db, kw):
    kw = {"amount": 2, "uses": 1, **kw}
    with pytest.raises(ValueError):
        gifts.create(db, ADMIN, kw["reward"], kw["amount"], kw["uses"], ADMINS, item_id=kw["item_id"])
    assert count(db, "gift_codes") == 0


def test_only_an_admin_makes_an_item_code(db):
    with pytest.raises(GameError) as e:
        gifts.create(db, P1, "item", 1, 1, ADMINS, item_id="sandwich")
    assert e.value.code == "gift_denied" and count(db, "gift_codes") == 0


def test_a_codes_item_never_changes_and_the_code_is_kept(db):
    row = make(db)
    import sqlite3
    for sql in ("UPDATE gift_codes SET item_id='sandwich'", "UPDATE gift_codes SET amount=9", "UPDATE gift_codes SET reward_type='char_xp', item_id=NULL", "DELETE FROM gift_codes"):
        with pytest.raises(sqlite3.IntegrityError):
            db.x(sql)
    assert gifts.get(db, row["code"])["item_id"] == "gift_box"


# ============================================================================================== redeeming
def test_redeeming_puts_the_item_in_the_bag_with_its_record(db):
    row = make(db, "tshirt_white", 3)
    r = gifts.redeem(db, P1, row["code"], now=NOW + 9)
    assert r == {"reward": "item", "amount": 3, "code": row["code"], "item": "tshirt_white", "qty": 3, "data": None}
    assert I.count(db, P1, "tshirt_white") == 3 and I.stack_count(db, P1) == 3
    s = db.q1("SELECT * FROM item_inventory WHERE user_id=?", (P1,))
    assert (s["acquired_at"], s["source"]) == (NOW + 9, "gift")
    assert [(x["item_id"], x["delta"], x["source"], x["ref"]) for x in db.qa("SELECT * FROM item_log WHERE user_id=?", (P1,))] == [("tshirt_white", 3, "gift", row["code"])]
    assert gifts.get(db, row["code"])["used_count"] == 1 and count(db, "gift_redemptions", "user_id=?", (P1,)) == 1
    red = db.q1("SELECT * FROM gift_redemptions WHERE user_id=?", (P1,))
    assert (red["reward_type"], red["amount"]) == ("item", 3) and I.audit(db, P1) == []


def test_a_code_works_once_per_player_and_up_to_its_limit(db):
    row = make(db, "gift_box", 2, uses=2)
    gifts.redeem(db, P1, row["code"])
    with pytest.raises(GameError) as e:
        gifts.redeem(db, P1, row["code"])
    assert e.value.code == "gift_used" and I.count(db, P1, "gift_box") == 2
    gifts.redeem(db, P2, row["code"])
    with pytest.raises(GameError) as e:
        gifts.redeem(db, P3, row["code"])
    assert e.value.code == "gift_exhausted" and I.count(db, P3, "gift_box") == 0 and I.count(db, P2, "gift_box") == 2
    assert gifts.get(db, row["code"])["used_count"] == 2


def test_stackable_gifts_merge_with_what_the_player_already_has(db):
    I.give(db, P1, "gift_box", 3, "system", "start")
    row = make(db, "gift_box", 4)
    gifts.redeem(db, P1, row["code"])
    assert I.count(db, P1, "gift_box") == 7 and I.stack_count(db, P1) == 1
    big = make(db, "gift_box", 30, uses=1)                                                            # more than one stack (10 each)
    gifts.redeem(db, P2, big["code"])
    assert I.count(db, P2, "gift_box") == 30 and I.stack_count(db, P2) == 3


def test_a_typed_code_with_any_spacing_or_case_works(db):
    row = make(db)
    typed = row["code"].lower().replace("-", " ")
    assert gifts.redeem(db, P1, gifts.normalize(typed))["item"] == "gift_box"


def test_redeeming_touches_no_balance_and_no_other_reward(db):
    row = make(db, "sandwich", 2)
    stock, life = core.get_user(db, P1)["cash"], cash.balance(db, P1)
    xp = charxp.get(db, P1)
    gifts.redeem(db, P1, row["code"])
    assert (core.get_user(db, P1)["cash"], cash.balance(db, P1), charxp.get(db, P1)) == (stock, life, xp)


def test_a_player_without_a_finished_character_still_gets_the_item(db):
    row = make(db, "old_key", 1)
    assert core.get_user(db, P4) and gifts.redeem(db, P4, row["code"])["qty"] == 1 and I.count(db, P4, "old_key") == 1


def test_bots_and_strangers_get_nothing(db):
    row = make(db)
    db.x("UPDATE users SET bot=1 WHERE id=?", (P3,))
    for uid in (P3, 123_456_789):
        with pytest.raises(GameError) as e:
            gifts.redeem(db, uid, row["code"])
        assert e.value.code == "no_user"
    assert gifts.get(db, row["code"])["used_count"] == 0 and count(db, "item_log") == 0


# ============================================================================================== never twice
def test_a_repeated_message_gives_the_item_once(db):
    row = make(db, "gift_box", 2)
    gifts.redeem(db, P1, row["code"])
    for _ in range(4):
        with pytest.raises(GameError) as e:
            gifts.redeem(db, P1, row["code"])
        assert e.value.code == "gift_used"
    assert I.count(db, P1, "gift_box") == 2 and count(db, "item_log", "user_id=?", (P1,)) == 1 and count(db, "gift_redemptions") == 1


def test_a_ledger_row_without_a_redemption_still_never_gives_twice(db):
    """If the item ledger already holds this player's grant for the code (whatever way it got there), redeeming refuses and leaves everything as it was."""
    row = make(db, "gift_box", 2)
    I.give(db, P1, "gift_box", 2, "gift", row["code"])
    before = books(db, P1)
    with pytest.raises(GameError) as e:
        gifts.redeem(db, P1, row["code"])
    assert e.value.code == "gift_used" and books(db, P1) == before
    assert gifts.get(db, row["code"])["used_count"] == 0 and count(db, "gift_redemptions") == 0          # not even a use was spent


def test_the_same_player_racing_with_himself_gets_it_once(tmp_path):
    path = str(tmp_path / "self.db")
    boot = Database(path)
    core.set_clock(lambda: NOW)
    core.ensure_user(boot, P1, None, "Me")
    code = make(boot, "tshirt_white", 2, uses=5)["code"]
    barrier, out, lock = threading.Barrier(8), [], threading.Lock()

    def worker():
        d = Database(path)
        barrier.wait()
        try:
            gifts.redeem(d, P1, code)
            res = "ok"
        except GameError as e:
            res = e.code
        with lock:
            out.append(res)

    ts = [threading.Thread(target=worker) for _ in range(8)]
    [t_.start() for t_ in ts]
    [t_.join() for t_ in ts]
    assert out.count("ok") == 1 and out.count("gift_used") == 7
    chk = Database(path)
    assert I.count(chk, P1, "tshirt_white") == 2 and gifts.get(chk, code)["used_count"] == 1 and I.audit(chk, P1) == []


def test_players_racing_for_the_last_place_of_an_item_code(tmp_path):
    path = str(tmp_path / "race.db")
    boot = Database(path)
    core.set_clock(lambda: NOW)
    for i in range(1, 9):
        core.ensure_user(boot, 900 + i, None, f"R{i}")
    code = make(boot, "gift_box", 1, uses=1)["code"]
    barrier, out, lock = threading.Barrier(8), [], threading.Lock()

    def worker(uid):
        d = Database(path)
        barrier.wait()
        try:
            gifts.redeem(d, uid, code)
            res = "ok"
        except GameError as e:
            res = e.code
        with lock:
            out.append(res)

    ts = [threading.Thread(target=worker, args=(900 + i,)) for i in range(1, 9)]
    [t_.start() for t_ in ts]
    [t_.join() for t_ in ts]
    assert out.count("ok") == 1 and all(r in ("ok", "gift_exhausted") for r in out)
    chk = Database(path)
    assert sum(I.count(chk, 900 + i, "gift_box") for i in range(1, 9)) == 1 and gifts.get(chk, code)["used_count"] == 1


# ============================================================================================== when it cannot be given
def test_a_full_bag_refuses_the_code_and_uses_nothing_up(db, monkeypatch):
    monkeypatch.setattr(C, "INV_MAX_STACKS", 2)
    I.give(db, P1, "old_key", 1, "gift", "a")
    I.give(db, P1, "umbrella", 1, "gift", "b")
    row = make(db, "tshirt_white", 1)
    before = books(db, P1)
    with pytest.raises(GameError) as e:
        gifts.redeem(db, P1, row["code"])
    assert e.value.code == "gift_full"
    assert books(db, P1) == before and gifts.get(db, row["code"])["used_count"] == 0 and count(db, "gift_redemptions") == 0
    I.take(db, P1, "umbrella", 1, "use", "free-a-place")
    assert gifts.redeem(db, P1, row["code"])["qty"] == 1 and I.count(db, P1, "tshirt_white") == 1 and gifts.get(db, row["code"])["used_count"] == 1      # the code was still there


def test_a_gift_that_only_partly_fits_gives_none_of_it(db, monkeypatch):
    monkeypatch.setattr(C, "INV_MAX_STACKS", 3)
    row = make(db, "tshirt_white", 4, uses=1)                                                         # four single shirts need four places
    with pytest.raises(GameError) as e:
        gifts.redeem(db, P1, row["code"])
    assert e.value.code == "gift_full" and I.total(db, P1) == 0 and count(db, "item_log") == 0 and gifts.get(db, row["code"])["used_count"] == 0


def test_an_item_that_left_the_catalog_cannot_be_given_and_costs_nothing(db, registry):
    items.register_item(id="short_lived", name_fa="زودگذر", category="misc")
    row = make(db, "short_lived", 1)
    del items.ITEMS["short_lived"]
    with pytest.raises(GameError) as e:
        gifts.redeem(db, P1, row["code"])
    assert e.value.code == "gift_unavailable" and gifts.get(db, row["code"])["used_count"] == 0 and count(db, "item_log") == 0


# ============================================================================================== words
def test_the_reward_is_named_in_words_in_both_languages():
    assert giftui.what("fa", "item", 3, "tshirt_white") == "3 عدد تی‌شرت سفید".replace("3", fmt.num(3, "fa"))
    assert giftui.what("en", "item", 3, "tshirt_white") == "3 × White T-shirt"
    assert giftui.amount_text("fa", "item", 1, None) .endswith("?") is True                           # (a row with no item never happens; it must not crash)
    assert t("fa", "gift.ok.item", n="x") != FA["gift.ok.item"] and "کوله‌پشتی" in FA["gift.ok.item"] and "inventory" in EN["gift.ok.item"]
    assert "e.gift_full" in FA and "e.gift_full" in EN


# ============================================================================================== the admin's walk, through the real handlers
def panel(calls):
    return [c for c in calls if isinstance(c, (EditMessageText, SendMessage))][-1]


def cbs(msg):
    return [b.callback_data for row in msg.reply_markup.inline_keyboard for b in row] if getattr(msg, "reply_markup", None) else []


def labels(msg):
    return [b.text for row in msg.reply_markup.inline_keyboard for b in row] if getattr(msg, "reply_markup", None) else []


def toasts(calls):
    return [c.text for c in calls if isinstance(c, AnswerCallbackQuery) and c.text]


def code_of(msg):
    import re
    m = re.search(CODE_RE, msg.text)
    assert m, msg.text
    return m.group(0)


def template(db, item_id):
    """The admin's catalog is empty by default; a test that wants to gift one of the old starter items makes it pickable the way migration 14 does (a `legacy` template). Returns the template id."""
    from hypemarket import legacy_items
    r = db.q1("SELECT id FROM item_templates WHERE item_id=?", (item_id,))
    if r:
        return r["id"]
    (iid, fa, en, cat, slot, mx, icon, dfa, den) = next(x for x in legacy_items.LEGACY if x[0] == item_id)
    return db.x("INSERT INTO item_templates(item_id,kind,name_fa,name_en,category,description_fa,description_en,slot,max_stack,icon,has_quality,brandable,created_by,created_at) "
                "VALUES(?,?,?,?,?,?,?,?,?,?,0,0,NULL,?)", (iid, "legacy", fa, en, cat, dfa, den, slot, mx, icon, NOW)).lastrowid


def walk(h, item="gift_box", qty="2", uses="3", confirm=True):
    tid = template(h.db, item)
    h.say(ADMIN, "/start")
    h.press(ADMIN, f"ad:gift:{ADMIN}")
    h.press(ADMIN, f"ad:gt:item:{ADMIN}")
    h.press(ADMIN, f"ad:gc:{items.category_of(item)}:{ADMIN}")
    h.press(ADMIN, f"ad:gp:{tid}:{ADMIN}")
    h.say(ADMIN, qty)
    m = panel(h.say(ADMIN, uses))
    if not confirm:
        return m
    return panel(h.press(ADMIN, next(c for c in cbs(m) if c.startswith("ad:gok:"))))


def test_the_admin_picks_an_item_a_quantity_and_a_number_of_players(h):
    tid = template(h.db, "tshirt_white")
    h.say(ADMIN, "/start")
    t1 = panel(h.press(ADMIN, f"ad:gift:{ADMIN}"))
    assert t("fa", "gift.b.item") in labels(t1) and f"ad:gt:item:{ADMIN}" in cbs(t1)
    cats = panel(h.press(ADMIN, f"ad:gt:item:{ADMIN}"))
    assert [c for c in cbs(cats) if c.startswith("ad:gc:")] == [f"ad:gc:{cid}:{ADMIN}" for cid in items.CATEGORIES] and cbs(cats)[-1] == f"ad:gx:{ADMIN}"
    pick = panel(h.press(ADMIN, f"ad:gc:clothing:{ADMIN}"))
    assert [c for c in cbs(pick) if c.startswith("ad:gp:")] == [f"ad:gp:{tid}:{ADMIN}"] and f"ad:gn:{ADMIN}" in cbs(pick)       # only this category's templates
    assert count(h.db, "gift_codes") == 0 and ADMIN not in h.app.pending                              # nothing is armed until an item is chosen
    ask = panel(h.press(ADMIN, f"ad:gp:{tid}:{ADMIN}"))
    assert "تی‌شرت سفید" in ask.text and fmt.num(C.GIFT_MAX_ITEM_QTY, "fa") in ask.text and h.app.pending[ADMIN][0] == adminpanel.GIFT_AMOUNT
    uses = panel(h.say(ADMIN, "3"))
    assert f"{fmt.num(3, 'fa')} عدد تی‌شرت سفید" in uses.text and h.app.pending[ADMIN][0] == adminpanel.GIFT_USES
    conf = panel(h.say(ADMIN, "2"))
    assert f"{fmt.num(3, 'fa')} عدد تی‌شرت سفید" in conf.text and f"{fmt.num(6, 'fa')} عدد تی‌شرت سفید" in conf.text and fmt.num(2, "fa") in conf.text
    assert count(h.db, "gift_codes") == 0                                                              # nothing is stored before the confirmation


def test_the_made_code_gives_the_item_to_players_who_type_it(h):
    made = walk(h, "tshirt_white", "2", "2")
    code = code_of(made)
    assert f"{fmt.num(2, 'fa')} عدد تی‌شرت سفید" in made.text
    row = gifts.get(h.db, code)
    assert (row["reward_type"], row["amount"], row["item_id"], row["max_uses"]) == ("item", 2, "tshirt_white", 2)
    out = h.say(P1, f"کد هدیه {code}")
    assert t("fa", "gift.ok.item", n=f"{fmt.num(2, 'fa')} عدد تی‌شرت سفید") in tb.Harness.texts(out) and code not in tb.Harness.texts(out)
    assert I.count(h.db, P1, "tshirt_white") == 2
    again = tb.Harness.texts(h.say(P1, f"کد هدیه {code}"))
    assert FA["e.gift_used"] in again and I.count(h.db, P1, "tshirt_white") == 2                       # a second message: told, and nothing more is given
    inv = tb.Harness.texts(h.say(P1, "کوله‌پشتی"))
    assert "کوله‌پشتی" in inv and f"پوشاک: <b>{fmt.num(2, 'fa')}</b>" in inv                              # and it is in the inventory screen


def test_the_english_walk_and_answer(h):
    h.say(ADMIN, "/start")
    h.say(P1, "/start")                                                                                # the player has to exist before the language can be changed
    h.db.x("UPDATE users SET lang='en' WHERE id IN (?, ?)", (ADMIN, P1))
    made = walk(h, "soda_can", "5", "1")
    assert "5 × Soda" in made.text
    out = tb.Harness.texts(h.say(P1, f"gift code {code_of(made)}"))
    assert t("en", "gift.ok.item", n="5 × Soda") in out and "Open «inventory»" in out and I.count(h.db, P1, "soda_can") == 5


def test_a_full_bag_is_told_with_its_own_message(h, monkeypatch):
    made = walk(h, "tshirt_white", "2", "1")
    h.say(P1, "/start")
    monkeypatch.setattr(C, "INV_MAX_STACKS", 1)
    I.give(h.db, P1, "old_key", 1, "gift", "x")
    out = tb.Harness.texts(h.say(P1, f"کد هدیه {code_of(made)}"))
    assert FA["e.gift_full"] in out and I.count(h.db, P1, "tshirt_white") == 0
    assert gifts.get(h.db, code_of(made))["used_count"] == 0


def test_the_template_picker_pages_through_a_whole_category(h):
    from hypemarket import fashion, itemtpl
    ids = [template(h.db, i) for i in ("tshirt_white", "hoodie_green", "jeans_blue", "chinos_beige", "cap_black")]
    for pid in fashion.product_ids("clothing"):
        ids.append(itemtpl.create_product(h.db, ADMIN, pid)["id"])
    assert len(ids) > C.INV_PAGE_SIZE
    h.say(ADMIN, "/start")
    h.press(ADMIN, f"ad:gift:{ADMIN}")
    h.press(ADMIN, f"ad:gt:item:{ADMIN}")
    pick = panel(h.press(ADMIN, f"ad:gc:clothing:{ADMIN}"))
    seen, page = [], 1
    while True:
        seen += [int(c.split(":")[2]) for c in cbs(pick) if c.startswith("ad:gp:")]
        nxt = f"ad:gi:{page + 1}:{ADMIN}"
        if nxt not in cbs(pick):
            break
        page += 1
        pick = panel(h.press(ADMIN, nxt))
    assert seen == ids and len(seen) == len(set(seen))
    assert all(len(c.encode()) <= 64 for c in cbs(pick))
    back = panel(h.press(ADMIN, f"ad:gi:{page - 1}:{ADMIN}"))
    assert f"ad:gi:{page}:{ADMIN}" in cbs(back)


def test_cancel_at_the_picker_or_after_it_leaves_nothing(h):
    tid = template(h.db, "sandwich")
    for step in ("pick", "amount"):
        h.say(ADMIN, "/start")
        h.press(ADMIN, f"ad:gift:{ADMIN}")
        h.press(ADMIN, f"ad:gt:item:{ADMIN}")
        h.press(ADMIN, f"ad:gc:food:{ADMIN}")
        if step == "amount":
            h.press(ADMIN, f"ad:gp:{tid}:{ADMIN}")
        out = h.press(ADMIN, f"ad:gx:{ADMIN}")
        assert FA["gift.cancelled"] in panel(out).text
        assert count(h.db, "gift_codes") == 0 and ADMIN not in h.app.pending and ADMIN not in h.app.gift_drafts
        assert h.say(ADMIN, "3") == []
    stale = h.press(ADMIN, f"ad:gp:{tid}:{ADMIN}")                                                     # a picker button of the cancelled draft
    assert FA["gift.stale"] in panel(stale).text and count(h.db, "gift_codes") == 0


def test_a_bad_quantity_asks_again_and_a_bad_pick_does_nothing(h):
    tid = template(h.db, "sandwich")
    h.say(ADMIN, "/start")
    h.press(ADMIN, f"ad:gift:{ADMIN}")
    h.press(ADMIN, f"ad:gt:item:{ADMIN}")
    h.press(ADMIN, f"ad:gc:food:{ADMIN}")
    for bad in ("not_an_item", "99999", str(tid + 500)):
        unknown = h.press(ADMIN, f"ad:gp:{bad}:{ADMIN}")
        assert toasts(unknown) == [FA["e.bad_usage"]] and ADMIN in h.app.gift_drafts and h.app.gift_drafts[ADMIN]["item"] is None, bad
    other = template(h.db, "tshirt_white")                                                              # a template of another category cannot be picked from this list
    assert toasts(h.press(ADMIN, f"ad:gp:{other}:{ADMIN}")) == [FA["e.bad_usage"]] and h.app.gift_drafts[ADMIN]["item"] is None
    h.press(ADMIN, f"ad:gp:{tid}:{ADMIN}")
    for bad in ("0", "-1", "2.5", "100", "abc", "1e1"):
        again = panel(h.say(ADMIN, bad))
        assert FA["gift.bad_amount"].format(max=fmt.num(C.GIFT_MAX_ITEM_QTY, "fa")) in again.text and h.app.pending[ADMIN][0] == adminpanel.GIFT_AMOUNT
    assert count(h.db, "gift_codes") == 0
    assert FA["gift.ask_uses"].split("{")[0] in panel(h.say(ADMIN, "99")).text


def test_a_player_cannot_use_the_admins_item_buttons(h):
    h.say(P1, "/start")
    for data in (f"ad:gt:item:{P1}", f"ad:gc:food:{P1}", f"ad:gp:1:{P1}", f"ad:gi:2:{P1}", f"ad:gn:{P1}", f"ad:gq:3:{P1}", f"ad:gr:nike:{P1}"):
        out = h.press(P1, data)
        assert toasts(out) == [FA["ad.denied"]] and count(h.db, "gift_codes") == 0, data
    out = h.press(P1, f"ad:gp:1:{ADMIN}")                                                            # somebody else's button
    assert toasts(out) == [FA["not_yours"]]


def test_the_picker_needs_a_draft_for_an_item_code(h):
    h.say(ADMIN, "/start")
    for data in (f"ad:gi:1:{ADMIN}", f"ad:gp:1:{ADMIN}", f"ad:gc:food:{ADMIN}", f"ad:gq:3:{ADMIN}", f"ad:gr:nike:{ADMIN}"):
        assert FA["gift.stale"] in panel(h.press(ADMIN, data)).text
    h.press(ADMIN, f"ad:gift:{ADMIN}")
    h.press(ADMIN, f"ad:gt:stock_money:{ADMIN}")                                                      # a coin draft has no picker
    assert FA["gift.stale"] in panel(h.press(ADMIN, f"ad:gp:1:{ADMIN}")).text and h.app.gift_drafts[ADMIN]["item"] is None
    assert FA["gift.stale"] in panel(h.press(ADMIN, f"ad:gc:food:{ADMIN}")).text


def test_a_double_tap_on_confirm_makes_one_item_code(h):
    m = walk(h, "gift_box", "1", "1", confirm=False)
    ok = next(c for c in cbs(m) if c.startswith("ad:gok:"))
    assert code_of(panel(h.press(ADMIN, ok)))
    assert FA["gift.stale"] in panel(h.press(ADMIN, ok)).text and count(h.db, "gift_codes") == 1


def test_the_code_list_names_the_item(h):
    walk(h, "umbrella", "2", "4")
    h.press(ADMIN, f"ad:gift:{ADMIN}")
    lst = panel(h.press(ADMIN, f"ad:gl:{ADMIN}"))
    assert f"{fmt.num(2, 'fa')} عدد چتر" in lst.text and f"0/4".replace("0", fmt.num(0, "fa")).replace("4", fmt.num(4, "fa")) in lst.text


def test_the_older_rewards_still_walk_the_same_way(h):
    h.say(ADMIN, "/start")
    h.press(ADMIN, f"ad:gift:{ADMIN}")
    out = h.press(ADMIN, f"ad:gt:life_cash:{ADMIN}")
    assert FA["gift.ask_amount"].split("{")[0] in panel(out).text and h.app.pending[ADMIN][0] == adminpanel.GIFT_AMOUNT and not any(c.startswith("ad:gp:") for c in cbs(panel(out)))
    h.say(ADMIN, "25")
    made = panel(h.press(ADMIN, next(c for c in cbs(panel(h.say(ADMIN, "2"))) if c.startswith("ad:gok:"))))
    row = gifts.get(h.db, code_of(made))
    assert (row["reward_type"], row["amount"], row["item_id"]) == ("life_cash", 2500, None)
