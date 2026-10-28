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
# v0.18.1: the bonus is a CALENDAR-day thing. It can be claimed once per Tehran day and a new day starts at 00:00 Tehran time (UTC+03:30), whatever
# the hour of the last claim was; the streak goes on when the last claim was yesterday (see daily_day() below and progress.daily).

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

# --- exchange: house-priced listed companies --------------------------------------------
# The house is the only counterparty: nobody's trades move these prices, so alt-account pump & dump is impossible.
# Safety rule enforced by tests (tests/test_exchange.py attacks it with simulated farmers):
#     2 x EX_YCAP + EX_EARN_BETA x EX_ZCLIP  <  2 x LISTED_SPREAD_BPS x (1 - LISTED_HOLDER_SHARE_BPS)
# i.e. the most that anybody can predict or steer (a perfectly timed swing of the activity "heat" from its lowest to its
# highest point, plus the activity part of one earnings night) is smaller than the cheapest possible round trip, even for
# a lone holder who gets the maximum share of his own levies back. So no strategy beats the levy.
LISTED_SPREAD_BPS = 150             # fixed levy on every buy and sell (perks and fee-free hours don't touch it)
LISTED_SLIP_COEF_BPS = 100          # extra levy: this many bps per 1.0 of (recent market-wide flow / depth)...
LISTED_SLIP_MAX_BPS = 300           # ...but never more than this
LISTED_DEPTH = coins(20_000)        # how much flow the market absorbs before slippage reaches LISTED_SLIP_COEF_BPS
LISTED_FLOW_HALF_LIFE = 600         # seconds; recent flow fades by half every 10 minutes
LISTED_HOLDER_SHARE_BPS = 3000      # at most this share of every levy is paid to the other holders as a dividend; the rest is burned
EX_TICK = 60                        # seconds between two price steps
EX_NOISE = 0.0011                   # hidden random move per step (the bulk of every move; unpredictable by design)
EX_MAX_STEP = 0.006                 # no single step moves a price by more than 0.6%
EX_BETA = 0.00025                   # activity "heat" pushed into the price per unit of surprise (z) per step
EX_ZCLIP = 3.0                      # activity surprises are clipped to +-3 sigma
EX_Y_HALF_LIFE = 240                # steps; the activity heat fades away by half in 4 hours
EX_YCAP = 0.008                     # the heat never moves a price by more than +-0.8% (a swing from the lowest to the highest point is at most 1.6%)
EX_ALPHA_LEVEL = 0.01               # how fast the "normal activity" estimate adapts (per step)
EX_ALPHA_VAR = 0.01
EX_ALPHA_BIAS = 0.02                # learns the average surprise of each half hour of the day and cancels it
EX_FLOOR_FRAC = 0.25                # the surprise scale is never smaller than 25% of normal activity...
EX_FLOOR_ABS = 0.7                  # ...plus this many events per step (so small communities can't be steered by one account)
EX_WARMUP = 30                      # steps of learning before activity may touch the price
EX_EARN_SD = 0.025                  # weekly earnings-night surprise (random, zero-mean)
EX_EARN_BETA = 0.001                # tiny activity part of the earnings jump (at most 3 x this = 0.3%)
EX_EARN_MAX = 0.07
EX_EARN_WEEKDAY = 3                 # Thursday (Monday=0)
EX_EARN_HOUR = 17                   # UTC (20:30 Tehran)
EX_PRICE_FLOOR = 0.10               # a price never leaves [10%, 10x] of its listing price
EX_PRICE_CEIL = 10.0
EX_NEWS_1H = 0.018                  # a company headline when a price moves this much in an hour...
EX_NEWS_6H = 0.032                  # ...or this much in six hours (after the fact, never before)
EX_NEWS_COOLDOWN = 5 * 3600

# --- companies -------------------------------------------------------------
COMPANY_COST = coins(5000)
COMPANY_MIN_LEVEL = 3
COMPANY_MAX_MEMBERS = 10
COMPANY_TAX_BPS = 500               # 5% of members' claimed passive income feeds the company stock
COMPANY_SYNERGY_PER_MEMBER = 2      # +2% daily bonus per extra member (cap 20%)

# --- projects --------------------------------------------------------------
PROJECT_MIN_GOAL = coins(5000)
PROJECT_MAX_GOAL = coins(1_000_000)
DEFAULT_PROJECT_GOAL = coins(10_000)   # used when the player doesn't type a goal
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

