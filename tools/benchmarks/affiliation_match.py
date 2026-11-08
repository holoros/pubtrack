"""Collect an author's works when OpenAlex has split or merged their record with namesakes.

A work counts when one authorship has a raw author name matching the person's pattern AND an affiliation
at an institution they held, within the years they held it. Optional exclusion regex on topic and title.

people.json example:
  {"jdoe": {"name_regex": "^(jane\\s+doe|doe,?\\s*jane)$", "search": "Doe",
            "spells": [{"ror": "https://ror.org/01adr0w49", "from": 2018, "to": 2026}],
            "exclude_regex": "concrete|cement"}}
Usage: OPENALEX_API_KEY=... python3 affiliation_match.py people.json > matched.json
"""
from __future__ import annotations
import json, re, sys
from _oa import get, h_of, SKIP_TYPES


def institution(ror: str) -> str:
    return get("/institutions", filter=f"ror:{ror}")["results"][0]["id"].split("/")[-1]


def collect(spec: dict) -> list[dict]:
    pat = re.compile(spec["name_regex"], re.I)
    bad = re.compile(spec["exclude_regex"], re.I) if spec.get("exclude_regex") else None
    works = {}
    for sp in spec["spells"]:
        inst = institution(sp["ror"])
        page = 1
        while True:
            r = get("/works", filter=f"authorships.institutions.id:{inst},raw_author_name.search:{spec['search']}",
                    **{"per-page": 200, "page": page, "select": "id,title,publication_year,type,cited_by_count,"
                       "counts_by_year,authorships,is_retracted,primary_topic"})
            for w in r["results"]:
                y = w.get("publication_year")
                if not y or not (sp["from"] <= y <= sp["to"]):
                    continue
                text = ((w.get("primary_topic") or {}).get("display_name") or "") + " " + (w.get("title") or "")
                if bad and bad.search(text):
                    continue
                for au in w["authorships"]:
                    if pat.match((au.get("raw_author_name") or "").strip()) and \
                            any((i.get("id") or "").endswith(inst) for i in au.get("institutions", [])):
                        works[w["id"]] = w
            if len(r["results"]) < 200:
                break
            page += 1
    keep = {}
    for w in works.values():
        if w.get("type") in SKIP_TYPES or w.get("is_retracted"):
            continue
        k = ((w.get("title") or w["id"]).lower()[:120], w["publication_year"])
        if k not in keep or keep[k]["cited_by_count"] < w["cited_by_count"]:
            keep[k] = w
    return list(keep.values())


def main() -> None:
    people = json.load(open(sys.argv[1]))
    out = {}
    for pid, spec in people.items():
        P = collect(spec)
        out[pid] = dict(ids=[w["id"].split("/")[-1] for w in P], works=len(P), h=h_of(w["cited_by_count"] for w in P),
                        first_year=min((w["publication_year"] for w in P), default=None),
                        titles=sorted((w["publication_year"], (w.get("title") or "")[:80]) for w in P))
    json.dump(out, sys.stdout, indent=1)


if __name__ == "__main__":
    main()
