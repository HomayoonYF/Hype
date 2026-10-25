"""Small drawn scenes for the job screens (v0.18.1): a picture that says what the player is doing, next to the text.

Everything is drawn with Pillow from simple shapes (no downloaded art, nothing copied from a real brand: the fast-food scene is a generic counter
with a burger, fries and a cap; the taxi is a generic hatchback). The look is the bot's: near-black green with mint and a few warm accents.

    scene(kind, lang, **params) -> PNG bytes     (960 x 540, cached)

kinds: board · snapp_home · snapp_pickup · snapp_done · mcd_door · mcd_work · mcd_ready · mcd_done · muni_home · muni_sweep · muni_waste · muni_green ·
muni_done. Text inside a picture is short on purpose (a title and a status chip); the message caption carries the details. Numbers go through
cards.shape, so they follow the number style switch (config.digits_mode) like everything else.
"""
from __future__ import annotations

import io
import math
import random
from functools import lru_cache

from PIL import Image, ImageDraw

from . import cards

W, H, SS = 960, 540, 2

BG_TOP, BG_BOT = (6, 10, 8), (16, 30, 21)
MINT, MINT_DIM, PALE = (146, 224, 170), (60, 104, 74), (200, 238, 210)
INK = (10, 14, 12)
GLASS = (26, 40, 46)
WARM = (255, 214, 130)
SKIN = (232, 190, 150)
SKIN2 = (196, 148, 112)
RED = (196, 64, 58)
MAROON = (150, 38, 44)
STEEL = (112, 126, 122)
WOOD = (122, 84, 52)
CAR_BODY = {"white": (238, 242, 240), "silver": (184, 192, 194), "black": (46, 52, 50), "blue": (64, 110, 184), "green": (60, 140, 92), "red": (198, 66, 60), "yellow": (232, 190, 52)}


class Canvas:
    """A drawing surface in 960 x 540 coordinates, painted at 2x and shrunk at the end for smooth edges."""

    def __init__(self):
        self.im = Image.new("RGB", (W * SS, H * SS), BG_TOP)
        self.d = ImageDraw.Draw(self.im, "RGBA")

    # -- primitives (all coordinates in the 960 x 540 space) --
    def _p(self, pts):
        return [(x * SS, y * SS) for x, y in pts]

    def rect(self, x0, y0, x1, y1, fill=None, r=0, outline=None, width=1):
        box = [x0 * SS, y0 * SS, x1 * SS, y1 * SS]
        if r:
            self.d.rounded_rectangle(box, r * SS, fill=fill, outline=outline, width=int(width * SS))
        else:
            self.d.rectangle(box, fill=fill, outline=outline, width=int(width * SS))

    def ell(self, cx, cy, rx, ry=None, fill=None, outline=None, width=1):
        ry = rx if ry is None else ry
        self.d.ellipse([(cx - rx) * SS, (cy - ry) * SS, (cx + rx) * SS, (cy + ry) * SS], fill=fill, outline=outline, width=int(width * SS))

    def poly(self, pts, fill=None, outline=None, width=1):
        self.d.polygon(self._p(pts), fill=fill, outline=outline if outline else None)
        if outline and width > 1:
            self.line(list(pts) + [pts[0]], outline, width)

    def line(self, pts, fill, width=1):
        self.d.line(self._p(pts), fill=fill, width=max(1, int(width * SS)), joint="curve")

    def pie(self, cx, cy, rx, ry, a0, a1, fill):
        self.d.pieslice([(cx - rx) * SS, (cy - ry) * SS, (cx + rx) * SS, (cy + ry) * SS], a0, a1, fill=fill)

    def arc(self, cx, cy, rx, ry, a0, a1, fill, width=1):
        self.d.arc([(cx - rx) * SS, (cy - ry) * SS, (cx + rx) * SS, (cy + ry) * SS], a0, a1, fill=fill, width=int(width * SS))

    def glow(self, cx, cy, r, color, alpha=70, steps=14):
        for i in range(steps, 0, -1):
            a = int(alpha * (1 - i / (steps + 1)) ** 2 / 3)
            rr = r * i / steps
            self.ell(cx, cy, rr, rr * 0.9, fill=(*color, max(1, a)))

    def text(self, xy, s, size, fill=PALE, anchor="la", bold=False):
        f = cards.font(int(size * SS), bold)
        self.d.text((xy[0] * SS, xy[1] * SS), cards.shape(s), font=f, fill=fill, anchor=anchor)

    def text_w(self, s, size, bold=False) -> float:
        return self.d.textlength(cards.shape(s), font=cards.font(int(size * SS), bold)) / SS

    def png(self) -> bytes:
        out = self.im.resize((W, H), Image.LANCZOS)
        buf = io.BytesIO()
        out.save(buf, "PNG", compress_level=6)
        return buf.getvalue()