# --- scam reports & trust ------------------------------------------------------------
REPORT_MIN_TRADES = 3               # only players with at least 3 trades can report or vote (blocks brand-new throw-away accounts)
REPORT_DAILY_LIMIT = 5
VOTE_DAILY_LIMIT = 20
REPORT_FLAG_WEIGHT = 3              # open report weight at which a stock shows a public 🚩 warning
REPORT_ALERT_LEVELS = (3, 6, 10)    # report weights at which the admins get a moderation message
TRUST_MIN_VOTERS = 3                # a trust label needs at least this many different voters
TRUST_GOOD = 3
TRUST_BAD = -3

# --- achievements --------------------------------------------------------------------
ACH_REWARD = {"bronze": coins(100), "silver": coins(300), "gold": coins(800), "legend": coins(2000)}

# --- daily salary DM -------------------------------------------------------------------
SALARY_WINDOW_HOURS = 6             # DMs only go out in the first 6 hours after SALARY_HOUR_UTC ("good morning" stays true)
SALARY_BATCH = 150                  # players per 5-minute round (about 20 messages per second)
SALARY_SEEN_DAYS = 14               # only players seen in the last 14 days
SALARY_MAX_IGNORED = 3              # stop after 3 unclaimed salaries in a row

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
    backup_keep: int = 30
    bots: bool = True          # automated market traders
    backup_every_min: int = 1440  # how often the database is sent to the admins (default: every 24 hours)
    report_hour: int = 5         # UTC hour of the daily analytics report to the admins
    salary: bool = True          # the daily salary DM
    salary_hour: int = 5         # UTC hour from which the salary DMs go out (05:00 UTC = 08:30 Tehran)


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


def _backup_minutes() -> int:
    """BACKUP_EVERY_HOURS (default 24, decimals allowed, never below 5 minutes). The old BACKUP_EVERY_MIN is no longer read."""
    try:
        hours = float(os.environ.get("BACKUP_EVERY_HOURS", "24") or 24)
    except ValueError:
        hours = 24.0
    return max(5, int(round(hours * 60)))


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
        backup_keep=max(1, int(os.environ.get("BACKUP_KEEP", "30") or 30)),
        backup_every_min=_backup_minutes(),
        report_hour=int(os.environ.get("REPORT_HOUR_UTC", "5") or 5) % 24,
        salary=_flag("SALARY_DM", "1"),
        salary_hour=int(os.environ.get("SALARY_HOUR_UTC", "5") or 5) % 24,
        bots=_flag("MARKET_BOTS", "1"),
    )


# --- the city map: walking, nearby players, button sessions (v0.17) -----------------------------------------------------------
# Read when used (not at import) so a value in .env counts and tests can change them. Every one is clamped to a sane range.
def _env_num(name: str, default: float, lo: float, hi: float) -> float:
    try:
        v = float(os.environ.get(name, "") or default)
    except ValueError:
        v = default
    return min(hi, max(lo, v))


# THE GAME-WORLD SCALE (v0.18.2). The map is measured in "map units"; players are told distances in metres / kilometres and speeds in km/h. ONE conversion ties
# them together, and nothing else converts: UNITS_PER_KM map units are one kilometre of the game world. The speeds players are told are the game-world values
# WALK_KMH_DEFAULT = 20 km/h and CAR_KMH_DEFAULT = 40 km/h (the rental Pride); at this scale they are exactly 10 and 20 map units a second, which is what trips were
# always planned with, so saved trips, durations and ETAs are unchanged. Travel time = road length / speed in map units and seconds, as before; the km/h, metres
# and minutes on screen are derived from the same numbers (fmt.kmh, fmt.dist), so a screen can never say one thing while the movement does another.
UNITS_PER_KM = 1800.0
WALK_KMH_DEFAULT = 20.0
CAR_KMH_DEFAULT = 40.0


def kmh_to_units(kmh: float) -> float:
    """A game-world speed in km/h as map units per second."""
    return kmh * UNITS_PER_KM / 3600.0


def to_kmh(units_per_second: float) -> float:
    """A speed in map units per second as game-world km/h."""
    return units_per_second * 3600.0 / UNITS_PER_KM


def to_metres(units: float) -> float:
    """A distance in map units as game-world metres."""
    return units * 1000.0 / UNITS_PER_KM


def to_square_metres(units2: float) -> float:
    """An area in square map units as game-world square metres."""
    return units2 * (1000.0 / UNITS_PER_KM) ** 2


def walk_speed() -> float:
    """Map units a player walks per second: 10 = 20 km/h (WALK_SPEED in .env overrides it; the screens then show the matching km/h). The 1360-unit-wide city is crossed in about two minutes."""
    return _env_num("WALK_SPEED", kmh_to_units(WALK_KMH_DEFAULT), 1.0, 100.0)


def nearby_radius() -> float:
    """Road distance (map units) within which another player shows on your map (MAP_NEARBY_RADIUS, default 300)."""
    return _env_num("MAP_NEARBY_RADIUS", 300.0, 20.0, 2000.0)


