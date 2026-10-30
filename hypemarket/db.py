"""Tiny synchronous SQLite wrapper.

The whole game runs on one asyncio thread and every game action is a plain
synchronous function without `await` inside, so each action is atomic with
respect to every other one: no locks needed, no race conditions possible.
Each action is also wrapped in a SQL transaction (`with db.tx():`), so an
exception rolls everything back.
"""
from __future__ import annotations

import sqlite3
from contextlib import contextmanager

SCHEMA = """
CREATE TABLE IF NOT EXISTS users(
  id INTEGER PRIMARY KEY, username TEXT, name TEXT NOT NULL DEFAULT '', lang TEXT NOT NULL DEFAULT 'fa',
  cash INTEGER NOT NULL DEFAULT 0, pending_div INTEGER NOT NULL DEFAULT 0, pending_inc INTEGER NOT NULL DEFAULT 0,
  created_at INTEGER NOT NULL, last_daily INTEGER NOT NULL DEFAULT 0, streak INTEGER NOT NULL DEFAULT 0,
  rep INTEGER NOT NULL DEFAULT 100, max_level INTEGER NOT NULL DEFAULT 0, spent_points INTEGER NOT NULL DEFAULT 0,
  rebirths INTEGER NOT NULL DEFAULT 0, legacy_pts INTEGER NOT NULL DEFAULT 0, legacy_total INTEGER NOT NULL DEFAULT 0,
  season_base INTEGER NOT NULL DEFAULT 0, referrer INTEGER, activated INTEGER NOT NULL DEFAULT 0,
  trades INTEGER NOT NULL DEFAULT 0, company_id INTEGER, company_joined INTEGER NOT NULL DEFAULT 0,
  last_trade INTEGER NOT NULL DEFAULT 0, badge TEXT NOT NULL DEFAULT '', pass_season INTEGER NOT NULL DEFAULT 0,
  extra_slots INTEGER NOT NULL DEFAULT 0, nw INTEGER NOT NULL DEFAULT 0, banned INTEGER NOT NULL DEFAULT 0,
  whale_dm INTEGER NOT NULL DEFAULT 1, bot INTEGER NOT NULL DEFAULT 0, bonus_points INTEGER NOT NULL DEFAULT 0
);
CREATE TABLE IF NOT EXISTS perks(user_id INTEGER NOT NULL, perk TEXT NOT NULL, level INTEGER NOT NULL, PRIMARY KEY(user_id, perk));
CREATE TABLE IF NOT EXISTS stocks(
  id INTEGER PRIMARY KEY AUTOINCREMENT, ticker TEXT UNIQUE NOT NULL, name TEXT NOT NULL, descr TEXT NOT NULL DEFAULT '',
  kind TEXT NOT NULL, owner_id INTEGER, company_id INTEGER, group_id INTEGER,
  p0 INTEGER NOT NULL, k INTEGER NOT NULL, supply INTEGER NOT NULL DEFAULT 0, reserve INTEGER NOT NULL DEFAULT 0,
  base_bps INTEGER NOT NULL DEFAULT 10000, event_bps INTEGER NOT NULL DEFAULT 10000,
  event_until INTEGER NOT NULL DEFAULT 0, event_kind TEXT NOT NULL DEFAULT '',
  acc_div INTEGER NOT NULL DEFAULT 0, div_pct INTEGER NOT NULL DEFAULT 0,
  fees_earned INTEGER NOT NULL DEFAULT 0, volume INTEGER NOT NULL DEFAULT 0, hype REAL NOT NULL DEFAULT 0,
  holders INTEGER NOT NULL DEFAULT 0, created_at INTEGER NOT NULL, status TEXT NOT NULL DEFAULT 'active',
  rugs INTEGER NOT NULL DEFAULT 0, last_rug INTEGER NOT NULL DEFAULT 0,
  goal INTEGER NOT NULL DEFAULT 0, funded INTEGER NOT NULL DEFAULT 0, logo TEXT, featured_until INTEGER NOT NULL DEFAULT 0
);
CREATE INDEX IF NOT EXISTS ix_stocks_owner ON stocks(owner_id);
CREATE INDEX IF NOT EXISTS ix_stocks_status ON stocks(status, kind);
CREATE TABLE IF NOT EXISTS holdings(
  user_id INTEGER NOT NULL, stock_id INTEGER NOT NULL, shares INTEGER NOT NULL, cost INTEGER NOT NULL DEFAULT 0,
  debt INTEGER NOT NULL DEFAULT 0, last_buy INTEGER NOT NULL DEFAULT 0, last_bonus INTEGER NOT NULL DEFAULT 0,
  PRIMARY KEY(user_id, stock_id)
);
CREATE INDEX IF NOT EXISTS ix_holdings_stock ON holdings(stock_id);
CREATE TABLE IF NOT EXISTS history(stock_id INTEGER NOT NULL, ts INTEGER NOT NULL, price INTEGER NOT NULL, supply INTEGER NOT NULL);
CREATE INDEX IF NOT EXISTS ix_history ON history(stock_id, ts);
CREATE TABLE IF NOT EXISTS news(id INTEGER PRIMARY KEY AUTOINCREMENT, ts INTEGER NOT NULL, kind TEXT NOT NULL, stock_id INTEGER, params TEXT NOT NULL DEFAULT '{}');
CREATE TABLE IF NOT EXISTS groups(
  id INTEGER PRIMARY KEY, title TEXT NOT NULL DEFAULT '', lang TEXT NOT NULL DEFAULT 'fa', news_mode TEXT NOT NULL DEFAULT 'quiet',
  board_msg INTEGER, board_hash TEXT NOT NULL DEFAULT '', board_fail INTEGER NOT NULL DEFAULT 0, last_news_id INTEGER NOT NULL DEFAULT 0,
  last_post INTEGER NOT NULL DEFAULT 0, msg_cap INTEGER NOT NULL DEFAULT 200, index_stock INTEGER, added_by INTEGER,
  bonus_paid INTEGER NOT NULL DEFAULT 0, score INTEGER NOT NULL DEFAULT 0, active INTEGER NOT NULL DEFAULT 1, created_at INTEGER NOT NULL
);
CREATE TABLE IF NOT EXISTS group_members(group_id INTEGER NOT NULL, user_id INTEGER NOT NULL, last_seen INTEGER NOT NULL, PRIMARY KEY(group_id, user_id));
CREATE INDEX IF NOT EXISTS ix_gm_user ON group_members(user_id);
CREATE TABLE IF NOT EXISTS activity(user_id INTEGER NOT NULL, chat_id INTEGER NOT NULL, day INTEGER NOT NULL, count INTEGER NOT NULL, PRIMARY KEY(user_id, chat_id, day));
CREATE TABLE IF NOT EXISTS alerts(id INTEGER PRIMARY KEY AUTOINCREMENT, user_id INTEGER NOT NULL, stock_id INTEGER NOT NULL, price INTEGER NOT NULL, direction TEXT NOT NULL, created INTEGER NOT NULL);
CREATE TABLE IF NOT EXISTS companies(
  id INTEGER PRIMARY KEY AUTOINCREMENT, name TEXT UNIQUE NOT NULL, owner_id INTEGER NOT NULL, stock_id INTEGER,
  treasury INTEGER NOT NULL DEFAULT 0, created_at INTEGER NOT NULL
);
CREATE TABLE IF NOT EXISTS purchases(charge_id TEXT PRIMARY KEY, user_id INTEGER NOT NULL, item TEXT NOT NULL, stars INTEGER NOT NULL, ts INTEGER NOT NULL);
CREATE TABLE IF NOT EXISTS kv(key TEXT PRIMARY KEY, value TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS reports(
  id INTEGER PRIMARY KEY AUTOINCREMENT, stock_id INTEGER NOT NULL, owner_id INTEGER, reporter INTEGER NOT NULL,
  reason TEXT NOT NULL, note TEXT NOT NULL DEFAULT '', weight INTEGER NOT NULL DEFAULT 1, ts INTEGER NOT NULL,
  status TEXT NOT NULL DEFAULT 'open', UNIQUE(stock_id, reporter)
);
CREATE INDEX IF NOT EXISTS ix_reports_stock ON reports(stock_id, status);
CREATE TABLE IF NOT EXISTS trust(
  target INTEGER NOT NULL, voter INTEGER NOT NULL, value INTEGER NOT NULL, weight INTEGER NOT NULL DEFAULT 1, ts INTEGER NOT NULL,
  PRIMARY KEY(target, voter)
);
CREATE INDEX IF NOT EXISTS ix_trust_target ON trust(target);
CREATE TABLE IF NOT EXISTS achievements(user_id INTEGER NOT NULL, aid TEXT NOT NULL, ts INTEGER NOT NULL, PRIMARY KEY(user_id, aid));
CREATE TABLE IF NOT EXISTS seen(day INTEGER NOT NULL, user_id INTEGER NOT NULL, PRIMARY KEY(day, user_id));
CREATE TABLE IF NOT EXISTS modlog(id INTEGER PRIMARY KEY AUTOINCREMENT, ts INTEGER NOT NULL, admin INTEGER NOT NULL, action TEXT NOT NULL, target TEXT NOT NULL, note TEXT NOT NULL DEFAULT '');
CREATE TABLE IF NOT EXISTS salary(
  user_id INTEGER PRIMARY KEY, enabled INTEGER NOT NULL DEFAULT 1, last_sent_day INTEGER NOT NULL DEFAULT 0,
  ignored INTEGER NOT NULL DEFAULT 0, last_nw INTEGER NOT NULL DEFAULT 0, blocked INTEGER NOT NULL DEFAULT 0
);
CREATE TABLE IF NOT EXISTS exflow(stock_id INTEGER PRIMARY KEY, flow REAL NOT NULL DEFAULT 0, ts INTEGER NOT NULL DEFAULT 0);
CREATE TABLE IF NOT EXISTS quests(
  user_id INTEGER NOT NULL, qid TEXT NOT NULL, period INTEGER NOT NULL, progress INTEGER NOT NULL DEFAULT 0, target INTEGER NOT NULL,
  reward INTEGER NOT NULL, points INTEGER NOT NULL DEFAULT 0, done INTEGER NOT NULL DEFAULT 0, PRIMARY KEY(user_id, qid, period)
);
CREATE TABLE IF NOT EXISTS worlds(
  id INTEGER PRIMARY KEY AUTOINCREMENT, chat_id INTEGER NOT NULL UNIQUE, seed INTEGER NOT NULL, gen_version INTEGER NOT NULL,
  name_fa TEXT NOT NULL, name_en TEXT NOT NULL, cols INTEGER NOT NULL, rows INTEGER NOT NULL, layout TEXT NOT NULL, created_at INTEGER NOT NULL
);
CREATE TABLE IF NOT EXISTS world_places(
  world_id INTEGER NOT NULL, place_id TEXT NOT NULL, kind TEXT NOT NULL, parcel_id TEXT NOT NULL, created_at INTEGER NOT NULL,
  PRIMARY KEY(world_id, place_id), UNIQUE(world_id, kind), UNIQUE(world_id, parcel_id)
);
CREATE TABLE IF NOT EXISTS world_players(
  world_id INTEGER NOT NULL, user_id INTEGER NOT NULL, x REAL NOT NULL, y REAL NOT NULL, at_place TEXT,
  spawn_x REAL NOT NULL, spawn_y REAL NOT NULL, created_at INTEGER NOT NULL, updated_at INTEGER NOT NULL,
  PRIMARY KEY(world_id, user_id)
);
CREATE TABLE IF NOT EXISTS world_trips(
  id INTEGER PRIMARY KEY AUTOINCREMENT, world_id INTEGER NOT NULL, user_id INTEGER NOT NULL, dest_id TEXT NOT NULL, dest_kind TEXT NOT NULL,
  route TEXT NOT NULL, length REAL NOT NULL, speed REAL NOT NULL, started_at INTEGER NOT NULL, arrive_at INTEGER NOT NULL,
  state TEXT NOT NULL DEFAULT 'walking' CHECK(state IN ('walking','arrived','cancelled')), mode TEXT NOT NULL DEFAULT 'walk',
  CHECK(arrive_at > started_at)
);
CREATE UNIQUE INDEX IF NOT EXISTS ux_trip_active ON world_trips(world_id, user_id) WHERE state='walking';
CREATE INDEX IF NOT EXISTS ix_trips_world ON world_trips(world_id, state);
CREATE TABLE IF NOT EXISTS map_sessions(
  sid TEXT PRIMARY KEY, world_id INTEGER NOT NULL, chat_id INTEGER NOT NULL, user_id INTEGER NOT NULL, message_id INTEGER,
  view TEXT NOT NULL DEFAULT 'map', page INTEGER NOT NULL DEFAULT 0, sel TEXT, back TEXT NOT NULL DEFAULT 'map',
  created_at INTEGER NOT NULL, expires_at INTEGER NOT NULL
);
CREATE INDEX IF NOT EXISTS ix_msess_user ON map_sessions(world_id, user_id, created_at);
CREATE TABLE IF NOT EXISTS casino_games(
  sid TEXT PRIMARY KEY, world_id INTEGER NOT NULL, user_id INTEGER NOT NULL, deck TEXT NOT NULL, player TEXT NOT NULL, dealer TEXT NOT NULL,
  state TEXT NOT NULL CHECK(state IN ('playing','done')), result TEXT NOT NULL DEFAULT '', seq INTEGER NOT NULL DEFAULT 1,
  created_at INTEGER NOT NULL, updated_at INTEGER NOT NULL,
  bet INTEGER NOT NULL DEFAULT 0, hand_no INTEGER NOT NULL DEFAULT 1, paid INTEGER NOT NULL DEFAULT 0
);
CREATE TABLE IF NOT EXISTS life_accounts(
  user_id INTEGER PRIMARY KEY, balance INTEGER NOT NULL DEFAULT 0 CHECK(balance >= 0), created_at INTEGER NOT NULL, updated_at INTEGER NOT NULL DEFAULT 0
);
CREATE TABLE IF NOT EXISTS life_ledger(
  id INTEGER PRIMARY KEY AUTOINCREMENT, user_id INTEGER NOT NULL, kind TEXT NOT NULL, ref TEXT NOT NULL, delta INTEGER NOT NULL,
  balance_after INTEGER NOT NULL, stock_delta INTEGER NOT NULL DEFAULT 0, note TEXT NOT NULL DEFAULT '', created_at INTEGER NOT NULL,
  UNIQUE(user_id, kind, ref)
);
CREATE INDEX IF NOT EXISTS ix_ledger_user ON life_ledger(user_id, id);
CREATE TABLE IF NOT EXISTS fx_quotes(
  id TEXT PRIMARY KEY, user_id INTEGER NOT NULL, direction TEXT NOT NULL CHECK(direction IN ('s2c','c2s')),
  src INTEGER NOT NULL CHECK(src > 0), dst INTEGER NOT NULL CHECK(dst > 0),
  state TEXT NOT NULL DEFAULT 'open' CHECK(state IN ('open','done','cancelled')), created_at INTEGER NOT NULL, expires_at INTEGER NOT NULL
);
CREATE INDEX IF NOT EXISTS ix_fx_user ON fx_quotes(user_id, state);
CREATE TABLE IF NOT EXISTS work_tasks(
  id INTEGER PRIMARY KEY AUTOINCREMENT, user_id INTEGER NOT NULL, world_id INTEGER NOT NULL, job TEXT NOT NULL, kind TEXT NOT NULL DEFAULT '',
  stage TEXT NOT NULL, state TEXT NOT NULL DEFAULT 'open' CHECK(state IN ('open','done','cancelled')), data TEXT NOT NULL DEFAULT '{}',
  reward INTEGER NOT NULL DEFAULT 0, cost INTEGER NOT NULL DEFAULT 0, paid INTEGER NOT NULL DEFAULT 0, receipt TEXT NOT NULL DEFAULT '',
  created_at INTEGER NOT NULL, started_at INTEGER, due_at INTEGER, expires_at INTEGER, finished_at INTEGER
);
CREATE UNIQUE INDEX IF NOT EXISTS ux_work_open ON work_tasks(user_id) WHERE state='open';
CREATE INDEX IF NOT EXISTS ix_work_user ON work_tasks(user_id, job, finished_at);
CREATE TABLE IF NOT EXISTS interactions(
  id INTEGER PRIMARY KEY AUTOINCREMENT, kind TEXT NOT NULL CHECK(kind IN ('greet','hug')), chat_id INTEGER NOT NULL,
  from_id INTEGER NOT NULL, to_id INTEGER NOT NULL,
  state TEXT NOT NULL DEFAULT 'sent' CHECK(state IN ('sent','pending','accepted','declined','cancelled','expired')),
  message_id INTEGER, created_at INTEGER NOT NULL, expires_at INTEGER, answered_at INTEGER
);
CREATE UNIQUE INDEX IF NOT EXISTS ux_hug_pending ON interactions(from_id, to_id) WHERE kind='hug' AND state='pending';
CREATE INDEX IF NOT EXISTS ix_inter_from ON interactions(from_id, kind, created_at);

-- v0.19: the permanent character. Identity (characters), the citizen-ID counter (citizen_seq), the genes and the visible traits (character_genes /
-- character_traits) and the character's own XP (character_xp / character_xp_log) are separate tables on purpose; nothing in here touches the stock game.
CREATE TABLE IF NOT EXISTS citizen_seq(id INTEGER PRIMARY KEY CHECK(id = 1), next_id INTEGER NOT NULL CHECK(next_id >= 0));
INSERT OR IGNORE INTO citizen_seq(id, next_id) VALUES(1, 0);
CREATE TABLE IF NOT EXISTS characters(
  user_id INTEGER PRIMARY KEY,
  citizen_id INTEGER NOT NULL UNIQUE CHECK(citizen_id >= 0),
  first_name TEXT NOT NULL DEFAULT '', last_name TEXT NOT NULL DEFAULT '',
  gender TEXT CHECK(gender IS NULL OR gender IN ('male','female')),
  setup_state TEXT NOT NULL DEFAULT 'first' CHECK(setup_state IN ('first','last','gender','done')),
  age_started_at INTEGER,
  birth_world_id INTEGER, current_world_id INTEGER,
  origin TEXT NOT NULL DEFAULT 'new' CHECK(origin IN ('new','legacy')),
  birth_basis TEXT NOT NULL DEFAULT 'spawn' CHECK(birth_basis IN ('spawn','legacy_presence','legacy_evidence','legacy_current')),
  created_at INTEGER NOT NULL, updated_at INTEGER NOT NULL,
  CHECK((birth_world_id IS NULL) = (current_world_id IS NULL)),
  CHECK(setup_state = 'first' OR first_name <> ''),
  CHECK(setup_state IN ('first','last') OR last_name <> ''),
  CHECK(setup_state <> 'done' OR (gender IS NOT NULL AND age_started_at IS NOT NULL)),
  CHECK(setup_state = 'done' OR age_started_at IS NULL)
);
CREATE INDEX IF NOT EXISTS ix_char_world ON characters(current_world_id);
CREATE TRIGGER IF NOT EXISTS trg_char_insert BEFORE INSERT ON characters
BEGIN
  SELECT RAISE(ABORT, 'character: unknown user') WHERE NOT EXISTS(SELECT 1 FROM users WHERE id = NEW.user_id);
  SELECT RAISE(ABORT, 'character: bots have none') WHERE EXISTS(SELECT 1 FROM users WHERE id = NEW.user_id AND bot = 1);
  SELECT RAISE(ABORT, 'character: unknown world') WHERE (NEW.birth_world_id IS NOT NULL AND NOT EXISTS(SELECT 1 FROM worlds WHERE id = NEW.birth_world_id))
                                                      OR (NEW.current_world_id IS NOT NULL AND NOT EXISTS(SELECT 1 FROM worlds WHERE id = NEW.current_world_id));
END;
CREATE TRIGGER IF NOT EXISTS trg_char_update BEFORE UPDATE ON characters
BEGIN
  SELECT RAISE(ABORT, 'character: immutable field') WHERE NEW.user_id IS NOT OLD.user_id OR NEW.citizen_id IS NOT OLD.citizen_id
                                                         OR NEW.created_at IS NOT OLD.created_at OR NEW.origin IS NOT OLD.origin
                                                         OR (OLD.birth_world_id IS NOT NULL AND (NEW.birth_world_id IS NOT OLD.birth_world_id OR NEW.birth_basis IS NOT OLD.birth_basis))
                                                         OR (OLD.age_started_at IS NOT NULL AND NEW.age_started_at IS NOT OLD.age_started_at)
                                                         OR (OLD.setup_state = 'done' AND (NEW.setup_state <> 'done' OR NEW.first_name IS NOT OLD.first_name
                                                                                          OR NEW.last_name IS NOT OLD.last_name OR NEW.gender IS NOT OLD.gender));
  SELECT RAISE(ABORT, 'character: a city once set is never cleared') WHERE OLD.current_world_id IS NOT NULL AND NEW.current_world_id IS NULL;
  SELECT RAISE(ABORT, 'character: unknown world') WHERE NEW.current_world_id IS NOT NULL AND NEW.current_world_id IS NOT OLD.current_world_id
                                                       AND NOT EXISTS(SELECT 1 FROM worlds WHERE id = NEW.current_world_id);
END;
CREATE TRIGGER IF NOT EXISTS trg_char_nodelete BEFORE DELETE ON characters
BEGIN
  SELECT RAISE(ABORT, 'character: a citizen id is never reused');
END;
CREATE TABLE IF NOT EXISTS character_genes(
  user_id INTEGER NOT NULL, trait TEXT NOT NULL, allele1 TEXT NOT NULL, allele2 TEXT NOT NULL, PRIMARY KEY(user_id, trait)
);
CREATE TABLE IF NOT EXISTS character_traits(
  user_id INTEGER NOT NULL, trait TEXT NOT NULL, code TEXT NOT NULL, PRIMARY KEY(user_id, trait)
);
CREATE TRIGGER IF NOT EXISTS trg_genes_insert BEFORE INSERT ON character_genes
BEGIN SELECT RAISE(ABORT, 'genes: no such character') WHERE NOT EXISTS(SELECT 1 FROM characters WHERE user_id = NEW.user_id); END;
CREATE TRIGGER IF NOT EXISTS trg_genes_update BEFORE UPDATE ON character_genes BEGIN SELECT RAISE(ABORT, 'genes are saved once'); END;
CREATE TRIGGER IF NOT EXISTS trg_genes_delete BEFORE DELETE ON character_genes BEGIN SELECT RAISE(ABORT, 'genes are saved once'); END;
CREATE TRIGGER IF NOT EXISTS trg_traits_insert BEFORE INSERT ON character_traits
BEGIN SELECT RAISE(ABORT, 'traits: no such character') WHERE NOT EXISTS(SELECT 1 FROM characters WHERE user_id = NEW.user_id); END;
CREATE TRIGGER IF NOT EXISTS trg_traits_update BEFORE UPDATE ON character_traits BEGIN SELECT RAISE(ABORT, 'traits are saved once'); END;
CREATE TRIGGER IF NOT EXISTS trg_traits_delete BEFORE DELETE ON character_traits BEGIN SELECT RAISE(ABORT, 'traits are saved once'); END;
CREATE TABLE IF NOT EXISTS character_xp(
  user_id INTEGER PRIMARY KEY, xp INTEGER NOT NULL DEFAULT 0 CHECK(xp >= 0), updated_at INTEGER NOT NULL
);
CREATE TRIGGER IF NOT EXISTS trg_xp_only_up BEFORE UPDATE ON character_xp WHEN NEW.xp < OLD.xp
BEGIN SELECT RAISE(ABORT, 'character xp only goes up'); END;
CREATE TABLE IF NOT EXISTS character_xp_log(
  id INTEGER PRIMARY KEY AUTOINCREMENT, user_id INTEGER NOT NULL, source TEXT NOT NULL, ref TEXT NOT NULL,
  delta INTEGER NOT NULL CHECK(delta >= 0), xp_after INTEGER NOT NULL CHECK(xp_after >= 0), created_at INTEGER NOT NULL,
  UNIQUE(user_id, source, ref)
);

-- v0.20: city IDs, journeys between cities, and admin gift codes. A city's ID is `worlds.id` (AUTOINCREMENT: never reused, even after a row is removed); the trigger below
-- makes it unchangeable. A journey (`intercity_trips`) is one row per trip: the character stays in the source city (`characters.current_world_id`) until the arrival is
-- applied, which flips the row travelling -> arrived and moves the character in ONE transaction; the partial unique index allows one open journey per character.
CREATE TRIGGER IF NOT EXISTS trg_world_id_fixed BEFORE UPDATE OF id ON worlds WHEN NEW.id IS NOT OLD.id
BEGIN SELECT RAISE(ABORT, 'a city ID never changes'); END;
CREATE TABLE IF NOT EXISTS intercity_trips(
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  user_id INTEGER NOT NULL, from_world_id INTEGER NOT NULL, to_world_id INTEGER NOT NULL,
  started_at INTEGER NOT NULL, arrive_at INTEGER NOT NULL, duration INTEGER NOT NULL,
  state TEXT NOT NULL DEFAULT 'travelling' CHECK(state IN ('travelling','arrived')), arrived_at INTEGER,
  CHECK(from_world_id <> to_world_id), CHECK(arrive_at > started_at), CHECK(duration = arrive_at - started_at),
  CHECK((state = 'arrived') = (arrived_at IS NOT NULL))
);
CREATE UNIQUE INDEX IF NOT EXISTS ux_intercity_open ON intercity_trips(user_id) WHERE state = 'travelling';
CREATE INDEX IF NOT EXISTS ix_intercity_to ON intercity_trips(to_world_id, state);
CREATE TRIGGER IF NOT EXISTS trg_intercity_insert BEFORE INSERT ON intercity_trips
BEGIN
  SELECT RAISE(ABORT, 'journey: only a finished character travels') WHERE NOT EXISTS(SELECT 1 FROM characters WHERE user_id = NEW.user_id AND setup_state = 'done');
  SELECT RAISE(ABORT, 'journey: it starts in the current city') WHERE NOT EXISTS(SELECT 1 FROM characters WHERE user_id = NEW.user_id AND current_world_id = NEW.from_world_id);
  SELECT RAISE(ABORT, 'journey: unknown destination') WHERE NOT EXISTS(SELECT 1 FROM worlds WHERE id = NEW.to_world_id);
  SELECT RAISE(ABORT, 'journey: a new journey starts travelling') WHERE NEW.state <> 'travelling' OR NEW.arrived_at IS NOT NULL;
END;
CREATE TRIGGER IF NOT EXISTS trg_intercity_update BEFORE UPDATE ON intercity_trips
BEGIN
  SELECT RAISE(ABORT, 'journey: immutable field') WHERE NEW.id IS NOT OLD.id OR NEW.user_id IS NOT OLD.user_id OR NEW.from_world_id IS NOT OLD.from_world_id
                                                      OR NEW.to_world_id IS NOT OLD.to_world_id OR NEW.started_at IS NOT OLD.started_at
                                                      OR NEW.arrive_at IS NOT OLD.arrive_at OR NEW.duration IS NOT OLD.duration;
  SELECT RAISE(ABORT, 'journey: an arrived journey stays arrived') WHERE OLD.state = 'arrived';
  SELECT RAISE(ABORT, 'journey: no arrival before its time') WHERE NEW.state = 'arrived' AND (NEW.arrived_at IS NULL OR NEW.arrived_at < OLD.arrive_at);
END;
CREATE TRIGGER IF NOT EXISTS trg_intercity_arrived AFTER UPDATE OF state ON intercity_trips WHEN NEW.state = 'arrived'
BEGIN
  SELECT RAISE(ABORT, 'journey: the character must be in the destination when the journey ends')
  WHERE NOT EXISTS(SELECT 1 FROM characters WHERE user_id = NEW.user_id AND current_world_id = NEW.to_world_id);
END;
CREATE TRIGGER IF NOT EXISTS trg_intercity_nodelete BEFORE DELETE ON intercity_trips BEGIN SELECT RAISE(ABORT, 'journeys are kept'); END;

CREATE TABLE IF NOT EXISTS gift_codes(
  code TEXT PRIMARY KEY CHECK(length(code) >= 8),
  reward_type TEXT NOT NULL CHECK(reward_type IN ('stock_money','life_cash','char_xp')),
  amount INTEGER NOT NULL CHECK(amount > 0), max_uses INTEGER NOT NULL CHECK(max_uses > 0),
  used_count INTEGER NOT NULL DEFAULT 0 CHECK(used_count >= 0 AND used_count <= max_uses),
  created_by INTEGER NOT NULL, created_at INTEGER NOT NULL
);
CREATE TABLE IF NOT EXISTS gift_redemptions(
  id INTEGER PRIMARY KEY AUTOINCREMENT, code TEXT NOT NULL, user_id INTEGER NOT NULL,
  reward_type TEXT NOT NULL, amount INTEGER NOT NULL CHECK(amount > 0), redeemed_at INTEGER NOT NULL,
  UNIQUE(code, user_id)
);
CREATE TRIGGER IF NOT EXISTS trg_gift_codes_update BEFORE UPDATE ON gift_codes
BEGIN
  SELECT RAISE(ABORT, 'gift code: only the use counter moves, and only up') WHERE NEW.code IS NOT OLD.code OR NEW.reward_type IS NOT OLD.reward_type OR NEW.amount IS NOT OLD.amount
                                                                              OR NEW.max_uses IS NOT OLD.max_uses OR NEW.created_by IS NOT OLD.created_by
                                                                              OR NEW.created_at IS NOT OLD.created_at OR NEW.used_count < OLD.used_count;
END;
CREATE TRIGGER IF NOT EXISTS trg_gift_codes_nodelete BEFORE DELETE ON gift_codes BEGIN SELECT RAISE(ABORT, 'gift codes are kept'); END;
CREATE TRIGGER IF NOT EXISTS trg_gift_redeem_insert BEFORE INSERT ON gift_redemptions
BEGIN
  SELECT RAISE(ABORT, 'gift redemption: unknown code') WHERE NOT EXISTS(SELECT 1 FROM gift_codes WHERE code = NEW.code AND reward_type = NEW.reward_type AND amount = NEW.amount);
END;
CREATE TRIGGER IF NOT EXISTS trg_gift_redeem_update BEFORE UPDATE ON gift_redemptions BEGIN SELECT RAISE(ABORT, 'redemptions are kept as they were'); END;
CREATE TRIGGER IF NOT EXISTS trg_gift_redeem_delete BEFORE DELETE ON gift_redemptions BEGIN SELECT RAISE(ABORT, 'redemptions are kept'); END;
"""


