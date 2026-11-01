"""The national ID card, `کارت ملی` (v0.20): a two-sided in-world identity document written as TEXT, drawn from the character's saved data.

There is no picture anywhere: the card is a message. Where a photo would be, everybody has the same blank default («مهمان» / GUEST); nobody's face is drawn.

EVERYTHING on the card is READ from saved rows through `identity.dossier` (nothing here writes, rolls or invents). No clock, no network, no random numbers: the same data gives the same text.

FRONT: the full name, the permanent citizen ID, the gender, the birthday, the current age, the height in centimetres, the birthplace, the nationality, the current city (while the character
is on a journey the card says so and names the destination: the arrival is applied first), the citizen status, the appearance (skin tone, hair colour and style, eye colour, build, face shape,
all read from the saved traits) and the character level with the current XP, a progress bar and the XP still needed for the next level (the v0.19 character-XP curve, `charxp`: a new character
is level 1 with an empty bar).
BACK: the home address (city, district, street, building; a part nobody has assigned yet says so in the document's own words and is never invented), the known languages, the fictional
fingerprint ID, the personal signature (a stable stylised signature written from the saved name and a hash of it: the same character always signs the same), the issue and expiry dates, the
issuing authority and the document number.
"""
from __future__ import annotations

from . import fmt, identity
from .texts import t

GENDER = {"fa": {"male": "مرد", "female": "زن"}, "en": {"male": "Male", "female": "Female"}}
LABELS = {
    "fa": {"unset": "ثبت نشده", "addr_none": "نشانی دقیق هنوز تخصیص نیافته", "unassigned": "تخصیص‌نیافته"},
    "en": {"unset": "Not set", "addr_none": "Street address not assigned yet", "unassigned": "Unassigned"},
}
# the blank default photo everybody has: a plain silhouette in a frame (monospace; Latin letters only so every client aligns it)
GUEST = "┌────────┐\n│  ▄██▄  │\n│  ▀██▀  │\n│ ▄████▄ │\n│ GUEST  │\n└────────┘"
FLOURISH = ("~", "≈", "∼", "⁓")


def gender_name(lang: str, gender: str) -> str:
    return GENDER["en" if lang == "en" else "fa"][gender]


def city_name(lang: str, city: tuple | None) -> str | None:
    return None if city is None else (city[1] if lang == "fa" else city[2])


def city_text(lang: str, city: tuple | None) -> str:
    """«ID • name» of a city (the ID is the permanent city ID), or the «not set» word."""
    if city is None:
        return LABELS["en" if lang == "en" else "fa"]["unset"]
    return f"{fmt.loc(str(city[0]), lang)} • {city_name(lang, city)}"


def current_city_text(lang: str, d: identity.Dossier) -> str:
    """Where the character is: the current city, or «travelling to ...» while a journey lasts."""
    if d.to is not None:
        return t(lang, "cg.trav", dest=fmt.esc(city_text(lang, d.to)))
    return fmt.esc(city_text(lang, d.current))


def address_parts(lang: str, d: identity.Dossier) -> tuple:
    """(city text, district, street, building, assigned) of the home address: a part nobody has assigned yet is None (never invented); `assigned` is True when any detail exists."""
    parts = (d.district, d.street, d.building)
    return city_text(lang, d.home) if d.home is not None else None, d.district, d.street, d.building, any(parts)


def address_detail(lang: str, d: identity.Dossier) -> str:
    """The second line of the address: the assigned district / street / building, or the in-world «not assigned yet»."""
    lab = LABELS["en" if lang == "en" else "fa"]
    got = [p for p in (d.district, d.street, d.building) if p]
    return ("، " if lang == "fa" else ", ").join(got) if got else lab["addr_none"]


def signature_text(d: identity.Dossier) -> str:
    """The holder's signature: the first initial, the family name and a flourish, all fixed by the saved name and its hash (`signature_seed`), so it never changes."""
    seed = d.signature_seed
    flourish = FLOURISH[int(seed[:2], 16) % len(FLOURISH)] * (2 + int(seed[2], 16) % 3)
    return f"{d.first.strip()[:1]}. {d.last.strip()} {flourish}"


def traits_line(lang: str, d: identity.Dossier) -> dict:
    """The saved appearance as words, for the card and the appearance screen."""
    tr = d.trait_map
    v = {k: identity.trait_value(lang, k, tr[k]) for k in tr}
    return {"skin": v["skin_tone"], "hair": ("، " if lang == "fa" else ", ").join((v["hair_color"], v["hair_style"])), "eyes": v["eye_color"], "build": v["build"], "face": v["face_shape"],
            "height": v["height"]}


def _doc(d: identity.Dossier) -> str:
    return f"{d.doc_number[:3]} {d.doc_number[3:6]} {d.doc_number[6:]}"


def front(lang: str, d: identity.Dossier) -> str:
    w = traits_line(lang, d)
    return t(lang, "idc.front", guest=f"<pre>{GUEST}</pre>", name=fmt.esc(d.name), cid=fmt.loc(str(d.citizen_id), lang), gender=gender_name(lang, d.gender), born=fmt.date(d.born_at, lang),
             age=fmt.num(d.age, lang), cm=fmt.num(d.height_cm, lang), birth=fmt.esc(city_text(lang, d.birth)), nat=identity.nationality_name(lang, d.nationality),
             city=current_city_text(lang, d), status=identity.status_name(lang, d.status), skin=w["skin"], hair=w["hair"], eyes=w["eyes"], build=w["build"], face=w["face"],
             level=fmt.num(d.level, lang), xp=fmt.num(d.xp, lang), bar=fmt.bar(d.xp_fraction, 12), pct=fmt.loc(str(int(d.xp_fraction * 100)), lang), left=fmt.num(d.xp_left, lang),
             next=fmt.num(d.level + 1, lang))


def back(lang: str, d: identity.Dossier) -> str:
    none = LABELS["en" if lang == "en" else "fa"]["unassigned"]
    city = city_text(lang, d.home) if d.home is not None else LABELS["en" if lang == "en" else "fa"]["unset"]
    langs = ("، " if lang == "fa" else ", ").join(identity.language_names(lang, d.languages))
    return t(lang, "idc.back", city=fmt.esc(city), district=fmt.esc(d.district or none), street=fmt.esc(d.street or none), building=fmt.esc(d.building or none), langs=fmt.esc(langs),
             fp=d.fingerprint, sig=fmt.esc(signature_text(d)), issued=fmt.date(d.issued_at, lang), expires=fmt.date(d.expires_at, lang),
             authority=fmt.esc(identity.authority_name(lang, d.authority)), doc=_doc(d))


def render(d: identity.Dossier, lang: str = "fa", side: str = "front") -> str:
    """The text of one side of the card."""
    return front(lang, d) if side == "front" else back(lang, d)
