"""SQLite access, schema setup, and small helpers shared by collectors."""

from __future__ import annotations

import re
import sqlite3
import unicodedata
from datetime import date, datetime, timezone
from importlib import resources
from pathlib import Path

SCHEMA_VERSION = 2


class SchemaError(RuntimeError):
    pass


def utcnow() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def norm_title(title: str) -> str:
    """Lowercase, strip accents, markup and punctuation, collapse whitespace."""
    t = unicodedata.normalize("NFKD", title or "")
    t = "".join(ch for ch in t if not unicodedata.combining(ch))
    t = re.sub(r"<[^>]+>", " ", t)            # html tags such as <i>
    t = re.sub(r"[^0-9a-zA-Z]+", " ", t).lower()
    return re.sub(r"\s+", " ", t).strip()


def connect(path: Path | str) -> sqlite3.Connection:
    if str(path) != ":memory:":
        Path(path).parent.mkdir(parents=True, exist_ok=True)
    con = sqlite3.connect(path)
    con.row_factory = sqlite3.Row
    con.execute("PRAGMA foreign_keys = ON")
    if str(path) != ":memory:":
        con.execute("PRAGMA journal_mode = WAL")
    return con


def schema_version(con: sqlite3.Connection) -> int | None:
    try:
        row = con.execute("SELECT value FROM meta WHERE key = 'schema_version'").fetchone()
        return int(row[0]) if row else None
    except sqlite3.OperationalError:
        return None


def init_schema(con: sqlite3.Connection) -> None:
    current = schema_version(con)
    if current is not None and current < SCHEMA_VERSION:
        raise SchemaError(f"Database is schema v{current}; import it with `pubtrack import-v1 DB --author ID`.")
    sql = resources.files("pubtrack").joinpath("schema.sql").read_text()
    con.executescript(sql)
    con.execute("INSERT OR REPLACE INTO meta(key, value) VALUES ('schema_version', ?)", (str(SCHEMA_VERSION),))
    con.commit()


def new_run(con: sqlite3.Connection, author_id: str, snapshot_date: str | None = None, origin: str = "sync") -> int:
    cur = con.execute(
        "INSERT INTO runs(author_id, started, snapshot_date, origin) VALUES (?, ?, ?, ?)",
        (author_id, utcnow(), snapshot_date or date.today().isoformat(), origin),
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


def run_author(con: sqlite3.Connection, run_id: int) -> str:
    return con.execute("SELECT author_id FROM runs WHERE run_id = ?", (run_id,)).fetchone()[0]


def latest_run(con: sqlite3.Connection, author_id: str, require: str | None = None) -> sqlite3.Row | None:
    where = "WHERE author_id = ?"
    if require:
        where += f" AND {require}_status = 'ok'"
    return con.execute(f"SELECT * FROM runs {where} ORDER BY snapshot_date DESC, run_id DESC LIMIT 1",
                       (author_id,)).fetchone()
