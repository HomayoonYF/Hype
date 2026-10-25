"""Background loops: they keep the market alive while nobody is typing."""
from __future__ import annotations

import asyncio
import hashlib
import logging

from aiogram.exceptions import TelegramBadRequest, TelegramForbiddenError, TelegramRetryAfter
from aiogram.types import BufferedInputFile

from . import achievements, analytics, backups, bots, casino, charts, config as C, exchange, salary, core, events, progress, quests, social, trading, views, work
from .handlers import App, fit_caption, flush_outbox, send_mod_alerts
from .texts import t

log = logging.getLogger("hypemarket.jobs")


async def fast_tick(app: App) -> None:
    """Every ~20 s: chat activity -> dividends, price alerts, queued DMs."""
    counts, seen = app.swap_buffers()
    if counts or seen:
        social.flush_activity(app.db, counts, seen)
    quests.drain_events(app.db, None)          # quests finished by chatting etc. become DMs
    achievements.drain_events(app.db, None)
    await send_mod_alerts(app)
    trading.check_alerts(app.db)
    exchange.announce_migration(app.db)        # one-time DMs about the removal of short selling
    await flush_outbox(app)


async def bot_tick(app: App) -> None:
    """Every ~30 s: the automated market traders make their small trades."""
    bots.tick(app.db)


async def exchange_tick(app: App) -> None:
    """Every ~60 s: the six listed companies take their next price step; on earnings night the report is posted to the groups."""
    exchange.tick(app.db)
    if exchange.earnings_due(app.db):
        report = exchange.run_earnings(app.db)
        if report:
            await post_earnings(app, report)
    await flush_outbox(app)


async def post_earnings(app: App, report: dict) -> None:
    """Earnings night: one chart + a short report in every group that follows the market (news mode quiet or live)."""
    db, bot = app.db, app.bot
    if bot is None:
        return
    png = charts.earnings_chart(report)
    for g in db.qa("SELECT * FROM groups WHERE active=1 AND news_mode!='off'"):
        try:
            text = views.earnings_text(g["lang"], report)
            await bot.send_photo(g["id"], BufferedInputFile(png, "earnings.png"), caption=fit_caption(text))
        except TelegramRetryAfter as e:
            log.warning("flood control while posting earnings; pausing %ss", e.retry_after)
            await asyncio.sleep(min(e.retry_after, 30))
        except TelegramForbiddenError:
            social.deactivate_group(db, g["id"])
        except Exception:
            log.warning("could not post the earnings report to group %s", g["id"], exc_info=True)
        await asyncio.sleep(0.05)


async def slow_tick(app: App) -> None:
    """Every ~5 min: events, price snapshots, net worth + levels, seasons, group boards."""
    events.tick(app.db, app.settings.events)
    core.record_stale(app.db)                  # a price point for every live stock, so charts have no gaps
    analytics.snapshot(app.db)
    await daily_report(app)
    progress.refresh_all_networth(app.db)
    social.refresh_group_scores(app.db)
    progress.end_season_if_due(app.db)
    work.notify_ready(app.db)                  # one DM per finished McDonald's shift (the shift itself never depends on this loop)
    try:
        casino.sweep(app.db)                   # blackjack hands left on the table (idle 30 min, or their message expired) are stood and paid
    except Exception:
        log.exception("casino sweep failed")
    await post_boards(app)
    await flush_outbox(app)


