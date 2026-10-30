"""The game's SECTIONS and the command namespace of each.

Bours-e Rofaqa is growing from a stock game into a bigger game, so the stock market is ONE section among several. Every
stock-only plain-text command lives behind the section word (سهام / stocks); everything that is not stock-only (help, menu,
profile, group settings, admin) stays global. A future section (a city, jobs, ...) is one more entry in SECTIONS plus its own
commands: it never has to fight the stock words for a name.

This file is the single source of truth for:
  * which commands belong to a section (STOCK_CMDS), which are global and which are for bot admins,
  * which old unprefixed stock words still work (LEGACY_KEEP) and which have been retired (everything else),
  * the words that open a section and the short in-section aliases («سهام من», «سهام راهنما»).
textparse.py reads it to parse messages; handlers.py to answer for retired words; docs/tests keep it honest.
"""
from __future__ import annotations

from dataclasses import dataclass, field

# --- command classes ------------------------------------------------------------------------------------------------
# Stock-only features: they get the canonical «سهام ...» form (the commands' own names and arguments are unchanged).
STOCK_CMDS = frozenset({
    "buy", "sell", "price", "chart", "market", "portfolio", "alert", "exchange", "earnings",
    "launch", "project", "meme", "mystock", "editstock", "setdividend", "cashout",
    "dividends", "income",
    "createcompany", "mycompany", "joincompany", "leavecompany", "payout",
    "group", "index", "grouprank", "leaderboard", "top", "share",
    "level", "reputation", "skills", "upgrade", "legacy", "rebirth", "quests", "achievements", "season",
    "news", "events", "whales", "trust", "distrust", "report",
})

# Global commands: not stock-only, never behind a section word.
GLOBAL_CMDS = frozenset({
    "start", "help", "menu", "profile", "language", "invite", "daily", "salary", "shop", "map",
    "cash", "fx", "jobs", "snapp", "mcd", "muni",
    "cities", "travel", "idcard", "giftcode",
    "settings", "setlang", "msglimit", "quiet",
})

# Slash commands of the bot's admins (never renamed).
ADMIN_CMDS = frozenset({"admin", "stats", "backup", "ban", "unban", "delist", "give", "fire"})

# Commands that only exist to open a section / its help (they are not "inside" a section, they ARE its doors).
SECTION_DOORS = frozenset({"stocks", "stockhelp", "stocks_unknown"})

# --- old unprefixed words -------------------------------------------------------------------------------------------
# Old stock words that STILL work without the section word: they are specific enough that no future global command can
# plausibly want them. Everything else in STOCK_CMDS was retired: typing it answers with a one-line pointer to the new form
# and executes nothing (so a word like سطح stays free for a future player-level command).
LEGACY_KEEP = frozenset({
    "market", "exchange", "earnings", "portfolio",            # بازار / بورس / گزارش مالی / پورتفوی
    "buy", "sell", "price", "chart",                           # only with a ticker, see LEGACY_NEEDS_TICKER
    "launch", "project", "meme",                               # only with a ticker, see LEGACY_NEEDS_TICKER
    "mystock", "editstock", "setdividend",                     # «mystock», «ویرایش سهام», «تنظیم سود»
})
# These old words are generic (خرید / فروش / قیمت ...), so they only keep working when the message really contains a ticker
# (خرید 10 SPEED). A bare «خرید» or «خرید خانه» is NOT consumed, so a future global command can own it. (The two-word phrases
# «عرضه میم» and «عرضه پروژه» are specific enough to keep working on their own.)
LEGACY_NEEDS_TICKER = frozenset({"buy", "sell", "price", "chart", "launch", "project", "meme"})


@dataclass(frozen=True)
class Section:
    id: str
    words: tuple                                   # words that open the section (already normalized by textparse.key)
    local: dict = field(default_factory=dict)      # in-section aliases: normalized word tuple -> command
    cmds: frozenset = frozenset()                  # commands that exist inside the section
    menu_cmd: str = ""                             # the command that shows the section's landing menu
    help_cmd: str = ""


