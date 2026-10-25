"""Telegram layer: routes messages and button presses to the game logic."""
from __future__ import annotations

import asyncio
import html
import logging
import re
import urllib.parse
import time
from collections import deque
from dataclasses import dataclass, field, replace

from aiogram import Bot, F, Router
from aiogram.exceptions import TelegramBadRequest, TelegramForbiddenError, TelegramRetryAfter
from aiogram.types import BufferedInputFile, CallbackQuery, ChatMemberUpdated, InputMediaPhoto, LabeledPrice, Message, PreCheckoutQuery, User

from . import achievements, adminpanel, analytics, backups, cash, charts, interact, lifeui, mapui, salary, config as C, core, events, fmt, gate, progress, quests, sections, shop, social, textparse, trading, trust, views, work, workui, world, worldinspect, worldmap, worldplaces
from .core import GameError
from .db import Database
from .texts import ALIASES, SOFT_ARGS, SOFT_EXACT, EXAMPLES, level_title, t
from .views import Reply, kb

log = logging.getLogger("hypemarket")
router = Router()
GROUPS = ("group", "supergroup")


class Ignore(Exception):
    """Silently drop the message (used for look-alike chat text in groups)."""


# --- runtime state -----------------------------------------------------------------------------
@dataclass
class App:
    settings: C.Settings
    db: Database
    bot: Bot | None = None
    botname: str = ""
    counts: dict = field(default_factory=dict)
    seen: set = field(default_factory=set)
    last_count: dict = field(default_factory=dict)
    last_cmd: dict = field(default_factory=dict)
    chat_sent: dict = field(default_factory=dict)
    touched: set = field(default_factory=set)
    touched_day: int = 0
    alert_ts: dict = field(default_factory=dict)
    pending: dict = field(default_factory=dict)       # uid -> (stock command, expiry): the player's next message answers a guided prompt
    gate_ok: dict = field(default_factory=dict)       # uid -> until when «is a member of the required channel» is trusted
    gate_no: dict = field(default_factory=dict)       # uid -> until when «not a member of the required channel» is trusted (a few seconds)
    gate_told: dict = field(default_factory=dict)     # (chat, uid) -> until when a group is not told again to join

    async def alert_admins(self, text: str, key: str = "generic", every: int = 600) -> None:
        """DM every admin (throttled per key so a crash loop can't spam you)."""
        t0 = time.time()
        if t0 - self.alert_ts.get(key, 0) < every or self.bot is None:
            return
        self.alert_ts[key] = t0
        for admin in self.settings.admins:
            try:
                await self.bot.send_message(admin, html.escape(text))
            except Exception:
                log.warning("could not alert admin %s", admin)

    def touch(self, uid: int) -> None:
        day = int(time.time()) // 86400
        if day != self.touched_day:
            self.touched, self.touched_day = set(), day
        if uid not in self.touched:
            self.touched.add(uid)
            analytics.touch(self.db, uid)

    def swap_buffers(self):
        counts, seen = self.counts, self.seen
        self.counts, self.seen = {}, set()
        return counts, seen

    def note_activity(self, uid: int, chat_id: int) -> None:
        self.seen.add((chat_id, uid))
        t0 = time.time()
        if t0 - self.last_count.get(uid, 0) >= C.ACT_MIN_GAP:
            self.last_count[uid] = t0
            self.counts[(uid, chat_id)] = self.counts.get((uid, chat_id), 0) + 1
        if len(self.last_count) > 50_000:
            self.last_count.clear()

    def throttled(self, uid: int, gap: float = 0.6) -> bool:
        t0 = time.time()
        if t0 - self.last_cmd.get(uid, 0) < gap:
            return True
        self.last_cmd[uid] = t0
        if len(self.last_cmd) > 50_000:
            self.last_cmd.clear()
        return False

    def chat_budget(self, chat_id: int, per_minute: int = 18) -> bool:
        """Stay under Telegram's ~20 messages/minute group limit."""
        dq = self.chat_sent.setdefault(chat_id, deque())
        t0 = time.time()
        while dq and t0 - dq[0] > 60:
            dq.popleft()
        if len(dq) >= per_minute:
            return False
        dq.append(t0)
        return True


@dataclass
class Ctx:
    app: App
    bot: Bot
    tg: User
    chat: object
    lang: str
    cmd: str
    args: list = field(default_factory=list)
    soft: bool = False
    msg: Message | None = None
    cb: CallbackQuery | None = None
    created: bool = False        # True when this very message registered the player

    @property
    def db(self) -> Database:
        return self.app.db

    @property
    def uid(self) -> int:
        return self.tg.id

    @property
    def is_group(self) -> bool:
        return self.chat.type in GROUPS

    def reply(self, text=None, kbd=None, **kw) -> Reply:
        return Reply(text=text, kb=kbd, edit=self.cb is not None, **kw)

    def usage(self, cmd: str) -> Reply:
        if self.soft:
            raise Ignore
        ex = EXAMPLES[cmd][0 if self.lang == "fa" else 1]
        return Reply(text=t(self.lang, "usage", ex=html.escape(ex)))


# --- helpers ------------------------------------------------------------------------------------
_TAGS = re.compile(r"<[^>]+>")


def plain(text: str, limit: int = 190) -> str:
    return html.unescape(_TAGS.sub("", text))[:limit]


MONEY_KW = {"funds": ("need", "have"), "rb_need": ("need", "nw"), "bad_goal": ("lo", "hi"), "fx_low_stock": ("need", "have")}
CASH_KW = {"cash_low": ("need", "have")}                        # life cash: whole cents, shown as $
TIME_ERRORS = ("rug_young", "rug_cooldown", "daily_wait", "job_cooldown", "job_notready", "ia_cooldown")


def err_text(lang: str, e: GameError) -> str:
    kw = {}
    money = MONEY_KW.get(e.code, ())
    dollars = CASH_KW.get(e.code, ())
    for k, v in e.kw.items():
        if k in money:
            kw[k] = fmt.coins_x(v, lang) if e.code == "fx_low_stock" else fmt.coins(v, lang)
        elif k in dollars:
            kw[k] = fmt.usd(v, lang)
        elif k == "ticker":
            kw[k] = fmt.tk(v)
        elif k == "job":
            kw[k] = t(lang, f"job.{v}.name")
        elif isinstance(v, int):
            kw[k] = fmt.num(v, lang)
        else:
            kw[k] = fmt.esc(v)
    if e.code in ("fx_amount", "fx_step"):
        kw["step"] = fmt.num(C.fx_coin_step(), lang)                              # «a multiple of 2 🪙» (the exchange's coin step at the current rate)
    if "secs" in e.kw and e.code in TIME_ERRORS:
        kw["time"] = fmt.dur(e.kw["secs"], lang)
    key = "e." + e.code
    from .texts import FA
    if key not in FA:
        return t(lang, "e.unknown", code=fmt.esc(e.code))
    return t(lang, key, **kw)


def lvl_line(lang: str, r: dict) -> str:
    lv = r.get("levelup")
    return "\n\n" + t(lang, "levelup", level=fmt.num(lv, lang), title=level_title(lang, lv)) if lv else ""


def ticker_exists(db: Database, tok: str) -> bool:
    return bool(db.q1("SELECT 1 FROM stocks WHERE ticker=?", (core.norm_ticker(tok),)))


def perk_key(tok: str) -> str | None:
    k = textparse.key(tok)
    for pk in C.PERKS:
        if k == pk or k in (textparse.key(t("fa", f"perk.{pk}.name")), textparse.key(t("en", f"perk.{pk}.name"))):
            return pk
    return None


LANGS = {"fa": "fa", "farsi": "fa", "persian": "fa", "فارسی": "fa", "en": "en", "english": "en", "انگلیسی": "en"}


BOT_WORDS = {"editstock", "setdividend", "createcompany", "joincompany", "msglimit", "setlang"}


def unambiguous(p: textparse.Parsed) -> bool:
    """Commands that can't be mistaken for normal chat (two-word Persian commands like «ویرایش سهام», or bot-only words).
    These always get an answer, with a helpful message instead of silence when the input is wrong."""
    inner = p.words - 1 if p.section else p.words      # «سهام عرضه میم» is as unmistakable as «عرضه میم»; «سهام خرید کنم یا نه؟» is just chat
    return p.cmd in SOFT_ARGS and (inner >= 2 or (p.section is None and p.cmd in BOT_WORDS))


def soft_ok(app: App, p: textparse.Parsed, m: Message | None = None) -> bool:
    """Plain-word commands in groups must look unmistakably like commands, so ordinary chat doesn't trigger the bot."""
    a, db = p.args, app.db
    if unambiguous(p):
        return True
    if p.cmd == "map":                                       # «مپ» alone, or «مپ P14»: any other words after it are just chat
        return not a or worldinspect.parse_ref(a) is not None
    if p.cmd == "fx":                                        # «صرافی» alone, «صرافی سهام»/«صرافی نقد», or with a valid amount: anything else is chat
        if not a:
            return True
        d = lifeui.direction_of(a[0])
        return d is not None and (len(a) == 1 or (len(a) == 2 and lifeui.parse_amount(d, a[1]) is not None))
    if p.cmd in SOFT_EXACT and p.cmd != "alert":
        return not a
    if p.cmd == "alert":
        return not a or (textparse.key(a[0]) in ("حذف", "delete", "del")) or (textparse.looks_like_ticker(a[0]) and ticker_exists(db, a[0]))
    if p.cmd not in SOFT_ARGS:
        return False
    if p.cmd in ("buy", "sell"):
        tkr, amt = textparse.split_trade(a)
        return len(a) <= 3 and bool(tkr) and ticker_exists(db, tkr)
    if p.cmd in ("price", "chart", "cashout"):
        return bool(a) and textparse.looks_like_ticker(a[0]) and ticker_exists(db, a[0])
    if p.cmd in ("launch", "project", "meme"):
        return len(a) >= 2 and textparse.looks_like_ticker(a[0]) and not ticker_exists(db, a[0])
    if p.cmd == "createcompany":
        return len(a) >= 2 and textparse.looks_like_ticker(a[-1])
    if p.cmd == "joincompany":
        return bool(a) and bool(db.q1("SELECT 1 FROM companies WHERE name=? COLLATE NOCASE", (" ".join(a),)))
    if p.cmd == "upgrade":
        return len(a) == 1 and perk_key(a[0]) is not None
    if p.cmd == "setdividend":
        return bool(a) and textparse.to_int(a[0]) is not None
    if p.cmd == "editstock":
        return len(a) >= 1 and textparse.key(a[0]) in EDIT_FIELDS
    if p.cmd == "msglimit":
        return len(a) == 1 and textparse.to_int(a[0]) is not None
    if p.cmd == "setlang":
        return len(a) == 1 and textparse.key(a[0]) in LANGS
    return False