# =====================================================================================================================
#                                                       building blocks
# =====================================================================================================================
def sky(c: Canvas, top=BG_TOP, bottom=BG_BOT, horizon=0.0) -> None:
    for y in range(H * SS):
        k = y / (H * SS)
        col = tuple(int(a + (b - a) * k) for a, b in zip(top, bottom))
        c.d.line([(0, y), (W * SS, y)], fill=col)


def stars(c: Canvas, seed: int, n: int = 40, limit: int = 220) -> None:
    rnd = random.Random(seed)
    for _ in range(n):
        x, y, r = rnd.uniform(0, W), rnd.uniform(0, limit), rnd.choice((0.8, 1.0, 1.3))
        c.ell(x, y, r, fill=(200, 238, 210, rnd.randint(60, 150)))


def skyline(c: Canvas, seed: int, base: int, far=(18, 34, 25), near=(12, 22, 16), windows=True) -> None:
    rnd = random.Random(seed)
    for layer, (col, hmin, hmax, wmin, wmax) in enumerate(((far, 80, 220, 40, 90), (near, 50, 170, 50, 110))):
        x = -20
        while x < W:
            w, h = rnd.randint(wmin, wmax), rnd.randint(hmin, hmax)
            c.rect(x, base - h, x + w, base + 2, fill=col)
            if windows:
                for wy in range(int(base - h + 12), int(base - 8), 18):
                    for wx in range(int(x + 8), int(x + w - 12), 16):
                        if rnd.random() < (0.30 if layer else 0.20):
                            c.rect(wx, wy, wx + 6, wy + 9, fill=(*WARM, 150 if layer else 90))
            x += w + rnd.randint(2, 14)


def chip(c: Canvas, x, y, s, size=26, fg=INK, bg=MINT, pad=14, anchor="l", bold=True) -> float:
    """A rounded status chip with text; (x, y) is its left (or right, anchor 'r') edge and vertical centre. Returns its width."""
    w = c.text_w(s, size, bold) + pad * 2
    h = size + 16
    x0 = x if anchor == "l" else x - w
    c.rect(x0, y - h / 2, x0 + w, y + h / 2, fill=bg, r=h / 2)
    c.text((x0 + w / 2, y), s, size, fg, "mm", bold)
    return w


def banner(c: Canvas, lang: str, title: str, sub: str | None = None) -> None:
    """The title at the top, on the reading side of the language."""
    rtl = lang == "fa"
    x = W - 36 if rtl else 36
    c.rect(0, 0, W, 96, fill=(6, 10, 8, 150))
    c.text((x, 26), title, 40, PALE, "ra" if rtl else "la", True)
    if sub:
        c.text((x, 70), sub, 22, MINT, "ra" if rtl else "la")


def progress_bar(c: Canvas, x0, y0, x1, y1, frac: float, rtl: bool, fill=MINT) -> None:
    frac = max(0.0, min(1.0, frac))
    c.rect(x0, y0, x1, y1, fill=(255, 255, 255, 36), r=(y1 - y0) / 2)
    w = (x1 - x0) * frac
    if w >= (y1 - y0) / 2:
        c.rect(x1 - w if rtl else x0, y0, x1 if rtl else x0 + w, y1, fill=fill, r=(y1 - y0) / 2)


