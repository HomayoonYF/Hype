"""Share cards: nice pictures of a portfolio, a rank or a stock, with the player's invite link on them.

Persian text is joined with arabic-reshaper, reordered with python-bidi and drawn with the bundled Vazirmatn font
(SIL Open Font License, see assets/OFL.txt). The font is loaded with Pillow's BASIC layout engine on purpose: with
the RAQM engine Pillow would shape the text a second time and break the Persian letters on some machines.
Emoji can't be drawn by that font, so they are stripped inside the pictures (captions keep them).
"""
from __future__ import annotations

import io
import re
import time
from functools import lru_cache
from pathlib import Path

import arabic_reshaper
from bidi.algorithm import get_display
from PIL import Image, ImageDraw, ImageFont

from . import achievements, charts, config as C, core, fmt, progress, trust
from .core import display_name, price
from .texts import level_title

FONT_DIR = Path(__file__).parent / "assets"
W, H = 1080, 1350
BG1, BG2, PANEL, LINE = (13, 17, 36), (30, 18, 56), (28, 34, 62), (52, 62, 98)
FG, MUTED, UP, DOWN, ACCENT, GOLD = (240, 244, 255), (150, 162, 196), (46, 214, 128), (244, 88, 88), (96, 165, 250), (255, 196, 61)
TIER = {"bronze": (205, 127, 50), "silver": (192, 200, 214), "gold": (255, 196, 61), "legend": (190, 120, 255)}

LABELS = {
    "fa": {"nw": "ارزش خالص", "rank": "رتبه‌ی کل", "stocks": "سهام‌ها", "ach": "دستاوردها", "streak": "رشته‌ی روزانه", "holdings": "سهام‌های من",
           "none": "هنوز سهامی ندارد", "more": "و {n} مورد دیگر", "coins": "سکه", "level": "سطح", "group_rank": "رتبه در گروه", "rebirths": "تولد دوباره", "rep": "اعتبار",
           "trust": "اعتماد", "price": "قیمت", "mcap": "ارزش بازار", "holders": "سهامدار", "owner": "مالک", "h24": "۲۴ ساعت", "flag": "گزارش کلاهبرداری دارد", "high": "بالاترین ۲۴ ساعت", "low": "پایین‌ترین ۲۴ ساعت",
           "join": "با این لینک شروع کن و {bonus} سکه هدیه بگیر", "buy": "ببین و بخر؛ با این لینک {bonus} سکه هدیه می‌گیری",
           "trusted": "مورد اعتماد", "flagged": "نامطمئن", "neutral": "بدون برچسب", "my_rank": "رتبه‌ی من",
           "kind": {"personal": "شخصی", "meme": "میم", "project": "پروژه", "bluechip": "بلوچیپ", "company": "شرکت", "index": "شاخص گروه", "listed": "بورس"}},
    "en": {"nw": "NET WORTH", "rank": "GLOBAL RANK", "stocks": "STOCKS", "ach": "ACHIEVEMENTS", "streak": "DAILY STREAK", "holdings": "MY HOLDINGS",
           "none": "No holdings yet", "more": "and {n} more", "coins": "coins", "level": "Level", "group_rank": "GROUP RANK", "rebirths": "REBIRTHS", "rep": "REPUTATION",
           "trust": "TRUST", "price": "PRICE", "mcap": "MARKET CAP", "holders": "HOLDERS", "owner": "OWNER", "h24": "24H", "flag": "Reported as a scam", "high": "24H HIGH", "low": "24H LOW",
           "join": "Start with this link and get {bonus} coins free", "buy": "Look and buy: this link gives you {bonus} coins free",
           "trusted": "Trusted", "flagged": "Not trusted", "neutral": "No label", "my_rank": "MY RANK",
           "kind": {"personal": "PERSONAL", "meme": "MEME", "project": "PROJECT", "bluechip": "BLUE CHIP", "company": "COMPANY", "index": "GROUP INDEX", "listed": "LISTED"}},
}
_AR = re.compile(r"[\u0600-\u06FF\u0750-\u077F\uFB50-\uFDFF\uFE70-\uFEFF]")
_EMOJI = re.compile("[\U0001F000-\U0001FFFF\u2600-\u27BF\u2B00-\u2BFF\uFE0F\u200d\u200c]")
_RESHAPER = arabic_reshaper.ArabicReshaper()


