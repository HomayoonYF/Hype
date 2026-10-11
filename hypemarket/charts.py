"""Detailed stock charts rendered with Pillow.

The picture contains only Latin text and digits (so no Persian shaping or fonts are needed); the Persian
details (name, owner, event names...) travel in the message caption that is sent together with the image.

Layout: header (ticker, price, change, O/H/L/C, volume) / candlestick panel with moving average, high-low
labels, current-price line and whale markers / volume bars / time axis / statistics strip.
"""
from __future__ import annotations

import io
import json
import time

from PIL import Image, ImageDraw, ImageFont

from . import config as C
from . import core

SPANS = {"1h": (3600, 60), "24h": (86400, 48), "7d": (7 * 86400, 56)}   # seconds, number of candles
BG, PANEL, GRID, FG, MUTED = (15, 19, 30), (22, 27, 42), (44, 52, 72), (232, 237, 247), (140, 152, 178)
UP, DOWN, AMBER, WHALE = (38, 208, 124), (240, 82, 82), (255, 176, 46), (95, 170, 255)


def _font(size: int):
    try:
        return ImageFont.load_default(size=size)
    except TypeError:   # very old Pillow
        return ImageFont.load_default()


def _money(units: float) -> str:
    v = units / C.UNIT
    if abs(v) >= 1_000_000:
        return f"{v / 1_000_000:.2f}M"
    if abs(v) >= 10_000:
        return f"{v / 1000:.1f}K"
    if abs(v) >= 1000:
        return f"{v:,.0f}"
    return f"{v:.2f}"


def _pct(p: float) -> str:
    return f"{p:+.1f}%"


