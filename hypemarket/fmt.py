"""Number/text formatting for Persian (RTL) and English output."""
from __future__ import annotations

import html

from . import config as C

FA_DIGITS = str.maketrans("0123456789", "۰۱۲۳۴۵۶۷۸۹")
EN_DIGITS = str.maketrans("۰۱۲۳۴۵۶۷۸۹٠١٢٣٤٥٦٧٨٩٬٫٪", "01234567890123456789,.%")      # Persian and Arabic-Indic digits and their signs -> 0-9 , . %
LRM = "\u200e"


def digits(s):
    """The ONE place the number style of everything the bot says is decided (v0.18.1). With config.digits_mode() == 'en' (the default) any Persian /
    Arabic-Indic digit and the signs ٬ ٫ ٪ in `s` become 0-9 , . %; with 'fa' (env DIGITS=fa, the old look) the text is left exactly as written.
    Applied to every template, every number formatter, every button label and every outgoing message, so a hard-coded «۱۰۰۰» says «1000» too."""
    if not s or not isinstance(s, str) or C.digits_mode() != "en":
        return s
    return s.translate(EN_DIGITS)


def esc(x) -> str:
    return html.escape(str(x), quote=False)


def loc(s: str, lang: str) -> str:
    """Localize digits and separators of an already formatted number."""
    if lang == "fa" and C.digits_mode() == "fa":
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


def coins_x(units: int, lang: str) -> str:
    """Stock money shown EXACTLY (never rounded): whole coins, plus up to two decimals when the amount has a fraction. Used where the exact
    amount matters, i.e. on the exchange screens."""
    units = int(units)
    sign = "-" if units < 0 else ""
    a = abs(units)
    whole, frac = divmod(a, C.UNIT)
    s = f"{whole:,}" + (("." + f"{frac:02d}".rstrip("0")) if frac else "")
    return f"{sign}{loc(s, lang)} 🪙"


def usd(cents: int, lang: str) -> str:
    """Life cash: always with the $ sign and two decimals («$100.00», «$۰٫۴۵»); never mixed up with a 🪙 amount. Exact integer arithmetic."""
    cents = int(cents)
    sign = "-" if cents < 0 else ""
    a = abs(cents)
    return f"{sign}${loc(f'{a // 100:,}.{a % 100:02d}', lang)}"


def clock(ts: int, lang: str) -> str:
    """A time of day for a timestamp, in the configured display zone («۱۴:۳۰»); the zone is named by the caller's text."""
    mins = (int(ts) // 60 + C.display_utc_offset()) % 1440
    return loc(f"{mins // 60:02d}:{mins % 60:02d}", lang)


def price(units: int, lang: str) -> str:
    """Prices of single shares are shown with two decimals (balances are rounded harder)."""
    v = units / C.UNIT
    s = f"{v:,.2f}".rstrip("0").rstrip(".") if v < 1000 else f"{round(v):,}"
    return f"{loc(s, lang)} 🪙"


def pct(p: float, lang: str, arrows: bool = True) -> str:
    sign = "٪" if lang == "fa" and C.digits_mode() == "fa" else "%"
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