@lru_cache(maxsize=64)
def font(size: int, bold: bool = False):
    path = FONT_DIR / ("Vazirmatn-Bold.ttf" if bold else "Vazirmatn-Regular.ttf")
    try:
        return ImageFont.truetype(str(path), size, layout_engine=ImageFont.Layout.BASIC)
    except OSError:
        try:
            return ImageFont.load_default(size=size)
        except TypeError:
            return ImageFont.load_default()


def shape(text: str) -> str:
    text = _EMOJI.sub("", str(text)).strip()
    return get_display(_RESHAPER.reshape(text)) if _AR.search(text) else text


@lru_cache(maxsize=1)
def _background() -> Image.Image:
    img = Image.new("RGB", (W, H), BG1)
    px = ImageDraw.Draw(img)
    for y in range(H):                                           # vertical gradient, drawn once for all cards
        k = y / H
        px.line([(0, y), (W, y)], fill=tuple(int(a + (b - a) * k) for a, b in zip(BG1, BG2)))
    return img


class Card:
    def __init__(self, lang: str):
        self.lang, self.rtl = lang, lang == "fa"
        self.L = LABELS[lang]
        self.img = _background().copy()
        self.d = ImageDraw.Draw(self.img)

    def width(self, s: str, f) -> float:
        return self.d.textlength(shape(s), font=f)

    def fit(self, s: str, f, maxw: int) -> str:
        s = _EMOJI.sub("", str(s)).strip()
        if self.width(s, f) <= maxw:
            return s
        while len(s) > 1 and self.width(s + "…", f) > maxw:
            s = s[:-1]
        return s + "…"

    def text(self, xy, s, f, fill=FG, anchor="la"):
        self.d.text(xy, shape(s), font=f, fill=fill, anchor=anchor)

    def side(self, y: int, s: str, f, fill=FG, margin: int = 60):
        """Text at the START edge of the reading direction (right for Persian, left for English)."""
        self.text((W - margin if self.rtl else margin, y), s, f, fill, "ra" if self.rtl else "la")

    def panel(self, box, fill=PANEL, radius=26, outline=None):
        self.d.rounded_rectangle(box, radius, fill=fill, outline=outline, width=2 if outline else 0)

    def header(self):
        brand = "بورس رفقا" if self.rtl else "Hype Market"
        mx = W - 60 if self.rtl else 60
        self.d.ellipse([mx - 26, 36, mx + 26, 88], fill=ACCENT)
        self.d.polygon([(mx - 12, 71), (mx - 2, 58), (mx + 4, 65), (mx + 14, 50), (mx + 14, 58), (mx + 4, 73), (mx - 2, 66), (mx - 8, 76)], fill=BG1)
        self.text((mx - 44 if self.rtl else mx + 44, 28), brand, font(46, True), FG, "ra" if self.rtl else "la")
        date = fmt.loc(time.strftime("%Y-%m-%d", time.gmtime(core.now())), self.lang)
        self.text((60 if self.rtl else W - 60, 40), date, font(28), MUTED, "la" if self.rtl else "ra")

    def delta_width(self, p: float, size: int) -> float:
        return self.width(_pct(p, self.lang), font(size, True)) + 12 + size * 0.72

    def delta(self, x, y, p: float, size: int, anchor: str = "la") -> float:
        """A percentage with a drawn triangle (the font has no ▲▼). anchor: la / ra (x = that edge); y = top of the text."""
        col = UP if p >= 0 else DOWN
        f = font(size, True)
        s = _pct(p, self.lang)
        w = self.width(s, f)
        tri, gap = size * 0.36, 12
        total = w + gap + tri * 2
        left = x if anchor == "la" else x - total
        if self.rtl:                                   # Persian: triangle on the right of the number
            text_x, arrow_x = left, left + w + gap + tri
        else:
            arrow_x, text_x = left + tri, left + tri * 2 + gap
        cy = y + size * 0.62
        pts = [(arrow_x, cy - tri), (arrow_x - tri, cy + tri), (arrow_x + tri, cy + tri)] if p >= 0 else [(arrow_x, cy + tri), (arrow_x - tri, cy - tri), (arrow_x + tri, cy - tri)]
        self.d.polygon(pts, fill=col)
        self.d.text((text_x, y), shape(s), font=f, fill=col, anchor="la")
        return total

    def tile(self, x, y, w, h, label, value, color=FG, vsize=44):
        self.panel([x, y, x + w, y + h], radius=22)
        self.text((x + w / 2, y + 18), label, font(24), MUTED, "ma")
        f = font(vsize, True)
        while self.width(value, f) > w - 24 and vsize > 22:
            vsize -= 2
            f = font(vsize, True)
        self.text((x + w / 2, y + h / 2 + 18), value, f, color, "mm")

    def tiles(self, y: int, items: list, h: int = 120, vsize: int = 40):
        n = len(items)
        tw = (W - 80 - (n - 1) * 18) / n
        for i, (lab, val, col) in enumerate(items):
            order = (n - 1 - i) if self.rtl else i
            self.tile(int(40 + order * (tw + 18)), y, int(tw), h, lab, val, col, vsize)

    def footer(self, link: str, line: str):
        self.panel([40, H - 210, W - 40, H - 40], fill=(22, 28, 56), outline=ACCENT)
        self.text((W / 2, H - 192), line, font(32, True), GOLD, "ma")
        f = font(34)
        shown = link.replace("https://", "")
        while self.width(shown, f) > W - 140 and f.size > 18:
            f = font(f.size - 2)
        self.text((W / 2, H - 124), shown, f, FG, "ma")
        self.text((W / 2, H - 78), "Telegram", font(22), MUTED, "ma")

    def png(self) -> bytes:
        out = io.BytesIO()
        self.img.save(out, "PNG", compress_level=6)
        return out.getvalue()


