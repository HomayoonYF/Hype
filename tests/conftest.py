"""Test configuration.

v0.18.1 made English digits the shipped default (config.digits_mode(): env DIGITS, default «en»). The long-standing suites assert the exact Persian
text of the bot («۱٬۰۰۰ 🪙»), so they run in the legacy mode `DIGITS=fa`, which is also the switch that brings the old look back. The English default is
covered by tests/test_digits.py, which flips the switch per test (monkeypatch), including a crawl of the screens that looks for any leftover Persian digit.
"""
import os

os.environ.setdefault("DIGITS", "fa")


import pytest


@pytest.fixture(autouse=True)
def _every_player_has_a_finished_character(request, monkeypatch):
    """v0.19: the life game needs a finished character. The long-standing suites are about everything AFTER that (the map, jobs, the casino, ...), so a player who
    sends anything through the real handlers gets a finished character at once (charkit.complete); the city is still assigned the real way, by the first `مپ`.
    The v0.19 suites (test_v019_*) turn this off and drive the real set-up themselves, and so does any module that sets REAL_CHARACTER_SETUP = True (v0.20: the ID card)."""
    if request.module.__name__.startswith("test_v019") or getattr(request.module, "REAL_CHARACTER_SETUP", False):
        yield
        return
    import charkit
    from hypemarket import handlers
    real = handlers._prepare

    def prepared(app, tg, chat, referrer=None):
        out = real(app, tg, chat, referrer)
        if not tg.is_bot:
            charkit.complete(app.db, tg.id)
        return out
    monkeypatch.setattr(handlers, "_prepare", prepared)
    yield
