# بورس رفقا / Hype Market — Telegram group stock-market game

Players launch **stocks of themselves, memes or projects**; friends invest in them. Prices follow a bonding curve, the
whole market lives inside Telegram groups, and the progression never ends (levels → skills → rebirth → legacy perks → seasons).
Persian-first, English included. Commands are plain words: `خرید 10 SPEED`, `بازار`, `روزانه` … (no `/` needed).

## 1. Run it (10 minutes)

1. In Telegram open **@BotFather** → `/newbot` → copy the token.
2. **Important:** BotFather → `/mybots` → your bot → *Bot Settings* → *Group Privacy* → **Turn off**.
   Without this the bot only sees `/commands`, so plain-word commands and chat-activity income won't work in groups.
   (The bot only counts messages; it never stores their text.)
3. Install Python 3.10+ and run:

```bash
cd hypemarket_bot
python3 -m venv .venv && source .venv/bin/activate      # Windows: .venv\Scripts\activate
pip install -r requirements.txt
cp .env.example .env                                     # then edit .env: BOT_TOKEN=...  ADMIN_IDS=your_numeric_id
python run.py
```

Add the bot to a group, type `روزانه`. Run the tests any time with `pip install pytest && python -m pytest tests -q`.

**Keep it running 24/7:** a small VPS *outside Iran* works well (Hetzner/DigitalOcean, ~$5). Example systemd unit:

```ini
[Service]
WorkingDirectory=/home/you/hypemarket_bot
ExecStart=/home/you/hypemarket_bot/.venv/bin/python run.py
Restart=always
```

The whole game is one SQLite file (`hypemarket.db`) — **back it up** (copy it daily).

## 2. What's inside

> **Since v0.14 every stock command starts with `سهام` (English: `stocks`)**, e.g. `سهام خرید 10 SPEED`, `سهام سطح`, `سهام بازار`. The names in the table below are the commands' own names; see section 11 for the exact forms.

| Area | What it does |
|---|---|
| Exchange | `بورس` — six **house-priced** companies (CHAT, RBNK, MEME, LEVI, STRK, TRST) that nobody can pump or dump · weekly earnings night (`گزارش مالی`) · see section 10 |
| Stocks | `عرضه` personal stock · `عرضه میم` meme · `عرضه پروژه` crowdfunded project (goal bar + reward) · blue-chip (legacy unlock) |
| Pricing | Linear bonding curve: reserve always equals the integral of the price curve, so buying/selling is symmetric and the market can never become insolvent |
| Passive income | owner share of every trade fee · dividends (owner sets `تنظیم سود`) · hourly holder bonus (`درآمد`) · chat activity pays the group's index and the author's stock · company tax |
| Risk & drama | rug pull (`خروج`, 6h minimum age, −40 reputation, tax) · hype pumps · crashes/booms · bubbles & corrections · meme mania · fee-free hours |
| Social | every group gets an index stock · group ranking · leaderboards · companies (shared treasury, daily-bonus synergy) · invites with rewards · bonus for adding the bot to a group |
| Progression | net-worth levels (no cap) · skill tree · reputation · rebirth with Legacy points & permanent perks · 30-day seasons with prizes |
| Live market | automated 🤖 market traders keep prices moving · 🐋 whale headlines (and DMs to holders) · detailed candlestick charts with volume, moving average, whale markers and a full stats caption |
| Safety | **verified backups sent to every admin every 24 hours** · scam reports (`گزارش`) and trust marks (`اعتماد` / `بی اعتماد`) with a public 🚩 warning and admin Delist / Ban / Dismiss buttons · `/ban` `/unban` `/delist` · players see a 🤝 trust label on every stock |
| Sharing | `کارت` makes a picture card (portfolio, rank or a stock) with your invite link and a Telegram "send to friends" button; a friend who opens a shared stock card lands on that stock and counts as your invite |
| Daily salary | every morning a private message with your wealth summary and a **claim salary** button (`حقوق` turns it on/off) |
| Progress | 26 achievements (`دستاوردها`) with rewards · admin analytics dashboard (`/stats`) and a daily report |
| City | `مپ` — the group's own persistent city map (v0.15) · `مپ P14`, `مپ L1`, `مپ D2` inspect one parcel, public place or district and mark it on the map (v0.16) · **v0.17: the map has buttons** (you, the people near you, places, empty lots, 🚶 walking along the roads), **every city has a casino** (v0.18.1: blackjack for cash, see section 17), and the buttons only obey the player who sent `مپ` · nothing can be bought or built yet · sections 12, 14 and 15 |
| Life cash & jobs | **v0.18:** a second balance, `پول نقد` ($, $100 at the start), next to stock money · `صرافی` turns stock money into cash, **one way only** (v0.18.2), at a fixed 1,000 🪙 = $5 · `مشاغل`: Snapp rides, McDonald's shifts, City Hall errands, a compact text list (v0.18.2: only route maps are pictures) · greetings and hugs are buttons under `📍 اطراف من` · see sections 16, 17 and 18 |
| Guidance | `راهنما` is a menu: starter guide (a checklist that ticks itself off), all commands, and how the game works · quests: a starter checklist, 3 daily and 3 weekly quests with automatic rewards |
| Telegram | inline buttons everywhere · price alerts by DM · charts · Stars shop (badges, season pass, extra slot, spotlight) · live market board per group (one message the bot keeps editing) |

Two languages live in `hypemarket/texts.py` (add a language by copying `EN`). Every number of the economy is in `hypemarket/config.py`.

## 3. Updating the bot

Your code and your players' data are separate: the data is the `hypemarket.db` file, which updates never touch.

```bash
# on your computer: change code, run the tests, publish
python -m pytest tests -q
git add -A && git commit -m "what changed" && git push

# on the machine that runs the bot
git pull
pip install -r requirements.txt          # only needed when requirements changed
sudo systemctl restart hypemarket        # or stop and re-run: python run.py
```

