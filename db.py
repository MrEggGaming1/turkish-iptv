"""SQLite cache: settings, catalogue, my channels, EPG."""
import json
import os
import sqlite3
import sys
import time
from contextlib import contextmanager
from pathlib import Path


def data_dir():
    if sys.platform == "darwin":
        base = Path.home() / "Library" / "Application Support" / "XtreamTV"
    else:
        base = Path(os.environ.get("XDG_DATA_HOME", Path.home() / ".local" / "share")) / "xtreamtv"
    base.mkdir(parents=True, exist_ok=True)
    return base


DB_PATH = data_dir() / "cache.db"
BACKUP_PATH = data_dir() / "state-backup.json"
RECOVERED = False  # True when the database had to be rebuilt after damage (e.g. a power cut)

SCHEMA = """
CREATE TABLE IF NOT EXISTS settings (key TEXT PRIMARY KEY, value TEXT);
CREATE TABLE IF NOT EXISTS categories (id TEXT PRIMARY KEY, name TEXT);
CREATE TABLE IF NOT EXISTS channels (
    stream_id INTEGER PRIMARY KEY, name TEXT, category_id TEXT, icon TEXT,
    epg_id TEXT, tv_archive INTEGER DEFAULT 0, missing INTEGER DEFAULT 0);
CREATE TABLE IF NOT EXISTS my_channels (stream_id INTEGER PRIMARY KEY, position INTEGER);
CREATE TABLE IF NOT EXISTS programmes (
    key TEXT, start INTEGER, stop INTEGER, title TEXT, descr TEXT);
CREATE INDEX IF NOT EXISTS idx_prog ON programmes(key, start);
"""


@contextmanager
def db():
    con = sqlite3.connect(DB_PATH, timeout=30)
    con.row_factory = sqlite3.Row
    con.execute("PRAGMA synchronous=FULL")  # a committed write survives a sudden power cut
    try:
        yield con
        con.commit()
    finally:
        con.close()


def _fsync_write(path, text):
    """Write a file so that it is either the old or the new version after a power cut, never half-written."""
    tmp = path.with_name(path.name + ".tmp")
    with open(tmp, "w", encoding="utf-8") as f:
        f.write(text)
        f.flush()
        os.fsync(f.fileno())
    os.replace(tmp, path)
    try:
        fd = os.open(path.parent, os.O_RDONLY)
        try:
            os.fsync(fd)
        finally:
            os.close(fd)
    except OSError:
        pass


def backup_state():
    """Small JSON copy of the settings + chosen channels, used to rebuild after database damage."""
    try:
        with db() as c:
            settings = {r["key"]: r["value"] for r in c.execute("SELECT key,value FROM settings")}
            mine = [r["stream_id"] for r in c.execute("SELECT stream_id FROM my_channels ORDER BY position")]
        _fsync_write(BACKUP_PATH, json.dumps({"settings": settings, "my_channels": mine}))
    except Exception:
        pass


def _is_corrupt(path):
    try:
        con = sqlite3.connect(path, timeout=10)
        try:
            row = con.execute("PRAGMA quick_check").fetchone()
        finally:
            con.close()
        return not row or row[0] != "ok"
    except sqlite3.Error:
        return True


def _recover():
    if not DB_PATH.exists() or not _is_corrupt(DB_PATH):
        return False
    stamp = time.strftime("%Y%m%d-%H%M%S")
    for suffix in ("", "-wal", "-shm"):
        p = Path(str(DB_PATH) + suffix)
        if p.exists():
            try:
                p.rename(DB_PATH.with_name(f"cache.db{suffix}.corrupt-{stamp}"))
            except OSError:
                p.unlink(missing_ok=True)
    for old in sorted(DB_PATH.parent.glob("cache.db.corrupt-*"))[:-2]:  # keep the two newest
        old.unlink(missing_ok=True)
    return True


