"""Entry point: wires settings, database, handlers and background jobs together."""
from __future__ import annotations

import asyncio
import logging

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
from . import bots, exchange, progress

log = logging.getLogger("hypemarket")

MENU = [
    ("daily", "جایزه روزانه", "Daily bonus"),
    ("market", "بازار", "Market"),
    ("portfolio", "پورتفوی", "Portfolio"),
    ("profile", "پروفایل", "Profile"),
    ("top", "برترین‌ها", "Top players"),
    ("skills", "مهارت‌ها", "Skills"),
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
    session = AiohttpSession(proxy=settings.proxy) if settings.proxy else None     # PROXY_URL in .env
    bot = Bot(settings.token, session=session, default=DefaultBotProperties(parse_mode=ParseMode.HTML, link_preview_is_disabled=True))
    me = await bot.get_me()
    app = App(settings=settings, db=db, bot=bot, botname=me.username or "")
    dp = Dispatcher()
    dp["app"] = app
    dp.include_router(router)
    await bot.set_my_commands([BotCommand(command=c, description=en) for c, fa, en in MENU])
    await bot.set_my_commands([BotCommand(command=c, description=fa) for c, fa, en in MENU], language_code="fa")
    if not settings.admins:
        log.error("ADMIN_IDS is empty: backups cannot be sent to anyone, and moderation/analytics alerts have no recipient. Set ADMIN_IDS in .env!")
    for admin in settings.admins:     # admins must have pressed /start in a private chat with the bot at least once
        try:
            await bot.send_message(admin, t("en", "backup.ready", min=settings.backup_every_min) + "\n" + t("fa", "backup.ready", min=settings.backup_every_min))
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
