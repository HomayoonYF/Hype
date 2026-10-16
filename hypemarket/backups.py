"""Hourly database backups, verified and sent to every admin as a Telegram document.

* a consistent copy is taken with SQLite's online backup API (safe while the bot runs);
* the copy is checked (integrity_check + row counts) BEFORE it is sent, and the admins are alerted if it is bad;
* it is gzip-compressed; files over Telegram's bot upload limit are split into numbered parts;
* a local copy is kept too (BACKUP_DIR, newest BACKUP_KEEP files).
Restore with:  python restore.py <backup file>
"""
from __future__ import annotations

import gzip
import hashlib
import logging
import os
import sqlite3
import tempfile
import time
from glob import glob

from aiogram.types import BufferedInputFile

from . import core
from .db import SCHEMA_VERSION, Database

log = logging.getLogger("hypemarket.backup")
PART_LIMIT = 45 * 1024 * 1024        # Telegram bots may upload up to 50 MB per file


def verify(path: str) -> dict:
    """Open a backup file and make sure it is a healthy database. Returns a few counts, raises on any problem."""
    con = sqlite3.connect(path)
    try:
        if con.execute("PRAGMA integrity_check").fetchone()[0] != "ok":
            raise RuntimeError("integrity_check failed")
        return {
            "users": con.execute("SELECT COUNT(*) FROM users WHERE bot=0").fetchone()[0],
            "stocks": con.execute("SELECT COUNT(*) FROM stocks WHERE status='active'").fetchone()[0],
            "groups": con.execute("SELECT COUNT(*) FROM groups WHERE active=1").fetchone()[0],
            "schema": con.execute("PRAGMA user_version").fetchone()[0],
        }
    finally:
        con.close()


def make_backup(db: Database, directory: str = "", keep: int = 48) -> dict:
    """Take, verify and compress a backup. Returns {data, name, size, raw_size, sha, meta, path}."""
    fd, tmp = tempfile.mkstemp(suffix=".db")
    os.close(fd)
    try:
        db.backup_to(tmp)
        meta = verify(tmp)
        raw = open(tmp, "rb").read()
    finally:
        os.remove(tmp)
    data = gzip.compress(raw, 6)
    stamp = time.strftime("%Y%m%d-%H%M%S", time.gmtime(core.now()))
    name = f"hypemarket-{stamp}-UTC.db.gz"
    path = ""
    if directory:
        os.makedirs(directory, exist_ok=True)
        path = os.path.join(directory, name)
        with open(path, "wb") as f:
            f.write(data)
        for old in sorted(glob(os.path.join(directory, "hypemarket-*.db.gz")))[:-keep]:
            os.remove(old)
    return {"data": data, "name": name, "size": len(data), "raw_size": len(raw), "sha": hashlib.sha256(data).hexdigest(), "meta": meta, "path": path}


def split(data: bytes, limit: int = PART_LIMIT) -> list:
    return [data[i:i + limit] for i in range(0, len(data), limit)] or [b""]


def caption(b: dict, part: int = 1, parts: int = 1) -> str:
    m = b["meta"]
    extra = f"\n📦 part {part}/{parts}: join with  cat {b['name']}.part* > {b['name']}" if parts > 1 else ""
    return (f"💾 Hype Market backup\n🕒 {time.strftime('%Y-%m-%d %H:%M UTC', time.gmtime(core.now()))}\n"
            f"👤 {m['users']} players • 📈 {m['stocks']} stocks • 👥 {m['groups']} groups • schema {m['schema']}\n"
            f"🔐 sha256 {b['sha'][:16]}…  ({b['size'] / 1024:.0f} KB){extra}\nRestore: python restore.py {b['name']}")


async def send_backup(app, reason: str = "scheduled") -> dict:
    """Make a backup and deliver it to every admin. Never raises; failures are logged and the admins are alerted."""
    s = app.settings
    result = {"ok": False, "sent": 0, "failed": 0}
    try:
        b = make_backup(app.db, s.backup_dir, s.backup_keep)
    except Exception as e:
        log.exception("backup failed")
        await app.alert_admins(f"🚨 Backup FAILED ({type(e).__name__}: {e}). Your data is NOT being backed up right now.", key="backup-failed", every=900)
        return result
    result.update(ok=True, name=b["name"], size=b["size"], meta=b["meta"])
    if not s.admins:
        log.warning("no ADMIN_IDS configured: backup saved locally only (%s)", b["path"] or "not saved")
        return result
    parts = split(b["data"])
    for admin in s.admins:
        try:
            for i, part in enumerate(parts, 1):
                fname = b["name"] if len(parts) == 1 else f"{b['name']}.part{i}"
                await app.bot.send_document(admin, BufferedInputFile(part, fname), caption=caption(b, i, len(parts)), disable_notification=True)
            result["sent"] += 1
        except Exception as e:      # the admin never pressed /start, blocked the bot, flood control...
            result["failed"] += 1
            log.warning("could not send backup to admin %s: %s", admin, e)
    if result["failed"] and not result["sent"]:
        log.error("backup could not be delivered to ANY admin (each admin must open the bot and press /start once)")
    return result
