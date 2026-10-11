"""The committed data store: one CSV per table under data/.

The CSVs are the source of truth in the repository. Each run loads them into an in memory
SQLite database, works there with the same code as before, and writes them back sorted by
primary key so a weekly commit is a small, readable diff.
"""

from __future__ import annotations

import csv
import sqlite3
from contextlib import contextmanager
from pathlib import Path

from .db import connect, init_schema

TABLES = {  # table -> ORDER BY used for export
    "runs": "run_id",
    "works": "work_id",
    "work_snapshots": "run_id, work_id, source",
    "work_year_counts": "work_id, source, year",
    "profile_snapshots": "run_id",
    "profile_year_counts": "run_id, year",
    "work_impact": "run_id, work_id",
    "metrics": "run_id, source, metric",
    "overrides": "override_id",
    "flags": "run_id, kind, work_id, detail",
    "title_lookups": "author_id, gs_id",
}
FK_ORDER = list(TABLES)  # parents before children


def _cols(con: sqlite3.Connection, table: str) -> list[str]:
    return [r[1] for r in con.execute(f"PRAGMA table_info({table})")]


def _fmt(v):
    if v is None:
        return ""
    if isinstance(v, float):
        return repr(round(v, 10))
    return v


def load(store: Path) -> sqlite3.Connection:
    con = connect(":memory:")
    init_schema(con)
    con.execute("PRAGMA foreign_keys = OFF")
    for t in FK_ORDER:
        f = store / f"{t}.csv"
        if not f.exists():
            continue
        cols = _cols(con, t)
        with f.open(newline="", encoding="utf-8") as fh:
            rd = csv.DictReader(fh)
            missing = set(rd.fieldnames or []) - set(cols)
            if missing:
                raise ValueError(f"{f}: unknown columns {sorted(missing)}")
            use = [c for c in cols if c in (rd.fieldnames or [])]
            sql = f"INSERT INTO {t} ({', '.join(use)}) VALUES ({', '.join('?' * len(use))})"
            con.executemany(sql, ([None if r[c] == "" else r[c] for c in use] for r in rd))
    con.commit()
    con.execute("PRAGMA foreign_keys = ON")
    bad = con.execute("PRAGMA foreign_key_check").fetchall()
    if bad:
        raise ValueError(f"foreign key violations after loading {store}: {bad[:5]}")
    return con


def save(con: sqlite3.Connection, store: Path) -> list[Path]:
    store.mkdir(parents=True, exist_ok=True)
    out = []
    for t, order in TABLES.items():
        cols = _cols(con, t)
        f = store / f"{t}.csv"
        tmp = f.with_suffix(".csv.tmp")
        with tmp.open("w", newline="", encoding="utf-8") as fh:
            w = csv.writer(fh, lineterminator="\n")
            w.writerow(cols)
            for r in con.execute(f"SELECT {', '.join(cols)} FROM {t} ORDER BY {order}"):
                w.writerow([_fmt(v) for v in r])
        tmp.replace(f)
        out.append(f)
    return out


@contextmanager
def opened(store: Path, write: bool = False):
    con = load(store)
    try:
        yield con
        if write:
            save(con, store)
    finally:
        con.close()
