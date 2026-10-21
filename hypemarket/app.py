"""Entry point: wires settings, database, handlers and background jobs together."""
from __future__ import annotations

import asyncio
import logging
import os

from aiogram import Bot, Dispatcher
from aiogram.client.default import DefaultBotProperties
from aiogram.client.session.aiohttp import AiohttpSession
from aiogram.enums import ParseMode
from aiogram.types import BotCommand

from . import config as C
from .db import Database
from .handlers import App, router
from .texts import t
from .jobs import start_jobs
from . import backups, bots, exchange, progress, worldplaces

log = logging.getLogger("hypemarket")

MENU = [
    ("menu", "منوی بازی", "Game menu"),
    ("stocks", "بخش سهام", "Stocks"),
    ("daily", "جایزه روزانه", "Daily bonus"),
    ("market", "بازار", "Market"),
    ("portfolio", "پورتفوی", "Portfolio"),
    ("profile", "پروفایل", "Profile"),
    ("invite", "دعوت دوستان", "Invite friends"),
    ("help", "راهنما", "Help"),
]


async def main() -> None:
    settings = C.load_settings()
    logging.basicConfig(level=settings.log_level, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    if not settings.token:
        raise SystemExit("BOT_TOKEN is missing. Copy .env.example to .env and put your BotFather token in it.")
    db = Database(settings.db_path)
    progress.ensure_season(db)
    if settings.bots:
        bots.ensure_bots(db)
    exchange.ensure_listed(db)                 # the six house-priced companies (no-op once they exist)
    try:                                       # every saved city gets its permanent casino; nothing about a city is regenerated or moved (no-op once done)
        added = worldplaces.ensure_all(db)
        if added:
            log.info("casino added to %d saved cities", added)
    except Exception:
        log.exception("adding the casino to saved cities failed (each city gets it the first time its map is opened instead)")
    session = AiohttpSession(proxy=settings.proxy) if settings.proxy else None     # PROXY_URL in .env
    bot = Bot(settings.token, session=session, default=DefaultBotProperties(parse_mode=ParseMode.HTML, link_preview_is_disabled=True))
    me = await bot.get_me()
    app = App(settings=settings, db=db, bot=bot, botname=me.username or "")
    dp = Dispatcher()
    dp["app"] = app
    dp.include_router(router)
    await bot.set_my_commands([BotCommand(command=c, description=en) for c, fa, en in MENU])
    await bot.set_my_commands([BotCommand(command=c, description=fa) for c, fa, en in MENU], language_code="fa")
    if os.environ.get("BACKUP_EVERY_MIN"):
        log.warning("BACKUP_EVERY_MIN in .env is no longer used: backups follow BACKUP_EVERY_HOURS (default 24).")
    if not settings.admins:
        log.error("ADMIN_IDS is empty: backups cannot be sent to anyone, and moderation/analytics alerts have no recipient. Set ADMIN_IDS in .env!")
    for admin in settings.admins:     # admins must have pressed /start in a private chat with the bot at least once
        try:
            await bot.send_message(admin, backups.ready_text(db, settings))
        except Exception as e:
            log.warning("could not message admin %s (%s): open the bot in a private chat and press /start so backups can reach you", admin, e)
    tasks = start_jobs(app)
    log.info("Bot @%s is running (privacy mode must be OFF in BotFather for chat-activity income).", app.botname)
    try:
        await dp.start_polling(bot, allowed_updates=["message", "callback_query", "my_chat_member", "pre_checkout_query"])
    finally:
        for task in tasks:
            task.cancel()
        await bot.session.close()
        db.close()


def run() -> None:
    try:
        asyncio.run(main())
    except (KeyboardInterrupt, SystemExit) as e:
        if isinstance(e, SystemExit) and e.code:
            raise