def _migrate_8(conn) -> None:
    """v0.18: a trip remembers how it is travelled (walk / car), and every existing human player gets the $100 starting cash exactly once.
    Nothing existing is changed or removed: one column is added with a default, and the new life-cash tables (created by SCHEMA, empty) are
    filled. The grant is a ledger row ('grant','start') that is UNIQUE per player, so repeating this step could not pay anyone twice."""
    cols = [r[1] for r in conn.execute("PRAGMA table_info(world_trips)").fetchall()]
    if "mode" not in cols:
        conn.execute("ALTER TABLE world_trips ADD COLUMN mode TEXT NOT NULL DEFAULT 'walk'")
    import time
    now = int(time.time())
    cents = 100_00
    for (uid,) in conn.execute("SELECT id FROM users WHERE bot=0 ORDER BY id").fetchall():
        conn.execute("INSERT OR IGNORE INTO life_accounts(user_id,balance,created_at,updated_at) VALUES(?,0,?,?)", (uid, now, now))
        cur = conn.execute("INSERT OR IGNORE INTO life_ledger(user_id,kind,ref,delta,balance_after,stock_delta,note,created_at) VALUES(?,?,?,?,?,0,'',?)",
                           (uid, "grant", "start", cents, cents, now))
        if cur.rowcount == 1:
            conn.execute("UPDATE life_accounts SET balance=balance+?, updated_at=? WHERE user_id=?", (cents, now, uid))


