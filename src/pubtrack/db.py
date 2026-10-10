"""SQLite access, schema setup, and small helpers shared by collectors."""

from __future__ import annotations

import re
import shutil
import sqlite3
import unicodedata
from datetime import date, datetime, timezone
from importlib import resources
from pathlib import Path

SCHEMA_VERSION = 1


def utcnow() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def norm_title(title: str) -> str:
    """Lowercase, strip accents, markup and punctuation, collapse whitespace."""
    t = unicodedata.normalize("NFKD", title or "")
    t = "".join(ch for ch in t if not unicodedata.combining(ch))
    t = re.sub(r"<[^>]+>", " ", t)            # html tags such as <i>
    t = re.sub(r"[^0-9a-zA-Z]+", " ", t).lower()
    return re.sub(r"\s+", " ", t).strip()


def connect(path: Path) -> sqlite3.Connection:
    path.parent.mkdir(parents=True, exist_ok=True)
    con = sqlite3.connect(path)
    con.row_factory = sqlite3.Row
    con.execute("PRAGMA foreign_keys = ON")
    con.execute("PRAGMA journal_mode = WAL")   # dashboard can read while sync writes
    return con


def init_schema(con: sqlite3.Connection, db_path: Path | None = None) -> None:
    current = None
    try:
        row = con.execute("SELECT value FROM meta WHERE key = 'schema_version'").fetchone()
        current = int(row[0]) if row else None
    except sqlite3.OperationalError:
        pass
    if current is not None and current < SCHEMA_VERSION and db_path and db_path.exists():
        backup = db_path.with_name(f"{db_path.stem}_PREV_{date.today():%Y%m%d}{db_path.suffix}")
        shutil.copy2(db_path, backup)
    sql = resources.files("pubtrack").joinpath("schema.sql").read_text()
    con.executescript(sql)
    con.execute("INSERT OR REPLACE INTO meta(key, value) VALUES ('schema_version', ?)", (str(SCHEMA_VERSION),))
    con.commit()


def new_run(con: sqlite3.Connection, snapshot_date: str | None = None, origin: str = "sync") -> int:
    cur = con.execute(
        "INSERT INTO runs(started, snapshot_date, origin) VALUES (?, ?, ?)",
        (utcnow(), snapshot_date or date.today().isoformat(), origin),
    )
    con.commit()
    return int(cur.lastrowid)


def finish_run(con: sqlite3.Connection, run_id: int, **fields) -> None:
    fields["finished"] = utcnow()
    cols = ", ".join(f"{k} = ?" for k in fields)
    con.execute(f"UPDATE runs SET {cols} WHERE run_id = ?", (*fields.values(), run_id))
    con.commit()


def add_flag(con: sqlite3.Connection, run_id: int, kind: str, detail: str, work_id: int | None = None) -> None:
    con.execute("INSERT INTO flags(run_id, work_id, kind, detail) VALUES (?, ?, ?, ?)", (run_id, work_id, kind, detail))


def latest_run(con: sqlite3.Connection, require: str | None = None) -> sqlite3.Row | None:
    where = f"WHERE {require}_status = 'ok'" if require else ""
    return con.execute(f"SELECT * FROM runs {where} ORDER BY snapshot_date DESC, run_id DESC LIMIT 1").fetchone()
