"""v0.18 / v0.18.1: player-to-player interactions: instant greetings and consent-based hug requests, reached through glass buttons only
(map -> «اطراف من» -> a player -> «سلام» / «بغل»; there are no typed «سلام کن» / «بغل کن» commands since v0.18.1).

Rules under test: a greeting respects a cooldown; a hug is a REQUEST that only the other player can accept or decline (only the
sender can take it back), expires, needs the two players to be near each other, cannot be used to pester, is idempotent under repeated / late / forged
presses, and nothing about it touches money, stocks or any other part of the game."""
import copy

import pytest

import test_bot as tb
import mapkit
from hypemarket import config as C, core, docs, interact, presence, texts, world, worldmap, worldplaces
from hypemarket.core import GameError
from hypemarket.db import Database
from aiogram.methods import AnswerCallbackQuery, EditMessageText, SendMessage

CH, CH2 = -100808, -100809
A, B, X, Y = 61001, 61002, 61003, 61004
T0 = 1_700_000_000


@pytest.fixture(scope="module")
def layout():
    return world.build(7)


class Env:
    def __init__(self, layout):
        self.now = [T0]
        core.set_clock(lambda: self.now[0])
        worldmap.cache_clear()
        worldplaces.cache_clear()
        self.db = Database(":memory:")
        for u in (A, B, X, Y):
            core.ensure_user(self.db, u, f"u{u}", f"User{u}", lang="fa")
            self.db.x("INSERT OR IGNORE INTO group_members(group_id,user_id,last_seen) VALUES(?,?,?)", (CH, u, T0))
        w, _ = world.save_world(self.db, CH, copy.deepcopy(layout))
        self.city = worldplaces.load_city(self.db, w)

    def adv(self, s):
        self.now[0] += s

    def go_to(self, uid, ref):
        out = presence.start_trip(self.db, self.city, uid, ref, self.now[0])
        assert out.status in ("started", "here")
        tr = presence.walking_trip(self.db, self.city.id, uid)
        if tr is not None:
            self.now[0] = max(self.now[0], tr["arrive_at"])
        return presence.stand(self.db, self.city, uid, self.now[0])

    def meet(self, *uids, at=None):
        ref = at or self.city.mcd_id
        for u in uids:
            self.go_to(u, ref)

    def far_place(self, from_ref):
        spot = self.city.spot(from_ref)
        field = self.city.net.field(spot.door.pt)
        far = max((s for s in self.city.spots("landmark") if s.id != from_ref), key=lambda s: field.to(s.door))
        assert field.to(far.door) > C.nearby_radius() + 50, "the test city has no place far enough from the McDonald's"
        return far.id

    def pending(self):
        return [dict(r) for r in self.db.qa("SELECT * FROM interactions WHERE kind='hug' AND state='pending'")]

    def rows(self, kind=None):
        q = "SELECT * FROM interactions" + (" WHERE kind=?" if kind else "") + " ORDER BY id"
        return [dict(r) for r in self.db.qa(q, (kind,) if kind else ())]


@pytest.fixture()
def E(layout):
    return Env(layout)


# ================================================================================================ who is the target
def test_the_target_is_a_player_picked_from_the_nearby_list(E):
    assert interact.target(E.db, A, B)["id"] == B
    assert interact.target(E.db, B, A)["id"] == A


@pytest.mark.parametrize("to,code", [(None, "ia_unknown"), ("61002", "ia_unknown"), (True, "ia_unknown"), (1.5, "ia_unknown"), (999_999, "ia_unknown"), (A, "ia_self"), (-1, "ia_unknown")])
def test_a_missing_or_unusable_target_is_refused_with_a_reason(E, to, code):
    with pytest.raises(GameError) as e:
        interact.target(E.db, A, to)
    assert e.value.code == code


def test_nobody_can_target_themselves_a_stranger_a_bot_or_a_banned_player(E):
    E.db.x("INSERT INTO users(id,name,created_at,bot,cash) VALUES(-9,'robot',1,1,100)")
    with pytest.raises(GameError) as e:
        interact.target(E.db, A, -9)
    assert e.value.code == "ia_unknown"
    E.db.x("UPDATE users SET banned=1 WHERE id=?", (Y,))
    with pytest.raises(GameError) as e:
        interact.target(E.db, A, Y)
    assert e.value.code == "ia_unknown"


def test_the_typed_target_helpers_of_v018_are_gone():
    assert not hasattr(interact, "resolve_target")                                                  # nothing is typed any more: players are picked from the map's list