async def salary_tick(app: App) -> None:
    """Every 5 minutes inside the salary window: send the daily salary DM to the next batch of eligible players."""
    s = app.settings
    if not s.salary or app.bot is None:
        return
    hour = (core.now() // 3600) % 24
    if not (s.salary_hour <= hour < s.salary_hour + C.SALARY_WINDOW_HOURS) and not (s.salary_hour + C.SALARY_WINDOW_HOURS > 24 and hour < (s.salary_hour + C.SALARY_WINDOW_HOURS) % 24):
        return
    day = core.now() // 86400
    for u in salary.candidates(app.db, day):
        try:
            r = views.salary_message(app.db, u["lang"], u["id"])
            await app.bot.send_message(u["id"], r.text, reply_markup=r.kb)
            salary.mark_sent(app.db, u["id"], day)
        except TelegramRetryAfter as e:
            log.warning("flood control while sending salary DMs; pausing %ss", e.retry_after)
            await asyncio.sleep(min(e.retry_after, 30))
            return                                        # the rest goes out on the next round
        except (TelegramForbiddenError, TelegramBadRequest):
            salary.mark_blocked(app.db, u["id"], day)     # never opened a private chat / blocked the bot: don't retry
        except Exception:
            log.exception("salary DM failed for %s", u["id"])
            salary.mark_blocked(app.db, u["id"], day)
        await asyncio.sleep(0.05)                         # about 20 messages per second, under Telegram's limit


async def backup_tick(app: App) -> None:
    """Every few minutes the clock is checked; when a backup is due (every 24 h by default) a verified, compressed copy of the whole
    database goes to every admin. The schedule is stored in the database, so a restart neither skips nor repeats a backup."""
    await backups.scheduled(app)


async def daily_report(app: App) -> None:
    """Once a day at REPORT_HOUR_UTC: the analytics dashboard goes to every admin."""
    day = core.now() // 86400
    if (core.now() // 3600) % 24 != app.settings.report_hour or core.kv_int(app.db, "last_report_day") == day:
        return
    core.kv_set(app.db, "last_report_day", day)
    for admin in app.settings.admins:
        u = core.get_user(app.db, admin)
        lang = u["lang"] if u else "en"
        r = views.dashboard_view(app.db, lang)
        try:
            await app.bot.send_message(admin, t(lang, "an.daily") + "\n\n" + r.text)
        except Exception:
            log.warning("could not send the daily report to admin %s", admin)


async def post_boards(app: App) -> None:
    db, bot = app.db, app.bot
    for g in db.qa("SELECT * FROM groups WHERE active=1 AND news_mode!='off'"):
        lang = g["lang"]
        try:
            if g["news_mode"] == "quiet":
                text = views.board_text(db, lang)
                h = hashlib.md5(text.encode()).hexdigest()
                if h == g["board_hash"] and g["board_msg"]:
                    continue
                if g["board_msg"]:
                    try:
                        await bot.edit_message_text(text, chat_id=g["id"], message_id=g["board_msg"])
                    except TelegramBadRequest as e:
                        if "not modified" not in str(e).lower():
                            raise
                else:
                    m = await bot.send_message(g["id"], text)
                    db.x("UPDATE groups SET board_msg=? WHERE id=?", (m.message_id, g["id"]))
                db.x("UPDATE groups SET board_hash=?, board_fail=0 WHERE id=?", (h, g["id"]))
            else:  # live: post a digest of new headlines, at most one message per 10 minutes
                rows = db.qa("SELECT * FROM news WHERE id>? ORDER BY id LIMIT 5", (g["last_news_id"],))
                if not rows or core.now() - g["last_post"] < 600:
                    continue
                text = "\n".join(views.render_news(lang, r) for r in rows)
                await bot.send_message(g["id"], text)
                db.x("UPDATE groups SET last_news_id=?, last_post=? WHERE id=?", (rows[-1]["id"], core.now(), g["id"]))
        except TelegramRetryAfter:
            log.warning("flood control while posting boards; will retry next cycle")
            return
        except TelegramForbiddenError:
            social.deactivate_group(db, g["id"])
        except TelegramBadRequest as e:
            log.warning("board failed in %s: %s", g["id"], e)
            fails = g["board_fail"] + 1
            if fails >= 3:
                db.x("UPDATE groups SET news_mode='off', board_fail=0, board_msg=NULL WHERE id=?", (g["id"],))
            else:
                db.x("UPDATE groups SET board_fail=?, board_msg=NULL, board_hash='' WHERE id=?", (fails, g["id"]))
        except Exception:
            log.exception("board error")


async def _loop(name: str, seconds: int, fn, app: App) -> None:
    await asyncio.sleep(5)
    n = 0
    while True:
        try:
            await fn(app)
            n += 1
            if name == "slow" and n % 12 == 0:
                events.prune(app.db)
        except asyncio.CancelledError:
            raise
        except Exception as e:
            log.exception("%s job failed", name)
            await app.alert_admins(f"Background job '{name}' failed: {type(e).__name__}: {e}", key=f"job:{name}", every=1800)
        await asyncio.sleep(seconds)


def start_jobs(app: App) -> list:
    tasks = [
        asyncio.create_task(_loop("fast", 20, fast_tick, app), name="fast"),
        asyncio.create_task(_loop("slow", 300, slow_tick, app), name="slow"),
    ]
    tasks.append(asyncio.create_task(_loop("exchange", 60, exchange_tick, app), name="exchange"))
    tasks.append(asyncio.create_task(_loop("salary", 300, salary_tick, app), name="salary"))
    tasks.append(asyncio.create_task(_loop("backup", min(backups.CHECK_EVERY, backups.interval(app.settings)), backup_tick, app), name="backup"))
    if app.settings.bots:
        tasks.append(asyncio.create_task(_loop("bots", 30, bot_tick, app), name="bots"))
    return tasks
