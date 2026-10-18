"""v0.15: the 24-hour backup schedule, the admin-only panel (/admin) and the required-channel gate.

Telegram is faked: a `Chan` is a pretend channel with members, an invite link and failure modes."""
import gzip
import os
import sqlite3
from types import SimpleNamespace

import pytest
from aiogram.exceptions import TelegramBadRequest
from aiogram.methods import AnswerCallbackQuery, EditMessageText, SendDocument, SendMessage

from hypemarket import adminpanel, backups, config as C, core, fmt, gate, handlers, jobs, sections, textparse, views
from hypemarket.db import Database
from hypemarket.texts import EN, FA, t

import test_bot as tb

ADMIN = 1
GROUP = -100500


# ---------------------------------------------------------------------------------------------- fakes
class Chan:
    """A pretend Telegram channel."""

    def __init__(self, id=-1001234567890, username="hypechan", title="Hype News", type="channel", bot_status="administrator", invite_link=None):
        self.id, self.username, self.title, self.type = id, username, title, type
        self.bot_status, self.invite_link = bot_status, invite_link
        self.members: dict = {}                 # uid -> (status, is_member)
        self.down = None                        # callable(method) -> exception: every member lookup fails with it
        self.asked: list = []                   # uids whose membership was looked up
        self.export_fails = False

    def join(self, uid):
        self.members[uid] = ("member", True)

    def leave(self, uid):
        self.members[uid] = ("left", False)


def install(h, *chans):
    orig = h.session.make_request
    by_id = {c.id: c for c in chans}
    by_name = {"@" + c.username.lower(): c for c in chans if c.username}

    async def fake(bot, method, timeout=None):
        name = type(method).__name__
        if name == "GetChat":
            h.session.calls.append(method)
            ref = method.chat_id
            ch = by_id.get(ref) if isinstance(ref, int) else by_name.get(str(ref).lower())
            if not ch:
                raise TelegramBadRequest(method=method, message="Bad Request: chat not found")
            return SimpleNamespace(id=ch.id, type=ch.type, title=ch.title, username=ch.username, invite_link=ch.invite_link)
        if name == "ExportChatInviteLink":
            h.session.calls.append(method)
            ch = by_id[method.chat_id]
            if ch.export_fails:
                raise TelegramBadRequest(method=method, message="Bad Request: not enough rights")
            return "https://t.me/+exported"
        if name == "GetChatMember" and method.chat_id in by_id:
            h.session.calls.append(method)
            ch = by_id[method.chat_id]
            if method.user_id == h.bot.id:
                return SimpleNamespace(status=ch.bot_status)
            ch.asked.append(method.user_id)
            if ch.down:
                raise ch.down(method)
            status, is_member = ch.members.get(method.user_id, ("left", False))
            return SimpleNamespace(status=status, is_member=is_member)
        return await orig(bot, method, timeout)
    h.session.make_request = fake


@pytest.fixture()
def h():
    return tb.Harness()


@pytest.fixture()
def ch(h):
    c = Chan()
    install(h, c)
    return c


def gate_on(h, c, link="https://t.me/hypechan", on=True):
    gate.save(h.db, {"id": c.id, "title": c.title, "username": c.username, "link": link}, on=on)


def prompts(calls):
    return [m for m in calls if isinstance(m, SendMessage) and "gate:check:" in cbs(m)]


def visible(calls):
    """What a player actually sees: the bot's own look-ups of the channel are not messages."""
    return [c for c in calls if type(c).__name__ not in ("GetChatMember", "GetChat", "ExportChatInviteLink")]


def cbs(msg):
    kbd = getattr(msg, "reply_markup", None)
    if not kbd:
        return ""
    return " ".join(b.callback_data or "" for row in kbd.inline_keyboard for b in row)


def urls(msg):
    return [b.url for row in msg.reply_markup.inline_keyboard for b in row if getattr(b, "url", None)]


def toasts(calls):
    return [c for c in calls if isinstance(c, AnswerCallbackQuery)]


def with_settings(h, **kw):
    h.app.settings = h.app.settings.__class__(**{**h.app.settings.__dict__, **kw})


# ================================================================================================ the 24-hour backups
def test_backups_default_to_every_24_hours_and_ignore_the_old_setting(monkeypatch, tmp_path):
    monkeypatch.chdir(tmp_path)
    for k in ("BACKUP_EVERY_HOURS", "BACKUP_EVERY_MIN", "BACKUP_KEEP"):
        monkeypatch.delenv(k, raising=False)
    assert C.load_settings().backup_every_min == 1440 and C.load_settings().backup_keep == 30
    monkeypatch.setenv("BACKUP_EVERY_MIN", "60")                              # an old .env still says hourly: it is no longer read
    assert C.load_settings().backup_every_min == 1440
    monkeypatch.setenv("BACKUP_EVERY_HOURS", "12")
    assert C.load_settings().backup_every_min == 720
    monkeypatch.setenv("BACKUP_EVERY_HOURS", "0.5")
    assert C.load_settings().backup_every_min == 30
    monkeypatch.setenv("BACKUP_EVERY_HOURS", "0")
    assert C.load_settings().backup_every_min == 5                            # never faster than every five minutes
    monkeypatch.setenv("BACKUP_EVERY_HOURS", "soon")
    assert C.load_settings().backup_every_min == 1440
    assert "BACKUP_EVERY_HOURS=24" in open(os.path.join(os.path.dirname(__file__), "..", ".env.example")).read()


