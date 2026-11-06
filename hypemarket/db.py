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
  reward_type TEXT NOT NULL CHECK(reward_type IN ('stock_money','life_cash','char_xp','item')),
  amount INTEGER NOT NULL CHECK(amount > 0), max_uses INTEGER NOT NULL CHECK(max_uses > 0),
  used_count INTEGER NOT NULL DEFAULT 0 CHECK(used_count >= 0 AND used_count <= max_uses),
  created_by INTEGER NOT NULL, created_at INTEGER NOT NULL,
  item_id TEXT,
  CHECK((reward_type = 'item') = (item_id IS NOT NULL))
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
                                                                              OR NEW.created_at IS NOT OLD.created_at OR NEW.item_id IS NOT OLD.item_id OR NEW.used_count < OLD.used_count;
END;
CREATE TRIGGER IF NOT EXISTS trg_gift_codes_nodelete BEFORE DELETE ON gift_codes BEGIN SELECT RAISE(ABORT, 'gift codes are kept'); END;
CREATE TRIGGER IF NOT EXISTS trg_gift_redeem_insert BEFORE INSERT ON gift_redemptions
BEGIN
  SELECT RAISE(ABORT, 'gift redemption: unknown code') WHERE NOT EXISTS(SELECT 1 FROM gift_codes WHERE code = NEW.code AND reward_type = NEW.reward_type AND amount = NEW.amount);
END;
CREATE TRIGGER IF NOT EXISTS trg_gift_redeem_update BEFORE UPDATE ON gift_redemptions BEGIN SELECT RAISE(ABORT, 'redemptions are kept as they were'); END;
CREATE TRIGGER IF NOT EXISTS trg_gift_redeem_delete BEFORE DELETE ON gift_redemptions BEGIN SELECT RAISE(ABORT, 'redemptions are kept'); END;

-- v0.20 (identity document): what a character's national ID shows besides the v0.19 profile. One row per character, created with the character (`character_identity`).
-- Created at birth: nationality, languages (game-world defaults, never read from Telegram), citizen status and the fictional fingerprint ID. Set ONCE when the profile is finished
-- (`issue`; for a character that was already finished it is backfilled from its saved age anchor): birthday, height in cm, signature seed, document number, issue and expiry
-- dates and the issuing authority. The home address has a city (the birth city) and three components that stay empty until a future property system assigns them.
CREATE TABLE IF NOT EXISTS character_identity(
  user_id INTEGER PRIMARY KEY,
  nationality TEXT NOT NULL CHECK(nationality <> ''), languages TEXT NOT NULL CHECK(languages <> ''),
  fingerprint_id TEXT NOT NULL UNIQUE CHECK(length(fingerprint_id) >= 8),
  status TEXT NOT NULL DEFAULT 'active' CHECK(status IN ('active','suspended','revoked')),
  home_world_id INTEGER, home_district TEXT, home_street TEXT, home_building TEXT,
  born_at INTEGER, height_cm INTEGER CHECK(height_cm IS NULL OR height_cm BETWEEN 100 AND 250),
  signature_seed TEXT, doc_number TEXT UNIQUE, issued_at INTEGER, expires_at INTEGER, authority TEXT,
  created_at INTEGER NOT NULL, updated_at INTEGER NOT NULL,
  CHECK((born_at IS NULL) = (issued_at IS NULL) AND (issued_at IS NULL) = (expires_at IS NULL) AND (issued_at IS NULL) = (doc_number IS NULL)
        AND (issued_at IS NULL) = (authority IS NULL) AND (issued_at IS NULL) = (height_cm IS NULL) AND (issued_at IS NULL) = (signature_seed IS NULL)),
  CHECK(expires_at IS NULL OR expires_at > issued_at)
);
CREATE TRIGGER IF NOT EXISTS trg_ident_insert BEFORE INSERT ON character_identity
BEGIN
  SELECT RAISE(ABORT, 'identity: no such character') WHERE NOT EXISTS(SELECT 1 FROM characters WHERE user_id = NEW.user_id);
  SELECT RAISE(ABORT, 'identity: unknown home city') WHERE NEW.home_world_id IS NOT NULL AND NOT EXISTS(SELECT 1 FROM worlds WHERE id = NEW.home_world_id);
END;
CREATE TRIGGER IF NOT EXISTS trg_ident_update BEFORE UPDATE ON character_identity
BEGIN
  SELECT RAISE(ABORT, 'identity: immutable field') WHERE NEW.user_id IS NOT OLD.user_id OR NEW.fingerprint_id IS NOT OLD.fingerprint_id OR NEW.created_at IS NOT OLD.created_at
      OR (OLD.issued_at IS NOT NULL AND (NEW.born_at IS NOT OLD.born_at OR NEW.height_cm IS NOT OLD.height_cm OR NEW.signature_seed IS NOT OLD.signature_seed
                                         OR NEW.doc_number IS NOT OLD.doc_number OR NEW.issued_at IS NOT OLD.issued_at OR NEW.expires_at IS NOT OLD.expires_at
                                         OR NEW.authority IS NOT OLD.authority))
      OR (OLD.home_world_id IS NOT NULL AND NEW.home_world_id IS NULL);
  SELECT RAISE(ABORT, 'identity: unknown home city') WHERE NEW.home_world_id IS NOT NULL AND NEW.home_world_id IS NOT OLD.home_world_id
                                                          AND NOT EXISTS(SELECT 1 FROM worlds WHERE id = NEW.home_world_id);
END;
CREATE TRIGGER IF NOT EXISTS trg_ident_nodelete BEFORE DELETE ON character_identity BEGIN SELECT RAISE(ABORT, 'identity is kept'); END;