# ================================================================================================ greetings
def test_a_greeting_is_instant_recorded_and_rate_limited(E):
    iid = interact.greet(E.db, CH, A, B, E.now[0])
    row = E.rows("greet")[0]
    assert row["id"] == iid and (row["from_id"], row["to_id"], row["chat_id"], row["state"]) == (A, B, CH, "sent") and row["expires_at"] is None
    for left in (C.GREET_COOLDOWN, 7):
        E.adv(C.GREET_COOLDOWN - left if left == 7 else 0)
        with pytest.raises(GameError) as e:
            interact.greet(E.db, CH, A, X, E.now[0])
        assert e.value.code == "ia_cooldown" and 0 < e.value.kw["secs"] <= C.GREET_COOLDOWN
    E.adv(C.GREET_COOLDOWN)
    assert interact.greet(E.db, CH, A, X, E.now[0]) > iid
    assert interact.greet(E.db, CH, B, A, E.now[0])                                               # somebody else's cooldown is their own
    assert len(E.rows("greet")) == 3


def test_a_greeting_needs_no_answer_and_does_not_block_a_hug(E):
    interact.greet(E.db, CH, A, B, E.now[0])
    E.meet(A, B)
    assert interact.hug_request(E.db, E.city, CH, A, B, E.now[0])["state"] == "pending"           # greeting and hugging have separate cooldowns


def test_the_greeting_cooldown_survives_a_restart(tmp_path, layout):
    path = str(tmp_path / "g.db")
    core.set_clock(lambda: T0)
    d = Database(path)
    core.ensure_user(d, A, "a", "A", lang="fa")
    interact.greet(d, CH, A, B, T0)
    d.close()
    d2 = Database(path)
    with pytest.raises(GameError) as e:
        interact.greet(d2, CH, A, B, T0 + 5)
    assert e.value.code == "ia_cooldown" and e.value.kw["secs"] == C.GREET_COOLDOWN - 5


# ================================================================================================ hug requests
def test_a_hug_request_is_pending_with_an_expiry_and_changes_nothing_else(E):
    E.meet(A, B)
    before = ([tuple(r) for r in E.db.qa("SELECT * FROM users ORDER BY id")], [tuple(r) for r in E.db.qa("SELECT * FROM life_accounts ORDER BY user_id")],
              [tuple(r) for r in E.db.qa("SELECT * FROM life_ledger")], [tuple(r) for r in E.db.qa("SELECT * FROM holdings")])
    row = interact.hug_request(E.db, E.city, CH, A, B, E.now[0])
    assert (row["kind"], row["state"], row["from_id"], row["to_id"], row["chat_id"]) == ("hug", "pending", A, B, CH)
    assert (row["created_at"], row["expires_at"]) == (E.now[0], E.now[0] + C.HUG_TTL) and row["answered_at"] is None
    after, _ = interact.hug_answer(E.db, E.city, CH, row["id"], B, "y", E.now[0])
    assert after["state"] == "accepted" and after["answered_at"] == E.now[0]
    assert before == ([tuple(r) for r in E.db.qa("SELECT * FROM users ORDER BY id")], [tuple(r) for r in E.db.qa("SELECT * FROM life_accounts ORDER BY user_id")],
                      [tuple(r) for r in E.db.qa("SELECT * FROM life_ledger")], [tuple(r) for r in E.db.qa("SELECT * FROM holdings")])       # no coin, dollar or share moved


def test_only_the_asked_player_can_accept_or_decline_and_only_the_asker_can_take_it_back(E):
    E.meet(A, B, X)
    row = interact.hug_request(E.db, E.city, CH, A, B, E.now[0])
    for who, act in ((A, "y"), (A, "n"), (X, "y"), (X, "n"), (X, "c"), (B, "c")):
        with pytest.raises(GameError) as e:
            interact.hug_answer(E.db, E.city, CH, row["id"], who, act, E.now[0])
        assert e.value.code == "ia_notyours", (who, act)
    assert interact.get(E.db, row["id"])["state"] == "pending" and interact.get(E.db, row["id"])["answered_at"] is None


