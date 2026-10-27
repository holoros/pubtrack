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
    """Largest g with the top g papers holding at least g^2 citations. As in Publish or Perish the
    list is padded with uncited papers, so g may exceed the paper count ([100] gives 10, not 1)."""
    s = sorted(c, reverse=True)
    total, g, i = 0, 0, 0
    while True:
        i += 1
        total += s[i - 1] if i <= len(s) else 0
        if total >= i * i:
            g = i
        elif i > len(s):
            break
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
    duplicates folded into their target). Only works with an OpenAlex snapshot in this run count, so
    a work that left the record does not keep contributing. Papers with no counts map to 0."""
    has_state = con.execute("SELECT 1 FROM work_state WHERE run_id = ? LIMIT 1", (run_id,)).fetchone() is not None
    src_tbl = "work_state w" if has_state else "works w"
    extra = "AND w.run_id = s.run_id" if has_state else ""
    rows = con.execute(
        f"SELECT w.work_id, w.merged_into, COALESCE(SUM(c.citations), 0) AS n "
        f"FROM {src_tbl} JOIN work_snapshots s ON s.work_id = w.work_id AND s.source = 'openalex' AND s.run_id = ? {extra} "
        "LEFT JOIN work_year_counts c ON c.work_id = w.work_id AND c.source = 'openalex' "
        "AND c.year BETWEEN ? AND ? "
        "WHERE w.excluded = 0 GROUP BY w.work_id", (run_id, y0, y1)).fetchall()
    out: dict[int, int] = {}
    for r in rows:
        if r["merged_into"] is None:
            out[r["work_id"]] = out.get(r["work_id"], 0) + int(r["n"])
    for r in rows:
        if r["merged_into"] is not None:
            if r["merged_into"] in out:
                out[r["merged_into"]] += int(r["n"])
            else:  # target has no OpenAlex snapshot: the duplicate stands in, as in paper_table
                out[r["work_id"]] = out.get(r["work_id"], 0) + int(r["n"])
    return out


def freeze_state(con, run_id: int, author_id: str) -> None:
    """Record the non citation inputs of every index for this run (year, author count, exclusion,
    merge target), so the run's metrics can be reproduced after the live works rows change."""
    con.execute("DELETE FROM work_state WHERE run_id = ?", (run_id,))
    con.execute(
        "INSERT INTO work_state(run_id, work_id, year, year_openalex, n_authors, excluded, merged_into) "
        "SELECT ?, work_id, year, year_openalex, n_authors, excluded, merged_into FROM works WHERE author_id = ?",
        (run_id, author_id))
    con.commit()


