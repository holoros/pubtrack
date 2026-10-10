"""CSV paper table, annual summary, and Publish or Perish import."""

from __future__ import annotations

import csv
from datetime import date, datetime
from pathlib import Path

from rapidfuzz import fuzz

from . import metrics
from .db import finish_run, latest_run, new_run, norm_title

PAPER_COLS = ["work_id", "title", "year", "venue", "doi", "gs_id", "openalex_id", "n_authors",
              "author_position", "is_last_author", "scholar_citations", "openalex_citations",
              "scholar_velocity_per_year", "fwci", "percentile", "top10", "top1", "match_method", "excluded"]


def paper_rows(con, author_id: str) -> list[dict]:
    rs = latest_run(con, author_id, "scholar")
    ro = latest_run(con, author_id, "openalex")
    rs_id = rs["run_id"] if rs else -1
    ro_id = ro["run_id"] if ro else -1
    rows = con.execute(
        "SELECT w.*, s.citations AS scholar_citations, o.citations AS openalex_citations, "
        "i.fwci, i.pct_value AS percentile, i.top10, i.top1 FROM works w "
        "LEFT JOIN work_snapshots s ON s.work_id = w.work_id AND s.source = 'scholar' AND s.run_id = ? "
        "LEFT JOIN work_snapshots o ON o.work_id = w.work_id AND o.source = 'openalex' AND o.run_id = ? "
        "LEFT JOIN work_impact i ON i.work_id = w.work_id AND i.run_id = ? "
        "WHERE w.author_id = ? AND w.merged_into IS NULL ORDER BY s.citations DESC NULLS LAST, o.citations DESC",
        (rs_id, ro_id, ro_id, author_id)).fetchall()
    out = []
    for r in rows:
        d = dict(r)
        hist = con.execute(
            "SELECT r.snapshot_date, s.citations FROM work_snapshots s JOIN runs r USING (run_id) "
            "WHERE s.work_id = ? AND s.source = 'scholar' AND r.snapshot_date > date('now', '-365 days') "
            "ORDER BY r.snapshot_date", (r["work_id"],)).fetchall()
        d["scholar_velocity_per_year"] = (
            metrics.ols_slope_per_year([h[0] for h in hist], [h[1] for h in hist])
            if len(hist) >= metrics.MIN_VELOCITY_POINTS else None)
        out.append({k: d.get(k) for k in PAPER_COLS})
    return out


def write_csv(rows: list[dict], path: Path) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=PAPER_COLS)
        w.writeheader()
        w.writerows(rows)
    return path


def annual_summary(con, author_id: str, year: int, out_dir: Path) -> list[Path]:
    """Two CSVs for activity reports: indices at the latest run, and the year's papers and citations."""
    rs = latest_run(con, author_id, "scholar")
    ro = latest_run(con, author_id, "openalex")
    stamp = date.today().isoformat()
    paths = []

    m_path = out_dir / f"{stamp}_{author_id}-impact-summary-{year}_DATA.csv"
    with m_path.open("w", newline="") as f:
        w = csv.writer(f)
        w.writerow(["source", "run_date", "metric", "value"])
        for run, src in ((rs, "scholar"), (ro, "openalex")):
            if not run:
                continue
            for r in con.execute("SELECT metric, value FROM metrics WHERE run_id = ? AND source = ? ORDER BY metric",
                                 (run["run_id"], src)):
                w.writerow([src, run["snapshot_date"], r["metric"], round(r["value"], 4)])
        if rs:
            yc = con.execute("SELECT citations FROM profile_year_counts WHERE run_id = ? AND year = ?",
                             (rs["run_id"], year)).fetchone()
            if yc:
                w.writerow(["scholar", rs["snapshot_date"], f"citations_received_{year}", yc[0]])
    paths.append(m_path)

    p_path = out_dir / f"{stamp}_{author_id}-papers-{year}_DATA.csv"
    write_csv([r for r in paper_rows(con, author_id) if r["year"] == year and not r["excluded"]], p_path)
    paths.append(p_path)
    return paths