def docs_sent(h):
    return [d for d in h.session.sent(SendDocument)]


def test_a_backup_is_sent_when_due_then_only_once_every_24_hours(h, tmp_path):
    with_settings(h, admins=frozenset({1, 2}), backup_dir=str(tmp_path / "bk"))
    assert backups.interval(h.app.settings) == 86400 and backups.due(h.db, 86400)            # never backed up: due now
    first = h.run(backups.scheduled(h.app))
    assert first["ok"] and first["sent"] == 2 and first.get("done") and len(docs_sent(h)) == 2
    assert backups.last_done(h.db) == core.now()
    for secs in (60, 3600, 12 * 3600, 86400 - 601):                                         # nothing more for the rest of the day
        h.advance(secs if secs < 86400 - 601 else 86400 - 601 - (60 + 3600 + 12 * 3600))
    assert h.run(backups.scheduled(h.app)) is None and len(docs_sent(h)) == 2
    h.advance(601)
    again = h.run(backups.scheduled(h.app))
    assert again and again["sent"] == 2 and len(docs_sent(h)) == 4
    assert backups.next_due(h.db, 86400) == core.now() + 86400


def test_every_scheduled_tick_asks_the_clock_not_the_calendar(h, tmp_path):
    """The job wakes up every few minutes; most wake-ups do nothing."""
    with_settings(h, admins=frozenset({1}), backup_dir="")
    h.run(backups.scheduled(h.app))
    n = len(docs_sent(h))
    for _ in range(30):
        h.advance(600)
        assert h.run(backups.scheduled(h.app)) is None
    assert len(docs_sent(h)) == n == 1
    assert backups.CHECK_EVERY == 600


def test_a_restart_neither_skips_nor_repeats_a_backup(h, tmp_path):
    with_settings(h, admins=frozenset({1}), backup_dir="")
    h.run(backups.scheduled(h.app))
    h.advance(3 * 3600)
    app2 = handlers.App(settings=h.app.settings, db=h.db, bot=h.bot, botname="TestBot")          # "the bot restarted": same database, fresh process
    assert h.run(backups.scheduled(app2)) is None and len(docs_sent(h)) == 1
    h.advance(21 * 3600)
    assert h.run(backups.scheduled(app2)) and len(docs_sent(h)) == 2
    # a bot that was down for days sends one backup right after it comes back, not one per missed day
    h.advance(5 * 86400)
    assert h.run(backups.scheduled(app2)) and h.run(backups.scheduled(app2)) is None and len(docs_sent(h)) == 3


def test_an_undelivered_backup_is_retried_in_an_hour_not_every_ten_minutes(h, tmp_path):
    with_settings(h, admins=frozenset({1}), backup_dir=str(tmp_path / "bk"))
    h.session.forbidden.add(1)                                                                 # the admin never pressed /start
    res = h.run(backups.scheduled(h.app))
    assert res["ok"] and res["sent"] == 0 and not res.get("done") and backups.last_done(h.db) == 0
    for _ in range(5):                                                                           # ten-minute wake-ups: no retry storm
        h.advance(600)
        assert h.run(backups.scheduled(h.app)) is None
    h.advance(600)
    h.session.forbidden.discard(1)
    ok = h.run(backups.scheduled(h.app))                                                         # an hour after the failure
    assert ok and ok["sent"] == 1 and ok.get("done") and backups.last_done(h.db) == core.now()


def test_a_backup_that_cannot_be_made_alerts_and_is_retried_later(h, monkeypatch):
    with_settings(h, admins=frozenset({1}))
    monkeypatch.setattr(backups, "make_backup", lambda *a, **k: (_ for _ in ()).throw(RuntimeError("disk full")))
    res = h.run(backups.scheduled(h.app))
    assert not res["ok"] and backups.last_done(h.db) == 0
    assert any("FAILED" in (m.text or "") for m in h.session.sent(SendMessage))
    h.advance(1200)
    assert h.run(backups.scheduled(h.app)) is None                                              # not again within the hour


def test_a_wrong_clock_cannot_stop_backups_forever(h):
    with_settings(h, admins=frozenset({1}), backup_dir="")
    core.kv_set(h.db, backups.LAST_KEY, core.now() + 10 * 86400)                                # a timestamp from the future
    assert backups.due(h.db, 86400)
    assert h.run(backups.scheduled(h.app)) and backups.last_done(h.db) == core.now()


