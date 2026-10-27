"""v0.18.2: Persian typography. Names and compound words are written the standard way (with the half-space / ZWNJ where it belongs), the same term means
the same thing everywhere, and every older spelling a player might type still works as a command."""
import re

import pytest

from hypemarket import docs, texts, textparse
from hypemarket.texts import EN, FA

ZW = "‌"
# the one place a plain space stays on purpose: the words drawn INTO the map picture (worldmap.MAP_WORD, `map.title` / `map.subtitle`)
IMAGE_KEYS = {"map.subtitle", "map.title"}


def persian_texts():
    for key, value in FA.items():
        yield "FA:" + key, value
    for key, entry in docs.DOCS.items():
        yield "DOC:" + key + ":fa", entry[0]


WRONG = {
    "مک‌دونالد written with a space or joined": r"مک دونالد|مکدونالد|مک ‌دونالد|مک‌ دونالد",
    "ساخت‌وساز": r"ساخت و ساز|ساختوساز|ساخت‌ و ساز|ساخت و‌ساز",
    "فست‌فود": r"فست فود|فستفود",
    "اجاره‌ای": r"اجاره ای|اجارهای|اجاره ‌ای",
    "نقشه‌ی": r"نقشه ی(?![ا-ی])|نقشهی",
    "این‌جا / اون‌جا": r"(?<![ا-ی‌])(این|اون|همین|همون)جا(?![ا-ی])",
    "بی‌اعتماد": r"بی اعتماد",
    "a plural / suffix / prefix left as its own word": r"[ا-ی] (ها|های|ترین)(?![ا-ی])|(?<![ا-ی‌])(می|نمی) (?=[ا-ی]{2})",
    "Arabic letters": "[يك]",
    "Arabic-Indic digits": "[٠-٩]",
    "space before a Persian punctuation mark": r" [؟،؛]",
    "no space after a Persian comma": r"،(?=[ا-ی])",
    "a half-space at an edge": r"(^|\s)‌|‌(\s|$)",
    "two half-spaces": "‌‌",
}


@pytest.mark.parametrize("label", list(WRONG))
def test_no_persian_text_has_this_typography_mistake(label):
    pattern = re.compile(WRONG[label])
    found = []
    for name, text in persian_texts():
        if name[3:] in IMAGE_KEYS:
            continue
        # «دائمی»: the hamza letter is not a joiner of the next word, a plain space after it is right
        for m in pattern.finditer(text.replace("ئ", "ی")):
            found.append((name, text[max(0, m.start() - 12): m.end() + 12]))
    assert not found, found[:8]


def test_the_names_the_spec_asks_for_are_written_the_standard_way():
    corpus = "\n".join(text for _, text in persian_texts())
    for good in ("مک‌دونالد", "ساخت‌وساز", "اجاره‌ای", "این‌جا" if "این‌جا" in corpus else "اون‌جا"):
        assert good in corpus, good
    assert FA["job.mcd.name"] == "مک‌دونالد" and FA["map.lm.mcdonalds"] == "مک‌دونالد"
    assert "ساخت‌وساز" in FA["mp.obj.foot"] and "ساخت‌وساز" in FA["wi.foot"]
    assert "اجاره‌ای" in FA["snapp.terms"] or "اجاره‌ی" in FA["snapp.terms"]


def test_the_image_label_keeps_its_own_spelling():
    """The map picture's label is drawn by the renderer with a plain space (see worldmap.MAP_WORD); the map itself is not part of this change."""
    from hypemarket import worldmap
    assert worldmap.MAP_WORD == "نقشه ی" and "نقشه ی" in FA["map.subtitle"]


def test_new_screens_use_single_spaces_and_one_terminology():
    for key in [k for k in FA if k.startswith(("job.", "snapp.", "mcd.", "muni.", "bj.", "fx.", "cash.", "mp.cas", "e.job", "e.fx"))]:
        text = FA[key]
        assert "  " not in text, key
        for bad in ("کش", "نقدینگی"):
            assert not re.search(rf"(?<![ا-ی]){bad}(?![ا-ی])", text), (key, bad)
    corpus = "\n".join(FA[k] for k in FA if k.startswith(("job.", "snapp.", "mcd.", "muni.", "fx.", "cash.")))
    assert "پول نقد" in corpus and "پول سهام" in corpus                                                    # the two currencies keep their two names


# ============================================================================================== every older spelling still runs
@pytest.mark.parametrize("typed,cmd", [
    ("مک‌دونالد", "mcd"), ("مک دونالد", "mcd"), ("مکدونالد", "mcd"), ("MCDONALDS", "mcd"), ("mcd", "mcd"),
    ("شغل‌ها", "jobs"), ("شغل ها", "jobs"), ("مشاغل", "jobs"), ("شغل", "jobs"),
    ("اسنپ", "snapp"), ("شهرداری", "muni"),
])
def test_every_spelling_of_a_job_command_is_understood(typed, cmd):
    parsed = textparse.parse(typed)
    assert parsed is not None and parsed.cmd == cmd, typed


@pytest.mark.parametrize("typed", ["سهام بی‌اعتماد SPEED", "سهام بی اعتماد SPEED", "سهام بیاعتماد SPEED"])
def test_the_distrust_command_accepts_all_its_spellings(typed):
    parsed = textparse.parse(typed)
    assert parsed is not None and parsed.cmd == "distrust" and parsed.args == ["SPEED"]


def test_the_command_a_message_tells_the_player_to_type_works_as_typed():
    from hypemarket.views import cmd_name
    assert cmd_name("fa", "mcd") == "مک‌دونالد" and cmd_name("en", "mcd") == "mcdonalds"
    hint = re.search(r"<code>(.+?)</code>", FA["n.shift_ready"]).group(1)
    assert hint == "مک‌دونالد" and textparse.parse(hint).cmd == "mcd"
    assert textparse.parse(re.search(r"<code>(.+?)</code>", EN["n.shift_ready"]).group(1)).cmd == "mcd"
    for key, entry in docs.DOCS.items():                                                                  # every example of the help is a command that really parses
        for line in entry[1].split("\n"):
            if key in ("jobs", "snapp", "mcd", "muni", "fx", "cash", "map"):
                assert textparse.parse(line) is not None, (key, line)


def test_the_doc_texts_of_the_jobs_describe_the_text_first_screens():
    fa, ex, en, ex_en = docs.DOCS["jobs"]
    assert "متنی" in fa and "صحنه" not in fa and "scene" not in en.lower() and "text" in en
    assert "صحنه" not in docs.DOCS["snapp"][0] and "۴۰ km/h" in docs.DOCS["snapp"][0] and "40 km/h" in docs.DOCS["snapp"][2]
    assert "واحد" not in docs.DOCS["snapp"][0] and "units" not in docs.DOCS["snapp"][2]