async def is_admin(c: Ctx) -> bool:
    if c.uid in c.app.settings.admins:
        return True
    if not c.is_group:
        return False
    try:
        m = await c.bot.get_chat_member(c.chat.id, c.uid)
        return m.status in ("creator", "administrator")
    except Exception:
        log.warning("could not check admin status in chat %s", c.chat.id, exc_info=True)
        return False


def bot_link(c: Ctx, param: str = "") -> str:
    return f"https://t.me/{c.app.botname}" + (f"?{param}" if param else "")


# --- sending ---------------------------------------------------------------------------------------
async def _call(coro_fn, *a, **kw):
    try:
        return await coro_fn(*a, **kw)
    except TelegramRetryAfter as e:
        log.warning("flood control: retry after %s", e.retry_after)
    except TelegramForbiddenError:
        log.info("forbidden: bot can't write here")
    except TelegramBadRequest as e:
        if "not modified" not in str(e).lower():
            log.warning("bad request: %s", e)
    return None


def fit_caption(text: str | None, limit: int = 1024) -> str | None:
    """Telegram rejects photo captions over 1024 characters: drop trailing lines until it fits."""
    if text is None or len(text) <= limit:
        return text
    lines = text.split("\n")
    while len("\n".join(lines)) > limit and len(lines) > 1:
        lines.pop()
    return "\n".join(lines)[:limit]


def _photo(r: Reply):
    return BufferedInputFile(r.photo, "chart.png") if isinstance(r.photo, (bytes, bytearray)) else r.photo


async def _told(r: Reply, message_id) -> None:
    """Tell the reply's `after` hook which message now shows it (the map remembers its message this way). A failing hook never breaks sending."""
    if r.after is None or message_id is None:
        return
    try:
        r.after(message_id)
    except Exception:
        log.exception("after-send hook failed")


def _finish(r: Reply) -> Reply:
    """The last look before a reply leaves: the number style (config.digits_mode) also applies to text added after the Reply was made."""
    r.text, r.toast = fmt.digits(r.text), fmt.digits(r.toast)
    if r.kb is not None:
        for row in r.kb.inline_keyboard:
            for b in row:
                b.text = fmt.digits(b.text)
    return r


async def _paint(r: Reply) -> None:
    """Draw the picture a reply asked for (r.pic). A picture that cannot be drawn is dropped: the text and buttons still go out."""
    if r.pic is None or r.photo:
        return
    try:
        r.photo = await r.pic()
    except Exception:
        log.exception("a picture could not be drawn")
        r.photo = None
    r.pic = None


async def _replace(m, r: Reply, c: Ctx) -> bool:
    """A reply that must replace a message of the other kind (a photo under a text, or text under a photo): the old message goes and a new one is
    sent. True when it was handled here; False when the old message cannot be deleted (then the caller edits what it can)."""
    if c.is_group and not c.app.chat_budget(c.chat.id):                          # no room to say anything in this chat right now: the old message stays as it is
        return True
    try:
        await m.delete()
    except Exception:
        return False
    if r.photo:
        sent = await _call(m.answer_photo, _photo(r), caption=fit_caption(r.text), reply_markup=r.kb)
    else:
        sent = await _call(m.answer, r.text, reply_markup=r.kb)
    if sent is not None:
        await _told(r, getattr(sent, "message_id", None))
    return True


async def send(c: Ctx, r: Reply) -> None:
    _finish(r)
    await _paint(r)
    if c.cb is not None:
        cb = c.cb
        await _call(cb.answer, r.toast[:200], show_alert=r.alert) if r.toast else await _call(cb.answer)
        m = cb.message
        if not m:
            return
        if r.fresh and r.edit and bool(r.photo) != bool(m.photo) and (r.photo or r.text) and await _replace(m, r, c):
            return
        if r.photo and r.edit and m.photo:
            if await _call(m.edit_media, media=InputMediaPhoto(media=_photo(r), caption=fit_caption(r.text)), reply_markup=r.kb) is not None:
                await _told(r, m.message_id)
        elif r.photo:
            sent = await _call(m.answer_photo, _photo(r), caption=fit_caption(r.text), reply_markup=r.kb)
            if sent is not None:
                await _told(r, getattr(sent, "message_id", None))
        elif r.text and r.edit:
            if m.photo:
                done = await _call(m.edit_caption, caption=fit_caption(r.text), reply_markup=r.kb)
            else:
                done = await _call(m.edit_text, r.text, reply_markup=r.kb)
            if done is not None:
                await _told(r, m.message_id)
        elif r.text:
            if not c.is_group or c.app.chat_budget(c.chat.id):
                sent = await _call(m.answer, r.text, reply_markup=r.kb)
                if sent is not None:                                                   # v0.18.1: a hug request made with a button remembers the message it lives in
                    await _told(r, getattr(sent, "message_id", None))
        return
    if c.is_group and not c.app.chat_budget(c.chat.id):
        return
    m = c.msg
    if r.photo:
        sent = await _call(m.reply_photo, _photo(r), caption=fit_caption(r.text), reply_markup=r.kb)
        if sent is not None:
            await _told(r, getattr(sent, "message_id", None))
    elif r.text:
        sent = await _call(m.reply, r.text, reply_markup=r.kb)
        if sent is not None:
            await _told(r, getattr(sent, "message_id", None))


async def flush_outbox(app: App) -> None:
    items = list(app.db.outbox)
    app.db.outbox.clear()
    for uid, key, kw in items:
        u = core.get_user(app.db, uid)
        if not u:
            continue
        try:
            await app.bot.send_message(uid, views.dm_text(u["lang"], key, kw))
        except TelegramRetryAfter:
            log.warning("flood control while sending DMs")
            break
        except (TelegramForbiddenError, TelegramBadRequest):
            pass  # user never opened the bot privately
        except Exception:
            log.exception("DM failed")


# --- command registry --------------------------------------------------------------------------------
HANDLERS: dict = {}
CALLBACKS: dict = {}


def cmd(name: str):
    def deco(fn):
        HANDLERS[name] = fn
        return fn
    return deco


def cbk(name: str):
    def deco(fn):
        CALLBACKS[name] = fn
        return fn
    return deco


def uname(c: Ctx) -> str:
    return core.display_name(core.get_user(c.db, c.uid))


# =============================== commands ==================================================
STOCK_PAYLOAD = re.compile(r"^s_([A-Za-z0-9]{2,10})(?:_r(\d+))?$")


@cmd("start")
async def h_start(c: Ctx):
    u = core.need_user(c.db, c.uid)
    welcome = c.reply(t(c.lang, "welcome", name=fmt.esc(core.display_name(u, False)), brand=t(c.lang, "brand"), cash=fmt.coins(u["cash"], c.lang)))
    m = STOCK_PAYLOAD.match(c.args[0]) if c.args else None
    if m and ticker_exists(c.db, m.group(1)):             # opened from a shared stock card: show that stock
        s = core.need_stock(c.db, m.group(1), active=False)
        card = views.stock_card(c.db, c.lang, s, None if c.is_group else c.uid)
        return [welcome, card] if c.created else card
    return welcome


@cmd("help")
async def h_help(c: Ctx):
    return views.help_menu(c.lang, c.uid)


@cmd("menu")
async def h_menu(c: Ctx):
    return views.main_menu(c.lang, c.uid)


@cmd("stocks")
async def h_stocks(c: Ctx):
    return views.stock_menu(c.lang, c.uid)


@cmd("stockhelp")
async def h_stockhelp(c: Ctx):
    return views.stock_help_menu(c.lang, c.uid)


@cmd("stocks_unknown")
async def h_stocks_unknown(c: Ctx):
    return Reply(text=t(c.lang, "stocks_unknown"))


async def h_retired(c: Ctx):
    """An OLD unprefixed stock word (e.g. «سطح»): nothing is executed, the player is pointed to the canonical form."""
    new = sections.canonical(c.cmd, c.lang)
    extra = " ".join(c.args)[:60]
    return Reply(text=t(c.lang, "retired", new=html.escape((new + " " + extra).strip())))


async def _open_screen(c: Ctx, target: str, allowed: tuple, back: tuple):
    """A menu button: run the ORDINARY command handler (same logic, same checks) and show its screen in place, plus a way back."""
    if target not in allowed or target not in HANDLERS:
        return None
    reply = await HANDLERS[target](replace(c, cmd=target, args=[]))
    return views.with_back(reply, back[0], back[1])


@cbk("mn")
async def cb_main_menu(c: Ctx):
    rest = c.args[:-1]                                  # the last part is always the player's id
    if rest and rest[0] == "go" and len(rest) >= 2:
        return await _open_screen(c, rest[1], views.MAIN_GO, (t(c.lang, "menu.back"), f"mn:menu:{c.uid}"))
    r = views.main_menu(c.lang, c.uid)
    r.edit = True
    return r


FX_PROMPTS = {"fx_s2c": "stocks", "fx_c2s": "cash"}        # guided exchange prompts: the word that names the direction in «صرافی <direction> <amount>»


def _guided(app: App, uid: int, cmd: str) -> None:
    """Remember that the player's next plain message answers the prompt of `cmd` (private chats only, a few minutes)."""
    now_ = core.now()
    for k in [k for k, v in app.pending.items() if v[1] <= now_]:
        del app.pending[k]
    app.pending[uid] = (cmd, now_ + sections.GUIDED_TTL)