def person(c: Canvas, x, y, s=1.0, shirt=MAROON, skin=SKIN, hair=(40, 28, 24), cap=None, apron=False, pants=(36, 46, 56), arm="down", mirror=False, vest=False) -> None:
    """A friendly flat person standing with feet at (x, y); about 150 * s tall. arm: down / wave / up (both hands up) / hold (forward)."""
    m = -1 if mirror else 1
    X = lambda dx: x + dx * s * m
    Y = lambda dy: y - dy * s
    # legs
    c.rect(X(-14) if not mirror else X(2), Y(52), X(-2) if not mirror else X(14), Y(0), fill=pants, r=4 * s)
    c.rect(X(2) if not mirror else X(-14), Y(52), X(14) if not mirror else X(-2), Y(0), fill=pants, r=4 * s)
    c.rect(X(-17) if not mirror else X(2), Y(5), X(-1) if not mirror else X(17), Y(-3), fill=(20, 24, 24), r=3 * s)
    c.rect(X(1) if not mirror else X(-17), Y(5), X(17) if not mirror else X(-1), Y(-3), fill=(20, 24, 24), r=3 * s)
    # torso
    c.rect(x - 22 * s, Y(112), x + 22 * s, Y(50), fill=shirt, r=10 * s)
    if vest:
        c.rect(x - 22 * s, Y(112), x - 10 * s, Y(50), fill=(236, 200, 52), r=5 * s)
        c.rect(x + 10 * s, Y(112), x + 22 * s, Y(50), fill=(236, 200, 52), r=5 * s)
        c.rect(x - 22 * s, Y(84), x + 22 * s, Y(78), fill=(220, 224, 224))
    if apron:
        c.rect(x - 15 * s, Y(98), x + 15 * s, Y(50), fill=(236, 238, 236), r=5 * s)
    # arms
    if arm == "wave":
        c.line([(X(22), Y(104)), (X(40), Y(120)), (X(44), Y(146))], shirt, 11 * s)
        c.ell(X(44), Y(150), 7 * s, fill=skin)
        c.line([(X(-22), Y(104)), (X(-27), Y(76)), (X(-24), Y(58))], shirt, 11 * s)
        c.ell(X(-24), Y(55), 6.5 * s, fill=skin)
    elif arm == "up":
        c.line([(X(22), Y(104)), (X(36), Y(126)), (X(34), Y(150))], shirt, 11 * s)
        c.line([(X(-22), Y(104)), (X(-36), Y(126)), (X(-34), Y(150))], shirt, 11 * s)
        c.ell(X(34), Y(153), 7 * s, fill=skin)
        c.ell(X(-34), Y(153), 7 * s, fill=skin)
    elif arm == "hold":
        c.line([(X(22), Y(104)), (X(34), Y(84)), (X(48), Y(86))], shirt, 11 * s)
        c.line([(X(-22), Y(104)), (X(-30), Y(84)), (X(-4), Y(88))], shirt, 11 * s)
        c.ell(X(50), Y(86), 6.5 * s, fill=skin)
    else:
        c.line([(X(22), Y(104)), (X(27), Y(76)), (X(24), Y(58))], shirt, 11 * s)
        c.line([(X(-22), Y(104)), (X(-27), Y(76)), (X(-24), Y(58))], shirt, 11 * s)
        c.ell(X(24), Y(55), 6.5 * s, fill=skin)
        c.ell(X(-24), Y(55), 6.5 * s, fill=skin)
    # head
    c.rect(x - 5 * s, Y(118), x + 5 * s, Y(108), fill=skin)
    c.ell(x, Y(132), 19 * s, 21 * s, fill=skin)
    if cap:
        c.pie(x, Y(136), 20 * s, 20 * s, 180, 360, cap)
        c.rect(x + (2 if not mirror else -30) * s, Y(137), x + (30 if not mirror else -2) * s, Y(131), fill=cap, r=3 * s)
    else:
        c.pie(x, Y(135), 20 * s, 21 * s, 180, 360, hair)
    c.ell(x - 6.5 * s, Y(130), 2.0 * s, fill=(30, 24, 22))
    c.ell(x + 6.5 * s, Y(130), 2.0 * s, fill=(30, 24, 22))
    c.arc(x, Y(124), 7 * s, 5 * s, 20, 160, (120, 60, 56), 2 * s)


# =====================================================================================================================
#                                                      cars (side view)
# =====================================================================================================================
def pride(c: Canvas, x, y, scale, color, lights=True, door_open=False) -> None:
    """A small hatchback in side view, front to the RIGHT, wheels resting on y. About 408 * scale wide."""
    body = CAR_BODY.get(color, CAR_BODY["white"]) if isinstance(color, str) else color
    shade = tuple(int(v * 0.74) for v in body)
    light = tuple(min(255, int(v * 1.1) + 8) for v in body)
    X = lambda v: x + v * scale
    Y = lambda v: y - v * scale
    c.ell(X(204), Y(-2), 215 * scale, 11 * scale, fill=(0, 0, 0, 120))                                   # ground shadow
    outline = [(8, 34), (4, 62), (20, 80), (58, 90), (66, 94), (118, 136), (252, 136), (318, 94), (392, 82), (404, 70), (406, 46), (394, 34)]
    c.poly([(X(a), Y(b)) for a, b in outline], fill=body)
    c.poly([(X(a), Y(b)) for a, b in [(8, 34), (4, 62), (12, 62), (396, 58), (406, 46), (394, 34)]], fill=shade)   # darker lower body
    c.line([(X(30), Y(82)), (X(300), Y(86))], light, 2.5 * scale)                                        # shoulder highlight
    glass = [(76, 94), (122, 130), (176, 130), (176, 94)]
    glass2 = [(186, 94), (186, 130), (250, 130), (304, 94)]
    c.poly([(X(a), Y(b)) for a, b in glass], fill=GLASS)
    c.poly([(X(a), Y(b)) for a, b in glass2], fill=GLASS)
    c.line([(X(86), Y(100)), (X(118), Y(124))], (255, 255, 255, 60), 3 * scale)
    c.line([(X(192), Y(100)), (X(222), Y(124))], (255, 255, 255, 60), 3 * scale)
    c.line([(X(181), Y(94)), (X(181), Y(36))], shade, 2.2 * scale)                                          # door seam
    c.rect(X(150), Y(74), X(166), Y(70), fill=shade, r=2 * scale)                                          # door handles
    c.rect(X(206), Y(74), X(222), Y(70), fill=shade, r=2 * scale)
    c.poly([(X(308), Y(96)), (X(322), Y(98)), (X(316), Y(108))], fill=shade)                               # mirror
    c.rect(X(2), Y(76), X(14), Y(56), fill=(190, 50, 50), r=3 * scale)                                     # tail light
    c.poly([(X(388), Y(80)), (X(404), Y(72)), (X(404), Y(58)), (X(386), Y(62))], fill=(255, 244, 200) if lights else (200, 206, 200))
    if lights:
        c.poly([(X(404), Y(72)), (X(404 + 330), Y(130)), (X(404 + 330), Y(-10)), (X(404), Y(58))], fill=(255, 240, 180, 38))
    c.rect(X(396), Y(46), X(408), Y(36), fill=(60, 66, 64), r=2 * scale)                                   # bumper
    c.rect(X(0), Y(40), X(14), Y(34), fill=(60, 66, 64), r=2 * scale)
    for wx in (96, 322):                                                                                   # wheels with arches
        c.ell(X(wx), Y(34), 42 * scale, 42 * scale, fill=(10, 14, 12))
        c.ell(X(wx), Y(34), 33 * scale, fill=(24, 28, 28))
        c.ell(X(wx), Y(34), 20 * scale, fill=(150, 158, 160))
        c.ell(X(wx), Y(34), 7 * scale, fill=(60, 66, 66))
        for k in range(5):
            a = k * 72
            c.line([(X(wx), Y(34)), (X(wx + 20 * math.cos(math.radians(a))), Y(34 + 20 * math.sin(math.radians(a))))], (96, 104, 106), 3 * scale)