def test_without_admins_the_backup_is_kept_locally_and_counts_as_done(h, tmp_path):
    with_settings(h, admins=frozenset(), backup_dir=str(tmp_path / "bk"))
    res = h.run(backups.scheduled(h.app))
    assert res["ok"] and res["sent"] == 0 and res.get("done") and len(os.listdir(tmp_path / "bk")) == 1
    f = os.path.join(tmp_path, "bk", os.listdir(tmp_path / "bk")[0])
    raw = gzip.decompress(open(f, "rb").read())
    out = tmp_path / "x.db"
    out.write_bytes(raw)
    assert sqlite3.connect(str(out)).execute("PRAGMA integrity_check").fetchone()[0] == "ok"


def test_the_job_loop_wakes_up_every_ten_minutes_and_the_manual_backup_still_works(h, tmp_path):
    with_settings(h, admins=frozenset({1}), backup_dir="")
    assert min(backups.CHECK_EVERY, backups.interval(h.app.settings)) == 600
    with_settings(h, backup_every_min=5)
    assert min(backups.CHECK_EVERY, backups.interval(h.app.settings)) == 300
    out = h.texts(h.say(1, "/backup"))
    assert "1" in out and len(docs_sent(h)) == 1
    assert backups.last_done(h.db) == 0                                                          # a manual backup does not move the schedule


def test_startup_message_says_how_often_and_when_next(h):
    with_settings(h, admins=frozenset({1}))
    first = backups.ready_text(h.db, h.app.settings)
    assert "1d" in first and fmt.dur(86400, "fa") in first and "/admin" in first and EN["backup.first"] in first and FA["backup.first"] in first
    core.kv_set(h.db, backups.LAST_KEY, core.now())
    later = backups.ready_text(h.db, h.app.settings)
    assert backups.when(core.now() + 86400) in later


# ================================================================================================ gate: setting up
def test_parse_ref_accepts_names_links_and_negative_ids_only():
    assert gate.parse_ref("@hypechan") == "@hypechan" and gate.parse_ref("hypechan") == "@hypechan"
    assert gate.parse_ref("https://t.me/hypechan") == "@hypechan" and gate.parse_ref("t.me/hypechan/") == "@hypechan"
    assert gate.parse_ref("  @Hype_Chan  ") == "@Hype_Chan" and gate.parse_ref("@Hype_Chan extra words") is None and gate.parse_ref("hello world!") is None
    assert gate.parse_ref("-1001234567890") == -1001234567890
    for bad in ("", "   ", "12345", "1234567890", "@a", "@bad name!", "https://t.me/+AbCdEf", "https://t.me/joinchat/xyz", "javascript:alert(1)",
                "https://evil.example/hypechan", "@" + "x" * 40, "-12", None):
        assert gate.parse_ref(bad) is None, bad


def test_resolve_checks_the_channel_and_the_bots_rights(h, ch):
    info = h.run(gate.resolve(h.bot, "@hypechan"))
    assert info == {"id": ch.id, "title": "Hype News", "username": "hypechan", "link": "https://t.me/hypechan"}
    assert h.run(gate.resolve(h.bot, str(ch.id)))["id"] == ch.id                                   # by numeric id too
    priv = Chan(id=-1009876543210, username=None, title="Secret", invite_link="https://t.me/+given")
    install(h, ch, priv)
    assert h.run(gate.resolve(h.bot, priv.id))["link"] == "https://t.me/+given"                      # private: the invite link the channel has
    priv.invite_link = None
    assert h.run(gate.resolve(h.bot, priv.id))["link"] == "https://t.me/+exported"                   # ...or one the bot creates
    priv.export_fails = True
    assert h.run(gate.resolve(h.bot, priv.id))["link"] == ""                                         # no link possible: still usable
    for raw, code in (("hello world!", "bad_ref"), ("@nosuchchan", "not_found"), ("-100111222333", "not_found"), (True, "bad_ref"), (5, "bad_ref"), (None, "bad_ref")):
        with pytest.raises(gate.GateError) as e:
            h.run(gate.resolve(h.bot, raw))
        assert e.value.code == code, raw
    ch.bot_status = "member"
    with pytest.raises(gate.GateError) as e:
        h.run(gate.resolve(h.bot, "@hypechan"))
    assert e.value.code == "bot_not_admin"
    ch.bot_status = "administrator"
    ch.type = "private"
    with pytest.raises(gate.GateError) as e:
        h.run(gate.resolve(h.bot, "@hypechan"))
    assert e.value.code == "not_channel"


