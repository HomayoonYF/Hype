"""The 2D portrait of a character (v0.20): a real picture drawn from the character's SAVED traits and gender, and from nothing else.

Input: the seven visible traits of `traits.py` (height, build, skin tone, hair colour, hair style, eye colour, face shape) and the gender. Output: an RGB image
(`PW` x `PH`). The drawing is pure geometry: no random numbers, no clock, no Telegram name, no network and no file besides the picture it makes, so the same traits
and gender give the same pixels on every call, after every restart, in every group and city, and two characters that differ in any one trait differ in the picture.
Every trait is visible:

* **height**: the head stands higher in the frame the taller the character is, against a height chart (ruled lines) behind them, and a marker line is drawn at the top of
  the head; the neck is longer for the tall.
* **build**: the width of the shoulders, of the neck, and a little of the face.
* **skin tone**: seven skin colours (face, neck, ears) with a matching shadow tone.
* **hair colour** (five) and **hair style** (straight / wavy / curly: a smooth, a rippled and a curled outline); eyebrows follow the hair colour.
* **eye colour**: the iris (brown / hazel / green / blue).
* **face shape**: oval / round / square / heart, four different outlines of the head.
* **gender**: a woman has long hair behind the shoulders, a softer brow and coloured lips; a man has short hair and a stronger brow and jaw.

It is drawn at three times the size and shrunk, so the edges are smooth. The finished picture of a character is kept in memory (`lru_cache`), never on disk and never in
the database: the saved traits are the only truth, and the picture is derived from them every time the process needs it.
"""
from __future__ import annotations

import math
from functools import lru_cache

from PIL import Image, ImageChops, ImageDraw

from . import traits as T

PW, PH = 360, 440                     # the finished portrait, in pixels
SS = 3                                # drawn at SS times the size, then shrunk
CX = PW / 2
HEAD_H = 160.0                        # the skull's height, top to chin

SKIN = {"very_light": (248, 225, 209), "light": (241, 207, 178), "medium": (228, 183, 148), "olive": (207, 162, 120),
        "tan": (184, 134, 94), "brown": (142, 98, 66), "dark": (98, 64, 44)}
HAIR = {"black": (26, 24, 27), "dark_brown": (62, 41, 31), "brown": (108, 72, 44), "blond": (208, 172, 96), "red": (152, 62, 36)}
BROW = {"black": (22, 20, 22), "dark_brown": (50, 33, 25), "brown": (84, 56, 36), "blond": (150, 118, 62), "red": (120, 50, 30)}
EYES = {"brown": (96, 60, 36), "hazel": (142, 106, 50), "green": (70, 128, 88), "blue": (68, 114, 172)}
HEAD_TOP = {"very_short": 124, "short": 106, "average": 88, "tall": 70, "very_tall": 52}       # y of the top of the head: the taller, the higher
NECK_LEN = {"very_short": 26, "short": 30, "average": 34, "tall": 38, "very_tall": 42}
SHOULDER = {"slim": 102, "average": 126, "sturdy": 150}                                       # half the width of the shoulders
NECK = {"slim": 28, "average": 33, "sturdy": 40}                                              # half the width of the neck
FACE_W = {"slim": -4.0, "average": 0.0, "sturdy": 4.0}                                        # the face is a little narrower / wider with the build
# the right half of the head outline: (share of the head height from the top, share of the full half-width)
PROFILE = {
    "oval":   [(0.0, 0.0), (0.04, 0.42), (0.12, 0.76), (0.26, 0.94), (0.42, 1.0), (0.58, 0.97), (0.72, 0.84), (0.85, 0.62), (0.94, 0.38), (1.0, 0.0)],
    "round":  [(0.0, 0.0), (0.05, 0.56), (0.15, 0.87), (0.30, 1.0), (0.50, 1.0), (0.68, 0.96), (0.82, 0.82), (0.92, 0.62), (0.98, 0.32), (1.0, 0.0)],
    "square": [(0.0, 0.0), (0.05, 0.58), (0.12, 0.86), (0.26, 0.98), (0.46, 1.0), (0.70, 0.99), (0.85, 0.95), (0.95, 0.88), (0.995, 0.70), (1.0, 0.56)],
    "heart":  [(0.0, 0.0), (0.05, 0.56), (0.14, 0.88), (0.27, 1.0), (0.40, 0.96), (0.56, 0.80), (0.72, 0.58), (0.86, 0.32), (0.95, 0.12), (1.0, 0.0)],
}
TIP = {"oval": 0.0, "round": 0.0, "square": 0.56, "heart": 0.0}                              # half-width left at the chin (a flat chin for the square face)

