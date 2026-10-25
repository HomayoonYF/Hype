"""Turns game data into Telegram messages (text + inline keyboards)."""
from __future__ import annotations

import json
import urllib.parse
from dataclasses import dataclass

from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup

from . import config as C
from . import cards, docs, salary, sections, achievements, analytics, charts, core, engine as E, events, exchange, fmt, progress, quests, social, trading, trust, world, worldinspect
from .core import display_name, price
from .fmt import esc
from .texts import ALIASES, STR, level_title, t


@dataclass
class Reply:
    text: str | None = None
    kb: InlineKeyboardMarkup | None = None
    photo: bytes | str | None = None
    toast: str | None = None
    alert: bool = False
    edit: bool = False
    after: object | None = None          # called with the id of the message that now shows this reply (see handlers.send)
    pic: object | None = None            # v0.18.1: an async function that draws this reply's picture (job screens); run by handlers.send, never by the caller
    fresh: bool = False                  # v0.18.1: when it replaces a message of the other kind (photo <-> text), delete that message and send a new one

    def __post_init__(self):
        self.text, self.toast = fmt.digits(self.text), fmt.digits(self.toast)      # number style (config.digits_mode); also applied again when sending


def kb(rows: list) -> InlineKeyboardMarkup | None:
    """rows of (label, callback_data) or (label, url, "url")."""
    out = []
    for row in rows:
        btns = []
        for b in row:
            if len(b) == 3:
                btns.append(InlineKeyboardButton(text=fmt.digits(b[0]), url=b[1]))
            else:
                btns.append(InlineKeyboardButton(text=fmt.digits(b[0]), callback_data=b[1]))
        if btns:
            out.append(btns)
    return InlineKeyboardMarkup(inline_keyboard=out) if out else None


KIND_EMOJI = {"personal": "👤", "meme": "🤪", "project": "🎯", "bluechip": "💎", "company": "🏢", "index": "👥", "listed": "🏛"}


# --- stock card ---------------------------------------------------------------------
def stock_card(db, lang: str, s, uid: int | None = None) -> Reply:
    s = core.stock_row(db, s["id"])
    L = lang
    lines = [f"{KIND_EMOJI.get(s['kind'], '📈')} <b>{esc(s['name'])}</b> {fmt.tk(s['ticker'])}"]
    if s["status"] != "active":
        lines.append(t(L, "e.delisted", ticker=fmt.tk(s["ticker"])))
        return Reply(text="\n".join(lines))
    if s["kind"] == "listed":
        return listed_card(db, lang, s, uid)
    if s["descr"]:
        lines.append(f"<i>{esc(s['descr'])}</i>")
    lines.append(t(L, "card.price", price=fmt.price(price(s), L), chg=fmt.pct(core.change_pct(db, s), L)))
    lines.append(t(L, "card.stats", mcap=fmt.coins(core.mcap(s), L), supply=fmt.num(s["supply"], L), holders=fmt.num(s["holders"], L)))
    if s["kind"] in C.OWNER_KINDS and s["owner_id"]:
        o = core.get_user(db, s["owner_id"])
        if o:
            lines.append(t(L, "card.owner", owner=esc(display_name(o)), rep=fmt.num(o["rep"], L)))
        if s["div_pct"]:
            lines.append(t(L, "card.div", pct=fmt.num(s["div_pct"], L)))
    elif s["kind"] == "company":
        c = db.q1("SELECT owner_id FROM companies WHERE id=?", (s["company_id"],))
        o = core.get_user(db, c["owner_id"]) if c else None
        lines.append(t(L, "card.company", owner=esc(display_name(o)) if o else "-"))
    elif s["kind"] == "index":
        lines.append(t(L, "card.index"))
    if s["owner_id"] and s["kind"] in C.OWNER_KINDS:
        tr = trust.score(db, s["owner_id"])
        if tr["voters"]:
            lines.append(t(L, "card.trust", label=t(L, "trust." + tr["label"]), up=fmt.num(tr["up"], L), down=fmt.num(tr["down"], L)))
        if trust.flagged(db, s["id"]):
            n = db.q1("SELECT COUNT(*) c FROM reports WHERE stock_id=? AND status='open'", (s["id"],))["c"]
            lines.append(t(L, "card.flag", n=fmt.num(n, L)))
    if s["goal"]:
        if s["funded"]:
            lines.append(t(L, "card.funded"))
        else:
            frac = s["reserve"] / s["goal"]
            lines.append(t(L, "card.goal", bar=fmt.bar(frac), pct=fmt.num(int(frac * 100), L)))
    if s["event_until"] > core.now():
        lines.append(t(L, "card.event", kind=t(L, "ev." + s["event_kind"]) if s["event_kind"] else "", left=fmt.dur(s["event_until"] - core.now(), L)))
    if s["hype"] >= 1:
        lines.append(t(L, "card.hype", n=fmt.num(round(s["hype"]), L)))
    if s["rugs"]:
        lines.append(t(L, "card.rugs", n=fmt.num(s["rugs"], L)))
    if uid:
        h = db.q1("SELECT shares FROM holdings WHERE user_id=? AND stock_id=?", (uid, s["id"]))
        if h:
            lines.append(t(L, "card.mine", shares=fmt.num(h["shares"], L), value=fmt.coins(core.liq(s, h["shares"]), L)))
    sid = s["id"]
    rows = [
        [(t(L, "btn.buy", n=fmt.num(n, L)), f"b:{sid}:{n}") for n in (1, 10, 100)],
        [(t(L, "btn.sell", n=fmt.num(10, L)), f"s:{sid}:10"), (t(L, "btn.sellall"), f"s:{sid}:0"), (t(L, "btn.buymax"), f"b:{sid}:0")],
        [(t(L, "btn.refresh"), f"st:{sid}"), (t(L, "btn.chart"), f"ch:{sid}:24h"), ("📤", f"scs:{sid}")],
    ]
    if s["owner_id"] and s["kind"] in C.OWNER_KINDS:
        rows.append([(t(L, "btn.trust"), f"tr:{sid}:1"), (t(L, "btn.distrust"), f"tr:{sid}:-1"), (t(L, "btn.report"), f"rp:{sid}")])
    return Reply(text="\n".join(lines), kb=kb(rows))


# --- the exchange (house-priced listed companies) -----------------------------------------
def _pcts(db, L: str, s) -> tuple[str, str]:
    """(fixed levy, slippage right now) as percent strings."""
    spread = fmt.pct(C.LISTED_SPREAD_BPS / 100, L, arrows=False)
    slip = fmt.pct(exchange.slip_bps_x100(exchange.flow_now(db, s["id"]), 0) / 10000, L, arrows=False)
    return spread, slip


def _quote_line(L: str, key: str, row: dict) -> tuple[str, str]:
    """(statement, voice). The voice is the company itself («بیانیه‌ی چت‌باکس»), never a person's name. On earnings night the statement is about the REAL
    numbers of the week (row["act"]: +1 activity grew, -1 it shrank); the price jump itself is random and has nothing to do with it. Headlines
    about a price move use its direction."""
    word = "up" if row.get("act", row.get("jump", row.get("pct", 0))) >= 0 else "down"
    return t(L, f"ex.{key}.{word}{row.get('q', 1)}"), t(L, f"ex.{key}.voice")


