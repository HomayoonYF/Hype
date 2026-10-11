"""Automatic SQLite backups (safe while the bot is running)."""
from __future__ import annotations

import sqlite3
import time
from pathlib import Path

from .db import Database


def make_backup(db: Database, dest_dir: str, keep: int = 7) -> Path:
    """Write a consistent snapshot of the live database into dest_dir and keep only the newest `keep` files."""
    folder = Path(dest_dir)
    folder.mkdir(parents=True, exist_ok=True)
    dest = folder / f"hypemarket-{time.strftime('%Y%m%d-%H%M%S')}.db"
    n = 1
    while dest.exists():
        dest = folder / f"hypemarket-{time.strftime('%Y%m%d-%H%M%S')}-{n}.db"
        n += 1
    out = sqlite3.connect(str(dest))
    try:
        db.conn.backup(out)
    finally:
        out.close()
    old = sorted(folder.glob("hypemarket-*.db"))
    for p in old[:-max(1, keep)]:
        try:
            p.unlink()
        except OSError:
            pass
    return dest