BG_TOP, BG_BOTTOM, CHART = (24, 48, 40), (11, 24, 19), (52, 104, 82)
SHIRT, SHIRT_DARK, SHIRT_EDGE = (34, 78, 58), (20, 50, 36), (74, 132, 104)


# =====================================================================================================================
#                                                       geometry
# =====================================================================================================================
def _mix(a, b, k: float) -> tuple:
    return tuple(int(round(x + (y - x) * k)) for x, y in zip(a, b))


def _shade(c, k: float = 0.82) -> tuple:
    return tuple(int(v * k) for v in c)


def _light(c, k: float = 0.35) -> tuple:
    return _mix(c, (255, 255, 255), k)


def spline(points: list, per: int = 8) -> list:
    """A smooth open curve through `points` (Catmull-Rom)."""
    if len(points) < 3:
        return list(points)
    p = [points[0]] + list(points) + [points[-1]]
    out = []
    for i in range(1, len(p) - 2):
        p0, p1, p2, p3 = p[i - 1], p[i], p[i + 1], p[i + 2]
        for k in range(per):
            t = k / per
            t2, t3 = t * t, t * t * t
            out.append(tuple(0.5 * ((2 * p1[j]) + (-p0[j] + p2[j]) * t + (2 * p0[j] - 5 * p1[j] + 4 * p2[j] - p3[j]) * t2 + (-p0[j] + 3 * p1[j] - 3 * p2[j] + p3[j]) * t3)
                             for j in (0, 1)))
    out.append(tuple(points[-1]))
    return out


class Frame:
    """The geometry of one head: where it is and how wide, derived from the traits only."""

    def __init__(self, tr: dict):
        self.top = float(HEAD_TOP[tr["height"]])
        self.half = {"oval": 55.0, "round": 62.0, "square": 60.0, "heart": 58.0}[tr["face_shape"]] + FACE_W[tr["build"]]
        self.shape = tr["face_shape"]
        self.chin = self.top + HEAD_H
        prof = PROFILE[self.shape]
        right = [(CX + self.half * f, self.top + HEAD_H * t) for t, f in prof]
        self.right = spline(right, 6)
        self.left = [(2 * CX - x, y) for x, y in self.right]

    def y(self, t: float) -> float:
        return self.top + HEAD_H * t

    def half_at(self, t: float) -> float:
        """Half the width of the head at the share `t` of its height."""
        pts = self.right
        yy = self.y(t)
        best = pts[0]
        for (x0, y0), (x1, y1) in zip(pts, pts[1:]):
            if y0 <= yy <= y1 and y1 > y0:
                return (x0 + (x1 - x0) * (yy - y0) / (y1 - y0)) - CX
            best = (x1, y1)
        return best[0] - CX

    def outline(self) -> list:
        return self.right + list(reversed(self.left))


# =====================================================================================================================
#                                                       the drawing
# =====================================================================================================================
class _Canvas:
    def __init__(self):
        self.img = Image.new("RGB", (PW * SS, PH * SS), BG_BOTTOM)
        self.d = ImageDraw.Draw(self.img, "RGBA")

    def pts(self, points):
        return [(x * SS, y * SS) for x, y in points]

    def poly(self, points, fill):
        self.d.polygon(self.pts(points), fill=fill)

    def ellipse(self, cx, cy, rx, ry, fill):
        self.d.ellipse([(cx - rx) * SS, (cy - ry) * SS, (cx + rx) * SS, (cy + ry) * SS], fill=fill)

    def line(self, points, fill, width):
        self.d.line(self.pts(points), fill=fill, width=max(1, int(round(width * SS))), joint="curve")

    def circle_chain(self, points, r, fill):
        for x, y in points:
            self.ellipse(x, y, r, r, fill)

    def overlay(self, paint, clip_points) -> None:
        """Whatever `paint(draw)` draws (in the SS-times-larger pixel space), kept only inside the polygon `clip_points`: shadows that must not spill over the background."""
        layer = Image.new("RGBA", self.img.size, (0, 0, 0, 0))
        paint(ImageDraw.Draw(layer, "RGBA"))
        mask = Image.new("L", self.img.size, 0)
        ImageDraw.Draw(mask).polygon(self.pts(clip_points), fill=255)
        layer.putalpha(ImageChops.multiply(layer.getchannel("A"), mask))
        self.img = Image.alpha_composite(self.img.convert("RGBA"), layer).convert("RGB")
        self.d = ImageDraw.Draw(self.img, "RGBA")


