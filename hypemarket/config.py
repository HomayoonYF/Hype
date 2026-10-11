"""Settings and every tunable number of the game economy.

All money is stored as an integer number of *units* (100 units = 1 coin) so the
engine never touches floats and balances can never drift.
"""
from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

UNIT = 100        # 100 units = 1 coin
BPS = 10_000      # basis points (10_000 = 100%)
ACC = 10 ** 12    # scale of the "dividend per share" accumulator


def coins(x: float) -> int:
    return int(round(x * UNIT))


# --- onboarding / growth ---------------------------------------------------
START_CASH = coins(1000)
REF_BONUS_NEW = coins(500)          # invited player's extra start cash
REF_BONUS_INVITER = coins(1000)     # paid to the inviter once the invitee made ACTIVATION_TRADES trades
ACTIVATION_TRADES = 3
GROUP_ADD_BONUS = coins(1000)       # paid once per group to the person who adds the bot
GROUP_ADD_MIN_MEMBERS = 5

# --- daily bonus -----------------------------------------------------------
DAILY_BASE = coins(100)
DAILY_STEP = coins(10)
DAILY_STREAK_CAP = 10
DAILY_COOLDOWN = 20 * 3600
DAILY_RESET = 48 * 3600

# --- trading ---------------------------------------------------------------
TRADE_FEE_BPS = 200                 # 2% on every buy and every sell
OWNER_SHARE_BPS = 5000              # half of the fee goes to the stock's owner, the rest is burned
TRADE_COOLDOWN = 2                  # seconds between two trades of one player
SELL_LOCK = 30                      # seconds before freshly bought shares can be sold
FOUNDER_SHARES = 10                 # shares the founder buys (through the curve) at launch
MAX_SHARES_PER_TRADE = 10_000_000
REP_START = 100
REP_MAX = 300
REP_DAILY_GAIN_CAP = 10             # reputation a player can earn per day from new investors

# --- passive income --------------------------------------------------------
HOLDER_BONUS_BPS_PER_HOUR = 1       # 0.01% of position value per hour
BONUS_MAX_HOURS = 24
ACT_UNITS = 5                       # minted units per counted chat message (0.05 coin)
ACT_MIN_GAP = 15                    # seconds between two counted messages of one player
DAILY_ACTIVITY_CAP = 300            # counted messages per player per day (all groups)
DEFAULT_GROUP_MSG_CAP = 200

# --- rug pulls -------------------------------------------------------------
RUG_MIN_AGE = 6 * 3600
RUG_REP_PENALTY = 40
RUG_TAX_BPS = 1000
RUG_COOLDOWN = 24 * 3600

# --- shorting --------------------------------------------------------------
SHORT_MARGIN_BPS = 5000
SHORT_OPEN_FEE_BPS = 100
SHORT_BORROW_BPS_HOUR = 5
SHORT_MIN_AGE = 6 * 3600
SHORT_MIN_SUPPLY = 50
SHORT_MIN_HOLDERS = 5
SHORT_MAX_OPEN = 3
SHORT_LIQUIDATION_BPS = 9000        # liquidated when 90% of the margin is lost

# --- companies -------------------------------------------------------------
COMPANY_COST = coins(5000)
COMPANY_MIN_LEVEL = 3
COMPANY_MAX_MEMBERS = 10
COMPANY_TAX_BPS = 500               # 5% of members' claimed passive income feeds the company stock
COMPANY_SYNERGY_PER_MEMBER = 2      # +2% daily bonus per extra member (cap 20%)

# --- projects --------------------------------------------------------------
PROJECT_MIN_GOAL = coins(5000)
PROJECT_MAX_GOAL = coins(1_000_000)
PROJECT_REWARD_BPS = 500            # minted to the owner when the goal is reached
PROJECT_DIVIDEND_BPS = 200          # minted as a special dividend to the investors

# --- progression -----------------------------------------------------------
LEVEL_BASE = coins(2000)            # level n needs LEVEL_BASE * 3^(n-1) net worth (2k, 6k, 18k, 54k, 162k, ...)
REBIRTH_BASE = coins(100_000)       # doubles with every rebirth
REBIRTH_POINTS = 2                  # skill points granted per rebirth
SEASON_DAYS = 30
SEASON_REWARDS = [(coins(10_000), 5), (coins(5_000), 3), (coins(2_000), 2)]  # (coins, legacy points) for top 3

# --- whales ----------------------------------------------------------------
WHALE_MIN = coins(2000)             # a trade must be at least this big...
WHALE_PCT_BPS = 1000                # ...and at least 10% of the stock's reserve
WHALE_COOLDOWN = 1800               # one whale headline per stock per 30 minutes
WHALE_DM_MAX = 100                  # holders notified by DM per whale

