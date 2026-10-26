"""OpenAlex collector: DOIs, author lists, yearly counts, FWCI and percentiles."""

from __future__ import annotations

import time

import requests

from .db import redact

BASE = "https://api.openalex.org"
SELECT = ",".join([
    "id", "doi", "display_name", "publication_year", "type", "authorships",
    "cited_by_count", "counts_by_year", "fwci", "citation_normalized_percentile",
    "primary_location",
])


# Not scholarly output in the citation sense; kept out so they never inflate paper counts.
SKIP_TYPES = {"erratum", "retraction", "paratext", "peer-review", "grant", "supplementary-materials"}


class OpenAlexError(RuntimeError):
    pass


def _params(mailto: str, api_key: str, **kw) -> dict:
    p = dict(kw)
    if mailto:
        p["mailto"] = mailto
    if api_key:
        p["api_key"] = api_key
    return p


def _get(path: str, params: dict, tries: int = 4) -> dict:
    for i in range(tries):
        try:
            r = requests.get(f"{BASE}{path}", params=params, timeout=60)
        except requests.RequestException as e:
            if i < tries - 1:
                time.sleep(2 ** (i + 1))
                continue
            # The message carries the full URL, api_key included.
            raise OpenAlexError(f"OpenAlex request failed on {path}: "
                                f"{type(e).__name__}: {redact(str(e), params.get('api_key', ''))}") from None
        if r.status_code == 200:
            return r.json()
        if r.status_code in (429, 500, 502, 503) and i < tries - 1:
            time.sleep(2 ** (i + 1))
            continue
        raise OpenAlexError(redact(f"OpenAlex HTTP {r.status_code} on {path}: {r.text[:200]}", params.get("api_key", "")))
    raise OpenAlexError("unreachable")


def short_id(url_or_id: str | None) -> str | None:
    if not url_or_id:
        return None
    return url_or_id.rstrip("/").split("/")[-1]


def resolve_author(orcid: str = "", name: str = "", mailto: str = "", api_key: str = "") -> list[dict]:
    """ORCID gives one exact author; a name search returns candidates to choose from."""
    if orcid:
        js = _get(f"/authors/orcid:{orcid}", _params(mailto, api_key))
        return [js]
    js = _get("/authors", _params(mailto, api_key, search=name, per_page=10))
    return js.get("results", [])


def search_title(title: str, mailto: str = "", api_key: str = "", per_page: int = 5) -> list[dict]:
    """OpenAlex full text title search, for works missing from the author's OpenAlex record."""
    from .db import norm_title

    # Plain words only: parentheses, ellipses and similar characters make the endpoint return 500.
    q = " ".join(norm_title(title.replace("\u2026", " ")).split()[:16])
    js = _get("/works", _params(mailto, api_key, filter=f"title.search:{q}", select=SELECT, per_page=per_page))
    return js.get("results", [])


PAGE_LIMIT = 10_000  # OpenAlex basic paging stops at 10,000 results; cursor paging has no limit


def fetch_works(author_ids: list[str], mailto: str = "", api_key: str = "") -> list[dict]:
    """All works for one or more OpenAlex author ids (author disambiguation can split a person).

    Since October 2026 OpenAlex returns 401 on cursor paging past the first page without an API
    key. With a key we page by cursor (no result limit); without one we page by number, which stays
    free up to 10,000 results, far beyond any single author's record."""
    flt = "author.id:" + "|".join(author_ids)
    out: list[dict] = []
    if api_key:
        cursor = "*"
        while cursor:
            js = _get("/works", _params(mailto, api_key, filter=flt, select=SELECT, per_page=200, cursor=cursor))
            out.extend(js.get("results", []))
            cursor = js.get("meta", {}).get("next_cursor")
            if not js.get("results"):
                break
        return out
    page = 1
    while True:
        js = _get("/works", _params(mailto, api_key, filter=flt, select=SELECT, per_page=200, page=page))
        res = js.get("results", [])
        out.extend(res)
        total = int((js.get("meta") or {}).get("count") or 0)
        if not res or len(out) >= total:
            break
        page += 1
        if (page - 1) * 200 >= PAGE_LIMIT:
            raise OpenAlexError(f"record exceeds {PAGE_LIMIT} works; set OPENALEX_API_KEY for cursor paging")
    return out


