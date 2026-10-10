"""Manual corrections, recorded once and reapplied on every run (idempotent)."""

from __future__ import annotations

from .db import utcnow
from .matching import merge_pair

ACTIONS = ("merge", "exclude", "include", "set_year", "set_doi", "set_openalex")
PRE = ("set_doi", "set_openalex")          # before automatic matching
POST = ("merge", "exclude", "include", "set_year")


def add(con, action: str, gs_id: str, target: str | None = None, note: str | None = None) -> int:
    if action not in ACTIONS:
        raise ValueError(f"action must be one of {ACTIONS}")
    if action in ("merge", "set_year", "set_doi", "set_openalex") and not target:
        raise ValueError(f"{action} needs a target")
    cur = con.execute(
        "INSERT INTO overrides(created, action, gs_id, target, note) VALUES (?, ?, ?, ?, ?)",
        (utcnow(), action, gs_id, target, note),
    )
    con.commit()
    return int(cur.lastrowid)


def _work_id(con, gs_id):
    r = con.execute("SELECT work_id FROM works WHERE gs_id = ?", (gs_id,)).fetchone()
    return r["work_id"] if r else None


def apply(con, phase: str, run_id: int | None = None) -> list[str]:
    from .db import add_flag

    actions = PRE if phase == "pre" else POST
    notes = []
    rows = con.execute(
        f"SELECT * FROM overrides WHERE active = 1 AND action IN ({','.join('?' * len(actions))}) ORDER BY override_id",
        actions,
    ).fetchall()
    for o in rows:
        wid = _work_id(con, o["gs_id"])
        if wid is None:
            notes.append(f"override {o['override_id']}: {o['gs_id']} not on profile this run")
            continue
        a, t = o["action"], o["target"]
        if a == "exclude":
            con.execute("UPDATE works SET excluded = 1 WHERE work_id = ?", (wid,))
        elif a == "include":
            con.execute("UPDATE works SET excluded = 0 WHERE work_id = ?", (wid,))
        elif a == "set_year":
            con.execute("UPDATE works SET year = ? WHERE work_id = ?", (int(t), wid))
        elif a == "set_doi":
            con.execute("UPDATE works SET doi = ? WHERE work_id = ?", (t.lower().replace("https://doi.org/", ""), wid))
        elif a == "merge":
            tid = _work_id(con, t)
            if tid is None:
                notes.append(f"override {o['override_id']}: merge target {t} not found")
                continue
            con.execute("UPDATE works SET merged_into = ? WHERE work_id = ?", (tid, wid))
        elif a == "set_openalex":
            cur = con.execute("SELECT openalex_id FROM works WHERE work_id = ?", (wid,)).fetchone()["openalex_id"]
            if cur == t:
                continue
            oa = con.execute("SELECT work_id, gs_id FROM works WHERE openalex_id = ?", (t,)).fetchone()
            if oa is None:
                notes.append(f"override {o['override_id']}: OpenAlex {t} not fetched yet")
            elif oa["gs_id"] is None:
                merge_pair(con, wid, oa["work_id"], "override", None)
            else:
                notes.append(f"override {o['override_id']}: {t} already linked to {oa['gs_id']}")
                if run_id:
                    add_flag(con, run_id, "override_conflict", notes[-1], wid)
    con.commit()
    return notes