# ------------------------------------------------------------------------------ data
def collect(db, s, span: str = "24h") -> dict:
    """Everything the picture and the caption need, computed from the price history."""
    span = span if span in SPANS else "24h"
    secs, ncandles = SPANS[span]
    sid = s["id"]
    t1 = core.now()
    t0 = t1 - secs
    pre = db.q1("SELECT ts, price, supply FROM history WHERE stock_id=? AND ts<? ORDER BY ts DESC LIMIT 1", (sid, t0))
    rows = [(r["ts"], r["price"], r["supply"]) for r in db.qa("SELECT ts, price, supply FROM history WHERE stock_id=? AND ts>=? ORDER BY ts", (sid, t0))]
    live = (t1, core.price(s), s["supply"])
    if not rows or rows[-1][1] != live[1] or rows[-1][2] != live[2]:
        rows.append(live)
    if pre:
        open_price = pre["price"]
        prev_supply = pre["supply"]
    else:
        open_price = rows[0][1]
        prev_supply = rows[0][2]
        t0 = max(t0, min(rows[0][0], t1 - secs // 6))        # young stock: zoom in on its real life
    width = max(1.0, (t1 - t0) / ncandles)
    buckets: list = [[] for _ in range(ncandles)]
    volume = 0
    for ts, price, supply in rows:
        i = min(ncandles - 1, max(0, int((ts - t0) / width)))
        vol = abs(supply - prev_supply) * price
        buckets[i].append((price, vol))
        volume += vol
        prev_supply = supply
    candles = []
    last_close = open_price
    for b in buckets:
        if b:
            prices = [p for p, _ in b]
            c = prices[-1]
            candles.append({"o": last_close, "h": max(prices + [last_close]), "l": min(prices + [last_close]), "c": c, "v": sum(v for _, v in b), "empty": False})
            last_close = c
        else:
            candles.append({"o": last_close, "h": last_close, "l": last_close, "c": last_close, "v": 0, "empty": True})
    close = core.price(s)
    whales = []
    for r in db.qa("SELECT ts, kind, params FROM news WHERE stock_id=? AND kind LIKE 'whale_%' AND ts>=? ORDER BY ts", (sid, t0)):
        p = json.loads(r["params"] or "{}")
        whales.append({"ts": r["ts"], "side": "buy" if r["kind"].endswith("buy") else "sell", "who": p.get("who", "?"), "amount": p.get("amount", 0)})
    return {
        "span": span, "t0": t0, "t1": t1, "width": width, "candles": candles, "open": open_price, "close": close,
        "high": max([c["h"] for c in candles] + [close]), "low": min([c["l"] for c in candles] + [close]), "volume": int(volume),
        "change": (close - open_price) * 100.0 / open_price if open_price else 0.0,
        "h1": core.change_pct(db, s, 3600), "d1": core.change_pct(db, s, 86400), "d7": core.change_pct(db, s, 7 * 86400),
        "whales": whales,
    }


# ------------------------------------------------------------------------------ drawing
def render(s, d: dict) -> bytes:
    W, H = 1100, 744
    img = Image.new("RGB", (W, H), BG)
    g = ImageDraw.Draw(img)
    f_xl, f_l, f_m, f_s = _font(40), _font(26), _font(19), _font(15)
    col = UP if d["close"] >= d["open"] else DOWN
    name = s["name"] if str(s["name"]).isascii() else ""

    # ---- header
    g.text((28, 16), f"${s['ticker']}", fill=FG, font=f_xl)
    g.text((30, 66), f"{s['kind'].upper()}  |  {d['span'].upper()}" + (f"  |  {name[:22]}" if name else ""), fill=MUTED, font=f_m)
    price_txt = _money(d["close"]) + " coins"
    g.text((W - 28 - g.textlength(price_txt, font=f_xl), 16), price_txt, fill=col, font=f_xl)
    chg_txt = f"{_pct(d['change'])} over {d['span']}"
    g.text((W - 28 - g.textlength(chg_txt, font=f_l), 64), chg_txt, fill=col, font=f_l)
    g.text((30, 96), f"O {_money(d['open'])}   H {_money(d['high'])}   L {_money(d['low'])}   C {_money(d['close'])}   Vol {_money(d['volume'])}", fill=FG, font=f_m)

    # ---- panels
    L, R = 86, W - 28
    top, bot = 130, 470            # candles
    vtop, vbot = 484, 560          # volume
    g.rounded_rectangle([L - 8, top - 8, R + 8, bot + 8], 10, fill=PANEL)
    g.rounded_rectangle([L - 8, vtop - 6, R + 8, vbot + 8], 10, fill=PANEL)
    candles = d["candles"]
    n = len(candles)
    lo, hi = d["low"], d["high"]
    if hi - lo < 1e-9:
        lo, hi = lo * 0.97, hi * 1.03 + 1
    pad = (hi - lo) * 0.10
    lo, hi = max(0.0, lo - pad), hi + pad
    py = lambda v: bot - (v - lo) / (hi - lo) * (bot - top)
    slot = (R - L) / n
    px = lambda i: L + slot * (i + 0.5)

    for k in range(5):                       # price grid + labels
        v = lo + (hi - lo) * k / 4
        g.line([(L, py(v)), (R, py(v))], fill=GRID)
        g.text((10, py(v) - 9), _money(v), fill=MUTED, font=f_s)

    sma = []                                  # 7-candle moving average
    for i in range(n):
        w = candles[max(0, i - 6): i + 1]
        sma.append(sum(c["c"] for c in w) / len(w))
    body_w = max(2.0, slot * 0.62)
    for i, c in enumerate(candles):
        x = px(i)
        colr = UP if c["c"] >= c["o"] else DOWN
        g.line([(x, py(c["h"])), (x, py(c["l"]))], fill=colr, width=2 if slot > 6 else 1)
        y1, y2 = sorted((py(c["o"]), py(c["c"])))
        g.rectangle([x - body_w / 2, y1, x + body_w / 2, max(y2, y1 + 2)], fill=colr)
    g.line([(px(i), py(v)) for i, v in enumerate(sma)], fill=AMBER, width=3, joint="curve")

    for i, key, lbl in ((max(range(n), key=lambda i: candles[i]["h"]), "h", "HIGH"), (min(range(n), key=lambda i: candles[i]["l"]), "l", "LOW")):
        txt = f"{lbl} {_money(candles[i][key])}"
        x = min(max(px(i) - 40, L), R - 110 - g.textlength(txt, font=f_s))
        g.text((x, py(candles[i][key]) + (-22 if key == "h" else 8)), txt, fill=FG, font=f_s)

    yc = py(d["close"])                       # current price line + tag
    for x in range(int(L), int(R), 14):
        g.line([(x, yc), (x + 7, yc)], fill=col)
    tagtxt = _money(d["close"])
    tw = g.textlength(tagtxt, font=f_s)
    g.rounded_rectangle([R - tw - 14, yc - 11, R + 6, yc + 11], 5, fill=col)
    g.text((R - tw - 7, yc - 9), tagtxt, fill=BG, font=f_s)

    for w in d["whales"]:                     # whale markers
        i = min(n - 1, max(0, int((w["ts"] - d["t0"]) / d["width"])))
        x = px(i)
        if w["side"] == "buy":
            y = py(candles[i]["l"]) + 14
            pts = [(x, y - 9), (x - 8, y + 6), (x + 8, y + 6)]
        else:
            y = py(candles[i]["h"]) - 14
            pts = [(x, y + 9), (x - 8, y - 6), (x + 8, y - 6)]
        g.polygon(pts, fill=WHALE, outline=FG)

    vmax = max([c["v"] for c in candles] + [1])   # volume
    for i, c in enumerate(candles):
        if c["v"] > 0:
            h = max(2, (c["v"] / vmax) * (vbot - vtop - 6))
            colr = UP if c["c"] >= c["o"] else DOWN
            g.rectangle([px(i) - body_w / 2, vbot - h, px(i) + body_w / 2, vbot], fill=tuple(int(x * 0.75) for x in colr))
    g.text((L, vtop - 4), "VOLUME", fill=MUTED, font=f_s)
    vm = f"max {_money(vmax)}"
    g.text((R - g.textlength(vm, font=f_s), vtop - 4), vm, fill=MUTED, font=f_s)

    fmt = "%H:%M" if d["span"] != "7d" else "%d %b"   # time axis
    for k in range(5):
        label = time.strftime(fmt, time.gmtime(d["t0"] + (d["t1"] - d["t0"]) * k / 4))
        x = L + (R - L) * k / 4
        g.text((min(max(x - g.textlength(label, font=f_s) / 2, 6), W - 60), vbot + 14), label, fill=MUTED, font=f_s)
    g.text((10, vbot + 14), "UTC", fill=MUTED, font=f_s)

    lx = L                                    # legend
    for colr, label in ((UP, "up candle"), (DOWN, "down candle"), (AMBER, "7-candle average"), (WHALE, "whale buy / sell")):
        g.rectangle([lx, vbot + 42, lx + 14, vbot + 56], fill=colr)
        g.text((lx + 20, vbot + 40), label, fill=MUTED, font=f_s)
        lx += int(g.textlength(label, font=f_s)) + 52

    cells = [                                 # statistics strip
        ("MARKET CAP", _money(core.mcap(s))), ("SHARES", f"{s['supply']:,}"), ("HOLDERS", f"{s['holders']:,}"),
        ("1H", _pct(d["h1"])), ("24H", _pct(d["d1"])), ("7D", _pct(d["d7"])), ("DIVIDEND", f"{s['div_pct']}%"),
        ("HYPE", f"{s['hype']:.0f}"), ("RUG PULLS", str(s["rugs"])), ("FEES EARNED", _money(s["fees_earned"])),
    ]
    cw = (W - 56) / 5
    for idx, (k, v) in enumerate(cells):
        cx, cy = 28 + (idx % 5) * cw, 628 + (idx // 5) * 44
        g.rounded_rectangle([cx, cy, cx + cw - 10, cy + 38], 8, fill=PANEL)
        g.text((cx + 10, cy + 3), k, fill=MUTED, font=f_s)
        g.text((cx + 10, cy + 18), v, fill=UP if v.startswith("+") else DOWN if v.startswith("-") else FG, font=f_m)
    g.text((W - 28 - g.textlength("Hype Market", font=f_s), H - 24), "Hype Market", fill=MUTED, font=f_s)
    out = io.BytesIO()
    img.save(out, "PNG", optimize=True)
    return out.getvalue()


def build(db, s, span: str = "24h"):
    """Returns (png_bytes, details)."""
    d = collect(db, s, span)
    return render(s, d), d
