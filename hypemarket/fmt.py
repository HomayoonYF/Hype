"""Number/text formatting for Persian (RTL) and English output."""
from __future__ import annotations

import html

from . import config as C

FA_DIGITS = str.maketrans("0123456789", "۰۱۲۳۴۵۶۷۸۹")
LRM = "\u200e"


def esc(x) -> str:
    return html.escape(str(x), quote=False)


def loc(s: str, lang: str) -> str:
    """Localize digits and separators of an already formatted number."""
    if lang == "fa":
        return s.translate(FA_DIGITS).replace(",", "٬").replace(".", "٫")
    return s


def num(n, lang: str) -> str:
    return loc(f"{int(n):,}", lang)


def coins(units: int, lang: str) -> str:
    v = units / C.UNIT
    a = abs(v)
    if a >= 10_000_000:
        s = f"{v / 1_000_000:,.1f}M"
    elif a >= 1000:
        s = f"{round(v):,}"
    else:
        s = f"{v:,.2f}".rstrip("0").rstrip(".")
    return f"{loc(s, lang)} 🪙"


def price(units: int, lang: str) -> str:
    """Prices of single shares are shown with two decimals (balances are rounded harder)."""
    v = units / C.UNIT
    s = f"{v:,.2f}".rstrip("0").rstrip(".") if v < 1000 else f"{round(v):,}"
    return f"{loc(s, lang)} 🪙"


def pct(p: float, lang: str, arrows: bool = True) -> str:
    sign = "٪" if lang == "fa" else "%"
    body = loc(f"{abs(p):,.1f}", lang) + sign
    if not arrows:
        return body
    return ("🟢▲ " if p >= 0 else "🔴▼ ") + body


def pnl(units: int, lang: str) -> str:
    return ("🟢▲ " if units >= 0 else "🔴▼ ") + coins(abs(units), lang)


def tk(ticker: str) -> str:
    """Ticker that keeps its '$' on the correct side inside right-to-left text."""
    return f"{LRM}${esc(ticker)}{LRM}"


def dur(seconds: int, lang: str) -> str:
    seconds = max(0, int(seconds))
    d, rem = divmod(seconds, 86400)
    h, rem = divmod(rem, 3600)
    m, s = divmod(rem, 60)
    parts = []
    if lang == "fa":
        if d:
            parts.append(f"{num(d, lang)} روز")
        if h:
            parts.append(f"{num(h, lang)} ساعت")
        if m and not d:
            parts.append(f"{num(m, lang)} دقیقه")
        if not parts:
            parts.append(f"{num(s, lang)} ثانیه")
        return " و ".join(parts)
    if d:
        parts.append(f"{d}d")
    if h:
        parts.append(f"{h}h")
    if m and not d:
        parts.append(f"{m}m")
    if not parts:
        parts.append(f"{s}s")
    return " ".join(parts)


def bar(frac: float, width: int = 10) -> str:
    frac = max(0.0, min(1.0, frac))
    full = int(round(frac * width))
    return "▰" * full + "▱" * (width - full)
