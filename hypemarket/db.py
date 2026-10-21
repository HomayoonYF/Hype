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
  state TEXT NOT NULL DEFAULT 'walking' CHECK(state IN ('walking','arrived','cancelled')), CHECK(arrive_at > started_at)
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
  created_at INTEGER NOT NULL, updated_at INTEGER NOT NULL
);
"""


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


SCHEMA_VERSION = 7
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
