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
| Safety | **hourly verified backups sent to every admin** · scam reports (`گزارش`) and trust marks (`اعتماد` / `بی اعتماد`) with a public 🚩 warning and admin Delist / Ban / Dismiss buttons · `/ban` `/unban` `/delist` · players see a 🤝 trust label on every stock |
| Sharing | `کارت` makes a picture card (portfolio, rank or a stock) with your invite link and a Telegram "send to friends" button; a friend who opens a shared stock card lands on that stock and counts as your invite |
| Daily salary | every morning a private message with your wealth summary and a **claim salary** button (`حقوق` turns it on/off) |
| Progress | 26 achievements (`دستاوردها`) with rewards · admin analytics dashboard (`/stats`) and a daily report |
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
Schema changes are applied automatically on start and keep all data; older databases are upgraded step by step (v0.13 = schema 5: short selling is removed, every open margin is refunded to its owner's cash, the 3 skill points of the old skill are returned, and each affected player gets one private message).
`راهنما` ← `همه‌ی دستورها` is generated from `hypemarket/docs.py`: add a command there (the tests fail if one is missing or an example doesn't parse).

## 4. Backups (read this once)

* Every `BACKUP_EVERY_MIN` minutes (default 60) the bot takes a consistent copy of the whole database, **checks it** (integrity check + counts),
  compresses it and sends it **silently** to every account in `ADMIN_IDS`, with a caption (time, players, stocks, checksum). It also keeps the
  newest `BACKUP_KEEP` (default 48) copies in `BACKUP_DIR` (`backups/`). The first backup arrives a few seconds after the bot starts.
* **Every admin must open the bot in a private chat and press `/start` once**, otherwise Telegram will not let the bot message them
  (the bot logs a warning at startup if it can't reach someone). If a backup fails or can't be made, the admins get an alert message.
* `/backup` (admins only) sends one immediately.
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

* Owner commands (your id in `ADMIN_IDS`): `/stats` (users, groups, coins minted/burned), `/give <id> <coins>`, `/fire [pump|crash|boom|special|holiday|mania]`.
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
* **Tested vs. not tested:** the game rules, Persian parsing, buttons, payments, group flows and background jobs are covered by about 355 automated tests,
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
  a chart and a report with real numbers posted to every group that follows the market, with a made-up CEO commenting on the numbers. Moves are announced *after* they happen.
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
| **Global** | `شروع`, `راهنما`, `منو`, `پروفایل`, `زبان`, `دعوت`, `روزانه`, `حقوق`, `فروشگاه`, `تنظیمات`, `تنظیم زبان`, `محدودیت پیام`, `حالت سکوت` | unchanged |
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