def _background(c: _Canvas, fr: Frame) -> None:
    for y in range(PH * SS):                                       # a vertical gradient
        c.d.line([(0, y), (PW * SS, y)], fill=_mix(BG_TOP, BG_BOTTOM, y / (PH * SS)))
    for k in range(0, 21):                                         # the height chart: ruled lines, a longer one every fifth
        y = 24 + k * 21
        long = k % 5 == 0
        c.line([(0, y), (PW * (0.12 if long else 0.06), y)], CHART + (150 if long else 90,), 1.4 if long else 1.0)
        c.line([(PW, y), (PW * (1 - (0.12 if long else 0.06)), y)], CHART + (150 if long else 90,), 1.4 if long else 1.0)
    c.line([(0, fr.top), (PW, fr.top)], (120, 196, 160, 120), 1.2)  # the marker at the top of the head


def _torso(c: _Canvas, fr: Frame, tr: dict, skin, female: bool) -> None:
    sh = SHOULDER[tr["build"]]
    y0 = fr.chin + NECK_LEN[tr["height"]]                          # the line of the shoulders
    nk = NECK[tr["build"]]
    left = CX - sh
    right = CX + sh
    body = [(CX - nk - 6, y0 - 8), (CX - nk - 36, y0 + 2), (left + 22, y0 + 14), (left + 6, y0 + 36), (left - 2, y0 + 70), (left - 6, PH + 4),
            (right + 6, PH + 4), (right + 2, y0 + 70), (right - 6, y0 + 36), (right - 22, y0 + 14), (CX + nk + 36, y0 + 2), (CX + nk + 6, y0 - 8)]
    c.poly(spline(body, 5), SHIRT)
    c.line(spline(body[1:6], 5), SHIRT_EDGE + (120,), 1.6)
    c.line(spline(list(reversed(body[6:11])), 5), SHIRT_EDGE + (120,), 1.6)
    # a collar: a plain V for a man, a round neckline for a woman
    chest = _shade(skin, 0.88)
    if female:
        neckline = spline([(CX - nk - 5, y0 - 4), (CX - nk + 1, y0 + 17), (CX, y0 + 28), (CX + nk - 1, y0 + 17), (CX + nk + 5, y0 - 4)], 5)
        c.poly(neckline, chest)
        c.line(neckline, SHIRT_EDGE + (150,), 1.6)
    else:
        c.poly([(CX - nk - 5, y0 - 6), (CX - 5, y0 + 40), (CX, y0 + 34), (CX + 5, y0 + 40), (CX + nk + 5, y0 - 6)], chest)
        c.poly([(CX - nk - 12, y0 - 10), (CX - nk - 4, y0 - 8), (CX - 5, y0 + 42), (CX - 9, y0 + 30)], SHIRT_DARK)
        c.poly([(CX + nk + 12, y0 - 10), (CX + nk + 4, y0 - 8), (CX + 5, y0 + 42), (CX + 9, y0 + 30)], SHIRT_DARK)
        c.line([(CX - nk - 5, y0 - 6), (CX - 5, y0 + 40)], SHIRT_EDGE + (160,), 1.5)
        c.line([(CX + nk + 5, y0 - 6), (CX + 5, y0 + 40)], SHIRT_EDGE + (160,), 1.5)


def _neck_top(fr: Frame, nk: float) -> float:
    """Where the neck starts under the jaw: the first place going down where the face is no wider than the neck (the neck then meets the outline, never sticks out beside the chin)."""
    for k in range(60, 101):
        t = k / 100
        if fr.half_at(t) <= nk:
            return fr.y(t) - 5
    return fr.chin - 10