def listed_card(db, lang: str, s, uid: int | None = None) -> Reply:
    L = lang
    co = exchange.company_of(db, s)
    name = t(L, f"ex.{co.key}.name") if co else esc(s["name"])
    lines = [f"{co.emoji if co else '🏛'} <b>{name}</b> {fmt.tk(s['ticker'])}"]
    if co:
        lines.append(t(L, "ex.card.tag", tag=t(L, f"ex.{co.key}.tag")))
    lines.append(t(L, "ex.card.rules"))
    lines.append(t(L, "card.price", price=fmt.price(price(s), L), chg=fmt.pct(core.change_pct(db, s), L)))
    lines.append(t(L, "card.stats", mcap=fmt.coins(core.mcap(s), L), supply=fmt.num(s["supply"], L), holders=fmt.num(s["holders"], L)))
    spread, slip = _pcts(db, L, s)
    lines.append(t(L, "ex.card.levy", spread=spread, slip=slip))
    lines.append(t(L, "ex.card.next", left=fmt.dur(max(0, exchange.next_earnings(db) - core.now()), L)))
    rep = exchange.last_report(db)
    row = next((r for r in rep["rows"] if co and r["key"] == co.key), None) if rep else None
    if row:
        quote, voice = _quote_line(L, co.key, row)
        lines.append(t(L, "ex.card.last", jump=fmt.pct(row["jump"], L), voice=esc(voice), quote=esc(quote)))
    if uid:
        h = db.q1("SELECT shares FROM holdings WHERE user_id=? AND stock_id=?", (uid, s["id"]))
        if h:
            lines.append(t(L, "card.mine", shares=fmt.num(h["shares"], L), value=fmt.coins(core.liq(s, h["shares"]), L)))
    sid = s["id"]
    rows = [
        [(t(L, "btn.buy", n=fmt.num(n, L)), f"b:{sid}:{n}") for n in (1, 5, 25)],
        [(t(L, "btn.sell", n=fmt.num(5, L)), f"s:{sid}:5"), (t(L, "btn.sellall"), f"s:{sid}:0"), (t(L, "btn.buymax"), f"b:{sid}:0")],
        [(t(L, "btn.refresh"), f"st:{sid}"), (t(L, "btn.chart"), f"ch:{sid}:24h"), ("📤", f"scs:{sid}"), (t(L, "ex.btn_ex"), "ex:r")],
    ]
    return Reply(text="\n".join(lines), kb=kb(rows))


def exchange_view(db, lang: str, uid: int | None = None) -> Reply:
    L = lang
    mine = {r["stock_id"]: r["shares"] for r in db.qa("SELECT stock_id, shares FROM holdings WHERE user_id=?", (uid,))} if uid else {}
    lines = [t(L, "ex.title"), t(L, "ex.intro"), ""]
    btns, first = [], None
    for co, s in exchange.listed_rows(db):
        first = first or s
        sh = mine.get(s["id"])
        lines.append(t(L, "ex.row", emoji=co.emoji, tk=fmt.tk(s["ticker"]), name=t(L, f"ex.{co.key}.name"), price=fmt.price(price(s), L),
                       chg=fmt.pct(core.change_pct(db, s), L), mine=t(L, "ex.mine", shares=fmt.num(sh, L)) if sh else ""))
        btns.append((f"{co.emoji} {s['ticker']}", f"st:{s['id']}"))
    if first:
        spread, slip = _pcts(db, L, first)
        lines += ["", t(L, "ex.footer", spread=spread, slip=slip, left=fmt.dur(max(0, exchange.next_earnings(db) - core.now()), L))]
    grid = [btns[i:i + 3] for i in range(0, len(btns), 3)]
    grid.append([(t(L, "ex.btn_earn"), "ex:e"), (t(L, "btn.refresh"), "ex:r")])
    return Reply(text="\n".join(lines), kb=kb(grid))


def earnings_text(lang: str, rep: dict, compact: bool = True) -> str:
    """The earnings-night report. compact=True fits a photo caption (1024 chars); False adds the real numbers and the footer."""
    L = lang
    lines = [t(L, "ex.earn.head", week=fmt.num(rep["week"] % 100, L)), ""]
    for r in rep["rows"]:
        co = exchange.BY_KEY.get(r["key"])
        if not co:
            continue
        quote, voice = _quote_line(L, co.key, r)
        lines.append(t(L, "ex.earn.row", emoji=co.emoji, tk=fmt.tk(r["ticker"]), wk=fmt.pct(r["wk_pct"], L), jump=fmt.pct(r["jump"], L)))
        if not compact:
            delta = ""
            if r["prev"]:
                delta = t(L, "ex.earn.delta", delta=fmt.pct((r["metric"] - r["prev"]) * 100.0 / r["prev"], L))
            lines.append(t(L, "ex.earn.metric", metric=t(L, f"ex.{co.key}.metric"), cur=fmt.num(round(r["metric"]), L), delta=delta))
        lines.append(t(L, "ex.earn.quote", quote=esc(quote), voice=esc(voice)))
    if not compact:
        lines += ["", t(L, "ex.earn.foot")]
    return "\n".join(lines)


def earnings_view(db, lang: str) -> Reply:
    rep = exchange.last_report(db)
    back = kb([[(t(lang, "ex.btn_ex"), "ex:r")]])
    if not rep:
        return Reply(text=t(lang, "ex.earn.none", left=fmt.dur(max(0, exchange.next_earnings(db) - core.now()), lang)), kb=back)
    return Reply(text=earnings_text(lang, rep, compact=False), kb=back)


def exchange_news_line(lang: str, row) -> str:
    p = json.loads(row["params"] or "{}")
    co = exchange.BY_KEY.get(p.get("key", ""))
    if not co:
        return ""
    L = lang
    if row["kind"] == "ex_earn":
        quote, voice = _quote_line(L, co.key, {"act": p.get("act", p.get("jump", 0)), "q": p.get("q", 1)})
        return t(L, "ex.news_earn", tk=fmt.tk(p.get("ticker", co.ticker)), wk=fmt.pct(p.get("pct", 0), L), voice=esc(voice), quote=esc(quote))
    up = row["kind"] == "ex_up"
    quote, voice = _quote_line(L, co.key, {"jump": 1 if up else -1, "q": p.get("q", 1)})
    return t(L, "ex.news_up" if up else "ex.news_down", emoji=co.emoji, tk=fmt.tk(p.get("ticker", co.ticker)), pct=fmt.pct(p.get("pct", 0), L, arrows=False), voice=esc(voice), quote=esc(quote))


# --- market ---------------------------------------------------------------------------
def market_view(db, lang: str, view: str = "top") -> Reply:
    L = lang
    view = view if view in ("top", "trend", "gain", "lose", "new") else "top"
    rows = trading.market(db, view, 8)
    tabs = [[(t(L, f"btn.{v}"), f"mk:{v}") for v in ("top", "trend", "gain", "lose", "new")], [(t(L, "ex.btn_ex"), "ex:r")]]
    if not rows:
        return Reply(text=t(L, "market.empty"), kb=kb(tabs))
    lines = [f"<b>{t(L, 'market.' + view)}</b>", ""]
    for i, s in enumerate(rows, 1):
        lines.append(f"{fmt.num(i, L)}. {KIND_EMOJI.get(s['kind'], '')} {fmt.tk(s['ticker'])} {esc(s['name'])} — {fmt.price(price(s), L)} {fmt.pct(core.change_pct(db, s), L)}")
    btns = [(f"${s['ticker']}", f"st:{s['id']}") for s in rows]
    grid = [btns[i:i + 4] for i in range(0, len(btns), 4)]
    return Reply(text="\n".join(lines), kb=kb(tabs + grid))