def test_accepting_declining_and_cancelling_each_work_once(E):
    E.meet(A, B)
    for act, who, state in (("y", B, "accepted"), ("n", B, "declined"), ("c", A, "cancelled")):
        E.adv(C.HUG_COOLDOWN + interact.PAIR_PAUSE)
        row = interact.hug_request(E.db, E.city, CH, A, B, E.now[0])
        done, changed = interact.hug_answer(E.db, E.city, CH, row["id"], who, act, E.now[0])
        assert changed is True and done["state"] == state
        for again in ("y", "n"):                                                                    # a repeated press (either button) changes nothing
            who2 = B
            same, changed2 = interact.hug_answer(E.db, E.city, CH, row["id"], who2, again, E.now[0] + 1)
            assert changed2 is False and same["state"] == state and same["answered_at"] == done["answered_at"]
        same, changed2 = interact.hug_answer(E.db, E.city, CH, row["id"], A, "c", E.now[0] + 1)
        assert changed2 is False and same["state"] == state


def test_a_request_that_nobody_answered_expires_by_the_clock(E):
    E.meet(A, B)
    row = interact.hug_request(E.db, E.city, CH, A, B, E.now[0])
    E.adv(C.HUG_TTL - 1)
    assert interact.hug_answer(E.db, E.city, CH, row["id"], B, "y", E.now[0])[0]["state"] == "accepted"      # one second before the end it still works
    E.adv(C.HUG_COOLDOWN + 1)
    row2 = interact.hug_request(E.db, E.city, CH, A, B, E.now[0])
    E.adv(C.HUG_TTL)
    late, changed = interact.hug_answer(E.db, E.city, CH, row2["id"], B, "y", E.now[0])               # the same second it runs out: too late
    assert changed is False and late["state"] == "expired" and late["answered_at"] == E.now[0]
    again, changed = interact.hug_answer(E.db, E.city, CH, row2["id"], B, "y", E.now[0] + 50)
    assert changed is False and again["state"] == "expired"
    E.adv(C.HUG_COOLDOWN)
    assert interact.hug_request(E.db, E.city, CH, A, B, E.now[0])["state"] == "pending"               # an expired request no longer blocks a new one


def test_one_open_request_per_sender_and_a_cooldown_between_requests(E):
    E.meet(A, B, X)
    first = interact.hug_request(E.db, E.city, CH, A, B, E.now[0])
    with pytest.raises(GameError) as e:
        interact.hug_request(E.db, E.city, CH, A, X, E.now[0])
    assert e.value.code in ("ia_cooldown", "ia_pending")
    E.adv(C.HUG_COOLDOWN)
    with pytest.raises(GameError) as e:
        interact.hug_request(E.db, E.city, CH, A, X, E.now[0])
    assert e.value.code == "ia_pending"                                                               # still waiting for B
    interact.hug_answer(E.db, E.city, CH, first["id"], A, "c", E.now[0])
    assert interact.hug_request(E.db, E.city, CH, A, X, E.now[0])["to_id"] == X
    assert len(E.pending()) == 1


def test_the_cooldown_is_counted_from_the_last_request_whatever_happened_to_it(E):
    E.meet(A, B)
    row = interact.hug_request(E.db, E.city, CH, A, B, E.now[0])
    interact.hug_answer(E.db, E.city, CH, row["id"], A, "c", E.now[0])
    with pytest.raises(GameError) as e:
        interact.hug_request(E.db, E.city, CH, A, B, E.now[0] + C.HUG_COOLDOWN - 1)
    assert e.value.code == "ia_cooldown" and e.value.kw["secs"] == 1


def test_a_declined_player_is_left_alone_for_a_while(E):
    E.meet(A, B)
    row = interact.hug_request(E.db, E.city, CH, A, B, E.now[0])
    interact.hug_answer(E.db, E.city, CH, row["id"], B, "n", E.now[0])
    E.adv(C.HUG_COOLDOWN)
    with pytest.raises(GameError) as e:
        interact.hug_request(E.db, E.city, CH, A, B, E.now[0])
    assert e.value.code == "ia_declined"
    E.meet(X)
    assert interact.hug_request(E.db, E.city, CH, A, X, E.now[0])["to_id"] == X                      # but A may ask somebody else
    E.adv(interact.PAIR_PAUSE)
    interact.hug_answer(E.db, E.city, CH, E.pending()[0]["id"], A, "c", E.now[0])
    E.adv(C.HUG_COOLDOWN)
    assert interact.hug_request(E.db, E.city, CH, A, B, E.now[0])["to_id"] == B


