"""Google Scholar collector. Profile pages only; never per article pages.

Two routes return the same normalized structure:
  {"profile": {"total", "h", "i10", "c5y", "h5", "i10_5y", "by_year": {year: n}},
   "works":   [{"gs_id", "title", "year", "venue", "citations"}]}
"""

from __future__ import annotations

import random
import time

import requests


class ScholarBlocked(RuntimeError):
    """Scholar refused the request (CAPTCHA, 429, or repeated failures)."""


def _int(x, default=None):
    try:
        return int(x)
    except (TypeError, ValueError):
        return default


def via_scholarly(scholar_id: str, min_delay: float = 10, max_delay: float = 25) -> dict:
    """Uses the scholarly package. One profile fill is roughly one request per 100 works."""
    try:
        from scholarly import scholarly
    except ImportError as e:  # pragma: no cover
        raise RuntimeError("scholarly is not installed") from e

    time.sleep(random.uniform(min_delay, max_delay) / 4)  # jitter before the first hit
    try:
        author = scholarly.search_author_id(scholar_id)
        time.sleep(random.uniform(min_delay, max_delay))
        author = scholarly.fill(author, sections=["basics", "indices", "counts", "publications"])
    except Exception as e:  # scholarly raises several types on CAPTCHA and blocks
        raise ScholarBlocked(f"{type(e).__name__}: {e}") from e

    works = []
    for p in author.get("publications", []):
        bib = p.get("bib", {})
        works.append({
            "gs_id": p.get("author_pub_id"),
            "title": bib.get("title", "").strip(),
            "year": _int(bib.get("pub_year")),
            "venue": bib.get("citation", "").strip() or None,
            "citations": _int(p.get("num_citations"), 0),
        })
    profile = {
        "total": _int(author.get("citedby")),
        "h": _int(author.get("hindex")),
        "i10": _int(author.get("i10index")),
        "c5y": _int(author.get("citedby5y")),
        "h5": _int(author.get("hindex5y")),
        "i10_5y": _int(author.get("i10index5y")),
        "by_year": {int(y): int(n) for y, n in (author.get("cites_per_year") or {}).items()},
    }
    if not works:
        raise ScholarBlocked("Profile returned no publications; treating as a failed fetch")
    return {"profile": profile, "works": works}


def via_serpapi(scholar_id: str, api_key: str, timeout: int = 60) -> dict:
    """SerpAPI google_scholar_author engine (free tier: 250 searches a month). Each page of up to
    100 articles is one search; the count is returned as data["serpapi_calls"]."""
    if not api_key:
        raise ScholarBlocked("SERPAPI_KEY is not set")
    url = "https://serpapi.com/search.json"
    works, start, first, calls = [], 0, None, 0
    while True:
        calls += 1
        r = requests.get(url, params={
            "engine": "google_scholar_author", "author_id": scholar_id,
            "num": 100, "start": start, "api_key": api_key,
        }, timeout=timeout)
        if r.status_code != 200:
            raise ScholarBlocked(f"SerpAPI HTTP {r.status_code}: {r.text[:200]}")
        js = r.json()
        if "error" in js:
            raise ScholarBlocked(f"SerpAPI error: {js['error']}")
        first = first or js
        arts = js.get("articles", [])
        for a in arts:
            works.append({
                "gs_id": a.get("citation_id"),
                "title": (a.get("title") or "").strip(),
                "year": _int(a.get("year")),
                "venue": a.get("publication"),
                "citations": _int((a.get("cited_by") or {}).get("value"), 0),
            })
        if len(arts) < 100:
            break
        start += 100

    table = {}
    for row in (first.get("cited_by") or {}).get("table", []):
        for k, v in row.items():
            vals = list(v.values())
            table[k] = (_int(vals[0]), _int(vals[1]) if len(vals) > 1 else None)
    graph = (first.get("cited_by") or {}).get("graph", [])
    profile = {
        "total": table.get("citations", (None, None))[0],
        "c5y": table.get("citations", (None, None))[1],
        "h": table.get("h_index", (None, None))[0],
        "h5": table.get("h_index", (None, None))[1],
        "i10": table.get("i10_index", (None, None))[0],
        "i10_5y": table.get("i10_index", (None, None))[1],
        "by_year": {int(g["year"]): int(g["citations"]) for g in graph if "year" in g},
    }
    if not works:
        raise ScholarBlocked("SerpAPI returned no articles")
    return {"profile": profile, "works": works, "serpapi_calls": calls}


def store(con, run_id: int, data: dict, today: str) -> int:
    """Upsert works by (author, gs_id) and write this run's Scholar snapshot. Returns n works."""
    from .db import norm_title, run_author

    author = run_author(con, run_id)
    n = 0
    for w in data["works"]:
        if not w["gs_id"] or not w["title"]:
            continue
        row = con.execute("SELECT work_id FROM works WHERE author_id = ? AND gs_id = ?",
                          (author, w["gs_id"])).fetchone()
        if row:
            work_id = row["work_id"]
            con.execute(
                "UPDATE works SET title = ?, title_norm = ?, venue = COALESCE(?, venue), last_seen = ?, "
                "year = COALESCE(?, year) WHERE work_id = ?",  # set_year overrides reapply after ingest
                (w["title"], norm_title(w["title"]), w["venue"], today, w["year"], work_id),
            )
        else:
            cur = con.execute(
                "INSERT INTO works(author_id, gs_id, title, title_norm, year, venue, match_method, first_seen, last_seen) "
                "VALUES (?, ?, ?, ?, ?, ?, 'none', ?, ?)",
                (author, w["gs_id"], w["title"], norm_title(w["title"]), w["year"], w["venue"], today, today),
            )
            work_id = cur.lastrowid
        con.execute(
            "INSERT OR REPLACE INTO work_snapshots(run_id, work_id, source, citations) VALUES (?, ?, 'scholar', ?)",
            (run_id, work_id, w["citations"]),
        )
        n += 1

    p = data["profile"]
    con.execute(
        "INSERT OR REPLACE INTO profile_snapshots(run_id, total_citations, h_reported, i10_reported, "
        "citations_5y, h5_reported, i10_5y_reported) VALUES (?, ?, ?, ?, ?, ?, ?)",
        (run_id, p.get("total"), p.get("h"), p.get("i10"), p.get("c5y"), p.get("h5"), p.get("i10_5y")),
    )
    for y, c in p.get("by_year", {}).items():
        con.execute("INSERT OR REPLACE INTO profile_year_counts(run_id, year, citations) VALUES (?, ?, ?)", (run_id, y, c))
    con.commit()
    return n
