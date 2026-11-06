"""Retro 2D pixel art for the business world (v0.211): storefront pictures, the «for sale» lot, and the little sprites the city map draws for shops, lots for sale, the cotton site and a styled
player. EVERYTHING here is original art drawn from small tables in this file (a 5x7 letter font, a few 9x9 sprites and flat colours): no logo, no picture and no font of any real brand or any
asset pack is used, a brand is only ever its NAME written in the pixel font on a sign. Pure functions: no database, no Telegram, nothing stored.

THE LOOK follows the branch's sign upgrade (upgrades.SIGN_STYLES): plain (cream board), green (dark green), neon (a night scene, a glowing board) and two (a gold-and-navy board). The window shows
what the branch really is (clothes for a shop, a sewing machine for a workshop) and a camera appears when security cameras are installed. A picture is drawn on a small grid and scaled up with
NEAREST so it stays crisp pixel art; nothing is smoothed.
"""
from __future__ import annotations

import io

from PIL import Image, ImageDraw

SCALE = 5                                                         # picture pixels per art pixel
W, H = 160, 96                                                    # the art grid of a storefront picture

# ---------------------------------------------------------------------------------------------------------------
#                                                   the letter font (5 x 7)
# ---------------------------------------------------------------------------------------------------------------
_G = {
    "A": ".###./#...#/#...#/#####/#...#/#...#/#...#", "B": "####./#...#/#...#/####./#...#/#...#/####.", "C": ".###./#...#/#..../#..../#..../#...#/.###.",
    "D": "####./#...#/#...#/#...#/#...#/#...#/####.", "E": "#####/#..../#..../####./#..../#..../#####", "F": "#####/#..../#..../####./#..../#..../#....",
    "G": ".###./#...#/#..../#.###/#...#/#...#/.###.", "H": "#...#/#...#/#...#/#####/#...#/#...#/#...#", "I": ".###./..#../..#../..#../..#../..#../.###.",
    "J": "..###/...#./...#./...#./...#./#..#./.##..", "K": "#...#/#..#./#.#../##.../#.#../#..#./#...#", "L": "#..../#..../#..../#..../#..../#..../#####",
    "M": "#...#/##.##/#.#.#/#.#.#/#...#/#...#/#...#", "N": "#...#/##..#/#.#.#/#..##/#...#/#...#/#...#", "O": ".###./#...#/#...#/#...#/#...#/#...#/.###.",
    "P": "####./#...#/#...#/####./#..../#..../#....", "Q": ".###./#...#/#...#/#...#/#.#.#/#..#./.##.#", "R": "####./#...#/#...#/####./#.#../#..#./#...#",
    "S": ".####/#..../#..../.###./....#/....#/####.", "T": "#####/..#../..#../..#../..#../..#../..#..", "U": "#...#/#...#/#...#/#...#/#...#/#...#/.###.",
    "V": "#...#/#...#/#...#/#...#/#...#/.#.#./..#..", "W": "#...#/#...#/#...#/#.#.#/#.#.#/##.##/#...#", "X": "#...#/#...#/.#.#./..#../.#.#./#...#/#...#",
    "Y": "#...#/#...#/.#.#./..#../..#../..#../..#..", "Z": "#####/....#/...#./..#../.#.../#..../#####",
    "0": ".###./#...#/#..##/#.#.#/##..#/#...#/.###.", "1": "..#../.##../..#../..#../..#../..#../.###.", "2": ".###./#...#/....#/...#./..#../.#.../#####",
    "3": "####./....#/....#/.###./....#/....#/####.", "4": "...#./..##./.#.#./#..#./#####/...#./...#.", "5": "#####/#..../####./....#/....#/#...#/.###.",
    "6": ".###./#..../#..../####./#...#/#...#/.###.", "7": "#####/....#/...#./..#../.#.../.#.../.#...", "8": ".###./#...#/#...#/.###./#...#/#...#/.###.",
    "9": ".###./#...#/#...#/.####/....#/....#/.###.", "#": ".#.#./.#.#./#####/.#.#./#####/.#.#./.#.#.", "-": "...../...../...../#####/...../...../.....",
    ".": "...../...../...../...../...../.##../.##..", "&": ".##../#..#./.##../.#.#./#..##/#..#./.##.#", "'": "..#../..#../...../...../...../...../.....",
    " ": "...../...../...../...../...../...../.....",
}
FONT = {ch: [row for row in g.split("/")] for ch, g in _G.items()}
LETTER_W, LETTER_H = 5, 7


