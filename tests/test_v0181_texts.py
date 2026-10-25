"""v0.18.1: the exchange's news and earnings reports speak with the company's own voice. No made-up person (the old CEO names) appears anywhere,
and the statements are written in a plain middle register."""
import json
import random
import re

import pytest

import test_bot as tb
from hypemarket import exchange as X, texts, views
from hypemarket.texts import EN, FA, t

OLD_NAMES = ("کیانا", "ناطقی", "بهرام", "گنجی", "میلاد", "پیکسل", "سورنا", "ژرف", "آریا", "ولتاژ", "نیکان", "حصار",
             "Kiana", "Nateghi", "Bahram", "Ganji", "Milad", "Pixel", "Sorena", "Deepwater", "Aria", "Volt", "Nikan", "Bastion")
KEYS = [co.key for co in X.COMPANIES]


@pytest.fixture()
def h():
    hh = tb.Harness()
    X.ensure_listed(hh.db, random.Random(5))
    return hh


def test_every_company_speaks_for_itself_not_through_a_person():
    assert len(KEYS) == 6
    for key in KEYS:
        fa, en = t("fa", f"ex.{key}.voice"), t("en", f"ex.{key}.voice")
        assert fa == "بیانیه‌ی " + t("fa", f"ex.{key}.name") and en == t("en", f"ex.{key}.name") + " statement"
        assert f"ex.{key}.ceo" not in FA and f"ex.{key}.ceo" not in EN
        for q in ("up1", "up2", "down1", "down2"):
            for lang in ("fa", "en"):
                assert len(t(lang, f"ex.{key}.{q}")) > 10


def test_no_old_name_is_left_in_any_text_or_the_docs():
    blob = json.dumps([FA, EN], ensure_ascii=False)
    for name in OLD_NAMES:
        assert name not in blob, name
    readme = open("README.md", encoding="utf-8").read() if __import__("os").path.exists("README.md") else ""
    for name in OLD_NAMES:
        assert name not in readme, name


def test_the_statements_have_no_slang_and_no_stiff_bookish_words():
    slang = ("می‌ترکن", "بترکون", "گاوصندوق", "پا به فرار", "استوری", "حباب", "!", "؟؟")
    stiff = ("بدینوسیله", "مزبور", "لذا", "بنابراین", "اینجانب", "مقتضی", "می‌باشد", "گردید", "نمایید")
    for key in KEYS:
        for q in ("up1", "up2", "down1", "down2"):
            text = t("fa", f"ex.{key}.{q}")
            assert not any(w in text for w in slang + stiff), (key, q, text)
            assert text.endswith(".") and "  " not in text


def rows_for(ups):
    return [{"key": co.key, "ticker": co.ticker, "wk_pct": 3.2 if ups else -2.4, "jump": 1.1 if ups else -0.9, "metric": 120.0, "prev": 100.0,
             "act": 1 if ups else -1, "q": 1 + (i % 2)} for i, co in enumerate(X.COMPANIES)]


@pytest.mark.parametrize("lang", ["fa", "en"])
@pytest.mark.parametrize("ups", [True, False])
@pytest.mark.parametrize("compact", [True, False])
def test_the_earnings_report_names_companies_not_people(lang, ups, compact):
    text = views.earnings_text(lang, {"week": 43, "rows": rows_for(ups)}, compact=compact)
    for key in KEYS:
        assert t(lang, f"ex.{key}.voice") in text
    assert not any(n in text for n in OLD_NAMES)
    if compact:
        assert len(text) <= 1024


@pytest.mark.parametrize("lang", ["fa", "en"])
def test_headlines_use_the_company_voice(h, lang):
    for co in X.COMPANIES:
        for kind, params in (("ex_up", {"pct": 2.5}), ("ex_down", {"pct": 1.5}), ("ex_earn", {"pct": 3.0, "jump": 1.0, "act": 1})):
            row = {"kind": kind, "params": json.dumps({"key": co.key, "ticker": co.ticker, "q": 2, **params})}
            line = views.exchange_news_line(lang, row)
            assert t(lang, f"ex.{co.key}.voice") in line and co.ticker in line and not any(n in line for n in OLD_NAMES)
            assert "{" not in line and "}" not in line


def test_the_stock_card_quotes_the_company(h):
    rng = random.Random(3)
    for _ in range(3):
        tb.T[0] += 3600
        X.tick(h.db, rng)
    h.advance(X.sched_ts(tb.T[0]) + 1800 - tb.T[0])
    assert X.run_earnings(h.db, random.Random(1)) is not None
    for key in KEYS:
        s = X.stock_row_for(h.db, key) if hasattr(X, "stock_row_for") else h.db.q1("SELECT * FROM stocks WHERE id=?", (X.stock_id(h.db, key),))
        card = views.listed_card(h.db, "fa", s).text
        assert t("fa", f"ex.{key}.voice") in card and not any(n in card for n in OLD_NAMES)