def map_session_ttl() -> int:
    """How long the buttons under a map message keep working, in seconds (MAP_SESSION_HOURS, default 24)."""
    return int(_env_num("MAP_SESSION_HOURS", 24.0, 0.05, 24.0 * 30) * 3600)


MAP_MAX_OTHERS = 8                   # most nearby players drawn / listed at once
MAP_SESSIONS_PER_PLAYER = 6          # a player's newest map messages in one city that keep working
MAP_PAGE = 6                         # entries per page of the place / parcel browsers
MAP_MAX_LOTS = 60                    # the parcel browser lists the nearest this many empty parcels


# --- life cash, the currency exchange and the side jobs (v0.18) ------------------------------------------------------------------
# Life cash is a SECOND currency, kept apart from the stock-game coins (`users.cash`, in 1/100-coin units). It is stored as an integer
# number of CENTS (never a float) in `life_accounts`, and every change goes through `cash.apply`, which writes one row of `life_ledger`.
CASH_START_CENTS = 100_00                  # every player gets $100.00 once (ledger row ('grant','start') is unique per player)
CASH_CAP_CENTS = 10 ** 15                  # no balance may grow past $10 trillion: a hard guard against overflow
# --- the casino (v0.18.1): blackjack played for life cash ----------------------------------------------------------------------------
CASINO_BETS_CENTS = (1_00, 5_00, 10_00, 25_00, 100_00)      # the chips of the table: $1, $5, $10, $25, $100 (a bet is always one chip)
CASINO_BLACKJACK_NUM, CASINO_BLACKJACK_DEN = 3, 2           # a natural blackjack pays 3 to 2 (the profit is bet * 3 / 2; every chip is a whole number of dollars)
CASINO_IDLE_SECS = 1800                                     # a hand nobody has touched for 30 minutes is stood (played out) by the bot and paid as usual


# --- the character (v0.19) -----------------------------------------------------------------------------------------------------------
# One permanent character per Telegram user: a citizen ID (zero-based, sequential, never reused), a name, a gender, an age that follows the
# game calendar, a birthplace and one authoritative current city. Everything the game balances about characters is here; the rules are in
# character.py / gamecal.py / traits.py / charxp.py.
CHAR_NAME_MIN = 2                          # letters, per name (first name and last name each)
CHAR_NAME_MAX = 20                         # ...and at most this many characters (spaces, hyphens and half-spaces count)
CHARACTER_START_AGE = 15                   # every finished character begins at this age
GAME_YEAR_DAYS = 4                         # one game year = this many REAL days (the accelerated game calendar, gamecal.py)
CHAR_XP_LEVEL_STEP = 100                   # character XP: level n starts at CHAR_XP_LEVEL_STEP * (n-1)^2 XP (charxp.py); stock XP is a different thing


FX_UNITS_PER_CENT = 200                    # THE RATE (v0.18.1): 1,000 🪙 (= 100,000 units) = $5.00 (= 500 cents)  ->  200 units per cent, 2 🪙 per cent
                                           # (v0.18 had 50 units per cent: 1,000 🪙 = $20.00). Only this number sets the rate; texts and limits are derived from it.
FX_QUOTE_TTL = 120                         # seconds a confirmation screen stays valid
FX_MAX_COINS = 10 ** 12                    # most coins one exchange may move (so typed junk like 99999999999999999999 is refused)