def test_one_player_cannot_be_flooded_with_requests(E):
    E.meet(A, B, X, Y)
    got = []
    for who in (A, X, Y):
        got.append(interact.hug_request(E.db, E.city, CH, who, B, E.now[0])) if len(got) < interact.MAX_INCOMING else None
    assert len(E.pending()) == 3
    E.db.x("INSERT INTO users(id,name,created_at,cash) VALUES(61005,'Z',1,100000)")
    E.meet(61005)
    with pytest.raises(GameError) as e:
        interact.hug_request(E.db, E.city, CH, 61005, B, E.now[0])
    assert e.value.code == "ia_busy" and len(E.pending()) == 3


def test_a_hug_needs_the_two_players_to_be_near_each_other(E):
    E.meet(A)
    E.go_to(B, E.far_place(E.city.mcd_id))
    assert interact.near(E.db, E.city, A, B, E.now[0]) is False
    with pytest.raises(GameError) as e:
        interact.hug_request(E.db, E.city, CH, A, B, E.now[0])
    assert e.value.code == "ia_far" and E.pending() == [] and E.rows("hug") == []
    E.go_to(B, E.city.mcd_id)
    assert interact.near(E.db, E.city, A, B, E.now[0]) is True
    assert interact.hug_request(E.db, E.city, CH, A, B, E.now[0])["state"] == "pending"


def test_accepting_checks_nearness_again_and_leaves_the_request_open(E):
    E.meet(A, B)
    row = interact.hug_request(E.db, E.city, CH, A, B, E.now[0])
    E.go_to(B, E.far_place(E.city.mcd_id))
    with pytest.raises(GameError) as e:
        interact.hug_answer(E.db, E.city, CH, row["id"], B, "y", E.now[0])
    assert e.value.code == "ia_far" and interact.get(E.db, row["id"])["state"] == "pending"
    assert interact.hug_answer(E.db, E.city, CH, row["id"], B, "n", E.now[0])[0]["state"] == "declined"      # saying no never needs to be near


def test_nearness_can_be_switched_off_by_configuration(E, monkeypatch):
    E.meet(A)
    E.go_to(B, E.far_place(E.city.mcd_id))
    monkeypatch.setenv("HUG_NEED_NEAR", "0")
    assert C.hug_need_near() is False
    assert interact.hug_request(E.db, E.city, CH, A, B, E.now[0])["state"] == "pending"
    monkeypatch.setenv("HUG_NEED_NEAR", "1")
    assert C.hug_need_near() is True


def test_a_request_belongs_to_the_chat_it_was_made_in(E):
    E.meet(A, B)
    row = interact.hug_request(E.db, E.city, CH, A, B, E.now[0])
    with pytest.raises(GameError) as e:
        interact.hug_answer(E.db, E.city, CH2, row["id"], B, "y", E.now[0])
    assert e.value.code == "ia_gone" and interact.get(E.db, row["id"])["state"] == "pending"


@pytest.mark.parametrize("iid", [0, -1, 99_999, None, "1", 1.0, True])
def test_hand_made_request_ids_find_nothing(E, iid):
    with pytest.raises(GameError) as e:
        interact.hug_answer(E.db, E.city, CH, iid, B, "y", E.now[0])
    assert e.value.code == "ia_gone"


def test_a_greeting_row_cannot_be_answered_as_a_hug(E):
    gid = interact.greet(E.db, CH, A, B, E.now[0])
    with pytest.raises(GameError) as e:
        interact.hug_answer(E.db, E.city, CH, gid, B, "y", E.now[0])
    assert e.value.code == "ia_gone"
    with pytest.raises(GameError):
        interact.hug_answer(E.db, E.city, CH, gid, B, "zzz", E.now[0])


def test_the_database_allows_only_one_pending_request_per_pair(E):
    E.meet(A, B)
    interact.hug_request(E.db, E.city, CH, A, B, E.now[0])
    with pytest.raises(Exception):
        E.db.x("INSERT INTO interactions(kind,chat_id,from_id,to_id,state,created_at,expires_at) VALUES('hug',?,?,?,'pending',?,?)", (CH, A, B, E.now[0], E.now[0] + 9))
    with pytest.raises(Exception):
        E.db.x("INSERT INTO interactions(kind,chat_id,from_id,to_id,state,created_at) VALUES('wave',?,?,?,'sent',?)", (CH, A, B, E.now[0]))