def _neck(c: _Canvas, fr: Frame, tr: dict, skin) -> None:
    nk = NECK[tr["build"]]
    y0 = fr.chin + NECK_LEN[tr["height"]]
    top = _neck_top(fr, nk)
    neck = [(CX - nk, top), (CX + nk, top), (CX + nk + 2, y0 + 2), (CX - nk - 2, y0 + 2)]
    c.poly(neck, _shade(skin, 0.9))
    low = [p for p in fr.outline() if p[1] > fr.y(0.62)]                                          # the shadow the jaw throws on the neck: the lower face, moved down
    c.overlay(lambda dr: dr.polygon([(x * SS, (y + 9) * SS) for x, y in low], fill=_shade(skin, 0.55) + (120,)), neck)


def _hair_back(c: _Canvas, fr: Frame, tr: dict, female: bool) -> None:
    if not female:
        return
    col = _shade(HAIR[tr["hair_color"]], 0.86)
    style = tr["hair_style"]
    y_end = fr.chin + NECK_LEN[tr["height"]] + {"straight": 52, "wavy": 58, "curly": 50}[style]
    wid = fr.half + {"straight": 16, "wavy": 20, "curly": 24}[style]
    c.ellipse(CX, fr.top + 70, wid, 92, col)
    left = [(CX - wid, fr.top + 70), (CX - wid - 2, fr.top + 130), (CX - wid - 6, y_end)]
    right = [(2 * CX - x, y) for x, y in left]
    if style == "straight":
        c.poly(left + [(CX - wid + 8, y_end + 6), (CX + wid - 8, y_end + 6)] + list(reversed(right)), col)
    elif style == "wavy":
        edge = []
        for i in range(0, 21):
            u = i / 20
            edge.append((CX - wid - 6 + 2 * (wid + 6) * u, y_end + 5 * math.sin(u * math.pi * 5)))
        c.poly(left + edge + list(reversed(right)), col)
        for i in range(0, 8):                                                       # waves on the sides
            yy = fr.top + 110 + i * 14
            dx = 4 * math.sin(i * 1.2)
            c.ellipse(CX - wid - 3 + dx, yy, 8, 9, col)
            c.ellipse(CX + wid + 3 - dx, yy, 8, 9, col)
    else:
        c.poly(left + [(CX - wid + 8, y_end), (CX + wid - 8, y_end)] + list(reversed(right)), col)
        r = 11
        for i in range(0, 8):
            yy = fr.top + 96 + i * 15
            c.ellipse(CX - wid - 2, yy, r, r, col)
            c.ellipse(CX + wid + 2, yy, r, r, col)
        for i in range(0, 8):
            xx = CX - wid + 8 + i * (2 * wid - 16) / 7
            c.ellipse(xx, y_end, r, r, col)


def _head(c: _Canvas, fr: Frame, tr: dict, skin, female: bool) -> None:
    ex = fr.half_at(0.50)
    for sgn in (-1, 1):                                                                       # ears
        c.ellipse(CX + sgn * (ex + 1), fr.y(0.52), 8.5, 15.5, _shade(skin, 0.93))
        c.ellipse(CX + sgn * (ex + 2), fr.y(0.52), 3.8, 8.5, _shade(skin, 0.76) + (140,))
    outline = fr.outline()
    c.poly(outline, skin)
    # light from the left: the right side of the face darkens gradually (strips, clipped to the head), the jaw a little more
    def paint(dr):
        for k in range(8):
            x0 = CX + fr.half * (0.15 + 0.11 * k)
            dr.rectangle([x0 * SS, 0, PW * SS, PH * SS], fill=_shade(skin, 0.6) + (11,))
    c.overlay(paint, outline)
    jaw = [p for p in fr.right if p[1] > fr.y(0.66)]
    c.line(jaw, _shade(skin, 0.62) + (70,), 2.2)
    c.line([(2 * CX - x, y) for x, y in jaw], _shade(skin, 0.62) + (45,), 2.0)
    blush = (210, 110, 110, 42 if female else 22)                                            # cheeks
    c.ellipse(CX - fr.half * 0.52, fr.y(0.64), 16, 9, blush)
    c.ellipse(CX + fr.half * 0.52, fr.y(0.64), 16, 9, blush)
    if not female:                                                                           # a man: a stronger jaw line
        c.line([p for p in fr.right if p[1] > fr.y(0.70)], _shade(skin, 0.55) + (60,), 3.0)
        c.line([(2 * CX - x, y) for x, y in fr.right if y > fr.y(0.70)], _shade(skin, 0.55) + (40,), 3.0)