def _migrate_9(conn) -> None:
    """v0.18.1: blackjack is played for life cash. A game row remembers the stake of its hand (`bet`, cents), which hand of the table it is
    (`hand_no`, it names the ledger rows of that hand) and what the finished hand paid back (`paid`, cents). Three columns are added with
    defaults; every existing game keeps its cards and its result (bet 0: a hand of the old no-stakes table, nothing was ever at risk)."""
    cols = [r[1] for r in conn.execute("PRAGMA table_info(casino_games)").fetchall()]
    for name in ("bet", "hand_no", "paid"):
        if name not in cols:
            conn.execute(f"ALTER TABLE casino_games ADD COLUMN {name} INTEGER NOT NULL DEFAULT {1 if name == 'hand_no' else 0}")


def _migrate_5(conn) -> None:
    """v0.13: short selling was removed. Refund every open margin to its owner's cash, give back the 3 skill points the
    'short' skill cost, delete the skill and drop the table. Affected players are told once (kv keys mig5:<id>)."""
    has = conn.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='shorts'").fetchone() is not None
    if has:
        for r in conn.execute("SELECT user_id, SUM(margin) m FROM shorts GROUP BY user_id").fetchall():
            conn.execute("UPDATE users SET cash=cash+? WHERE id=?", (r[1], r[0]))
            conn.execute("INSERT OR REPLACE INTO kv(key,value) VALUES(?,?)", (f"mig5:{r[0]}", f"{r[1]},0"))
    for r in conn.execute("SELECT user_id FROM perks WHERE perk='short'").fetchall():
        conn.execute("UPDATE users SET spent_points=MAX(0, spent_points-3) WHERE id=?", (r[0],))
        old = conn.execute("SELECT value FROM kv WHERE key=?", (f"mig5:{r[0]}",)).fetchone()
        refund = int(str(old[0]).split(",")[0]) if old else 0
        conn.execute("INSERT OR REPLACE INTO kv(key,value) VALUES(?,?)", (f"mig5:{r[0]}", f"{refund},3"))
    conn.execute("DELETE FROM perks WHERE perk='short'")
    conn.execute("DROP TABLE IF EXISTS shorts")