@cbk("sm")
async def cb_stock_menu(c: Ctx):
    """sm:menu | sm:cat:<category> | sm:go:<screen>:<origin> | sm:ask:<cmd>:<origin> | sm:sure:<cmd>:<origin> | sm:do:<cmd>:<origin> | sm:cancel:<origin> (where the prompt was opened: "m" or a category),
    always followed by the player's id. <origin> is where the button was pressed ("m" = the stock menu, else a category) so 🔙 returns there."""
    rest = c.args[:-1]
    sub = rest[0] if rest else "menu"
    arg = rest[1] if len(rest) > 1 else ""
    origin = rest[2] if len(rest) > 2 and rest[2] in views.ORIGINS else "m"
    r = None
    if sub == "go":
        return await _open_screen(c, arg, views.STOCK_GO, views.back_button(c.lang, origin, c.uid))
    if sub == "do" and arg in sections.SURE_CMDS:
        return await _open_screen(c, arg, tuple(sections.SURE_CMDS), views.back_button(c.lang, origin, c.uid))
    if sub == "ask" and arg in sections.ASK_CMDS:
        if not c.is_group:
            _guided(c.app, c.uid, arg)
        r = views.ask_view(c.lang, c.uid, arg, origin, private=not c.is_group)
    elif sub == "sure" and arg in sections.SURE_CMDS:
        r = views.sure_view(c.lang, c.uid, arg, origin)
    elif sub == "cat" or (sub == "cancel" and arg in views.ORIGINS and arg != "m"):
        r = views.stock_category(c.lang, c.uid, arg)
    elif sub in ("menu", "cancel"):
        r = views.stock_menu(c.lang, c.uid)
    else:
        return Reply(toast=t(c.lang, "e.bad_usage"))                 # an unknown or hand-made button does nothing
    r.edit = True
    return r


@cbk("hp")
async def cb_help(c: Ctx):
    arg = c.args[1] if len(c.args) > 2 else ""          # hp:cat:<n>:<uid>  /  hp:cmds:<uid>
    return views.help_section(c.db, c.lang, c.uid, c.args[0], arg, c.uid in c.app.settings.admins)


@cmd("quests")
async def h_quests(c: Ctx):
    return views.quests_view(c.db, c.lang, c.uid)


@cbk("qs")
async def cb_quests(c: Ctx):
    r = views.quests_view(c.db, c.lang, c.uid)
    r.edit = True
    return r


@cmd("whales")
async def h_whales(c: Ctx):
    return views.whales_view(c.db, c.lang, c.uid)


@cbk("wh")
async def cb_whales(c: Ctx):
    c.db.x("UPDATE users SET whale_dm=1-whale_dm WHERE id=?", (c.uid,))
    r = views.whales_view(c.db, c.lang, c.uid)
    r.edit = True
    return r


@cmd("profile")
async def h_profile(c: Ctx):
    return views.profile_view(c.db, c.lang, c.uid)


@cmd("language")
async def h_language(c: Ctx):
    scope = "g" if c.is_group else "u"
    ident = c.chat.id if c.is_group else c.uid
    if c.is_group and not await is_admin(c):
        return Reply(text=t(c.lang, "admin_only"))
    return Reply(text="🌐", kb=kb([[("فارسی", f"lang:fa:{scope}:{ident}"), ("English", f"lang:en:{scope}:{ident}")]]))


@cmd("setlang")
async def h_setlang(c: Ctx):
    if not c.args or textparse.key(c.args[0]) not in LANGS:
        return c.usage("setlang")
    code = LANGS[textparse.key(c.args[0])]
    if c.is_group:
        if not await is_admin(c):
            return Reply(text=t(c.lang, "admin_only"))
        social.set_group_lang(c.db, c.chat.id, code)
    else:
        c.db.x("UPDATE users SET lang=? WHERE id=?", (code, c.uid))
    return Reply(text=t(code, "lang.set", lang=t(code, "lang_name")))


@cbk("lang")
async def cb_lang(c: Ctx):
    code, scope, ident = c.args[0], c.args[1], int(c.args[2])
    if code not in ("fa", "en"):
        return Reply(toast=t(c.lang, "e.bad_usage"))
    if scope == "g":
        if ident != c.chat.id or not await is_admin(c):
            return Reply(toast=t(c.lang, "admin_only"), alert=True)
        social.set_group_lang(c.db, ident, code)
    else:
        if ident != c.uid:
            return Reply(toast=t(c.lang, "not_yours"))
        c.db.x("UPDATE users SET lang=? WHERE id=?", (code, c.uid))
    return Reply(text=t(code, "lang.set", lang=t(code, "lang_name")), edit=True)


# ---- trading ---------------------------------------------------------------------------------
def trade_kb(db, lang, s):
    return views.stock_card(db, lang, s).kb


@cmd("buy")
async def h_buy(c: Ctx):
    tkr, amt = textparse.split_trade(c.args)
    if not tkr:
        return c.usage("buy")
    if amt is None:
        return views.stock_card(c.db, c.lang, core.need_stock(c.db, tkr), c.uid)
    r = trading.buy(c.db, c.uid, tkr, n=amt, spend_all=(amt == 0))
    s = r["stock"]
    text = t(c.lang, "buy_ok", tk=fmt.tk(s["ticker"]), shares=fmt.num(r["shares"], c.lang), cost=fmt.coins(r["cost"], c.lang), fee=fmt.coins(r["fee"], c.lang),
             price=fmt.price(r["price"], c.lang), holding=fmt.num(r["holding"], c.lang)) + lvl_line(c.lang, r)
    if trust.flagged(c.db, s["id"]):
        text += t(c.lang, "warn.flagged")
    return Reply(text=text, kb=trade_kb(c.db, c.lang, s))


@cmd("sell")
async def h_sell(c: Ctx):
    tkr, amt = textparse.split_trade(c.args)
    if not tkr:
        return c.usage("sell")
    if amt is None:
        return views.stock_card(c.db, c.lang, core.need_stock(c.db, tkr), c.uid)
    r = trading.sell(c.db, c.uid, tkr, n=amt, sell_all=(amt == 0))
    s = r["stock"]
    pnl = t(c.lang, "pnl_line", pnl=fmt.pnl(r["pnl"], c.lang))
    text = t(c.lang, "sell_ok", tk=fmt.tk(s["ticker"]), shares=fmt.num(r["shares"], c.lang), net=fmt.coins(r["net"], c.lang), fee=fmt.coins(r["fee"], c.lang),
             price=fmt.price(r["price"], c.lang), pnl=pnl) + lvl_line(c.lang, r)
    return Reply(text=text, kb=trade_kb(c.db, c.lang, s))


@cmd("price")
async def h_price(c: Ctx):
    if not c.args:
        return c.usage("price")
    s = core.need_stock(c.db, c.args[0], active=False)
    card = views.stock_card(c.db, c.lang, s, c.uid if not c.is_group else None)
    if s["logo"] and s["status"] == "active":
        card.photo = s["logo"]
    return card


@cbk("st")
async def cb_stock(c: Ctx):
    s = core.stock_row(c.db, int(c.args[0]))
    if not s:
        return Reply(toast=t(c.lang, "e.no_stock", ticker="?"))
    card = views.stock_card(c.db, c.lang, s, None if c.is_group else c.uid)
    card.edit = True
    return card


SPAN_WORDS = {"1h": "1h", "h": "1h", "ساعت": "1h", "24h": "24h", "day": "24h", "روز": "24h", "7d": "7d", "week": "7d", "هفته": "7d", "هفتگی": "7d"}


def _chart(db, lang, s, span, edit=False):
    png, d = charts.build(db, s, span)
    sid = s["id"]
    rows = [[(("• " if d["span"] == sp else "") + sp, f"ch:{sid}:{sp}") for sp in ("1h", "24h", "7d")]]
    return Reply(photo=png, text=views.chart_caption(db, lang, s, d), kb=kb(rows), edit=edit)


@cmd("chart")
async def h_chart(c: Ctx):
    if not c.args:
        return c.usage("chart")
    s = core.need_stock(c.db, c.args[0])
    span = next((SPAN_WORDS[textparse.key(a)] for a in c.args[1:] if textparse.key(a) in SPAN_WORDS), "24h")
    return _chart(c.db, c.lang, s, span)


@cbk("ch")
async def cb_chart(c: Ctx):
    s = core.stock_row(c.db, int(c.args[0]))
    if not s or s["status"] != "active":
        return Reply(toast=t(c.lang, "e.no_stock", ticker="?"))
    return _chart(c.db, c.lang, s, c.args[1], edit=bool(c.msg and c.msg.photo))


@cmd("market")
async def h_market(c: Ctx):
    return views.market_view(c.db, c.lang, "top")


@cbk("mk")
async def cb_market(c: Ctx):
    r = views.market_view(c.db, c.lang, c.args[0])
    r.edit = True
    return r


@cmd("portfolio")
async def h_portfolio(c: Ctx):
    return views.portfolio_view(c.db, c.lang, c.uid)


@cbk("pf")
async def cb_portfolio(c: Ctx):
    r = views.portfolio_view(c.db, c.lang, c.uid)
    r.edit = True
    return r


@cmd("alert")
async def h_alert(c: Ctx):
    a = c.args
    if not a:
        return views.alerts_view(c.db, c.lang, c.uid)
    if textparse.key(a[0]) in ("حذف", "delete", "del"):
        n = trading.delete_alerts(c.db, c.uid, a[1] if len(a) > 1 else None)
        return Reply(text=t(c.lang, "alerts_deleted", n=fmt.num(n, c.lang)))
    v = textparse.to_number(a[1]) if len(a) > 1 else None
    if v is None:
        return c.usage("alert")
    r = trading.set_alert(c.db, c.uid, a[0], int(round(v * C.UNIT)))
    return Reply(text=t(c.lang, "alert_ok", tk=fmt.tk(r["stock"]["ticker"]), dir=t(c.lang, "dir." + r["direction"]), target=fmt.price(r["target"], c.lang)))


@cmd("exchange")
async def h_exchange(c: Ctx):
    return views.exchange_view(c.db, c.lang, None if c.is_group else c.uid)