def _face(c: _Canvas, fr: Frame, tr: dict, skin, female: bool) -> None:
    ey = fr.y(0.50)
    dx = fr.half * 0.47
    brow = BROW[tr["hair_color"]]
    iris = EYES[tr["eye_color"]]
    for sgn in (-1, 1):
        x = CX + sgn * dx
        # the eye: the white, the iris, the pupil, a highlight, and the lid line
        shape = spline([(x - 14.5, ey + 1), (x - 7, ey - 5.6), (x + 7, ey - 5.6), (x + 14.5, ey + 1), (x + 7, ey + 6.0), (x - 7, ey + 6.0), (x - 14.5, ey + 1)], 4)
        c.poly(shape, (244, 242, 238))
        c.ellipse(x, ey, 6.2, 6.2, iris)
        c.ellipse(x, ey, 2.9, 2.9, (12, 10, 10))
        c.ellipse(x - 1.6, ey - 2.2, 1.4, 1.4, (255, 255, 255, 235))
        c.line(shape[: len(shape) // 2 + 1], (32, 24, 22, 230), 1.7)
        if female:
            c.line([(x - 14.5, ey + 1), (x - 17, ey - 1.2)] if sgn < 0 else [(x + 14.5, ey + 1), (x + 17, ey - 1.2)], (32, 24, 22, 200), 1.5)
        # the eyebrow
        thick = 3.4 if female else 4.6
        lift = -3.0 if female else 0.0
        by = ey - 15 + lift
        c.line(spline([(x - 15 * 0.95, by + 3), (x - 5, by - 1.5), (x + 6, by - 2.0), (x + 15 * 0.95, by + 2.4)], 5), brow + (255,), thick)
    # the nose
    ny = fr.y(0.67)
    c.line([(CX - 2.0, ey + 6), (CX - 4.0, ny - 6)], _shade(skin, 0.68) + (120,), 2.2)
    c.line(spline([(CX - 9, ny - 1), (CX - 5, ny + 3), (CX, ny + 4), (CX + 5, ny + 3), (CX + 9, ny - 1)], 4), _shade(skin, 0.62) + (190,), 2.0)
    c.ellipse(CX - 4.6, ny + 1.6, 1.7, 1.2, _shade(skin, 0.45) + (200,))
    c.ellipse(CX + 4.6, ny + 1.6, 1.7, 1.2, _shade(skin, 0.45) + (200,))
    # the mouth
    my = fr.y(0.79)
    mw = 15 if female else 14
    if female:
        lip = _mix(skin, (176, 74, 92), 0.72)
        c.poly(spline([(CX - mw, my), (CX - mw / 2, my - 4.4), (CX, my - 2.8), (CX + mw / 2, my - 4.4), (CX + mw, my), (CX, my + 0.8)], 4), lip)
        c.poly(spline([(CX - mw, my), (CX, my + 0.8), (CX + mw, my), (CX + mw / 2, my + 5.6), (CX, my + 7.0), (CX - mw / 2, my + 5.6)], 4), _shade(lip, 1.06))
        c.line([(CX - mw, my), (CX, my + 1.0), (CX + mw, my)], _shade(lip, 0.55) + (230,), 1.3)
    else:
        lip = _shade(_mix(skin, (150, 82, 74), 0.5), 0.9)
        c.line(spline([(CX - mw, my - 0.5), (CX - mw / 2, my + 0.6), (CX + mw / 2, my + 0.6), (CX + mw, my - 0.5)], 4), _shade(lip, 0.6) + (240,), 2.0)
        c.line(spline([(CX - mw * 0.7, my + 4.6), (CX, my + 6.0), (CX + mw * 0.7, my + 4.6)], 4), _shade(skin, 0.7) + (130,), 2.2)


def _hair_front(c: _Canvas, fr: Frame, tr: dict, female: bool) -> None:
    col = HAIR[tr["hair_color"]]
    hi = _light(col, 0.28)
    style = tr["hair_style"]
    t_end = 0.46 if female else 0.44
    off = {"straight": 6.0, "wavy": 8.0, "curly": 11.0}[style] + (0.0 if female else -1.0)
    thick = 9.0 if not female else 7.0

    def outer(side: list) -> list:
        pts = []
        for x, y in side:
            t = (y - fr.top) / HEAD_H
            if t > t_end:
                break
            grow = off * (1.0 if t > 0.12 else 0.8 + 0.2 * t / 0.12)
            sign = 1 if x >= CX else -1
            nx = x + sign * grow
            ny = y - off * max(0.0, 1.0 - t / 0.34)
            if style == "wavy":
                nx += sign * 3.2 * math.sin(t * 38)
            pts.append((nx, ny))
        return pts

    ro, lo = outer(fr.right), outer(fr.left)
    ro[0] = (CX + 2.0, ro[0][1] - 1.0)
    lo[0] = (CX - 2.0, lo[0][1] - 1.0)
    ro_s = spline(ro, 4)
    lo_s = spline(lo, 4)
    jr = ro[-1]
    jl = lo[-1]
    ir = (jr[0] - off - thick, jr[1])
    il = (jl[0] + off + thick, jl[1])
    if female:                                                                              # a centre parting: the hairline sweeps down to the temples
        line = [ir, (CX + fr.half * 0.78, fr.y(0.27)), (CX + fr.half * 0.40, fr.y(0.15)), (CX, fr.y(0.10)),
                (CX - fr.half * 0.40, fr.y(0.15)), (CX - fr.half * 0.78, fr.y(0.27)), il]
    else:                                                                                   # short hair: a straight, higher hairline
        line = [ir, (CX + fr.half * 0.82, fr.y(0.26)), (CX + fr.half * 0.50, fr.y(0.19)), (CX, fr.y(0.17)),
                (CX - fr.half * 0.50, fr.y(0.19)), (CX - fr.half * 0.82, fr.y(0.26)), il]
    cap = list(reversed(lo_s)) + ro_s + spline(line, 5)
    c.poly(cap, col)
    if style == "curly":
        r = 10.0
        ring = []
        for i, (x, y) in enumerate(ro_s[::3] + list(reversed(lo_s))[::3]):
            ring.append((x, y))
        c.circle_chain(ring, r, col)
        crown = [(CX + (i - 3) * 14, fr.top - off * 0.4 + 2 * math.sin(i * 1.7)) for i in range(0, 7)]
        c.circle_chain(crown, r, col)
        front = [(CX + (i - 3) * 17, fr.y(0.15) + 3 * math.sin(i * 2.1)) for i in range(0, 7)]
        c.circle_chain(front, 7.5, col)
    elif style == "wavy":
        front = spline([(CX - fr.half * 0.8, fr.y(0.24)), (CX - fr.half * 0.4, fr.y(0.15)), (CX, fr.y(0.2)), (CX + fr.half * 0.4, fr.y(0.15)), (CX + fr.half * 0.8, fr.y(0.24))], 5)
        c.line(front, col, 5.0)
    if style == "straight":                                                                 # a soft highlight on the crown
        c.line(spline([(CX - fr.half * 0.70, fr.y(0.15)), (CX - fr.half * 0.30, fr.top - off * 0.35 + 5), (CX + fr.half * 0.25, fr.top - off * 0.35 + 6)], 5), hi + (90,), 4.0)


# =====================================================================================================================
#                                                       the picture
# =====================================================================================================================
def check(traits: dict, gender: str) -> None:
    """The traits and gender are complete and in the catalog, or ValueError / TraitError (a damaged row never draws a made-up person)."""
    if gender not in ("male", "female"):
        raise ValueError(f"gender {gender!r}")
    if set(traits) != set(T.TRAIT_NAMES):
        raise T.TraitError("the traits must cover exactly the catalog")
    for name in T.TRAIT_NAMES:
        T._index(name, traits[name])


@lru_cache(maxsize=256)
def _draw(items: tuple, gender: str) -> Image.Image:
    tr = dict(items)
    female = gender == "female"
    fr = Frame(tr)
    skin = SKIN[tr["skin_tone"]]
    c = _Canvas()
    _background(c, fr)
    _hair_back(c, fr, tr, female)
    _torso(c, fr, tr, skin, female)
    _neck(c, fr, tr, skin)
    _head(c, fr, tr, skin, female)
    _face(c, fr, tr, skin, female)
    _hair_front(c, fr, tr, female)
    return c.img.resize((PW, PH), Image.LANCZOS)


def render(traits: dict, gender: str) -> Image.Image:
    """The portrait of a character with these saved `traits` ({trait: code}) and `gender`: a new RGB image of PW x PH pixels every call (the drawing itself is cached)."""
    check(traits, gender)
    return _draw(tuple(sorted(traits.items())), gender).copy()


def cache_clear() -> None:
    _draw.cache_clear()