def _money(units: int, lang: str) -> str:
    v = units / C.UNIT
    s = f"{v / 1_000_000:,.1f}M" if abs(v) >= 10_000_000 else f"{round(v):,}" if abs(v) >= 1000 else f"{v:,.2f}".rstrip("0").rstrip(".")
    return fmt.loc(s, lang)


def _pct(p: float, lang: str) -> str:
    return fmt.loc(f"{abs(p):.1f}", lang) + ("٪" if lang == "fa" else "%")


def _bonus(lang: str) -> str:
    return fmt.num(C.REF_BONUS_NEW // C.UNIT, lang)


def global_rank(db, uid: int):
    u = core.get_user(db, uid)
    if not u or u["trades"] <= 0:
        return None
    return db.q1("SELECT COUNT(*)+1 r FROM users WHERE bot=0 AND banned=0 AND trades>0 AND nw>?", (u["nw"],))["r"]


def group_rank(db, uid: int, chat_id: int | None):
    if not chat_id:
        return None
    u = core.get_user(db, uid)
    return db.q1("SELECT COUNT(*)+1 r FROM group_members m JOIN users x ON x.id=m.user_id WHERE m.group_id=? AND x.banned=0 AND x.bot=0 AND x.nw>?", (chat_id, u["nw"]))["r"]


def _identity(c: Card, db, lang: str, uid: int):
    info = progress.level_info(db, uid)          # saves the real level first, so the card is never stale
    u = core.need_user(db, uid)
    c.header()
    c.side(130, c.fit(display_name(u, False), font(64, True), 820), font(64, True))
    c.side(214, f"{c.L['level']} {fmt.num(info['level'], lang)} • {level_title(lang, info['level'])}", font(32), ACCENT)
    return u, info


# ================================================================================ portfolio
def portfolio_card(db, lang: str, uid: int, link: str) -> bytes:
    c = Card(lang)
    L = c.L
    u, info = _identity(c, db, lang, uid)
    c.panel([40, 280, W - 40, 520], radius=34)
    c.text((W / 2, 306), L["nw"], font(30), MUTED, "ma")
    c.text((W / 2, 410), _money(info["nw"], lang), font(118, True), FG, "mm")
    c.text((W / 2, 484), L["coins"], font(30), GOLD, "mm")
    rank = global_rank(db, uid)
    n_stocks = db.q1("SELECT COUNT(*) c FROM holdings h JOIN stocks s ON s.id=h.stock_id WHERE h.user_id=? AND s.status='active'", (uid,))["c"]
    c.tiles(548, [(L["rank"], f"#{fmt.num(rank, lang)}" if rank else "—", GOLD), (L["stocks"], fmt.num(n_stocks, lang), FG),
                  (L["ach"], f"{fmt.num(len(achievements.unlocked(db, uid)), lang)}/{fmt.num(len(achievements.ALL_A), lang)}", FG), (L["streak"], fmt.num(u["streak"], lang), FG)])
    c.panel([40, 696, W - 40, H - 236], radius=30)
    c.side(716, L["holdings"], font(30, True), MUTED, 80)
    rows = sorted(({"t": r["ticker"], "v": core.liq(r, r["shares"]), "cost": r["cost"]} for r in core.holdings_rows(db, uid)), key=lambda r: -r["v"])
    if not rows:
        c.text((W / 2, 880), L["none"], font(36), MUTED, "mm")
    y = 776
    for r in rows[:5]:
        pct = (r["v"] - r["cost"]) * 100.0 / r["cost"] if r["cost"] else 0
        c.d.line([(70, y + 70), (W - 70, y + 70)], fill=LINE)
        left, right = (W - 80, 80) if c.rtl else (80, W - 80)
        c.text((left, y), "$" + r["t"], font(40, True), FG, "ra" if c.rtl else "la")
        c.delta(right, y, pct, 34, "la" if c.rtl else "ra")
        c.text((W / 2, y + 6), _money(r["v"], lang), font(32), MUTED, "ma")
        y += 80
    if len(rows) > 5:
        c.text((W / 2, y + 4), L["more"].format(n=fmt.num(len(rows) - 5, lang)), font(26), MUTED, "ma")
    c.footer(link, L["join"].format(bonus=_bonus(lang)))
    return c.png()


# ================================================================================ rank
def rank_card(db, lang: str, uid: int, link: str, chat_id: int | None = None) -> bytes:
    c = Card(lang)
    L = c.L
    u, info = _identity(c, db, lang, uid)
    rank = global_rank(db, uid)
    c.panel([40, 290, W - 40, 640], radius=34)
    c.text((W / 2, 316), L["my_rank"], font(30), MUTED, "ma")
    c.d.ellipse([W / 2 - 130, 370, W / 2 + 130, 630], outline=GOLD, width=8)
    c.text((W / 2, 500), f"#{fmt.num(rank, lang)}" if rank else "—", font(110, True), GOLD, "mm")
    g = group_rank(db, uid, chat_id)
    if g:
        c.text((W / 2 + (-340 if c.rtl else 340), 500), f"{L['group_rank']}", font(28), MUTED, "mm")
        c.text((W / 2 + (-340 if c.rtl else 340), 546), f"#{fmt.num(g, lang)}", font(52, True), FG, "mm")
    tr = trust.score(db, uid)
    tcol = UP if tr["label"] == "trusted" else DOWN if tr["label"] == "flagged" else FG
    c.tiles(668, [(L["nw"], _money(u["nw"], lang), FG), (L["rebirths"], fmt.num(u["rebirths"], lang), FG), (L["rep"], fmt.num(u["rep"], lang), FG), (L["trust"], L[tr["label"]], tcol)], vsize=38)
    got = achievements.unlocked(db, uid)
    c.panel([40, 816, W - 40, H - 236], radius=30)
    c.side(836, f"{L['ach']}  {fmt.num(len(got), lang)}/{fmt.num(len(achievements.ALL_A), lang)}", font(30, True), MUTED, 80)
    cols, size, gap = 13, 56, 12
    x0 = (W - (cols * size + (cols - 1) * gap)) / 2
    for i, a in enumerate(achievements.ALL_A):
        cx, cy = x0 + (i % cols) * (size + gap), 910 + (i // cols) * (size + gap)
        if a.id in got:
            c.d.ellipse([cx, cy, cx + size, cy + size], fill=TIER[a.tier])
            c.d.ellipse([cx + 12, cy + 12, cx + size - 12, cy + size - 12], outline=BG1, width=4)
        else:
            c.d.ellipse([cx, cy, cx + size, cy + size], outline=LINE, width=3)
    c.footer(link, L["join"].format(bonus=_bonus(lang)))
    return c.png()


# ================================================================================ stock
def stock_card(db, lang: str, s, link: str) -> bytes:
    c = Card(lang)
    L = c.L
    d = charts.collect(db, s, "24h")
    c.header()
    c.side(118, "$" + s["ticker"], font(96, True))
    c.side(236, c.fit(s["name"], font(46, True), 760), font(46, True), MUTED)
    kind = L["kind"].get(s["kind"], s["kind"])
    kw = c.width(kind, font(26, True)) + 40
    kx = (W - 60 - kw) if c.rtl else 60
    c.panel([kx, 306, kx + kw, 354], fill=(40, 52, 98), radius=24)
    c.text((kx + kw / 2, 330), kind, font(26, True), ACCENT, "mm")
    col = UP if d["change"] >= 0 else DOWN
    c.panel([40, 380, W - 40, 700], radius=34)
    c.text((W / 2, 396), L["price"], font(28), MUTED, "ma")
    c.text((W / 2, 480), _money(price(s), lang), font(96, True), FG, "mm")
    fl = font(26)
    lw = c.width(L["h24"], fl)
    cw = c.delta_width(d["change"], 34) + lw + 90
    cx0 = W / 2 - cw / 2
    c.panel([cx0, 538, cx0 + cw, 594], fill=tuple(int(v * 0.25) for v in col), radius=28)
    c.delta(cx0 + 30, 546, d["change"], 34, "la")
    c.text((cx0 + cw - 30, 566), L["h24"], fl, MUTED, "rm")
    closes = [x["c"] for x in d["candles"]]
    x0, x1, y0, y1 = 80, W - 80, 615, 685
    lo, hi = min(closes), max(closes)
    span = (hi - lo) or 1
    pts = [(x0 + (x1 - x0) * i / max(1, len(closes) - 1), y1 - (v - lo) / span * (y1 - y0)) for i, v in enumerate(closes)]
    c.d.polygon(pts + [(x1, y1 + 6), (x0, y1 + 6)], fill=tuple(int(v * 0.22 + b * 0.78) for v, b in zip(col, PANEL)))
    c.d.line(pts, fill=col, width=5, joint="curve")
    owner = core.get_user(db, s["owner_id"]) if s["owner_id"] else None
    tr = trust.score(db, s["owner_id"]) if owner else None
    tcol = UP if tr and tr["label"] == "trusted" else DOWN if tr and tr["label"] == "flagged" else FG
    cells = [(L["mcap"], _money(core.mcap(s), lang), FG), (L["holders"], fmt.num(s["holders"], lang), FG),
             (L["owner"], c.fit(display_name(owner, False), font(36, True), 400) if owner else "—", FG), (L["trust"], L[tr["label"]] if tr else "—", tcol)]
    c.tiles(728, cells[:2], h=112)
    c.tiles(858, cells[2:], h=112)
    if trust.flagged(db, s["id"]):
        c.panel([40, 990, W - 40, 1100], fill=(90, 28, 36), radius=22)
        c.text((W / 2, 1045), L["flag"], font(36, True), (255, 150, 150), "mm")
    else:
        c.tiles(988, [(L["high"], _money(d["high"], lang), UP), (L["low"], _money(d["low"], lang), DOWN)], h=112)
    c.footer(link, L["buy"].format(bonus=_bonus(lang)))
    return c.png()
