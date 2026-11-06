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
CASINO_BLACKJACK_NUM, CASINO_BLACKJACK_DEN = 3, 2           # a natural blackjack, and (v0.20) ANY hand won with a final total of exactly 21, pays 3 to 2 (profit = bet * 3 / 2, i.e. 2.5x the chip comes back; every chip is a whole number of dollars)
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


INTERCITY_TRAVEL_MINUTES_DEFAULT = 10      # v0.20: a journey between two cities lasts this many REAL minutes (intercity.py)


def intercity_travel_secs() -> int:
    """How long a journey between two cities takes, in seconds: INTERCITY_TRAVEL_MINUTES in .env (real minutes, default 10, at least one second and at most one
    day). The ONE place that decides it. A journey saves its own duration when it starts, so changing the setting never moves the arrival of a journey
    that is already under way."""
    return max(1, int(round(_env_num("INTERCITY_TRAVEL_MINUTES", float(INTERCITY_TRAVEL_MINUTES_DEFAULT), 1.0 / 60.0, 24.0 * 60.0) * 60.0)))


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


# --- admin gift codes (v0.20) ----------------------------------------------------------------------------------------------------------
# What an admin may put into one gift code (the amount is per redeemer, in the unit the admin types it in) and how many different players may use it. These are sanity
# bounds against a typo, not game balance; gifts.py is the only place that reads them.
GIFT_MAX_STOCK_COINS = 1_000_000           # stock money, whole 🪙 per redeemer
GIFT_MAX_CASH_DOLLARS = 100_000            # life cash, whole dollars per redeemer
GIFT_MAX_XP = 100_000                      # character XP per redeemer
GIFT_MAX_USES = 10_000                     # how many different players may redeem one code
GIFT_MAX_ITEM_QTY = 99                     # items per redeemer of an item gift code (v0.21)


# --- the identity document (v0.20): nationality, languages, height, the card's validity and its issuing authority --------------------------------
# Every value here is a GAME-WORLD setting: nothing about a character's nationality, language or height is ever read from Telegram (the language of the app, the
# country of the phone, an IP address). identity.py is the only reader.
CARD_VALID_GAME_YEARS = 10                 # a card is valid for this many GAME years from its issue date (4 real days each, so 40 real days); env CARD_VALID_GAME_YEARS
CHAR_NATIONALITY_DEFAULT = "hype"          # the one nationality of the game world (a code; its names are the texts `nat.<code>`); env CHAR_NATIONALITY
CHAR_LANGUAGES_DEFAULT = "fa"              # the languages a new character knows (codes, comma separated; names are the texts `ln.<code>`); env CHAR_LANGUAGES
CARD_AUTHORITY_DEFAULT = "registry"        # the issuing authority (a code; its names are the texts `auth.<code>`); env CARD_AUTHORITY, or CARD_AUTHORITY_FA / CARD_AUTHORITY_EN for any name
# Height in centimetres: the saved height TRAIT (very_short .. very_tall, never changed) picks a 10 cm band per gender and a fixed offset inside it (identity.height_cm_for),
# so the number on the card always agrees with the trait the card and the appearance screen show.
HEIGHT_BANDS_CM = {
    "male":   {"very_short": 150, "short": 160, "average": 170, "tall": 180, "very_tall": 190},
    "female": {"very_short": 140, "short": 150, "average": 160, "tall": 170, "very_tall": 180},
}
HEIGHT_BAND_WIDTH_CM = 10


def card_valid_years() -> int:
    return int(_env_num("CARD_VALID_GAME_YEARS", float(CARD_VALID_GAME_YEARS), 1.0, 200.0))


def _env_code(name: str, default: str) -> str:
    v = os.environ.get(name, "").strip().lower()
    return v if v and len(v) <= 24 and all(ch.isascii() and (ch.isalnum() or ch == "_") for ch in v) else default


def char_nationality() -> str:
    return _env_code("CHAR_NATIONALITY", CHAR_NATIONALITY_DEFAULT)


def char_languages() -> tuple:
    """The default known languages of a new character: the codes of env CHAR_LANGUAGES (comma separated, at most 5, no repeats), else the default."""
    raw = os.environ.get("CHAR_LANGUAGES", "").strip().lower() or CHAR_LANGUAGES_DEFAULT
    out = []
    for part in raw.replace(" ", "").split(","):
        if part and len(part) <= 8 and part.isascii() and part.isalpha() and part not in out:
            out.append(part)
    return tuple(out[:5]) or (CHAR_LANGUAGES_DEFAULT,)


