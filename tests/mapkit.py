"""Helpers shared by the interactive-map tests (v0.17): what the bot must have drawn for a player, and picture comparisons."""
import io

from PIL import Image, ImageChops

from hypemarket import mapui, presence, world, worldmap, worldplaces


def city_of(h, chat):
    return worldplaces.load_city(h.db, world.get_world(h.db, chat))


def shown(h, chat, uid, lang="fa", hl=None):
    """The picture the bot has to send to player `uid`: the saved city with its permanent places, drawn from the saved geometry (optionally with
    a place highlighted), with that player's own markers painted on it."""
    city = city_of(h, chat)
    st = presence.stand(h.db, city, uid)
    others = presence.nearby(h.db, city, uid, st.pos, st.now)
    return worldmap.overlay(worldmap.render(city.layout, lang, hl), lang, mapui._marks(lang, city, st, others, tag_dest=hl is None))


def png(data):
    return Image.open(io.BytesIO(data)).convert("RGB")


def changed_fraction(a: bytes, b: bytes) -> float:
    """Share of pixels that differ between two pictures of the same size."""
    x, y = png(a), png(b)
    assert x.size == y.size
    diff = ImageChops.difference(x, y).convert("L").point(lambda v: 255 if v else 0)
    return diff.histogram()[255] / (x.width * x.height)


def same_city(a: bytes, b: bytes, limit: float = 0.02) -> bool:
    """Two players' pictures of one city: identical except for the little markers (and the header and legend bands are untouched)."""
    if changed_fraction(a, b) > limit:
        return False
    x, y = png(a), png(b)
    head = (0, 0, x.width, worldmap.HEADER - 6)
    foot = (0, x.height - worldmap.FOOTER + 6, x.width, x.height)
    return ImageChops.difference(x.crop(head), y.crop(head)).getbbox() is None and ImageChops.difference(x.crop(foot), y.crop(foot)).getbbox() is None


# --- driving the map through the real handlers ------------------------------------------------------------------------------------
from aiogram.methods import AnswerCallbackQuery, EditMessageCaption, EditMessageMedia, EditMessageText, SendMessage, SendPhoto

EDITS = (EditMessageMedia, EditMessageCaption, EditMessageText)


def buttons_of(call) -> list:
    mk = getattr(call, "reply_markup", None)
    return [(b.text, b.callback_data) for row in mk.inline_keyboard for b in row] if mk else []


def rows_of(call) -> list:
    mk = getattr(call, "reply_markup", None)
    return [[(b.text, b.callback_data) for b in row] for row in mk.inline_keyboard] if mk else []


def text_of(call) -> str:
    if isinstance(call, EditMessageMedia):
        return call.media.caption or ""
    return getattr(call, "caption", None) or getattr(call, "text", None) or ""


class Out:
    """What one button press made the bot do."""
    def __init__(self, calls):
        self.calls = calls
        self.edits = [c for c in calls if isinstance(c, EDITS)]
        self.sent = [c for c in calls if isinstance(c, (SendMessage, SendPhoto))]
        self.answers = [c for c in calls if isinstance(c, AnswerCallbackQuery)]
        self.alerts = [c.text for c in self.answers if c.show_alert and c.text]
        self.toasts = [c.text for c in self.answers if c.text]
        last = self.edits[-1] if self.edits else None
        self.edit = last
        self.caption = text_of(last) if last else ""
        self.buttons = buttons_of(last) if last else []
        self.rows = rows_of(last) if last else []
        self.photo = last.media.media.data if isinstance(last, EditMessageMedia) else None

    def data(self):
        return [d for _t, d in self.buttons]

    def find(self, needle: str):
        """The callback data of the first button whose label contains `needle`."""
        return next(d for t_, d in self.buttons if needle in t_)


class Driver:
    def __init__(self, h, chat):
        self.h, self.chat = h, chat

    def open(self, uid, text="مپ"):
        calls = self.h.say(uid, text, chat=self.chat)
        row = self.session(uid)
        out = Out([])
        sent = [c for c in calls if isinstance(c, (SendPhoto, SendMessage))]
        out.sent, out.calls = sent, calls
        if sent:
            out.caption, out.buttons, out.rows = text_of(sent[-1]), buttons_of(sent[-1]), rows_of(sent[-1])
            out.photo = sent[-1].photo.data if isinstance(sent[-1], SendPhoto) else None
        out.sid, out.mid = (row["sid"], row["message_id"]) if row else (None, None)
        return out

    def session(self, uid):
        return self.h.db.q1("SELECT * FROM map_sessions WHERE user_id=? AND chat_id=? ORDER BY rowid DESC LIMIT 1", (uid, self.chat))

    def press(self, uid, data, view, chat=None, mid=None):
        calls = self.h.press(uid, data, chat=self.chat if chat is None else chat, mid=view.mid if mid is None else mid, photo=True)
        out = Out(calls)
        out.sid, out.mid = view.sid, view.mid
        return out

    def go(self, uid, view, needle=None, data=None):
        """Press the button whose label contains `needle` (or this exact data) of `view`; returns the Out (it carries sid/mid for the next press)."""
        d = data or view.find(needle)
        return self.press(uid, d, view)