# --- portfolio / profile ------------------------------------------------------------------
def portfolio_view(db, lang: str, uid: int) -> Reply:
    L = lang
    p = trading.portfolio(db, uid)
    u = p["user"]
    lines = [t(L, "port.head", name=esc(display_name(u))), t(L, "port.cash", cash=fmt.coins(u["cash"], L), nw=fmt.coins(p["networth"], L))]
    if u["pending_div"] or u["pending_inc"]:
        lines.append(t(L, "port.pending", div=fmt.coins(u["pending_div"], L), inc=fmt.coins(u["pending_inc"], L)))
    lines.append("")
    if not p["rows"]:
        lines.append(t(L, "port.empty"))
    for r in p["rows"]:
        lines.append(t(L, "port.row", tk=fmt.tk(r["stock"]["ticker"]), shares=fmt.num(r["shares"], L), value=fmt.coins(r["value"], L), pnl=fmt.pct(r["pct"], L)))
    rows = [[(t(L, "btn.claim") + " " + ("سود" if L == "fa" else "dividends"), f"cl:div:{uid}"), (t(L, "btn.claim") + " " + ("درآمد" if L == "fa" else "income"), f"cl:inc:{uid}")],
            [(t(L, "btn.refresh"), f"pf:{uid}"), ("📤", f"scp:{uid}")]]
    return Reply(text="\n".join(lines), kb=kb(rows))


def profile_view(db, lang: str, uid: int) -> Reply:
    L = lang
    info = progress.level_info(db, uid)
    u = info["user"]
    stocks = ", ".join(fmt.tk(s["ticker"]) for s in trading.my_stocks(db, uid)) or t(L, "none")
    company = t(L, "none")
    if u["company_id"]:
        c = db.q1("SELECT name FROM companies WHERE id=?", (u["company_id"],))
        company = esc(c["name"]) if c else t(L, "none")
    text = t(L, "profile", name=esc(display_name(u)), level=fmt.num(info["level"], L), title=level_title(L, info["level"]), cash=fmt.coins(u["cash"], L),
             nw=fmt.coins(info["nw"], L), rep=fmt.num(u["rep"], L), rb=fmt.num(u["rebirths"], L), streak=fmt.num(u["streak"], L), company=company, stocks=stocks,
             ach=f"{fmt.num(len(achievements.unlocked(db, uid)), L)}/{fmt.num(len(achievements.ALL_A), L)}")
    return Reply(text=text, kb=kb([[("📤", f"scr:{uid}")]]))


def level_view(db, lang: str, uid: int) -> Reply:
    L = lang
    i = progress.level_info(db, uid)
    text = t(L, "level", level=fmt.num(i["level"], L), title=level_title(L, i["level"]), bar=fmt.bar(i["frac"]), pct=fmt.pct(i["frac"] * 100, L, arrows=False),
             nw=fmt.coins(i["nw"], L), next=fmt.coins(i["next"], L), free=fmt.num(i["points_free"], L), total=fmt.num(i["points_total"], L))
    if i["levelup"]:
        text += "\n\n" + t(L, "levelup", level=fmt.num(i["levelup"], L), title=level_title(L, i["levelup"]))
    return Reply(text=text)


def rep_view(db, lang: str, uid: int) -> Reply:
    r = progress.reputation_info(db, uid)
    return Reply(text=t(lang, "rep", rep=fmt.num(r["rep"], lang), mult=fmt.num(r["launch_mult"], lang), rugs=fmt.num(r["rugs"], lang)))


# --- perks ---------------------------------------------------------------------------------
def perks_view(db, lang: str, uid: int, kind: str) -> Reply:
    L = lang
    u = core.need_user(db, uid)
    items = progress.perk_list(db, uid, kind)
    if kind == "skill":
        head = t(L, "skills.head", free=fmt.num(progress.points_free(u), L))
    else:
        head = t(L, "legacy.head", pts=fmt.num(u["legacy_pts"], L), total=fmt.num(u["legacy_total"], L))
    lines = [head, ""]
    btns = []
    for it in items:
        name = t(L, f"perk.{it['key']}.name")
        cost = t(L, "perk.maxed") if it["cost"] is None else t(L, "perk.cost", cost=fmt.num(it["cost"], L))
        lines.append(t(L, "perk.row", name=name, lvl=fmt.num(it["level"], L), max=fmt.num(it["max"], L), cost=cost, desc=t(L, f"perk.{it['key']}.desc")))
        if it["cost"] is not None:
            btns.append((t(L, "perk.btn", name=name, cost=fmt.num(it["cost"], L)), f"up:{it['key']}:{uid}"))
    rows = [btns[i:i + 2] for i in range(0, len(btns), 2)]
    rows.append([("🌳", f"sk:{uid}"), ("🏛", f"lg:{uid}")])
    return Reply(text="\n".join(lines), kb=kb(rows))


def rebirth_view(db, lang: str, uid: int) -> Reply:
    L = lang
    i = progress.rebirth_info(db, uid)
    text = t(L, "rb.info", need=fmt.coins(i["need"], L), nw=fmt.coins(i["nw"], L), gain=fmt.num(i["gain"], L), count=fmt.num(i["count"] + 1, L))
    rows = [[(t(L, "btn.yes"), f"rb:ok:{uid}")] if i["ok"] else [], [(t(L, "btn.cancel"), f"rb:no:{uid}")]]
    return Reply(text=text, kb=kb(rows))


# --- company ---------------------------------------------------------------------------------
def company_view(db, lang: str, uid: int) -> Reply:
    L = lang
    info = social.company_info(db, uid)
    c, members, st = info["company"], info["members"], info["stock"]
    owner = next((m for m in members if m["id"] == c["owner_id"]), None)
    mem_lines = "\n".join(f"• {esc(display_name(m))} — {fmt.coins(m['nw'], L)}" for m in members)
    head = t(L, "company.head", name=esc(c["name"]), tk=fmt.tk(st["ticker"]) if st else "")
    body = t(L, "company.body", owner=esc(display_name(owner)) if owner else "-", n=fmt.num(len(members), L), max=fmt.num(C.COMPANY_MAX_MEMBERS, L),
             members=mem_lines, treasury=fmt.coins(c["treasury"], L), syn=fmt.num(min(20, (len(members) - 1) * C.COMPANY_SYNERGY_PER_MEMBER), L))
    rows = [[(f"${st['ticker']}", f"st:{st['id']}")]] if st else []
    return Reply(text=head + "\n" + body, kb=kb(rows))


# --- news / events / season ---------------------------------------------------------------------
INT_KEYS = {"pct", "minutes", "n", "season", "shares"}


def render_news(lang: str, row) -> str:
    if row["kind"] in ("ex_up", "ex_down", "ex_earn"):
        return exchange_news_line(lang, row)
    kw = {}
    for k, v in json.loads(row["params"] or "{}").items():
        if k == "ticker":
            kw["tk"] = fmt.tk(v)
        elif k == "amount":
            kw[k] = fmt.coins(v, lang)
        elif k in INT_KEYS:
            kw[k] = fmt.num(v, lang)
        else:
            kw[k] = esc(v)
    return t(lang, "news." + row["kind"], **kw)


def news_view(db, lang: str, limit: int = 10) -> Reply:
    rows = db.qa("SELECT * FROM news ORDER BY id DESC LIMIT ?", (limit,))
    if not rows:
        return Reply(text=t(lang, "news.head") + "\n\n" + t(lang, "news.empty"))
    return Reply(text=t(lang, "news.head") + "\n\n" + "\n".join(render_news(lang, r) for r in rows))