def card_authority() -> str:
    return _env_code("CARD_AUTHORITY", CARD_AUTHORITY_DEFAULT)


# --- the inventory (v0.21) ---------------------------------------------------------------------------------------------------------------
# Sanity bounds of the bag, not game balance. items.py / inventory.py / invui.py are the only readers.
INV_MAX_STACKS = 200                       # a player's bag holds at most this many stacks (a stack is one row: up to the item's max stack size, or one single non-stackable item)
INV_MAX_GIVE = 999                         # the most one grant may put in a bag in one go
INV_PAGE_SIZE = 6                          # items per page of a category or slot screen (one button each)


# =====================================================================================================================
#                                  v0.211: skills, brands, properties, textile, tailoring and stores
# =====================================================================================================================
# --- skills (skills.py): every skill goes 0 to 1000 and is earned by COMPLETED work only --------------------------------------------------
SKILL_MAX = 1000
SKILL_CURVE_POWER = 1.8                    # work needed for level L = SKILL_WORK[skill] * (L / 1000) ** 1.8: the first levels come quickly, the last ones take most of the time (diminishing returns)
SKILL_WORK = {                             # the work (units) a player has to complete to reach 1000, per skill
    "mcdonalds": 1000,                     # one unit = one completed 8-hour shift: 1000 shifts is the long-term benchmark of the spec
    "textile": 2000,                       # one unit = ten minutes of finished cotton work
    "tailoring": 2000,                     # one unit = ten minutes of finished tailoring
    "accounting": 3000,                    # one unit = one sale settled by that accountant
    "warehouse": 3000,                     # one unit = one stock run that moved something
    "production": 2000,                    # one unit = ten minutes of finished production work as an employee
}
SKILL_TITLES = ((0, "beginner"), (50, "trainee"), (150, "skilled"), (300, "experienced"), (500, "professional"), (750, "expert"), (900, "master"))   # (level that starts the title, title id)
WORK_UNIT_SECONDS = 600                    # the ten minutes of the textile / tailoring / production units

MCD_XP = 10                                # character XP for one completed shift (separate from the skill)
MCD_WAGE_BONUS_BPS_MAX = 2500              # at skill 1000 a shift pays up to 25% more (scaled linearly by the level at the START of the shift)
MCD_SHIFT_CUT_BPS_MAX = 2000               # ...and lasts up to 20% less
MCD_TITLE_BONUS = True                     # (the titles themselves are shown on the job screen)

# --- brands -----------------------------------------------------------------------------------------------------------------------------
BRAND_NAMES_DEFAULT = "NIKE,BALENCIAGA,GIVENCHY,LOUIS VUITTON"      # env BRAND_NAMES (comma separated) replaces it; the list is small on purpose and can grow without any code change
BRAND_FEE_CENTS = 1_000_00                 # registering a brand
BRAND_MAX_PER_PLAYER = 1                   # one player owns at most this many brands (0 = no limit), so nobody can hoard the short list


def _brand_key(name: str) -> str:
    return "_".join("".join(ch.lower() if ch.isalnum() else " " for ch in name).split())[:24]


def brand_names() -> dict:
    """{brand key: display name} of the brands that can be registered, in the configured order (invalid / repeated names are dropped)."""
    out: dict = {}
    for raw in (os.environ.get("BRAND_NAMES") or BRAND_NAMES_DEFAULT).split(","):
        name = " ".join(raw.split()).upper()
        key = _brand_key(name)
        if 3 <= len(key) <= 24 and key[0].isalpha() and key.isascii() and key not in out and len(name) <= 24:
            out[key] = name
    return out


