"""Pair Scholar works with OpenAlex works.

Scholar profile listings carry no DOI and no author list, so matching runs on
DOI (only when an override supplied one), then exact normalized title, then
fuzzy title. Assignment is one to one and greedy by score; a fuzzy match must
also beat the runner up by a clear margin. Anything left over is reported, not guessed.
"""

from __future__ import annotations

from rapidfuzz import fuzz

from .db import norm_title

FUZZY_MIN = 92.0
FUZZY_MARGIN = 3.0
DUP_TITLE = 97.0      # two OpenAlex titles this close are copies of one work
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
        if exact:
            # Several OpenAlex records with one title are copies of one work (preprint and article):
            # take the closest year, then the most cited.
            yield 100.0, "title_exact", g, _best(g, exact)
            continue
        scored = sorted(
            ((fuzz.token_sort_ratio(g["title_norm"], o["title_norm"]), o) for o in oa_rows
             if _year_ok(g["year"], o["year"])),
            key=lambda t: t[0], reverse=True,
        )
        if scored and scored[0][0] >= FUZZY_MIN:
            top = scored[0]
            close = [o for s, o in scored if top[0] - s < FUZZY_MARGIN]
            # A runner up within the margin blocks the match unless it is a copy of the same work.
            if all(fuzz.token_sort_ratio(top[1]["title_norm"], o["title_norm"]) >= DUP_TITLE for o in close):
                yield float(top[0]), "title_fuzzy", g, _best(g, close)


def _best(g, rows):
    gy = g["year"]
    return min(rows, key=lambda o: (abs(gy - o["year"]) if gy and o["year"] else 9,
                                    -(o["cites"] if "cites" in o.keys() and o["cites"] is not None else 0)))


def merge_pair(con, gs_work_id: int, oa_work_id: int, method: str, score: float | None) -> None:
    """Fold an OpenAlex only row into its Scholar row and delete it."""
    oa = con.execute("SELECT * FROM works WHERE work_id = ?", (oa_work_id,)).fetchone()
    con.execute("UPDATE works SET openalex_id = NULL WHERE work_id = ?", (oa_work_id,))
    con.execute(
        "UPDATE works SET openalex_id = ?, doi = COALESCE(doi, ?), authors = ?, n_authors = ?, "
        "author_position = ?, is_last_author = ?, year = COALESCE(year, ?), year_openalex = ?, type = ?, "
        "match_method = ?, match_score = ? WHERE work_id = ?",
        (oa["openalex_id"], oa["doi"], oa["authors"], oa["n_authors"], oa["author_position"],
         oa["is_last_author"], oa["year"], oa["year_openalex"], oa["type"], method, score, gs_work_id),
    )
    for table in ("work_snapshots", "work_year_counts", "work_impact", "work_ytd", "work_state"):
        # A Scholar row could already hold OpenAlex data from an earlier link; the OA row's is newer.
        if table == "work_snapshots":
            con.execute("DELETE FROM work_snapshots WHERE work_id = ? AND source = 'openalex' AND run_id IN "
                        "(SELECT run_id FROM work_snapshots WHERE work_id = ? AND source = 'openalex')",
                        (gs_work_id, oa_work_id))
        elif table == "work_year_counts":
            con.execute("DELETE FROM work_year_counts WHERE work_id = ? AND source = 'openalex'", (gs_work_id,))
        else:
            con.execute(f"DELETE FROM {table} WHERE work_id = ? AND run_id IN "
                        f"(SELECT run_id FROM {table} WHERE work_id = ?)", (gs_work_id, oa_work_id))
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
        "SELECT w.work_id, w.doi, w.title_norm, w.year, COALESCE(("
        "  SELECT s.citations FROM work_snapshots s WHERE s.work_id = w.work_id AND s.source = 'openalex' "
        "  ORDER BY s.run_id DESC LIMIT 1), 0) AS cites FROM works w "
        "WHERE w.author_id = ? AND w.gs_id IS NULL AND w.openalex_id IS NOT NULL", (author_id,)).fetchall()
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
    counts["oa_duplicate"] = collapse_openalex_duplicates(con, author_id)
    con.commit()
    counts["unmatched_scholar"] = con.execute(
        "SELECT COUNT(*) FROM works WHERE author_id = ? AND gs_id IS NOT NULL AND openalex_id IS NULL "
        "AND merged_into IS NULL AND excluded = 0", (author_id,)).fetchone()[0]
    counts["openalex_only"] = con.execute(
        "SELECT COUNT(*) FROM works WHERE author_id = ? AND gs_id IS NULL AND openalex_id IS NOT NULL",
        (author_id,)).fetchone()[0]
    return counts


