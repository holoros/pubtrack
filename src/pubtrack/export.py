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


def paper_rows(con) -> list[dict]:
    rs = latest_run(con, "scholar")
    ro = latest_run(con, "openalex")
    rs_id = rs["run_id"] if rs else -1
    ro_id = ro["run_id"] if ro else -1
    rows = con.execute(
        "SELECT w.*, s.citations AS scholar_citations, o.citations AS openalex_citations, "
        "i.fwci, i.pct_value AS percentile, i.top10, i.top1 FROM works w "
        "LEFT JOIN work_snapshots s ON s.work_id = w.work_id AND s.source = 'scholar' AND s.run_id = ? "
        "LEFT JOIN work_snapshots o ON o.work_id = w.work_id AND o.source = 'openalex' AND o.run_id = ? "
        "LEFT JOIN work_impact i ON i.work_id = w.work_id AND i.run_id = ? "
        "WHERE w.merged_into IS NULL ORDER BY s.citations DESC NULLS LAST, o.citations DESC",
        (rs_id, ro_id, ro_id)).fetchall()
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


def annual_summary(con, year: int, out_dir: Path) -> list[Path]:
    """Two CSVs for activity reports: indices at the latest run, and the year's papers and citations."""
    rs = latest_run(con, "scholar")
    ro = latest_run(con, "openalex")
    stamp = date.today().isoformat()
    paths = []

    m_path = out_dir / f"{stamp}_scholar-impact-summary-{year}_DATA.csv"
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

    p_path = out_dir / f"{stamp}_scholar-papers-{year}_DATA.csv"
    write_csv([r for r in paper_rows(con) if r["year"] == year and not r["excluded"]], p_path)
    paths.append(p_path)
    return paths


def import_pop(con, csv_path: Path, first_year: int = 0) -> dict:
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

    works = con.execute("SELECT work_id, title_norm, year FROM works WHERE gs_id IS NOT NULL").fetchall()
    by_norm = {}
    for w in works:
        by_norm.setdefault(w["title_norm"], []).append(w)

    run_id = new_run(con, snap, origin="pop_import")
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
