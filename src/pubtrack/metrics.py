"""Impact indices. Pure functions first, then the per run computation.

Notation: c citations per paper, y age in years (current year minus publication
year plus 1), a authors per paper, A academic age.
"""

from __future__ import annotations

import math
import statistics
from datetime import date


# ---------------------------------------------------------------- pure functions

def h_of(scores) -> int:
    """Largest n such that the n-th highest score is at least n (works for real valued scores)."""
    s = sorted((float(x) for x in scores), reverse=True)
    h = 0
    for i, v in enumerate(s, start=1):
        if v >= i:
            h = i
        else:
            break
    return h


def h_index(c) -> int:
    return h_of(c)


def g_index(c) -> int:
    """Largest g with the top g papers holding at least g^2 citations (capped at the paper count)."""
    s = sorted(c, reverse=True)
    total, g = 0, 0
    for i, v in enumerate(s, start=1):
        total += v
        if total >= i * i:
            g = i
    return g


def i10(c) -> int:
    return sum(1 for v in c if v >= 10)


def e_index(c) -> float:
    s = sorted(c, reverse=True)
    h = h_index(s)
    return math.sqrt(max(sum(s[:h]) - h * h, 0))


def hc_index(c, y, gamma: float = 4.0, delta: float = 1.0) -> int:
    """Contemporary h (Sidiropoulos et al. 2007): h of gamma * y^-delta * c."""
    return h_of(gamma * ci * (yi ** -delta) for ci, yi in zip(c, y))


def hi_norm(c, a) -> int:
    """h of citations divided by author count."""
    return h_of(ci / max(ai, 1) for ci, ai in zip(c, a))


def annual_h(c, y) -> int:
    """Largest n with n papers at c/y >= n. Compare with Publish or Perish hA before treating as equivalent."""
    return h_of(ci / yi for ci, yi in zip(c, y))


def awcr(c, y) -> float:
    return float(sum(ci / yi for ci, yi in zip(c, y)))


def awcr_pa(c, y, a) -> float:
    return float(sum(ci / (yi * max(ai, 1)) for ci, yi, ai in zip(c, y, a)))


def ols_slope_per_year(dates: list[str], values: list[float]) -> float | None:
    """Least squares slope of cumulative citations against time, in citations per year."""
    if len(dates) < 2:
        return None
    t = [date.fromisoformat(d).toordinal() / 365.25 for d in dates]
    mt, mv = statistics.fmean(t), statistics.fmean(values)
    sxx = sum((ti - mt) ** 2 for ti in t)
    if sxx == 0:
        return None
    return sum((ti - mt) * (vi - mv) for ti, vi in zip(t, values)) / sxx


# ---------------------------------------------------------------- per run computation

MIN_VELOCITY_POINTS = 8


def paper_table(con, run_id: int, source: str) -> list[dict]:
    """Included papers for one source and run, with merged duplicates folded into their target."""
    rows = con.execute(
        "SELECT w.work_id, w.year, w.n_authors, w.merged_into, w.excluded, s.citations "
        "FROM work_snapshots s JOIN works w USING (work_id) WHERE s.run_id = ? AND s.source = ?",
        (run_id, source),
    ).fetchall()
    papers = {r["work_id"]: dict(r) for r in rows if not r["excluded"] and r["merged_into"] is None}
    for r in rows:
        if r["merged_into"] is not None and not r["excluded"] and r["merged_into"] in papers:
            papers[r["merged_into"]]["citations"] += r["citations"]
    return list(papers.values())


def compute(con, run_id: int, source: str, first_year: int = 0) -> dict[str, float]:
    run = con.execute("SELECT snapshot_date, author_id FROM runs WHERE run_id = ?", (run_id,)).fetchone()
    cur_year = date.fromisoformat(run["snapshot_date"]).year
    papers = paper_table(con, run_id, source)
    if not papers:
        return {}
    c = [p["citations"] for p in papers]
    dated = [p for p in papers if p["year"]]
    cy = [p["citations"] for p in dated]
    yy = [max(cur_year - int(p["year"]) + 1, 1) for p in dated]
    aa = [p["n_authors"] or 1 for p in dated]
    fy = first_year or (min(int(p["year"]) for p in dated) if dated else cur_year)
    A = max(cur_year - fy + 1, 1)

    h = h_index(c)
    hin = hi_norm(cy, aa)
    out = {
        "n_papers": len(papers),
        "n_undated": len(papers) - len(dated),
        "n_no_authors": sum(1 for p in dated if not p["n_authors"]),
        "citations": sum(c),
        "h": h,
        "g": g_index(c),
        "i10": i10(c),
        "m_quotient": h / A,
        "e": e_index(c),
        "hc": hc_index(cy, yy),
        "hI_norm": hin,
        "hI_annual": hin / A,
        "annual_h": annual_h(cy, yy),
        "awcr": awcr(cy, yy),
        "aw": math.sqrt(awcr(cy, yy)),
        "awcr_pa": awcr_pa(cy, yy, aa),
        "academic_age": A,
    }

    if source == "openalex":
        imp = con.execute(
            "SELECT i.fwci, i.top1, i.top10 FROM work_impact i JOIN works w USING (work_id) "
            "WHERE i.run_id = ? AND w.excluded = 0 AND w.merged_into IS NULL", (run_id,),
        ).fetchall()
        f = [r["fwci"] for r in imp if r["fwci"] is not None]
        if f:
            out["fwci_median"] = statistics.median(f)
            out["fwci_mean"] = statistics.fmean(f)
        t10 = [r["top10"] for r in imp if r["top10"] is not None]
        t1 = [r["top1"] for r in imp if r["top1"] is not None]
        if t10:
            out["top10_share"] = sum(t10) / len(t10)
        if t1:
            out["top1_share"] = sum(t1) / len(t1)

    if source == "scholar":
        hist = con.execute(
            "SELECT r.snapshot_date, p.total_citations FROM profile_snapshots p JOIN runs r USING (run_id) "
            "WHERE r.author_id = ? AND p.total_citations IS NOT NULL AND r.origin = 'sync' "
            "AND r.snapshot_date > date(?, '-365 days') AND r.snapshot_date <= ? "
            "ORDER BY r.snapshot_date", (run["author_id"], run["snapshot_date"], run["snapshot_date"]),
        ).fetchall()
        if len(hist) >= MIN_VELOCITY_POINTS:
            v = ols_slope_per_year([r[0] for r in hist], [r[1] for r in hist])
            if v is not None:
                out["velocity_per_year"] = v
    return out


def interim_rate(con, run_id: int) -> float | None:
    """Mean OpenAlex citations per year over the last two complete years (used until velocity exists)."""
    run = con.execute("SELECT snapshot_date, author_id FROM runs WHERE run_id = ?", (run_id,)).fetchone()
    y = date.fromisoformat(run["snapshot_date"]).year
    r = con.execute(
        "SELECT SUM(c.citations) FROM work_year_counts c JOIN works w USING (work_id) "
        "WHERE w.author_id = ? AND c.source = 'openalex' AND c.year IN (?, ?) AND w.excluded = 0 "
        "AND w.merged_into IS NULL", (run["author_id"], y - 1, y - 2),
    ).fetchone()[0]
    return r / 2 if r is not None else None


def store(con, run_id: int, source: str, values: dict[str, float]) -> None:
    for k, v in values.items():
        con.execute("INSERT OR REPLACE INTO metrics(run_id, source, metric, value) VALUES (?, ?, ?, ?)",
                    (run_id, source, k, float(v)))
    con.commit()
