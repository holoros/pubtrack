"""Sync one author (Scholar, then OpenAlex, then matching, overrides, checks and metrics),
and plan a multi author refresh that respects each author's cadence and the SerpAPI budget.

A Scholar failure never stops a run; OpenAlex still writes its snapshot.
"""

from __future__ import annotations

import logging
import math
from dataclasses import dataclass
from datetime import date, datetime, timezone

from . import matching, metrics, openalex, overrides, scholar
from .config import CADENCE_DAYS, Author, Config
from .db import add_flag, finish_run, new_run

log = logging.getLogger("pubtrack")

RATIO_HI, RATIO_LO = 5.0, 0.5
DROP_ABS, DROP_REL = 3, 0.05
NEW_AUTHOR_SERPAPI_GUESS = 5


def run_checks(con, run_id: int, author_id: str, scholar_raw: dict | None) -> list[str]:
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

    # 3. Citation drops against the previous successful Scholar run for this author.
    prev = con.execute(
        "SELECT run_id FROM runs WHERE author_id = ? AND scholar_status = 'ok' AND run_id < ? "
        "ORDER BY run_id DESC LIMIT 1", (author_id, run_id)).fetchone()
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
    for r in con.execute("SELECT work_id, title FROM works WHERE author_id = ? AND gs_id IS NOT NULL "
                         "AND openalex_id IS NULL AND excluded = 0 AND merged_into IS NULL",
                         (author_id,)).fetchall():
        add_flag(con, run_id, "unmatched", r["title"][:120], r["work_id"])
    con.commit()
    return msgs


def sync(con, cfg: Config, author: Author, sources=("scholar", "openalex"), scholar_via: str | None = None,
         snapshot_date: str | None = None) -> dict:
    s = cfg.settings
    via = scholar_via or s.scholar_via
    today = snapshot_date or date.today().isoformat()
    run_id = new_run(con, author.id, today)
    status = {"run_id": run_id, "author_id": author.id, "scholar_status": "skipped", "openalex_status": "skipped"}
    messages: list[str] = []
    raw = None
    calls = None

    if "scholar" in sources and via != "none":
        if not author.scholar_id:
            messages.append("scholar_id not set")
        else:
            try:
                if via == "serpapi":
                    raw = scholar.via_serpapi(author.scholar_id, s.serpapi_key)
                    calls = raw.get("serpapi_calls")
                else:
                    raw = scholar.via_scholarly(author.scholar_id, s.min_delay, s.max_delay)
                status["n_scholar"] = scholar.store(con, run_id, raw, today)
                status["scholar_status"] = "ok"
            except Exception as e:
                status["scholar_status"] = "failed"
                calls = getattr(e, "serpapi_calls", calls)
                messages.append(f"Scholar: {e}")
                log.warning("%s: Scholar fetch failed: %s", author.id, e)

    if "openalex" in sources:
        ids = author.openalex_ids
        if not ids:
            status["openalex_status"] = "failed"
            messages.append("openalex_id not set; run `pubtrack resolve`")
        else:
            try:
                works = openalex.fetch_works(ids, s.mailto, s.openalex_key)
                status["n_openalex"] = openalex.store(con, run_id, works, ids, author.surname, today)
                status["openalex_status"] = "ok"
            except Exception as e:
                status["openalex_status"] = "failed"
                messages.append(f"OpenAlex: {e}")
                log.warning("%s: OpenAlex fetch failed: %s", author.id, e)

    messages += overrides.apply(con, author.id, "pre", run_id)
    status["matching"] = matching.reconcile(con, author.id)
    messages += overrides.apply(con, author.id, "post", run_id)
    messages += run_checks(con, run_id, author.id, raw)

    for src in ("scholar", "openalex"):
        if status[f"{src}_status"] == "ok":
            vals = metrics.compute(con, run_id, src, author.first_year)
            if src == "openalex":
                ir = metrics.interim_rate(con, run_id)
                if ir is not None:
                    vals["interim_rate_per_year"] = ir
            metrics.store(con, run_id, src, vals)
            status[f"metrics_{src}"] = vals

    finish_run(con, run_id,
               scholar_status=status["scholar_status"], openalex_status=status["openalex_status"],
               scholar_via=via if status["scholar_status"] != "skipped" else None, serpapi_calls=calls,
               n_scholar=status.get("n_scholar"), n_openalex=status.get("n_openalex"),
               message="; ".join(messages) or None)
    status["messages"] = messages
    return status