def test_the_setting_lives_in_the_database(tmp_path):
    path = str(tmp_path / "g.db")
    d = Database(path)
    assert gate.get(d) is None and gate.active(d) is None
    gate.save(d, {"id": -1001, "title": "T <b>", "username": "tt", "link": "https://t.me/tt"}, on=False)
    assert gate.get(d)["on"] is False and gate.active(d) is None
    gate.set_on(d, True)
    d.close()
    d = Database(path)                                                                               # a restart keeps it
    s = gate.active(d)
    assert s and s["chat"] == -1001 and gate.label(s) == "@tt" and s["link"] == "https://t.me/tt"
    gate.clear(d)
    assert gate.get(d) is None
    gate.save(d, {"id": -1002, "title": "Only title", "username": "", "link": ""}, on=True)
    assert gate.label(gate.active(d)) == "Only title"
    core.kv_set(d, "gate:chat", "garbage")
    assert gate.get(d) is None                                                                       # a damaged setting means "no gate", never a crash


# ================================================================================================ gate: players
def test_the_gate_is_off_until_an_admin_turns_it_on(h, ch):
    welcome = h.say(2, "/start")
    assert core.get_user(h.db, 2) is not None and not prompts(welcome) and [c for c in welcome if isinstance(c, SendMessage)]
    assert ch.asked == []                                                                            # no gate: Telegram is never asked
    gate_on(h, ch, on=False)
    h.say(3, "/daily")
    assert ch.asked == [] and core.get_user(h.db, 3) is not None


def test_a_player_who_has_not_joined_gets_a_join_prompt_and_nothing_else(h, ch):
    gate_on(h, ch)
    for text in ("/daily", "/profile", "منو", "سهام", "/market", "مپ", "/help", "خرید 10 SPEED"):
        calls = h.say(2, text)
        ps = prompts(calls)
        assert len(ps) == 1 and len(visible(calls)) == 1, text                                               # exactly the prompt, nothing else
        assert "@hypechan" in ps[0].text and urls(ps[0]) == ["https://t.me/hypechan"] and "gate:check:2" in cbs(ps[0])
    assert core.get_user(h.db, 2) is None                                                            # blocked messages leave no trace
    assert h.db.q1("SELECT COUNT(*) c FROM users WHERE bot=0")["c"] == 0
    # ordinary text is not a command: no prompt, no reply
    assert h.say(2, "سلام چطورید") == []


def test_the_prompt_is_in_the_players_language(h, ch):
    gate_on(h, ch)
    fa = prompts(h.say(2, "/daily"))[0].text
    core.ensure_user(h.db, 9, "u9", "Nine", lang="en")
    h.db.x("UPDATE users SET lang='en' WHERE id=9")
    en = prompts(h.say(9, "/daily"))[0].text
    assert "عضو کانال" in fa and "User2" in fa and "join" in en.lower() and "User9" in en


def test_start_still_registers_a_new_player_and_keeps_the_invite(h, ch):
    h.say(5, "/start")                                                                               # the inviter registers before the gate exists
    gate_on(h, ch)
    calls = h.say(2, "/start ref_5")
    assert len(prompts(calls)) == 1 and "welcome" not in h.texts(calls).lower()
    u = core.get_user(h.db, 2)
    assert u is not None and u["referrer"] == 5                                                      # registered, invite counted
    assert len(visible(calls)) == 1                                                                  # but the player only sees the join prompt


def test_members_pass_and_membership_is_remembered_for_ten_minutes(h, ch):
    gate_on(h, ch)
    ch.join(2)
    first = h.say(2, "/start")
    assert not prompts(first) and ch.asked == [2]
    for _ in range(5):
        assert not prompts(h.say(2, "/daily"))
    assert ch.asked == [2]                                                                           # one API call, however much they use the bot
    h.advance(gate.OK_TTL + 1)
    h.say(2, "/daily")
    assert ch.asked == [2, 2]                                                                        # asked again after the TTL
    ch.leave(2)
    assert not prompts(h.say(2, "/daily"))                                                           # still remembered as a member for the TTL
    h.advance(gate.OK_TTL + 1)
    calls = h.say(2, "/daily")
    assert len(prompts(calls)) == 1                                                                  # left the channel: blocked again once the cache expires


def test_every_membership_status_is_judged_correctly(h, ch):
    gate_on(h, ch)
    cases = {3: ("member", True, True), 4: ("administrator", True, True), 5: ("creator", True, True), 6: ("left", False, False),
             7: ("kicked", False, False), 8: ("restricted", True, True), 9: ("restricted", False, False)}
    for uid, (status, is_member, ok) in cases.items():
        ch.members[uid] = (status, is_member)
        verdict = h.run(gate.membership(h.app, uid))
        assert verdict == ("yes" if ok else "no"), (uid, status, is_member)
    assert h.run(gate.membership(h.app, 99)) == "no"                                                 # never seen in the channel
    assert h.run(gate.membership(h.app, ADMIN)) == "yes" and ADMIN not in ch.asked                  # admins are never asked about