def plate(c: Canvas, x, y, text, scale=1.0, lang="fa") -> None:
    """A small Iranian-style plate chip that carries the car's model year (e.g. 1384)."""
    w, h = 190 * scale, 48 * scale
    c.rect(x - w / 2, y - h / 2, x + w / 2, y + h / 2, fill=(244, 246, 244), r=7 * scale, outline=(30, 40, 36), width=2)
    c.rect(x - w / 2, y - h / 2, x - w / 2 + 34 * scale, y + h / 2, fill=(28, 84, 160), r=7 * scale)
    c.text((x - w / 2 + 17 * scale, y), "IR", 15 * scale, (255, 255, 255), "mm", True)
    c.text((x + 16 * scale, y), text, 26 * scale, (20, 24, 22), "mm", True)


def road(c: Canvas, y0=410, curb=True) -> None:
    c.rect(0, y0, W, H, fill=(18, 24, 21))
    c.rect(0, y0 - 16, W, y0, fill=(52, 70, 58))                                    # pavement
    for x in range(-40, W, 120):
        c.rect(x, y0 + 80, x + 70, y0 + 88, fill=(110, 128, 116, 190), r=3)
    c.rect(0, y0, W, y0 + 4, fill=(32, 44, 36))


# =====================================================================================================================
#                                                        props
# =====================================================================================================================
def burger(c: Canvas, x, y, s=1.0) -> None:
    c.pie(x, y - 14 * s, 34 * s, 28 * s, 180, 360, (222, 150, 64))                                         # top bun
    for dx in (-14, 0, 14):
        c.ell(x + dx * s, y - 30 * s, 2.4 * s, 1.4 * s, fill=(250, 232, 190))
    c.rect(x - 36 * s, y - 13 * s, x + 36 * s, y - 6 * s, fill=(110, 190, 90), r=4 * s)                    # lettuce
    c.rect(x - 34 * s, y - 7 * s, x + 34 * s, y + 2 * s, fill=(92, 52, 36), r=4 * s)                       # patty
    c.poly([(x - 34 * s, y + 2 * s), (x + 34 * s, y + 2 * s), (x + 22 * s, y + 10 * s), (x - 22 * s, y + 10 * s)], fill=(250, 204, 21))   # cheese
    c.rect(x - 34 * s, y + 8 * s, x + 34 * s, y + 20 * s, fill=(222, 150, 64), r=7 * s)                    # bottom bun


def fries(c: Canvas, x, y, s=1.0) -> None:
    for i, dx in enumerate((-16, -8, 0, 8, 16)):
        c.rect(x + dx * s - 3 * s, y - (44 + (i % 3) * 6) * s, x + dx * s + 3 * s, y - 8 * s, fill=(246, 206, 92), r=2 * s)
    c.poly([(x - 24 * s, y - 14 * s), (x + 24 * s, y - 14 * s), (x + 18 * s, y + 24 * s), (x - 18 * s, y + 24 * s)], fill=(196, 56, 52))
    c.rect(x - 24 * s, y - 14 * s, x + 24 * s, y - 8 * s, fill=(170, 40, 40))


def cup(c: Canvas, x, y, s=1.0) -> None:
    c.poly([(x - 18 * s, y - 40 * s), (x + 18 * s, y - 40 * s), (x + 14 * s, y + 22 * s), (x - 14 * s, y + 22 * s)], fill=(236, 238, 236))
    c.poly([(x - 17 * s, y - 20 * s), (x + 17 * s, y - 20 * s), (x + 14 * s, y + 22 * s), (x - 14 * s, y + 22 * s)], fill=(196, 56, 52))
    c.rect(x - 21 * s, y - 46 * s, x + 21 * s, y - 38 * s, fill=(210, 214, 210), r=3 * s)
    c.line([(x + 4 * s, y - 46 * s), (x + 10 * s, y - 66 * s)], (250, 250, 250), 3 * s)


