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


def hm_index(c, a) -> float:
    """Schreiber (2008) hm: papers counted fractionally (1/a) in rank order; the largest effective
    rank r_eff with the paper at that rank still holding at least r_eff citations."""
    pairs = sorted(zip(c, a), key=lambda t: -t[0])
    r_eff, hm = 0.0, 0.0
    for ci, ai in pairs:
        r_eff += 1.0 / max(ai, 1)
        if ci >= r_eff:
            hm = r_eff
        else:
            break
    return hm


def acc(c, n: int) -> int:
    """Publish or Perish accN: papers with at least N citations."""
    return sum(1 for v in c if v >= n)


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


def rate_per_paper(c, y) -> list[float]:
    """Citations per year since publication for each paper (c / y)."""
    return [ci / yi for ci, yi in zip(c, y)]


def recent_subset(papers: list[dict], y0: int, y1: int) -> list[dict]:
    """Papers published in the closed year window [y0, y1]."""
    return [p for p in papers if p["year"] and y0 <= int(p["year"]) <= y1]


# ---------------------------------------------------------------- per run computation

MIN_VELOCITY_POINTS = 8
WINDOW_YEARS = 5  # the "5 y" window is the last five complete calendar years


def window_bounds(cur_year: int, n: int = WINDOW_YEARS) -> tuple[int, int]:
    return cur_year - n, cur_year - 1


def window_citations_openalex(con, run_id: int, y0: int, y1: int) -> dict[int, int]:
    """Citations each included paper received in [y0, y1], from OpenAlex counts_by_year (merged
    duplicates folded into their target). Papers with no counts in the window map to 0."""
    rows = con.execute(
        "SELECT w.work_id, w.merged_into, COALESCE(SUM(c.citations), 0) AS n "
        "FROM works w JOIN runs r ON r.author_id = w.author_id "
        "LEFT JOIN work_year_counts c ON c.work_id = w.work_id AND c.source = 'openalex' "
        "AND c.year BETWEEN ? AND ? "
        "WHERE r.run_id = ? AND w.excluded = 0 GROUP BY w.work_id", (y0, y1, run_id)).fetchall()
    out: dict[int, int] = {}
    for r in rows:
        if r["merged_into"] is None:
            out[r["work_id"]] = out.get(r["work_id"], 0) + int(r["n"])
    for r in rows:
        if r["merged_into"] is not None and r["merged_into"] in out:
            out[r["merged_into"]] += int(r["n"])
    return out


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
        # Publish or Perish per author measures (author count unknown counts as 1, as for hI norm)
        "hm": hm_index(cy, aa),
        "cites_per_author": float(sum(ci / ai for ci, ai in zip(cy, aa))),
        "papers_per_author": float(sum(1 / ai for ai in aa)),
        "cites_per_year": sum(c) / A,
        "cites_per_paper": sum(c) / len(c),
    }
    for n in (1, 2, 5, 10, 20):
        out[f"acc{n}"] = acc(c, n)

    # Annualized: everything divided by academic age or by paper age, so authors at different
    # career stages compare on rates rather than accumulated totals.
    out["papers_per_year"] = len(papers) / A
    out["g_per_year"] = out["g"] / A
    out["i10_per_year"] = out["i10"] / A
    if dated:
        rates = rate_per_paper(cy, yy)
        out["cites_per_paper_year_mean"] = statistics.fmean(rates)
        out["cites_per_paper_year_median"] = statistics.median(rates)

    # Recent work: papers published in the last five complete years and the citations they hold.
    y0, y1 = window_bounds(cur_year)
    recent = recent_subset(papers, y0, y1)
    rc = [p["citations"] for p in recent]
    out["papers_5y"] = len(recent)
    out["cites_recent_papers"] = sum(rc)
    out["h_recent"] = h_index(rc)
    if recent:
        out["cites_per_recent_paper"] = sum(rc) / len(rc)
    known = [p["n_authors"] for p in dated if p["n_authors"]]
    if known:
        out["authors_per_paper_mean"] = statistics.fmean(known)
        out["authors_per_paper_median"] = statistics.median(known)

    if source == "openalex":
        # Citations received in the window, per paper: h5, i10 (5 y), total and rate.
        wc = window_citations_openalex(con, run_id, y0, y1)
        wv = [wc.get(p["work_id"], 0) for p in papers]
        out["cites_5y"] = sum(wv)
        out["cites_per_year_5y"] = sum(wv) / WINDOW_YEARS
        out["h5"] = h_index(wv)
        out["i10_5y"] = i10(wv)
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
        # Scholar reports h5 and i10 (5 y) over "since year" = current year minus 4, so the window
        # includes the partial current year; stored as reported. cites_5y uses the complete year
        # histogram so it matches the OpenAlex window definition.
        prof = con.execute("SELECT h5_reported, i10_5y_reported FROM profile_snapshots WHERE run_id = ?",
                           (run_id,)).fetchone()
        if prof:
            if prof["h5_reported"] is not None:
                out["h5"] = prof["h5_reported"]
            if prof["i10_5y_reported"] is not None:
                out["i10_5y"] = prof["i10_5y_reported"]
        by_year = con.execute("SELECT year, citations FROM profile_year_counts WHERE run_id = ?",
                              (run_id,)).fetchall()
        if by_year:
            c5 = sum(int(r["citations"] or 0) for r in by_year if y0 <= int(r["year"]) <= y1)
            out["cites_5y"] = c5
            out["cites_per_year_5y"] = c5 / WINDOW_YEARS
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