def test_admins_are_never_gated_even_when_not_in_the_channel(h, ch):
    gate_on(h, ch)
    calls = h.say(ADMIN, "/start")
    assert not prompts(calls) and ch.asked == []
    pressed = h.press(ADMIN, "mn:menu:1")
    assert [c for c in pressed if isinstance(c, EditMessageText)] and not [c for c in pressed if isinstance(c, AnswerCallbackQuery) and c.show_alert]


def test_banned_players_stay_ignored_and_are_not_prompted(h, ch):
    h.say(2, "/start")
    h.db.x("UPDATE users SET banned=1 WHERE id=2")
    gate_on(h, ch)
    assert h.say(2, "/daily") == []


def test_buttons_do_not_work_until_the_player_has_joined(h, ch):
    h.say(2, "/start")
    gate_on(h, ch)
    calls = h.press(2, "mn:menu:2")
    ts = toasts(calls)
    assert len(ts) == 1 and ts[0].show_alert and "@hypechan" in ts[0].text and not [c for c in calls if isinstance(c, EditMessageText)]
    assert len(visible(calls)) == 1
    ch.join(2)
    h.advance(gate.NO_TTL + 1)
    assert [c for c in h.press(2, "mn:menu:2") if isinstance(c, EditMessageText)]                      # the same button works once they joined


def test_i_joined_button_checks_live_and_lets_the_player_in(h, ch):
    gate_on(h, ch)
    h.say(2, "/start")                                                                               # registered, prompted
    still = h.press(2, "gate:check:2")
    t_ = toasts(still)
    assert len(t_) == 1 and t_[0].show_alert and "@hypechan" in t_[0].text and not [c for c in still if isinstance(c, EditMessageText)]
    ch.join(2)
    asked = len(ch.asked)
    done = h.press(2, "gate:check:2")
    e = [c for c in done if isinstance(c, EditMessageText)]
    assert len(ch.asked) == asked + 1                                                                # asked Telegram again, not the cache
    assert e and FA["gate.ok"] in e[-1].text and "mn:go:profile:2" in cbs(e[-1])                     # confirmation plus the main menu
    assert not prompts(h.say(2, "/daily"))                                                           # and the bot works for them now
    other = h.press(3, "gate:check:2")                                                               # somebody else's button
    assert [c for c in toasts(other)] and not [c for c in other if isinstance(c, EditMessageText)]


def test_i_joined_answers_even_when_the_gate_has_been_turned_off(h, ch):
    gate_on(h, ch)
    h.say(2, "/start")
    gate.set_on(h.db, False)
    e = [c for c in h.press(2, "gate:check:2") if isinstance(c, EditMessageText)]
    assert e and FA["gate.ok"] in e[-1].text


def test_turning_the_gate_off_frees_everyone_at_once(h, ch):
    gate_on(h, ch)
    assert prompts(h.say(2, "/start"))
    gate.set_on(h.db, False)
    assert not prompts(h.say(2, "/daily")) and not prompts(h.say(3, "/daily"))


def test_in_groups_only_commands_trigger_one_notice_per_player_per_90_seconds(h, ch):
    gate_on(h, ch)
    assert h.say(2, "سلام بچه‌ها", chat=GROUP) == [] and h.say(2, "امروز چه خبره", chat=GROUP) == []     # chat is not a command: silence
    first = h.say(2, "/menu", chat=GROUP)
    assert len(prompts(first)) == 1 and "gate:check:2" in cbs(prompts(first)[0])
    asked = len(ch.asked)
    for text in ("/menu", "/daily", "منو"):
        assert visible(h.say(2, text, chat=GROUP)) == [], text                                       # no spam
    assert len(ch.asked) == asked                                                                    # and no extra API calls either (the "no" is remembered briefly)
    other = h.say(3, "/menu", chat=GROUP)
    assert len(prompts(other)) == 1 and "gate:check:3" in cbs(prompts(other)[0])                              # each player gets their own notice
    h.advance(gate.NOTICE_EVERY + 1)
    assert len(prompts(h.say(2, "/menu", chat=GROUP))) == 1
    assert core.get_user(h.db, 2) is None                                                            # nobody was registered by it


def test_in_a_group_the_i_joined_button_belongs_to_the_player_it_was_made_for(h, ch):
    gate_on(h, ch)
    h.say(2, "/menu", chat=GROUP)
    wrong = h.press(3, "gate:check:2", chat=GROUP)
    assert [c for c in toasts(wrong)] and not [c for c in wrong if isinstance(c, EditMessageText)]
    ch.join(2)
    ok = h.press(2, "gate:check:2", chat=GROUP)
    e = [c for c in ok if isinstance(c, EditMessageText)]
    assert e and FA["gate.ok"] in e[-1].text and not cbs(e[-1])                                       # in a group: just the confirmation


