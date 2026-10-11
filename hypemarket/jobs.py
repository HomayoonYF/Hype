"""Background loops: they keep the market alive while nobody is typing."""
from __future__ import annotations

import asyncio
import hashlib
import logging

from aiogram.exceptions import TelegramBadRequest, TelegramForbiddenError, TelegramRetryAfter

from . import bots, core, events, progress, quests, social, trading, views
from .handlers import App, flush_outbox
from .texts import t

log = logging.getLogger("hypemarket.jobs")


async def fast_tick(app: App) -> None:
    """Every ~20 s: chat activity -> dividends, price alerts, short liquidations, queued DMs."""
    counts, seen = app.swap_buffers()
    if counts or seen:
        social.flush_activity(app.db, counts, seen)
    quests.drain_events(app.db, None)          # quests finished by chatting etc. become DMs
    trading.check_alerts(app.db)
    trading.liquidate_shorts(app.db)
    await flush_outbox(app)


async def bot_tick(app: App) -> None:
    """Every ~30 s: the automated market traders make their small trades."""
    bots.tick(app.db)


async def slow_tick(app: App) -> None:
    """Every ~5 min: events, price snapshots, net worth + levels, seasons, group boards."""
    events.tick(app.db, app.settings.events)
    core.record_stale(app.db)                  # a price point for every live stock, so charts have no gaps
    progress.refresh_all_networth(app.db)
    social.refresh_group_scores(app.db)
    progress.end_season_if_due(app.db)
    await post_boards(app)
    await flush_outbox(app)


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
        except Exception:
            log.exception("%s job failed", name)
        await asyncio.sleep(seconds)


def start_jobs(app: App) -> list:
    tasks = [
        asyncio.create_task(_loop("fast", 20, fast_tick, app), name="fast"),
        asyncio.create_task(_loop("slow", 300, slow_tick, app), name="slow"),
    ]
    if app.settings.bots:
        tasks.append(asyncio.create_task(_loop("bots", 30, bot_tick, app), name="bots"))
    return tasks