class _RawDb:
    """The three calls character.py needs, on the bare connection of a migration (which already runs inside its own transaction)."""
    def __init__(self, conn):
        self.conn = conn

    def x(self, sql, args=()):
        return self.conn.execute(sql, args)

    def q1(self, sql, args=()):
        return self.conn.execute(sql, args).fetchone()

    def qa(self, sql, args=()):
        return self.conn.execute(sql, args).fetchall()

    @contextmanager
    def tx(self):
        yield self


def _migrate_10(conn) -> None:
    """v0.19: every existing human player with saved world activity gets a PENDING character (names and gender are still to be chosen by the player on their next
    life-game entry): a citizen ID from the sequence (0, 1, 2, ... in ascending Telegram user ID), stable traits, 0 character XP, and a birthplace and current city
    reconstructed from the old rows by the documented rule (character.legacy_choice). Only NEW rows are written (characters, genes, traits, xp, the counter): no
    existing table, row, balance, ledger entry, city, position, trip, task or casino game is changed or removed, and running it again creates nothing."""
    from . import character
    character.migrate_legacy(_RawDb(conn))


SCHEMA_VERSION = 11
# version -> SQL (or a function taking the connection) upgrading a database FROM version-1.
# New tables need no entry: SCHEMA creates them with IF NOT EXISTS.
MIGRATIONS: dict = {
    2: """
    ALTER TABLE users ADD COLUMN whale_dm INTEGER NOT NULL DEFAULT 1;
    ALTER TABLE users ADD COLUMN bot INTEGER NOT NULL DEFAULT 0;
    ALTER TABLE users ADD COLUMN bonus_points INTEGER NOT NULL DEFAULT 0;
    """,
    3: "SELECT 1;",   # v3 only added new tables (reports, trust, achievements, seen, modlog), which SCHEMA creates
    4: "SELECT 1;",   # v4 only added the salary table, which SCHEMA creates
    5: _migrate_5,    # v5: short selling removed; the exchange tables are created by SCHEMA
    6: "SELECT 1;",                          # v0.15: the `worlds` table (one city per group) comes from SCHEMA; nothing else changes
    7: "SELECT 1;",                          # v0.17: world_places / world_players / world_trips / map_sessions / casino_games are new tables from SCHEMA; no existing table or row changes
    8: _migrate_8,                           # v0.18: life cash + exchange + jobs + interactions tables (from SCHEMA), world_trips.mode, and the one-time $100 grant to existing players
    9: _migrate_9,                           # v0.18.1: casino_games.bet / hand_no / paid (blackjack for life cash); nothing existing changes
    10: _migrate_10,                         # v0.19: characters (tables from SCHEMA) and a pending character for every player with saved world activity; nothing existing changes
    11: "SELECT 1;",                         # v0.20: intercity_trips, gift_codes, gift_redemptions and the unchangeable city ID are new tables / triggers from SCHEMA; nothing existing changes
}