def test_when_telegram_cannot_answer_players_are_let_in_and_the_admin_is_told_once(h, ch):
    gate_on(h, ch)
    ch.down = lambda m: TelegramBadRequest(method=m, message="Bad Request: CHAT_ADMIN_REQUIRED")
    calls = h.say(2, "/start")
    assert not prompts(calls) and core.get_user(h.db, 2) is not None                                 # nobody is locked out by a broken gate
    alerts = [m for m in h.session.sent(SendMessage) if m.chat_id == ADMIN and "Channel gate" in (m.text or "")]
    assert len(alerts) == 1
    h.advance(gate.UNKNOWN_TTL + 1)
    h.say(3, "/start")
    h.say(2, "/daily")
    assert len([m for m in h.session.sent(SendMessage) if m.chat_id == ADMIN and "Channel gate" in (m.text or "")]) == 1     # throttled: one an hour
    ch.down = None
    ch.join(4)
    h.advance(gate.UNKNOWN_TTL + 1)
    assert not prompts(h.say(4, "/start")) and prompts(h.say(7, "/start"))                           # back to normal as soon as Telegram answers


def test_user_not_found_means_not_a_member_not_an_outage(h, ch):
    gate_on(h, ch)
    ch.down = lambda m: TelegramBadRequest(method=m, message="Bad Request: user not found")
    assert len(prompts(h.say(2, "/start"))) == 1
    assert not [m for m in h.session.sent(SendMessage) if m.chat_id == ADMIN and "Channel gate" in (m.text or "")]


def test_chat_activity_and_payments_are_not_blocked_by_the_gate(h, ch):
    """The gate guards commands and buttons; it never swallows a payment or counts as a command."""
    gate_on(h, ch)
    h.say(ADMIN, "/start")
    h.db.x("UPDATE users SET cash=cash+0 WHERE id=1")
    h.say(2, "hello everyone", chat=GROUP)                                                           # ordinary chat in a group: handled as before
    assert ch.asked == [] and not h.session.sent(SendDocument)
    paid = h.feed({"message": {"message_id": 9, "date": 0, "chat": {"id": 2, "type": "private"}, "from": {"id": 2, "is_bot": False, "first_name": "P"},
                               "successful_payment": {"currency": "XTR", "total_amount": 1, "invoice_payload": "no-such-item",
                                                      "telegram_payment_charge_id": "c1", "provider_payment_charge_id": "p1"}}})
    assert not prompts(paid)                                                                         # a payment is processed (or reported), never answered with a join prompt


# ================================================================================================ the admin panel
def panel_buttons(msg):
    return [b.callback_data for row in msg.reply_markup.inline_keyboard for b in row]


def test_admin_opens_the_panel_in_private(h):
    calls = h.say(ADMIN, "/admin")
    m = calls[-1]
    assert isinstance(m, SendMessage)
    cb = panel_buttons(m)
    assert cb == ["ad:chan:1", "ad:bk:1"] and all(len(c.encode()) <= 64 for c in cb)
    assert FA["ad.chan_none"] in m.text and fmt.dur(86400, "fa") in m.text
    h.db.x("UPDATE users SET lang='en' WHERE id=1")
    m2 = h.say(ADMIN, "/admin")[-1]
    assert "Admin panel" in m2.text and "not set" in m2.text and "1d" in m2.text


def test_regular_players_get_silence_for_admin_in_every_form(h):
    h.say(2, "/start")
    for text in ("/admin", "/admin on", "admin", "/admin@TestBot", "!admin", "ADMIN"):
        assert h.say(2, text) == [], text
        assert h.say(2, text, chat=GROUP) == [], text
    assert h.say(3, "/admin") == []                                                                  # not even registered


def test_the_panel_never_opens_in_a_group_not_even_for_an_admin(h):
    calls = h.say(ADMIN, "/admin", chat=GROUP)
    assert len(calls) == 1 and FA["ad.private_only"] in calls[0].text and not calls[0].reply_markup


def test_forged_admin_buttons_do_nothing_for_regular_players(h, ch):
    h.say(2, "/start")
    before = (gate.get(h.db), dict(h.app.pending), len(docs_sent(h)))
    for sub in ("main", "chan", "bk", "ask", "on", "off", "check", "rm", "rmok", "bknow", "zzz"):
        calls = h.press(2, f"ad:{sub}:2")                                                            # their own id as the suffix: passes the owner check
        assert [c for c in toasts(calls)] and not [c for c in calls if isinstance(c, (EditMessageText, SendMessage, SendDocument))], sub
        calls = h.press(2, f"ad:{sub}:1")                                                            # the admin's id as the suffix: not theirs
        assert not [c for c in calls if isinstance(c, (EditMessageText, SendDocument))], sub
        calls = h.press(2, f"ad:{sub}:2", chat=GROUP)
        assert not [c for c in calls if isinstance(c, (EditMessageText, SendDocument))], sub
    assert (gate.get(h.db), dict(h.app.pending), len(docs_sent(h))) == before