def _restore_backup():
    try:
        data = json.loads(BACKUP_PATH.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return
    with db() as c:
        c.executemany("INSERT OR REPLACE INTO settings VALUES (?,?)",
                      [(k, str(v)) for k, v in data.get("settings", {}).items()])
        c.execute("DELETE FROM my_channels")
        c.executemany("INSERT INTO my_channels VALUES (?,?)",
                      [(sid, i) for i, sid in enumerate(data.get("my_channels", []))])
        c.execute("INSERT OR REPLACE INTO settings VALUES ('recovered_at', ?)", (str(int(time.time())),))


def init():
    """Open the database; if it was damaged, set it aside and rebuild settings from the backup."""
    global RECOVERED
    RECOVERED = _recover()
    with db() as c:
        c.execute("PRAGMA journal_mode=WAL")
        c.executescript(SCHEMA)
    if RECOVERED:
        _restore_backup()
        backup_state()
    return RECOVERED


def healthy():
    return not _is_corrupt(DB_PATH)


def get(key, default=None):
    with db() as c:
        r = c.execute("SELECT value FROM settings WHERE key=?", (key,)).fetchone()
    return r["value"] if r else default


def put(key, value):
    with db() as c:
        c.execute("INSERT OR REPLACE INTO settings VALUES (?,?)", (key, str(value)))
    backup_state()


def _int(v):
    try:
        return int(v)
    except (TypeError, ValueError):
        return 0


def epg_key(ch):
    return ch["epg_id"] or f"sid:{ch['stream_id']}"


def replace_catalog(cats, streams):
    crow = [(str(x.get("category_id")), x.get("category_name") or "") for x in cats]
    srow = [(_int(s.get("stream_id")), s.get("name") or "", str(s.get("category_id") or ""),
             s.get("stream_icon") or "", s.get("epg_channel_id") or "", _int(s.get("tv_archive")))
            for s in streams if s.get("stream_id") is not None]
    with db() as c:
        c.execute("DELETE FROM categories")
        c.executemany("INSERT OR REPLACE INTO categories VALUES (?,?)", crow)
        c.execute("UPDATE channels SET missing=1")
        c.executemany(
            """INSERT INTO channels(stream_id,name,category_id,icon,epg_id,tv_archive,missing)
               VALUES (?,?,?,?,?,?,0)
               ON CONFLICT(stream_id) DO UPDATE SET name=excluded.name,
               category_id=excluded.category_id, icon=excluded.icon,
               epg_id=excluded.epg_id, tv_archive=excluded.tv_archive, missing=0""", srow)


def categories():
    with db() as c:
        return [dict(r) for r in c.execute("SELECT * FROM categories ORDER BY name COLLATE NOCASE")]


def find_channels(cat_id=None, text="", limit=1500):
    sql = ("SELECT c.*, cat.name AS cat_name FROM channels c "
           "LEFT JOIN categories cat ON cat.id=c.category_id WHERE c.missing=0")
    args = []
    if cat_id:
        sql += " AND c.category_id=?"
        args.append(cat_id)
    if text:
        sql += " AND c.name LIKE ?"
        args.append(f"%{text}%")
    sql += " ORDER BY c.name COLLATE NOCASE LIMIT ?"
    args.append(limit)
    with db() as c:
        return [dict(r) for r in c.execute(sql, args)]


def my_channels(include_missing=False):
    sql = ("SELECT c.* FROM my_channels m JOIN channels c ON c.stream_id=m.stream_id")
    if not include_missing:
        sql += " WHERE c.missing=0"
    sql += " ORDER BY m.position"
    with db() as c:
        rows = [dict(r) for r in c.execute(sql)]
    for i, r in enumerate(rows):
        r["number"] = i + 1
    return rows


def set_my_channels(ids):
    with db() as c:
        c.execute("DELETE FROM my_channels")
        c.executemany("INSERT INTO my_channels VALUES (?,?)", [(sid, i) for i, sid in enumerate(ids)])
    backup_state()


def replace_epg(rows):
    with db() as c:
        c.execute("DELETE FROM programmes")
        c.executemany("INSERT INTO programmes VALUES (?,?,?,?,?)", rows)


def programmes(keys, start, end):
    out = {k: [] for k in keys}
    with db() as c:
        for r in c.execute("SELECT key,start,stop,title,descr FROM programmes "
                           "WHERE stop>? AND start<? ORDER BY start", (start, end)):
            if r["key"] in out:
                out[r["key"]].append((r["start"], r["stop"], r["title"], r["descr"]))
    return out


def now_next(key, t=None):
    t = t or time.time()
    with db() as c:
        rows = c.execute("SELECT start,stop,title,descr FROM programmes WHERE key=? AND stop>? "
                         "ORDER BY start LIMIT 2", (key, t)).fetchall()
    rows = [tuple(r) for r in rows]
    if not rows:
        return None, None
    if rows[0][0] <= t:
        return rows[0], (rows[1] if len(rows) > 1 else None)
    return None, rows[0]


def select_by_terms(terms, limit=500):
    """Pick every channel whose category name contains one of the terms. Returns how many were chosen."""
    terms = [t.strip() for t in terms if t.strip()]
    if not terms:
        return 0
    sql = ("SELECT c.stream_id FROM channels c JOIN categories cat ON cat.id=c.category_id "
           "WHERE c.missing=0 AND (" + " OR ".join("cat.name LIKE ?" for _ in terms) +
           ") ORDER BY cat.name COLLATE NOCASE, c.name COLLATE NOCASE LIMIT ?")
    with db() as c:
        ids = [r["stream_id"] for r in c.execute(sql, [f"%{t}%" for t in terms] + [limit])]
    if ids:
        set_my_channels(ids)
    return len(ids)


def checkpoint():
    """Fold the write-ahead log into the main database file (safe before a power cut)."""
    try:
        with db() as c:
            c.execute("PRAGMA wal_checkpoint(TRUNCATE)")
    except sqlite3.Error:
        pass
