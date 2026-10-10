"""Pair Scholar works with OpenAlex works.

Scholar profile listings carry no DOI and no author list, so matching runs on
DOI (only when an override supplied one), then exact normalized title, then
fuzzy title. Assignment is one to one and greedy by score; a fuzzy match must
also beat the runner up by a clear margin. Anything left over is reported, not guessed.
"""

from __future__ import annotations

from rapidfuzz import fuzz

FUZZY_MIN = 92.0
FUZZY_MARGIN = 3.0
YEAR_TOL = 1


def _year_ok(a, b) -> bool:
    return a is None or b is None or abs(int(a) - int(b)) <= YEAR_TOL


def candidates(gs_rows, oa_rows):
    """Yield (score, method, gs_row, oa_row) for every plausible pair."""
    by_doi = {r["doi"]: r for r in oa_rows if r["doi"]}
    by_norm: dict[str, list] = {}
    for r in oa_rows:
        by_norm.setdefault(r["title_norm"], []).append(r)

    for g in gs_rows:
        if g["doi"] and g["doi"] in by_doi:
            yield 101.0, "doi", g, by_doi[g["doi"]]
            continue
        exact = [o for o in by_norm.get(g["title_norm"], []) if _year_ok(g["year"], o["year"])]
        if len(exact) == 1:
            yield 100.0, "title_exact", g, exact[0]
            continue
        scored = sorted(
            ((fuzz.token_sort_ratio(g["title_norm"], o["title_norm"]), o) for o in oa_rows
             if _year_ok(g["year"], o["year"])),
            key=lambda t: t[0], reverse=True,
        )
        if scored and scored[0][0] >= FUZZY_MIN:
            runner = scored[1][0] if len(scored) > 1 else 0.0
            if scored[0][0] - runner >= FUZZY_MARGIN:
                yield float(scored[0][0]), "title_fuzzy", g, scored[0][1]


def merge_pair(con, gs_work_id: int, oa_work_id: int, method: str, score: float | None) -> None:
    """Fold an OpenAlex only row into its Scholar row and delete it."""
    oa = con.execute("SELECT * FROM works WHERE work_id = ?", (oa_work_id,)).fetchone()
    con.execute("UPDATE works SET openalex_id = NULL WHERE work_id = ?", (oa_work_id,))
    con.execute(
        "UPDATE works SET openalex_id = ?, doi = COALESCE(doi, ?), authors = ?, n_authors = ?, "
        "author_position = ?, is_last_author = ?, year = COALESCE(year, ?), match_method = ?, match_score = ? "
        "WHERE work_id = ?",
        (oa["openalex_id"], oa["doi"], oa["authors"], oa["n_authors"], oa["author_position"],
         oa["is_last_author"], oa["year"], method, score, gs_work_id),
    )
    for table in ("work_snapshots", "work_year_counts", "work_impact"):
        # A Scholar row could already hold OpenAlex data from an earlier link; the OA row's is newer.
        if table == "work_snapshots":
            con.execute("DELETE FROM work_snapshots WHERE work_id = ? AND source = 'openalex' AND run_id IN "
                        "(SELECT run_id FROM work_snapshots WHERE work_id = ? AND source = 'openalex')",
                        (gs_work_id, oa_work_id))
        elif table == "work_year_counts":
            con.execute("DELETE FROM work_year_counts WHERE work_id = ? AND source = 'openalex'", (gs_work_id,))
        else:
            con.execute("DELETE FROM work_impact WHERE work_id = ? AND run_id IN "
                        "(SELECT run_id FROM work_impact WHERE work_id = ?)", (gs_work_id, oa_work_id))
        con.execute(f"UPDATE {table} SET work_id = ? WHERE work_id = ?", (gs_work_id, oa_work_id))
    con.execute("UPDATE flags SET work_id = ? WHERE work_id = ?", (gs_work_id, oa_work_id))
    con.execute("DELETE FROM works WHERE work_id = ?", (oa_work_id,))


def reconcile(con, author_id: str) -> dict:
    gs_rows = con.execute(
        "SELECT w.work_id, w.doi, w.title_norm, w.year, COALESCE(("
        "  SELECT s.citations FROM work_snapshots s WHERE s.work_id = w.work_id AND s.source = 'scholar' "
        "  ORDER BY s.run_id DESC LIMIT 1), 0) AS cites FROM works w "
        "WHERE w.author_id = ? AND w.gs_id IS NOT NULL AND w.openalex_id IS NULL AND w.merged_into IS NULL",
        (author_id,)).fetchall()
    oa_rows = con.execute(
        "SELECT work_id, doi, title_norm, year FROM works WHERE author_id = ? AND gs_id IS NULL "
        "AND openalex_id IS NOT NULL", (author_id,)).fetchall()
    def ydiff(g, o):
        return abs(g["year"] - o["year"]) if g["year"] and o["year"] else 9

    # Best score first; ties go to the closer year, then to the more cited Scholar version
    # (a preprint or duplicate entry should not take the published paper's OpenAlex record).
    pairs = sorted(candidates(gs_rows, oa_rows),
                   key=lambda t: (-t[0], ydiff(t[2], t[3]), -t[2]["cites"], t[2]["work_id"]))
    used_g, used_o, counts = set(), set(), {"doi": 0, "title_exact": 0, "title_fuzzy": 0}
    for score, method, grow, orow in pairs:
        g, o = grow["work_id"], orow["work_id"]
        if g in used_g or o in used_o:
            continue
        merge_pair(con, g, o, method, score)
        used_g.add(g)
        used_o.add(o)
        counts[method] += 1
    con.commit()
    counts["unmatched_scholar"] = con.execute(
        "SELECT COUNT(*) FROM works WHERE author_id = ? AND gs_id IS NOT NULL AND openalex_id IS NULL "
        "AND merged_into IS NULL AND excluded = 0", (author_id,)).fetchone()[0]
    counts["openalex_only"] = con.execute(
        "SELECT COUNT(*) FROM works WHERE author_id = ? AND gs_id IS NULL AND openalex_id IS NOT NULL",
        (author_id,)).fetchone()[0]
    return counts