def coin_stack(c: Canvas, x, y, s=1.0, n=4) -> None:
    for i in range(n):
        yy = y - i * 9 * s
        c.ell(x, yy + 6 * s, 24 * s, 8 * s, fill=(176, 130, 20))
        c.ell(x, yy, 24 * s, 8 * s, fill=(246, 196, 61))
        c.ell(x, yy, 14 * s, 4.6 * s, outline=(196, 148, 30), width=1.4)


def banknote(c: Canvas, x, y, s=1.0, ang=0.0) -> None:
    pts = [(-40, -20), (40, -20), (40, 20), (-40, 20)]
    ca, sa = math.cos(ang), math.sin(ang)
    P = [(x + (px * ca - py * sa) * s, y + (px * sa + py * ca) * s) for px, py in pts]
    c.poly(P, fill=(120, 196, 140), outline=(60, 120, 80), width=2)
    c.ell(x, y, 11 * s, fill=(90, 160, 112))
    c.text((x, y), "$", int(16 * s), (230, 250, 236), "mm", True)


def broom(c: Canvas, x, y, s=1.0, tilt=-0.35) -> None:
    ca, sa = math.cos(tilt), math.sin(tilt)
    T = lambda px, py: (x + (px * ca - py * sa) * s, y + (px * sa + py * ca) * s)
    c.line([T(0, -120), T(0, 20)], (150, 104, 62), 6 * s)
    c.poly([T(-20, 20), T(20, 20), T(30, 62), T(-30, 62)], fill=(232, 200, 90))
    for k in range(-24, 28, 8):
        c.line([T(k, 24), T(k * 1.2, 60)], (178, 146, 52), 2 * s)


def tree(c: Canvas, x, y, s=1.0, shade=1.0) -> None:
    c.rect(x - 9 * s, y - 70 * s, x + 9 * s, y, fill=(104, 72, 46), r=3 * s)
    g = lambda v: tuple(int(q * shade) for q in v)
    for dx, dy, r in ((0, -128, 54), (-38, -98, 38), (38, -98, 38), (0, -84, 44)):
        c.ell(x + dx * s, y + dy * s, r * s, r * s * 0.92, fill=g((52, 132, 78)))
    for dx, dy, r in ((-14, -138, 22), (22, -120, 18)):
        c.ell(x + dx * s, y + dy * s, r * s, fill=g((78, 164, 100, 190)))


def bin_(c: Canvas, x, y, s=1.0, color=(52, 118, 84), full=True) -> None:
    c.poly([(x - 30 * s, y - 70 * s), (x + 30 * s, y - 70 * s), (x + 24 * s, y), (x - 24 * s, y)], fill=color)
    c.rect(x - 34 * s, y - 80 * s, x + 34 * s, y - 68 * s, fill=tuple(int(v * 0.8) for v in color), r=4 * s)
    for dx in (-12, 0, 12):
        c.line([(x + dx * s, y - 60 * s), (x + dx * 0.8 * s, y - 8 * s)], tuple(int(v * 0.7) for v in color), 3 * s)
    if full:
        c.ell(x - 10 * s, y - 88 * s, 12 * s, fill=(182, 176, 120))
        c.ell(x + 12 * s, y - 86 * s, 9 * s, fill=(130, 150, 170))


def leaves(c: Canvas, seed: int, x0, y0, x1, y1, n=18) -> None:
    rnd = random.Random(seed)
    for _ in range(n):
        x, y = rnd.uniform(x0, x1), rnd.uniform(y0, y1)
        col = rnd.choice(((214, 150, 54), (190, 96, 46), (226, 188, 70)))
        a = rnd.uniform(0, math.pi)
        c.poly([(x, y), (x + 10 * math.cos(a), y + 6 * math.sin(a)), (x + 4, y + 9)], fill=col)


# =====================================================================================================================
#                                                       the scenes
# =====================================================================================================================
def _board(lang: str, title: str, sub: str | None) -> Canvas:
    c = Canvas()
    sky(c)
    stars(c, 5)
    skyline(c, 11, 420)
    road(c, 420)
    # three round job badges
    for i, (cx, kind) in enumerate(((220, "taxi"), (480, "burger"), (740, "broom"))):
        c.glow(cx, 300, 150, MINT, 90)
        c.ell(cx, 300, 98, fill=(14, 30, 21), outline=MINT_DIM, width=3)
        if kind == "taxi":
            pride(c, cx - 78, 330, 0.38, "white", lights=False)
        elif kind == "burger":
            burger(c, cx, 308, 1.5)
        else:
            broom(c, cx + 6, 262, 1.2, -0.5)
    banner(c, lang, title, sub)
    return c