# ---------------------------------------------------------------- planning a refresh

@dataclass
class Plan:
    author: Author
    scholar: bool
    openalex: bool
    serpapi_estimate: int
    reason: str


def _days_since_ok(con, author_id: str, src: str, today: date) -> int | None:
    r = con.execute(f"SELECT MAX(snapshot_date) FROM runs WHERE author_id = ? AND {src}_status = 'ok' "
                    "AND origin = 'sync'", (author_id,)).fetchone()[0]
    return None if r is None else (today - date.fromisoformat(r)).days


def serpapi_used_this_month(con, now: datetime | None = None) -> int:
    now = now or datetime.now(timezone.utc)
    month = now.strftime("%Y-%m")
    return int(con.execute("SELECT COALESCE(SUM(serpapi_calls), 0) FROM runs WHERE substr(started, 1, 7) = ?",
                           (month,)).fetchone()[0])


def serpapi_estimate(con, author_id: str) -> int:
    r = con.execute("SELECT n_scholar FROM runs WHERE author_id = ? AND scholar_status = 'ok' "
                    "ORDER BY run_id DESC LIMIT 1", (author_id,)).fetchone()
    if not r or not r[0]:
        return NEW_AUTHOR_SERPAPI_GUESS
    return max(1, math.ceil(r[0] / 100))


def plan(con, cfg: Config, only: list[str] | None = None, force_scholar=False, force_openalex=False,
         today: date | None = None, scholar_via: str | None = None) -> list[Plan]:
    """Decide what each author gets this run. Scholar via SerpAPI is admitted in order of
    staleness until the monthly budget would be exceeded; the rest are deferred, never failed."""
    today = today or date.today()
    via = scholar_via or cfg.settings.scholar_via
    oa_due_days = CADENCE_DAYS.get(cfg.settings.openalex_cadence, 6)
    plans = []
    for a in cfg.authors:
        if only and a.id not in only:
            continue
        d_oa = _days_since_ok(con, a.id, "openalex", today)
        oa = bool(a.openalex_ids) and (force_openalex or d_oa is None or d_oa >= oa_due_days)
        due_days = CADENCE_DAYS[a.scholar_cadence]
        d_gs = _days_since_ok(con, a.id, "scholar", today)
        gs = (via != "none" and bool(a.scholar_id) and due_days is not None
              and (force_scholar or d_gs is None or d_gs >= due_days))
        plans.append(Plan(a, gs, oa, serpapi_estimate(con, a.id) if gs and via == "serpapi" else 0,
                          f"scholar last ok {d_gs} d ago, openalex last ok {d_oa} d ago"))
    if via == "serpapi":
        left = cfg.settings.serpapi_monthly_budget - serpapi_used_this_month(con)
        stale_first = sorted((p for p in plans if p.scholar),
                             key=lambda p: -(_days_since_ok(con, p.author.id, "scholar", today) or 10_000))
        for p in stale_first:
            if p.serpapi_estimate <= left:
                left -= p.serpapi_estimate
            else:
                p.scholar = False
                p.reason += "; Scholar deferred: SerpAPI monthly budget"
    return plans


def sync_all(con, cfg: Config, plans: list[Plan], scholar_via: str | None = None,
             snapshot_date: str | None = None) -> list[dict]:
    out = []
    for p in plans:
        sources = tuple(s for s, on in (("scholar", p.scholar), ("openalex", p.openalex)) if on)
        if not sources:
            log.info("%s: nothing due (%s)", p.author.id, p.reason)
            continue
        log.info("%s: syncing %s", p.author.id, "+".join(sources))
        st = sync(con, cfg, p.author, sources=sources, scholar_via=scholar_via, snapshot_date=snapshot_date)
        if "deferred" in p.reason:
            con.execute("UPDATE runs SET scholar_status = 'deferred' WHERE run_id = ?", (st["run_id"],))
            con.commit()
        out.append(st)
    return out