# ================================================================================================ through the real handlers
@pytest.fixture()
def h(layout):
    worldmap.cache_clear()
    worldplaces.cache_clear()
    hh = tb.Harness()
    world.save_world(hh.db, CH, copy.deepcopy(layout))
    for u in (A, B, X):
        hh.say(u, "شروع")
        hh.say(u, "سلام به همه", chat=CH, name=f"User{u}")
        hh.db.x("INSERT OR IGNORE INTO group_members(group_id,user_id,last_seen) VALUES(?,?,?)", (CH, u, T0))      # (the bot records members in batches; the test does it at once)
    return hh


def city(h):
    return worldplaces.load_city(h.db, world.get_world(h.db, CH))


def meet(h, *uids, at=None):
    c = city(h)
    ref = at or c.mcd_id
    for u in uids:
        out = presence.start_trip(h.db, c, u, ref)
        assert out.status in ("started", "here")
        tr = presence.walking_trip(h.db, c.id, u)
        if tr is not None:
            h.advance(tr["arrive_at"] - core.now() + 1)
        presence.stand(h.db, c, u)


def far_from_mcd(h):
    c = city(h)
    field = c.net.field(c.spot(c.mcd_id).door.pt)
    return max((s for s in c.spots("landmark") if s.id != c.mcd_id), key=lambda s: field.to(s.door)).id


def sent(calls):
    return [c for c in calls if isinstance(c, SendMessage)]


def edits(calls):
    return [c for c in calls if isinstance(c, EditMessageText)]


def alerts(calls):
    return [c for c in calls if isinstance(c, AnswerCallbackQuery) and c.show_alert and c.text]


def toasts(calls):
    return [c.text for c in calls if isinstance(c, AnswerCallbackQuery) and c.text]


def data_of(call):
    return [b.callback_data for row in call.reply_markup.inline_keyboard for b in row]


def nearby(h, who=A):
    """Open the map of `who` and its «اطراف من» screen: returns (driver, the nearby screen)."""
    D = mapkit.Driver(h, CH)
    m = D.open(who)
    nb = D.go(who, m, data=f"mp:{m.sid}:nb")
    return D, nb


def person(h, to=B, who=A):
    """Open the map, «اطراف من», and the screen of player `to`: returns (driver, that screen)."""
    D, nb = nearby(h, who)
    return D, D.go(who, nb, data=f"mp:{nb.sid}:pp:{to}")


def say_hello(h, to=B, who=A):
    D, pp = person(h, to, who)
    return D, pp, D.go(who, pp, data=f"mp:{pp.sid}:gr:{to}")


def ask_hug(h, to=B, who=A):
    D, pp = person(h, to, who)
    out = D.go(who, pp, data=f"mp:{pp.sid}:hg:{to}")
    msgs = sent(out.calls)
    return out, (msgs[-1] if msgs else None)


def test_around_me_lists_the_players_as_glass_buttons(h):
    meet(h, A, B, X)
    D, nb = nearby(h)
    assert f"mp:{nb.sid}:pp:{B}" in nb.data() and f"mp:{nb.sid}:pp:{X}" in nb.data()
    labels = {d: t_ for t_, d in nb.buttons}
    assert "User61002" in labels[f"mp:{nb.sid}:pp:{B}"] and "User61003" in labels[f"mp:{nb.sid}:pp:{X}"] and labels[f"mp:{nb.sid}:pp:{B}"].startswith("👤")
    assert nb.rows[0][0][1].startswith(f"mp:{nb.sid}:pp:")                                          # the people come first, the places after them
    assert "سلام" in nb.caption and "بغل" in nb.caption                                              # the hint says what the buttons do
    assert all(len(d.encode()) <= 64 for d in nb.data())


def test_nobody_nearby_means_no_player_buttons(h):
    meet(h, A)
    meet(h, B, at=far_from_mcd(h))
    D, nb = nearby(h)
    assert not [d for d in nb.data() if ":pp:" in d] and "کسی" in nb.caption or "بازیکنی نزدیک تو نیست" in nb.caption


def test_pressing_a_name_opens_that_player_with_hello_and_hug(h):
    meet(h, A, B)
    D, pp = person(h)
    assert pp.caption.count("User61002") >= 1 and pp.photo is not None
    assert [d for d in pp.data() if ":gr:" in d] == [f"mp:{pp.sid}:gr:{B}"] and [d for d in pp.data() if ":hg:" in d] == [f"mp:{pp.sid}:hg:{B}"]
    assert any(d.endswith(":nb") for d in pp.data()) and any(d.endswith(":rf") for d in pp.data())       # the way back to the list and to the map
    assert [t_ for t_, d in pp.buttons if ":gr:" in d][0].startswith("👋") and [t_ for t_, d in pp.buttons if ":hg:" in d][0].startswith("🤗")


