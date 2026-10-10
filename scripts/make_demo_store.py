"""Build a synthetic multi author data store (no network) for previewing and testing the app.

    python scripts/make_demo_store.py app/tests/fixtures/demo_store
    PUBTRACK_DATA=app/tests/fixtures/demo_store Rscript -e "shiny::runApp('app')"

All names, titles and counts are invented.
"""

import random
import shutil
import sys
from datetime import date, timedelta
from pathlib import Path

from pubtrack import openalex, scholar, store
from pubtrack import sync as syncmod
from pubtrack.config import Author, Config, Settings
from pubtrack.db import connect, init_schema
from pubtrack.metrics import h_index

out = Path(sys.argv[1] if len(sys.argv) > 1 else "app/tests/fixtures/demo_store")
shutil.rmtree(out, ignore_errors=True)
rng = random.Random(42)

SPECS = [  # id, name, n papers, first year, lognormal mu, weeks of history
    ("demo-senior", "Avery Senior", 60, 2002, 3.3, 20),
    ("demo-mid", "Blake Middle", 35, 2010, 3.0, 12),
    ("demo-early", "Casey Early", 14, 2018, 2.5, 6),
]
SUBJECTS = ["crown ratio", "stand density", "site index", "mortality", "diameter growth", "height growth",
            "biomass", "lidar structure", "regeneration", "self thinning", "carbon stocks", "ingrowth"]

con = connect(":memory:")
init_schema(con)
settings = Settings(scholar_via="scholarly")
authors = [Author(id=i, name=n, surname=n.split()[-1], scholar_id=i.upper(), openalex_id=f"A{k}",
                  first_year=fy, scholar_cadence="weekly", group="demo")
           for k, (i, n, _, fy, _, _) in enumerate(SPECS, start=1)]
cfg = Config(settings=settings, authors=authors)

shared = {"title": "A shared paper on crown ratio and stand density across the Acadian region", "year": 2019}
d_end = date(2026, 10, 4)
for (aid, name, n, fy, mu, weeks), au in zip(SPECS, authors):
    papers = []
    for i in range(n):
        yr = rng.randint(fy, 2026)
        age = 2027 - yr
        base = int(rng.lognormvariate(mu, 1.0) * min(age, 12) / 10) + rng.randint(0, 3)
        papers.append({"i": i, "year": yr, "c0": base, "rate": max(0.0, rng.gauss(base / 45, 0.4)),
                       "title": f"{name.split()[0]}'s study {i:02d} of {rng.choice(SUBJECTS)} in mixed stands"})
    papers.append({"i": 99, "year": shared["year"], "c0": 40, "rate": 0.5, "title": shared["title"]})
    for week in range(weeks):
        snap = d_end - timedelta(weeks=weeks - 1 - week)
        gs_works = [{"gs_id": f"{au.scholar_id}:p{p['i']}", "title": p["title"], "year": p["year"],
                     "venue": "Demo Journal", "citations": int(p["c0"] + p["rate"] * week)} for p in papers]
        total = sum(w["citations"] for w in gs_works)
        h = h_index([w["citations"] for w in gs_works])
        by_year = {y: max(1, int(total * (y - fy + 1) ** 1.3 / sum((z - fy + 1) ** 1.3 for z in range(fy, 2027))))
                   for y in range(fy, 2027)}
        scholar.via_scholarly = lambda *a, _w=gs_works, _t=total, _h=h, _b=by_year, **k: {
            "profile": {"total": _t, "h": _h, "i10": sum(w["citations"] >= 10 for w in _w), "c5y": _t // 2,
                        "h5": _h // 2, "i10_5y": 3, "by_year": _b}, "works": _w}
        oa = []
        for p, w in zip(papers, gs_works):
            if p["i"] % 11 == 10:
                continue  # leave some unmatched
            c = int(w["citations"] * 0.7)
            yrs = list(range(max(p["year"], 2017), 2027))
            wts = [(y - p["year"] + 1) * 0.8 ** max(0, y - p["year"] - 3) for y in yrs]
            counts = [{"year": y, "cited_by_count": int(round(c * wt / sum(wts)))} for y, wt in zip(yrs, wts)]
            n_auth = rng.randint(1, 7)
            auths = [{"author": {"id": f"https://openalex.org/A{100 + k}", "display_name": f"Coauthor {k}"}}
                     for k in range(n_auth)]
            auths[rng.randrange(n_auth)] = {"author": {"id": f"https://openalex.org/{au.openalex_id}",
                                                       "display_name": name}}
            wid = "W777" if p["i"] == 99 else f"W{au.openalex_id[1:]}{1000 + p['i']}"
            f = round(rng.lognormvariate(0.1, 0.8), 2)
            oa.append({"id": f"https://openalex.org/{wid}", "doi": f"https://doi.org/10.9999/{wid.lower()}",
                       "display_name": p["title"], "publication_year": p["year"], "type": "article",
                       "authorships": auths, "cited_by_count": c, "counts_by_year": counts, "fwci": f,
                       "citation_normalized_percentile": {"value": min(0.999, f / 4), "is_in_top_1_percent": f > 3.5,
                                                          "is_in_top_10_percent": f > 2.2},
                       "primary_location": {"source": {"display_name": "Demo Journal"}}})
        openalex.fetch_works = lambda *a, _oa=oa, **k: _oa
        st = syncmod.sync(con, cfg, au, snapshot_date=snap.isoformat())
    print(f"{aid}: {weeks} runs, h {st['metrics_scholar']['h']:.0f}")

store.save(con, out)
(out / "authors.yml").write_text(
    "settings:\n  scholar_via: none\nauthors:\n" + "".join(
        f"  - id: {a.id}\n    name: {a.name}\n    openalex_id: {a.openalex_id}\n    first_year: {a.first_year}\n"
        f"    scholar_id: {a.scholar_id}\n    group: demo\n" for a in authors))
print(f"demo store: {out}")