def _snapp_scene(lang: str, mode: str, color: str, plate_text: str, title: str, sub: str | None, pax: int, chip_text: str | None) -> Canvas:
    c = Canvas()
    sky(c)
    stars(c, 21)
    skyline(c, 3, 398)
    road(c, 410)
    if mode == "pickup":
        pride(c, 120, 452, 0.86, color)
        person(c, 640, 408, 1.55, shirt=(70, 120, 168), hair=(34, 24, 20), arm="wave")
        _prop(c, pax, 640, 408, 1.55)
        if plate_text:
            plate(c, 300, 506, plate_text, 0.7)
    else:
        pride(c, 250, 456, 1.1, color)
        if plate_text:
            plate(c, 480, 508, plate_text, 0.85)
        if mode == "done":
            coin_stack(c, 820, 440, 1.5, 5)
            banknote(c, 760, 380, 1.1, -0.25)
            banknote(c, 870, 392, 1.0, 0.3)
    banner(c, lang, title, sub)
    if chip_text:
        chip(c, W - 36 if lang == "fa" else 36, 118, chip_text, 24, INK, MINT, anchor="r" if lang == "fa" else "l")
    return c


def _prop(c: Canvas, pax: int, x, y, s) -> None:
    """What each of the eight passengers carries (the same order as snapp.pax.0 ... 7)."""
    if pax == 0:                                                       # a bag and books
        c.rect(x - 64 * s, y - 62 * s, x - 36 * s, y - 28 * s, fill=(170, 96, 70), r=4 * s)
        c.rect(x - 64 * s, y - 28 * s, x - 36 * s, y - 20 * s, fill=(240, 230, 200))
    elif pax == 1:                                                     # a suitcase
        c.rect(x - 76 * s, y - 50 * s, x - 44 * s, y - 2 * s, fill=(120, 80, 60), r=5 * s)
        c.line([(x - 60 * s, y - 50 * s), (x - 60 * s, y - 70 * s)], STEEL, 3 * s)
    elif pax == 2:                                                     # in a hurry: a watch
        c.ell(x + 62 * s, y - 112 * s, 9 * s, fill=(240, 240, 232), outline=(80, 90, 88), width=2)
    elif pax == 3:                                                     # flowers and sweets
        for dx, col in ((-10, (230, 96, 120)), (0, (250, 204, 21)), (10, (190, 120, 255))):
            c.ell(x + 62 * s + dx * s, y - 142 * s, 7 * s, fill=col)
        c.rect(x + 50 * s, y - 100 * s, x + 76 * s, y - 80 * s, fill=(236, 200, 170), r=3 * s)
    elif pax == 4:                                                     # shopping bags
        c.poly([(x - 66 * s, y - 56 * s), (x - 36 * s, y - 56 * s), (x - 40 * s, y - 14 * s), (x - 62 * s, y - 14 * s)], fill=(226, 190, 90))
        c.poly([(x + 38 * s, y - 50 * s), (x + 66 * s, y - 50 * s), (x + 62 * s, y - 10 * s), (x + 42 * s, y - 10 * s)], fill=(120, 170, 210))
    elif pax == 5:                                                     # a backpack
        c.rect(x - 36 * s, y - 112 * s, x - 14 * s, y - 62 * s, fill=(60, 150, 96), r=8 * s)
    elif pax == 6:                                                     # a folding bike
        c.ell(x - 70 * s, y - 20 * s, 18 * s, fill=None, outline=(180, 190, 186), width=3)
        c.ell(x - 34 * s, y - 20 * s, 18 * s, fill=None, outline=(180, 190, 186), width=3)
        c.line([(x - 70 * s, y - 20 * s), (x - 52 * s, y - 52 * s), (x - 34 * s, y - 20 * s)], (232, 200, 90), 3 * s)
    else:                                                              # a newspaper
        c.rect(x + 40 * s, y - 100 * s, x + 76 * s, y - 70 * s, fill=(232, 230, 220), r=2 * s)
        for k in range(4):
            c.line([(x + 44 * s, y - (94 - k * 7) * s), (x + 72 * s, y - (94 - k * 7) * s)], (120, 120, 112), 1.5 * s)