def collapse_openalex_duplicates(con, author_id: str) -> int:
    """OpenAlex only rows with one normalized title within a year of each other are versions of one
    work (preprint and article, or repeated deposits). All but the most cited fold into it through
    merged_into, so paper counts and per paper averages count the work once. Rows a user excluded or
    already merged are left alone."""
    rows = con.execute(
        "SELECT w.work_id, w.title_norm, w.year, COALESCE(("
        "  SELECT s.citations FROM work_snapshots s WHERE s.work_id = w.work_id AND s.source = 'openalex' "
        "  ORDER BY s.run_id DESC LIMIT 1), 0) AS cites FROM works w "
        "WHERE w.author_id = ? AND w.gs_id IS NULL AND w.openalex_id IS NOT NULL AND w.excluded = 0 "
        "AND w.merged_into IS NULL ORDER BY w.title_norm, cites DESC, w.work_id", (author_id,)).fetchall()
    groups: dict[str, list] = {}
    for r in rows:
        groups.setdefault(r["title_norm"], []).append(r)
    n = 0
    for same in groups.values():
        if len(same) < 2:
            continue
        target = same[0]  # most cited
        for r in same[1:]:
            if _year_ok(target["year"], r["year"]):
                con.execute("UPDATE works SET merged_into = ?, match_method = 'oa_duplicate' WHERE work_id = ?",
                            (target["work_id"], r["work_id"]))
                n += 1
    return n


SEARCH_MIN = 95.0     # title search results must be near exact, since no author id vouches for them
RETRY_AFTER_DAYS = 90  # a miss is searched again after this long while the paper is still young
RETRY_YOUNG_YEARS = 3


def title_search_fallback(con, author, run_id: int, mailto: str, api_key: str, today: str, cap: int = 120,
                          skip_types=None) -> dict:
    """For Scholar works still unmatched, search OpenAlex by title once each, store a near exact hit
    (within a year) as this author's OpenAlex record, and link it. Every search is logged, hit or miss."""
    from . import openalex

    todo = con.execute(
        "SELECT w.work_id, w.gs_id, w.title, w.title_norm, w.year FROM works w "
        "LEFT JOIN title_lookups t ON t.author_id = w.author_id AND t.gs_id = w.gs_id "
        "WHERE w.author_id = ? AND w.gs_id IS NOT NULL AND w.openalex_id IS NULL AND w.excluded = 0 "
        "AND w.merged_into IS NULL AND length(w.title_norm) >= 20 AND (t.gs_id IS NULL OR "
        # a miss on a young paper is retried: new work often reaches Scholar before OpenAlex
        "  (t.openalex_id IS NULL AND t.searched <= date(?, ?) AND (w.year IS NULL OR w.year >= ?))) "
        "ORDER BY t.searched IS NOT NULL, w.work_id LIMIT ?",
        (author.id, today, f"-{RETRY_AFTER_DAYS} days", int(today[:4]) - RETRY_YOUNG_YEARS, cap)).fetchall()
    found, errors = 0, 0
    for g in todo:
        hit, score = None, None
        try:
            res = openalex.search_title(g["title"], mailto, api_key)
            errors = 0
        except openalex.OpenAlexError:
            errors += 1
            if errors >= 3:
                break  # the service is struggling; the rest wait for the next run
            continue  # not logged, so this title is retried next run
        truncated = g["title"].rstrip().endswith(("\u2026", "..."))
        best = None
        for w in res:
            t = norm_title(w.get("display_name") or "")
            s = fuzz.token_sort_ratio(g["title_norm"], t)
            if truncated:  # Scholar cut the title short: compare against the same length prefix
                s = max(s, fuzz.ratio(g["title_norm"], t[:len(g["title_norm"])]))
            if s >= SEARCH_MIN and _year_ok(g["year"], w.get("publication_year")):
                if best is None or s > best[0] or (s == best[0] and (w.get("cited_by_count") or 0) > (best[1].get("cited_by_count") or 0)):
                    best = (s, w)
        if best:
            score, w = best
            oid = openalex.short_id(w["id"])
            taken = con.execute("SELECT work_id, gs_id FROM works WHERE author_id = ? AND openalex_id = ?",
                                (author.id, oid)).fetchone()
            if taken is None:
                openalex.store(con, run_id, [w], author.openalex_ids, author.surname, today, skip_types)
                oa_row = con.execute("SELECT work_id FROM works WHERE author_id = ? AND openalex_id = ?",
                                     (author.id, oid)).fetchone()
                if oa_row is not None:  # store() skips non research types such as errata
                    merge_pair(con, g["work_id"], oa_row["work_id"], "title_search", float(score))
                    hit = oid
                    found += 1
            elif taken["gs_id"] is None:
                merge_pair(con, g["work_id"], taken["work_id"], "title_search", float(score))
                hit = oid
                found += 1
        con.execute("INSERT OR REPLACE INTO title_lookups(author_id, gs_id, searched, openalex_id, score) "
                    "VALUES (?, ?, ?, ?, ?)", (author.id, g["gs_id"], today, hit, score))
    con.commit()
    return {"searched": len(todo), "linked": found}
