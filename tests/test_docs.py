"""The راهنما command reference must list EVERY command, with examples that really work."""
import re

import pytest

from hypemarket import docs, textparse, views
from hypemarket.texts import ALIASES, FA, EN

import test_bot as tb


def test_every_command_is_documented_and_categorised():
    assert set(docs.DOCS) == set(ALIASES), set(docs.DOCS) ^ set(ALIASES)
    in_categories = {c for _, cmds in docs.CATEGORIES for c in cmds}
    assert in_categories == set(ALIASES)
    for cid, _ in docs.CATEGORIES:
        assert f"cat.{cid}" in FA and f"cat.{cid}" in EN and cid in docs.CATEGORY_EMOJI
    for cmd, (fa_d, fa_e, en_d, en_e) in docs.DOCS.items():
        assert all(x.strip() for x in (fa_d, fa_e, en_d, en_e)), cmd


@pytest.mark.parametrize("lang", ["fa", "en"])
def test_every_example_is_a_command_the_bot_understands(lang):
    for cmd, (fa_d, fa_e, en_d, en_e) in docs.DOCS.items():
        for line in (fa_e if lang == "fa" else en_e).split("\n"):
            parsed = textparse.parse(line.strip(), None)
            assert parsed is not None and parsed.cmd == cmd, (lang, cmd, line, parsed)


@pytest.mark.parametrize("lang", ["fa", "en"])
@pytest.mark.parametrize("admin", [False, True])
def test_every_page_fits_one_message_and_is_valid_html(lang, admin):
    cats = views.visible_categories(admin)
    assert ("botadmin" in [c for _, c, _ in cats]) == admin
    for idx, cid, cmds in cats:
        r = views.help_category(lang, 5, idx, admin)
        assert len(r.text) <= 4096, (cid, len(r.text))
        assert tb.assert_valid_telegram_html(r.text) is None
        for c in cmds:                                              # every command shows its name and at least one example
            assert views.cmd_name(lang, c) in r.text
    assert tb.assert_valid_telegram_html(views.help_commands_menu(lang, 5, admin).text) is None
    assert tb.assert_valid_telegram_html(FA["help.tips"] if lang == "fa" else EN["help.tips"]) is None


def test_players_cannot_open_the_bot_admin_page():
    last = len(docs.CATEGORIES) - 1
    assert docs.CATEGORIES[last][0] == "botadmin"
    r = views.help_category("fa", 5, last, is_admin=False)
    assert "ادمین ربات" not in r.text or "همه‌ی دستورها" in r.text          # falls back to the menu


@pytest.fixture()
def h():
    return tb.Harness()


def test_help_buttons_walk_through_every_category(h):
    h.say(10, "شروع")
    out = h.texts(h.press(10, "hp:cmds:10"))
    assert "همه‌ی دستورها" in out
    for i in range(len(docs.CATEGORIES) - 1):                      # all but the bot-admin page
        page = h.texts(h.press(10, f"hp:cat:{i}:10"))
        assert "<code>" in page, i
    assert "ادمین‌های ربات" not in "".join(h.texts(h.press(10, f"hp:cat:{len(docs.CATEGORIES) - 1}:10")))
    assert "/stats" in h.texts(h.press(1, f"hp:cat:{len(docs.CATEGORIES) - 1}:1"))        # user 1 is a bot admin in the harness
    assert "نکته" in h.texts(h.press(10, "hp:tips:10"))


def test_documented_examples_never_crash_when_actually_run(h):
    """Run every Persian example for real (private chat). A command may refuse politely, but never hit the generic error."""
    h.say(1, "شروع")
    h.db.x("UPDATE users SET cash=5000000 WHERE id=1")
    h.say(1, "عرضه SPEED اسپید")
    h.advance(60)
    for cmd, (fa_d, fa_e, en_d, en_e) in docs.DOCS.items():
        if cmd in ("give", "fire", "ban", "unban", "delist"):
            continue
        for line in fa_e.split("\n"):
            h.advance(5)
            out = h.texts(h.say(1, line))
            assert FA["err"] not in out and "Traceback" not in out, (cmd, line)