def test_a_regular_player_cannot_set_the_channel_even_with_a_forged_prompt(h, ch):
    h.say(2, "/start")
    h.app.pending[2] = (adminpanel.PROMPT, core.now() + 300)                                         # as if the prompt had been armed for them
    calls = h.say(2, "@hypechan")
    assert gate.get(h.db) is None and calls == []
    assert 2 not in h.app.pending


def test_admin_sets_the_channel_with_the_guided_prompt(h, ch):
    h.say(ADMIN, "/start")
    ask = h.press(ADMIN, "ad:ask:1")
    e = [c for c in ask if isinstance(c, EditMessageText)][-1]
    assert FA["ad.ask"] in e.text and panel_buttons(e) == ["ad:chan:1"] and h.app.pending[ADMIN][0] == adminpanel.PROMPT
    out = h.say(ADMIN, "@hypechan")
    s = gate.active(h.db)
    assert s and s["chat"] == ch.id and s["username"] == "hypechan" and s["on"]
    last = [c for c in out if isinstance(c, SendMessage)][-1]
    assert "@hypechan" in last.text and "روشن" in last.text and ADMIN not in h.app.pending
    # now a player is asked to join it
    assert prompts(h.say(2, "/start"))
    ch.join(2)
    h.advance(gate.NO_TTL + 1)
    assert not prompts(h.say(2, "/daily"))                                                           # and joining lets them through


def test_the_prompt_accepts_links_and_numeric_ids_and_survives_mistakes(h, ch):
    h.press(ADMIN, "ad:ask:1")
    for bad, key in (("not a channel!!", "ad.err.bad_ref"), ("@nosuchchan", "ad.err.not_found")):
        out = h.say(ADMIN, bad)
        msg = [c for c in out if isinstance(c, SendMessage)][-1]
        assert FA[key] in msg.text and FA["ad.retry"] in msg.text and panel_buttons(msg) == ["ad:chan:1"]
        assert gate.get(h.db) is None and ADMIN in h.app.pending                                    # still waiting for another try
    ch.bot_status = "member"
    out = h.say(ADMIN, "https://t.me/hypechan")
    assert FA["ad.err.bot_not_admin"] in [c for c in out if isinstance(c, SendMessage)][-1].text and gate.get(h.db) is None
    ch.bot_status = "administrator"
    h.say(ADMIN, "-1001234567890")
    assert gate.active(h.db)["chat"] == ch.id
    gate.clear(h.db)
    h.press(ADMIN, "ad:ask:1")
    h.say(ADMIN, "t.me/hypechan")
    assert gate.active(h.db)["chat"] == ch.id


def test_the_prompt_expires_and_other_buttons_cancel_it(h, ch):
    h.press(ADMIN, "ad:ask:1")
    h.advance(adminpanel.PROMPT_TTL + 5)
    assert h.say(ADMIN, "@hypechan") == [] and gate.get(h.db) is None                                # too late: it is just chat
    h.press(ADMIN, "ad:ask:1")
    h.press(ADMIN, "ad:chan:1")                                                                      # «Cancel»
    assert ADMIN not in h.app.pending
    assert h.say(ADMIN, "@hypechan") == [] and gate.get(h.db) is None


def test_a_real_command_typed_during_the_prompt_runs_as_a_command(h, ch):
    h.press(ADMIN, "ad:ask:1")
    calls = h.say(ADMIN, "/menu")
    assert [c for c in calls if isinstance(c, SendMessage)] and gate.get(h.db) is None and ADMIN not in h.app.pending


def test_channel_screen_toggle_check_and_remove(h, ch):
    h.press(ADMIN, "ad:ask:1")
    h.say(ADMIN, "@hypechan")
    screen = lambda calls: [c for c in calls if isinstance(c, EditMessageText)][-1]
    off = screen(h.press(ADMIN, "ad:off:1"))
    assert not gate.active(h.db) and gate.get(h.db) and FA["ad.turned_off"] in off.text and "ad:on:1" in panel_buttons(off)
    assert not prompts(h.say(2, "/start"))
    on = screen(h.press(ADMIN, "ad:on:1"))
    assert gate.active(h.db) and FA["ad.turned_on"] in on.text and "ad:off:1" in panel_buttons(on)
    chk = screen(h.press(ADMIN, "ad:check:1"))
    assert "@hypechan" in chk.text and "✅" in chk.text
    ch.bot_status = "member"                                                                         # the bot lost its admin rights
    bad = screen(h.press(ADMIN, "ad:check:1"))
    assert FA["ad.err.bot_not_admin"] in bad.text and gate.active(h.db)                              # reported; nothing changed silently
    broken = screen(h.press(ADMIN, "ad:on:1"))
    assert FA["ad.err.bot_not_admin"] in broken.text
    ask = screen(h.press(ADMIN, "ad:rm:1"))
    assert "ad:rmok:1" in panel_buttons(ask) and "ad:chan:1" in panel_buttons(ask) and gate.get(h.db)
    h.press(ADMIN, "ad:chan:1")                                                                      # «No»: nothing removed
    assert gate.get(h.db)
    h.press(ADMIN, "ad:rm:1")
    done = screen(h.press(ADMIN, "ad:rmok:1"))
    assert gate.get(h.db) is None and FA["ad.removed"] in done.text
    need = toasts(h.press(ADMIN, "ad:on:1"))
    assert need and need[-1].show_alert and FA["ad.need_chan"] in need[-1].text