def import_pop(con, author_id: str, csv_path: Path, first_year: int = 0) -> dict:
    """Seed history from a Publish or Perish Google Scholar export. Only rows matching works already
    on the profile are loaded; the rest are reported."""
    with csv_path.open(newline="", encoding="utf-8-sig") as f:
        rows = list(csv.DictReader(f))
    if not rows:
        raise ValueError("empty file")
    qd = rows[0].get("QueryDate") or rows[0].get("Query date") or ""
    try:
        snap = datetime.fromisoformat(qd.strip()[:19]).date().isoformat()
    except ValueError:
        raise ValueError(f"Cannot read QueryDate {qd!r}; pass a file exported with its query date")

    works = con.execute("SELECT work_id, title_norm, year FROM works WHERE author_id = ? AND gs_id IS NOT NULL",
                        (author_id,)).fetchall()
    by_norm = {}
    for w in works:
        by_norm.setdefault(w["title_norm"], []).append(w)

    run_id = new_run(con, author_id, snap, origin="pop_import")
    loaded, skipped, total = 0, [], 0
    for r in rows:
        t = norm_title(r.get("Title", ""))
        cites = int(float(r.get("Cites") or 0))
        hit = by_norm.get(t, [])
        if len(hit) != 1:
            best = max(works, key=lambda w: fuzz.token_sort_ratio(t, w["title_norm"]), default=None)
            hit = [best] if best and fuzz.token_sort_ratio(t, best["title_norm"]) >= 95 else []
        if not hit:
            skipped.append(r.get("Title", "")[:80])
            continue
        con.execute("INSERT OR REPLACE INTO work_snapshots(run_id, work_id, source, citations) VALUES (?, ?, 'scholar', ?)",
                    (run_id, hit[0]["work_id"], cites))
        loaded += 1
        total += cites
    con.execute("INSERT OR REPLACE INTO profile_snapshots(run_id, total_citations) VALUES (?, ?)", (run_id, total))
    con.commit()
    metrics.store(con, run_id, "scholar", metrics.compute(con, run_id, "scholar", first_year))
    finish_run(con, run_id, scholar_status="ok", openalex_status="skipped", n_scholar=loaded,
               message=f"Publish or Perish import from {csv_path.name}; {len(skipped)} rows unmatched")
    return {"run_id": run_id, "snapshot_date": snap, "loaded": loaded, "skipped": skipped}


V1_TABLES = ["runs", "works", "work_snapshots", "work_year_counts", "profile_snapshots", "profile_year_counts",
             "work_impact", "metrics", "overrides", "flags"]


def import_v1(con, v1_path: Path, author_id: str) -> dict:
    """Copy a single author pubtrack v1 SQLite database into the store under one author id.
    Run and work ids are renumbered past those already present, so it can join a populated store."""
    import sqlite3

    old = sqlite3.connect(f"file:{v1_path}?mode=ro", uri=True)
    old.row_factory = sqlite3.Row
    ver = old.execute("SELECT value FROM meta WHERE key = 'schema_version'").fetchone()
    if not ver or int(ver[0]) != 1:
        raise ValueError(f"{v1_path} is not a pubtrack v1 database")
    run_off = con.execute("SELECT COALESCE(MAX(run_id), 0) FROM runs").fetchone()[0]
    work_off = con.execute("SELECT COALESCE(MAX(work_id), 0) FROM works").fetchone()[0]
    ov_off = con.execute("SELECT COALESCE(MAX(override_id), 0) FROM overrides").fetchone()[0]
    n = {}
    con.execute("PRAGMA foreign_keys = OFF")
    for t in V1_TABLES:
        rows = [dict(r) for r in old.execute(f"SELECT * FROM {t}")]
        for r in rows:
            if "run_id" in r and r["run_id"] is not None:
                r["run_id"] += run_off
            if "work_id" in r and r["work_id"] is not None:
                r["work_id"] += work_off
            if t == "works" and r.get("merged_into") is not None:
                r["merged_into"] += work_off
            if t == "overrides":
                r["override_id"] += ov_off
            if t in ("runs", "works", "overrides"):
                r["author_id"] = author_id
            if r:
                cols = list(r)
                con.execute(f"INSERT INTO {t} ({', '.join(cols)}) VALUES ({', '.join('?' * len(cols))})",
                            [r[c] for c in cols])
        n[t] = len(rows)
    con.commit()
    con.execute("PRAGMA foreign_keys = ON")
    old.close()
    return n
