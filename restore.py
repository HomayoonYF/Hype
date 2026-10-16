"""Restore a backup:   python restore.py hypemarket-20261011-120000-UTC.db.gz

Stop the bot first. The current database (if any) is kept next to it as <name>.before-restore-<time>.
Works with .db.gz, .db, or the numbered parts of a split backup (pass them in order: file.part1 file.part2 ...).
"""
import gzip
import os
import shutil
import sqlite3
import sys
import time


def restore(files: list, target: str) -> dict:
    raw = b"".join(open(f, "rb").read() for f in files)
    if raw[:2] == b"\x1f\x8b":
        raw = gzip.decompress(raw)
    tmp = target + ".restoring"
    with open(tmp, "wb") as f:
        f.write(raw)
    try:
        con = sqlite3.connect(tmp)
        if con.execute("PRAGMA integrity_check").fetchone()[0] != "ok":
            raise SystemExit("This backup file is damaged (integrity check failed). Nothing was changed.")
        info = {"users": con.execute("SELECT COUNT(*) FROM users").fetchone()[0], "stocks": con.execute("SELECT COUNT(*) FROM stocks").fetchone()[0]}
        con.close()
    except sqlite3.DatabaseError as e:
        os.remove(tmp)
        raise SystemExit(f"This is not a valid backup ({e}). Nothing was changed.")
    for ext in ("-wal", "-shm"):                       # stale journal files of the old database must not be replayed
        if os.path.exists(target + ext):
            os.remove(target + ext)
    if os.path.exists(target):
        shutil.move(target, f"{target}.before-restore-{time.strftime('%Y%m%d-%H%M%S')}")
    shutil.move(tmp, target)
    return info


if __name__ == "__main__":
    if len(sys.argv) < 2:
        raise SystemExit(__doc__)
    from hypemarket.config import load_settings
    target = load_settings().db_path
    info = restore(sys.argv[1:], target)
    print(f"Restored {info['users']} players and {info['stocks']} stocks into {target}. Start the bot again.")