# Side jobs. Every number that balances them is HERE and nowhere else (cents unless the name says otherwise).
# CALIBRATION (see README §16 and tests/test_life_jobs.py::test_job_earnings_stay_modest_and_ordered): the stock game pays about 1,000 🪙 a day
# of active play = $5 a day at the fixed exchange rate (v0.18 had $20). These values were fitted on the real generated cities (12 seeds) so that, per hour of
# ACTIVE play, Snapp pays about $10, municipal work about $7 and McDonald's about $1.50 (passive: you only start and collect), i.e. an hour of side
# work is worth roughly half a day of stock income, Snapp never exceeds ~1.6x municipal work, and nothing is "instant wealth".
CAR_SPEED_DEFAULT = kmh_to_units(CAR_KMH_DEFAULT)        # map units per second in the rental Pride = 40 km/h = 20 units/s (walking is 20 km/h); env CAR_SPEED
SNAPP_FEE_CENTS = 20                       # rental fee of the car, charged ONCE per completed ride, out of the payout (nothing is charged for an abandoned ride)
SNAPP_BASE_FARE_CENTS = 22                 # fare = base + per-100-units of ROAD distance of the ride (pickup to destination); base MUST exceed the fee
SNAPP_PER_100_CENTS = 3                    # ...3 cents per 100 map units of road = 54 cents per kilometre (snapp_per_km_cents(); the fare is computed in map units and rounds down to a cent)
SNAPP_TIP_MAX_CENTS = 10                   # a tip is 0..this, decided by the server from the saved ride (never from anything the player sends)
SNAPP_TIP_ODDS = (3, 5)                    # a tip happens in 3 of 5 rides on average (about 3 cents a ride: roughly 15% of the income)
SNAPP_PICKUP_RANGE = (120.0, 480.0)        # road distance from the player to the passenger
SNAPP_RIDE_RANGE = (240.0, 900.0)          # road distance of the ride itself
SNAPP_COOLDOWN = 30                        # seconds between the end of one ride and the next request
MCD_SHIFT_SECONDS = 8 * 3600               # a shift is 8 REAL hours
MCD_WAGE_CENTS = 12_00                     # paid once, when the shift has run its 8 hours and the player collects it
MUNI_TASKS = {                             # task kind -> reward in cents (fixed per kind, shown before the task is accepted)
    "sweep": 10,
    "waste": 12,
    "green": 11,
}
MUNI_RANGE = (100.0, 520.0)                # road distance from the player to the task site
MUNI_COOLDOWN = 20                         # seconds between finishing a task and being offered the next

# Player interactions
GREET_COOLDOWN = 20                        # seconds between two greetings of one player
HUG_COOLDOWN = 60                          # seconds between two hug requests of one player
HUG_TTL = 300                              # seconds a hug request waits for an answer
HUG_NEED_NEAR = True                       # a hug needs both players near each other (road distance <= nearby_radius()); env HUG_NEED_NEAR=0 turns it off


def car_speed() -> float:
    """Map units per second of the rental car (CAR_SPEED, default 20, range 1-200)."""
    return _env_num("CAR_SPEED", CAR_SPEED_DEFAULT, 1.0, 200.0)


def snapp_per_km_cents() -> int:
    """The per-distance part of a Snapp fare as cents per game-world kilometre (what the terms screen shows): SNAPP_PER_100_CENTS per 100 map units."""
    return int(round(SNAPP_PER_100_CENTS * UNITS_PER_KM / 100.0))


def display_utc_offset() -> int:
    """Minutes added to UTC when a shift's start and end are shown as a clock time (DISPLAY_UTC_OFFSET_MIN, default 210 = Tehran)."""
    return int(_env_num("DISPLAY_UTC_OFFSET_MIN", 210.0, -720.0, 840.0))


def hug_need_near() -> bool:
    return os.environ.get("HUG_NEED_NEAR", "1").strip().lower() not in ("0", "false", "no", "off")


def fx_coin_step() -> int:
    """The smallest number of WHOLE coins that is an exact number of cents (2 at the rate 1,000 🪙 = $5; 1 at the old rate). Stock money -> cash only
    moves multiples of it, so no exchange ever needs rounding."""
    import math
    return math.lcm(UNIT, FX_UNITS_PER_CENT) // UNIT


def fx_cents_for_coins(coins: int) -> int:
    """Exact cents for `coins` whole coins (a multiple of fx_coin_step())."""
    return coins * UNIT // FX_UNITS_PER_CENT


def fx_coins_for_cents(cents: int) -> int:
    """Whole-coin value of `cents` (exact when it is a multiple of the step; otherwise rounded DOWN, for display of limits only)."""
    return cents * FX_UNITS_PER_CENT // UNIT


def daily_utc_offset() -> int:
    """Minutes added to UTC to find the day of the daily bonus (DAILY_UTC_OFFSET_MIN, default 210 = Tehran, UTC+03:30; Iran has no daylight saving)."""
    return int(_env_num("DAILY_UTC_OFFSET_MIN", 210.0, -720.0, 840.0))


def daily_day(ts: int) -> int:
    """The number of the Tehran calendar day a timestamp falls in (a new number starts at 00:00 Tehran time)."""
    return (int(ts) + daily_utc_offset() * 60) // 86400


def daily_next_reset(ts: int) -> int:
    """The timestamp (UTC seconds) of the next 00:00 Tehran time after `ts`."""
    return (daily_day(ts) + 1) * 86400 - daily_utc_offset() * 60


def digits_mode() -> str:
    """'en' (default since v0.18.1): every number is shown with English digits 0-9 and the signs , . %; 'fa': the old Persian digits ۰-۹ and ٬ ٫ ٪.
    One switch (env DIGITS=fa) brings the old look back; the Persian digits still exist in the code and the texts."""
    return "fa" if os.environ.get("DIGITS", "en").strip().lower() in ("fa", "persian", "farsi", "old", "legacy") else "en"