def text_width(s: str, scale: int = 1) -> int:
    n = len(s)
    return 0 if n == 0 else (n * (LETTER_W + 1) - 1) * scale


def clean(s: str, limit: int = 24) -> str:
    """Only what the font can draw: letters (upper-cased), digits, space and . - # & ' ; anything else becomes a space; runs of spaces collapse."""
    out = "".join(ch if ch in FONT else " " for ch in (s or "").upper())
    return " ".join(out.split())[:limit]


def draw_text(d: ImageDraw.ImageDraw, x: int, y: int, s: str, color, scale: int = 1) -> int:
    """Draw `s` with its top-left corner at (x, y); returns the width used."""
    cx = x
    for ch in s:
        rows = FONT.get(ch, FONT[" "])
        for ry, row in enumerate(rows):
            for rx, v in enumerate(row):
                if v == "#":
                    d.rectangle([cx + rx * scale, y + ry * scale, cx + (rx + 1) * scale - 1, y + (ry + 1) * scale - 1], fill=color)
        cx += (LETTER_W + 1) * scale
    return cx - x - scale


def fit_scale(s: str, room: int, tall: int, biggest: int = 3) -> int:
    """The biggest scale (1..biggest) at which `s` fits `room` wide and `tall` high (1 when nothing bigger does)."""
    best = 1
    for k in range(1, biggest + 1):
        if text_width(s, k) <= room and LETTER_H * k <= tall:
            best = k
    return best