def paper_table(con, run_id: int, source: str) -> list[dict]:
    """Included papers for one source and run, with merged duplicates folded into their target.
    Inputs come from the run's frozen work_state when it exists (runs since 0.2.4), else from the live
    works rows. The OpenAlex source uses the OpenAlex publication year where it has one."""
    has_state = con.execute("SELECT 1 FROM work_state WHERE run_id = ? LIMIT 1", (run_id,)).fetchone() is not None
    src_tbl = "work_state w" if has_state else "works w"
    extra = "AND w.run_id = s.run_id" if has_state else ""
    year = "COALESCE(w.year_openalex, w.year)" if source == "openalex" else "w.year"
    rows = con.execute(
        f"SELECT w.work_id, {year} AS year, w.n_authors, w.merged_into, w.excluded, s.citations "
        f"FROM work_snapshots s JOIN {src_tbl} ON w.work_id = s.work_id {extra} WHERE s.run_id = ? AND s.source = ?",
        (run_id, source),
    ).fetchall()
    papers = {r["work_id"]: dict(r) for r in rows if not r["excluded"] and r["merged_into"] is None}
    for r in rows:
        if r["merged_into"] is not None and not r["excluded"]:
            if r["merged_into"] in papers:
                papers[r["merged_into"]]["citations"] += r["citations"]
            else:
                # The merge target has no snapshot in this source (Scholar only target, OpenAlex only
                # duplicate): the duplicate stands in for it rather than dropping its citations.
                papers[r["work_id"]] = dict(r)
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
    # Per author indices use only papers whose author count is known (Scholar alone gives none;
    # it comes from the matched OpenAlex record). Imputing one author inflated them by the match rate.
    known = [p for p in dated if p["n_authors"]]
    ck = [p["citations"] for p in known]
    yk = [max(cur_year - int(p["year"]) + 1, 1) for p in known]
    ak = [p["n_authors"] for p in known]
    min_year = min(int(p["year"]) for p in dated) if dated else None
    fy = first_year or (min_year if min_year else cur_year)
    A = max(cur_year - fy + 1, 1)

    h = h_index(c)
    hin = hi_norm(ck, ak)
    out = {
        "n_papers": len(papers),
        "n_undated": len(papers) - len(dated),
        "n_no_authors": len(dated) - len(known),
        "author_count_coverage": len(known) / len(dated) if dated else 0.0,
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
        "awcr_pa": awcr_pa(ck, yk, ak),
        "academic_age": A,
        # Publish or Perish per author measures, over papers with a known author count
        "hm": hm_index(ck, ak),
        "cites_per_author": float(sum(ci / ai for ci, ai in zip(ck, ak))),
        "papers_per_author": float(sum(1 / ai for ai in ak)),
        "cites_per_year": sum(c) / A,
        "cites_per_paper": sum(c) / len(c),
    }
    if min_year:
        out["min_year"] = min_year
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
    # OpenAlex only, like the window block, so the names mean one thing across authors.
    y0, y1 = window_bounds(cur_year)
    if source == "openalex":
        recent = recent_subset(papers, y0, y1)
        rc = [p["citations"] for p in recent]
        out["papers_5y"] = len(recent)
        out["cites_recent_papers"] = sum(rc)
        out["h_recent"] = h_index(rc)
        if recent:
            out["cites_per_recent_paper"] = sum(rc) / len(rc)
    if ak:
        out["authors_per_paper_mean"] = statistics.fmean(ak)
        out["authors_per_paper_median"] = statistics.median(ak)

    if source == "openalex":
        # Citations received in the window, per paper: h5, i10 (5 y), total and rate.
        wc = window_citations_openalex(con, run_id, y0, y1)
        wv = [wc.get(p["work_id"], 0) for p in papers]
        out["cites_5y"] = sum(wv)
        out["cites_per_year_5y"] = sum(wv) / WINDOW_YEARS
        out["h5"] = h_index(wv)
        out["i10_5y"] = i10(wv)
        ids_in = {p["work_id"] for p in papers}  # same stand-in rule as paper_table
        imp = [r for r in con.execute(
            "SELECT i.work_id, i.fwci, i.top1, i.top10 FROM work_impact i WHERE i.run_id = ?", (run_id,),
        ).fetchall() if r["work_id"] in ids_in]
        f = [r["fwci"] for r in imp if r["fwci"] is not None]
        out["n_with_fwci"] = len(f)
        if f:
            out["fwci_median"] = statistics.median(f)
            out["fwci_mean"] = statistics.fmean(f)
        t10 = [r["top10"] for r in imp if r["top10"] is not None]
        t1 = [r["top1"] for r in imp if r["top1"] is not None]
        out["n_with_percentile"] = len(t10)  # the denominator of the shares; recent papers have none yet
        if t10:
            out["top10_share"] = sum(t10) / len(t10)
        if t1:
            out["top1_share"] = sum(t1) / len(t1)

    if source == "scholar":
        # Scholar's "since" year is the current year minus 5, so its reported h5 and i10 cover six
        # calendar years including the partial current one, not the five complete years used for
        # the OpenAlex window. They are stored under their own names, never as h5 / i10_5y.
        # cites_5y uses the complete year histogram so it matches the OpenAlex window definition.
        prof = con.execute("SELECT h5_reported, i10_5y_reported, citations_5y FROM profile_snapshots "
                           "WHERE run_id = ?", (run_id,)).fetchone()
        if prof:
            if prof["h5_reported"] is not None:
                out["h5_reported"] = prof["h5_reported"]
            if prof["i10_5y_reported"] is not None:
                out["i10_5y_reported"] = prof["i10_5y_reported"]
            if prof["citations_5y"] is not None:
                out["cites_since_reported"] = prof["citations_5y"]
        by_year = con.execute("SELECT year, citations FROM profile_year_counts WHERE run_id = ?",
                              (run_id,)).fetchall()
        if by_year:  # the Scholar histogram over the same window, under its own name
            c5 = sum(int(r["citations"] or 0) for r in by_year if y0 <= int(r["year"]) <= y1)
            out["cites_5y_scholar"] = c5
            out["cites_per_year_5y_scholar"] = c5 / WINDOW_YEARS
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
    ids_in = {p["work_id"] for p in paper_table(con, run_id, "openalex")}
    if not ids_in:
        return None
    rows = con.execute(
        "SELECT c.work_id, SUM(c.citations) AS n FROM work_year_counts c "
        "WHERE c.source = 'openalex' AND c.year IN (?, ?) GROUP BY c.work_id", (y - 1, y - 2),
    ).fetchall()
    return sum(int(r["n"]) for r in rows if r["work_id"] in ids_in) / 2


def store(con, run_id: int, source: str, values: dict[str, float]) -> None:
    for k, v in values.items():
        con.execute("INSERT OR REPLACE INTO metrics(run_id, source, metric, value) VALUES (?, ?, ?, ?)",
                    (run_id, source, k, float(v)))
    con.commit()
