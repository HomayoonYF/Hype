"""The national ID card, `کارت ملی` (v0.20): a picture of the player's in-game identity card with the character's own 2D portrait on it.

What is on the card, and where each thing comes from (everything is READ from what is saved; nothing here writes, rolls or invents):

* the portrait: drawn by `portrait.py` from the character's saved traits (`character_traits`) and gender, so it is the same picture every time, in every group and city, after
  any number of restarts and journeys, and it changes only if the saved traits do (they never do: the database refuses it);
* first and last name, gender and citizen ID (`characters`; the ID is the v0.19 citizen ID, shown exactly as the set-up showed it);
* the age: `character.age` at the moment the card is made (15 plus one year per four complete real days since the profile was finished);
* the birthplace (`birth_world_id`: never changes) and the current city (`current_world_id`), each as «ID • name». While the character is on a journey between cities the
  current city is NOT shown as if they had arrived: the card says they are travelling and names the destination; the arrival is applied first, so a card made after the
  journey's end shows the new city.

The picture is made in memory (Pillow, the Vazirmatn font bundled with the bot), sent as a photo and never stored. No network, no random numbers: the same data gives the same
bytes. A player whose character is not finished gets the set-up instead of a card.

The look: black and dark green, thin lines, light text; the portrait stands on a height chart. It is an in-game card and says so on its face.
"""
from __future__ import annotations

import asyncio
import io
from dataclasses import dataclass

from PIL import Image, ImageDraw

from . import character, core, fmt, intercity, portrait, traits as T
from .cards import font, shape
from .core import GameError
from .texts import t
from .views import Reply

W, H = 1000, 630
BG_A, BG_B = (7, 10, 9), (13, 26, 20)
PANEL, PANEL_LINE = (14, 31, 24), (38, 80, 62)
ACCENT, ACCENT_DIM = (96, 190, 146), (52, 112, 86)
INK, MUTED = (234, 242, 236), (132, 160, 146)
GENDER = {"fa": {"male": "مرد", "female": "زن"}, "en": {"male": "Male", "female": "Female"}}
LABELS = {
    "fa": {"title": "کارت ملی", "brand": "بورس رفقا", "first": "نام", "last": "نام خانوادگی", "cid": "کد شهروندی", "age": "سن", "gender": "جنسیت", "birth": "زادگاه",
           "city": "شهر فعلی", "years": "سال", "travel": "در سفر به", "unset": "هنوز ثبت نشده", "foot": "کارت شناسایی درون‌بازی • فاقد اعتبار قانونی"},
    "en": {"title": "NATIONAL ID", "brand": "Hype Market", "first": "FIRST NAME", "last": "LAST NAME", "cid": "CITIZEN ID", "age": "AGE", "gender": "GENDER", "birth": "BIRTHPLACE",
           "city": "CURRENT CITY", "years": "years", "travel": "Travelling to", "unset": "Not set yet", "foot": "In-game identity card • no legal validity"},
}


@dataclass(frozen=True)
class CardData:
    """Everything the card shows, read from the saved rows (and nothing else)."""
    first: str
    last: str
    citizen_id: int
    gender: str
    age: int
    traits: tuple                                # ((trait, code), ...) in catalog order
    birth: tuple | None                          # (city id, name_fa, name_en)
    here: tuple | None                           # the current city, as above (None: not set yet)
    to: tuple | None                             # the destination of a journey in progress (then `here` is where they set off from and is not shown as the current city)


def _city(db, city_id) -> tuple | None:
    if city_id is None:
        return None
    w = db.q1("SELECT id, name_fa, name_en FROM worlds WHERE id=?", (city_id,))
    return (w["id"], w["name_fa"], w["name_en"]) if w is not None else (city_id, "?", "?")


