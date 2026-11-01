"""The character hub, `شخصیت` (v0.20): one place for everything the game knows about the player's character, and the screens it opens. Every screen is TEXT: there are no pictures.

    شخصیت ─┬─ کارت ملی      the two-sided national ID card as a text document (front, reverse, back to the hub)
           ├─ مشخصات        the profile: name, citizen ID, gender, birthday, age, nationality, birthplace, current city, status, residence
           ├─ ظاهر          the blank default photo everybody has and the traits: height, build, skin tone, hair colour, hair style, eye colour and face shape
           ├─ ژنتیک         the two genes (alleles) of every trait and the visible result they make
           ├─ اطلاعات شخصی   nationality, languages, home address, fingerprint ID, signature, issue and expiry dates, issuing authority, document number
           └─ پیشرفت        character level, exact XP, a progress bar and the XP still needed for the next level

Every screen is READ-ONLY: it shows what `identity.dossier` reads from the saved rows and changes nothing (a missing identity row is repaired, once, with defaults). `کارت ملی` stays a
direct command that opens the front of the card. The buttons are `cg:<section>:<player id>`; the player id is the opener's, and `handlers.on_callback` refuses anybody else's press before
anything runs (`cg` is a BOUND prefix). Pressing a button again only draws the same screen again (nothing is started or paid), the side of the card already shown has a button that only says so,
and every screen has a button back (to the hub, and the hub back to the main menu). A screen replaces the message the button was on (one message per click).
"""
from __future__ import annotations

from . import character, fmt, identity, idcard, intercity, traits as T
from .core import GameError
from .texts import t
from .views import Reply, kb

PREFIX = "cg"
SECTIONS = ("hub", "card", "cardb", "prof", "look", "gene", "pers", "prog")
NOOPS = ("nf", "nr")                               # the buttons of the side of the card that is already showing: they only answer with a short note
ICONS = {"height": "📏", "build": "💪", "skin_tone": "🎨", "hair_color": "💇", "hair_style": "〰️", "eye_color": "👁", "face_shape": "🙂"}


trait_name = identity.trait_name
trait_value = identity.trait_value


def _cb(section: str, uid: int) -> str:
    return f"{PREFIX}:{section}:{uid}"


def _name(d) -> str:
    return fmt.esc(d.name)


# =====================================================================================================================
#                                                       the screens
# =====================================================================================================================
def hub_reply(lang: str, d, uid: int) -> Reply:
    rows = [[(t(lang, "cg.b.card"), _cb("card", uid)), (t(lang, "cg.b.prof"), _cb("prof", uid))],
            [(t(lang, "cg.b.look"), _cb("look", uid)), (t(lang, "cg.b.gene"), _cb("gene", uid))],
            [(t(lang, "cg.b.pers"), _cb("pers", uid)), (t(lang, "cg.b.prog"), _cb("prog", uid))],
            [(t(lang, "menu.back"), f"mn:menu:{uid}")]]
    text = t(lang, "cg.hub", name=_name(d), cid=fmt.loc(str(d.citizen_id), lang), age=fmt.num(d.age, lang), level=fmt.num(d.level, lang))
    return Reply(text=text, kb=kb(rows))


def _back_row(lang: str, uid: int) -> list:
    return [(t(lang, "cg.b.hub"), _cb("hub", uid))]


def card_reply(lang: str, d, uid: int, side: str) -> Reply:
    front = side == "front"
    rows = [[(t(lang, "cg.b.front_on" if front else "cg.b.front"), _cb("card", uid) if not front else f"{PREFIX}:nf:{uid}"),
             (t(lang, "cg.b.rear" if front else "cg.b.rear_on"), _cb("cardb", uid) if front else f"{PREFIX}:nr:{uid}")],
            _back_row(lang, uid)]
    return Reply(text=idcard.render(d, lang, side), kb=kb(rows))


def _city_line(lang: str, d) -> str:
    return idcard.current_city_text(lang, d)


def prof_reply(lang: str, d, uid: int) -> Reply:
    home = idcard.city_text(lang, d.home) if d.home is not None else idcard.LABELS[lang]["unset"]
    got = any((d.district, d.street, d.building))
    detail = fmt.esc(idcard.address_detail(lang, d)) if got else t(lang, "cg.addr.none")
    text = t(lang, "cg.prof", name=_name(d), cid=fmt.loc(str(d.citizen_id), lang), gender=idcard.gender_name(lang, d.gender), born=fmt.date(d.born_at, lang),
             age=fmt.num(d.age, lang) + ("" if lang == "fa" else " years"), nat=identity.nationality_name(lang, d.nationality), birth=fmt.esc(idcard.city_text(lang, d.birth)),
             city=_city_line(lang, d), status=identity.status_name(lang, d.status), home=fmt.esc(home), detail=detail)
    return Reply(text=text, kb=kb([_back_row(lang, uid)]))