def center_text(d, box: tuple, s: str, color, biggest: int = 3) -> None:
    x0, y0, x1, y1 = box
    k = fit_scale(s, x1 - x0 + 1, y1 - y0 + 1, biggest)
    w = text_width(s, k)
    draw_text(d, x0 + (x1 - x0 + 1 - w) // 2, y0 + (y1 - y0 + 1 - LETTER_H * k) // 2, s, color, k)


# ---------------------------------------------------------------------------------------------------------------
#                                                   palettes (one per sign style)
# ---------------------------------------------------------------------------------------------------------------
PAL = {
    "plain": {"sky": [(150, 196, 226), (170, 208, 232), (190, 220, 238)], "wall": (176, 112, 84), "wall2": (156, 96, 72), "board": (236, 226, 196), "ink": (52, 44, 40), "edge": (96, 78, 64),
              "aw1": (198, 70, 62), "aw2": (240, 232, 214), "glass": (150, 200, 214), "door": (92, 62, 46), "night": False, "glow": None},
    "green": {"sky": [(150, 196, 226), (170, 208, 232), (190, 220, 238)], "wall": (150, 126, 96), "wall2": (132, 110, 84), "board": (28, 76, 52), "ink": (214, 244, 214), "edge": (16, 44, 30),
              "aw1": (40, 110, 70), "aw2": (232, 236, 214), "glass": (150, 200, 214), "door": (60, 78, 58), "night": False, "glow": None},
    "neon": {"sky": [(14, 16, 38), (24, 22, 56), (38, 28, 74)], "wall": (54, 48, 78), "wall2": (46, 40, 68), "board": (18, 14, 34), "ink": (255, 90, 200), "edge": (90, 230, 255),
             "aw1": (255, 90, 200), "aw2": (40, 30, 70), "glass": (60, 90, 130), "door": (30, 26, 54), "night": True, "glow": (255, 90, 200)},
    "two": {"sky": [(150, 196, 226), (170, 208, 232), (190, 220, 238)], "wall": (138, 132, 150), "wall2": (120, 114, 134), "board": (30, 44, 92), "ink": (244, 204, 88), "edge": (244, 204, 88),
            "aw1": (30, 44, 92), "aw2": (244, 204, 88), "glass": (150, 200, 214), "door": (36, 40, 76), "night": False, "glow": None},
}
GROUND = (88, 88, 96)
PAVE = (150, 150, 158)
ROAD = (58, 58, 66)
LINE = (232, 214, 96)
GARMENT = ((226, 80, 74), (70, 130, 220), (240, 200, 70), (90, 190, 120))
CLOSED = (170, 60, 60)
OPEN = (70, 190, 110)


def _pal(style: str) -> dict:
    return PAL.get(style) or PAL["plain"]


def _rect(d, x0, y0, x1, y1, fill, outline=None):
    d.rectangle([x0, y0, x1, y1], fill=fill, outline=outline)


# ---------------------------------------------------------------------------------------------------------------
#                                                   small parts
# ---------------------------------------------------------------------------------------------------------------
def _sky(d, pal):
    bands = pal["sky"]
    h = 74
    for i, col in enumerate(bands):
        _rect(d, 0, i * h // len(bands), W - 1, (i + 1) * h // len(bands), col)
    if pal["night"]:
        for i, (x, y) in enumerate(((10, 6), (30, 14), (52, 4), (80, 9), (110, 5), (136, 12), (150, 4), (20, 24), (124, 22))):
            d.point((x, y), fill=(250, 250, 255) if i % 2 else (160, 190, 255))
        _rect(d, 138, 8, 146, 16, (235, 235, 210))
        _rect(d, 141, 8, 146, 16, pal["sky"][1])
    else:
        _rect(d, 8, 8, 18, 14, (255, 236, 150))
        for x, y, w in ((40, 10, 16), (96, 6, 20), (128, 16, 14)):
            _rect(d, x, y, x + w, y + 3, (250, 250, 252))
            _rect(d, x + 3, y - 2, x + w - 3, y, (250, 250, 252))


def _ground(d):
    _rect(d, 0, 74, W - 1, 82, PAVE)
    for x in range(0, W, 12):
        d.line([(x, 74), (x, 82)], fill=(134, 134, 142))
    _rect(d, 0, 83, W - 1, H - 1, ROAD)
    for x in range(4, W, 22):
        _rect(d, x, 90, x + 10, 91, LINE)


def _wall(d, pal, x0, y0, x1, y1):
    _rect(d, x0, y0, x1, y1, pal["wall"])
    for row, y in enumerate(range(y0 + 2, y1, 5)):
        d.line([(x0, y), (x1, y)], fill=pal["wall2"])
        for x in range(x0 + (0 if row % 2 else 5), x1, 10):
            d.line([(x, y), (x, min(y + 4, y1))], fill=pal["wall2"])
    _rect(d, x0 - 2, y0 - 4, x1 + 2, y0 - 1, pal["edge"])


def _garment(d, x, y, k):
    col = GARMENT[k % len(GARMENT)]
    kind = k % 3
    d.line([(x + 3, y), (x + 3, y + 1)], fill=(60, 60, 66))
    d.line([(x, y + 2), (x + 6, y + 2)], fill=(60, 60, 66))
    if kind == 0:                                                   # a shirt
        _rect(d, x + 1, y + 3, x + 5, y + 9, col)
        _rect(d, x - 1, y + 3, x + 7, y + 5, col)
    elif kind == 1:                                                 # trousers
        _rect(d, x + 1, y + 3, x + 5, y + 6, col)
        _rect(d, x + 1, y + 7, x + 2, y + 11, col)
        _rect(d, x + 4, y + 7, x + 5, y + 11, col)
    else:                                                           # a cap on a shelf
        _rect(d, x, y + 6, x + 6, y + 8, col)
        _rect(d, x + 1, y + 4, x + 5, y + 6, col)


def _machine(d, x, y):
    _rect(d, x, y + 8, x + 14, y + 10, (70, 70, 80))
    _rect(d, x + 2, y + 2, x + 6, y + 8, (200, 200, 210))
    _rect(d, x + 2, y + 2, x + 12, y + 4, (200, 200, 210))
    _rect(d, x + 11, y + 4, x + 12, y + 8, (120, 120, 130))
    _rect(d, x + 7, y + 5, x + 9, y + 7, (240, 90, 80))


def _window(d, pal, x0, y0, x1, y1, content: str, n: int):
    _rect(d, x0 - 1, y0 - 1, x1 + 1, y1 + 1, pal["edge"])
    _rect(d, x0, y0, x1, y1, pal["glass"])
    d.line([(x0 + 2, y1 - 2), (x0 + 8, y0 + 3)], fill=(220, 240, 248))
    if content == "shop":
        for i in range(min(n, 4)):
            _garment(d, x0 + 4 + i * 9, y0 + 3, i)
        if n == 0:
            center_text(d, (x0, y0 + 6, x1, y0 + 14), "NEW", pal["edge"], 1)
        _rect(d, x0, y1 - 3, x1, y1, (110, 84, 60))
    else:
        for i in range(min(max(n, 1), 2)):
            _machine(d, x0 + 3 + i * 16, y0 + 4)
        _rect(d, x0, y1 - 3, x1, y1, (84, 84, 94))


def _awning(d, pal, x0, y, x1):
    for i, x in enumerate(range(x0, x1, 6)):
        col = pal["aw1"] if i % 2 == 0 else pal["aw2"]
        _rect(d, x, y, min(x + 5, x1), y + 5, col)
        _rect(d, x + 1, y + 6, min(x + 4, x1), y + 7, col)


def _camera(d, x, y):
    _rect(d, x, y, x + 5, y + 3, (40, 40, 46))
    _rect(d, x + 5, y + 1, x + 7, y + 2, (110, 110, 120))
    d.point((x + 1, y + 1), fill=(240, 60, 60))


def _glow(d, box, color):
    x0, y0, x1, y1 = box
    for g in (3, 2, 1):
        c = tuple(int(c * (0.28 + 0.14 * (3 - g))) for c in color)
        d.rectangle([x0 - g, y0 - g, x1 + g, y1 + g], outline=c)


def _image() -> tuple:
    img = Image.new("RGB", (W, H), (0, 0, 0))
    return img, ImageDraw.Draw(img)


def _png(img: Image.Image) -> bytes:
    big = img.resize((img.width * SCALE, img.height * SCALE), Image.NEAREST)
    buf = io.BytesIO()
    big.save(buf, "PNG", optimize=True)
    return buf.getvalue()


# ---------------------------------------------------------------------------------------------------------------
#                                                   the pictures
# ---------------------------------------------------------------------------------------------------------------
def storefront(brand: str, seq: int = 1, sign: str = "plain", mode: str = "retail", *, open_now: bool = True, shown: int = 0, machines: int = 0, cameras: bool = False) -> bytes:
    """The picture of a branch: its name on the sign (the pixel font), the awning and the sign colours of its sign upgrade, a window that shows what it is (`shown` pieces on display for a shop,
    sewing machines for a workshop), the OPEN / CLOSED plate and a camera when security cameras are installed. Returns PNG bytes."""
    pal = _pal(sign)
    img, d = _image()
    _sky(d, pal)
    _wall(d, pal, 12, 18, 147, 74)
    name = clean(brand, 18) or "SHOP"
    board = (26, 22, 133, 40)
    if pal["glow"]:
        _glow(d, board, pal["glow"])
    _rect(d, board[0] - 1, board[1] - 1, board[2] + 1, board[3] + 1, pal["edge"])
    _rect(d, *board, pal["board"])
    center_text(d, (board[0] + 3, board[1] + 2, board[2] - 3, board[3] - 2), name, pal["ink"], 2)
    _awning(d, pal, 18, 44, 141)
    retail, production = mode in ("retail", "both"), mode in ("production", "both")
    left = "shop" if retail else "work"
    _window(d, pal, 22, 55, 60, 72, left, shown if retail else machines)
    _window(d, pal, 100, 55, 138, 72, "work" if production else "shop", machines if production else shown)
    _rect(d, 68, 52, 92, 74, pal["edge"])
    _rect(d, 69, 53, 91, 74, pal["door"])
    _rect(d, 71, 55, 89, 66, pal["glass"] if mode else (60, 60, 66))
    d.point((87, 70), fill=(240, 220, 120))
    d.point((87, 71), fill=(240, 220, 120))
    plate = OPEN if open_now else CLOSED
    _rect(d, 69, 56, 91, 64, plate)
    center_text(d, (69, 57, 91, 63), "OPEN" if open_now else "SHUT", (250, 250, 250), 1)
    if cameras:
        _camera(d, 138, 42)
    if production:
        _rect(d, 130, 8, 136, 20, (96, 84, 80))
        _rect(d, 129, 6, 137, 8, (70, 60, 58))
        for i, (x, y) in enumerate(((134, 3), (136, 1))):
            _rect(d, x, y, x + 2, y + 1, (214, 214, 220))
    _ground(d)
    tag = f"#{max(1, int(seq))}"
    draw_text(d, 80 - text_width(tag) // 2, 76, tag, (44, 44, 52), 1)                    # the street number, on the pavement in front of the door
    return _png(img)


def for_sale(tier_index: int = 0, zone: str = "commercial") -> bytes:
    """An EMPTY shell with a FOR SALE board: a bare outline of a building (bigger for a higher tier), no sign, no window glass, a board on a post. Returns PNG bytes."""
    pal = _pal("plain")
    img, d = _image()
    _sky(d, pal)
    k = max(0, min(4, int(tier_index)))
    w = 52 + k * 14
    x0 = 80 - w // 2
    top = 70 - (28 + k * 6)
    _rect(d, x0, top, x0 + w, 74, (206, 198, 184), (120, 112, 100))
    for x in range(x0 + 4, x0 + w - 3, 8):
        _rect(d, x, top + 6, x + 4, top + 12, (92, 98, 110))
    _rect(d, 76, 54, 84, 74, (80, 70, 60))
    if zone == "industrial":
        _rect(d, x0 + w - 8, top - 10, x0 + w - 3, top, (120, 112, 108))
    _rect(d, 20, 60, 22, 74, (96, 70, 48))
    _rect(d, 6, 38, 36, 61, (236, 226, 196), (96, 70, 48))
    center_text(d, (8, 41, 34, 47), "FOR", (190, 50, 50), 1)
    center_text(d, (8, 50, 34, 56), "SALE", (190, 50, 50), 1)
    _ground(d)
    for x in (100, 112, 124, 136):
        d.line([(x, 74), (x + 2, 70)], fill=(90, 140, 80))
    return _png(img)


# ---------------------------------------------------------------------------------------------------------------
#                                                   map sprites (9 x 9)
# ---------------------------------------------------------------------------------------------------------------
SPRITES = {
    "shop": ["..AAAAA..", ".ABABABA.", ".AAAAAAA.", ".WWWWWWW.", ".WGWDWGW.", ".WGWDWGW.", ".WWWDWWW.", "KKKKKKKKK", "........."],
    "work": ["....C....", "....C.C..", "...WWWWW.", ".WWWWWWW.", ".WGGWGGW.", ".WGGWGGW.", ".WWWWDDW.", "KKKKKKKKK", "........."],
    "sale": ["..SSSSS..", "..SSSSS..", "..SSSSS..", "....P....", "....P....", "....P....", "....P....", "...PPP...", "........."],
    "cotton": ["...W.W...", "..WWWWW..", ".WWWWWWW.", ".WWWWWWW.", "..WWWWW..", "...GGG...", "..G.G.G..", ".G..G..G.", "........."],
}
CROWN = ["Y.Y.Y.Y", "YYYYYYY", "YYRYRYY", "YYYYYYY"]
MARK_PAL = {
    "plain": {"A": (198, 70, 62), "B": (240, 232, 214), "W": (236, 226, 196), "G": (150, 200, 214), "D": (92, 62, 46), "K": (60, 54, 50), "C": (96, 84, 80)},
    "green": {"A": (40, 110, 70), "B": (232, 236, 214), "W": (190, 206, 176), "G": (150, 200, 214), "D": (60, 78, 58), "K": (30, 52, 38), "C": (96, 84, 80)},
    "neon": {"A": (255, 90, 200), "B": (90, 230, 255), "W": (60, 52, 96), "G": (90, 230, 255), "D": (255, 90, 200), "K": (20, 16, 40), "C": (130, 120, 160)},
    "two": {"A": (30, 44, 92), "B": (244, 204, 88), "W": (150, 146, 170), "G": (150, 200, 214), "D": (30, 44, 92), "K": (24, 30, 64), "C": (96, 84, 80)},
}
OTHER_PAL = {"S": (236, 226, 196), "P": (150, 100, 60), "W": (244, 244, 248), "G": (60, 140, 80), "Y": (244, 204, 88), "R": (230, 70, 70)}
OUTLINE = (6, 8, 7)


def blit(img: Image.Image, rows: list, pal: dict, center: tuple, scale: int = 2, outline: bool = True) -> None:
    """Paint a sprite (rows of palette letters, '.' is empty) with its centre at `center` on `img` (RGB, in place), each art pixel `scale` x `scale`, with a dark one-pixel outline so it reads on
    any map colour."""
    d = ImageDraw.Draw(img)
    h, w = len(rows), len(rows[0])
    ox, oy = int(center[0] - w * scale / 2), int(center[1] - h * scale / 2)
    cells = {(x, y) for y, row in enumerate(rows) for x, ch in enumerate(row) if ch != "." and ch in pal}
    if outline:
        for (x, y) in cells:
            for dx in (-1, 0, 1):
                for dy in (-1, 0, 1):
                    if (x + dx, y + dy) not in cells:
                        d.rectangle([ox + (x + dx) * scale, oy + (y + dy) * scale, ox + (x + dx + 1) * scale - 1, oy + (y + dy + 1) * scale - 1], fill=OUTLINE)
    for (x, y) in cells:
        d.rectangle([ox + x * scale, oy + y * scale, ox + (x + 1) * scale - 1, oy + (y + 1) * scale - 1], fill=pal[rows[y][x]])


def marker(img: Image.Image, kind: str, center: tuple, sign: str = "plain", scale: int = 2) -> None:
    """Draw the map sprite of a shop / workshop (coloured by its sign style), a lot for sale, or the cotton site."""
    rows = SPRITES.get(kind)
    if rows is None:
        return
    pal = MARK_PAL.get(sign) or MARK_PAL["plain"]
    if kind in ("sale", "cotton"):
        pal = {**pal, **OTHER_PAL}
    blit(img, rows, pal, center, scale)


def style_mark(img: Image.Image, tier: str, center: tuple, color: tuple) -> None:
    """The style of a player on the map, always readable and never replacing the marker: a plain one has nothing added, simple a thin ring, average a double ring, stylish a ring and a spark,
    iconic a ring and a pixel crown above it."""
    d = ImageDraw.Draw(img)
    cx, cy = int(center[0]), int(center[1])
    rings = {"plain": 0, "simple": 1, "average": 2, "stylish": 2, "iconic": 2}.get(tier, 0)
    for i in range(rings):
        r = 11 + i * 3
        d.ellipse([cx - r, cy - r, cx + r, cy + r], outline=color)
    if tier == "stylish":
        for dx, dy in ((0, -17), (-1, -17), (1, -17), (0, -18), (0, -16)):
            d.point((cx + dx, cy + dy), fill=(244, 204, 88))
    if tier == "iconic":
        blit(img, CROWN, {"Y": (244, 204, 88), "R": (230, 70, 70)}, (cx, cy - 20), 2)
