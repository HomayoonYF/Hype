"""Turns a chat message into (command, arguments).

Commands can be Persian words without "/" (e.g.  خرید 10 SPEED ), English words,
or classic slash commands (/buy 10 SPEED).  Persian typing is messy, so every
word is normalized first: Arabic ي/ك become ی/ک, Persian/Arabic digits become
ASCII, half-spaces (ZWNJ) and diacritics are dropped.
"""
from __future__ import annotations

import re
from dataclasses import dataclass

from .texts import ALIASES

_AR = {"ي": "ی", "ك": "ک", "ى": "ی", "ۀ": "ه", "ة": "ه", "ؤ": "و", "أ": "ا", "إ": "ا", "ٱ": "ا", "ئ": "ی"}
_DIGITS = str.maketrans("۰۱۲۳۴۵۶۷۸۹٠١٢٣٤٥٦٧٨٩", "01234567890123456789")
_DROP = dict.fromkeys(map(ord, "\u064b\u064c\u064d\u064e\u064f\u0650\u0651\u0652\u0640\u200c\u200d\u200e\u200f\u202a\u202b\u202c\u202d\u202e\u2066\u2067\u2068\u2069\ufeff"), None)

ALL_WORDS = {"all", "max", "همه", "حداکثر", "کامل", "کل"}


def key(token: str) -> str:
    """Matching key of one word (digits to ASCII, Persian letters unified, lowercase)."""
    token = token.translate(_DIGITS)
    for a, b in _AR.items():
        token = token.replace(a, b)
    return token.translate(_DROP).lower()


def _build() -> tuple[dict, int]:
    table: dict = {}
    longest = 1
    for cmd, words in ALIASES.items():
        for w in words:
            toks = tuple(key(x) for x in w.split())
            table[toks] = cmd
            longest = max(longest, len(toks))
    return table, longest


TABLE, LONGEST = _build()


@dataclass
class Parsed:
    cmd: str
    args: list          # original (un-normalized) argument words
    prefixed: bool      # started with / ! or .
    words: int = 1      # how many words the command itself used ("ویرایش سهام" = 2)


_PREFIX = re.compile(r"^[/!.]+")


def parse(text: str, botname: str | None = None) -> Parsed | None:
    t = (text or "").strip()
    if not t or len(t) > 400 or "\n" in t[:200] and len(t.splitlines()) > 4:
        return None
    prefixed = False
    m = _PREFIX.match(t)
    if m:
        prefixed = True
        t = t[m.end():].lstrip()
    toks = t.split()
    if not toks:
        return None
    if "@" in toks[0]:
        first, _, name = toks[0].partition("@")
        if botname and name.lower() != botname.lower():
            return None
        toks[0] = first
    keys = [key(x) for x in toks]
    for n in range(min(LONGEST, len(toks)), 0, -1):
        cmd = TABLE.get(tuple(keys[:n]))
        if cmd:
            return Parsed(cmd, toks[n:], prefixed, n)
    return None


# --- argument helpers --------------------------------------------------------------
_NUM = re.compile(r"^(\d+(?:\.\d+)?)([km]?)$")


def to_number(tok: str) -> float | None:
    s = key(tok).replace(",", "").replace("٫", ".").replace("٬", "")
    m = _NUM.match(s)
    if not m:
        return None
    v = float(m.group(1))
    if m.group(2) == "k":
        v *= 1_000
    elif m.group(2) == "m":
        v *= 1_000_000
    return v


def to_int(tok: str) -> int | None:
    v = to_number(tok)
    if v is None or v != int(v):
        return None
    return int(v)


def is_all(tok: str) -> bool:
    return key(tok) in {key(w) for w in ALL_WORDS}


def looks_like_ticker(tok: str) -> bool:
    t = tok.strip().lstrip("$#").upper().translate(str.maketrans("۰۱۲۳۴۵۶۷۸۹", "0123456789"))
    return bool(re.fullmatch(r"[A-Z][A-Z0-9]{1,9}", t))


def split_trade(args: list) -> tuple[str | None, int | None]:
    """Understands '10 SPEED', 'SPEED 10', 'all SPEED', 'SPEED' and returns (ticker, amount).
    amount: a count, 0 for "all/max", or None when missing."""
    ticker, amount = None, None
    for a in args[:3]:
        if is_all(a):
            amount = 0
        elif to_int(a) is not None:
            if amount is None:
                amount = to_int(a)
        elif looks_like_ticker(a) and ticker is None:
            ticker = a
    return ticker, amount