def look_reply(lang: str, d, uid: int) -> Reply:
    tr = d.trait_map
    v = {k: trait_value(lang, k, tr[k]) for k in T.TRAIT_NAMES}
    text = t(lang, "cg.look", name=_name(d), guest=f"<pre>{idcard.GUEST}</pre>", cm=fmt.num(d.height_cm, lang), height=v["height"], build=v["build"], skin=v["skin_tone"],
             hair_color=v["hair_color"], hair_style=v["hair_style"], eye_color=v["eye_color"], face_shape=v["face_shape"])
    return Reply(text=text, kb=kb([_back_row(lang, uid)]))


def gene_reply(lang: str, d, uid: int) -> Reply:
    res = d.trait_map
    lines = []
    for trait, a1, a2 in d.genes:
        lines.append(t(lang, "cg.gene.line", icon=ICONS[trait], trait=trait_name(lang, trait), a1=trait_value(lang, trait, a1), a2=trait_value(lang, trait, a2),
                       result=trait_value(lang, trait, res[trait])))
    return Reply(text=t(lang, "cg.gene", name=_name(d), lines="\n".join(lines)), kb=kb([_back_row(lang, uid)]))


def pers_reply(lang: str, d, uid: int) -> Reply:
    none = t(lang, "cg.unassigned")
    langs = ("، " if lang == "fa" else ", ").join(identity.language_names(lang, d.languages))
    city = idcard.city_text(lang, d.home) if d.home is not None else idcard.LABELS[lang]["unset"]
    text = t(lang, "cg.pers", name=_name(d), nat=identity.nationality_name(lang, d.nationality), langs=fmt.esc(langs), city=fmt.esc(city),
             district=fmt.esc(d.district or none), street=fmt.esc(d.street or none), building=fmt.esc(d.building or none), fp=d.fingerprint, sig=fmt.esc(idcard.signature_text(d)),
             issued=fmt.date(d.issued_at, lang), expires=fmt.date(d.expires_at, lang), authority=fmt.esc(identity.authority_name(lang, d.authority)),
             doc=f"{d.doc_number[:3]} {d.doc_number[3:6]} {d.doc_number[6:]}")
    return Reply(text=text, kb=kb([_back_row(lang, uid)]))


def prog_reply(lang: str, d, uid: int) -> Reply:
    text = t(lang, "cg.prog", name=_name(d), level=fmt.num(d.level, lang), xp=fmt.num(d.xp, lang), bar=fmt.bar(d.xp_fraction, 12), pct=fmt.loc(str(int(d.xp_fraction * 100)), lang),
             into=fmt.num(d.xp_into, lang), span=fmt.num(d.xp_span, lang), next=fmt.num(d.level + 1, lang), left=fmt.num(d.xp_left, lang))
    return Reply(text=text, kb=kb([_back_row(lang, uid)]))


# =====================================================================================================================
#                                                  opening a screen
# =====================================================================================================================
async def dossier_or_setup(c, unfinished):
    """(dossier, None) for a finished character, else (None, reply): the set-up for an unfinished one, None / None for an account that has no character (a bot)."""
    ch = character.ensure_profile(c.db, c.uid)
    if ch is None:
        return None, None
    if not character.is_done(ch):
        return None, unfinished(c, ch)
    await intercity.asettle(c.db, c.uid)                                 # a journey that has ended is applied first, so the current city is the real one
    return identity.dossier(c.db, c.uid), None


async def screen(lang: str, d, uid: int, section: str) -> Reply:
    if section == "hub":
        return hub_reply(lang, d, uid)
    if section in ("card", "cardb"):
        return card_reply(lang, d, uid, "front" if section == "card" else "back")
    if section == "prof":
        return prof_reply(lang, d, uid)
    if section == "look":
        return look_reply(lang, d, uid)
    if section == "gene":
        return gene_reply(lang, d, uid)
    if section == "pers":
        return pers_reply(lang, d, uid)
    if section == "prog":
        return prog_reply(lang, d, uid)
    raise GameError("bad_usage")


async def command(c, section: str, unfinished) -> Reply | None:
    """`شخصیت` (the hub) and `کارت ملی` (the front of the card) typed as commands: a new message. None for an account that has no character."""
    d, reply = await dossier_or_setup(c, unfinished)
    if d is None:
        return reply
    return await screen(c.lang, d, c.uid, section)


async def callback(c, unfinished) -> Reply | None:
    """`cg:<section>:<player id>` (the player id, the last part, was already checked against the presser by the dispatcher): the screen replaces the one the button was on."""
    rest = c.args[:-1]
    sub = rest[0] if len(rest) == 1 else None
    if sub in NOOPS:
        return Reply(toast=t(c.lang, "cg.noop"))
    if sub not in SECTIONS:
        return Reply(toast=t(c.lang, "cg.bad"))                          # a hand-made button does nothing
    d, reply = await dossier_or_setup(c, unfinished)
    if d is None:
        return reply
    r = await screen(c.lang, d, c.uid, sub)
    r.edit = True
    r.fresh = True                                                        # opened from a message of another kind (a picture), the old message is replaced
    return r
