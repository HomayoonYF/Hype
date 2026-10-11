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

| Area | What it does |
|---|---|
| Stocks | `عرضه` personal stock · `عرضه میم` meme · `عرضه پروژه` crowdfunded project (goal bar + reward) · blue-chip (legacy unlock) |
| Pricing | Linear bonding curve: reserve always equals the integral of the price curve, so buying/selling is symmetric and the market can never become insolvent |
| Passive income | owner share of every trade fee · dividends (owner sets `تنظیم سود`) · hourly holder bonus (`درآمد`) · chat activity pays the group's index and the author's stock · company tax |
| Risk & drama | rug pull (`خروج`, 6h minimum age, −40 reputation, tax) · shorting (perk) · hype pumps · crashes/booms · bubbles & corrections · meme mania · fee-free hours |
| Social | every group gets an index stock · group ranking · leaderboards · companies (shared treasury, daily-bonus synergy) · invites with rewards · bonus for adding the bot to a group |
| Progression | net-worth levels (no cap) · skill tree · reputation · rebirth with Legacy points & permanent perks · 30-day seasons with prizes |
| Live market | automated 🤖 market traders keep prices moving · 🐋 whale headlines (and DMs to holders) · detailed candlestick charts with volume, moving average, whale markers and a full stats caption |
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

## 4. Operating it

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

## 5. Things you should know (honest limits)

* **Virtual coins only.** Keep it that way. Selling coins for money or paying out cash prizes turns this into gambling/securities
  territory with legal and Telegram-policy risk. The shop sells only cosmetics and small perks.
* **Stars:** Telegram Stars may be hard to buy for users in some countries (including Iran), so don't plan revenue around the shop
  until you've checked it with real users. Everything else works without it.
* **Alt accounts:** new accounts get starting cash, so people can farm with extra accounts. Mitigations built in: sell lock,
  trade cooldown, daily activity caps, referral rewards only after 3 real trades, shorts voided on rugs. For a big launch, add
  account-age rules or a captcha-like gate in `ensure_user`.
* **Scale:** SQLite + one process comfortably handles thousands of players. If you outgrow it, the database layer is isolated in `db.py`.
* **Tested vs. not tested:** the game rules, Persian parsing, buttons, payments, group flows and background jobs are covered by 86 automated tests,
  including a randomized simulation (traders, whales, quests, rug pulls, shorts, rebirths all at once) that checks every coin is accounted for. These run against a *simulated* Telegram. I could not run
  against real Telegram servers, so do a 15-minute manual test with two accounts and a test group before announcing it.
