"""Shareable portfolio card (PNG) with correct Persian shaping.

Persian text needs two extra steps before Pillow can draw it: letters must be joined
(arabic_reshaper) and the line reordered right-to-left (python-bidi). The bundled
Vazirmatn font (SIL Open Font License, see assets/OFL.txt) has the glyphs.
If the optional libraries or font are missing the card falls back to plain text.
"""
from __future__ import annotations

import io
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

try:
    import arabic_reshaper
    from bidi.algorithm import get_display
except Exception:  # pragma: no cover - optional dependency
    arabic_reshaper = None
    get_display = None

ASSETS = Path(__file__).parent / "assets"
W, H = 900, 520
BG_TOP, BG_BOTTOM = (16, 20, 38), (36, 24, 66)
GREEN, RED, WHITE, MUTED, PANEL = (46, 204, 113), (231, 76, 60), (240, 243, 252), (150, 160, 190), (30, 36, 64)


def _font(size: int, bold: bool = False):
    for name in (("Vazirmatn-Bold.ttf" if bold else "Vazirmatn-Regular.ttf"),):
        p = ASSETS / name
        if p.exists():
            try:
                return ImageFont.truetype(str(p), size, layout_engine=ImageFont.Layout.BASIC)
            except Exception:
                pass
    try:
        return ImageFont.load_default(size=size)
    except TypeError:
        return ImageFont.load_default()


def shape(text: str, rtl: bool) -> str:
    """Join Persian letters and put the line in visual order."""
    if not rtl or arabic_reshaper is None or get_display is None:
        return text
    return get_display(arabic_reshaper.reshape(text))


def _gradient() -> Image.Image:
    img = Image.new("RGB", (W, H), BG_TOP)
    px = img.load()
    for y in range(H):
        t = y / H
        row = tuple(int(a + (b - a) * t) for a, b in zip(BG_TOP, BG_BOTTOM))
        for x in range(W):
            px[x, y] = row
    return img


def render_portfolio(*, lang: str, brand: str, name: str, title: str, level_label: str, net_worth: str, subtitle: str,
                     rows: list, footer: str) -> bytes:
    """rows: [(left_text, mid_text, pct_text, pct_value)] (max 4). All strings are already localized."""
    rtl = lang == "fa"
    img = _gradient()
    d = ImageDraw.Draw(img)
    f_brand, f_name, f_big, f_mid, f_small = _font(30, True), _font(44, True), _font(72, True), _font(30), _font(24)
    pad = 48

    def put(xy, text, font, fill, anchor_side):
        """anchor_side: 'start' = reading-direction start (right in Persian, left in English)."""
        s = shape(text, rtl)
        x = (W - pad if anchor_side == "start" else pad) if rtl else (pad if anchor_side == "start" else W - pad)
        ha = ("r" if anchor_side == "start" else "l") if rtl else ("l" if anchor_side == "start" else "r")
        d.text((x, xy[1]), s, font=font, fill=fill, anchor=ha + "a")

    put((0, 26), brand, f_brand, (255, 196, 61), "start")
    put((0, 84), name, f_name, WHITE, "start")
    put((0, 146), f"{level_label} • {title}", f_mid, MUTED, "start")
    put((0, 196), net_worth, f_big, WHITE, "start")
    put((0, 282), subtitle, f_small, MUTED, "start")

    def cell(text, x_side, y0, fill):
        has_fa = any("\u0600" <= ch <= "\u06ff" for ch in text)
        s_ = shape(text, rtl and has_fa)
        x = (W - pad - 14 if x_side == "start" else pad + 14) if rtl else (pad + 14 if x_side == "start" else W - pad - 14)
        ha = ("r" if x_side == "start" else "l") if rtl else ("l" if x_side == "start" else "r")
        d.text((x, y0), s_, font=f_small, fill=fill, anchor=ha + "a")

    y = 330
    for left, mid, pct_text, pct_val in rows[:4]:
        d.rounded_rectangle([pad, y, W - pad, y + 40], radius=12, fill=PANEL)
        cell(left, "start", y + 5, WHITE)
        d.text((W // 2, y + 5), shape(mid, rtl and any("\u0600" <= ch <= "\u06ff" for ch in mid)), font=f_small, fill=MUTED, anchor="ma")
        cell(pct_text, "end", y + 5, GREEN if pct_val >= 0 else RED)
        y += 48
    d.text((W // 2, H - 38), footer, font=f_small, fill=MUTED, anchor="ma")
    out = io.BytesIO()
    img.save(out, "PNG")
    return out.getvalue()