def _author_fields(work: dict, author_ids: set[str], surname: str) -> tuple[str, int, int | None, int | None]:
    auths = work.get("authorships") or []
    names = [((a.get("author") or {}).get("display_name") or "") for a in auths]
    pos = None
    for i, a in enumerate(auths, start=1):
        if short_id((a.get("author") or {}).get("id")) in author_ids:
            pos = i
            break
    if pos is None and surname:
        for i, nm in enumerate(names, start=1):
            if surname.lower() in nm.lower():
                pos = i
                break
    n = len(auths) or None
    is_last = int(pos == n) if pos and n else None
    return "; ".join(names), n, pos, is_last


def store(con, run_id: int, works: list[dict], author_ids: list[str], surname: str, today: str) -> int:
    """Upsert by openalex_id. Works not yet linked to a Scholar record are stored as OpenAlex only rows;
    matching.reconcile() pairs them with Scholar rows afterwards."""
    from .db import norm_title, run_author

    author = run_author(con, run_id)
    ids = set(author_ids)
    n = 0
    for w in works:
        oid = short_id(w.get("id"))
        title = (w.get("display_name") or "").strip()
        if not oid or not title:
            continue
        if (w.get("type") or "") in SKIP_TYPES:
            continue
        doi = (w.get("doi") or "").replace("https://doi.org/", "").lower() or None
        authors, n_auth, pos, is_last = _author_fields(w, ids, surname)
        venue = (((w.get("primary_location") or {}).get("source")) or {}).get("display_name")
        row = con.execute("SELECT work_id FROM works WHERE author_id = ? AND openalex_id = ?",
                          (author, oid)).fetchone()
        if row:
            work_id = row["work_id"]
            con.execute(
                "UPDATE works SET doi = COALESCE(doi, ?), authors = ?, n_authors = ?, author_position = ?, "
                "is_last_author = ?, year = COALESCE(year, ?), venue = COALESCE(venue, ?), last_seen = ? WHERE work_id = ?",
                (doi, authors, n_auth, pos, is_last, w.get("publication_year"), venue, today, work_id),
            )
        else:
            cur = con.execute(
                "INSERT INTO works(author_id, openalex_id, doi, title, title_norm, year, venue, authors, n_authors, "
                "author_position, is_last_author, match_method, first_seen, last_seen) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'none', ?, ?)",
                (author, oid, doi, title, norm_title(title), w.get("publication_year"), venue, authors, n_auth,
                 pos, is_last, today, today),
            )
            work_id = cur.lastrowid
        con.execute(
            "INSERT OR REPLACE INTO work_snapshots(run_id, work_id, source, citations) VALUES (?, ?, 'openalex', ?)",
            (run_id, work_id, int(w.get("cited_by_count") or 0)),
        )
        for c in w.get("counts_by_year") or []:
            con.execute(
                "INSERT OR REPLACE INTO work_year_counts(work_id, year, source, citations, run_id) "
                "VALUES (?, ?, 'openalex', ?, ?)",
                (work_id, int(c["year"]), int(c.get("cited_by_count") or 0), run_id),
            )
        pct = w.get("citation_normalized_percentile") or {}
        con.execute(
            "INSERT OR REPLACE INTO work_impact(run_id, work_id, fwci, pct_value, top1, top10) VALUES (?, ?, ?, ?, ?, ?)",
            (run_id, work_id, w.get("fwci"), pct.get("value"),
             int(bool(pct.get("is_in_top_1_percent"))) if pct else None,
             int(bool(pct.get("is_in_top_10_percent"))) if pct else None),
        )
        n += 1
    con.commit()
    return n
