"""Build a synthetic demo database (no network) for previewing and testing the dashboard.

    python scripts/make_demo_db.py /tmp/pubtrack_demo.sqlite
    PUBTRACK_DB=/tmp/pubtrack_demo.sqlite Rscript -e "shiny::runApp('r')"

All titles and counts are invented.
"""

import random
import sys
from datetime import date, timedelta
from pathlib import Path

from pubtrack import openalex, scholar, sync as syncmod
from pubtrack.config import Config
from pubtrack.db import connect, init_schema

out = Path(sys.argv[1] if len(sys.argv) > 1 else "/tmp/pubtrack_demo.sqlite")
out.unlink(missing_ok=True)
rng = random.Random(42)

N = 40
papers = []
for i in range(N):
    yr = rng.randint(2004, 2025)
    base = int(rng.lognormvariate(3.2, 1.0) * (2026 - yr) / 10) + rng.randint(0, 4)
    papers.append({"i": i, "year": yr, "c0": base, "rate": max(0.0, rng.gauss(base / 40, 0.5)),
                   "title": f"Demo paper {i:02d} on forest growth and yield modeling"})

cfg = Config(scholar_id="DEMO", surname="Demo", orcid="", openalex_id="A1", first_year=2004, mailto="",
             openalex_key="", min_delay=0, max_delay=0, serpapi_key="", db_path=out, path=out)
con = connect(out)
init_schema(con, out)

d0 = date(2026, 6, 7)
for week in range(18):
    snap = d0 + timedelta(weeks=week)
    gs_works = [{"gs_id": f"DEMO:p{p['i']}", "title": p["title"], "year": p["year"], "venue": "Demo Journal",
                 "citations": int(p["c0"] + p["rate"] * week)} for p in papers]
    total = sum(w["citations"] for w in gs_works)
    from pubtrack.metrics import h_index
    h = h_index([w["citations"] for w in gs_works])
    by_year = {y: int(total * (y - 2003) / 300) for y in range(2005, 2027)}
    scholar.via_scholarly = lambda *a, _w=gs_works, _t=total, _h=h, _b=by_year, **k: {
        "profile": {"total": _t, "h": _h, "i10": sum(w["citations"] >= 10 for w in _w), "c5y": _t // 2,
                    "h5": _h // 2, "i10_5y": 3, "by_year": _b}, "works": _w}
    oa = []
    for p, w in zip(papers, gs_works):
        if p["i"] % 9 == 8:
            continue  # leave some unmatched
        c = int(w["citations"] * 0.7)
        counts = [{"year": y, "cited_by_count": max(0, int(c / max(1, 2026 - p["year"])) + rng.randint(-1, 1))}
                  for y in range(max(p["year"], 2017), 2027)]
        n_auth = rng.randint(1, 8)
        auths = [{"author": {"id": f"https://openalex.org/A{100 + k}", "display_name": f"Coauthor {k}"}} for k in range(n_auth)]
        auths[rng.randrange(n_auth)] = {"author": {"id": "https://openalex.org/A1", "display_name": "Demo Author"}}
        f = round(rng.lognormvariate(0.1, 0.8), 2)
        oa.append({"id": f"https://openalex.org/W{1000 + p['i']}", "doi": f"https://doi.org/10.9999/demo.{p['i']}",
                   "display_name": p["title"], "publication_year": p["year"], "authorships": auths,
                   "cited_by_count": c, "counts_by_year": counts, "fwci": f,
                   "citation_normalized_percentile": {"value": min(0.999, f / 4), "is_in_top_1_percent": f > 3.5,
                                                      "is_in_top_10_percent": f > 2.2},
                   "primary_location": {"source": {"display_name": "Demo Journal"}}})
    openalex.fetch_works = lambda *a, _oa=oa, **k: _oa
    st = syncmod.sync(con, cfg, snapshot_date=snap.isoformat())
print(f"demo database: {out}  runs: 18  last h: {st['metrics_scholar']['h']:.0f}")