# --- commercial property (estate.py) ----------------------------------------------------------------------------------------------------
# A property is a vacant lot of the saved city that can hold a business. Its attributes are WORKED OUT once from the lot's geometry and zone with the tables below, then SAVED in its row:
# a later change of these numbers never changes a property that exists. All prices are life cash, in cents.
PROPERTY_PRICE_MIN_CENTS = 250_00          # the cheapest a property can be
PROPERTY_PRICE_MAX_CENTS = 50_000_00       # the most expensive
PROPERTY_ROUND_CENTS = 5_00                # prices are whole multiples of this
PROPERTY_ZONE_RATE_CENTS = {"commercial": 14_00, "residential": 4_00, "industrial": 9_00}         # cents per square metre of the lot, by the zone of the land (a zone not listed has no property)
PROPERTY_ZONE_MODES = {"commercial": ("retail", "production", "both"), "residential": ("retail",), "industrial": ("production",)}   # the business modes a lot of that zone permits
PROPERTY_CENTRE_BPS = (10000, 15000)       # the price factor from the edge of the city (first) to its centre (second)
PROPERTY_FRONT_BPS = 11500                 # a lot that fronts a main road costs this much more (basis points)
PROPERTY_TIERS = (                         # (tier id, smallest area in m2 of the tier, warehouse capacity (items), display capacity (items), employee slots, equipment slots, highest product quality in stars)
    ("kiosk", 0, 60, 10, 1, 2, 1),
    ("small", 90, 150, 24, 2, 3, 2),
    ("medium", 180, 400, 60, 4, 5, 3),
    ("large", 400, 900, 120, 7, 7, 4),
    ("flagship", 800, 2000, 240, 10, 9, 4),   # (a commercial property never makes more than four stars: five is for gifted items only)
)
PROPERTY_ZONE_MULT_BPS = {"industrial": {"warehouse": 15000, "display": 0}, "residential": {"warehouse": 7000}}   # capacity multipliers by zone (basis points): a workshop has no shop floor, a corner shop a small stockroom
COMMERCIAL_MAX_QUALITY = 4                 # the highest star rating anything made by a business can have
BIZ_NEAR_UNITS = 60.0                      # map units (about 33 m): how close to a property's door a player must stand to inspect, buy or visit it
FITOUT_BPS = 5_00                          # fitting an empty shell for its business mode costs this share of the price (basis points) ...
FITOUT_MIN_CENTS = 25_00
MODE_CHANGE_BPS = 15_00                    # ... and converting it later to another mode costs this much (it is deliberately expensive)
MODE_CHANGE_MIN_CENTS = 75_00
BUSINESS_MODES = ("retail", "production", "both")
BRANCH_SIGN_DEFAULT = "plain"
BRAND_SEQ_CAP = 99                         # branch numbers per brand and city


# --- textile: the cotton job (textile.py) -----------------------------------------------------------------------------------------------
# A batch of cotton work: the player picks a GRADE (the fabric's quality, 1 to 5 stars). A better grade needs a higher textile skill and takes much longer. Every number is fixed when the batch is
# STARTED (from the skill at that moment) and saved in the task; the output is collected once the server clock says the batch is done.
COTTON_GRADES = {                          # grade -> (textile skill needed, base seconds of work, base units of fabric)
    1: (0, 30 * 60, 2),
    2: (100, 60 * 60, 3),
    3: (300, 120 * 60, 4),
    4: (550, 240 * 60, 6),
    5: (800, 480 * 60, 9),
}
COTTON_SPEED_BPS_MAX = 2000                # at skill 1000 a batch takes up to 20% less time (linear in the level at the start)
COTTON_YIELD_BPS_MAX = 3000                # ... and gives up to 30% more fabric (rounded down, never fewer than the base)
COTTON_XP = {1: 1, 2: 1, 3: 1, 4: 2, 5: 3} # character XP for one completed batch of that grade (separate from the skill)
FABRIC_GAME_CENTS = {1: 40, 2: 55, 3: 75, 4: 105, 5: 150}      # what the game pays for ONE unit of fabric of that quality (about what a job of the same hours pays)
FABRIC_STACK = 100

# --- purchase ads (market.py) ------------------------------------------------------------------------------------------------------------
AD_MAX_QTY = 500                           # the most units one ad can ask for
AD_MAX_UNIT_CENTS = 500_00                 # the highest price per unit an ad may offer
AD_TTL_CHOICES = (6 * 3600, 24 * 3600, 3 * 86400, 7 * 86400)    # the lifetimes an owner can choose (seconds)
AD_MAX_OPEN = 5                            # open ads per branch
AD_BOARD_PAGE = 6