def test_hello_names_both_players_and_notifies_the_target(h):
    meet(h, A, B)
    D, pp, out = say_hello(h)
    msgs = sent(out.calls)
    assert len(msgs) == 1 and not out.edits                                                         # a NEW message in the group; the map message is left alone
    assert f'<a href="tg://user?id={A}">' in msgs[0].text and f'<a href="tg://user?id={B}">' in msgs[0].text and "User61001" in msgs[0].text and "User61002" in msgs[0].text
    assert msgs[0].reply_markup is None                                                             # nothing to answer
    assert out.toasts and "سلام" in out.toasts[0]
    assert [(r["from_id"], r["to_id"], r["state"]) for r in h.db.qa("SELECT * FROM interactions")] == [(A, B, "sent")]


def test_the_hello_cooldown_message_is_friendly_and_nothing_is_recorded(h):
    meet(h, A, B, X)
    D, pp, first = say_hello(h)
    again = D.go(A, pp, data=f"mp:{pp.sid}:gr:{B}")
    assert again.alerts and "⏳" in again.alerts[0] and "ثانیه" in again.alerts[0] and not sent(again.calls)
    assert len(h.db.qa("SELECT * FROM interactions")) == 1
    h.advance(C.GREET_COOLDOWN)
    assert sent(D.go(A, pp, data=f"mp:{pp.sid}:gr:{B}").calls)


def test_a_hello_can_go_to_anyone_in_the_list_one_after_another(h):
    meet(h, A, B, X)
    D, nb = nearby(h)
    pb = D.go(A, nb, data=f"mp:{nb.sid}:pp:{B}")
    assert sent(D.go(A, pb, data=f"mp:{pb.sid}:gr:{B}").calls)
    h.advance(C.GREET_COOLDOWN)
    back = D.go(A, pb, data=f"mp:{pb.sid}:nb")
    px = D.go(A, back, data=f"mp:{back.sid}:pp:{X}")
    out = D.go(A, px, data=f"mp:{px.sid}:gr:{X}")
    assert f"tg://user?id={X}" in sent(out.calls)[0].text
    assert [(r["to_id"]) for r in h.db.qa("SELECT * FROM interactions ORDER BY id")] == [B, X]


def test_typed_hello_and_hug_words_are_gone_and_plain_chat_stays_plain(h):
    meet(h, A, B)
    for text in ("سلام", "سلام کن", "بغل", "بغل کن", "بغل کردن خوبه", "wave", "hug", "greet", "!سلام کن", "/greet", "!بغل کن", "سلام کن @u61002", "hug @u61002"):
        h.say(A, text, chat=CH)
        h.say(A, text, chat=CH, reply_to=B)
        assert h.db.qa("SELECT * FROM interactions") == [], text
    assert "greet" not in docs.DOCS and "hug" not in docs.DOCS and "greet" not in texts.ALIASES and "hug" not in texts.ALIASES
    for d in docs.DOCS.values():
        assert "سلام کن" not in d[0] and "بغل کن" not in d[0] and "greet" not in d[2].lower().split() and "reply to a player" not in d[2]


def test_a_hello_button_only_works_from_that_players_screen(h):
    meet(h, A, B, X)
    D, nb = nearby(h)
    out = D.go(A, nb, data=f"mp:{nb.sid}:gr:{B}")                                                   # the list is showing, not B's screen
    assert out.alerts and not sent(out.calls) and h.db.qa("SELECT * FROM interactions") == []
    pb = D.go(A, nb, data=f"mp:{nb.sid}:pp:{B}")
    out = D.go(A, pb, data=f"mp:{pb.sid}:gr:{X}")                                                   # B's screen is showing; a button for X is a stale / hand-made one
    assert out.alerts and not sent(out.calls) and h.db.qa("SELECT * FROM interactions") == []


@pytest.mark.parametrize("who", ["99999", "-5", "abc", "1e3", "61001", "", "9" * 30])
def test_hand_made_player_ids_show_the_list_again_and_change_nothing(h, who):
    meet(h, A, B)
    D, nb = nearby(h)
    for act in ("pp", "gr", "hg"):
        out = D.go(A, nb, data=f"mp:{nb.sid}:{act}:{who}") if who else D.go(A, nb, data=f"mp:{nb.sid}:{act}")
        assert not sent(out.calls) and h.db.qa("SELECT * FROM interactions") == [], (act, who)