def _mcd_inside(lang: str, title: str, sub: str | None, state: str, frac: float, left_text: str | None, chip_text: str | None) -> Canvas:
    c = Canvas()
    sky(c, (20, 14, 10), (42, 28, 18))                                  # a warm indoor wall
    c.rect(0, 0, W, 330, fill=(34, 24, 18))
    # window onto the night street
    c.rect(36, 120, 300, 300, fill=(10, 18, 22), r=10, outline=(110, 90, 60), width=4)
    for i, (bx, bh) in enumerate(((52, 90), (100, 60), (150, 110), (212, 76), (252, 96))):
        c.rect(bx, 296 - bh, bx + 40, 296, fill=(20, 34, 30))
        for wy in range(296 - bh + 8, 290, 16):
            c.rect(bx + 8, wy, bx + 14, wy + 8, fill=(*WARM, 120))
    c.line([(168, 120), (168, 300)], (110, 90, 60), 4)
    # menu board
    c.rect(360, 112, 700, 270, fill=(24, 18, 14), r=10, outline=(110, 90, 60), width=4)
    for i, (mx, kind) in enumerate(((410, "burger"), (530, "fries"), (650, "cup"))):
        (burger if kind == "burger" else fries if kind == "fries" else cup)(c, mx, 196, 0.9)
        c.rect(mx - 38, 232, mx + 38, 240, fill=(190, 160, 110, 150), r=4)
        c.rect(mx - 26, 248, mx + 26, 254, fill=(150, 130, 96, 130), r=3)
    for lx in (330, 600, 830):                                          # warm pendant lamps
        c.line([(lx, 0), (lx, 70)], (90, 70, 50), 3)
        c.glow(lx, 92, 90, WARM, 110)
        c.pie(lx, 82, 26, 18, 180, 360, (210, 170, 90))
        c.ell(lx, 84, 20, 6, fill=(255, 236, 170))
    # the worker stands behind the counter: drawn first, the counter covers the legs
    person(c, 560, 396, 1.7, shirt=MAROON, cap=MAROON, apron=True, arm="hold" if state != "ready" else "up")
    c.rect(0, 360, W, H, fill=(88, 62, 44))
    c.rect(0, 340, W, 372, fill=(176, 186, 182), r=0)
    c.rect(0, 372, W, 380, fill=(120, 130, 126))
    # register + food on the counter
    c.rect(700, 292, 800, 340, fill=(60, 66, 64), r=6)
    c.rect(710, 300, 790, 322, fill=(140, 220, 170), r=3)
    c.text((750, 311), "$", 18, INK, "mm", True)
    c.rect(690, 336, 810, 346, fill=(40, 44, 42), r=3)
    burger(c, 230, 352, 1.15)
    fries(c, 330, 352, 1.0)
    cup(c, 405, 352, 0.95)
    if state == "ready":
        coin_stack(c, 850, 342, 1.3, 5)
        c.glow(850, 300, 90, (250, 204, 21), 140)
    banner(c, lang, title, sub)
    if chip_text:
        chip(c, W - 36 if lang == "fa" else 36, 118, chip_text, 24, INK, MINT if state != "ready" else (250, 204, 21), anchor="r" if lang == "fa" else "l")
    if frac is not None:
        rtl = lang == "fa"
        c.rect(0, H - 100, W, H, fill=(6, 10, 8, 205))
        progress_bar(c, 36, H - 44, W - 36, H - 24, frac, rtl, MINT if state != "ready" else (250, 204, 21))
        if left_text:
            c.text((W - 36 if rtl else 36, H - 94), left_text, 24, PALE, "ra" if rtl else "la", True)
    return c


def _mcd_door(lang: str, title: str, sub: str | None, done: bool) -> Canvas:
    c = Canvas()
    sky(c)
    stars(c, 9)
    skyline(c, 7, 380, windows=True)
    road(c, 420)
    # a generic fast-food storefront: glass front, striped awning, a big burger sign
    c.rect(220, 190, 740, 420, fill=(30, 22, 18), r=0)
    c.rect(250, 250, 710, 410, fill=(250, 226, 160, 235), r=6)
    c.rect(250, 250, 710, 410, outline=(110, 90, 60), width=4, r=6)
    c.line([(480, 250), (480, 410)], (110, 90, 60), 4)
    for i in range(16):
        c.poly([(220 + i * 32.5, 190), (220 + (i + 1) * 32.5, 190), (220 + (i + 1) * 32.5 - 8, 246), (220 + i * 32.5 - 8, 246)], fill=MAROON if i % 2 == 0 else (236, 230, 220))
    c.rect(300, 118, 660, 178, fill=(30, 22, 18), r=12, outline=(250, 204, 21), width=3)
    burger(c, 360, 156, 0.8)
    c.text((520, 148), "FAST FOOD" if lang != "fa" else "فست‌فود", 30, (250, 220, 120), "mm", True)
    c.glow(480, 330, 190, WARM, 120)
    person(c, 330, 410, 1.2, shirt=MAROON, cap=MAROON, apron=True, arm="wave")
    if done:
        coin_stack(c, 830, 420, 1.4, 5)
    banner(c, lang, title, sub)
    return c