class Database:
    def __init__(self, path: str = ":memory:") -> None:
        self.conn = sqlite3.connect(path, isolation_level=None, check_same_thread=False)
        self.conn.row_factory = sqlite3.Row
        self.conn.execute("PRAGMA journal_mode=WAL")
        self.conn.execute("PRAGMA synchronous=NORMAL")
        self.conn.execute("PRAGMA foreign_keys=OFF")
        self._depth = 0
        self._open_schema()
        self.outbox: list = []   # (user_id, key, kwargs) direct messages queued by game actions
        self.quest_events: list = []   # (user_id, quest_id, reward_units, points) completed quests waiting to be announced
        self.ach_events: list = []     # (user_id, achievement_id, reward_units, points) unlocked achievements waiting to be announced
        self.mod_events: list = []     # ("report", stock_id) moderation alerts waiting to be sent to the admins

    def _open_schema(self) -> None:
        """Create tables on a new database, or upgrade an older one in place (data is kept)."""
        existed = self.conn.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='users'").fetchone() is not None
        ver = self.conn.execute("PRAGMA user_version").fetchone()[0]
        if ver > SCHEMA_VERSION:
            raise RuntimeError(f"This database was created by a newer version of the bot (schema {ver} > {SCHEMA_VERSION}).")
        self.conn.executescript(SCHEMA)          # creates missing tables; leaves existing ones untouched
        if not existed:
            self.conn.execute(f"PRAGMA user_version={SCHEMA_VERSION}")
            return
        cur = ver or 1                           # databases from before versioning are schema 1
        for v in range(cur + 1, SCHEMA_VERSION + 1):
            step = MIGRATIONS[v]
            if callable(step):                   # a Python migration runs inside one transaction: all or nothing
                self.conn.execute("BEGIN IMMEDIATE")
                try:
                    step(self.conn)
                    self.conn.execute("COMMIT")
                except BaseException:
                    self.conn.execute("ROLLBACK")
                    raise
            else:
                self.conn.executescript(step)
            self.conn.execute(f"PRAGMA user_version={v}")
        if ver == 0 and cur == SCHEMA_VERSION:
            self.conn.execute(f"PRAGMA user_version={SCHEMA_VERSION}")

    def backup_to(self, path: str) -> None:
        """Consistent online copy of the whole database (safe while the bot is running)."""
        if self._depth:
            raise RuntimeError("backup inside a transaction")
        dest = sqlite3.connect(path)
        try:
            self.conn.backup(dest)
        finally:
            dest.close()

    def x(self, sql: str, args: tuple = ()) -> sqlite3.Cursor:
        return self.conn.execute(sql, args)

    def q1(self, sql: str, args: tuple = ()):
        return self.conn.execute(sql, args).fetchone()

    def qa(self, sql: str, args: tuple = ()) -> list:
        return self.conn.execute(sql, args).fetchall()

    @contextmanager
    def tx(self):
        """Atomic block. Nested use joins the outer transaction."""
        if self._depth:
            self._depth += 1
            try:
                yield self
            finally:
                self._depth -= 1
            return
        self.conn.execute("BEGIN IMMEDIATE")
        self._depth = 1
        mark, mark_q, mark_a, mark_m = len(self.outbox), len(self.quest_events), len(self.ach_events), len(self.mod_events)
        try:
            yield self
            self.conn.execute("COMMIT")
        except BaseException:
            self.conn.execute("ROLLBACK")
            del self.outbox[mark:]
            del self.quest_events[mark_q:]
            del self.ach_events[mark_a:]
            del self.mod_events[mark_m:]
            raise
        finally:
            self._depth = 0

    def close(self) -> None:
        self.conn.close()
