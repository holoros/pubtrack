"""One sync: Scholar, then OpenAlex, then matching, overrides, checks and metrics.

A Scholar failure never stops the run; OpenAlex still writes its snapshot.
"""

from __future__ import annotations

import logging
from datetime import date

from . import matching, metrics, openalex, overrides, scholar
from .config import Config
from .db import add_flag, finish_run, new_run

log = logging.getLogger("pubtrack")

RATIO_HI, RATIO_LO = 5.0, 0.5
DROP_ABS, DROP_REL = 3, 0.05


def run_checks(con, run_id: int, scholar_raw: dict | None) -> list[str]:
    msgs = []
    # 1. Local h from raw Scholar counts must equal Scholar's reported h.
    if scholar_raw:
        local_h = metrics.h_index([w["citations"] for w in scholar_raw["works"]])
        rep = scholar_raw["profile"].get("h")
        if rep is not None and local_h != rep:
            m = f"local h {local_h} from profile list differs from reported h {rep}"
            add_flag(con, run_id, "h_mismatch", m)
            msgs.append(m)

    # 2. Scholar to OpenAlex ratio on linked papers.
    rows = con.execute(
        "SELECT w.work_id, w.title, s.citations AS gs, o.citations AS oa FROM works w "
        "JOIN work_snapshots s ON s.work_id = w.work_id AND s.source = 'scholar' AND s.run_id = ? "
        "JOIN work_snapshots o ON o.work_id = w.work_id AND o.source = 'openalex' AND o.run_id = ? "
        "WHERE w.excluded = 0", (run_id, run_id)).fetchall()
    for r in rows:
        if r["oa"] >= 5 or r["gs"] >= 5:
            ratio = (r["gs"] + 0.5) / (r["oa"] + 0.5)
            if ratio > RATIO_HI or ratio < RATIO_LO:
                add_flag(con, run_id, "ratio", f"Scholar {r['gs']} vs OpenAlex {r['oa']}: {r['title'][:80]}", r["work_id"])

    # 3. Citation drops against the previous successful Scholar run.
    prev = con.execute(
        "SELECT run_id FROM runs WHERE scholar_status = 'ok' AND run_id < ? ORDER BY run_id DESC LIMIT 1",
        (run_id,)).fetchone()
    if prev and scholar_raw:
        drops = con.execute(
            "SELECT w.work_id, w.title, p.citations AS before, c.citations AS now FROM work_snapshots c "
            "JOIN work_snapshots p ON p.work_id = c.work_id AND p.source = 'scholar' AND p.run_id = ? "
            "JOIN works w ON w.work_id = c.work_id WHERE c.source = 'scholar' AND c.run_id = ? "
            "AND c.citations < p.citations", (prev["run_id"], run_id)).fetchall()
        for d in drops:
            lost = d["before"] - d["now"]
            if lost > DROP_ABS or lost > DROP_REL * d["before"]:
                add_flag(con, run_id, "drop", f"{d['before']} to {d['now']}: {d['title'][:80]}", d["work_id"])

    # 4. Scholar works still unmatched to OpenAlex.
    for r in con.execute("SELECT work_id, title FROM works WHERE gs_id IS NOT NULL AND openalex_id IS NULL "
                         "AND excluded = 0 AND merged_into IS NULL").fetchall():
        add_flag(con, run_id, "unmatched", r["title"][:120], r["work_id"])
    con.commit()
    return msgs


def sync(con, cfg: Config, sources=("scholar", "openalex"), scholar_via: str = "scholarly",
         snapshot_date: str | None = None) -> dict:
    today = snapshot_date or date.today().isoformat()
    run_id = new_run(con, today)
    status = {"run_id": run_id, "scholar_status": "skipped", "openalex_status": "skipped"}
    messages: list[str] = []
    raw = None

    if "scholar" in sources:
        if not cfg.scholar_id:
            status["scholar_status"] = "failed"
            messages.append("scholar_id not set in config")
        else:
            try:
                if scholar_via == "serpapi":
                    raw = scholar.via_serpapi(cfg.scholar_id, cfg.serpapi_key)
                else:
                    raw = scholar.via_scholarly(cfg.scholar_id, cfg.min_delay, cfg.max_delay)
                status["n_scholar"] = scholar.store(con, run_id, raw, today)
                status["scholar_status"] = "ok"
            except Exception as e:
                status["scholar_status"] = "failed"
                messages.append(f"Scholar: {e}")
                log.warning("Scholar fetch failed: %s", e)

    if "openalex" in sources:
        ids = [x.strip() for x in cfg.openalex_id.split(",") if x.strip()]
        if not ids:
            status["openalex_status"] = "failed"
            messages.append("openalex_id not set; run `pubtrack resolve`")
        else:
            try:
                works = openalex.fetch_works(ids, cfg.mailto, cfg.openalex_key)
                status["n_openalex"] = openalex.store(con, run_id, works, ids, cfg.surname, today)
                status["openalex_status"] = "ok"
            except Exception as e:
                status["openalex_status"] = "failed"
                messages.append(f"OpenAlex: {e}")
                log.warning("OpenAlex fetch failed: %s", e)

    messages += overrides.apply(con, "pre", run_id)
    status["matching"] = matching.reconcile(con)
    messages += overrides.apply(con, "post", run_id)
    messages += run_checks(con, run_id, raw)

    for src in ("scholar", "openalex"):
        if status[f"{src}_status"] == "ok":
            vals = metrics.compute(con, run_id, src, cfg.first_year)
            if src == "openalex":
                ir = metrics.interim_rate(con, run_id)
                if ir is not None:
                    vals["interim_rate_per_year"] = ir
            metrics.store(con, run_id, src, vals)
            status[f"metrics_{src}"] = vals

    finish_run(con, run_id,
               scholar_status=status["scholar_status"], openalex_status=status["openalex_status"],
               n_scholar=status.get("n_scholar"), n_openalex=status.get("n_openalex"),
               message="; ".join(messages) or None)
    status["messages"] = messages
    return status