# --- automated market traders (clearly labelled 🤖 accounts) -------------------------
BOT_CASH_TARGET = coins(3000)       # each trader is topped up to this much cash...
BOT_DAILY_TOPUP = coins(1500)       # ...but never by more than this per day
BOT_ACT_PROB = 0.35                 # chance a trader acts on each 30-second tick
BOT_MIN_AGE = 1800                  # only stocks older than 30 minutes
BOT_MIN_SUPPLY = 30
BOT_MIN_REAL_HOLDERS = 2            # and with at least two real (non-bot) holders
BOT_MAX_POSITION = coins(1500)      # value cap of one trader's position in one stock
BOT_MAX_SUPPLY_PCT = 20             # a trader never holds more than 20% of a stock

# --- misc ------------------------------------------------------------------
HISTORY_KEEP_DAYS = 7
ALERT_PERK_STEP = 3                 # alerts per level of the "alerts" skill
MAX_CALLBACK_AGE = 0


@dataclass(frozen=True)
class AssetType:
    key: str
    p0: int                 # price of the very first share (units)
    k: int                  # price increase per share (units)
    launch_cost: int        # burned at launch (units)
    min_level: int = 0
    legacy_perk: str | None = None


ASSET_TYPES = {
    "personal": AssetType("personal", coins(1), 1, coins(500)),
    "meme": AssetType("meme", coins(0.2), 2, coins(150), min_level=1),
    "project": AssetType("project", coins(5), 1, coins(1500), min_level=2),
    "bluechip": AssetType("bluechip", coins(20), 1, coins(5000), min_level=3, legacy_perk="types"),
}
OWNER_KINDS = ("personal", "meme", "project", "bluechip")


@dataclass(frozen=True)
class Perk:
    key: str
    kind: str          # "skill" (reset on rebirth) or "legacy" (permanent)
    max_level: int
    base_cost: int


PERKS = {p.key: p for p in [
    Perk("fee", "skill", 5, 1),        # -10% trading fees per level
    Perk("launch", "skill", 5, 1),     # -10% launch cost per level
    Perk("daily", "skill", 10, 1),     # +15% daily bonus per level
    Perk("bonus", "skill", 5, 1),      # +50% holder bonus per level
    Perk("alerts", "skill", 3, 1),     # unlocks price alerts, 3 alerts per level
    Perk("short", "skill", 1, 3),      # unlocks short selling
    Perk("head", "legacy", 10, 1),     # +20% start cash per level
    Perk("owner", "legacy", 5, 1),     # +10% owner share of trading fees per level
    Perk("boost", "legacy", 5, 1),     # +10% price boost for 1h on every launch per level
    Perk("income", "legacy", 10, 1),   # +5% on all claimed passive income per level
    Perk("types", "legacy", 1, 5),     # unlocks blue-chip assets
]}


# --- shop (Telegram Stars) ---------------------------------------------------
SHOP_ITEMS = {
    "badge_fire": {"stars": 25, "kind": "badge", "value": "🔥"},
    "badge_rocket": {"stars": 35, "kind": "badge", "value": "🚀"},
    "badge_crown": {"stars": 50, "kind": "badge", "value": "👑"},
    "badge_diamond": {"stars": 100, "kind": "badge", "value": "💎"},
    "pass": {"stars": 150, "kind": "pass"},
    "slot": {"stars": 100, "kind": "slot"},
    "spotlight": {"stars": 75, "kind": "spotlight"},
}


@dataclass(frozen=True)
class Settings:
    token: str
    db_path: str
    admins: frozenset
    lang: str
    shop: bool
    rug: bool
    events: bool
    log_level: str
    proxy: str = ""            # e.g. socks5://127.0.0.1:1080 or http://host:port (needed where Telegram is blocked)
    backup_dir: str = "backups"  # empty string disables automatic backups
    backup_keep: int = 14
    bots: bool = True          # automated market traders


def _load_dotenv(path: Path) -> None:
    if not path.exists():
        return
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        k, v = line.split("=", 1)
        os.environ.setdefault(k.strip(), v.strip().strip('"').strip("'"))


def _flag(name: str, default: str) -> bool:
    return os.environ.get(name, default).strip().lower() in ("1", "true", "yes", "on")


def load_settings() -> Settings:
    _load_dotenv(Path(".env"))
    admins = frozenset(int(x) for x in os.environ.get("ADMIN_IDS", "").replace(" ", "").split(",") if x.strip().lstrip("-").isdigit())
    return Settings(
        token=os.environ.get("BOT_TOKEN", "").strip(),
        db_path=os.environ.get("DB_PATH", "hypemarket.db"),
        admins=admins,
        lang=os.environ.get("DEFAULT_LANG", "fa") if os.environ.get("DEFAULT_LANG", "fa") in ("fa", "en") else "fa",
        shop=_flag("SHOP_ENABLED", "1"),
        rug=_flag("ALLOW_RUG", "1"),
        events=_flag("RANDOM_EVENTS", "1"),
        log_level=os.environ.get("LOG_LEVEL", "INFO").upper(),
        proxy=os.environ.get("PROXY_URL", "").strip(),
        backup_dir=os.environ.get("BACKUP_DIR", "backups").strip(),
        backup_keep=max(1, int(os.environ.get("BACKUP_KEEP", "14") or 14)),
        bots=_flag("MARKET_BOTS", "1"),
    )