def test_somebody_elses_map_buttons_do_nothing(h):
    meet(h, A, B, X)
    D, pp = person(h)
    out = D.go(X, pp, data=f"mp:{pp.sid}:gr:{B}")                                                   # X presses the buttons of A's map
    assert not sent(out.calls) and h.db.qa("SELECT * FROM interactions") == []
    out = D.go(B, pp, data=f"mp:{pp.sid}:hg:{B}")
    assert not sent(out.calls) and h.db.qa("SELECT * FROM interactions") == []


def test_a_player_who_walked_away_is_not_in_the_list_any_more(h):
    meet(h, A, B)
    D, pp = person(h)
    meet(h, B, at=far_from_mcd(h))
    out = D.go(A, pp, data=f"mp:{pp.sid}:gr:{B}")
    assert not sent(out.calls) and h.db.qa("SELECT * FROM interactions") == []
    assert out.toasts and "نزدیک" in out.toasts[0] and out.edits                                       # the list is shown again, with the reason


def test_a_hug_request_goes_out_with_buttons_for_the_target_and_the_sender(h):
    meet(h, A, B)
    out, msg = ask_hug(h)
    row = h.db.q1("SELECT * FROM interactions WHERE kind='hug'")
    assert row["state"] == "pending" and row["message_id"] is not None                              # the message it lives in is remembered
    assert f"tg://user?id={A}" in msg.text and f"tg://user?id={B}" in msg.text and not out.edits
    assert data_of(msg) == [f"ia:y:{row['id']}:{B}", f"ia:n:{row['id']}:{B}", f"ia:c:{row['id']}:{A}"]
    assert all(len(d.encode()) <= 64 for d in data_of(msg)) and out.toasts and "بغل" in out.toasts[0]


def test_the_hug_buttons_end_to_end_with_double_and_foreign_presses(h):
    meet(h, A, B, X)
    _c, msg = ask_hug(h)
    row = h.db.q1("SELECT * FROM interactions WHERE kind='hug'")
    yes, no, cancel = data_of(msg)
    out = h.press(A, yes, chat=CH)                                                                    # the sender presses «accept» on their own request
    assert not edits(out) and toasts(out) and h.db.q1("SELECT state FROM interactions WHERE id=?", (row["id"],))["state"] == "pending"
    out = h.press(X, yes, chat=CH)                                                                    # a bystander presses it
    assert not edits(out) and h.db.q1("SELECT state FROM interactions WHERE id=?", (row["id"],))["state"] == "pending"
    out = h.press(A, f"ia:y:{row['id']}:{A}", chat=CH)                                                # the sender forges the target's id on the button
    assert not edits(out) and alerts(out) and h.db.q1("SELECT state FROM interactions WHERE id=?", (row["id"],))["state"] == "pending"
    out = h.press(B, yes, chat=CH)                                                                    # the real target accepts
    assert edits(out) and "همدیگه رو بغل کردن" in edits(out)[-1].text and h.db.q1("SELECT state FROM interactions WHERE id=?", (row["id"],))["state"] == "accepted"
    for who, data in ((B, yes), (B, no), (A, cancel), (B, yes)):                                      # repeated and late presses change nothing and say so
        out = h.press(who, data, chat=CH)
        assert h.db.q1("SELECT state FROM interactions WHERE id=?", (row["id"],))["state"] == "accepted", (who, data)
        assert "دیگه فعال نیست" in " ".join(toasts(out)), (who, data)


def test_declining_and_taking_back_through_the_buttons(h):
    meet(h, A, B)
    _c, msg = ask_hug(h)
    yes, no, cancel = data_of(msg)
    out = h.press(B, no, chat=CH)
    assert "بغل نکرد" in edits(out)[-1].text and h.db.q1("SELECT state FROM interactions")["state"] == "declined"
    h.advance(C.HUG_COOLDOWN + interact.PAIR_PAUSE)
    _c, msg2 = ask_hug(h)
    row = h.db.q1("SELECT * FROM interactions WHERE state='pending'")
    out = h.press(B, f"ia:c:{row['id']}:{B}", chat=CH)                                                # the target cannot "take back" the sender's request
    assert not edits(out) and h.db.q1("SELECT state FROM interactions WHERE id=?", (row["id"],))["state"] == "pending"
    out = h.press(A, f"ia:c:{row['id']}:{A}", chat=CH)
    assert "پس گرفت" in edits(out)[-1].text and h.db.q1("SELECT state FROM interactions WHERE id=?", (row["id"],))["state"] == "cancelled"