@cmd("earnings")
async def h_earnings(c: Ctx):
    return views.earnings_view(c.db, c.lang)


@cbk("ex")
async def cb_exchange(c: Ctx):
    r = views.earnings_view(c.db, c.lang) if c.args and c.args[0] == "e" else views.exchange_view(c.db, c.lang, None if c.is_group else c.uid)
    r.edit = True
    return r


@cbk("b")
async def cb_buy(c: Ctx):
    sid, n = int(c.args[0]), int(c.args[1])
    s = core.stock_row(c.db, sid)
    if not s:
        return Reply(toast=t(c.lang, "e.no_stock", ticker="?"))
    r = trading.buy(c.db, c.uid, s["ticker"], n=n, spend_all=(n == 0))
    toast = plain(t(c.lang, "buy_ok", tk=fmt.tk(s["ticker"]), shares=fmt.num(r["shares"], c.lang), cost=fmt.coins(r["cost"], c.lang), fee=fmt.coins(r["fee"], c.lang),
                    price=fmt.price(r["price"], c.lang), holding=fmt.num(r["holding"], c.lang)) + lvl_line(c.lang, r))
    card = views.stock_card(c.db, c.lang, r["stock"], None if c.is_group else c.uid)
    card.edit, card.toast = True, toast
    return card


@cbk("s")
async def cb_sell(c: Ctx):
    sid, n = int(c.args[0]), int(c.args[1])
    s = core.stock_row(c.db, sid)
    if not s:
        return Reply(toast=t(c.lang, "e.no_stock", ticker="?"))
    r = trading.sell(c.db, c.uid, s["ticker"], n=n, sell_all=(n == 0))
    pnl = t(c.lang, "pnl_line", pnl=fmt.pnl(r["pnl"], c.lang))
    toast = plain(t(c.lang, "sell_ok", tk=fmt.tk(s["ticker"]), shares=fmt.num(r["shares"], c.lang), net=fmt.coins(r["net"], c.lang), fee=fmt.coins(r["fee"], c.lang),
                    price=fmt.price(r["price"], c.lang), pnl=pnl) + lvl_line(c.lang, r))
    card = views.stock_card(c.db, c.lang, r["stock"], None if c.is_group else c.uid)
    card.edit, card.toast = True, toast
    return card


# ---- launching / owning -------------------------------------------------------------------------------
def _launch(c: Ctx, kind: str, name_idx: int, goal: int = 0):
    rest = " ".join(c.args[name_idx:])
    name, _, descr = rest.partition("|")
    s = trading.launch(c.db, c.uid, c.args[0], name.strip(), kind=kind, descr=descr.strip(), goal=goal)
    text = t(c.lang, "launch_ok", tk=fmt.tk(s["ticker"]), ticker=s["ticker"], name=fmt.esc(s["name"]), price=fmt.price(core.price(s), c.lang))
    if goal:
        text += "\n" + t(c.lang, "launch_goal", goal=fmt.coins(goal, c.lang))
        if goal == C.DEFAULT_PROJECT_GOAL:
            text += "\n" + t(c.lang, "launch_goal_default")
    return Reply(text=text, kb=trade_kb(c.db, c.lang, s))


@cmd("launch")
async def h_launch(c: Ctx):
    if len(c.args) < 2:
        return c.usage("launch")
    return _launch(c, "personal", 1)


@cmd("meme")
async def h_meme(c: Ctx):
    if len(c.args) < 2:
        return c.usage("meme")
    return _launch(c, "meme", 1)


@cmd("project")
async def h_project(c: Ctx):
    if len(c.args) < 2:
        return c.usage("project")
    if not textparse.looks_like_ticker(c.args[0]):
        return Reply(text=t(c.lang, "e.bad_ticker") + "\n\n" + t(c.lang, "usage", ex=html.escape(EXAMPLES["project"][0 if c.lang == "fa" else 1])))
    goal = textparse.to_number(c.args[1])
    if goal is not None and len(c.args) >= 3:                      # عرضه پروژه TRIP 20000 نام | توضیح
        return _launch(c, "project", 2, goal=int(round(goal * C.UNIT)))
    if goal is not None:                                           # a number but no name
        return c.usage("project")
    return _launch(c, "project", 1, goal=C.DEFAULT_PROJECT_GOAL)  # عرضه پروژه TRIP نام | توضیح   (default goal)


@cmd("mystock")
async def h_mystock(c: Ctx):
    s = trading.my_stock(c.db, c.uid, c.args[0] if c.args else None)
    card = views.stock_card(c.db, c.lang, s, c.uid if not c.is_group else None)
    card.text += "\n" + t(c.lang, "card.fees", fees=fmt.coins(s["fees_earned"], c.lang))
    others = [x["ticker"] for x in trading.my_stocks(c.db, c.uid) if x["id"] != s["id"]]
    if others:
        card.text += "\n" + " ".join(fmt.tk(x) for x in others)
    return card


EDIT_FIELDS = {
    "نام": "name", "اسم": "name", "عنوان": "name", "name": "name", "title": "name",
    "توضیح": "descr", "توضیحات": "descr", "بیو": "descr", "درباره": "descr", "descr": "descr", "description": "descr", "bio": "descr", "about": "descr",
    "لوگو": "logo", "عکس": "logo", "تصویر": "logo", "پروفایل": "logo", "logo": "logo", "photo": "logo", "image": "logo",
}


def _edit_parts(db, args: list):
    """Accepts  [TICKER] <field> <text...>  or  <field> [TICKER] <text...>. Returns (ticker|None, field|None, text)."""
    ticker, field_, rest = None, None, []
    for i, a in enumerate(args):
        k = textparse.key(a)
        if field_ is None and k in EDIT_FIELDS:
            field_ = EDIT_FIELDS[k]
        elif field_ is None and ticker is None and textparse.looks_like_ticker(a) and i == 0:
            ticker = a
        elif field_ is not None and ticker is None and not rest and field_ != "descr" and textparse.looks_like_ticker(a) and ticker_exists(db, a):
            ticker = a
        else:
            rest.append(a)
    return ticker, field_, " ".join(rest)


@cmd("editstock")
async def h_editstock(c: Ctx):
    ticker, field_, text = _edit_parts(c.db, c.args)
    if not field_:
        return c.usage("editstock")
    if field_ == "logo":
        m = c.msg
        photo = (m.photo if m and m.photo else None) or (m.reply_to_message.photo if m and m.reply_to_message and m.reply_to_message.photo else None)
        if not photo:
            return Reply(text=t(c.lang, "edit.logo_how"))
        text = photo[-1].file_id
    elif not text and field_ == "name":
        return c.usage("editstock")
    s = trading.edit_stock(c.db, c.uid, field_, text, ticker)
    return Reply(text=t(c.lang, "edit_ok2", tk=fmt.tk(s["ticker"]), field=t(c.lang, f"edit.f_{field_}")))


@cmd("setdividend")
async def h_setdividend(c: Ctx):
    pct = textparse.to_int(c.args[0]) if c.args else None
    if pct is None:
        return c.usage("setdividend")
    s = trading.set_dividend(c.db, c.uid, pct, c.args[1] if len(c.args) > 1 else None)
    return Reply(text=t(c.lang, "setdiv_ok", pct=fmt.num(pct, c.lang), tk=fmt.tk(s["ticker"])))