def events_view(db, lang: str) -> Reply:
    L = lang
    ev = events.active_events(db)
    lines = [t(L, "ev.head"), ""]
    shown = False
    if ev["fee_free"]:
        lines.append(t(L, "ev.feefree", left=fmt.dur(ev["fee_free"], L)))
        shown = True
    g = ev["global"]
    if g:
        pct = (g["bps"] - C.BPS) / 100
        lines.append(t(L, "ev.global", kind=t(L, "ev." + g["kind"]), pct=fmt.pct(pct, L), left=fmt.dur(g["until"] - core.now(), L)))
        shown = True
    for r in ev["stocks"]:
        pct = (r["event_bps"] - C.BPS) / 100
        lines.append(f"{fmt.tk(r['ticker'])} — {t(L, 'ev.' + r['event_kind']) if r['event_kind'] else ''} {fmt.pct(pct, L)} • {fmt.dur(r['event_until'] - core.now(), L)}")
        shown = True
    if not shown:
        lines.append(t(L, "ev.none"))
    return Reply(text="\n".join(lines))


def season_view(db, lang: str) -> Reply:
    L = lang
    info = progress.season_info(db)
    lines = [t(L, "season.head", id=fmt.num(info["id"], L), left=fmt.dur(info["left"], L))]
    top = progress.season_standings(db, 10)
    if not top:
        lines.append(t(L, "season.empty"))
    medals = ["🥇", "🥈", "🥉"]
    for i, r in enumerate(top):
        mark = medals[i] if i < 3 else f"{fmt.num(i + 1, L)}."
        lines.append(f"{mark} {esc(display_name(r))} — {fmt.pnl(r['gain'], L)}")
    p = [f"{fmt.coins(c, L)} + {fmt.num(lp, L)}🏛" for c, lp in C.SEASON_REWARDS]
    lines.append(t(L, "season.prizes", p1=p[0], p2=p[1], p3=p[2]))
    return Reply(text="\n".join(lines))


# --- leaderboards -------------------------------------------------------------------------------
def _rows(lang: str, rows) -> str:
    medals = ["🥇", "🥈", "🥉"]
    out = []
    for i, r in enumerate(rows):
        mark = medals[i] if i < 3 else f"{fmt.num(i + 1, lang)}."
        out.append(t(lang, "lb.row", i=mark, name=esc(display_name(r)), nw=fmt.coins(r["nw"], lang), level=fmt.num(r["max_level"], lang)).replace(f"{mark}. ", f"{mark} ", 1))
    return "\n".join(out)


def leaderboard_view(db, lang: str, chat_id: int) -> Reply:
    rows = social.group_leaderboard(db, chat_id, 10)
    return Reply(text=t(lang, "lb.head") + "\n\n" + (_rows(lang, rows) if rows else t(lang, "season.empty")))


def top_view(db, lang: str) -> Reply:
    rows = social.global_top(db, 10)
    return Reply(text=t(lang, "top.head") + "\n\n" + (_rows(lang, rows) if rows else t(lang, "season.empty")))


def grouprank_view(db, lang: str) -> Reply:
    rows = social.group_ranking(db, 10)
    medals = ["🥇", "🥈", "🥉"]
    lines = [t(lang, "grouprank.head"), ""]
    for i, r in enumerate(rows):
        mark = medals[i] if i < 3 else f"{fmt.num(i + 1, lang)}."
        lines.append(f"{mark} {esc(r['title'])} — {fmt.coins(r['score'], lang)}")
    if not rows:
        lines.append(t(lang, "season.empty"))
    return Reply(text="\n".join(lines))


def group_view(db, lang: str, chat_id: int) -> Reply:
    L = lang
    i = social.group_info(db, chat_id)
    g, idx = i["group"], i["index"]
    text = t(L, "group.info", title=esc(g["title"]), rank=fmt.num(i["rank"], L), members=fmt.num(i["members"], L), score=fmt.coins(g["score"], L),
             tk=fmt.tk(idx["ticker"]) if idx else "", price=fmt.price(price(idx), L) if idx else "")
    if i["top"]:
        text += t(L, "group.top", rows=_rows(L, i["top"]))
    return Reply(text=text, kb=kb([[(f"${idx['ticker']}", f"st:{idx['id']}")]]) if idx else None)


def settings_view(db, lang: str, chat_id: int) -> Reply:
    L = lang
    g = social.get_group(db, chat_id)
    text = t(L, "settings", lang=t(g["lang"], "lang_name"), mode=t(L, "mode." + g["news_mode"]), cap=fmt.num(g["msg_cap"], L))
    return Reply(text=text, kb=kb([[(t(L, "btn.lang"), f"set:lang:{chat_id}"), (t(L, "btn.mode"), f"set:mode:{chat_id}"), (t(L, "btn.cap"), f"set:cap:{chat_id}")]]))


# --- shop / alerts --------------------------------------------------------------------------------
def shop_view(lang: str) -> Reply:
    rows = [[(t(lang, "shop.item", name=t(lang, f"shop.{k}.t"), stars=fmt.num(v["stars"], lang)), f"shop:{k}")] for k, v in C.SHOP_ITEMS.items()]
    return Reply(text=t(lang, "shop.head"), kb=kb(rows))


def alerts_view(db, lang: str, uid: int) -> Reply:
    rows = trading.list_alerts(db, uid)
    if not rows:
        return Reply(text=t(lang, "alerts_empty"))
    lines = [t(lang, "alerts_head")]
    for a in rows:
        lines.append(f"{fmt.tk(a['ticker'])} {t(lang, 'dir.' + a['direction'])} {fmt.price(a['price'], lang)}")
    return Reply(text="\n".join(lines))


# --- group board / direct messages -----------------------------------------------------------------
def board_text(db, lang: str) -> str:
    L = lang
    rows = [s for s in trading.market(db, "top", 20) if s["kind"] != "listed"][:5]          # the exchange has its own block below
    listed = exchange.listed_rows(db)
    lines = [t(L, "board.head", brand=t(L, "brand")), ""]
    for s in rows:
        lines.append(f"{KIND_EMOJI.get(s['kind'], '')} {fmt.tk(s['ticker'])} {fmt.price(price(s), L)} {fmt.pct(core.change_pct(db, s), L)}")
    if not rows and not listed:
        lines.append(t(L, "market.empty"))
    if listed:
        lines += ["", t(L, "board.ex")]
        for co, s in listed:
            lines.append(f"{co.emoji} {fmt.tk(s['ticker'])} {fmt.price(price(s), L)} {fmt.pct(core.change_pct(db, s), L)}")
    news = db.qa("SELECT * FROM news ORDER BY id DESC LIMIT 3")
    if news:
        lines += ["", t(L, "board.news")] + [render_news(L, r) for r in news]
    return "\n".join(lines)


def dm_text(lang: str, key: str, kw: dict) -> str:
    out = {}
    if key == "quest_done":
        pts = t(lang, "quests.pts", n=fmt.num(kw["points"], lang)) if kw.get("points") else ""
        return t(lang, "n.quest_done", text=quest_text(lang, kw["qid"], kw["n"]), amount=fmt.coins(kw["amount"], lang), pts=pts)
    if key == "ach_done":
        a = achievements.BY_ID[kw["aid"]]
        pts = t(lang, "quests.pts", n=fmt.num(kw["points"], lang)) if kw.get("points") else ""
        return t(lang, "n.ach_done", emoji=a.emoji, text=t(lang, "a." + a.id), amount=fmt.coins(kw["amount"], lang), pts=pts)
    if key == "levelup":
        out["title"] = level_title(lang, kw["level"])
    for k, v in kw.items():
        if k == "ticker":
            out["tk"] = fmt.tk(v)
        elif k in ("amount", "payout", "coins"):
            out[k] = fmt.coins(v, lang)
        elif k == "cents":                                       # life cash (v0.18): whole cents, shown as $
            out[k] = fmt.usd(v, lang)
        elif k in ("price", "target"):
            out[k] = fmt.price(v, lang)
        elif isinstance(v, int):
            out[k] = fmt.num(v, lang)
        else:
            out[k] = esc(v)
    return t(lang, "n." + key, **out)