When a version changes the database layout, the bot **upgrades your existing database automatically on start** (it keeps all data;
this version added three columns and the quests table). Still, copy `hypemarket.db` before updating, and never commit it:
the included `.gitignore` already excludes `.env` (your token), `*.db` and `backups/`. If your token was ever pushed to a public repo,
revoke it in @BotFather (`/revoke`) and put the new one in `.env`.
Schema changes are applied automatically on start and keep all data; older databases are upgraded step by step (v0.13 = schema 5: short selling is removed, every open margin is refunded to its owner's cash, the 3 skill points of the old skill are returned, and each affected player gets one private message; v0.15 = schema 6: only adds the empty `worlds` table, nothing else is read or written; v0.17 = schema 7: only adds five new map tables, `world_places`, `world_players`, `world_trips`, `map_sessions` and `casino_games`, no existing table or row is touched; v0.18 = schema 8: life cash, jobs, interactions; v0.18.1 = schema 9: three columns on `casino_games`, see section 17).
`راهنما` ← `همه‌ی دستورها` is generated from `hypemarket/docs.py`: add a command there (the tests fail if one is missing or an example doesn't parse).

## 4. Backups (read this once)

* **Once every 24 hours** (`BACKUP_EVERY_HOURS`, default 24) the bot takes a consistent copy of the whole database, **checks it** (integrity check + counts),
  compresses it and sends it **silently** to every account in `ADMIN_IDS`, with a caption (time, players, stocks, checksum). It also keeps the
  newest `BACKUP_KEEP` (default 30) copies in `BACKUP_DIR` (`backups/`). The old `BACKUP_EVERY_MIN` setting is no longer read, so an old `.env` that still says 60 cannot bring the hourly backups back.
* **The schedule survives restarts.** The time of the last delivered backup is stored in the database: restarting the bot never skips a due backup and never sends an extra one. A brand-new
  install sends its first backup a few seconds after start; a bot that was off for days sends one right after it comes back. If delivery fails (an admin never pressed `/start`), it is retried an hour later, not every few minutes.
* **Every admin must open the bot in a private chat and press `/start` once**, otherwise Telegram will not let the bot message them
  (the bot logs a warning at startup if it can't reach someone). If a backup fails or can't be made, the admins get an alert message.
* `/backup` (admins only) sends one immediately; so does the panel's **💾 Backups → 📤 Send one now**. Neither moves the 24-hour schedule.
* **Restore:** stop the bot, then `python restore.py hypemarket-YYYYMMDD-HHMMSS-UTC.db.gz` (it checks the file, keeps your current database as
  `hypemarket.db.before-restore-<time>`, installs the backup) and start the bot again. Files over 45 MB are sent in numbered parts:
  `python restore.py file.part1 file.part2 ...`.
* Backups contain your players' Telegram ids and names: keep the admin chats private. Also keep the database on a disk that survives restarts
  (with Docker: a mounted volume), and download one of the Telegram backups to your own computer now and then.

## 5. Share cards and the daily salary

* **Share cards** (`کارت`, `کارت پورتفوی`, `کارت رتبه`, `کارت SPEED`, or the 📤 button on portfolio/profile/stock cards): a 1080×1350 picture in Persian or English
  with the player's name (Persian shaped correctly with the bundled Vazirmatn font, see `hypemarket/assets/OFL.txt`), numbers, a mini chart (stocks) or achievement badges (rank),
  plus a caption with the invite link. A friend who taps a shared **stock** link gets the welcome and the stock card, and counts as the sharer's invite (start bonus right away).
* **Daily salary DM:** from `SALARY_HOUR_UTC` (default 05:00 UTC = 08:30 Tehran) for 6 hours, each player seen in the last 14 days who has the bot open privately gets one message:
  net worth and change since yesterday, global rank, streak, pending income, best stock, quest progress, and buttons to claim the daily bonus / collect income.
  It pauses after 3 unclaimed salaries, never retries players who blocked the bot (they come back when they write to it), and players can switch it off with `حقوق`.
  Sending is spread out (about 20 messages per second). Turn the whole feature off with `SALARY_DM=0`.

## 6. Reports, trust and moderation

* **Trust marks** are about people: `اعتماد SPEED` / `بی اعتماد SPEED` (or reply to a message with `اعتماد` / `بی اعتماد`), or the 👍/👎 buttons on a stock.
  One vote per person (vote again the same way to remove it). A label (✅ trusted / ⚠️ not trusted) needs at least 3 different voters; higher-level players' votes weigh more (1–3).
  `اعتماد` alone shows the trust board.
* **Scam reports** are about stocks: `گزارش SPEED` (then pick a reason) or the 🚩 button. Reporting/voting needs at least 3 trades and has daily limits.
  At report weight 3 the stock shows a public 🚩 warning (and buyers get a warning); at weights 3, 6 and 10 every admin gets a message with
  **🗑 Delist / 🚫 Ban owner / ✅ Dismiss** buttons. Nothing is removed automatically, so brigading can't kill a stock. Delisting pays holders from the stock's reserve,
  lowers the owner's reputation and thanks the reporters (they earn the "Scam hunter" achievement). Automated market traders avoid reported and distrusted stocks.
* Admin commands: `/ban <id>` (also delists their stocks), `/unban <id>`, `/delist <TICKER>`. Banned players are ignored completely.

## 7. Analytics (admins only)

`/stats` shows players (new, daily/weekly/monthly active, stickiness), **retention** (day 1 and day 7), the **starter funnel** (where new players stop: daily → first buy → launch → first investor → income → level 1),
the economy (coins minted/burned today and yesterday), market activity, engagement and moderation. The same dashboard is sent to every admin once a day at `REPORT_HOUR_UTC`.
Only aggregated numbers are collected; the one per-player record is a "seen today" mark used for activity and retention.

## 8. Operating it

* Owner commands (your id in `ADMIN_IDS`): `/admin` (the settings panel, section 13), `/stats` (users, groups, coins minted/burned), `/give <id> <coins>`, `/fire [pump|crash|boom|special|holiday|mania]`.
* Group admins: `تنظیمات` (language, news mode, message cap), `حالت سکوت` (pinned board), `محدودیت پیام N`.
* Watch `minted` vs `burned` in `/stats`. If minted grows far faster than burned, lower `DAILY_BASE`, `ACT_UNITS`, or raise `TRADE_FEE_BPS`.
* `.env` switches: `ALLOW_RUG=0` removes rug pulls, `SHOP_ENABLED=0` closes the shop, `RANDOM_EVENTS=0` stops random events,
  `MARKET_BOTS=0` switches the automated traders off, `PROXY_URL=socks5://127.0.0.1:1080` routes Telegram through a proxy where it is blocked.
* **Automated market traders** (6 accounts named 🤖 Atlas, Nova, Orion, Hedge, Bull, Bear): they trade small amounts every ~30 s through the normal
  buy/sell code, so the economy stays solvent. They pay the normal fee but nobody earns it, only touch stocks that are >= 30 min old with >= 2 independent
  real investors, have capped position sizes and a capped daily cash top-up (visible as `minted` in `/stats`), and never appear in rankings, seasons, quests or whale news.
  The game's "how it works" screen tells players they exist.
* **Whales:** a single trade of at least 2,000 coins *and* at least 10% of the stock's reserve (one headline per stock per 30 minutes). Tune in `config.py` (`WHALE_*`).
* **Quests:** rewards are minted coins, so watch `minted` vs `burned`; targets and rewards scale with level (`quests.py`).

## 9. Things you should know (honest limits)

* **Virtual coins only.** Keep it that way. Selling coins for money or paying out cash prizes turns this into gambling/securities
  territory with legal and Telegram-policy risk. The shop sells only cosmetics and small perks.
* **Stars:** Telegram Stars may be hard to buy for users in some countries (including Iran), so don't plan revenue around the shop
  until you've checked it with real users. Everything else works without it.
* **Alt accounts:** new accounts get starting cash, so people can farm with extra accounts. Mitigations built in: sell lock,
  trade cooldown, daily activity caps, referral rewards only after 3 real trades, and the exchange companies are priced by the house so nobody can move them with alts. For a big launch, add
  account-age rules or a captcha-like gate in `ensure_user`.
* **Scale:** SQLite + one process comfortably handles thousands of players. If you outgrow it, the database layer is isolated in `db.py`.
* **Tested vs. not tested:** the game rules, Persian parsing, buttons, payments, group flows and background jobs are covered by about 950 automated tests,
  including a randomized simulation (traders, whales, quests, rug pulls, exchange trades and earnings nights, rebirths all at once) that checks every coin is accounted for, and a set of simulated *farmers* that attack the exchange (section 10). These run against a *simulated* Telegram. I could not run
  against real Telegram servers, so do a 15-minute manual test with two accounts and a test group before announcing it.

## 10. The Rofaqa Exchange (v0.13) — companies nobody can pump or dump

**Why it exists.** Normal stocks have a bonding curve: whoever buys moves the price, so with alt accounts people could pump one stock and cash out with another
(this is how the old *short* skill printed money, so it was removed). The exchange is the opposite: six companies whose price is set by **the house**.
Trades never move it, there are **no limits** on how much anybody can buy or sell, and nobody can print coins with alts.

| Ticker | Company | Its price listens to |
|---|---|---|
| `CHAT` | Chatterbox | chat messages in the groups |
| `RBNK` | Rofaqa Bank | dividend and income payouts |
| `MEME` | Meme Mining Co | trades of meme stocks |
| `LEVI` | Leviathan Capital | trading volume of the normal market |
| `STRK` | Streak Energy | daily-bonus claims |
| `TRST` | Trust Shield | votes, scam reports and admin actions |

Commands: `بورس` (the six companies), `خرید 5 CHAT`, `فروش همه CHAT`, `قیمت CHAT`, `نمودار CHAT`, `گزارش مالی` (the last earnings night). Everything also works from buttons.

**How a price moves (all in `exchange.py`, numbers in `config.py` under `EX_*` / `LISTED_*`):**

* Every minute: `price = A × e^heat`. `A` is a hidden random walk (zero-mean, so the price is a *martingale*: the past says nothing about the future,
  the randomness comes from the operating system and can't be reproduced). `heat` is a small push (at most ±0.8%) from what *really* happened in the game,
  but only the **surprise** counts: activity is compared with what the model expected (learned normal level, correlation removed, the average of each half hour
  of the day cancelled), so daily rhythms and slowly growing communities give nothing to trade on.
* Every Thursday 17:00 UTC (20:30 Tehran) is **earnings night**: a random, zero-mean jump (about ±2.5%, plus at most 0.3% from the week's activity),
  a chart and a report with real numbers posted to every group that follows the market, with a short statement of the company itself commenting on the numbers (v0.18.1: no made-up people; the voice is «بیانیه‌ی <company>»). Moves are announced *after* they happen.
* Prices stay between 10% and 10× of their listing price.

**What trading costs (house rules, nobody is exempt):** a fixed levy of 1.5% on every buy and every sell, plus slippage that grows with how much *all players together* traded in the
last minutes (up to +3%, fades with a 10-minute half-life, so splitting an order over alts doesn't help). 30% of every levy goes to the *other* holders of that company as
a dividend (a seller never shares in the levy of his own sale), the rest is burned. Perks, fee-free hours and the fee skill don't change it. Trades in the exchange never feed the
counters the exchange itself reads, so there is no feedback loop to farm.

**Why it can't be farmed.** The most anybody can predict or steer is the heat swing (±0.8%, so 1.6% from lowest to highest) plus the activity part of one earnings night (0.3%).
That is smaller than the *cheapest conceivable* round trip (about 2.1% after the most a lone holder can get back of his own levies), so every strategy loses on average.
`tests/test_exchange.py` proves this by attacking it with simulated farmers: trend followers, mean reverters, time-of-day traders, an insider who knows the algorithm *and* steers the activity with
a swarm of accounts, buy-before-earnings holders, a lone holder slicing orders, alt-account wash trading, big pump-and-dump orders — all must lose after costs, and the tests fail if anybody
weakens `EX_YCAP`, `EX_EARN_BETA`, `LISTED_HOLDER_SHARE_BPS` or the noise in a way that would let a strategy win.

**House economics.** The house is the counterparty: when a price rises it *mints* the difference for the holders, when it falls it *burns* it. Because prices are a martingale this nets to about zero over time,
and the levies are burned, so the exchange removes more coins than it creates on average. `/stats` shows the exchange: holders, market value, today's trades and volume, levies (and the holders' share),
coins the house minted/burned for price moves today, and the house result since launch. Watch that last number for a few weeks; if it drifts far from zero, raise `LISTED_SPREAD_BPS`.

## 11. Sections, the `سهام` namespace, `منو` and the help hub (v0.14)

**Why.** The game is growing beyond the stock market. So the stock market is now *one section* of the game with its own commands; global words (like `سطح`) stay free for future systems.
`hypemarket/sections.py` is the single table that says which command belongs where; a future section is one more entry there.

| Class | Commands | Typed as |
|---|---|---|
| **Stock section** | buy, sell, price, chart, market, exchange, earnings, portfolio, alerts, launch / meme / project, own stock, edit, dividend %, cash out, dividends, income, companies, groups' index and ranks, leaderboards, share cards, level, reputation, skills, upgrade, legacy, rebirth, quests, achievements, season, news, events, whales, trust, distrust, report | `سهام <command> <arguments>` (English `stocks <command> ...`) |
| **Global** | `شروع`, `راهنما`, `منو`, `پروفایل`, `زبان`, `دعوت`, `روزانه`, `حقوق`, `فروشگاه`, `تنظیمات`, `تنظیم زبان`, `محدودیت پیام`, `حالت سکوت`, `مپ` (v0.15, see sections 12, 14 and 15) | unchanged |
| **Bot admins** | `/stats /backup /ban /unban /delist /give /fire` | unchanged (same authorization checks) |

* `سهام` alone opens the stock menu. `سهام راهنما` opens stock help. `سهام من` (your stock), `سهام ویرایش ...` (edit) are the in-section spellings of those two.
* The prefix only counts as the **first word** of a message. In groups, `سهام ...` has to look like a real command (valid ticker, valid arguments), exactly like plain commands always had to: chat such as «سهام خرید کنم یا نه؟» is ignored.
* Unknown words after `سهام` run nothing (private chat: a one-line hint). Global and admin commands can't be reached through the prefix.
* Arguments, tickers, reply-to-message behavior, validation, permissions and side effects are exactly the old ones: the prefix only changes how the command is *found* (`textparse.py`), then the ordinary handler runs.
* Persian normalization (Arabic letters, Persian digits, half-spaces) works inside the namespace; `سهام مهارت‌ها`, `سهام مهارت ها` and `stocks skills` are the same command.

**Old spellings.** *Still working* (specific enough that no future global command can want them): `بازار`, `بورس`, `گزارش مالی`, `پورتفوی`, `خرید / فروش / قیمت / نمودار` **only when they contain a ticker** (`خرید 10 SPEED`),
`عرضه SPEED ...`, `عرضه میم`, `عرضه پروژه`, `ویرایش سهام`, `تنظیم سود`, `mystock`. *Retired* (answer with one line pointing to the new form, execute **nothing**): everything else, for example `سطح`, `مهارت‌ها`, `ارتقا`, `میراث`, `تولد دوباره`, `ماموریت`,
`دستاوردها`, `فصل`, `اخبار`, `رویداد`, `نهنگ`, `سود`, `درآمد`, `خروج`, `هشدار`, `اعتبار`, `رتبه`, `برترین‌ها`, `گروه`, `شاخص`, `کارت`, `اعتماد`, `گزارش`, all company words, and a bare `خرید` / `فروش` / `قیمت` / `نمودار` / `عرضه`.
Why: these are generic words ("level", "income", "buy") that a future life-game command will want. There is **no universal redirect**: each retired word only ever answers with its own pointer. To retire or keep another word, edit `LEGACY_KEEP` in `sections.py` (the first candidates when the life game arrives: `بازار`, `پورتفوی`, `عرضه`).

**Menus.** `منو` → *سهام* · *راهنما* · پروفایل · جایزه روزانه · دعوت دوستان. `راهنما` → *سهام* (starter guide, all commands) · *درباره بازی* (how the game works) · back to the menu.
Every command example shown by the bot is the canonical form, and `tests/test_namespace.py` checks that each one parses.

**The stock menu is made of buttons.** `سهام` (or `منو` → سهام) opens a menu where **every stock command is one tap away**: four shortcut rows (market, exchange, portfolio, profile, my stock, level, skills, quests) and eight
category buttons: 💱 trading · 🚀 your stock (launch, meme, project, edit, dividend %, cash out) · 💰 income · 🏢 company · 👥 groups and ranks · ⭐ progress · 📰 live market · 🛡 safety. A category lists its commands as buttons (two per row, 🔙 at the bottom).
The layout is one table in `sections.py` (`STOCK_QUICK`, `STOCK_CATEGORIES`); a test fails if a stock command has no button or two. A button does one of three things:

* **Screens** (market, level, skills, quests, ...): runs the *ordinary* command handler with no arguments and edits the message in place, plus a 🔙 button back to where you pressed it. Same logic and permission checks as typing the command. `ارتقا` opens the skills screen (its upgrade buttons live there).
* **Commands that need typing** (buy, sell, price, chart, alert, launch / meme / project, edit, dividend %, create / join company, trust, distrust, report): the button explains the format with an example. In a **private chat** the player's *next* message is taken as the arguments
  (`10 SPEED` after 🛒 buy runs `سهام خرید 10 SPEED`); the prompt waits 5 minutes (`GUIDED_TTL`), is dropped by ❌ cancel, by any other button or by any real command, and belongs to that one player. In a **group** nothing is armed: the button just shows the exact command to type,
  so group chatter is never swallowed. The prompt is kept in memory only (no database change; a bot restart simply forgets it).
* **Commands that move coins or leave something** (`ترک شرکت`, `تقسیم سود شرکت`): the button asks "are you sure?" with ✅ / ❌ first.

Callbacks: `mn:` (main menu), `sm:` (stock menu: `sm:cat`, `sm:go`, `sm:ask`, `sm:sure`, `sm:do`, `sm:cancel`), `hp:` (help). All of them end with the player's id and only react to the player who opened them; a hand-made `sm:` button can reach nothing outside the menu's whitelist
(`sections.MENU_SCREENS` / `ASK_CMDS` / `SURE_CMDS`), so it can't run admin commands or anything unlisted. Tests: `tests/test_stock_menu.py`.

**No data was touched.** No database migration (schema stays 5); stock levels, skills, rebirths, legacy points, seasons, quests, balances and prices are not read or written by the routing layer.

**Bug fix: launching again after a rebirth.** A rebirth delists your stocks, but a delisted stock kept its ticker forever, so `عرضه SPEED` answered "ticker taken". Now the owner can launch the ticker of their *own* delisted stock again
(the old record is kept under an archive name like `SPEED~12`); other players still can't take it, and active tickers and the exchange's tickers stay protected.

## 12. World Engine v1 — one city per group and the `مپ` command (v0.15)

**Why.** The next part of the game is a modern-day life simulator played *inside each group*. This version lays only the foundation: every Telegram group (or supergroup) owns exactly one
persistent, procedurally generated city, and players can look at it. Nothing in it can be bought, built or driven yet — that is deliberately left for later versions, and the map's caption says so.

| What | How |
|---|---|
| **`مپ`** (also `نقشه`, `map`) | A global command (not part of `سهام`). In a group it sends the group's city as a PNG with a caption: city name, "your group's city", that this is the starting map, the district legend and how many lots are empty. In a private chat it explains that maps belong to groups; no world is created. The word must stand alone, or be followed by an ID (section 14): `مپ کجاست؟` or `مپ 12` is ordinary chat and is ignored, like every other plain-word command in groups. |
| **`منو` → 🗺 نقشه شهر** | A button on the main menu. It runs the *same* `map` handler as the typed command (same world, same checks, same picture) and, like the other `mn:` buttons, only reacts to the player who opened the menu. In a private chat it shows the explanation with a way back. |
| **One world per group** | `worlds` table, `UNIQUE(chat_id)`: the key is the Telegram chat id, never the title or the player who typed first. The first `مپ` generates the city **outside** any database transaction (about 0.3 s, in a worker thread, so the bot keeps answering everybody else), then saves it with `INSERT OR IGNORE` and re-reads the row: two players racing each other get the same saved world and the city is built once per group, never once per request. Nothing is generated at start-up or by a background job; a group that never asks never gets a city. |
| **The whole layout is saved** | Not just the seed: `hypemarket/world.py` stores seed, generator version, name (Persian + English), size and the complete JSON layout (roads as polylines with junctions, neighbourhood outlines, blocks, lots, landmarks, river, terrain lines — each with a stable id such as `r12`, `p87`, `d3`). A future generator can change freely without silently redrawing cities people already know. A damaged row is reported (`⚠️ نقشه‌ی ذخیره‌شده … خراب شده`) and **never** regenerated or overwritten. |
| **The city** | **Not a grid.** `hypemarket/worldgeom.py` builds neighbourhoods as curved Voronoi-style cells, each with its own street pattern and density (open suburbs, dense centres), connects them with winding main roads (shortest paths with a turn penalty, so they bend gently instead of zig-zagging) and a river with a few bridges, builds one exact planar road network from all of it, then bends the whole thing with a smooth bounded warp and cuts lots from the blocks it leaves. Every layout is **validated** before it is saved (connected roads, no street ending in the middle of nowhere, lots clear of roads and water, no overlapping lots, every lot within reach of a road, four public places — city hall, hospital, school, station —, at least a quarter of the lots empty for the future, JSON round-trip). About 90–170 lots per city. Generation uses its own `random.Random(...)`: the market's randomness is untouched and the same seed always gives the same city. At most 6 attempts, then a friendly error. |
| **The picture** | `hypemarket/worldmap.py` draws the saved layout with Pillow in memory (about 1250×1090 px, 200–400 KB) in a deliberately quiet style: near-black background `#080B09`, land `#101812`, streets `#243B2A`, main roads `#45654A`, muted grey-green labels; no gradients, no glow, no icons, buildings as flat footprints, empty lots as dashed outlines, a dark river, faint terrain lines only in the countryside. Labels are few (city, five district names, four public places) and never sit on a main road or in the water. The title and legend follow the group's language (Persian is shaped and right-to-left, like the share cards). Drawing runs in a worker thread and each (city, language) picture is cached in memory (bounded); the cache is never the source of truth. If the bundled font is missing Pillow's default font is used. |

Zero stock impact: no stock command, price, balance, level, rebirth, legacy, season, quest or background job is touched; `مپ` moves no money. Tests: `tests/test_world.py` (generator validity across seeds, determinism, global random state, "not a grid" and "no dead ends" measurements, overlap/connectivity checks, the validator against broken layouts, palette discipline of the PNG, language and font fallback, cache, the event loop staying responsive, persistence across restart, group isolation, racing threads and racing requests, migration from schema 5, the command and the menu button, Telegram, renderer and generator failures, corrupt rows).

## 13. The admin panel and the required channel (v0.15)

**`/admin`** opens a settings panel with buttons. It is for the accounts in `ADMIN_IDS` (your `.env`) **only**: for anybody else `/admin` is silently ignored (like `/stats`), every button re-checks `ADMIN_IDS` when it is pressed (a copied or forged button does nothing, and only the player the keyboard was made for can press it), and it only opens in a private chat with the bot.

| Screen | What you can do |
|---|---|
| **📢 Required channel** | **✏️ Set channel** — send `@yourchannel`, `t.me/yourchannel` or (private channel) its numeric id such as `-1001234567890`. The bot looks the channel up, checks that **it is an administrator there** (Telegram only lets an administrator list a channel's members) and turns the lock on. **🔛/⏸** switch the lock, **🔍 Check access** re-tests it, **🗑 Remove** forgets the channel (asks first). The setting lives in the database, so no restart and no `.env` edit. |
| **💾 Backups** | When the next 24-hour backup is due, when the last one went out, **📤 Send one now**. |

**What players see.** While the lock is on, a player (anybody who is not in `ADMIN_IDS`) who has not joined the channel gets **no answer from the bot except a message in their language with a 📢 join button and a ✅ "I joined" button**, in private chats, in groups (one notice per player per 90 seconds, so a group is never spammed) and on every button. "I joined" asks Telegram again and, when it is true, opens the menu. `/start` still registers a new player (so the invite link that brought them keeps counting); a blocked player's other messages are not even recorded.

**Safe by design.** Membership answers are cached briefly (10 minutes for "member", 10 seconds for "not a member"), so an active player costs about one Telegram call per ten minutes. Admins are never blocked. If Telegram cannot answer (the bot was removed from the channel, an outage…) the players are **let through** instead of locking the whole game, and the admins get one alert an hour saying so; a player Telegram says is not a member is always blocked. Ordinary chat in a group is not a command and never triggers the gate, and chat-activity income is not gated; payments are always processed. Changing or removing the channel forgets every remembered answer.

**Setup in 3 steps:** 1. add the bot to your channel as an **administrator**; 2. open the bot in a private chat, send `/admin` → 📢 Required channel → ✏️ Set channel; 3. send the channel name. Tests: `tests/test_admin_gate.py` (the 24-hour schedule across restarts, retries, clock errors; every way a regular player could try to reach the panel; the guided prompt, errors and expiry; each membership status; caching; groups; failures of Telegram; HTML safety of channel titles).


## 14. World Interaction v1 — inspect any location (v0.16)

**Why.** The city should be explorable before it is purchasable. This version only lets players *look* at one place in their group's city: no buying, no building, no prices, no owners, no income, no jobs. Nothing is written anywhere when someone inspects.

| What | How |
|---|---|
| **`مپ`** | Unchanged: the group's city map, **byte-for-byte the same picture** as in v0.15 (checked against the v0.15 renderer). The only addition is two lines under the caption: how to look closer and the four public places with their IDs (`شهرداری L1 · بیمارستان L2 · مدرسه L3 · ایستگاه L4`), which is where players find their first ID. |
| **`مپ <ID>`** | Inspects one object of **this group's own city** and sends the same map with that place marked, plus an information panel as the caption. Works typed as `مپ P14`, `مپ p14`, `مپ P-014`, `مپ #P14`, `مپ P۱۴`, `مپ P 14`, `/map P14`, `map P14`, `نقشه P14`. It is a global command, handled by the same `map` handler (not part of `سهام`); the `منو` button still runs plain `مپ`. |
| **Which IDs** | The ones the world engine already stores in every layout — **no second ID system**: `P…` parcels (empty lots, built lots, parks/green blocks and the lots public places stand on), `L…` public places (city hall, hospital, school, station) and `D…` districts. Roads (`r…`), blocks (`b…`) and water (`w…`) are not inspectable and answer "not found" like any unknown ID. A public place and the parcel under it are two views of the same thing: `مپ L2` and `مپ P58` both say it is the hospital. |
| **How IDs are persisted** | They live **inside the saved layout JSON** in the `worlds` table, next to the geometry they name, assigned once when the city was generated (`p1…pN` counting up without gaps, `l1…l4`, `d1…dN`). They are looked up in the layout of the chat that asked and nowhere else, so two groups can both have a `P5` and each only ever sees its own. Restarts, repeated requests, drawing the picture at another size or in other colours, other groups' cities and future redesigns of the renderer cannot change them, because none of those touch the stored layout. IDs are never taken from list positions in the picture. |
| **Migration** | **None.** v0.15 cities already carry every ID, so no database migration, no layout-version bump and no re-generation was needed; existing cities keep their roads, districts, landmarks and lots exactly. A damaged saved layout is reported (`⚠️ نقشه‌ی ذخیره‌شده … خراب شده`) and never replaced. Backups and restores include everything as before (tests restore a backup and compare every answer). |

**What a panel says** (only what the layout contains; localized, Persian digits in Persian): the ID and the city name; the *type* (residential/commercial/industrial/civic lot, park, green space, or "public place — hospital"); the *district* (its kind plus its ID, e.g. `تجاری (D8)`, which can be inspected too); the *status* (empty land, built, green space, public place); *road access* — measured on the real road geometry: **connected** (a road is next to the lot, with that road's kind) or, for the few lots that sit inside a block (about 2% of lots), **"no direct connection"** with the distance to the nearest road; the *location* (map coordinates, and the nearest public place); the *size*; and up to four *nearby parcels* as tap-to-copy IDs so players can keep exploring. A district panel counts its lots (empty / built), green parcels and public places. **Left out on purpose** because it does not exist yet: owners, prices, rent, income, permits, costs. The footer says that buying and building are not available yet.

**The picture.** `worldmap.render(layout, lang, highlight)` draws the saved geometry as always and then marks the place: a translucent mint fill and outline on its real polygon, a ring around small places so it can be found on a phone, and the ID in a small tag that avoids the map's labels and stays inside the frame. A district gets a tinted outline instead of a ring. Flat colours only (`#AAE8B8` on the same near-black map, no glow, no gradients). It never generates anything and never changes the layout. The plain map and inspection pictures have separate bounded in-memory caches. If the picture cannot be drawn, the panel is still sent as text.

**Input handling.** An ID that looks like one but does not exist (`مپ P9999`, `مپ B3`) gets a short Persian/English error that points at a real ID to try. In a group, words after `مپ` that are not ID-shaped (`مپ کجاست؟`, `map of 5`, `مپ P14 چطوره`) are ordinary conversation and are ignored; a slash command with junk (`/map hello`) gets a one-message hint with examples and creates no city. In a private chat the usual "maps belong to groups" explanation is shown. The first `مپ <ID>` in a group that has no city yet creates it through the normal safe flow (once per group), then inspects it.

**Files.** New: `hypemarket/worldinspect.py` (ID parsing, lookup in the saved layout, facts and panels, highlight geometry; read-only), `hypemarket/texts_world.py` (FA/EN strings), `tests/test_world_inspect.py`. Changed: `handlers.py` (`مپ` takes an optional ID; group chat filter), `worldmap.py` (optional highlight, separate cache), `views.py` + `texts.py` (caption hint, text tables), `docs.py` (command reference examples). Tests: IDs unique/stable/scoped, restart, repeated use, every spelling of an ID, errors and junk, panel facts against the geometry, road-access measurement, picture checks (the change is confined to the place, stays green, inside the frame), nothing changes in any database table, the plain map is untouched, the menu button, stock commands and chat are unaffected, corrupt worlds, render failure, backup and restore.


## 15. Interactive map, walking and the casino (v0.17)

**What changed for players.** `مپ` still sends the group's city, now with your own marker, the players near you and a small keyboard. Everything you can look at is a button: no IDs to type. Walking and browsing cost nothing (v0.18.1: only blackjack at the casino is played for cash, section 17), and nothing here touches stocks, XP, levels or any stock-game number.

| What | How it works |
|---|---|
| **The keyboard** | `📍 اطراف من` (people and public places near you) · `🗺️ مکان‌ها` (all public places, 6 per page, nearest first) · `🌿 زمین‌های نزدیک` (the nearest 60 empty lots, 6 per page) · `🎰 کازینو` · `🔄 به‌روزرسانی نقشه` (and `🚶 سفر من` while you walk). Every list has ◀️ / ▶️ and a way back to the map; one tap on a place opens the same information panel as `مپ <ID>` (the panel is the existing v0.16 one), the same picture with that place marked, your road distance to it, and `🚶 رفتن`. Distances are **road** distances, not straight lines. The message is edited in place wherever Telegram allows it. |
| **Whose buttons?** | Every `مپ` creates a *map session*: one row in `map_sessions` with the opener's Telegram ID, the city, the chat and the message it lives in. Buttons carry only a random 8-character session code (`mp:<code>:<action>`), **never a user ID**. Before any button does anything the server checks, in this order: the session exists · the presser is its owner · the chat matches · the message the button sits on is the session's message · it has not expired. This check runs in the dispatcher *before the presser is even registered* and again inside the handler. Anyone else gets a short alert (`این نقشه برای بازیکن دیگری است. برای استفاده از نقشه، دستور مپ را بزن.`) and nothing is edited, started, drawn or changed. It covers browsing, paging, back, refresh, going, entering the casino and every blackjack button. Each player's newest 6 maps per city keep working (older ones stop, safely); a session lasts 24 hours from its last press (`MAP_SESSION_HOURS`). Expired, unknown, copied or forged codes get the same safe alert. A press that is valid for the owner but not for the screen (for example `رفتن` from a screen that is no longer showing that place) is refused with a short alert, so repeated or late presses never apply twice. |
| **Where you stand** | `world_players`: one row per player per city (primary key `(world, user)`, created with `INSERT OR IGNORE`, so opening the map again never moves or duplicates you). The first time you open the map you are placed on a road, on dry land, within about 140 road units of the station; the spot is chosen by a hash of *(the city's seed, your Telegram ID)*, so it is stable, needs no randomness, and does not depend on stocks, levels or rebirths. Cities and positions are per group: the same person has a separate spot and trip in every group's city. |
| **Who is near** | Other players of the **same city** whose **road distance** from you is at most 300 units (`MAP_NEARBY_RADIUS`); at most 8 are drawn, nearest first. The distance follows the roads, so someone just across the river with no bridge nearby is not "near". Banned users and bots are never shown. Nobody far away is shown. The map shows small mint rings with short names (a few labels are dropped if they would overlap); your own marker is the solid mint disc. Positions are public to anyone who can see the group message, by design. |
| **Walking** | `🚶 رفتن` plans the shortest route **along the saved roads** (a graph built from the city's own road geometry; the river is crossed only on bridges) from where you stand to the place's door, saves it in `world_trips` (route, length, speed, start and arrival time) and returns: you are **not** teleported. Duration is `ceil(length ÷ speed)` seconds with the speed of the trip (walking: default 10 units per second, shown to players as **20 km/h**; the whole city is crossed in about two minutes), no luck, no multiplier. While you walk, the map shows the part already done, the dashed rest, a ring at the destination and `🚶 در راه … — ٪ · … دیگر`; your marker sits at the point of the route the clock has reached. A second walk cannot be started (the database refuses it: a unique index allows one `walking` trip per player per city). Arrival is **lazy**: there are no timers and no background task. The next time *you* open or press anything on a map, a guarded `UPDATE` marks the trip arrived and writes your new position once (until then everybody else already sees you standing at the destination, because positions are worked out from the route and the clock). Restarting the bot loses nothing: route, length and times are saved. |
| **The casino** | Every city has exactly one, `کازینو` (`L5`-style ID: the next free `L` number). It is a fixed public place on an empty parcel that is marked as a public place for good, so a future land sale can never offer it. Which parcel: a pure function of the saved layout (an empty commercial parcel fronting a main road, away from the other public places, near the middle of town), chosen **once** and saved in `world_places` (`UNIQUE(world, kind)` and `UNIQUE(world, parcel)` make a second casino impossible). **New cities** get it in the same transaction that saves the city. **Existing cities** get it at start-up in one pass (`worldplaces.ensure_all`) or, failing that, the first time their map is opened. The saved layout JSON is never edited and nothing is re-generated: roads, river, bridges, districts, seed, every parcel ID and coordinate and every position stay exactly as they were; the casino is overlaid in memory over the saved layout. Drawn as a small diamond with the label `کازینو` / `Casino`, in the map's own colours. |
| **Entering** | `🎰 کازینو` opens the casino like any place: far away it shows the road distance and `🚶 رفتن`; while you walk there it shows your progress; only once you **have arrived** does it offer `🎰 ورود به کازینو`. The check is made again on every press (and on every blackjack button), from the saved position, so refreshing or pressing an old button never skips the walk. |
| **Blackjack** (`casino.py`) | Player against dealer. *(v0.17 had no stakes; since v0.18.1 a hand is played for life cash, see section 17.)* No XP, no item, nothing touches stock money. One standard 52-card deck, shuffled afresh for every hand with a generator of its own (`random.SystemRandom`, never the global `random`, never seeded by the game; world generation and the stock market are not touched). Two cards each, one dealer card face down; ace = 11 or 1, J/Q/K = 10; two cards worth 21 is a **natural**: if either side has one the hand ends at once (both: push). Buttons `🃏 کارت بکش` and `✋ بایست`; going over 21 loses; reaching 21 stands automatically. When you stand, the dealer reveals the card and **keeps drawing until he has beaten your total** (v0.18.2: he no longer stands on 17; see section 18). Dealer over 21: you win; dealer higher: you lose; equal totals only push when both have 21. Then you pick a chip for the next hand. No doubling, splitting or insurance. The hand lives in `casino_games` (one row per map session, owner and city stored), so it survives a restart. Every button carries the hand's step number: a state change is `UPDATE … WHERE seq=?` and the number moves on, so a repeated, duplicated or late press matches nothing and changes nothing; another player's press is refused by the owner check before it gets near the game. |
| **The Persian label** | The map's own labels use exactly `نقشه ی` (with a space). The renderer sends every label through one function (`worldmap.fix_label`) that turns the old joined or zero-width-joiner spellings of that word into the exact form, the texts were changed at the source, and tests fail if the old spelling comes back in the map's strings or the renderer. Font, positions, colours and design are unchanged. |
| **Migration** | Schema 7, additive: `SCHEMA` creates five new tables with `IF NOT EXISTS`; the migration step is a no-op `SELECT 1`. No existing table, row, city layout or player is touched, and a database that never uses `مپ` keeps working untouched. The casino rows are written the first time (or at start-up) and are idempotent. Backups and restores include the new tables like any other. A damaged saved city is reported and never replaced. |

**Settings** (all optional, in `.env`, see `.env.example`): `WALK_SPEED` (map units per second, default 10 = 20 km/h, range 1–100), `MAP_NEARBY_RADIUS` (road units, default 300, range 20–2000), `MAP_SESSION_HOURS` (default 24). Bad values fall back to the default.

**Not in this version, on purpose:** buying or selling land, building, jobs, vehicles, flights, casino bets or payouts or currencies (v0.18.1 added cash blackjack, section 17), travel between cities, a Mini App.

**Files.** New: `worldnet.py` (road graph and routing), `worldplaces.py` (the casino overlay and `load_city`), `presence.py` (positions, walks, who is near), `mapui.py` (sessions, ownership checks, screens, buttons), `casino.py` (blackjack), `texts_map.py` (FA/EN strings). Changed: `handlers.py` (`مپ` opens a session; the `mp:` callback and its pre-check; reply hook that remembers the message), `views.py` (caption with your status), `worldmap.py` (casino marker, markers/route overlay, label fix), `worldinspect.py` (panel without the footer lines inside the interactive map), `world.py` (new city gets its casino in the same transaction), `db.py` (schema 7), `config.py` (settings), `app.py` (start-up pass), `docs.py` and `texts.py` (help text, label). Tests: `tests/test_map_ui.py` (keyboard, browsing, pages, ownership of every callback, stale sessions, the label), `tests/test_map_walk.py` (spawn, nearby, routes, timing, restart, no economy side effects), `tests/test_casino.py` (casino in new and old cities, no duplicates, arrival gate, every blackjack rule and callback, the RNG, no side effects); the schema-version assertions in two older tests were moved from 6 to 7.

**Honest limits.** All positions and trips are lazy and simple: there is no collision between players and no real-time movement (the map shows where people are *when you look*). A very crowded spawn area can cover labels (a few are dropped). The pictures were checked here with a stand-in for the Persian shaper; look at the first Persian map in a real chat.


## 16. Cash economy, side jobs and social interactions (v0.18)

**What changed for players.** Every player now has a second balance, **💵 پول نقد (life cash)**, counted in dollars and cents, next to the **🪙 پول سهام (stock money)** they already had. Life cash pays for the new side jobs; stock money is still what the stock game uses; the two meet only at the exchange (`صرافی`). Nothing here changes how stocks, levels, XP, skills, rebirths or portfolio value work.

| Word | Opens |
|---|---|
| `پول` (also `پول نقد`, `کیف پول`, `wallet`, `cash`) | the wallet: both balances under their own names, the fixed rate, the latest cash transactions |
| `صرافی` (`currency`, `fx`) | the exchange: pick a direction, pick or type an amount, review, confirm or cancel. Typed shortcuts: `صرافی سهام 250`, `صرافی نقد 5`, `صرافی سهام همه` |
| `مشاغل` (`jobs`) | the job board: Snapp, McDonald's, City Hall work, the active job, your last jobs |
| `اسنپ` / `مک دونالد` / `شهرداری` | each job's own screen |
| `مپ` → `📍 اطراف من` → a player → `👋 سلام` / `🤗 بغل` | greet / ask for a hug (v0.18.1: buttons; the typed `سلام کن` / `بغل کن` commands were removed) |

### Two currencies

| | 💵 Life cash (`پول نقد`) | 🪙 Stock money (`پول سهام`) |
|---|---|---|
| Unit stored | integer **cents** (`life_accounts.balance`) | integer 1/100-coin units (`users.cash`, unchanged) |
| Shown as | `$100.00` | `1,000 🪙` |
| Starting amount | **$100.00, once per player** | 1,000 🪙 (unchanged) |
| Earned by | Snapp, McDonald's, City Hall work, the exchange | the stock game, the exchange |
| Spent on | the Snapp car rental, the exchange | the stock game, the exchange |
| Portfolio value | not part of it | shares you hold are **not** stock money; the wallet says so and never adds them in |

Every change of life cash is one row of the append-only `life_ledger` (`UNIQUE(user, kind, ref)`: the *ref* is the ride / shift / task / exchange id), written in the same transaction as the balance. That unique key is what makes every payout idempotent, and the ledger always adds up to the balance (a test checks it). A balance can neither go below zero (a `CHECK` in the table, and the code refuses first) nor above $10 trillion (a guard against overflow).

**The $100.** New players get it when their account is created. Existing players get it in migration 8 as a ledger row `('grant','start')`; a second run of the migration, a restart, a second `/start` or a direct retry cannot grant it again. Automated 🤖 traders have no account.

### The exchange (`صرافی`)

**Rate: 1,000 🪙 = $5.00, one way only (stock money → cash), no fee, no spread** *(v0.18: $20.00; changed in v0.18.1; v0.18.2 removed cash → stock money, see section 18)*. Written as integers: 2 🪙 = 1 cent, 1 cent = 200 units (`FX_UNITS_PER_CENT`). There is no way back.

**Rounding policy: there is none, because nothing ever needs rounding.** Stock money → cash moves **whole coins in multiples of 2** (every 2 🪙 are exactly 1 cent; an odd amount is refused with the step, never rounded). *(v0.18.1 also moved whole cents the other way; that direction no longer exists.)* Anything else is *refused*, never rounded: `2.5` coins, `0`, negative numbers, `1e3`, `NaN`, text, numbers past the limit (10¹² coins / cents), and booleans or floats from code. «همه / all» converts every whole coin you have (the fraction of a coin stays), trimmed to the limits and to what the cash cap still allows.

Flow: pick a preset (`50 / 250 / 1000 🪙`, only those you can afford), type an amount, or «all» → **preview** (you give, you get, the rate, both balances before and after, how long the offer lasts) → `✅ تأیید` or `❌ لغو`.

How it is kept safe: the preview is a stored, expiring quote (`fx_quotes`: random id, player, direction, exact amounts, 120 s). Amount buttons only ask for a *preview*. The confirm / cancel buttons carry **only the quote's id**, never an amount or a rate. Confirming flips the quote `open → done` with a guarded `UPDATE`, takes the source currency with a guarded `UPDATE ... WHERE cash >= ?`, writes the ledger row and adds the destination, **all in one transaction**: it all happens or none of it does (a test breaks the middle of it and checks both balances). A second press, a stale or expired preview, somebody else's preview or a made-up id changes nothing and says so. Only the newest preview of a player can be confirmed. If your balance changed after the preview, the confirmation fails cleanly and the preview stays open. Supply bookkeeping follows (`burned` for the coins that leave) and `check_level` runs like after any trade, so net worth and levels see the new stock balance.

### Economy implications (read this before you open it to a big group)

* **Stock income converts at $5 per 1,000 🪙** (v0.18.1). A day of active stock play pays about 1,000 🪙, i.e. about **$5/day**. Nothing about stock earnings was changed.
* **The starting $100 equals 20,000 🪙 at the fixed rate** (v0.18.1). Since v0.18.2 that is only a comparison: cash can no longer be turned into stock money, so a new account's $100 (like every dollar earned by jobs or won at the casino) stays in the cash economy, where the only sinks are the Snapp rental fee and the casino's house edge. If cash inflation worries you, the knob is `CASH_START_CENTS` in `config.py` (new players only; the migration grants the same amount to existing players).
* **Side jobs pay about half a stock-day per active hour at most** (calibration below). McDonald's is passive: $12 for an 8-hour shift, so three back-to-back shifts a day are $36 ≈ 1,800 🪙/day if converted, with almost no activity. If that feels too generous, lower `MCD_WAGE_CENTS`.
* Stock → cash **burns** coins (`burned` follows); since v0.18.2 nothing mints coins from cash, so the exchange can only shrink the stock-money supply.

### Jobs: one framework, three jobs (`work.py`, `workui.py`)

A job task is one row of `work_tasks`: who, which city, which job, its **stage**, the server-computed terms (reward, rental cost), the stage data, and its timestamps (created, started, **due**, finished). Rules that hold for every job:

* **One open task per player, ever** — a partial `UNIQUE` index (`state='open'`), so two overlapping shifts, two rides, or a ride plus a shift are impossible even if two presses race.
* **Everything lives in the database.** No timer, no background task and no in-memory state decides anything. Arrival and "the shift is due" are evaluated *lazily* from saved trips and the server clock when anybody looks, so a restart changes nothing and nothing is lost.
* **Every payout goes through one function** that flips the task `open → done` (guarded by its stage) and writes the ledger rows in the same transaction. A repeated or concurrent completion matches nothing and pays nothing.
* **A button carries only ids** (`jb:<action>:<task id>:<player id>`, municipal offers also the kind and the offer round) and has exactly the pieces the bot drew into it. The player id must be the presser's. Amounts, rewards, distances, tips and times are recomputed from saved state, never read from a button.
* A cooldown after a finished or given-up task (Snapp 30 s, City Hall 20 s; none for McDonald's) stops free re-rolls. Jobs pay **life cash only** and never touch stock money, XP, levels, skills or the portfolio (a test compares every stock-game table before and after).
* A task belongs to the city (group) it was started in. From another group the screen says so and offers to give it up (McDonald's shifts excepted).

**🚕 Snapp.** A rental car is yours from the start; every ride shows its rental fee **before** you take it.
* *Ride*: planned from the saved city: the passenger's pickup and the destination are real parcels / public places connected by roads, at least 60 / 120 road units apart, pickup 120–480 road units from you and the ride 240–900. The same city, driver, position and ride number always give the same ride.
* *Driving model*: the car is the ordinary v0.17 trip with mode «car» at **40 km/h** (`CAR_SPEED`, default 20 units/s; walking is 20 km/h = 10 units/s): time = road length ÷ speed, rounded up, along the saved roads, bridges included; no traffic, no teleport. You drive to the passenger, **pickup is possible only after you have actually arrived** (checked from the saved trip and the clock on every press), then drive to the destination; arrival pays.
* *Fare* = **$0.22 base + $0.03 per 100 road units** of the ride (integer cents; the distance is rounded to a whole unit, the per-distance part rounds down). *Tip*: 0 to $0.10, 3 rides in 5 on average, **decided by the server** from a hash of the city, the driver and the ride, so repeating the settlement can never re-roll it. *Rental fee*: **$0.20**, charged once when the ride ends and netted from the payout (the base fare is always above the fee, so a ride never costs money). The receipt shows base fare, tip, fee, **net** and the new balance.
* Cancelling a ride (any time) costs nothing and pays nothing; you stop **on the road where the car is**, not at a place; the cooldown applies. While a ride is running the map's `🚶 رفتن` is refused (the ride owns your one trip). A stop that turns out unreachable cancels the ride for free and says so.

**🍔 McDonald's.** Every city has one (below), at a public location. You walk there (the ordinary v0.17 walk), press *apply*, confirm, and the shift starts: **8 real hours**, saved as `started_at` / `due_at` and judged only by the **server clock**. The status screen shows the wage, the start, the expected end (clock times in `DISPLAY_UTC_OFFSET_MIN`, default Tehran), the time left and whether it was collected. The wage (**$12.00**) can be collected only once the time has passed, **exactly once** (the ledger key is the shift id). No overlapping or duplicate shifts (one open task); the shift **cannot be cancelled** and blocks every other job until collected; it survives restarts; a one-time private message tells you when it is over. The wage works from a private chat too.

**🧹 شهرداری (City Hall work).** Repeatable errands at real places of the city: **رفتگری** (sweeping, a built or empty lot) **$0.10**, **جمع‌آوری زباله** (waste collection, a public place) **$0.12**, **نگهداری فضای سبز** (green-space care, a park parcel) **$0.11**. You are offered one target per kind, each 100–520 road units away; **the reward is on the button before you accept**. You must walk there (ordinary walk) and the task is completable **only while you are standing at the target** (checked on every press), paid **once**, one task at a time. The offers are a pure function of the saved city, you, your position and your *offer round*, which moves on with every accept, so an old screen can never accept a newer offer. No XP, no levels, no stock effect.

**Calibration.** Fitted on the real generated cities and checked by `tests/test_life_jobs.py::test_job_earnings_stay_modest_and_ordered`: per hour of *active* play (road time + about 25 s of reading and tapping per task) **Snapp ≈ $11**, **City Hall ≈ $7–9.5**, **McDonald's $1.50 (passive)** — roughly half a stock-day per active hour at the very most, Snapp never above 1.8× City Hall. Every constant is at the top of the v0.18 block of `config.py`.

### McDonald's as a place (no city was regenerated)

The McDonald's is a second permanent public place, added exactly like the casino: a row of `world_places` says "this empty parcel of this city is, for good, a McDonald's"; the saved layout JSON is never edited. Its ID is the next free `L` number (`L6` after the casino's `L5`); the parcel is a pure function of the saved layout and the parcel the casino took (an empty lot, preferably commercial, fronting a main road, away from other places). New cities get it when they are saved; old cities get it at start-up (`ensure_all`) or the first time somebody loads them. **Every existing city layout, parcel ID, coordinate, place ID, player position and trip is untouched** (tests compare them before and after). A city with no empty parcel left loads normally and just has no McDonald's (the job says so). It is drawn on the map (🍔 icon, label), listed under `🗺️ مکان‌ها`, can be inspected (`مپ L6`), and is never offered as empty land.

### Greetings and hugs (`interact.py`, `interactui.py`)

*v0.18.1: these are glass buttons now; the typed commands `سلام کن` / `بغل کن` were removed (a plain message with those words is just conversation again).* Open `مپ`, press **`📍 اطراف من`**: the players near you (by road distance, nearest first, at most 8) are buttons. Press one → a small card with **`👋 سلام`** and **`🤗 بغل`**.

* **`👋 سلام`** — instant. The group gets a one-line greeting that names (and notifies) both players; the map message stays where it is. 20 s cooldown per sender (read from the table, so it survives restarts).
* **`🤗 بغل`** — a *request*, never an action. The other player gets a message with `🤗 قبول` / `🙅 نه، ممنون`; the sender gets `↩️ پس گرفتن`. **Only the target can accept or decline, only the sender can take it back** (checked from the saved request, not from the button). It expires after 5 minutes (lazily: the first press or the next request sees it). Repeated, late, expired or forged presses change nothing and say so. A hug needs both players to be **near each other** (road distance ≤ `MAP_NEARBY_RADIUS`, checked when asking *and* when accepting; saying no never needs it; `HUG_NEED_NEAR=0` turns the rule off). Limits against pestering: 60 s between requests, one open request per sender, at most 3 waiting for the same player, and a 10-minute pause for the same pair after a «no».
* The person buttons carry the player's id but are only honoured when that player is in *your* current nearby list and the screen is the person card of the same session (a forged id, a player who walked away or an old screen gets a short notice and the real list).
* Nothing here touches money, stocks, levels or any other part of the game.

### Migration (schema 8, additive)

`SCHEMA` creates five new tables with `IF NOT EXISTS` (`life_accounts`, `life_ledger`, `fx_quotes`, `work_tasks`, `interactions`); migration 8 adds `world_trips.mode` (default `'walk'`, so every existing trip stays a walk) and writes one `('grant','start')` ledger row plus a $100 account for every existing human player. Nothing existing is changed or removed: no city, position, stock balance, portfolio, trip or casino record is touched (the tests compare users, stock tables, cities and trips before and after). A database that never uses the new words works exactly as before; reopening it changes nothing. Backups and restores include the new tables like any other.

### Settings (all optional, `.env`; bad values fall back to the default)

`CAR_SPEED` (map units per second of the Snapp car, default 20 = 40 km/h, range 1–200) · `DISPLAY_UTC_OFFSET_MIN` (minutes added to UTC for the shift clock times, default 210 = Tehran) · `HUG_NEED_NEAR` (`1`/`0`). Every other number (fee, fares, tips, wage, rewards, ranges, cooldowns, the exchange rate and limits, the starting cash, hug timings) is a constant in the v0.18 block of `config.py`.

### Files

New: `cash.py` (accounts, ledger, parsing, the exchange), `work.py` (the job framework and the three jobs), `workui.py` (their screens and `jb:` buttons), `lifeui.py` (wallet and exchange screens), `interact.py` (greetings, hugs), `texts_life.py` (FA/EN strings), tests `test_life_cash.py`, `test_life_jobs.py`, `test_life_flow.py`, `test_life_places.py`, `test_life_social.py`. Changed: `db.py` (tables, migration 8), `config.py`, `core.py` (account at registration), `handlers.py` (words, `cs:` / `jb:` / `ia:` callbacks, group filter, error texts), `presence.py` (trip mode, cancel), `worldplaces.py` / `world.py` / `worldmap.py` (the McDonald's), `mapui.py` (jobs button, no walking away from a ride), `views.py`, `sections.py`, `docs.py`, `texts.py`, `jobs.py` (shift-over notice), and the test files whose expectations the new place and buttons changed (`test_casino`, `test_map_ui`, `test_world_inspect`, `test_exchange`, `test_namespace`, `test_bot` helper).

### Honest limits

Out of scope on purpose and not built: land, construction, businesses, vehicle ownership, travel between cities, and any game other than blackjack (v0.18.1 made blackjack a cash game, section 17). There is no traffic, collision or real-time movement; the car is faster walking and positions are applied when somebody looks. The tests run against a simulated Telegram and a stand-in for the Persian shaper (the one failing test of the suite, `tests/test_share.py::test_persian_is_really_shaped_and_the_font_ships_with_the_bot`, needs the real shaper and passes on a normal install); look at the first Persian screens in a real chat and do the two-account check from section 9 before announcing.

## 17. v0.18.1 — fixes and polish on top of v0.18

Schema **9** (additive). Everything below was asked for in one list; each item says what changed and what to watch.

| # | What | How |
|---|---|---|
| 1 | **Interactions are buttons** | `📍 اطراف من` → a person → `👋 سلام` / `🤗 بغل` (section 16). Typed `سلام کن` / `بغل کن` are gone from the parser, the help and the docs. |
| 2 | **Snapp has a map picture** | Taking or driving a ride shows the city map with the route: **your way to the passenger in blue** (the part already driven is solid), **the passenger's ride to the destination in yellow**, a person pin at the pickup, a flag pin at the destination, and **you as a small car** (pointing the way you drive) instead of the round marker. Other players who are driving show as cars on your map too (`presence.Other.mode`), so a future real pickup of another player just works. Walking to McDonald's or a City Hall site is drawn like every walk with a bag pin at the workplace. `jobviz.py` builds the markers (pure, runs in a worker thread); `worldmap.overlay` draws them. A picture that cannot be drawn is simply not sent: the screen's text and buttons always go out. |
| 3 | **1,000 🪙 = $5** | `config.FX_UNITS_PER_CENT = 200` is the only number that sets the rate; every text and limit is derived (`fx_coin_step()` = 2: 🪙 → $ moves even amounts, odd ones are refused with the step, never rounded). Existing balances are not touched. |
| 4 | **Pictures for jobs** *(replaced in v0.18.2: only maps remain, see section 18)* | `scenes.py` drew the job board, the Snapp passenger waiting next to the rental car, the McDonald's counter with the **shift's progress bar and the remaining time**, ready-to-collect, and the street workers (sweeping, waste, green space). They are drawn with Pillow from the task's state (no image files, nothing downloaded), so they are always in sync with the text. |
| 5 | **The rental car is a Pride** | A Snapp ride's car is a **پراید** with a **model year 1380–1395** and a colour, chosen from the saved ride (a pure function of city, driver and ride number, stored in the task data: it never changes during the ride). The year is shown in the text («پراید ۱۳۸۴ (سفید)» / «Pride 1384 (white)»), on the plate in the picture and as a car-shaped marker on the map. No other car model exists anywhere. |
| 6 | **The casino plays for cash, blackjack only** | See below. |
| 7 | **«روزانه» resets at 00:00 Tehran time** | The daily bonus is once per *Tehran calendar day* (`config.daily_day`, `DAILY_UTC_OFFSET_MIN`, default 210), not 24 h after the last claim. The streak continues if the last claim was *yesterday*; a missed day resets it. The wait message says «ساعت ۰۰:۰۰ به وقت تهران». The quests' streak snapshot uses the same day. |
| 8 | **No made-up people in the exchange news** | The six companies no longer have fake CEO names. Headlines, the company card and the earnings night quote **the company itself** («بیانیه‌ی چت‌باکس» / "Chatterbox statement"). The statements were rewritten in a plain middle register: spoken enough to read easily, no street slang, nothing bookish (`texts_exchange.py`, keys `ex.<company>.voice` and `up1/up2/down1/down2`). |
| 9 | **English digits** | `config.digits_mode()` (env `DIGITS`, default `en`): every template, number formatter, button label and outgoing message goes through `fmt.digits()`, so «۱٬۰۰۰ 🪙» is «1,000 🪙» everywhere, in pictures too. **The Persian numbers are not deleted**: set `DIGITS=fa` and the old look is back. (Names typed by players that contain Persian digits are converted as well.) |

### Blackjack for cash (`casino.py`, `mapui.py`)

* The casino has **one game: blackjack** (nothing else was added). You play with **life cash** (dollars); stock coins, shares, levels and XP are never touched (a test compares every stock table before and after).
* **Chips**: `$1 · $5 · $10 · $25 · $100` (`config.CASINO_BETS_CENTS`), shown only if you can pay them. Pressing a chip deals the hand and **takes the chip in the same database transaction**; if you cannot pay it, no hand exists and nothing moves.
* **Pay table**: win or dealer bust **1 to 1**, natural blackjack **3 to 2**, push gives the chip back, a loss or bust pays nothing. Rules of the cards are as in v0.17 (v0.18.2 changed the dealer, see section 18; no doubling, splitting or insurance).
* **Never twice**: every movement is a `life_ledger` row (`casino_bet` / `casino_win`, ref `<session>:<hand number>`) and `(user, kind, ref)` is unique; every press also carries the game's `seq`, so repeated, late or duplicated presses change nothing. A failure while paying undoes the whole press (tests break the middle of it).
* **Forgotten hands**: a hand left alone for 30 minutes (`CASINO_IDLE_SECS`), or whose map message expired or was pushed out, is **stood automatically and paid by the same rules** (`casino.sweep`, run by the 5-minute background loop and whenever a player opens a map), so a chip can never disappear with a forgotten message. A balance is never pushed past the cap: a payout that would is cut to the room left.
* Schema 9 adds `bet`, `hand_no`, `paid` to `casino_games` (defaults 0 / 1 / 0, so every old game row, finished or running, keeps its cards and result; an old running hand is stood without any money moving). The old `🔁 دست جدید` button of a v0.18 message just shows the chips.

### Settings added (`.env`, optional)

`DIGITS` (`en` default; `fa` = old Persian digits) · `DAILY_UTC_OFFSET_MIN` (default 210 = Tehran; the daily bonus resets at local midnight of that offset). The rate, the chips, the blackjack payout and the idle time are constants in `config.py`.

### Consequences you should know about

* **The $100 start grant is now worth 20,000 🪙** at the new rate (it was 5,000 🪙), and job wages are relatively larger than stock income ($5 per ~1,000 🪙 a day). If alt accounts worry you, the knobs are `CASH_START_CENTS` and the job constants in `config.py` — I changed none of them.
* The daily bonus is stricter about streaks (a streak survives only if you claimed *yesterday*), and a player who claims at 23:59 can claim again at 00:00.
* An odd number of 🪙 can no longer be exchanged (the step is 2); the screen says so.
* Blackjack has a house edge like any real table; nothing refunds a loss.

### Files

New: `jobviz.py` (markers for the job pictures), `scenes.py` (scene illustrations), `interactui.py` (messages of the glass-button interactions), tests `test_v0181_daily.py`, `test_v0181_pictures.py`, `test_v0181_texts.py`, `test_casino_cash.py`, `test_digits.py`, `conftest.py` (the older suites run with `DIGITS=fa`; the English default is covered by `test_digits.py`). Changed: `config.py`, `cash.py`, `lifeui.py`, `progress.py`, `quests.py`, `casino.py`, `mapui.py`, `presence.py`, `work.py`, `workui.py`, `worldmap.py`, `handlers.py`, `views.py`, `fmt.py`, `cards.py`, `db.py` (schema 9), `jobs.py` (casino sweep), `interact.py`, `texts*.py`, `docs.py`, `sections.py`, and the older tests whose expectations moved with the above.

### Honest limits

The pictures are drawn with Pillow from scratch (no art files); they were checked here with a stand-in for the Persian shaper, so look at the first few in a real chat. The tests run against a simulated Telegram (the one known failure, `tests/test_share.py::test_persian_is_really_shaped_and_the_font_ships_with_the_bot`, needs the real shaper and passes on a normal install).

## 18. v0.18.2 — Life Game polish, one look, one-way exchange (on top of v0.18.1)

**No schema change** (still schema 9, nothing to migrate, no table or column added, no saved row rewritten). Everything below was asked for in one list; each row says what changed and what to watch.

| # | What | How |
|---|---|---|
| 1 | **One look: the city map's** | The black / dark-green, flat, minimal look of the map is the source of truth for every Life Game screen. The illustrated banners, the job "scene" pictures and the character drawings are gone (`scenes.py` is deleted); the map generator, the renderer and every saved city are **untouched** (`worldmap.py` is not changed; the map picture keeps its own label «نقشه ی» on purpose, see 5). |
| 2 | **The exchange is one-way** | Stock money → cash only. Cash → stock money no longer exists **anywhere**: not in `cash.py` (no `c2s` path, no `FX_MAX_CENTS`), not in the handlers, not in the buttons or texts, not in the wallet and exchange screens, not in the typed command (`صرافی نقد 5` is answered with «پول نقد به پول سهام تبدیل نمی‌شه…», in private and in a group; ordinary chat that only starts with the word is still ordinary chat). The rate stays **1,000 🪙 = $5.00**, the step stays 2 🪙, quotes are still stored, single-use and expire after 120 s, every confirm is one transaction with its ledger row, a repeated or late press changes nothing, and a forged or stale id is refused. **Old data is kept**: an open `c2s` preview of v0.18.x that is still in a database, and an old `cs:a:c2s:…` button on somebody's screen, are answered with the one-way refusal and move nothing (the stale row is voided); old ledger rows are never rewritten. |
| 3 | **Persian typography** | Names and compound words are written the standard way, with the half-space where it belongs: «مک‌دونالد», «ساخت‌وساز», «اجاره‌ای», «فست‌فود», «نقشه‌ی», «این‌جا», «بی‌اعتماد», «می‌شه» … . One term per thing everywhere (`پول نقد` for the life cash, `پول سهام` for the stock money; no «کش», no «نقدینگی»). **Every old spelling a player may type still runs the command** (`مک دونالد`, `مکدونالد`, `شغل ها`, `بی اعتماد`, `mcd`, `mcdonalds`…): `textparse` drops the half-space before matching, and `ALIASES` lists the new spellings first. A regression test scans every Persian string and every help entry for the typical mistakes (a space where a half-space belongs, a half-space at an edge, Arabic ي/ك, space before «؟», missing space after «،», …). **Generated images**: the job scene pictures (the only generated images that carried Persian words of the Life Game) are gone, so what is left is the map and route pictures (their labels are drawn by the unchanged map renderer, anchored at their centre) and the share cards (unchanged): plain text is used wherever a picture is not needed. |
| 4 | **`مشاغل` is a compact text list** | One short message: the three jobs, each with its real state on one line (free / on a ride / shift running / ready to collect / task in progress) and **one button** into it. No picture. |
| 5 | **No job characters, no invented numbers** | The standalone job illustrations are removed. **The map and route pictures stay** (the way to a passenger, the ride, the walk to McDonald's or to a City Hall site). The dashboards show only what the game really tracks: McDonald's the saved shift (start, end, remaining time, wage), Snapp the estimate and the earnings apart (fare, fee, tip) and the stated speeds, City Hall the real tasks with their fixed rewards. There is **no XP, level, skill, rank or percentage of anything the game does not store** (a test walks every job through every state and searches every screen for such words). |
| 6 | **The casino is concise, and the dealer plays to beat you** | See below. |
| 7 | **Explicit speeds** | Walking **20 km/h**, the rental Pride **40 km/h**, nowhere "twice as fast". See below. |
| 8 | **Nothing else touched** | Stocks, XP, levels, the Rofaqa Exchange, the daily bonus and salary, sections, the admin panel, share cards, backups: unchanged. Saved cities, trips, casino rows, tasks and ledger rows keep their values. |

### Blackjack: concise screens and the dealer rule

* **One table message**: the entry screen is the chips (`🎰 کازینو`, your cash, «مبلغ شرطت رو انتخاب کن.»), the hand screen shows the bet, both hands and `🃏 کارت بکش` / `✋ بایست`, the result screen shows the result, the money change and the chips for the next hand. The rules are **not** repeated on every screen: there is a **rules button** that opens them (`bj:i`) and goes back to the table. The entry button of the casino opens the table directly, and a hand that is already running is resumed, never replaced.
* **The dealer rule (custom, as specified).** The dealer does **not** stand on 17. After you stand, he keeps drawing **until his total beats yours**, and he never draws on 21 or more. In `casino.dealer_wants_card`: `t < player_total`, or `t == player_total` while `DEALER_DRAWS_ON_TIE = True`. Results: dealer over 21 → you win; your total higher (only possible when the deck is empty, which the tests prove cannot happen) → win; the dealer's higher → you lose; equal totals **push** only at 21, otherwise he draws on a tie and either passes you or busts. Naturals are as before (either side's natural ends the hand at once, both = push, yours pays 3 to 2). If you would rather have him **stop on a tie** (a tie then pushes at any total), set `DEALER_DRAWS_ON_TIE = False` in `casino.py`: one constant, the tests cover both values.
* **Payouts and limits are unchanged**: win 1 : 1, natural 3 : 2, push returns the chip, chips $1 / $5 / $10 / $25 / $100, one chip per hand.
* **What it does to the house edge** (an infinite-deck calculation with the best possible player strategy, `3 : 2` naturals included; the real single deck differs by a little): about **−3.2 %** per hand for a perfect player under the new rule, against about −2.4 % with the usual «stand on 17» rule. A simple player who hits until 16 or 17 loses about 5 % per hand under both rules; the rule hurts most a player who stops early (hitting until 12: about −8 % before, −22 % now). It makes the table a bit worse for the player than a normal one, and nothing can leave the cash economy any more (see the risks below).
* **State machine**: the whole machine (deal, naturals, hit, bust, 21, stand, dealer draw, every result, every repeated / late / foreign press, the idle sweep, a restart in the middle of a hand, an old v0.18.1 row, an exhausted deck) is covered by `tests/test_v0182_blackjack.py`, including 30,000 random hands with random strategies where the dealer never stops while he still wants a card and the result always agrees with the totals, and database-level tests that every result settles the bet exactly once.

### Speeds and the scale of the world

* **`UNITS_PER_KM = 1800`** is the only conversion between the map's units and the game-world kilometre (`config.py`). Walking is 20 km/h = **10 units/s**, the rental Pride 40 km/h = **20 units/s**: exactly the speeds trips were always planned with, so **saved trips, durations and ETAs do not change** and each trip keeps the speed it was saved with (a later change of `WALK_SPEED` / `CAR_SPEED` affects new trips only).
* Distances read as metres below a kilometre and kilometres above (`fmt.dist`: «۵۰۰ متر», «۱٫۵ کیلومتر»; from 995 m up it is «1 km», never «1000 m»), areas as m² (`fmt.area`, used by the parcel inspector), speeds as `km/h` (`fmt.kmh`). No screen says «units» or «واحد» for a distance any more. The Snapp terms screen shows the per-kilometre part of the fare (`config.snapp_per_km_cents()`, 54 ¢/km).
* **Distance, duration and ETA come from one saved number each**: the object screen (`مپ` → a place) shows distance, the speed and the ETA, and the trip screen shows the remaining distance and the speed; a test proves ETA = ⌈road length ÷ stated speed⌉ and that the Pride takes half the walking time on the same road. The `.env` keys `WALK_SPEED` / `CAR_SPEED` still work (units per second); the screens then show the matching km/h.

### Settings

No new key. `WALK_SPEED` and `CAR_SPEED` keep their meaning (map units per second; defaults 10 and 20 = 20 and 40 km/h). `.env.example` now says so. `DEALER_DRAWS_ON_TIE` is a constant in `casino.py`, like the other game rules.

### Remaining economy abuse risks (audit; nothing here was changed beyond what is listed above)

What the one-way exchange **closes**: the cash → stock-money loop (job wages, casino wins and the $100 start grant can no longer become coins, so the stock market can no longer be fed from the cash economy, and alt accounts cannot funnel cash into a main account's stocks). What is still open, most important first:

1. **Cash only grows.** Its sources are the $100 start grant, the daily jobs, casino wins and the exchange (stock → cash, no limit besides what a player owns); its sinks are the Snapp fee and the casino's edge. There is **nothing to buy with it yet**, so today it is harmless, but any v0.19 feature that sells something for cash (land, buildings, vehicles) will meet an economy with no brake. Decide the sinks and a per-day exchange limit before shipping such a feature.
2. **Alt accounts**: every account gets $100 once, a McDonald's shift pays $12 a day passively and the exchange has no daily limit. There is **no player-to-player cash transfer**, so alts cannot send money to a main account today (and the casino cannot be used to pass money: a lost hand goes to the house). Still, N accounts earn N × the wages. The gate (required channel and group membership) is the only barrier.
3. **Scripted play**: Snapp rides and City Hall tasks can be automated with a user-account script, bounded by the cooldowns (30 s / 20 s) and the fixed rewards; a McDonald's shift is naturally passive. Nothing detects automation.
4. **Coins already created by the old cash → stock exchange stay.** v0.18 / v0.18.1 let players turn cash into coins; I did not take any of that back. To see how much, run: `SELECT user_id, COUNT(*), SUM(stock_delta) FROM life_ledger WHERE kind='fx' AND stock_delta>0 GROUP BY user_id ORDER BY 3 DESC;` (those are the cash → stock rows; `note='cash->stock'`).
5. **Stock-money inflation is outside this change**: the daily bonus, salary, trader-bot top-ups and trading profit are as before, and the exchange now turns any of it into cash at a fixed $5 per 1,000 🪙.
6. **Casino** has a negative expectation for the player under every strategy, no transfer path and no way out of the cash economy, so it cannot be used to launder or multiply money; a hand left alone for 30 minutes is stood and paid by the same rules; a balance is never pushed past the cap.

### Honest limits

* The Persian shaping and the fonts of the **map images** (their labels) were checked with a stand-in for the shaper, as before: look at the first map, route and casino messages in a real chat. The text screens are plain Telegram text and don't depend on it.
* The pictures were not redesigned; the map's look was the reference and the map code is unchanged.
* The one known test that needs the real shaper (`tests/test_share.py::test_persian_is_really_shaped_and_the_font_ships_with_the_bot`) cannot run in this build environment and passes on a normal install.
* The house-edge numbers are calculated for an infinite deck; the game deals from one 52-card deck, so the true values differ slightly.

### Test report

* **Full suite, run once at the end on the final code**: 1,167 tests collected, 1 deselected (see below), 1,166 run: **1,164 passed, 2 failed** in 8 min 27 s. Both failures were out-of-date expectations in tests, not product bugs: `test_map_ui.py::test_no_map_rendering_source_contains_the_old_spelling` still forbade the standard «نقشه‌ی» in the text tables (real text messages use it since v0.18.2; the check now covers the renderer and the strings drawn into the picture), and `test_v0181_pictures.py::test_the_mcdonalds_screens_are_maps_while_walking_and_text_at_the_counter` expected a filled block in a progress bar of a walk that has just begun (it is 0 %, so the bar is empty). After fixing those two tests the two files were run again: **47 passed**. The rest of the suite was not run a second time (only those two test files changed).
* **Deselected**: `tests/test_share.py::test_persian_is_really_shaped_and_the_font_ships_with_the_bot` (needs the real `arabic-reshaper`, which cannot be installed here; passes on a normal install). The suite otherwise runs against a simulated Telegram and a stand-in for the Persian shaper.
* **Newly written for this version**: `test_v0182_blackjack.py`, `test_v0182_exchange.py`, `test_v0182_jobs_ui.py`, `test_v0182_scale.py`, `test_v0182_typography.py` (all passed in the full run). The older suites that moved with the changes are listed under Files.
* **Not checked here**: the real Persian shaping of the pictures and a real Telegram client (see the manual checks below).

### Manual checks in a real Telegram chat (10 minutes)

1. `مپ` → the map is as before (nothing in the picture changed).
2. `مشاغل` → a short text list; each job opens a text dashboard; only the Snapp ride / the walk to McDonald's / the City Hall walk show a map picture.
3. `صرافی` → only stock money → cash; `صرافی نقد 5` is refused; try an old preview button, if one is still on screen.
4. The casino: enter, play a few hands to see a dealer draw past 17 and the result message; the rules button.
5. A place on the map → the screen shows metres / kilometres, **20 km/h** and the ETA; start a walk and a Snapp ride and check the remaining distance and speed on the trip screen.
6. Look at the Persian of the new texts once (half-spaces: «مک‌دونالد», «ساخت‌وساز», «اجاره‌ای»).

### Files

Deleted: `scenes.py`. Changed: `config.py` (scale, `snapp_per_km_cents`, `FX_MAX_CENTS` removed), `fmt.py` (`kmh`, `dist`, `area`), `cash.py`, `casino.py`, `mapui.py`, `lifeui.py`, `handlers.py`, `jobviz.py`, `work.py`, `workui.py`, `worldinspect.py`, `texts.py`, `texts_life.py`, `texts_map.py`, `texts_world.py`, `docs.py`, `.env.example`, `README.md`, and the older tests whose expectations moved (`test_casino.py`, `test_casino_cash.py`, `test_life_cash.py`, `test_life_flow.py`, `test_map_ui.py`, `test_namespace.py`, `test_v0181_pictures.py`). New tests: `test_v0182_blackjack.py`, `test_v0182_exchange.py`, `test_v0182_jobs_ui.py`, `test_v0182_scale.py`, `test_v0182_typography.py`.
