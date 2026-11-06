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


@pytest.fixture()
def legacy_catalog():
    """v0.211 seeds no item at all. The suites of v0.21 test the inventory machinery with its 17 old starter items, so they ask for them explicitly: the items are registered for one test
    only (and taken out again), exactly as a test of any other game would register what it needs. Nothing in the game ever grants them."""
    from hypemarket import items, legacy_items
    added = []
    for (iid, fa, en, cat, slot, mx, icon, dfa, den) in legacy_items.LEGACY:
        if iid not in items.ITEMS:
            items.register_item(id=iid, name_fa=fa, name_en=en, category=cat, equippable=slot is not None, slot=slot, stackable=mx > 1, max_stack=mx, icon=icon,
                                description_fa=dfa, description_en=den)
            added.append(iid)
    yield items
    for iid in added:
        items.ITEMS.pop(iid, None)


@pytest.fixture(autouse=True)
def _keep_the_business_hooks():
    """`biz` keeps a module-level list of permission hooks (staff.py registers the one that turns a manager's contract into permissions at import time). Several tests register their own hooks
    and empty the list again; without this, such a test would take the REAL hook away from every test that runs after it in the same process (a manager would silently lose every permission).
    So the list is snapshotted once the real hook is surely registered and put back after each test."""
    from hypemarket import biz, estate, market, staff, stock, tailor  # noqa: F401  (importing them registers their hooks)
    saved, blockers = list(biz._PERM_HOOKS), list(estate._BLOCKERS)
    yield
    biz._PERM_HOOKS[:] = saved
    estate._BLOCKERS[:] = blockers                                           # the same for the list of things that keep a property from being converted