def test_changing_the_channel_forgets_who_had_already_joined_the_old_one(h, ch):
    other = Chan(id=-1005555555555, username="otherchan", title="Other")
    install(h, ch, other)
    gate_on(h, ch)
    ch.join(2)
    assert not prompts(h.say(2, "/start"))                                                           # a member of the first channel
    h.press(ADMIN, "ad:ask:1")
    h.say(ADMIN, "@otherchan")
    assert gate.active(h.db)["chat"] == other.id
    assert len(prompts(h.say(2, "/daily"))) == 1                                                     # must join the new one: the old "yes" is gone


def test_backup_screen_shows_the_schedule_and_sends_one_on_demand(h):
    with_settings(h, admins=frozenset({1}), backup_dir="")
    screen = lambda calls: [c for c in calls if isinstance(c, EditMessageText)][-1]
    s = screen(h.press(ADMIN, "ad:bk:1"))
    assert FA["ad.never"] in s.text and FA["ad.now"] in s.text and "ad:bknow:1" in panel_buttons(s)
    sent = screen(h.press(ADMIN, "ad:bknow:1"))
    assert len(docs_sent(h)) == 1 and "✅" in sent.text and backups.last_done(h.db) == 0
    h.run(backups.scheduled(h.app))
    again = screen(h.press(ADMIN, "ad:bk:1"))
    assert backups.when(core.now()) in again.text and backups.when(core.now() + 86400) in again.text


def test_panel_texts_exist_in_both_languages_and_stay_valid_html(h, ch):
    keys = [k for k in FA if k.startswith(("ad.", "gate."))]
    assert keys and set(keys) == {k for k in EN if k.startswith(("ad.", "gate."))}
    for lang in ("fa", "en"):
        h.db.x("UPDATE users SET lang=? WHERE id=1", (lang,)) if core.get_user(h.db, 1) else core.ensure_user(h.db, 1, "a", "Admin", lang=lang)
        h.db.x("UPDATE users SET lang=? WHERE id=1", (lang,))
        h.say(ADMIN, "/admin")
        for data in ("ad:chan:1", "ad:ask:1", "ad:bk:1", "ad:bknow:1", "ad:main:1"):
            h.press(ADMIN, data)
        h.say(ADMIN, "@hypechan")                                                                     # (the harness checks every message's HTML)
        gate.clear(h.db)
        h.press(ADMIN, "ad:ask:1")
        h.say(ADMIN, "@nosuch")
        h.say(ADMIN, "/menu")


def test_a_channel_title_cannot_break_the_message(h):
    evil = Chan(title="<b>x</b> & <script>", username=None, invite_link="https://t.me/+ok")
    install(h, evil)
    h.press(ADMIN, "ad:ask:1")
    h.say(ADMIN, str(evil.id))
    assert gate.active(h.db)["title"] == "<b>x</b> & <script>"
    for text in ("/admin", ):
        h.say(ADMIN, text)
    h.press(ADMIN, "ad:chan:1")
    prompts_ = prompts(h.say(2, "/start"))
    assert len(prompts_) == 1 and "<script>" not in prompts_[0].text                                  # escaped; the harness also validates the HTML


def test_admin_is_registered_everywhere_the_registry_requires():
    assert "admin" in sections.ADMIN_CMDS and "admin" in handlers.HANDLERS
    p = textparse.parse("/admin", None)
    assert p and p.cmd == "admin" and not p.retired
    from hypemarket import docs
    assert "admin" in docs.DOCS and any("admin" in cmds for cid, cmds in docs.CATEGORIES if cid == "botadmin")
    assert "gate" in handlers.CALLBACKS and "ad" in handlers.CALLBACKS and {"ad", "gate"} <= handlers.BOUND


def test_gate_and_admin_callback_data_fit_telegram(h, ch):
    gate_on(h, ch)
    for m in prompts(h.say(2, "/start")):
        assert all(len(c.encode()) <= 64 for c in cbs(m).split())
    for uid in (1, 2 ** 40 + 7):
        for sub in ("main", "chan", "bk", "ask", "on", "off", "check", "rm", "rmok", "bknow"):
            assert len(f"ad:{sub}:{uid}".encode()) <= 64