def _muni(lang: str, kind: str, title: str, sub: str | None, chip_text: str | None, done: bool) -> Canvas:
    c = Canvas()
    sky(c, (6, 10, 8), (18, 34, 24))
    stars(c, 14)
    skyline(c, 17, 398, windows=True)
    if kind == "home":                                                  # city hall with a flag, the three tools in front
        c.rect(300, 220, 660, 410, fill=(46, 66, 52))
        c.poly([(280, 222), (480, 150), (680, 222)], fill=(62, 88, 68))
        for i in range(6):
            c.rect(322 + i * 56, 250, 340 + i * 56, 396, fill=(176, 198, 182), r=4)
        c.rect(430, 330, 530, 410, fill=(28, 40, 32), r=4)
        c.line([(480, 150), (480, 90)], (190, 200, 194), 4)
        c.poly([(482, 92), (540, 104), (482, 118)], fill=MINT)
        road(c, 410)
        broom(c, 110, 380, 1.1, -0.2)
        bin_(c, 850, 408, 1.2)
        tree(c, 180, 410, 0.6)
    else:
        road(c, 410)
        if kind == "sweep":
            leaves(c, 4, 180, 392, 760, 430, 26)
            person(c, 560, 420, 1.55, shirt=(236, 130, 54), vest=True, arm="hold", cap=(236, 130, 54))
            broom(c, 640, 380, 1.2, -0.7)
            c.glow(700, 430, 70, (200, 190, 150), 40)
        elif kind == "waste":
            c.rect(120, 230, 520, 410, fill=(48, 92, 70), r=14)               # a waste truck
            c.rect(520, 280, 640, 410, fill=(58, 112, 84), r=12)
            c.rect(540, 296, 620, 350, fill=GLASS, r=6)
            c.rect(120, 230, 520, 246, fill=(34, 70, 52), r=8)
            for wx in (200, 570):
                c.ell(wx, 410, 36, fill=(10, 14, 12))
                c.ell(wx, 410, 22, fill=(150, 158, 160))
            c.glow(640, 330, 90, (255, 240, 180), 70)
            bin_(c, 730, 410, 1.2)
            person(c, 850, 420, 1.5, shirt=(236, 130, 54), vest=True, arm="hold", cap=(236, 130, 54))
        else:
            tree(c, 360, 412, 1.5)
            tree(c, 600, 412, 1.1, 0.85)
            c.rect(190, 388, 250, 412, fill=(70, 50, 36), r=4)
            person(c, 760, 420, 1.55, shirt=(60, 140, 96), vest=True, arm="hold", cap=(60, 140, 96))
            for k in range(7):                                             # water drops
                c.ell(700 - k * 12, 366 + k * 6, 3.0, 4.4, fill=(130, 200, 255, 200))
            c.rect(660, 350, 700, 378, fill=(130, 150, 160), r=5)
    if done:
        coin_stack(c, 120, 440, 1.3, 4)
    banner(c, lang, title, sub)
    if chip_text:
        chip(c, W - 36 if lang == "fa" else 36, 118, chip_text, 24, INK, MINT, anchor="r" if lang == "fa" else "l")
    return c


KINDS = ("board", "snapp_home", "snapp_pickup", "snapp_done", "mcd_door", "mcd_work", "mcd_ready", "mcd_done", "muni_home", "muni_sweep", "muni_waste", "muni_green", "muni_done")


@lru_cache(maxsize=160)
def _scene(kind: str, lang: str, title: str, sub: str, chip_text: str, car: str, plate_text: str, pax: int, q: int, left_text: str, mkind: str) -> bytes:
    sub = sub or None
    chip_text = chip_text or None
    if kind == "board":
        c = _board(lang, title, sub)
    elif kind in ("snapp_home", "snapp_pickup", "snapp_done"):
        c = _snapp_scene(lang, kind.split("_")[1], car or "white", plate_text, title, sub, pax, chip_text)
    elif kind in ("mcd_door", "mcd_done"):
        c = _mcd_door(lang, title, sub, kind == "mcd_done")
    elif kind in ("mcd_work", "mcd_ready"):
        c = _mcd_inside(lang, title, sub, "ready" if kind == "mcd_ready" else "work", 1.0 if kind == "mcd_ready" else q / 48.0, left_text or None, chip_text)
    elif kind in ("muni_home", "muni_sweep", "muni_waste", "muni_green"):
        c = _muni(lang, kind.split("_")[1], title, sub, chip_text, False)
    elif kind == "muni_done":
        c = _muni(lang, mkind or "sweep", title, sub, chip_text, True)
    else:
        raise ValueError(f"unknown scene {kind!r}")
    return c.png()


def scene(kind: str, lang: str, *, title: str = "", sub: str = "", chip: str = "", car: str = "white", plate_text: str = "", pax: int = 0,
          frac: float = 0.0, left_text: str = "", muni_kind: str = "") -> bytes:
    """The picture for a job screen. Pure and cached: the same arguments give the same bytes. `frac` (0..1) is the shift progress, drawn in
    48 steps (10 minutes of an 8-hour shift)."""
    if kind not in KINDS:
        raise ValueError(f"unknown scene {kind!r}")
    q = max(0, min(48, int(round(float(frac) * 48))))
    return _scene(kind, lang, title, sub, chip, car, plate_text, int(pax) % 8, q, left_text, muni_kind)