def test_a_late_press_after_the_time_is_up_edits_the_message_to_expired(h):
    meet(h, A, B)
    _c, msg = ask_hug(h)
    h.advance(C.HUG_TTL)
    out = h.press(B, data_of(msg)[0], chat=CH)
    assert "بی‌جواب" in edits(out)[-1].text and "دیگه فعال نیست" in " ".join(toasts(out))
    assert h.db.q1("SELECT state FROM interactions")["state"] == "expired"


def test_a_hug_cooldown_and_open_request_are_explained_politely(h):
    meet(h, A, B)
    D, pp = person(h)
    first = D.go(A, pp, data=f"mp:{pp.sid}:hg:{B}")
    assert sent(first.calls)
    again = D.go(A, pp, data=f"mp:{pp.sid}:hg:{B}")
    assert again.alerts and ("⏳" in again.alerts[0] or "بازه" in again.alerts[0]) and not sent(again.calls)
    h.advance(C.HUG_COOLDOWN)
    third = D.go(A, pp, data=f"mp:{pp.sid}:hg:{B}")
    assert third.alerts and "بازه" in third.alerts[0]
    assert len(h.db.qa("SELECT * FROM interactions WHERE kind='hug'")) == 1


def test_a_hug_after_a_no_is_refused_for_a_while(h):
    meet(h, A, B)
    D, pp = person(h)
    msg = sent(D.go(A, pp, data=f"mp:{pp.sid}:hg:{B}").calls)[-1]
    h.press(B, data_of(msg)[1], chat=CH)
    h.advance(C.HUG_COOLDOWN)
    out = D.go(A, pp, data=f"mp:{pp.sid}:hg:{B}")
    assert out.alerts and "رد کرد" in out.alerts[0] and len(h.db.qa("SELECT * FROM interactions WHERE kind='hug'")) == 1


@pytest.mark.parametrize("data", ["ia:y:abc:61002", "ia:z:1:61002", "ia:y:1:2:61002", "ia:y:99999:61002", "ia:y::61002", "ia:y:1", "ia:y", "ia:y:-1:61002", "ia:y:1e3:61002",
                                  "ia:c:1:61002", "ia:y:" + "9" * 40 + ":61002"])
def test_forged_hug_buttons_do_nothing_harmful(h, data):
    meet(h, A, B)
    ask_hug(h)
    before = [tuple(r) for r in h.db.qa("SELECT * FROM interactions")]
    out = h.press(B, data, chat=CH)
    assert [tuple(r) for r in h.db.qa("SELECT * FROM interactions")] == before
    assert not edits(out)


def test_a_hug_button_in_a_private_chat_finds_no_city(h):
    meet(h, A, B)
    _c, msg = ask_hug(h)
    out = h.press(B, data_of(msg)[0], chat=B)
    assert not edits(out) and h.db.q1("SELECT state FROM interactions")["state"] == "pending"


def test_hugs_and_hellos_leave_money_and_the_stock_game_alone(h):
    meet(h, A, B)
    snap = lambda: ([tuple(r) for r in h.db.qa("SELECT id,cash,pending_div,pending_inc,rep,max_level,spent_points,rebirths,legacy_pts,legacy_total,nw,trades,badge FROM users ORDER BY id")],
                    [tuple(r) for r in h.db.qa("SELECT * FROM life_accounts ORDER BY user_id")],
                    [tuple(r) for r in h.db.qa("SELECT * FROM life_ledger ORDER BY id")], [tuple(r) for r in h.db.qa("SELECT * FROM holdings")])
    before = snap()
    D, pp, _hello = say_hello(h)
    h.advance(C.GREET_COOLDOWN)
    msg = sent(D.go(A, pp, data=f"mp:{pp.sid}:hg:{B}").calls)[-1]
    h.press(B, data_of(msg)[0], chat=CH)
    assert snap() == before


def test_the_wording_in_english(h):
    h.db.x("UPDATE groups SET lang='en' WHERE id=?", (CH,)) if h.db.q1("SELECT 1 FROM groups WHERE id=?", (CH,)) else None
    for k in ("mp.pp.title", "mp.pp.b.greet", "mp.pp.b.hug", "mp.pp.gone", "mp.pp.sent_greet", "mp.pp.sent_hug", "mp.nb.hint_people", "mp.nb.pbtn"):
        assert texts.t("en", k, name="N", d="1") != k and texts.t("fa", k, name="N", d="1") != k