# --- help menu, guide, quests, whales, chart caption -------------------------------------------------------
def quest_text(lang: str, qid: str, n: int) -> str:
    q = quests.ALL[qid]
    shown = fmt.num(n // C.UNIT, lang) + " 🪙" if q.metric in quests.MONEY_METRICS else fmt.num(n, lang)
    return t(lang, "q." + qid, n=shown)


def quest_done_lines(lang: str, items: list) -> str:
    out = []
    for qid, reward, points, target in items:
        pts = t(lang, "quests.pts", n=fmt.num(points, lang)) if points else ""
        out.append(t(lang, "quest.done", text=quest_text(lang, qid, target), reward=fmt.coins(reward, lang), pts=pts))
    return "\n".join(out)


# --- the global menu, the stock section menu, the help hub ----------------------------------------------------------
MAIN_GO = ("profile", "daily", "invite", "map", "cash", "jobs")          # global screens the main menu opens (each one is an ordinary command)
STOCK_GO = tuple(sorted(sections.MENU_SCREENS))   # screens a stock-menu button may open (ordinary commands, run with no arguments)
ORIGINS = frozenset({"m"} | {cid for cid, _ in sections.STOCK_CATEGORIES})     # where a button was pressed: "m" = the stock menu, else a category


def main_menu(lang: str, uid: int) -> Reply:
    rows = [[(t(lang, "menu.stocks"), f"sm:menu:{uid}"), (t(lang, "menu.help"), f"hp:menu:{uid}")],
            [(t(lang, "menu.profile"), f"mn:go:profile:{uid}"), (t(lang, "menu.daily"), f"mn:go:daily:{uid}")],
            [(t(lang, "menu.invite"), f"mn:go:invite:{uid}"), (t(lang, "menu.map"), f"mn:go:map:{uid}")],
            [(t(lang, "menu.cash"), f"mn:go:cash:{uid}"), (t(lang, "menu.jobs"), f"mn:go:jobs:{uid}")]]
    return Reply(text=t(lang, "menu.title", brand=t(lang, "brand")), kb=kb(rows))


def map_caption(lang: str, w, layout: dict, status: str = "") -> str:
    """The text under the city picture. `status` (the viewer's position, walk and neighbours) goes right under the title line."""
    st = world.stats(layout)
    name = w["name_fa"] if lang == "fa" else w["name_en"]
    cap = t(lang, "map.caption", name=fmt.esc(name), vacant=fmt.num(st["vacant"], lang), parcels=fmt.num(st["parcels"], lang))
    if status:
        head, _, rest = cap.partition("\n")
        cap = head + "\n" + status + "\n" + rest
    return cap + "\n\n" + t(lang, "map.hint", example=worldinspect.example_id(layout), places=worldinspect.places_line(lang, layout))


def back_button(lang: str, origin: str, uid: int) -> tuple:
    """(label, callback) that leads back to where a stock-menu button was pressed."""
    if origin in ORIGINS and origin != "m":
        return t(lang, "sm.back_to", name=t(lang, f"sm.catname.{origin}")), f"sm:cat:{origin}:{uid}"
    return t(lang, "sm.back"), f"sm:menu:{uid}"


def cmd_button(lang: str, cmd: str, origin: str, uid: int) -> tuple:
    """The button of one command: a screen opens in place, typed input is guided, coin-moving actions ask first."""
    label = t(lang, f"sm.{cmd}")
    if cmd in sections.ASK_CMDS:
        return label, f"sm:ask:{cmd}:{origin}:{uid}"
    if cmd in sections.SURE_CMDS:
        return label, f"sm:sure:{cmd}:{origin}:{uid}"
    return label, f"sm:go:{sections.SCREEN_OF.get(cmd, cmd)}:{origin}:{uid}"


def stock_menu(lang: str, uid: int) -> Reply:
    """The stock section's landing page: shortcuts to the everyday screens, then one button per category (every command is inside one)."""
    rows = [[cmd_button(lang, c, "m", uid) for c in row] for row in sections.STOCK_QUICK]
    cats = [(t(lang, f"sm.cat.{cid}"), f"sm:cat:{cid}:{uid}") for cid, _ in sections.STOCK_CATEGORIES]
    rows += [cats[i:i + 2] for i in range(0, len(cats), 2)]
    rows.append([(t(lang, "sm.help"), f"hp:stock:{uid}")])
    rows.append([(t(lang, "menu.back"), f"mn:menu:{uid}")])
    return Reply(text=t(lang, "sm.title"), kb=kb(rows))


def stock_category(lang: str, uid: int, cid: str) -> Reply:
    cmds = dict(sections.STOCK_CATEGORIES).get(cid)
    if not cmds:
        return stock_menu(lang, uid)
    btns = [cmd_button(lang, c, cid, uid) for c in cmds]
    rows = [btns[i:i + 2] for i in range(0, len(btns), 2)]
    rows.append([(t(lang, "sm.back"), f"sm:menu:{uid}")])
    return Reply(text=t(lang, "sm.cat_head", label=esc(t(lang, f"sm.cat.{cid}"))), kb=kb(rows))


def _doc(lang: str, cmd: str) -> tuple:
    """(description, first canonical example) of a command, from the command reference."""
    fa_d, fa_e, en_d, en_e = docs.DOCS[cmd]
    return (esc(fa_d), fa_e.split("\n")[0]) if lang == "fa" else (esc(en_d), en_e.split("\n")[0])


def ask_view(lang: str, uid: int, cmd: str, origin: str, private: bool) -> Reply:
    """A command that needs typed input. Private chat: the next message of the player is taken as the arguments. Group: show what to type."""
    desc, full = _doc(lang, cmd)
    note_key = f"ask.note.{cmd}"
    note = t(lang, "ask.note", text=t(lang, note_key)) if note_key in STR[lang] else ""
    label = esc(t(lang, f"sm.{cmd}"))
    if private:
        text = t(lang, "ask.private", label=label, desc=desc, fmt=esc(t(lang, f"ask.fmt.{cmd}")), note=note, mins=fmt.num(sections.GUIDED_TTL // 60, lang))
    else:
        text = t(lang, "ask.group", label=label, desc=desc, full=esc(full), note=note)
    rows = []
    if cmd in sections.ASK_ALSO:
        rows.append([(t(lang, f"sm.view_{cmd}"), f"sm:go:{sections.ASK_ALSO[cmd]}:{origin}:{uid}")])
    rows.append([(t(lang, "sm.cancel"), f"sm:cancel:{origin}:{uid}")])
    return Reply(text=text, kb=kb(rows))


def sure_view(lang: str, uid: int, cmd: str, origin: str) -> Reply:
    desc, _full = _doc(lang, cmd)
    rows = [[(t(lang, "sm.yes"), f"sm:do:{cmd}:{origin}:{uid}"), (t(lang, "sm.no"), f"sm:cancel:{origin}:{uid}")]]
    return Reply(text=t(lang, "sm.sure", label=esc(t(lang, f"sm.{cmd}")), desc=desc), kb=kb(rows))


def with_back(r: Reply, label: str, data: str) -> Reply:
    """The screen of an existing command, opened from a menu: same text and buttons plus one way back (the message is edited in place)."""
    if r is None or not isinstance(r, Reply) or r.photo:
        return r
    rows = [list(row) for row in (r.kb.inline_keyboard if r.kb else [])]
    rows.append([InlineKeyboardButton(text=label, callback_data=data)])
    r.kb = InlineKeyboardMarkup(inline_keyboard=rows)
    r.edit = True
    return r


def help_menu(lang: str, uid: int) -> Reply:
    """The global help hub: stock help, and how the game works."""
    rows = [[(t(lang, "help.btn_stock"), f"hp:stock:{uid}")], [(t(lang, "help.btn_about"), f"hp:about:{uid}")], [(t(lang, "menu.back"), f"mn:menu:{uid}")]]
    return Reply(text=t(lang, "help.menu", brand=t(lang, "brand")), kb=kb(rows))


def stock_help_menu(lang: str, uid: int) -> Reply:
    """Stock help: the starter guide and the command reference."""
    rows = [[(t(lang, "help.btn_guide"), f"hp:guide:{uid}")], [(t(lang, "help.btn_cmds"), f"hp:cmds:{uid}")], [(t(lang, "help.btn_back"), f"hp:menu:{uid}")]]
    return Reply(text=t(lang, "help.stock"), kb=kb(rows))


def cmd_name(lang: str, cmd: str) -> str:
    words = ALIASES[cmd]
    persian = [w for w in words if not w.isascii()]
    return (persian[0] if lang == "fa" and persian else next((w for w in words if w.isascii()), words[0]))


def visible_categories(is_admin: bool) -> list:
    return [(i, cid, cmds) for i, (cid, cmds) in enumerate(docs.CATEGORIES) if is_admin or cid != "botadmin"]


def help_commands_menu(lang: str, uid: int, is_admin: bool) -> Reply:
    btns = [(f"{docs.CATEGORY_EMOJI[cid]} {t(lang, 'cat.' + cid)}", f"hp:cat:{i}:{uid}") for i, cid, _ in visible_categories(is_admin)]
    rows = [btns[k:k + 2] for k in range(0, len(btns), 2)]
    rows.append([(t(lang, "help.tips_btn"), f"hp:tips:{uid}")])
    rows.append([(t(lang, "help.btn_back_stock"), f"hp:stock:{uid}")])
    return Reply(text=t(lang, "help.cmds_menu"), kb=kb(rows), edit=True)


def help_category(lang: str, uid: int, idx: int, is_admin: bool) -> Reply:
    if not 0 <= idx < len(docs.CATEGORIES) or (docs.CATEGORIES[idx][0] == "botadmin" and not is_admin):
        return help_commands_menu(lang, uid, is_admin)
    cid, cmds = docs.CATEGORIES[idx]
    lines = [f"{docs.CATEGORY_EMOJI[cid]} <b>{t(lang, 'cat.' + cid)}</b>", ""]
    for c in cmds:
        fa_d, fa_e, en_d, en_e = docs.DOCS[c]
        desc, ex = (fa_d, fa_e) if lang == "fa" else (en_d, en_e)
        lines.append(t(lang, "help.cat_row", name=esc(cmd_name(lang, c)), desc=esc(desc), examples="\n".join(f"   <code>{esc(x)}</code>" for x in ex.split("\n"))))
        lines.append("")
    rows = [[(t(lang, "help.btn_cmds"), f"hp:cmds:{uid}"), (t(lang, "help.btn_back_stock"), f"hp:stock:{uid}")]]
    return Reply(text="\n".join(lines).rstrip(), kb=kb(rows), edit=True)


def help_section(db, lang: str, uid: int, section: str, arg: str = "", is_admin: bool = False) -> Reply:
    if section == "cmds":
        return help_commands_menu(lang, uid, is_admin)
    if section == "cat":
        return help_category(lang, uid, int(arg) if arg.isdigit() else -1, is_admin)
    if section == "tips":
        return Reply(text=t(lang, "help.tips"), kb=kb([[(t(lang, "help.btn_cmds"), f"hp:cmds:{uid}")]]), edit=True)
    back = kb([[(t(lang, "help.btn_back"), f"hp:menu:{uid}")]])
    if section == "about":
        return Reply(text=t(lang, "help.about", ex=t(lang, "help.ex")), kb=back, edit=True)
    if section == "guide":
        r = guide_view(db, lang, uid)
        r.edit = True
        return r
    r = stock_help_menu(lang, uid) if section == "stock" else help_menu(lang, uid)
    r.edit = True
    return r


def guide_view(db, lang: str, uid: int) -> Reply:
    steps = quests.listing(db, uid)["starter"]
    done = sum(1 for x in steps if x["row"]["done"])
    lines = [t(lang, "guide.head", done=fmt.num(done, lang), total=fmt.num(len(steps), lang))]
    nxt = None
    for x in steps:
        mark = "✅" if x["row"]["done"] else "⬜"
        lines.append(f"{mark} {t(lang, 'q.' + x['q'].id)} — {fmt.coins(x['row']['reward'], lang)}")
        if nxt is None and not x["row"]["done"]:
            nxt = x
    if nxt:
        lines.append(t(lang, "guide.next", text=t(lang, "g." + nxt["q"].id, reward=fmt.coins(nxt["row"]["reward"], lang))))
    else:
        lines.append(t(lang, "guide.done"))
    rows = [[(t(lang, "help.btn_back_stock"), f"hp:stock:{uid}"), ("🎯", f"qs:{uid}")]]
    return Reply(text="\n".join(lines), kb=kb(rows))


def _qnum(q, v: int, lang: str) -> str:
    return fmt.num(v // C.UNIT, lang) if q.metric in quests.MONEY_METRICS else fmt.num(v, lang)


def _quest_rows(lang: str, items: list) -> list:
    out = []
    for x in items:
        q, row = x["q"], x["row"]
        pts = t(lang, "quests.pts", n=fmt.num(row["points"], lang)) if row["points"] else ""
        if row["target"] == 1:      # one-step quest: a single line is enough
            out.append(t(lang, "quests.row1", mark="✅" if row["done"] else "⬜", text=quest_text(lang, q.id, row["target"]), reward=fmt.coins(row["reward"], lang), pts=pts))
            continue
        out.append(t(lang, "quests.row", mark="✅" if row["done"] else "⬜", text=quest_text(lang, q.id, row["target"]), bar=fmt.bar(row["progress"] / max(1, row["target"]), 8),
                     prog=_qnum(q, row["progress"], lang), target=_qnum(q, row["target"], lang), reward=fmt.coins(row["reward"], lang), pts=pts))
    return out


def quests_view(db, lang: str, uid: int) -> Reply:
    data = quests.listing(db, uid)
    lines = [t(lang, "quests.head")]
    st = data["starter"]
    done = sum(1 for x in st if x["row"]["done"])
    if done < len(st):
        lines.append(t(lang, "quests.starter", done=fmt.num(done, lang), total=fmt.num(len(st), lang)))
        lines += _quest_rows(lang, st)
    lines.append(t(lang, "quests.daily", left=fmt.dur(quests.seconds_left("daily"), lang)))
    lines += _quest_rows(lang, data["daily"])
    lines.append(t(lang, "quests.weekly", left=fmt.dur(quests.seconds_left("weekly"), lang)))
    lines += _quest_rows(lang, data["weekly"])
    lines.append(t(lang, "quests.footer"))
    return Reply(text="\n".join(lines), kb=kb([[(t(lang, "btn.refresh"), f"qs:{uid}"), ("🧭", f"hp:guide:{uid}")]]))


def whales_view(db, lang: str, uid: int) -> Reply:
    rows = db.qa("SELECT * FROM news WHERE kind LIKE 'whale_%' ORDER BY id DESC LIMIT 10")
    lines = [t(lang, "whales.head"), ""]
    lines += [render_news(lang, r) for r in rows] if rows else [t(lang, "whales.empty", min=fmt.coins(C.WHALE_MIN, lang))]
    u = core.need_user(db, uid)
    on = bool(u["whale_dm"])
    lines += ["", t(lang, "whales.dm_on" if on else "whales.dm_off")]
    return Reply(text="\n".join(lines), kb=kb([[(t(lang, "whales.btn_off" if on else "whales.btn_on"), f"wh:{uid}")]]))


def _chart_misc(db, L: str, s) -> str:
    if s["kind"] == "listed":
        co = exchange.company_of(db, s)
        spread, _slip = _pcts(db, L, s)
        return t(L, "ex.chart_misc", spread=spread, tag=t(L, f"ex.{co.key}.tag") if co else "")
    return t(L, "chart.misc", div=fmt.num(s["div_pct"], L), fees=fmt.coins(s["fees_earned"], L), hype=fmt.num(round(s["hype"]), L), rugs=fmt.num(s["rugs"], L))


def chart_caption(db, lang: str, s, d: dict) -> str:
    L = lang
    co = exchange.company_of(db, s)
    lines = [t(L, "chart.title", emoji=KIND_EMOJI.get(s["kind"], "📈"), name=t(L, f"ex.{co.key}.name") if co else esc(s["name"]), tk=fmt.tk(s["ticker"]), span=d["span"]),
             t(L, "chart.price", price=fmt.price(d["close"], L), chg=fmt.pct(d["change"], L)),
             t(L, "chart.range", high=fmt.price(d["high"], L), low=fmt.price(d["low"], L)),
             t(L, "chart.open", open=fmt.price(d["open"], L), close=fmt.price(d["close"], L)),
             t(L, "chart.volume", vol=fmt.coins(d["volume"], L)),
             t(L, "chart.market", mcap=fmt.coins(core.mcap(s), L), supply=fmt.num(s["supply"], L), holders=fmt.num(s["holders"], L)),
             t(L, "chart.changes", h1=fmt.pct(d["h1"], L), d1=fmt.pct(d["d1"], L), d7=fmt.pct(d["d7"], L)),
             _chart_misc(db, L, s)]
    if s["event_until"] > core.now() and s["event_kind"]:
        lines.append(t(L, "chart.event", kind=t(L, "ev." + s["event_kind"]), left=fmt.dur(s["event_until"] - core.now(), L)))
    if d["whales"]:
        w = d["whales"][-1]
        lines.append(t(L, "chart.whale", who=esc(w["who"]), side=t(L, "chart.side_" + w["side"]), amount=fmt.coins(w["amount"], L), ago=fmt.dur(core.now() - w["ts"], L)))
    lines.append("<i>" + t(L, "chart.legend") + "</i>")
    return "\n".join(lines)


# --- achievements, trust, moderation, analytics -----------------------------------------------------------
def ach_done_lines(lang: str, items: list) -> str:
    out = []
    for aid, reward, points in items:
        a = achievements.BY_ID[aid]
        pts = t(lang, "quests.pts", n=fmt.num(points, lang)) if points else ""
        out.append(t(lang, "ach.done", emoji=a.emoji, text=t(lang, "a." + aid), reward=fmt.coins(reward, lang), pts=pts))
    return "\n".join(out)


def achievements_view(db, lang: str, uid: int) -> Reply:
    got = achievements.unlocked(db, uid)
    lines = [t(lang, "ach.head", done=fmt.num(len(got), lang), total=fmt.num(len(achievements.ALL_A), lang)), ""]
    for a in achievements.ALL_A:
        if a.id in got:
            lines.append(t(lang, "ach.row", emoji=a.emoji, text=t(lang, "a." + a.id)))
    for a in achievements.ALL_A:
        if a.id not in got:
            lines.append(t(lang, "ach.row_locked", text=t(lang, "a." + a.id)))
    lines.append(t(lang, "ach.footer"))
    return Reply(text="\n".join(lines))


def trust_label(lang: str, tr: dict) -> str:
    return t(lang, "trust." + tr["label"])


def trust_result(db, lang: str, r: dict) -> str:
    key = {1: "trust.up", -1: "trust.down", 0: "trust.removed"}[r["value"]]
    tr = r["score"]
    return t(lang, key, name=esc(display_name(r["target"])), label=trust_label(lang, tr), up=fmt.num(tr["up"], lang), down=fmt.num(tr["down"], lang))


def trust_board_view(db, lang: str) -> Reply:
    b = trust.trust_board(db)
    row = lambda rs: "\n".join(t(lang, "trust.row", name=esc(display_name(r)), net=fmt.num(r["net"], lang), voters=fmt.num(r["voters"], lang)) for r in rs) or t(lang, "trust.none")
    return Reply(text=t(lang, "trust.board", good=row(b["good"]), bad=row(b["bad"])))


def report_menu(lang: str, s) -> Reply:
    sid = s["id"]
    rows = [[(t(lang, f"reason.{r}"), f"rp2:{sid}:{r}")] for r in trust.REASONS]
    return Reply(text=t(lang, "report.menu", tk=fmt.tk(s["ticker"])), kb=kb(rows))


def mod_alert(db, lang: str, sid: int) -> Reply:
    sm = trust.summary(db, sid)
    s, o = sm["stock"], sm["owner"]
    reasons = "  ".join(f"{t(lang, 'reason.' + k)}: {fmt.num(v, lang)}" for k, v in sm["reasons"].items())
    notes = "\n".join(f"• {esc(n)}" for n in sm["notes"])
    tr = sm["trust"]
    text = t(lang, "mod.alert", tk=fmt.tk(s["ticker"]), name=esc(s["name"]), owner=esc(display_name(o)) if o else "-", oid=o["id"] if o else "-", rep=fmt.num(o["rep"], lang) if o else "-",
             trust=f"{trust_label(lang, tr)} ({fmt.num(tr['up'], lang)}👍 {fmt.num(tr['down'], lang)}👎)" if tr else "-", mcap=fmt.coins(core.mcap(s), lang), holders=fmt.num(s["holders"], lang),
             rugs=fmt.num(s["rugs"], lang), n=fmt.num(sm["reporters"], lang), w=fmt.num(sm["weight"], lang), reasons=reasons,
             notes=("\n" + t(lang, "mod.notes", notes=notes)) if notes else "")
    rows = [[(t(lang, "mod.btn_delist"), f"mod:delist:{sid}"), (t(lang, "mod.btn_ban"), f"mod:ban:{sid}")], [(t(lang, "mod.btn_dismiss"), f"mod:dismiss:{sid}")]]
    return Reply(text=text, kb=kb(rows))


def dashboard_view(db, lang: str) -> Reply:
    d = analytics.dashboard(db)
    L = lang
    pl, rt, ec, mk, en, mo, gr = d["players"], d["retention"], d["economy"], d["market"], d["engagement"], d["moderation"], d["groups"]
    ex = exchange.house_stats(db)
    pc = lambda v: fmt.pct(v, L, arrows=False) if v is not None else "—"
    n = lambda v: fmt.num(v, L)
    c = lambda v: fmt.coins(v, L)
    parts = [t(L, "an.head", brand=t(L, "brand")),
             t(L, "an.players", total=n(pl["total"]), new_today=n(pl["new_today"]), new_7d=n(pl["new_7d"]), dau=n(pl["dau"]), dau_y=n(pl["dau_yesterday"]), wau=n(pl["wau"]), mau=n(pl["mau"]),
               stick=pc(pl["dau"] * 100.0 / pl["mau"] if pl["mau"] else None), banned=n(pl["banned"])),
             t(L, "an.retention", d1=pc(rt["d1"]), d1n=n(rt["d1_n"]), d7=pc(rt["d7"]), d7n=n(rt["d7_n"]))]
    if d["funnel"]:
        parts.append(t(L, "an.funnel", rows="\n".join(f"• {t(L, 'q.' + qid)}: {pc(p)}" for qid, p in d["funnel"])))
    parts += [t(L, "an.economy", supply=c(ec["supply"]), minted=c(ec["minted"]), burned=c(ec["burned"]), mt=c(ec["minted_today"]), bt=c(ec["burned_today"]), my=c(ec["minted_yesterday"]), by=c(ec["burned_yesterday"])),
              t(L, "an.market", stocks=n(mk["stocks"]), mcap=c(mk["mcap"]), trades=n(mk["trades"]), bot=n(mk["bot_trades"]), volume=c(mk["volume"]), launches=n(mk["launches"]), whales=n(mk["whales"]),
                rugs=n(mk["rugs"]), trades_y=n(mk["trades_yesterday"]), volume_y=c(mk["volume_yesterday"])),
              t(L, "an.exchange", holders=n(ex["holders"]), mcap=c(ex["mcap"]), trades=n(ex["trades_today"]), volume=c(ex["volume_today"]), levy=c(ex["levy_today"]), div=c(ex["div_today"]),
                minted=c(ex["minted_today"]), burned=c(ex["burned_today"]), pnl=fmt.pnl(ex["pnl"], L), levy_total=c(ex["levy_total"])),
              t(L, "an.engagement", quests=n(en["quests"]), ach=n(en["achievements"]), votes=n(en["votes"]), reports=n(en["reports"]), qy=n(en["quests_yesterday"]), ay=n(en["achievements_yesterday"])),
              t(L, "an.moderation", open=n(mo["open_reports"]), flagged=n(mo["flagged"]), actions=n(mo["actions_7d"])),
              t(L, "an.groups", active=n(gr["active"]), chatting=n(gr["chatting_today"]), top=("\n" + "\n".join(f"   {i}. {esc(g['title'])} — {c(g['score'])}" for i, g in enumerate(gr["top"], 1))) if gr["top"] else "")]
    return Reply(text="\n\n".join(parts))


# --- share cards ---------------------------------------------------------------------------------------------
def share_url(link: str, text: str) -> str:
    return "https://t.me/share/url?" + urllib.parse.urlencode({"url": link, "text": text}, quote_via=urllib.parse.quote)


def share_menu(db, lang: str, uid: int) -> Reply:
    rows = [[(t(lang, "share.btn_p"), f"scp:{uid}"), (t(lang, "share.btn_r"), f"scr:{uid}")]]
    mine = trading.my_stocks(db, uid)
    if mine:
        s = max(mine, key=lambda r: price(r) * r["supply"])
        rows.append([(t(lang, "share.btn_s", tk="$" + s["ticker"]), f"scs:{s['id']}")])
    return Reply(text=t(lang, "share.menu"), kb=kb(rows))


def share_reply(db, lang: str, kind: str, uid: int, botname: str, s=None, chat_id: int | None = None) -> Reply:
    """A picture card + caption + a Telegram 'send to friends' button. kind: p(ortfolio) / r(ank) / s(tock)."""
    L = lang
    ref = f"https://t.me/{botname}?start=ref_{uid}"
    u = core.need_user(db, uid)
    name = esc(display_name(u, False))
    bonus = fmt.coins(C.REF_BONUS_NEW, L)
    brand = t(L, "brand")
    if kind == "p":
        info = progress.level_info(db, uid)
        png, link = cards.portfolio_card(db, L, uid, ref), ref
        caption = t(L, "share.cap_p", name=name, brand=brand, nw=fmt.coins(info["nw"], L), bonus=bonus, link=link)
        text = t(L, "share.txt_p", brand=brand, nw=fmt.coins(info["nw"], L))
    elif kind == "r":
        info = progress.level_info(db, uid)
        rank = cards.global_rank(db, uid)
        png, link = cards.rank_card(db, L, uid, ref, chat_id), ref
        rtxt = f"#{fmt.num(rank, L)}" if rank else "—"
        caption = t(L, "share.cap_r", name=name, brand=brand, rank=rtxt, nw=fmt.coins(info["nw"], L), level=fmt.num(info["level"], L), bonus=bonus, link=link)
        text = t(L, "share.txt_r", brand=brand, rank=rtxt)
    else:
        link = f"https://t.me/{botname}?start=s_{s['ticker']}_r{uid}"
        png = cards.stock_card(db, L, s, link)
        chg = fmt.pct(core.change_pct(db, s), L)
        caption = t(L, "share.cap_s", tk=fmt.tk(s["ticker"]), name=esc(s["name"]), price=fmt.price(price(s), L), chg=chg, bonus=bonus, link=link)
        text = t(L, "share.txt_s", tk="$" + s["ticker"], brand=brand, price=fmt.price(price(s), L), chg=fmt.pct(core.change_pct(db, s), L, arrows=False))
    return Reply(photo=png, text=caption, kb=kb([[(t(L, "share.send"), share_url(link, text), "url")]]))


# --- daily salary DM ------------------------------------------------------------------------------------------
def salary_message(db, lang: str, uid: int) -> Reply:
    L = lang
    sm = salary.summary(db, uid)
    u = sm["user"]
    change = t(L, "salary.change", chg=fmt.pct(sm["change"], L)) if sm["change"] is not None else ""
    extra = ""
    if sm["pending"] > 0:
        extra += t(L, "salary.pending", x=fmt.coins(sm["pending"], L))
    if sm["best"]:
        extra += t(L, "salary.best", tk=fmt.tk(sm["best"][0]), chg=fmt.pct(sm["best"][1], L))
    pv = sm["preview"]
    pay = t(L, "salary.pay", amount=fmt.coins(pv["amount"], L), pct=fmt.num(pv["pct"], L)) if pv else t(L, "salary.pay_done")
    text = t(L, "salary.dm", name=esc(display_name(u, False)), nw=fmt.coins(sm["nw"], L), change=change, rank=f"#{fmt.num(sm['rank'], L)}" if sm["rank"] else "—",
             streak=fmt.num(sm["streak"], L), extra=extra, qdone=fmt.num(sm["quests_done"], L), qtotal=fmt.num(sm["quests_total"], L), pay=pay)
    rows = []
    if pv:
        rows.append([(t(L, "salary.btn_claim"), f"sal:claim:{uid}")])
    if sm["pending"] > 0:
        rows.append([(t(L, "salary.btn_income"), f"sal:inc:{uid}")])
    rows.append([(t(L, "salary.btn_quests"), f"qs:{uid}"), (t(L, "salary.btn_pf"), f"pf:{uid}")])
    rows.append([(t(L, "salary.btn_off"), f"sal:off:{uid}")])
    return Reply(text=text, kb=kb(rows))


def salary_status(db, lang: str, uid: int) -> Reply:
    on = bool(salary.get(db, uid)["enabled"])
    return Reply(text=t(lang, "salary.on" if on else "salary.off"), kb=kb([[(t(lang, "salary.turn_off" if on else "salary.turn_on"), f"sal:tog:{uid}")]]))