-- v0.21 (inventory). The ITEM CATALOG (what an item is) lives in code (items.py); these tables hold what players OWN. `item_inventory`: one row per STACK (a player, an item, a quantity
-- of at least 1, when and how the stack was started, optional item-specific data as JSON); a non-stackable item is one row of quantity 1 each. `item_equipment`: what a player wears,
-- ONE item per slot (the primary key); an equipped item is out of the bag and goes back into it when taken off. `item_log`: the append-only ledger of every grant and removal with its
-- source and reference; a (user, item, source, reference) can gain (or lose) only once, so a repeated callback or retry never gives (or takes) the same thing twice.
CREATE TABLE IF NOT EXISTS item_inventory(
  id INTEGER PRIMARY KEY AUTOINCREMENT, user_id INTEGER NOT NULL, item_id TEXT NOT NULL CHECK(item_id <> ''),
  quantity INTEGER NOT NULL CHECK(quantity >= 1), acquired_at INTEGER NOT NULL, source TEXT NOT NULL CHECK(source <> ''), data TEXT
);
CREATE INDEX IF NOT EXISTS ix_item_inventory_user ON item_inventory(user_id, item_id);
CREATE TRIGGER IF NOT EXISTS trg_item_inv_update BEFORE UPDATE ON item_inventory
BEGIN
  SELECT RAISE(ABORT, 'inventory: the owner and the item of a stack never change') WHERE NEW.id IS NOT OLD.id OR NEW.user_id IS NOT OLD.user_id OR NEW.item_id IS NOT OLD.item_id;
END;
CREATE TABLE IF NOT EXISTS item_equipment(
  user_id INTEGER NOT NULL, slot TEXT NOT NULL CHECK(slot <> ''), item_id TEXT NOT NULL CHECK(item_id <> ''),
  acquired_at INTEGER NOT NULL, source TEXT NOT NULL CHECK(source <> ''), data TEXT, equipped_at INTEGER NOT NULL,
  PRIMARY KEY(user_id, slot)
) WITHOUT ROWID;
CREATE TABLE IF NOT EXISTS item_log(
  id INTEGER PRIMARY KEY AUTOINCREMENT, user_id INTEGER NOT NULL, item_id TEXT NOT NULL CHECK(item_id <> ''),
  delta INTEGER NOT NULL CHECK(delta <> 0), source TEXT NOT NULL CHECK(source <> ''), ref TEXT, created_at INTEGER NOT NULL
);
CREATE UNIQUE INDEX IF NOT EXISTS uq_item_log_gain ON item_log(user_id, item_id, source, ref) WHERE ref IS NOT NULL AND delta > 0;
CREATE UNIQUE INDEX IF NOT EXISTS uq_item_log_loss ON item_log(user_id, item_id, source, ref) WHERE ref IS NOT NULL AND delta < 0;
CREATE TRIGGER IF NOT EXISTS trg_item_log_update BEFORE UPDATE ON item_log BEGIN SELECT RAISE(ABORT, 'the item log is kept as it was'); END;
CREATE TRIGGER IF NOT EXISTS trg_item_log_delete BEFORE DELETE ON item_log BEGIN SELECT RAISE(ABORT, 'the item log is kept'); END;

