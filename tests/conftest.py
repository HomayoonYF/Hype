"""Test configuration.

v0.18.1 made English digits the shipped default (config.digits_mode(): env DIGITS, default «en»). The long-standing suites assert the exact Persian
text of the bot («۱٬۰۰۰ 🪙»), so they run in the legacy mode `DIGITS=fa`, which is also the switch that brings the old look back. The English default is
covered by tests/test_digits.py, which flips the switch per test (monkeypatch), including a crawl of the screens that looks for any leftover Persian digit.
"""
import os

os.environ.setdefault("DIGITS", "fa")
