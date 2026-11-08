"""Impact-factor-weighted citations from OpenAlex.

IF proxy = source summary_stats.2yr_mean_citedness (OpenAlex's open analogue of the 2-year Journal Impact
Factor; Clarivate's JIF is proprietary). For each person: (1) unique works published in the window that cite
any of their works, each weighted by the citing journal's IF; (2) their own window papers weighted by their
own journal's IF. Citing works without an IF get zero weight and are counted separately.

Caution: IFs differ several fold by field, so these measures largely track field and venue choice. In a
17-person test the weighted counts ranked people almost exactly as raw counts did (Spearman 0.98).
Prefer FWCI for field-fair comparisons.

authors.json: {"jdoe": {"author_ids": ["A123"]}} or {"jdoe": {"work_ids": ["W1", ...]}}
Usage: OPENALEX_API_KEY=... python3 if_weighted.py authors.json --from 2021 --to 2025 > ifw.csv
"""
from __future__ import annotations
import argparse, csv, json, statistics, sys
from _oa import get, SKIP_TYPES

SEL = "id,title,publication_year,type,cited_by_count,primary_location,is_retracted"


def src(w):
    s = ((w.get("primary_location") or {}).get("source") or {})
    return (s.get("id") or "").split("/")[-1] or None


def own_works(spec):
    W = []
    if spec.get("author_ids"):
        page = 1
        while True:
            r = get("/works", filter="author.id:" + "|".join(spec["author_ids"]), **{"per-page": 200, "page": page, "select": SEL})
            W += r["results"]
            if len(r["results"]) < 200:
                break
            page += 1
    for i in range(0, len(spec.get("work_ids", [])), 50):
        ids = spec["work_ids"][i:i + 50]
        W += get("/works", filter="openalex_id:" + "|".join(ids), **{"per-page": 50, "select": SEL})["results"]
    keep = {}
    for w in W:
        if w.get("type") in SKIP_TYPES or w.get("is_retracted") or not w.get("publication_year"):
            continue
        k = ((w.get("title") or w["id"]).lower()[:120], w["publication_year"])
        if k not in keep or keep[k]["cited_by_count"] < w["cited_by_count"]:
            keep[k] = w
    return list(keep.values())


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("authors"); ap.add_argument("--from", dest="y0", type=int, default=2021); ap.add_argument("--to", dest="y1", type=int, default=2025)
    a = ap.parse_args()
    people = json.load(open(a.authors))
    own, citing = {}, {}
    for pid, spec in people.items():
        P = own[pid] = own_works(spec)
        ids = [w["id"].split("/")[-1] for w in P]
        C = {}
        for i in range(0, len(ids), 50):
            page = 1
            while True:
                r = get("/works", filter="cites:" + "|".join(ids[i:i + 50]) + f",publication_year:{a.y0}-{a.y1}",
                        **{"per-page": 200, "page": page, "select": "id,primary_location"})
                for c in r["results"]:
                    C[c["id"]] = src(c)
                if len(r["results"]) < 200:
                    break
                page += 1
        citing[pid] = C
        print(pid, len(P), "works", len(C), "citing works", file=sys.stderr, flush=True)
    sources = sorted({s for C in citing.values() for s in C.values() if s} | {src(w) for P in own.values() for w in P if src(w)})
    IF = {}
    for i in range(0, len(sources), 50):
        for s in get("/sources", filter="openalex_id:" + "|".join(sources[i:i + 50]), **{"per-page": 50, "select": "id,summary_stats"})["results"]:
            IF[s["id"].split("/")[-1]] = (s.get("summary_stats") or {}).get("2yr_mean_citedness")
    wr = csv.writer(sys.stdout)
    wr.writerow(["id", "citing_works", "citing_with_if", "if_weighted_citations", "mean_citing_venue_if",
                 "papers", "papers_with_if", "if_weighted_papers", "mean_publishing_venue_if"])
    for pid in people:
        known = [IF[s] for s in citing[pid].values() if s and IF.get(s) is not None]
        rec = [w for w in own[pid] if a.y0 <= w["publication_year"] <= a.y1]
        vk = [IF[src(w)] for w in rec if src(w) and IF.get(src(w)) is not None]
        wr.writerow([pid, len(citing[pid]), len(known), round(sum(known), 1), round(statistics.fmean(known), 2) if known else "",
                     len(rec), len(vk), round(sum(vk), 1), round(statistics.fmean(vk), 2) if vk else ""])


if __name__ == "__main__":
    main()
