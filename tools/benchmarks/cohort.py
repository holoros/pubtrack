"""Build a reference cohort for career-stage benchmarks.

Pulls every author matching an OpenAlex filter (default: US authors whose topics fall in subfield
1107 Forestry, 15 to 1,500 works, with an ORCID), then one call per author for the first publication
year. Writes cohort.csv with citations received and works per year for the last ten complete years.

Usage: OPENALEX_API_KEY=... python3 cohort.py [--filter F] [--out cohort.csv] [--threads 6]
About one API call per author; a free key covers about 10,000 calls a day.
"""
from __future__ import annotations
import argparse, csv, time
from concurrent.futures import ThreadPoolExecutor
from datetime import date
from _oa import get

DEFAULT = "topics.subfield.id:1107,last_known_institutions.country_code:US,works_count:15-1500,has_orcid:true"


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--filter", default=DEFAULT)
    ap.add_argument("--out", default="cohort.csv")
    ap.add_argument("--threads", type=int, default=6)
    a = ap.parse_args()
    y1 = date.today().year - 1
    years = range(y1 - 9, y1 + 1)
    auth, page = [], 1
    while True:
        r = get("/authors", filter=a.filter, **{"per-page": 200, "page": page,
                "select": "id,works_count,cited_by_count,counts_by_year,summary_stats,topics"})
        auth += r["results"]
        if len(r["results"]) < 200:
            break
        page += 1
        time.sleep(0.2)
    print("authors", len(auth), flush=True)

    def one(au: dict) -> dict:
        aid = au["id"].split("/")[-1]
        w = get("/works", filter=f"author.id:{aid},type:article|review|book|book-chapter",
                sort="publication_year:asc", **{"per-page": 1, "select": "publication_year"})
        fy = w["results"][0]["publication_year"] if w.get("results") else None
        cby = {x["year"]: x for x in au.get("counts_by_year", [])}
        row = dict(id=aid, works=au["works_count"], citations=au["cited_by_count"],
                   h=(au.get("summary_stats") or {}).get("h_index"), first_year=fy)
        for y in years:
            row[f"c{y}"] = cby.get(y, {}).get("cited_by_count", 0)
            row[f"w{y}"] = cby.get(y, {}).get("works_count", 0)
        return row

    with ThreadPoolExecutor(a.threads) as ex:
        out = list(ex.map(one, auth))
    with open(a.out, "w", newline="") as f:
        wr = csv.DictWriter(f, fieldnames=list(out[0])); wr.writeheader(); wr.writerows(out)
    print("done", len(out))


if __name__ == "__main__":
    main()