SECTIONS: dict[str, Section] = {
    "stocks": Section(
        id="stocks",
        words=("سهام", "stocks", "stock"),
        local={
            ("من",): "mystock",                    # سهام من
            ("ویرایش",): "editstock",              # سهام ویرایش نام ...
            ("راهنما",): "stockhelp", ("کمک",): "stockhelp", ("help",): "stockhelp",
            ("منو",): "stocks", ("menu",): "stocks",
        },
        cmds=STOCK_CMDS | {"stockhelp", "stocks"},
        menu_cmd="stocks",
        help_cmd="stockhelp",
    ),
}


def section_of(word_key: str) -> Section | None:
    for s in SECTIONS.values():
        if word_key in s.words:
            return s
    return None


def legacy_applies(cmd: str, args: list, looks_like_ticker, words: int = 1) -> bool:
    """May this OLD, unprefixed spelling still run `cmd`? (False = retired: answer with a pointer, do nothing.)"""
    if cmd not in STOCK_CMDS:
        return True                                # global / admin / door commands are never retired
    if cmd not in LEGACY_KEEP:
        return False
    if cmd in LEGACY_NEEDS_TICKER and words < 2:          # «عرضه میم» / «عرضه پروژه» are two-word phrases: specific enough on their own
        return any(looks_like_ticker(a) for a in args[:3])
    return True


def canonical(cmd: str, lang: str) -> str:
    """The canonical spelling of a stock command, as a player types it: «سهام سطح» / «stocks level»."""
    from .texts import ALIASES
    words = ALIASES.get(cmd, [cmd])
    if lang == "fa":
        fa = [w for w in words if not w.isascii()]
        if fa:
            return fa[0] if fa[0].startswith("سهام") else "سهام " + fa[0]
    en = [w for w in words if w.isascii()]
    return "stocks " + (en[0] if en else words[0])


# --- the stock section's button menu ------------------------------------------------------------------------------------
# Every stock command is reachable from the menu by exactly one button, in exactly one category screen (tests enforce it).
STOCK_QUICK = (("market", "exchange"), ("portfolio", "profile"), ("mystock", "level"), ("skills", "quests"))   # the shortcut rows of the menu
STOCK_CATEGORIES = (
    ("trade", ("market", "exchange", "earnings", "price", "chart", "buy", "sell", "portfolio", "alert")),
    ("own", ("launch", "meme", "project", "mystock", "editstock", "setdividend", "cashout")),
    ("income", ("dividends", "income")),
    ("company", ("mycompany", "createcompany", "joincompany", "leavecompany", "payout")),
    ("social", ("group", "index", "grouprank", "leaderboard", "top", "share")),
    ("progress", ("level", "reputation", "skills", "upgrade", "legacy", "rebirth", "quests", "achievements", "season")),
    ("live", ("news", "events", "whales")),
    ("safety", ("trust", "distrust", "report")),
)
# What a button does:
#   ASK    commands that need typed input (a ticker, a name, a number): the button explains the format and, in a private chat,
#          takes the player's NEXT message as the arguments («guided input»); in a group it shows the command to type.
#   SURE   commands that move coins or leave something: the button asks "are you sure?" first.
#   screen everything else: the button shows the command's ordinary screen in place (SCREEN_OF maps a button to another screen).
ASK_CMDS = frozenset({"price", "chart", "buy", "sell", "alert", "launch", "meme", "project", "editstock", "setdividend",
                      "createcompany", "joincompany", "trust", "distrust", "report"})
SURE_CMDS = frozenset({"leavecompany", "payout"})
SCREEN_CMDS = STOCK_CMDS - ASK_CMDS - SURE_CMDS
SCREEN_OF = {"upgrade": "skills"}                      # the «upgrade» button opens the skills screen: the upgrade buttons live there
ASK_ALSO = {"trust": "trust", "alert": "alert"}        # the prompt of these also offers a button for the command's no-argument screen
MENU_SCREENS = frozenset(SCREEN_OF.get(c, c) for c in SCREEN_CMDS) | frozenset(ASK_ALSO.values()) | {"profile"}      # every screen a menu button may open
GUIDED_TTL = 300                                       # seconds a guided prompt waits for the player's answer