# --- tailoring (tailor.py) -----------------------------------------------------------------------------------------------------------------
TAILOR_MAX_BATCH = 20                      # pieces in one order
TAILOR_SKILL_Q = ((0, 1), (150, 2), (400, 3), (700, 4), (900, 5))         # (tailoring skill from which this quality is possible, the quality): one of the factors of the final quality
TAILOR_SPEED_BPS_MAX = 2500                # skill 1000 makes a piece up to 25% faster ...
TAILOR_SAVE_BPS_MAX = 1000                 # ... and uses up to 10% less material (never fewer than one unit per piece)
TAILOR_XP_PER_HOUR = 1                     # character XP of a finished order per hour of its work (at least 1), only for a human worker
BOT_WORKER_SKILL = 300                     # a bot production worker works like a human of this skill: fixed, never grows
BOT_WORKER_WAGE_DAY_CENTS = {"accountant": 2_00, "production": 3_00, "warehouse": 2_00, "sales": 1_50}   # what a bot employee costs a day (the game takes it: it is a sink, not a payment to a player)

# --- stock, prices and the game's buyback (stock.py) -----------------------------------------------------------------------------------------------
DISPLAY_SLOT_ITEMS = 10                    # one display slot holds this many pieces of one product
PRICE_MAX_MULT = 20                        # an owner's price is at most this many times the game's own value of the piece
BUYBACK_BPS = 4500                         # the game buys surplus stock at 45% of its own value of the piece: always less than the same fabric sells for
STOCK_RUN_MAX_MOVES = 200

# --- customers and the accountant (store.py) --------------------------------------------------------------------------------------------------
QUOTE_TTL = 300                            # seconds a price quote (the confirm screen) stays valid
APPROVAL_TTL = 600                         # seconds a purchase waits for the owner / manager's approval when no accountant handles it
BOT_AUTO_LIMIT_CENTS = 100_00              # the biggest purchase a BOT accountant completes by itself
ACCOUNTANT_AUTO_BASE_CENTS = 100_00        # a player accountant: this plus ACCOUNTANT_AUTO_PER_LEVEL x skill level
ACCOUNTANT_AUTO_PER_LEVEL_CENTS = 50
ACCOUNTING_MIN_SALE_CENTS = 100            # a sale smaller than this never counts towards Accounting skill or reputation
ACCOUNTING_XP = 1

# --- staff (staff.py) -------------------------------------------------------------------------------------------------------------------
STAFF_PERCENT_MAX_BPS = 3000               # one contract may pay at most 30% of the sales ...
STAFF_PERCENT_TOTAL_BPS = 5000             # ... and all percent contracts of a branch together at most 50%
STAFF_FIXED_MAX_DAY_CENTS = 500_00
STAFF_OFFER_TTL = 3 * 86400
ROLES = ("manager", "accountant", "warehouse", "production", "sales")
BOT_ROLES = ("accountant", "warehouse", "production", "sales")

# --- reputation (reputation in biz.py's family: rep.py) ----------------------------------------------------------------------------------------
REP_SALE_PER_STAR = 1                      # a completed sale adds this per quality star of the piece (a piece without quality counts one star)
REP_FILL = 3                               # a fulfilled material ad
REP_WAGE_RUN = 1                           # a payroll that was paid in full
REP_PER_BUYER_DAILY = 6                    # at most this many points per branch from one customer a (server) day: nobody can farm a reputation with one account
REP_TITLES = ((0, "newcomer"), (40, "known"), (150, "trusted"), (500, "respected"), (1500, "renowned"))

# --- style (style.py) -------------------------------------------------------------------------------------------------------------------
STYLE_Q_POINTS = {0: 6, 1: 8, 2: 12, 3: 18, 4: 26, 5: 32}          # one worn piece: by its quality (0 = a piece without a quality)
STYLE_COMPLETE_BONUS = 12                  # upper + lower + footwear all worn
STYLE_ACCESSORY_BONUS = 4                  # per worn accessory or headwear (at most two count)
STYLE_TIERS = ((0, "plain"), (1, "simple"), (25, "average"), (55, "stylish"), (80, "iconic"))
STYLE_TIP_BPS_MAX = 1000                   # an iconic outfit raises a Snapp passenger's tipping chance by at most 10 points (nobody's chance goes DOWN for plain clothes)
OUTFIT_KINDS = ("streetwear", "casual", "formal", "work")