def build(db, uid: int, now: int | None = None) -> CardData:
    """The card's data for player `uid`. GameError: char_none (no character), card_char (not finished: the caller sends them to the set-up), card_traits (a finished
    character without saved traits: a damaged database, reported and never drawn as someone else)."""
    now = core.now() if now is None else now
    ch = character.get(db, uid)
    if ch is None:
        raise GameError("char_none")
    if not character.is_done(ch):
        raise GameError("card_char")
    tr = T.traits_of(db, uid)
    if tr is None:
        raise GameError("card_traits")
    trip = intercity.active(db, uid)
    return CardData(first=ch["first_name"], last=ch["last_name"], citizen_id=ch["citizen_id"], gender=ch["gender"], age=character.age(ch, now),
                    traits=tuple((n, tr[n]) for n in T.TRAIT_NAMES), birth=_city(db, ch["birth_world_id"]), here=_city(db, ch["current_world_id"]),
                    to=_city(db, trip["to_world_id"]) if trip is not None else None)


# =====================================================================================================================
#                                                       the picture
# =====================================================================================================================
RLM = "\u200f"


def _shape(text: str, rtl: bool) -> str:
    """The text as the picture needs it (letters joined, in visual order). In the Persian card the paragraph direction is set to right-to-left by a leading RLM (removed
    again after the reordering), so a text that STARTS with a number («17 سال», «1 • شهر یک») keeps the number on the right, where it is read first."""
    return shape(RLM + text).replace(RLM, "") if rtl else shape(text)


def _fit(d: ImageDraw.ImageDraw, text: str, maxw: int, rtl: bool, sizes=(40, 36, 32, 28, 24, 21), bold=True):
    """(shaped text, font) of the largest size where the text fits `maxw`; the smallest size cuts it with «…» (a name never runs out of its column)."""
    for size in sizes:
        f = font(size, bold)
        s = _shape(text, rtl)
        if d.textlength(s, font=f) <= maxw:
            return s, f
    f = font(sizes[-1], bold)
    body = text
    while len(body) > 1 and d.textlength(_shape(body + "…", rtl), font=f) > maxw:
        body = body[:-1]
    return _shape(body + "…", rtl), f


def _city_text(lang: str, city: tuple | None) -> str:
    if city is None:
        return LABELS[lang]["unset"]
    return f"{fmt.loc(str(city[0]), lang)} • {city[1] if lang == 'fa' else city[2]}"


def _background() -> Image.Image:
    img = Image.new("RGB", (W, H), BG_A)
    px = ImageDraw.Draw(img)
    for y in range(H):
        k = y / H
        px.line([(0, y), (W, y)], fill=tuple(int(a + (b - a) * k) for a, b in zip(BG_A, BG_B)))
    for x in range(-H, W, 26):                                          # a fine diagonal hatch, drawn the same every time
        px.line([(x, H), (x + H, 0)], fill=(16, 34, 27), width=1)
    return img