-- v0.211. ITEM TEMPLATES: the admin's giftable items (empty by default: nothing is seeded). `kind`: custom (the row IS the item's definition, item_id `tpl_<n>`), product (a preset for a product type of
-- fashion.py: item_id is that product's id) or legacy (the definition of a v0.21 starter item that a database already referenced: kept so the item still has a name). `gift_item_data` holds the
-- instance data (quality, brand) of an item gift code; it is write-once, like the code itself.
CREATE TABLE IF NOT EXISTS item_templates(
  id INTEGER PRIMARY KEY AUTOINCREMENT, item_id TEXT NOT NULL UNIQUE CHECK(item_id <> ''), kind TEXT NOT NULL CHECK(kind IN ('custom','product','legacy')),
  name_fa TEXT NOT NULL CHECK(name_fa <> ''), name_en TEXT, category TEXT NOT NULL, description_fa TEXT NOT NULL DEFAULT '', description_en TEXT, slot TEXT,
  max_stack INTEGER NOT NULL DEFAULT 1 CHECK(max_stack >= 1), icon TEXT NOT NULL DEFAULT '📦', has_quality INTEGER NOT NULL DEFAULT 0, brandable INTEGER NOT NULL DEFAULT 0,
  created_by INTEGER, created_at INTEGER NOT NULL
);
CREATE TABLE IF NOT EXISTS gift_item_data(code TEXT PRIMARY KEY, data TEXT NOT NULL CHECK(data <> ''));
CREATE TRIGGER IF NOT EXISTS trg_gift_item_data_update BEFORE UPDATE ON gift_item_data BEGIN SELECT RAISE(ABORT, 'gift item data is kept as it was'); END;
CREATE TRIGGER IF NOT EXISTS trg_gift_item_data_delete BEFORE DELETE ON gift_item_data BEGIN SELECT RAISE(ABORT, 'gift item data is kept'); END;

-- v0.211. SKILLS (skills.py): `work` in thousandths of a unit, only up; `skill_log` is the append-only record, UNIQUE per (player, skill, source, reference): a grant happens once.
CREATE TABLE IF NOT EXISTS character_skills(
  user_id INTEGER NOT NULL, skill TEXT NOT NULL, work INTEGER NOT NULL DEFAULT 0 CHECK(work >= 0), updated_at INTEGER NOT NULL, PRIMARY KEY(user_id, skill)
) WITHOUT ROWID;
CREATE TRIGGER IF NOT EXISTS trg_skill_only_up BEFORE UPDATE ON character_skills WHEN NEW.work < OLD.work BEGIN SELECT RAISE(ABORT, 'skills only go up'); END;
CREATE TABLE IF NOT EXISTS skill_log(
  id INTEGER PRIMARY KEY AUTOINCREMENT, user_id INTEGER NOT NULL, skill TEXT NOT NULL, source TEXT NOT NULL, ref TEXT NOT NULL, delta INTEGER NOT NULL CHECK(delta >= 0),
  work_after INTEGER NOT NULL CHECK(work_after >= 0), created_at INTEGER NOT NULL, UNIQUE(user_id, skill, source, ref)
);
CREATE TRIGGER IF NOT EXISTS trg_skill_log_update BEFORE UPDATE ON skill_log BEGIN SELECT RAISE(ABORT, 'the skill log is kept as it was'); END;
CREATE TRIGGER IF NOT EXISTS trg_skill_log_delete BEFORE DELETE ON skill_log BEGIN SELECT RAISE(ABORT, 'the skill log is kept'); END;

-- v0.211. THE BUSINESS WORLD. Every table below is new (nothing existing changes). Money is always LIFE CASH in cents; a business's money (its till) moves only together with a row of
-- the player's own cash ledger (cash.apply), so no cent appears or disappears without a record.
-- PROPERTIES (estate.py): one row per lot of a saved city that can hold a business, made once from the lot's geometry (the layout itself is never edited). The lot, its size, zone and price
-- never change; the owner is set once, by a purchase, and never changes (there is no resale in this version).
CREATE TABLE IF NOT EXISTS properties(
  id INTEGER PRIMARY KEY AUTOINCREMENT, world_id INTEGER NOT NULL, parcel_id TEXT NOT NULL CHECK(parcel_id <> ''), zone TEXT NOT NULL, area_m2 INTEGER NOT NULL CHECK(area_m2 > 0),
  tier TEXT NOT NULL, price INTEGER NOT NULL CHECK(price > 0), warehouse_cap INTEGER NOT NULL CHECK(warehouse_cap >= 0), display_cap INTEGER NOT NULL CHECK(display_cap >= 0),
  staff_slots INTEGER NOT NULL CHECK(staff_slots >= 0), equip_slots INTEGER NOT NULL CHECK(equip_slots >= 0), max_quality INTEGER NOT NULL CHECK(max_quality BETWEEN 1 AND 4),
  modes TEXT NOT NULL CHECK(modes <> ''), mode TEXT CHECK(mode IS NULL OR mode IN ('retail','production','both')), owner_id INTEGER, bought_at INTEGER, bought_price INTEGER,
  fitted_at INTEGER, created_at INTEGER NOT NULL, UNIQUE(world_id, parcel_id), CHECK((owner_id IS NULL) = (bought_at IS NULL))
);
CREATE INDEX IF NOT EXISTS ix_properties_owner ON properties(owner_id);
CREATE TRIGGER IF NOT EXISTS trg_properties_fixed BEFORE UPDATE ON properties
WHEN NEW.id IS NOT OLD.id OR NEW.world_id IS NOT OLD.world_id OR NEW.parcel_id IS NOT OLD.parcel_id OR NEW.zone IS NOT OLD.zone OR NEW.area_m2 IS NOT OLD.area_m2 OR NEW.tier IS NOT OLD.tier
  OR NEW.price IS NOT OLD.price OR NEW.warehouse_cap IS NOT OLD.warehouse_cap OR NEW.display_cap IS NOT OLD.display_cap OR NEW.staff_slots IS NOT OLD.staff_slots
  OR NEW.equip_slots IS NOT OLD.equip_slots OR NEW.max_quality IS NOT OLD.max_quality OR NEW.modes IS NOT OLD.modes OR (OLD.owner_id IS NOT NULL AND NEW.owner_id IS NOT OLD.owner_id)
BEGIN SELECT RAISE(ABORT, 'a property keeps its lot, size, price and owner'); END;
CREATE TRIGGER IF NOT EXISTS trg_properties_nodelete BEFORE DELETE ON properties BEGIN SELECT RAISE(ABORT, 'properties are kept'); END;
-- BRANDS (brands.py): the primary key IS the global rule: one owner per brand name, whatever races. The owner never changes and a brand is never deleted.
CREATE TABLE IF NOT EXISTS brands(brand TEXT PRIMARY KEY CHECK(brand <> ''), owner_id INTEGER NOT NULL, fee INTEGER NOT NULL CHECK(fee >= 0), registered_at INTEGER NOT NULL) WITHOUT ROWID;
CREATE INDEX IF NOT EXISTS ix_brands_owner ON brands(owner_id);
CREATE TRIGGER IF NOT EXISTS trg_brands_fixed BEFORE UPDATE ON brands BEGIN SELECT RAISE(ABORT, 'a registered brand keeps its owner'); END;
CREATE TRIGGER IF NOT EXISTS trg_brands_nodelete BEFORE DELETE ON brands BEGIN SELECT RAISE(ABORT, 'brands are kept'); END;
-- BRANCHES (biz.py): one business of a brand at one property. At most ONE open branch per property (the partial index); a closed branch stays as history. `till` is the branch's own money.
CREATE TABLE IF NOT EXISTS branches(
  id INTEGER PRIMARY KEY AUTOINCREMENT, brand TEXT NOT NULL, owner_id INTEGER NOT NULL, property_id INTEGER NOT NULL, world_id INTEGER NOT NULL, seq INTEGER NOT NULL CHECK(seq >= 1),
  state TEXT NOT NULL DEFAULT 'open' CHECK(state IN ('open','closed')), sign TEXT NOT NULL DEFAULT 'plain', till INTEGER NOT NULL DEFAULT 0 CHECK(till >= 0),
  opened_at INTEGER NOT NULL, closed_at INTEGER, created_at INTEGER NOT NULL, UNIQUE(brand, world_id, seq)
);
CREATE UNIQUE INDEX IF NOT EXISTS uq_branch_property_open ON branches(property_id) WHERE state = 'open';
CREATE INDEX IF NOT EXISTS ix_branches_owner ON branches(owner_id, state);
CREATE INDEX IF NOT EXISTS ix_branches_brand ON branches(brand, state);
CREATE TRIGGER IF NOT EXISTS trg_branches_fixed BEFORE UPDATE ON branches
WHEN NEW.id IS NOT OLD.id OR NEW.brand IS NOT OLD.brand OR NEW.owner_id IS NOT OLD.owner_id OR NEW.property_id IS NOT OLD.property_id OR NEW.world_id IS NOT OLD.world_id OR NEW.seq IS NOT OLD.seq
  OR (OLD.state = 'closed' AND NEW.state IS NOT 'closed')
BEGIN SELECT RAISE(ABORT, 'a branch keeps its brand, owner and property, and a closed branch stays closed'); END;
CREATE TRIGGER IF NOT EXISTS trg_branches_nodelete BEFORE DELETE ON branches BEGIN SELECT RAISE(ABORT, 'branches are kept'); END;
-- THE TILL: every movement of a branch's money, append-only, UNIQUE per (branch, kind, reference): the same movement twice is a no-op.
CREATE TABLE IF NOT EXISTS biz_ledger(
  id INTEGER PRIMARY KEY AUTOINCREMENT, branch_id INTEGER NOT NULL, kind TEXT NOT NULL, ref TEXT NOT NULL, delta INTEGER NOT NULL CHECK(delta <> 0),
  balance_after INTEGER NOT NULL CHECK(balance_after >= 0), actor INTEGER, note TEXT NOT NULL DEFAULT '', created_at INTEGER NOT NULL, UNIQUE(branch_id, kind, ref)
);
CREATE INDEX IF NOT EXISTS ix_biz_ledger_branch ON biz_ledger(branch_id, id);
CREATE TRIGGER IF NOT EXISTS trg_biz_ledger_update BEFORE UPDATE ON biz_ledger BEGIN SELECT RAISE(ABORT, 'the branch ledger is kept as it was'); END;
CREATE TRIGGER IF NOT EXISTS trg_biz_ledger_delete BEFORE DELETE ON biz_ledger BEGIN SELECT RAISE(ABORT, 'the branch ledger is kept'); END;
-- UPGRADES (upgrades.py): the level of each upgrade a branch has installed (only up).
CREATE TABLE IF NOT EXISTS branch_upgrades(branch_id INTEGER NOT NULL, kind TEXT NOT NULL, level INTEGER NOT NULL CHECK(level >= 1), installed_at INTEGER NOT NULL, PRIMARY KEY(branch_id, kind)) WITHOUT ROWID;
CREATE TRIGGER IF NOT EXISTS trg_branch_upgrades_up BEFORE UPDATE ON branch_upgrades WHEN NEW.level < OLD.level BEGIN SELECT RAISE(ABORT, 'an installed upgrade is never removed'); END;
-- EVENTS: a short append-only record of the business world's one-off facts (a property bought, fitted or converted, a brand registered, a branch opened or closed ...).
CREATE TABLE IF NOT EXISTS biz_events(id INTEGER PRIMARY KEY AUTOINCREMENT, kind TEXT NOT NULL, subject TEXT NOT NULL, actor INTEGER, data TEXT NOT NULL DEFAULT '', created_at INTEGER NOT NULL);
CREATE TRIGGER IF NOT EXISTS trg_biz_events_update BEFORE UPDATE ON biz_events BEGIN SELECT RAISE(ABORT, 'the event record is kept as it was'); END;
CREATE TRIGGER IF NOT EXISTS trg_biz_events_delete BEFORE DELETE ON biz_events BEGIN SELECT RAISE(ABORT, 'the event record is kept'); END;
-- STOCK (stock.py): what a branch holds, in two separate places: the WAREHOUSE ('wh') and the public DISPLAY ('disp'). `data` is the canonical item data (quality, brand, production history), '' for none.
-- A quantity never goes below zero (CHECK); every change has a row in `stock_log` (append-only, unique per branch, kind, reference, place and variant), so a repeated operation is a no-op.
CREATE TABLE IF NOT EXISTS branch_stock(
  branch_id INTEGER NOT NULL, loc TEXT NOT NULL CHECK(loc IN ('wh','disp')), item_id TEXT NOT NULL, data TEXT NOT NULL DEFAULT '', qty INTEGER NOT NULL CHECK(qty >= 0),
  PRIMARY KEY(branch_id, loc, item_id, data)
) WITHOUT ROWID;
CREATE TABLE IF NOT EXISTS stock_log(
  id INTEGER PRIMARY KEY AUTOINCREMENT, branch_id INTEGER NOT NULL, kind TEXT NOT NULL, loc TEXT NOT NULL, item_id TEXT NOT NULL, data TEXT NOT NULL DEFAULT '', qty INTEGER NOT NULL CHECK(qty <> 0),
  ref TEXT NOT NULL, actor INTEGER, cents INTEGER NOT NULL DEFAULT 0, created_at INTEGER NOT NULL, UNIQUE(branch_id, kind, ref, loc, item_id, data)
);
CREATE INDEX IF NOT EXISTS ix_stock_log_branch ON stock_log(branch_id, id);
CREATE TRIGGER IF NOT EXISTS trg_stock_log_update BEFORE UPDATE ON stock_log BEGIN SELECT RAISE(ABORT, 'the stock log is kept as it was'); END;
CREATE TRIGGER IF NOT EXISTS trg_stock_log_delete BEFORE DELETE ON stock_log BEGIN SELECT RAISE(ABORT, 'the stock log is kept'); END;
-- the owner's standing instructions for one product variant: its price, how many to keep on display, how many to keep back in the warehouse, whether the warehouse refills the display and whether the
-- SURPLUS (the warehouse beyond `keep`, once the display is full) is sold to the game. Nothing is ever sold to the game unless `buyback` is 1.
CREATE TABLE IF NOT EXISTS stock_rules(
  branch_id INTEGER NOT NULL, item_id TEXT NOT NULL, data TEXT NOT NULL DEFAULT '', price INTEGER CHECK(price IS NULL OR price > 0), target INTEGER NOT NULL DEFAULT 0 CHECK(target >= 0),
  keep INTEGER NOT NULL DEFAULT 0 CHECK(keep >= 0), refill INTEGER NOT NULL DEFAULT 0 CHECK(refill IN (0,1)), buyback INTEGER NOT NULL DEFAULT 0 CHECK(buyback IN (0,1)), updated_at INTEGER NOT NULL,
  PRIMARY KEY(branch_id, item_id, data)
) WITHOUT ROWID;
-- PURCHASE ADS (market.py): a branch wants material. The budget (qty x unit price) leaves the branch's till when the ad is created and stays reserved in the ad (`reserved`), so the money is
-- there when a seller fulfils it; what is not used goes back when the ad closes. A fulfilment is one row of `ad_fills`, unique by its reference.
CREATE TABLE IF NOT EXISTS material_ads(
  id INTEGER PRIMARY KEY AUTOINCREMENT, branch_id INTEGER NOT NULL, buyer_id INTEGER NOT NULL, material TEXT NOT NULL, min_q INTEGER NOT NULL CHECK(min_q BETWEEN 1 AND 5),
  unit_cents INTEGER NOT NULL CHECK(unit_cents > 0), qty_total INTEGER NOT NULL CHECK(qty_total > 0), qty_left INTEGER NOT NULL CHECK(qty_left >= 0 AND qty_left <= qty_total),
  reserved INTEGER NOT NULL CHECK(reserved >= 0), state TEXT NOT NULL DEFAULT 'open' CHECK(state IN ('open','filled','cancelled','expired')), expires_at INTEGER NOT NULL, created_at INTEGER NOT NULL, closed_at INTEGER,
  CHECK(state <> 'open' OR reserved = qty_left * unit_cents), CHECK(state = 'open' OR reserved = 0)
);
CREATE INDEX IF NOT EXISTS ix_ads_open ON material_ads(state, expires_at);
CREATE INDEX IF NOT EXISTS ix_ads_branch ON material_ads(branch_id, state);
CREATE TABLE IF NOT EXISTS ad_fills(
  id INTEGER PRIMARY KEY AUTOINCREMENT, ad_id INTEGER NOT NULL, seller_id INTEGER NOT NULL, qty INTEGER NOT NULL CHECK(qty > 0), unit_cents INTEGER NOT NULL, total_cents INTEGER NOT NULL CHECK(total_cents > 0),
  quality INTEGER NOT NULL, ref TEXT NOT NULL UNIQUE, created_at INTEGER NOT NULL
);
CREATE TRIGGER IF NOT EXISTS trg_ad_fills_update BEFORE UPDATE ON ad_fills BEGIN SELECT RAISE(ABORT, 'a fulfilment is kept as it was'); END;
CREATE TRIGGER IF NOT EXISTS trg_ad_fills_delete BEFORE DELETE ON ad_fills BEGIN SELECT RAISE(ABORT, 'fulfilments are kept'); END;
-- PRODUCTION ORDERS (tailor.py). One row is one batch of pieces: who made it, from what, at which property, how long it took and the quality with the factors that decided it. Once done it IS the
-- production history of the pieces (their item data carries this id as `ph`). At most one order works at a time in a branch and for a worker; a commercial quality is never above the property's
-- own highest quality (a trigger), whatever the code does.
CREATE TABLE IF NOT EXISTS prod_orders(
  id INTEGER PRIMARY KEY AUTOINCREMENT, branch_id INTEGER NOT NULL, property_id INTEGER NOT NULL, world_id INTEGER NOT NULL, brand TEXT NOT NULL, product TEXT NOT NULL, pieces INTEGER NOT NULL CHECK(pieces > 0),
  worker_id INTEGER, bot INTEGER NOT NULL DEFAULT 0 CHECK(bot IN (0,1)), requested_by INTEGER NOT NULL, state TEXT NOT NULL DEFAULT 'working' CHECK(state IN ('working','done','cancelled')),
  quality INTEGER NOT NULL CHECK(quality BETWEEN 1 AND 5), factors TEXT NOT NULL, materials TEXT NOT NULL, seconds INTEGER NOT NULL CHECK(seconds > 0), skill INTEGER NOT NULL DEFAULT 0, equipment TEXT NOT NULL DEFAULT '',
  task_id INTEGER, started_at INTEGER NOT NULL, due_at INTEGER NOT NULL, finished_at INTEGER, CHECK((bot = 1) = (worker_id IS NULL))
);
CREATE UNIQUE INDEX IF NOT EXISTS uq_prod_branch_working ON prod_orders(branch_id) WHERE state = 'working';
CREATE UNIQUE INDEX IF NOT EXISTS uq_prod_worker_working ON prod_orders(worker_id) WHERE state = 'working' AND worker_id IS NOT NULL;
CREATE INDEX IF NOT EXISTS ix_prod_branch ON prod_orders(branch_id, id);
CREATE TRIGGER IF NOT EXISTS trg_prod_quality BEFORE INSERT ON prod_orders
WHEN NEW.quality > COALESCE((SELECT max_quality FROM properties WHERE id = NEW.property_id), 0)
BEGIN SELECT RAISE(ABORT, 'a property never makes pieces better than its own highest quality'); END;
CREATE TRIGGER IF NOT EXISTS trg_prod_fixed BEFORE UPDATE ON prod_orders
WHEN NEW.id IS NOT OLD.id OR NEW.branch_id IS NOT OLD.branch_id OR NEW.product IS NOT OLD.product OR NEW.pieces IS NOT OLD.pieces OR NEW.quality IS NOT OLD.quality OR NEW.brand IS NOT OLD.brand
  OR NEW.worker_id IS NOT OLD.worker_id OR NEW.materials IS NOT OLD.materials OR NEW.seconds IS NOT OLD.seconds OR NEW.property_id IS NOT OLD.property_id OR OLD.state <> 'working'
BEGIN SELECT RAISE(ABORT, 'a production order keeps what it made and a finished one never changes'); END;
CREATE TRIGGER IF NOT EXISTS trg_prod_nodelete BEFORE DELETE ON prod_orders BEGIN SELECT RAISE(ABORT, 'production history is kept'); END;
-- CUSTOMER ORDERS (store.py): a price quote becomes a purchase exactly once (the state moves only forward, guarded); `settled_by` says who completed it ('accountant', 'checkout', or the approver).
CREATE TABLE IF NOT EXISTS store_orders(
  id INTEGER PRIMARY KEY AUTOINCREMENT, branch_id INTEGER NOT NULL, buyer_id INTEGER NOT NULL, item_id TEXT NOT NULL, data TEXT NOT NULL DEFAULT '', qty INTEGER NOT NULL CHECK(qty > 0),
  unit_cents INTEGER NOT NULL CHECK(unit_cents > 0), total_cents INTEGER NOT NULL CHECK(total_cents > 0),
  state TEXT NOT NULL DEFAULT 'quote' CHECK(state IN ('quote','pending','done','declined','expired','cancelled')), settled_by TEXT NOT NULL DEFAULT '', created_at INTEGER NOT NULL, expires_at INTEGER NOT NULL, done_at INTEGER
);
CREATE INDEX IF NOT EXISTS ix_store_orders_branch ON store_orders(branch_id, state, id);
CREATE INDEX IF NOT EXISTS ix_store_orders_buyer ON store_orders(buyer_id, state);
CREATE TRIGGER IF NOT EXISTS trg_store_orders_fixed BEFORE UPDATE ON store_orders
WHEN NEW.id IS NOT OLD.id OR NEW.branch_id IS NOT OLD.branch_id OR NEW.buyer_id IS NOT OLD.buyer_id OR NEW.item_id IS NOT OLD.item_id OR NEW.data IS NOT OLD.data OR NEW.qty IS NOT OLD.qty
  OR NEW.unit_cents IS NOT OLD.unit_cents OR NEW.total_cents IS NOT OLD.total_cents OR OLD.state IN ('done','declined','expired','cancelled')
BEGIN SELECT RAISE(ABORT, 'an order keeps its terms and a finished order never changes'); END;
CREATE TRIGGER IF NOT EXISTS trg_store_orders_nodelete BEFORE DELETE ON store_orders BEGIN SELECT RAISE(ABORT, 'orders are kept'); END;
-- CONTRACTS (staff.py): an employee (a player, or NULL for a fixed-performance bot) of one branch in one role. The terms never change once written (a trigger): a different deal is a NEW contract the
-- employee has to accept. `accrual_from` is when the pay starts counting; `paid_fixed` / `paid_pct` are what has been paid so far.
CREATE TABLE IF NOT EXISTS contracts(
  id INTEGER PRIMARY KEY AUTOINCREMENT, branch_id INTEGER NOT NULL, employee_id INTEGER, role TEXT NOT NULL CHECK(role IN ('manager','accountant','warehouse','production','sales')),
  pay_type TEXT NOT NULL CHECK(pay_type IN ('fixed','percent')), pay_value INTEGER NOT NULL CHECK(pay_value >= 0), perms TEXT NOT NULL DEFAULT '',
  state TEXT NOT NULL DEFAULT 'offered' CHECK(state IN ('offered','active','ended','declined')), offered_by INTEGER NOT NULL, offered_at INTEGER NOT NULL, expires_at INTEGER,
  starts_at INTEGER, ended_at INTEGER, ended_by INTEGER, paid_fixed INTEGER NOT NULL DEFAULT 0 CHECK(paid_fixed >= 0), paid_pct INTEGER NOT NULL DEFAULT 0 CHECK(paid_pct >= 0)
);
CREATE INDEX IF NOT EXISTS ix_contracts_branch ON contracts(branch_id, state);
CREATE INDEX IF NOT EXISTS ix_contracts_emp ON contracts(employee_id, state);
CREATE UNIQUE INDEX IF NOT EXISTS uq_contract_player ON contracts(branch_id, employee_id, role) WHERE employee_id IS NOT NULL AND state IN ('offered','active');
CREATE UNIQUE INDEX IF NOT EXISTS uq_contract_bot ON contracts(branch_id, role) WHERE employee_id IS NULL AND state = 'active';
CREATE TRIGGER IF NOT EXISTS trg_contracts_fixed BEFORE UPDATE ON contracts
WHEN NEW.id IS NOT OLD.id OR NEW.branch_id IS NOT OLD.branch_id OR NEW.employee_id IS NOT OLD.employee_id OR NEW.role IS NOT OLD.role OR NEW.pay_type IS NOT OLD.pay_type OR NEW.pay_value IS NOT OLD.pay_value
  OR NEW.perms IS NOT OLD.perms OR NEW.offered_by IS NOT OLD.offered_by OR NEW.offered_at IS NOT OLD.offered_at OR OLD.state IN ('declined')
  OR (OLD.state = 'ended' AND (NEW.state IS NOT 'ended' OR NEW.ended_at IS NOT OLD.ended_at OR NEW.starts_at IS NOT OLD.starts_at))
  OR (OLD.state = 'active' AND NEW.state IN ('offered','declined'))
  OR (OLD.state = 'offered' AND NEW.state = 'ended')
  OR NEW.paid_fixed < OLD.paid_fixed OR NEW.paid_pct < OLD.paid_pct
BEGIN SELECT RAISE(ABORT, 'a contract keeps its terms'); END;
CREATE TRIGGER IF NOT EXISTS trg_contracts_nodelete BEFORE DELETE ON contracts BEGIN SELECT RAISE(ABORT, 'contracts are kept'); END;
-- REPUTATION (rep.py): the score of a branch and the append-only record of what gave it (unique per branch, kind and reference: the same sale cannot count twice).
CREATE TABLE IF NOT EXISTS branch_rep(branch_id INTEGER PRIMARY KEY, score INTEGER NOT NULL DEFAULT 0 CHECK(score >= 0), sales INTEGER NOT NULL DEFAULT 0 CHECK(sales >= 0), updated_at INTEGER NOT NULL) WITHOUT ROWID;
CREATE TABLE IF NOT EXISTS rep_log(
  id INTEGER PRIMARY KEY AUTOINCREMENT, branch_id INTEGER NOT NULL, kind TEXT NOT NULL, ref TEXT NOT NULL, delta INTEGER NOT NULL CHECK(delta > 0), who INTEGER, created_at INTEGER NOT NULL, UNIQUE(branch_id, kind, ref)
);
CREATE INDEX IF NOT EXISTS ix_rep_log_who ON rep_log(branch_id, who, created_at);
CREATE TRIGGER IF NOT EXISTS trg_rep_log_update BEFORE UPDATE ON rep_log BEGIN SELECT RAISE(ABORT, 'the reputation record is kept as it was'); END;
CREATE TRIGGER IF NOT EXISTS trg_rep_log_delete BEFORE DELETE ON rep_log BEGIN SELECT RAISE(ABORT, 'the reputation record is kept'); END;
-- SAVED OUTFITS (style.py): one per kind and player: {slot: [item id, data]} as it was worn when it was saved.
CREATE TABLE IF NOT EXISTS outfits(user_id INTEGER NOT NULL, kind TEXT NOT NULL, slots TEXT NOT NULL, saved_at INTEGER NOT NULL, PRIMARY KEY(user_id, kind)) WITHOUT ROWID;
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


def _migrate_12(conn) -> None:
    """v0.20 (identity document). Every existing character gets one `character_identity` row: the game-world default nationality and languages, an Active citizen status, a
    new fictional fingerprint ID and, when the character has a birth city, that city as the home city. A character whose profile is FINISHED is also issued its document: the
    issue date is its saved age anchor (`age_started_at`), the birthday is that anchor minus the starting age in game years (so the age that is worked out from the anchor does
    not change), the expiry follows the configured policy and the height, signature seed and document number are derived exactly as for a new character. Only new rows are
    written: no existing table, row, balance, ledger entry, city, trip, XP record or trait changes, and a character that already has a row is skipped (safe to repeat)."""
    from . import identity
    identity.backfill(_RawDb(conn))


# The three triggers of the gift tables, written again for the rebuild below (a test compares them with the ones a fresh database gets from SCHEMA, so the two copies cannot drift).
_GIFT_TRIGGERS_V13 = (
    """CREATE TRIGGER IF NOT EXISTS trg_gift_codes_update BEFORE UPDATE ON gift_codes
BEGIN
  SELECT RAISE(ABORT, 'gift code: only the use counter moves, and only up') WHERE NEW.code IS NOT OLD.code OR NEW.reward_type IS NOT OLD.reward_type OR NEW.amount IS NOT OLD.amount
                                                                              OR NEW.max_uses IS NOT OLD.max_uses OR NEW.created_by IS NOT OLD.created_by
                                                                              OR NEW.created_at IS NOT OLD.created_at OR NEW.item_id IS NOT OLD.item_id OR NEW.used_count < OLD.used_count;
END""",
    "CREATE TRIGGER IF NOT EXISTS trg_gift_codes_nodelete BEFORE DELETE ON gift_codes BEGIN SELECT RAISE(ABORT, 'gift codes are kept'); END",
    """CREATE TRIGGER IF NOT EXISTS trg_gift_redeem_insert BEFORE INSERT ON gift_redemptions
BEGIN
  SELECT RAISE(ABORT, 'gift redemption: unknown code') WHERE NOT EXISTS(SELECT 1 FROM gift_codes WHERE code = NEW.code AND reward_type = NEW.reward_type AND amount = NEW.amount);
END""",
)


def _migrate_13(conn) -> None:
    """v0.21 (inventory). The three inventory tables are new (from SCHEMA) and nobody is given any item. The one change to an existing table: `gift_codes` may now reward an ITEM
    (reward type 'item' plus an `item_id` column), and SQLite cannot widen a CHECK in place, so the table is REBUILT: a new table with the new definition, every old row copied
    exactly as it was in the order it was made (code, reward, amount, limit, use counter, maker, date), the old table dropped, the new one renamed, and the three triggers that
    mention it created again. `gift_redemptions` is not touched. It is all one transaction (the caller's): either every code is still there afterwards or nothing changed. A table
    that already has the new definition (a database created by this version) is left alone, so repeating it changes nothing."""
    row = conn.execute("SELECT sql FROM sqlite_master WHERE type='table' AND name='gift_codes'").fetchone()
    if row is None or "'item'" in row[0]:
        return
    before = conn.execute("SELECT COUNT(*) FROM gift_codes").fetchone()[0]
    for trg in ("trg_gift_codes_update", "trg_gift_codes_nodelete", "trg_gift_redeem_insert"):
        conn.execute(f"DROP TRIGGER IF EXISTS {trg}")
    conn.execute("""CREATE TABLE gift_codes_v13(
  code TEXT PRIMARY KEY CHECK(length(code) >= 8),
  reward_type TEXT NOT NULL CHECK(reward_type IN ('stock_money','life_cash','char_xp','item')),
  amount INTEGER NOT NULL CHECK(amount > 0), max_uses INTEGER NOT NULL CHECK(max_uses > 0),
  used_count INTEGER NOT NULL DEFAULT 0 CHECK(used_count >= 0 AND used_count <= max_uses),
  created_by INTEGER NOT NULL, created_at INTEGER NOT NULL,
  item_id TEXT,
  CHECK((reward_type = 'item') = (item_id IS NOT NULL))
)""")
    conn.execute("INSERT INTO gift_codes_v13(code,reward_type,amount,max_uses,used_count,created_by,created_at) "
                 "SELECT code,reward_type,amount,max_uses,used_count,created_by,created_at FROM gift_codes ORDER BY rowid")
    if conn.execute("SELECT COUNT(*) FROM gift_codes_v13").fetchone()[0] != before:
        raise RuntimeError("gift code rebuild lost rows")                                 # the caller rolls everything back
    conn.execute("DROP TABLE gift_codes")
    conn.execute("ALTER TABLE gift_codes_v13 RENAME TO gift_codes")
    for sql in _GIFT_TRIGGERS_V13:
        conn.execute(sql)


def _migrate_14(conn) -> None:
    """v0.211. The starter items of v0.21 leave the catalog (items.py registers none). NOTHING a player owns is deleted or changed: every item id that any stack, worn item, ledger row or gift
    code still names becomes a `legacy` row of `item_templates` (its v0.21 definition, from legacy_items.py), so it keeps its name, category and slot and stays wearable. Ids nobody
    references get no row. A short report (how many stacks by acquisition source, which legacy items are in use) is written to `kv` (key `migration14`) for the owner to read: stacks whose source is
    not `gift` cannot be told apart from a legitimate grant, so they are LEFT ALONE and only counted. Safe to repeat (INSERT OR IGNORE on the unique item id)."""
    import json
    import time
    from .legacy_items import LEGACY
    have = {r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    refs: dict = {}
    for table, col in (("item_inventory", "item_id"), ("item_equipment", "item_id"), ("item_log", "item_id"), ("gift_codes", "item_id")):
        if table in have:
            for (iid,) in conn.execute(f"SELECT DISTINCT {col} FROM {table} WHERE {col} IS NOT NULL"):
                refs[iid] = refs.get(iid, 0) + 1
    now = int(time.time())
    kept = []
    for iid, fa, en, cat, slot, stack, icon, dfa, den in LEGACY:
        if iid in refs:
            conn.execute("INSERT OR IGNORE INTO item_templates(item_id,kind,name_fa,name_en,category,description_fa,description_en,slot,max_stack,icon,has_quality,brandable,created_by,created_at) "
                         "VALUES(?,?,?,?,?,?,?,?,?,?,0,0,NULL,?)", (iid, "legacy", fa, en, cat, dfa, den, slot, stack, icon, now))
            kept.append(iid)
    by_source = {}
    if "item_inventory" in have:
        for src, n in conn.execute("SELECT source, COUNT(*) FROM item_inventory GROUP BY source"):
            by_source[src] = n
    report = {"legacy_items_kept": sorted(kept), "stacks_by_source": by_source, "unknown_ids": sorted(i for i in refs if i not in {r[0] for r in LEGACY})}
    conn.execute("INSERT OR REPLACE INTO kv(key,value) VALUES('migration14',?)", (json.dumps(report, sort_keys=True),))


SCHEMA_VERSION = 14
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
    12: _migrate_12,                       # v0.20 (identity document): one `character_identity` row per existing character (the table itself comes from SCHEMA); see _migrate_12
    13: _migrate_13,                       # v0.21 (inventory): the item tables come from SCHEMA; `gift_codes` is rebuilt (copied row for row) to allow item rewards; see _migrate_13
    14: _migrate_14,                       # v0.211: the seeded items leave the catalog; items still referenced become `legacy` templates (nothing deleted); every new table comes from SCHEMA
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

    @contextmanager
    def savepoint(self):
        """An atomic block that can fail on its own (v0.21). Outside any transaction it is an ordinary `tx()`. Inside one it is a SAVEPOINT: if the block raises, only ITS writes are
        undone and the exception goes on; the outer transaction is not touched (its own caller decides whether to go on or to roll back everything)."""
        if not self._depth:
            with self.tx():
                yield self
            return
        self._sp = getattr(self, "_sp", 0) + 1
        name = f"hm_sp{self._sp}"
        self.conn.execute(f"SAVEPOINT {name}")
        try:
            yield self
        except BaseException:
            self.conn.execute(f"ROLLBACK TO {name}")
            self.conn.execute(f"RELEASE {name}")
            raise
        else:
            self.conn.execute(f"RELEASE {name}")
        finally:
            self._sp -= 1

    def close(self) -> None:
        self.conn.close()