def _rug_menu(c: Ctx, s) -> Reply:
    sid, uid = s["id"], c.uid
    text = t(c.lang, "rug_warn", tk=fmt.tk(s["ticker"]), pen=fmt.num(C.RUG_REP_PENALTY, c.lang), tax=fmt.num(C.RUG_TAX_BPS // 100, c.lang))
    rows = [[(f"☠️ {fmt.num(p, c.lang)}%", f"rug:{sid}:{p}:{uid}") for p in (25, 50, 70)], [(t(c.lang, "btn.cancel"), f"rug:no:{uid}")]]
    return Reply(text=text, kb=kb(rows))


def _do_rug(c: Ctx, ticker: str, pct: int) -> Reply:
    r = trading.rug(c.db, c.uid, ticker, pct, allowed=c.app.settings.rug)
    return Reply(text=t(c.lang, "rug_ok", tk=fmt.tk(r["stock"]["ticker"]), taken=fmt.coins(r["taken"], c.lang)) + lvl_line(c.lang, r))


@cmd("cashout")
async def h_cashout(c: Ctx):
    if not c.app.settings.rug:
        raise GameError("rug_off")
    ticker = next((a for a in c.args if textparse.looks_like_ticker(a) and textparse.to_int(a) is None), None)
    pct = next((textparse.to_int(a) for a in c.args if textparse.to_int(a) is not None), None)
    s = trading.my_stock(c.db, c.uid, ticker)
    if pct is not None:
        return _do_rug(c, s["ticker"], pct)
    return _rug_menu(c, s)


@cbk("rug")
async def cb_rug(c: Ctx):
    if c.args[0] == "no":
        return Reply(text="👍", edit=True)
    sid, pct = int(c.args[0]), int(c.args[1])
    s = core.stock_row(c.db, sid)
    r = _do_rug(c, s["ticker"], pct)
    r.edit = True
    return r


# ---- passive income ------------------------------------------------------------------------------------
def _claim_extra(c: Ctx, r: dict) -> str:
    extra = ""
    if r["tax"]:
        extra += t(c.lang, "claim_tax", tax=fmt.coins(r["tax"], c.lang))
    if r["bonus"]:
        extra += t(c.lang, "claim_bonus", bonus=fmt.coins(r["bonus"], c.lang))
    return extra + lvl_line(c.lang, r)


@cmd("dividends")
async def h_dividends(c: Ctx):
    r = trading.claim_dividends(c.db, c.uid)
    return Reply(text=t(c.lang, "div_ok", net=fmt.coins(r["net"], c.lang), extra=_claim_extra(c, r)))


@cmd("income")
async def h_income(c: Ctx):
    r = trading.claim_income(c.db, c.uid)
    return Reply(text=t(c.lang, "inc_ok", net=fmt.coins(r["net"], c.lang), extra=_claim_extra(c, r)))


@cbk("cl")
async def cb_claim(c: Ctx):
    try:
        r = trading.claim_dividends(c.db, c.uid) if c.args[0] == "div" else trading.claim_income(c.db, c.uid)
        toast = plain(t(c.lang, "div_ok" if c.args[0] == "div" else "inc_ok", net=fmt.coins(r["net"], c.lang), extra=""))
    except GameError as e:
        toast = plain(err_text(c.lang, e))
    view = views.portfolio_view(c.db, c.lang, c.uid)
    view.edit, view.toast = True, toast
    return view


@cmd("daily")
async def h_daily(c: Ctx):
    r = progress.daily(c.db, c.uid)
    return Reply(text=t(c.lang, "daily_ok", amount=fmt.coins(r["amount"], c.lang), streak=fmt.num(r["streak"], c.lang), pct=fmt.num(r["pct"], c.lang)) + lvl_line(c.lang, r))


# ---- companies -------------------------------------------------------------------------------------------
@cmd("createcompany")
async def h_createcompany(c: Ctx):
    if len(c.args) < 2:
        return c.usage("createcompany")
    co = social.create_company(c.db, c.uid, " ".join(c.args[:-1]), c.args[-1])
    st = core.stock_row(c.db, co["stock_id"])
    return Reply(text=t(c.lang, "company.created", name=fmt.esc(co["name"]), tk=fmt.tk(st["ticker"])))


@cmd("joincompany")
async def h_joincompany(c: Ctx):
    if not c.args:
        return c.usage("joincompany")
    co = social.join_company(c.db, c.uid, " ".join(c.args))
    return Reply(text=t(c.lang, "company.joined", name=fmt.esc(co["name"])))


@cmd("leavecompany")
async def h_leavecompany(c: Ctx):
    co = social.leave_company(c.db, c.uid)
    return Reply(text=t(c.lang, "company.left", name=fmt.esc(co["name"])))


@cmd("mycompany")
async def h_mycompany(c: Ctx):
    return views.company_view(c.db, c.lang, c.uid)


@cmd("payout")
async def h_payout(c: Ctx):
    r = social.payout_company(c.db, c.uid)
    return Reply(text=t(c.lang, "company.payout", n=fmt.num(r["members"], c.lang), each=fmt.coins(r["each"], c.lang)))


# ---- groups & rankings ------------------------------------------------------------------------------------
@cmd("group")
async def h_group(c: Ctx):
    if not c.is_group:
        return Reply(text=t(c.lang, "group_only"))
    return views.group_view(c.db, c.lang, c.chat.id)


@cmd("index")
async def h_index(c: Ctx):
    if not c.is_group:
        return Reply(text=t(c.lang, "group_only"))
    g = social.get_group(c.db, c.chat.id)
    return views.stock_card(c.db, c.lang, core.stock_row(c.db, g["index_stock"]), None if c.is_group else c.uid)


@cmd("leaderboard")
async def h_leaderboard(c: Ctx):
    return views.leaderboard_view(c.db, c.lang, c.chat.id) if c.is_group else views.top_view(c.db, c.lang)


@cmd("top")
async def h_top(c: Ctx):
    return views.top_view(c.db, c.lang)


@cmd("grouprank")
async def h_grouprank(c: Ctx):
    return views.grouprank_view(c.db, c.lang)


@cmd("map")
async def h_map(c: Ctx):
    """The group's city (World Engine v1), now interactive (v0.17). One city per chat, generated once and then always the same saved layout;
    every `مپ` opens a map session (owner-locked buttons, your marker, the people near you, walking, the casino: see mapui).
    `مپ <ID>` (World Interaction v1) opens the same screen on one parcel, public place or district of THAT group's saved city. Looking
    never changes the city; the only things written are the player's own position row (once), a session row and, on «go», a trip."""
    if not c.is_group:
        return Reply(text=t(c.lang, "map.private"))
    ref = None
    if c.args:
        ref = worldinspect.parse_ref(c.args)
        if ref is None:                                                  # not ID-shaped: ordinary chat stays unanswered, a slash command gets a hint
            if c.soft:
                raise Ignore
            return Reply(text=t(c.lang, "wi.bad"))
    try:
        w, created = await world.aensure_world(c.db, c.chat.id)
        city = await worldplaces.aload_city(c.db, w)
    except world.WorldError as e:
        log.error("map of chat %s unavailable: %s", c.chat.id, e.code)
        return Reply(text=t(c.lang, "map.failed" if e.code == "generation_failed" else "map.corrupt"))
    if created:
        log.info("world %s created for chat %s (seed %s)", w["id"], c.chat.id, w["seed"])
    if ref is not None and worldinspect.resolve(city.layout, ref) is None:
        return Reply(text=worldinspect.unknown_text(c.lang, city.layout, ref))
    try:
        return await mapui.open_map(c, w, city, ref)
    except Exception:                                                     # drawing failed: the saved city stays exactly as it is
        log.exception("map of world %s failed", w["id"])
        return Reply(text=t(c.lang, "map.failed"))


@cbk("mp")
async def cb_map(c: Ctx):
    """Every button under a map message. Ownership is verified in on_callback before anything else and again inside mapui.handle."""
    return await mapui.handle(c)


@cmd("invite")
async def h_invite(c: Ctx):
    return Reply(text=t(c.lang, "invite", link=bot_link(c, f"start=ref_{c.uid}"), glink=bot_link(c, "startgroup=true"),
                        inviter=fmt.coins(C.REF_BONUS_INVITER, c.lang), new=fmt.coins(C.REF_BONUS_NEW, c.lang), gb=fmt.coins(C.GROUP_ADD_BONUS, c.lang)))


# ---- progress ----------------------------------------------------------------------------------------------
@cmd("level")
async def h_level(c: Ctx):
    return views.level_view(c.db, c.lang, c.uid)


@cmd("reputation")
async def h_reputation(c: Ctx):
    return views.rep_view(c.db, c.lang, c.uid)


@cmd("skills")
async def h_skills(c: Ctx):
    return views.perks_view(c.db, c.lang, c.uid, "skill")


@cmd("legacy")
async def h_legacy(c: Ctx):
    return views.perks_view(c.db, c.lang, c.uid, "legacy")


@cmd("upgrade")
async def h_upgrade(c: Ctx):
    if not c.args:
        return c.usage("upgrade")
    key = perk_key(c.args[0])
    if not key:
        raise GameError("no_perk")
    r = progress.upgrade(c.db, c.uid, key)
    return Reply(text=t(c.lang, "upgrade_ok", name=t(c.lang, f"perk.{key}.name"), level=fmt.num(r["level"], c.lang)))


@cbk("up")
async def cb_upgrade(c: Ctx):
    key = c.args[0]
    try:
        r = progress.upgrade(c.db, c.uid, key)
        toast = plain(t(c.lang, "upgrade_ok", name=t(c.lang, f"perk.{key}.name"), level=fmt.num(r["level"], c.lang)))
    except GameError as e:
        toast = plain(err_text(c.lang, e))
    view = views.perks_view(c.db, c.lang, c.uid, C.PERKS[key].kind)
    view.edit, view.toast = True, toast
    return view


@cbk("sk")
async def cb_sk(c: Ctx):
    v = views.perks_view(c.db, c.lang, c.uid, "skill")
    v.edit = True
    return v


@cbk("lg")
async def cb_lg(c: Ctx):
    v = views.perks_view(c.db, c.lang, c.uid, "legacy")
    v.edit = True
    return v


@cmd("rebirth")
async def h_rebirth(c: Ctx):
    return views.rebirth_view(c.db, c.lang, c.uid)


@cbk("rb")
async def cb_rebirth(c: Ctx):
    if c.args[0] == "no":
        return Reply(text="👍", edit=True)
    r = progress.rebirth(c.db, c.uid)
    return Reply(text=t(c.lang, "rb.ok", gain=fmt.num(r["gain"], c.lang), cash=fmt.coins(r["cash"], c.lang), count=fmt.num(r["count"], c.lang)), edit=True)


# ---- news / season / shop ---------------------------------------------------------------------------------
@cmd("news")
async def h_news(c: Ctx):
    return views.news_view(c.db, c.lang)


@cmd("events")
async def h_events(c: Ctx):
    return views.events_view(c.db, c.lang)


@cmd("season")
async def h_season(c: Ctx):
    return views.season_view(c.db, c.lang)


@cmd("shop")
async def h_shop(c: Ctx):
    if not c.app.settings.shop:
        return Reply(text=t(c.lang, "shop.off"))
    if c.is_group:
        return Reply(text=t(c.lang, "open_private"), kb=kb([[(t(c.lang, "open_private_btn"), bot_link(c, "start=shop"), "url")]]))
    return views.shop_view(c.lang)


@cbk("shop")
async def cb_shop(c: Ctx):
    if c.is_group or not c.app.settings.shop:
        return Reply(toast=t(c.lang, "private_only"), alert=True)
    item = c.args[0]
    spec = shop.item_check(c.db, c.uid, item)
    title = plain(t(c.lang, f"shop.{item}.t"), 32)
    desc = plain(t(c.lang, f"shop.{item}.d"), 250)
    await _call(c.bot.send_invoice, chat_id=c.uid, title=title, description=desc, payload=item, provider_token="", currency="XTR",
                prices=[LabeledPrice(label=title, amount=spec["stars"])])
    return Reply()


# ---- group admin ------------------------------------------------------------------------------------------
MODES = ["quiet", "live", "off"]
CAPS = [0, 50, 100, 200, 300]


async def _admin_group(c: Ctx):
    if not c.is_group:
        return Reply(text=t(c.lang, "group_only"))
    if not await is_admin(c):
        return Reply(text=t(c.lang, "admin_only"))
    return None


@cmd("settings")
async def h_settings(c: Ctx):
    return await _admin_group(c) or views.settings_view(c.db, c.lang, c.chat.id)


@cmd("quiet")
async def h_quiet(c: Ctx):
    deny = await _admin_group(c)
    if deny:
        return deny
    social.set_news_mode(c.db, c.chat.id, "quiet")
    return Reply(text=t(c.lang, "settings.saved") + "\n" + t(c.lang, "mode.quiet"))


@cmd("msglimit")
async def h_msglimit(c: Ctx):
    deny = await _admin_group(c)
    if deny:
        return deny
    cap = textparse.to_int(c.args[0]) if c.args else None
    if cap is None:
        return c.usage("msglimit")
    social.set_msg_cap(c.db, c.chat.id, cap)
    return Reply(text=t(c.lang, "settings.saved"))


@cbk("set")
async def cb_set(c: Ctx):
    what, chat_id = c.args[0], int(c.args[1])
    if chat_id != c.chat.id or not await is_admin(c):
        return Reply(toast=t(c.lang, "admin_only"), alert=True)
    g = social.get_group(c.db, chat_id)
    if what == "lang":
        social.set_group_lang(c.db, chat_id, "en" if g["lang"] == "fa" else "fa")
    elif what == "mode":
        social.set_news_mode(c.db, chat_id, MODES[(MODES.index(g["news_mode"]) + 1) % len(MODES)])
    elif what == "cap":
        social.set_msg_cap(c.db, chat_id, CAPS[(CAPS.index(g["msg_cap"]) + 1) % len(CAPS)] if g["msg_cap"] in CAPS else CAPS[0])
    g = social.get_group(c.db, chat_id)
    v = views.settings_view(c.db, g["lang"], chat_id)
    v.edit = True
    return v


# ---- owner-only maintenance -----------------------------------------------------------------------------------
@cmd("stats")
async def h_stats(c: Ctx):
    if c.uid not in c.app.settings.admins:
        raise Ignore
    return views.dashboard_view(c.db, c.lang)


@cmd("backup")
async def h_backup(c: Ctx):
    if c.uid not in c.app.settings.admins:
        raise Ignore
    res = await backups.send_backup(c.app, "manual")
    if not res["ok"]:
        return Reply(text=t(c.lang, "backup.failed"))
    m = res["meta"]
    return Reply(text=t(c.lang, "backup.manual", n=fmt.num(res["sent"], c.lang), size=fmt.num(res["size"] // 1024, c.lang), users=fmt.num(m["users"], c.lang), stocks=fmt.num(m["stocks"], c.lang)))


@cmd("admin")
async def h_admin(c: Ctx):
    """The bot admin's panel. Players who are not in ADMIN_IDS get the same silence as for any other admin command."""
    if not adminpanel.allowed(c.app, c.uid):
        raise Ignore
    if c.is_group:
        return Reply(text=t(c.lang, "ad.private_only"))
    return adminpanel.main(c.app, c.lang, c.uid)


@cbk("ad")
async def cb_admin(c: Ctx):
    """ad:<screen or action>:<player id>. Every press re-checks ADMIN_IDS: a copied or forged button does nothing for anyone else."""
    if not adminpanel.allowed(c.app, c.uid) or c.is_group:
        return Reply(toast=t(c.lang, "ad.denied"))
    return await adminpanel.press(c.app, c.lang, c.uid, c.args[:-1])


def join_prompt(app: App, lang: str, uid: int, name: str, s: dict) -> Reply:
    rows = []
    if s.get("link"):
        rows.append([(t(lang, "gate.join"), s["link"], "url")])
    rows.append([(t(lang, "gate.joined"), f"gate:check:{uid}")])
    return Reply(text=t(lang, "gate.need", name=fmt.esc(name), channel=fmt.esc(gate.label(s))), kb=kb(rows))


@cbk("gate")
async def cb_gate(c: Ctx):
    """«✅ I joined»: ask Telegram again (never the cache) and let the player in if they really are a member."""
    s = gate.active(c.db)
    if s and await gate.membership(c.app, c.uid, fresh=True) == "no":
        return Reply(toast=t(c.lang, "gate.still", channel=gate.label(s)), alert=True)
    done = Reply(text=t(c.lang, "gate.ok"), edit=True)
    if not c.is_group:
        menu = views.main_menu(c.lang, c.uid)
        done = Reply(text=t(c.lang, "gate.ok") + "\n\n" + menu.text, kb=menu.kb, edit=True)
    return done


@cmd("ban")
async def h_ban(c: Ctx):
    if c.uid not in c.app.settings.admins:
        raise Ignore
    if not c.args or not c.args[0].isdigit():
        return Reply(text="/ban <user_id>")
    r = trust.admin_ban(c.db, c.uid, int(c.args[0]))
    return Reply(text=t(c.lang, "mod.done_ban", oid=r["user"]["id"], tickers=", ".join(fmt.tk(x) for x in r["delisted"]) or "-"))


@cmd("unban")
async def h_unban(c: Ctx):
    if c.uid not in c.app.settings.admins:
        raise Ignore
    if not c.args or not c.args[0].isdigit():
        return Reply(text="/unban <user_id>")
    trust.admin_unban(c.db, c.uid, int(c.args[0]))
    return Reply(text=t(c.lang, "mod.ok"))


@cmd("delist")
async def h_delist(c: Ctx):
    if c.uid not in c.app.settings.admins:
        raise Ignore
    if not c.args:
        return Reply(text="/delist <TICKER>")
    r = trust.admin_delist(c.db, c.uid, core.need_stock(c.db, c.args[0])["id"])
    return Reply(text=t(c.lang, "mod.done_delist", tk=fmt.tk(r["stock"]["ticker"])))


@cbk("mod")
async def cb_mod(c: Ctx):
    if c.uid not in c.app.settings.admins:
        return Reply(toast=t(c.lang, "mod.not_admin"), alert=True)
    action, sid = c.args[0], int(c.args[1])
    s = core.stock_row(c.db, sid)
    if not s or (action == "delist" and s["status"] != "active"):
        return Reply(text=t(c.lang, "mod.gone"), edit=True)
    if action == "delist":
        trust.admin_delist(c.db, c.uid, sid)
        text = t(c.lang, "mod.done_delist", tk=fmt.tk(s["ticker"]))
    elif action == "ban":
        r = trust.admin_ban(c.db, c.uid, s["owner_id"])
        text = t(c.lang, "mod.done_ban", oid=r["user"]["id"], tickers=", ".join(fmt.tk(x) for x in r["delisted"]) or "-")
    else:
        trust.admin_dismiss(c.db, c.uid, sid)
        text = t(c.lang, "mod.done_dismiss", tk=fmt.tk(s["ticker"]))
    return Reply(text=text, edit=True)


async def send_mod_alerts(app: App) -> None:
    """Tell every admin about stocks whose scam reports just crossed an alert level (with action buttons)."""
    events_, app.db.mod_events = app.db.mod_events, []
    for _kind, sid in events_:
        for admin in app.settings.admins:
            u = core.get_user(app.db, admin)
            r = views.mod_alert(app.db, u["lang"] if u else "en", sid)
            await _call(app.bot.send_message, admin, r.text, reply_markup=r.kb)


# ---- trust, reports, achievements ----------------------------------------------------------------
REASON_WORDS = {"rug": "rug", "راگ": "rug", "خروج": "rug", "fake": "fake", "دروغ": "fake", "جعلی": "fake", "spam": "spam", "اسپم": "spam", "توهین": "spam", "other": "other", "دیگر": "other"}


def _trust_target(c: Ctx):
    if c.args and textparse.looks_like_ticker(c.args[0]) and ticker_exists(c.db, c.args[0]):
        s = core.need_stock(c.db, c.args[0], active=False)
        if s["owner_id"] and s["kind"] in C.OWNER_KINDS:
            return s["owner_id"]
        raise GameError("trust_target")
    rm = c.msg.reply_to_message if c.msg else None
    if rm and rm.from_user and not rm.from_user.is_bot:
        return rm.from_user.id
    return None


def _vote(c: Ctx, value: int):
    target = _trust_target(c)
    if target is None:
        if value == 1:
            return views.trust_board_view(c.db, c.lang)
        return c.usage("distrust")
    if not core.get_user(c.db, target):
        raise GameError("trust_target")
    return Reply(text=views.trust_result(c.db, c.lang, trust.vote(c.db, c.uid, target, value)))


@cmd("trust")
async def h_trust(c: Ctx):
    return _vote(c, 1)


@cmd("distrust")
async def h_distrust(c: Ctx):
    return _vote(c, -1)


@cbk("tr")
async def cb_trust(c: Ctx):
    s = core.stock_row(c.db, int(c.args[0]))
    if not s or not s["owner_id"]:
        return Reply(toast=t(c.lang, "e.no_stock", ticker="?"))
    r = trust.vote(c.db, c.uid, s["owner_id"], int(c.args[1]))
    card = views.stock_card(c.db, c.lang, s, None if c.is_group else c.uid)
    card.edit, card.toast = True, plain(views.trust_result(c.db, c.lang, r))
    return card


def _do_report(c: Ctx, s, reason: str, note: str = "") -> Reply:
    r = trust.report(c.db, c.uid, s["ticker"], reason, note)
    key = "report.ok" if r["new"] else "report.updated"
    extra = t(c.lang, "report.flagged") if r["flagged"] else ""
    return Reply(text=t(c.lang, key, tk=fmt.tk(s["ticker"]), extra=extra))


@cmd("report")
async def h_report(c: Ctx):
    if not c.args:
        return c.usage("report")
    s = core.need_stock(c.db, c.args[0])
    rest = list(c.args[1:])
    reason = None
    if rest and textparse.key(rest[0]) in REASON_WORDS:
        reason = REASON_WORDS[textparse.key(rest.pop(0))]
    note = " ".join(rest)
    trust.can_act(c.db, c.uid)
    if reason is None and not note:
        return views.report_menu(c.lang, s)
    return _do_report(c, s, reason or "other", note)


@cbk("rp")
async def cb_report_menu(c: Ctx):
    s = core.stock_row(c.db, int(c.args[0]))
    if not s or s["status"] != "active":
        return Reply(toast=t(c.lang, "e.no_stock", ticker="?"))
    trust.can_act(c.db, c.uid)
    return views.report_menu(c.lang, s)


@cbk("rp2")
async def cb_report(c: Ctx):
    s = core.stock_row(c.db, int(c.args[0]))
    if not s or s["status"] != "active":
        return Reply(toast=t(c.lang, "e.no_stock", ticker="?"))
    r = _do_report(c, s, c.args[1])
    r.edit = True
    return r


@cmd("salary")
async def h_salary(c: Ctx):
    if c.is_group:
        return Reply(text=t(c.lang, "private_only"))
    return views.salary_status(c.db, c.lang, c.uid)


@cbk("sal")
async def cb_salary(c: Ctx):
    action = c.args[0]
    if action in ("off", "tog"):
        on = (not salary.get(c.db, c.uid)["enabled"]) if action == "tog" else False
        salary.set_enabled(c.db, c.uid, on)
        r = views.salary_status(c.db, c.lang, c.uid)
        r.edit = True
        return r
    if action == "claim":
        r = progress.daily(c.db, c.uid)                 # raises daily_wait (shown as a toast) if already claimed
        view = views.salary_message(c.db, c.lang, c.uid)
        rows = [[b for b in row if not (b.callback_data or "").startswith("sal:claim")] for row in view.kb.inline_keyboard]
        view.kb = kb([[(b.text, b.callback_data) for b in row] for row in rows if row])
        view.text = t(c.lang, "salary.claimed", amount=fmt.coins(r["amount"], c.lang), streak=fmt.num(r["streak"], c.lang), pct=fmt.num(r["pct"], c.lang)) + lvl_line(c.lang, r)
        view.edit = True
        return view
    if action == "inc":
        total = 0
        for fn in (trading.claim_income, trading.claim_dividends):
            try:
                total += fn(c.db, c.uid)["net"]
            except GameError:
                pass
        view = views.salary_message(c.db, c.lang, c.uid)
        view.edit = True
        view.toast = t(c.lang, "salary.income_ok", amount=fmt.coins(total, c.lang)) if total else t(c.lang, "salary.income_none")
        return view
    return Reply(toast=t(c.lang, "e.bad_usage"))


PORTFOLIO_WORDS = {"پورتفوی", "پرتفوی", "پورتفولیو", "portfolio", "pf"}
RANK_WORDS = {"رتبه", "رتبه‌ام", "رتبهام", "rank", "profile", "پروفایل"}


def _share(c: Ctx, kind: str, s=None) -> Reply:
    return views.share_reply(c.db, c.lang, kind, c.uid, c.app.botname, s, c.chat.id if c.is_group else None)


@cmd("share")
async def h_share(c: Ctx):
    if not c.args:
        return views.share_menu(c.db, c.lang, c.uid)
    k = textparse.key(c.args[0])
    if k in {textparse.key(w) for w in PORTFOLIO_WORDS}:
        return _share(c, "p")
    if k in {textparse.key(w) for w in RANK_WORDS}:
        return _share(c, "r")
    return _share(c, "s", core.need_stock(c.db, c.args[0]))


@cbk("scp")
async def cb_share_portfolio(c: Ctx):
    return _share(c, "p")


@cbk("scr")
async def cb_share_rank(c: Ctx):
    return _share(c, "r")


@cbk("scs")
async def cb_share_stock(c: Ctx):
    s = core.stock_row(c.db, int(c.args[0]))
    if not s or s["status"] != "active":
        return Reply(toast=t(c.lang, "e.no_stock", ticker="?"))
    return _share(c, "s", s)


@cmd("achievements")
async def h_achievements(c: Ctx):
    return views.achievements_view(c.db, c.lang, c.uid)


@cmd("give")
async def h_give(c: Ctx):
    if c.uid not in c.app.settings.admins:
        raise Ignore
    if len(c.args) != 2 or not c.args[0].lstrip("-").isdigit() or textparse.to_number(c.args[1]) is None:
        return Reply(text="/give <user_id> <coins>")
    amount = int(textparse.to_number(c.args[1]) * C.UNIT)
    with c.db.tx():
        core.need_user(c.db, int(c.args[0]))
        c.db.x("UPDATE users SET cash=cash+? WHERE id=?", (amount, int(c.args[0])))
        core.mint(c.db, amount)
    return Reply(text="✅")


@cmd("fire")
async def h_fire(c: Ctx):
    if c.uid not in c.app.settings.admins:
        raise Ignore
    kind = events.fire_event(c.db, c.args[0] if c.args else None)
    return Reply(text=f"🔥 {kind}")


# =============================== life cash, exchange, jobs, interactions (v0.18) ==================
@cmd("cash")
async def h_cash(c: Ctx):
    """The wallet: life cash and stock money side by side (two separate currencies)."""
    return lifeui.wallet(c.db, c.lang, c.uid)


@cmd("fx")
async def h_fx(c: Ctx):
    """The currency exchange. Without arguments: the screen with buttons. «صرافی سهام 250» / «صرافی نقد 5» / «... همه»: straight to the preview."""
    if not c.args:
        return lifeui.fx_home(c.db, c.lang, c.uid)
    d = lifeui.direction_of(c.args[0])
    if d is None or len(c.args) > 2:
        return c.usage("fx")
    if len(c.args) == 1:
        if not c.is_group:
            _guided(c.app, c.uid, "fx_" + d)
        return lifeui.fx_direction(c.db, c.lang, c.uid, d)
    amt = lifeui.parse_amount(d, c.args[1])
    if amt is None:
        raise GameError("fx_amount")
    q = cash.make_quote(c.db, c.uid, d, None if amt[0] == "all" else amt[1])
    return lifeui.fx_confirm(c.db, c.lang, c.uid, q)


@cbk("cs")
async def cb_cash(c: Ctx):
    """cs:w | cs:fx | cs:d:<dir> | cs:a:<dir>:<amount or all> | cs:ok:<quote id> | cs:no:<quote id>, always followed by the player's id.
    Only `ok` moves anything, and it carries nothing but the id of a stored, expiring quote."""
    rest = c.args[:-1]
    sub = rest[0] if rest else "w"
    arg = rest[1:]
    if len(arg) != {"w": 0, "fx": 0, "d": 1, "a": 2, "ok": 1, "no": 1}.get(sub, -1):            # exactly the pieces the bot drew into the button: anything else is hand-made
        return Reply(toast=t(c.lang, "e.bad_usage"))
    if sub == "w":
        r = lifeui.wallet(c.db, c.lang, c.uid)
    elif sub == "fx":
        r = lifeui.fx_home(c.db, c.lang, c.uid)
    elif sub == "d" and arg[0] in (cash.S2C, cash.C2S):
        if not c.is_group:
            _guided(c.app, c.uid, "fx_" + arg[0])
        r = lifeui.fx_direction(c.db, c.lang, c.uid, arg[0])
    elif sub == "a" and len(arg) == 2 and arg[0] in (cash.S2C, cash.C2S):
        if arg[1] == "all":
            amount = None
        elif re.fullmatch(r"\d{1,15}", arg[1]):
            amount = int(arg[1])
        else:
            raise GameError("fx_amount")
        q = cash.make_quote(c.db, c.uid, arg[0], amount)
        r = lifeui.fx_confirm(c.db, c.lang, c.uid, q)
    elif sub == "ok":
        try:
            r = lifeui.fx_done(c.lang, c.uid, cash.confirm(c.db, c.uid, arg[0]))
        except GameError as e:
            if e.code in ("fx_gone", "fx_done", "fx_expired"):          # the preview is stale: say so and show the exchange again
                r = lifeui.fx_home(c.db, c.lang, c.uid)
                r.toast, r.alert = plain(err_text(c.lang, e)), True
                r.edit, r.fresh = True, True
                return r
            raise
    elif sub == "no":
        cash.cancel(c.db, c.uid, arg[0])
        r = lifeui.fx_cancelled(c.lang, c.uid)
    else:
        return Reply(toast=t(c.lang, "e.bad_usage"))
    r.edit = True
    r.fresh = True                                    # v0.18.1: opened from a job picture, the wallet replaces that message instead of keeping its picture
    return r


@cmd("jobs")
async def h_jobs(c: Ctx):
    return await workui.board(c)


@cmd("snapp")
async def h_snapp(c: Ctx):
    return await workui.snapp(c)


@cmd("mcd")
async def h_mcd(c: Ctx):
    return await workui.mcd(c)


@cmd("muni")
async def h_muni(c: Ctx):
    return await workui.muni(c)


@cbk("jb")
async def cb_jobs(c: Ctx):
    """Every job button (jb:<action>[:...]:<player id>); the player id was checked against the presser, the task against the saved row."""
    return await workui.handle(c)


@cbk("ia")
async def cb_interact(c: Ctx):
    """ia:y|n:<request id>:<target id> and ia:c:<request id>:<sender id>. The request, who may answer it and its state are read from the saved row."""
    rest = c.args[:-1]
    if len(rest) != 2 or rest[0] not in interact.NEW_STATE or not rest[1].isdigit():
        return Reply(toast=t(c.lang, "e.bad_usage"))
    try:
        city = await workui.city_of(c)
    except workui.NoCity as e:
        return Reply(toast=plain(t(c.lang, e.key)), alert=True)
    if city is None:
        return Reply(toast=t(c.lang, "e.ia_gone"), alert=True)
    row, changed = interact.hug_answer(c.db, city, c.chat.id, int(rest[1]), c.uid, rest[0])
    a, b = core.need_user(c.db, row["from_id"]), core.need_user(c.db, row["to_id"])
    text = t(c.lang, f"ia.hug.{row['state']}", a=interact.mention(a), b=interact.mention(b), ttl=fmt.dur(C.HUG_TTL, c.lang))
    return Reply(text=text, edit=True, toast=None if changed else plain(t(c.lang, "ia.late")))


# =============================== dispatch =======================================================
async def run(c: Ctx, fn, *, is_cb: bool) -> None:
    try:
        reply = await fn(c)
    except Ignore:
        return
    except GameError as e:
        text = err_text(c.lang, e)
        reply = Reply(toast=plain(text), alert=True) if is_cb else Reply(text=text)
    except Exception as e:
        log.exception("handler failed: %s", c.cmd)
        await c.app.alert_admins(f"Handler '{c.cmd}' crashed: {type(e).__name__}: {e}", key=f"h:{c.cmd}")
        reply = Reply(toast=plain(t(c.lang, "err")), alert=True) if is_cb else Reply(text=t(c.lang, "err"))
    done = quests.drain_events(c.db, c.uid)      # the actor sees completions inline; other players get a DM
    unlocked = achievements.drain_events(c.db, c.uid)
    replies = [r for r in (reply if isinstance(reply, list) else [reply]) if r is not None]
    if (done or unlocked) and replies:
        line = "\n".join(x for x in (views.quest_done_lines(c.lang, done) if done else "", views.ach_done_lines(c.lang, unlocked) if unlocked else "") if x)
        last = replies[-1]
        if is_cb:
            last.toast = plain(line, 120) + ("\n" + last.toast if last.toast else "")
        elif last.text:
            last.text += "\n\n" + line
    if not replies:
        if is_cb:
            await _call(c.cb.answer)
        return
    try:
        for r in replies:
            await send(c, r)
        await flush_outbox(c.app)
        await send_mod_alerts(c.app)
    except Exception:
        log.exception("sending the reply failed (old message, deleted chat?)")


def _lang_for(app: App, chat, uid: int) -> str:
    if chat.type in GROUPS:
        g = social.get_group(app.db, chat.id)
        return g["lang"] if g else app.settings.lang
    u = core.get_user(app.db, uid)
    return u["lang"] if u else app.settings.lang


def _prepare(app: App, tg: User, chat, referrer: int | None = None):
    """Register the group and the player if needed. Returns (language to answer in, player was just created)."""
    if chat.type in GROUPS:
        social.ensure_group(app.db, chat.id, chat.title or "Group", lang=app.settings.lang)
    _u, created = core.ensure_user(app.db, tg.id, tg.username, tg.full_name, lang=app.settings.lang, referrer=referrer)
    app.touch(tg.id)
    if chat.type == "private":
        salary.unblock(app.db, tg.id)                  # they can obviously receive messages from the bot now
    return _lang_for(app, chat, tg.id), created


async def handle_message(m: Message, app: App, text: str) -> None:
    tg = m.from_user
    if not tg or tg.is_bot:
        return
    chat = m.chat
    group = chat.type in GROUPS
    if group:
        app.note_activity(tg.id, chat.id)
    p = textparse.parse(text, app.botname)
    pend = app.pending.get(tg.id) if chat.type == "private" else None
    if pend:                                      # a guided prompt («🛒 خرید») is waiting for this player's answer
        app.pending.pop(tg.id, None)
        if core.now() <= pend[1] and (p is None or p.cmd == "stocks_unknown"):
            if pend[0] == adminpanel.PROMPT:      # the admin's answer to «✏️ Set channel»
                if adminpanel.allowed(app, tg.id):
                    lang, created = _prepare(app, tg, chat)
                    c = Ctx(app=app, bot=app.bot, tg=tg, chat=chat, lang=lang, cmd="admin", args=[text], msg=m, created=created)
                    await run(c, lambda cx: adminpanel.channel_text(app, cx.lang, cx.uid, text), is_cb=False)
                return
            if pend[0] in FX_PROMPTS:                                           # «🪙 → 💵»: the plain message is the amount to exchange
                p = textparse.parse(f"fx {FX_PROMPTS[pend[0]]} {text}", app.botname)
            else:
                p = textparse.parse(f"stocks {pend[0]} {text}", app.botname)   # the plain message is the command's arguments
    if not p or (p.cmd not in HANDLERS and not p.retired):
        return
    soft = group and not p.prefixed and not unambiguous(p)
    if group and not p.prefixed and not unambiguous(p) and not soft_ok(app, p, m):
        return
    banned = core.get_user(app.db, tg.id)
    if banned and banned["banned"]:
        return                                   # banned players are ignored completely
    if app.throttled(tg.id):
        return
    referrer = None
    if p.cmd == "start" and p.args:
        payload = p.args[0]
        if payload.startswith("ref_") and payload[4:].isdigit():
            referrer = int(payload[4:])
        elif STOCK_PAYLOAD.match(payload) and STOCK_PAYLOAD.match(payload).group(2):       # s_SPEED_r123: a shared stock link also invites
            referrer = int(STOCK_PAYLOAD.match(payload).group(2))
    if gate.active(app.db) and tg.id not in app.settings.admins and await gate.membership(app, tg.id) == "no":
        # the required channel: nothing else runs for this player until they have joined. /start still registers them (so the invite
        # that brought them counts); everything else waits and is not even recorded.
        if p.cmd == "start":
            _prepare(app, tg, chat, referrer)
        if gate.may_notify(app, chat.id, tg.id, group) and (not group or app.chat_budget(chat.id)):
            g_lang = _lang_for(app, chat, tg.id)
            r = join_prompt(app, g_lang, tg.id, tg.full_name or tg.username or "", gate.active(app.db))
            await _call(m.reply, r.text, reply_markup=r.kb)
        return
    lang, created = _prepare(app, tg, chat, referrer)
    c = Ctx(app=app, bot=app.bot, tg=tg, chat=chat, lang=lang, cmd=p.cmd, args=p.args, soft=soft, msg=m, created=created)
    if p.cmd == "start" and p.args and p.args[0] == "shop":
        c.cmd = "shop"
    await run(c, h_retired if p.retired else HANDLERS[c.cmd], is_cb=False)


@router.message(F.text)
async def on_text(m: Message, app: App) -> None:
    await handle_message(m, app, m.text)


@router.message(F.photo & F.caption)
async def on_photo(m: Message, app: App) -> None:
    await handle_message(m, app, m.caption)


BOUND = {"pf", "cl", "up", "sk", "lg", "rb", "rug", "hp", "qs", "wh", "scp", "scr", "sal", "mn", "sm", "ad", "gate", "cs", "jb", "ia"}


@router.callback_query()
async def on_callback(cb: CallbackQuery, app: App) -> None:
    if not cb.data or not cb.message:
        await _call(cb.answer)
        return
    tg, chat = cb.from_user, cb.message.chat
    parts = cb.data.split(":")
    fn = CALLBACKS.get(parts[0])
    if not fn:
        await _call(cb.answer)
        return
    banned = core.get_user(app.db, tg.id)
    if (banned and banned["banned"]) or app.throttled(tg.id, 0.4):
        await _call(cb.answer)
        return
    if parts[0] != "gate" and tg.id not in app.settings.admins:
        gs = gate.active(app.db)
        if gs and await gate.membership(app, tg.id) == "no":                  # the required channel: no button works until they have joined
            await _call(cb.answer, t(_lang_for(app, chat, tg.id), "gate.cb", channel=gate.label(gs)), show_alert=True)
            return
    if parts[0] == "mp":
        # a map button: the session's owner, chat and message are checked FIRST, before the presser is registered or touched in any way.
        # Anyone else gets a short alert; nothing is edited, started, drawn or changed.
        why = mapui.pre_check(app.db, parts, tg.id, chat.id, cb.message.message_id)
        if why:
            await _call(cb.answer, mapui.plain(t(_lang_for(app, chat, tg.id), mapui.DENY.get(why, "mp.stale"))), show_alert=True)
            return
    if not (parts[0] == "sm" and parts[1:2] == ["ask"]):
        app.pending.pop(tg.id, None)                                          # pressing anything else cancels a guided prompt
    lang, _created = _prepare(app, tg, chat)
    c = Ctx(app=app, bot=app.bot, tg=tg, chat=chat, lang=lang, cmd=parts[0], args=parts[1:], cb=cb, msg=cb.message)
    if parts[0] in BOUND and parts[-1] != str(tg.id):
        await _call(cb.answer, t(lang, "not_yours"))
        return
    await run(c, fn, is_cb=True)


# =============================== group membership, payments ==========================================
@router.my_chat_member()
async def on_my_chat_member(ev: ChatMemberUpdated, app: App) -> None:
    chat = ev.chat
    if chat.type not in GROUPS:
        return
    new, old = ev.new_chat_member.status, ev.old_chat_member.status
    if new in ("member", "administrator") and old in ("left", "kicked"):
        social.ensure_group(app.db, chat.id, chat.title or "Group", added_by=ev.from_user.id, lang=app.settings.lang)
        adder = ev.from_user
        if adder and not adder.is_bot:
            core.ensure_user(app.db, adder.id, adder.username, adder.full_name, lang=app.settings.lang)
            try:
                count = await app.bot.get_chat_member_count(chat.id)
            except Exception:
                count = 0
            bonus = social.group_add_bonus(app.db, chat.id, adder.id, count)
            if bonus:
                core.notify(app.db, adder.id, "group_bonus", amount=bonus)
        g = social.get_group(app.db, chat.id)
        await _call(app.bot.send_message, chat.id, t(g["lang"], "group.welcome", brand=t(g["lang"], "brand")))
        await flush_outbox(app)
    elif new in ("left", "kicked"):
        social.deactivate_group(app.db, chat.id)


@router.pre_checkout_query()
async def on_pre_checkout(q: PreCheckoutQuery, app: App) -> None:
    ok = q.currency == "XTR" and q.invoice_payload in C.SHOP_ITEMS and core.get_user(app.db, q.from_user.id) is not None
    await _call(q.answer, ok=ok, error_message=None if ok else "Invalid order")


@router.message(F.successful_payment)
async def on_paid(m: Message, app: App) -> None:
    sp = m.successful_payment
    uid = m.from_user.id
    lang = _lang_for(app, m.chat, uid)
    try:
        first = shop.grant(app.db, uid, sp.invoice_payload, sp.telegram_payment_charge_id, sp.total_amount)
    except Exception:
        log.exception("PAID BUT NOT GRANTED: user=%s item=%s charge=%s", uid, sp.invoice_payload, sp.telegram_payment_charge_id)
        for admin in app.settings.admins:
            await _call(app.bot.send_message, admin, f"⚠️ Paid but not granted: user={uid} item={sp.invoice_payload} charge={sp.telegram_payment_charge_id}")
        await _call(m.answer, t(lang, "err"))
        return
    if first:
        await _call(m.answer, t(lang, "shop.thanks", item=t(lang, f"shop.{sp.invoice_payload}.t")))