def render(data: CardData, lang: str = "fa") -> bytes:
    """The card as PNG bytes. Pure: the same `data` and language give the same bytes."""
    lang = "en" if lang == "en" else "fa"
    rtl = lang == "fa"
    lab = LABELS[lang]
    img = _background()
    d = ImageDraw.Draw(img, "RGBA")
    d.rounded_rectangle([10, 10, W - 11, H - 11], 30, outline=ACCENT_DIM, width=3)
    d.rounded_rectangle([22, 22, W - 23, H - 23], 22, outline=(30, 62, 49), width=1)

    # the header
    d.rounded_rectangle([34, 34, W - 35, 112], 16, fill=PANEL, outline=PANEL_LINE, width=1)
    title, ft = _fit(d, lab["title"], 420, rtl, sizes=(50, 46, 42), bold=True)
    brand, fb = _fit(d, lab["brand"], 300, rtl, sizes=(30, 26), bold=False)
    if rtl:
        d.text((W - 64, 73), title, font=ft, fill=ACCENT, anchor="rm")
        d.text((64, 73), brand, font=fb, fill=MUTED, anchor="lm")
    else:
        d.text((64, 73), title, font=ft, fill=ACCENT, anchor="lm")
        d.text((W - 64, 73), brand, font=fb, fill=MUTED, anchor="rm")

    # the portrait, on the side the reading direction ends on
    pw, ph = 300, 366
    px0 = 44 if rtl else W - 44 - pw
    py0 = 136
    pic = portrait.render(dict(data.traits), data.gender).resize((pw, ph), Image.LANCZOS)
    d.rounded_rectangle([px0 - 6, py0 - 6, px0 + pw + 6, py0 + ph + 6], 14, fill=PANEL, outline=ACCENT_DIM, width=2)
    mask = Image.new("L", (pw, ph), 0)
    ImageDraw.Draw(mask).rounded_rectangle([0, 0, pw - 1, ph - 1], 10, fill=255)
    img.paste(pic, (px0, py0), mask)
    d = ImageDraw.Draw(img, "RGBA")

    # the fields: two columns and a full-width last row
    left, right = (px0 + pw + 40, W - 52) if rtl else (52, px0 - 40)
    span = right - left
    gap = 28
    colw = (span - gap) // 2
    cols = [(right - colw, right), (left, left + colw)] if rtl else [(left, left + colw), (right - colw, right)]

    def field(x0: int, x1: int, y: int, label: str, value: str, width: int | None = None):
        w = (x1 - x0) if width is None else width
        anchor = "ra" if rtl else "la"
        x = x1 if rtl else x0
        d.text((x, y), _shape(label, rtl), font=font(22, False), fill=MUTED, anchor=anchor)
        s, f = _fit(d, value, w, rtl)
        d.text((x, y + 30), s, font=f, fill=INK, anchor=anchor)
        d.line([(x0, y + 82), (x1, y + 82)], fill=(36, 74, 58), width=1)

    y = 138
    age_text = f"{fmt.num(data.age, lang)} {lab['years']}"
    field(*cols[0], y, lab["first"], data.first)
    field(*cols[1], y, lab["last"], data.last)
    y += 96
    field(*cols[0], y, lab["cid"], fmt.loc(str(data.citizen_id), lang))
    field(*cols[1], y, lab["age"], age_text)
    y += 96
    field(*cols[0], y, lab["gender"], GENDER[lang][data.gender])
    field(*cols[1], y, lab["birth"], _city_text(lang, data.birth))
    y += 96
    here = f"{lab['travel']} {_city_text(lang, data.to)}" if data.to is not None else _city_text(lang, data.here)
    field(left, right, y, lab["city"], here)

    # the footer
    d.line([(44, H - 62), (W - 44, H - 62)], fill=(36, 74, 58), width=1)
    foot, ff = _fit(d, lab["foot"], 640, rtl, sizes=(22, 20), bold=False)
    d.text((W - 52, H - 38) if rtl else (52, H - 38), foot, font=ff, fill=MUTED, anchor="rm" if rtl else "lm")
    out = io.BytesIO()
    img.save(out, "PNG", optimize=False, compress_level=6)
    return out.getvalue()


# =====================================================================================================================
#                                                       the command
# =====================================================================================================================
def caption(lang: str, data: CardData) -> str:
    """The short text under the picture (the picture itself carries every field)."""
    return t(lang, "idc.caption", first=fmt.esc(data.first), last=fmt.esc(data.last))


async def show(c, unfinished) -> Reply | None:
    """`کارت ملی`: the card of the player's own character as a photo. `unfinished(c, ch)` builds the answer for a character that is not finished (the set-up).
    None for an account that has no character (a bot). A journey that has ended is applied before the card is made."""
    ch = character.ensure_profile(c.db, c.uid)
    if ch is None:
        return None
    if not character.is_done(ch):
        return unfinished(c, ch)
    await intercity.asettle(c.db, c.uid)
    data = build(c.db, c.uid)
    png = await asyncio.to_thread(render, data, c.lang)
    return Reply(photo=png, text=caption(c.lang, data))
