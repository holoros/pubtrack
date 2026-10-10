"""End to end sync against fixtures: no network."""

import csv
from datetime import date, timedelta
from pathlib import Path

import pytest

from pubtrack import config as cfgmod
from pubtrack import export, metrics, overrides, scholar, openalex, sync as syncmod
from pubtrack.db import connect, init_schema

GS_WORKS = [
    {"gs_id": "AUTH:p1", "title": "A generalized Beta regression model for crown ratio", "year": 2010, "venue": "For Sci", "citations": 40},
    {"gs_id": "AUTH:p2", "title": "Maximum stand density for mixed species stands", "year": 2015, "venue": "FEM", "citations": 25},
    {"gs_id": "AUTH:p3", "title": "Remote sensing of forest structure: a review", "year": 2018, "venue": "RSE", "citations": 12},
    {"gs_id": "AUTH:p4", "title": "Maximum stand density for mixed-species stands", "year": 2015, "venue": "arXiv", "citations": 3},
    {"gs_id": "AUTH:p5", "title": "An unpublished thesis chapter", "year": 2009, "venue": None, "citations": 2},
]


def oa_work(wid, title, year, cites, doi, n_auth, pos, fwci, top10, counts):
    auths = [{"author": {"id": f"https://openalex.org/A{i}", "display_name": f"Person {i}"}} for i in range(n_auth)]
    auths[pos - 1] = {"author": {"id": "https://openalex.org/A999", "display_name": "Aaron R. Weiskittel"}}
    return {"id": f"https://openalex.org/{wid}", "doi": f"https://doi.org/{doi}", "display_name": title,
            "publication_year": year, "authorships": auths, "cited_by_count": cites,
            "counts_by_year": [{"year": y, "cited_by_count": n} for y, n in counts],
            "fwci": fwci, "citation_normalized_percentile": {"value": 0.9, "is_in_top_1_percent": False,
                                                              "is_in_top_10_percent": top10},
            "primary_location": {"source": {"display_name": "Venue"}}}


OA_WORKS = [
    oa_work("W1", "A generalized beta regression model for crown ratio", 2010, 30, "10.1/a", 3, 1, 1.8, True, [(2025, 4), (2024, 5)]),
    oa_work("W2", "Maximum stand density for mixed species stands", 2015, 20, "10.1/b", 4, 2, 1.1, False, [(2025, 3), (2024, 2)]),
    oa_work("W3", "Remote sensing of forest structure - a review!", 2019, 10, "10.1/c", 2, 2, 0.7, False, [(2025, 2)]),
    oa_work("W9", "Something OpenAlex attributes to the author", 2020, 1, "10.1/z", 5, 5, 0.2, False, []),
]


@pytest.fixture
def env(tmp_path, monkeypatch):
    cfg_path = tmp_path / "config.toml"
    monkeypatch.setenv("PUBTRACK_CONFIG", str(cfg_path))
    cfgmod.write_template(cfg_path)
    cfgmod.set_value("author", "scholar_id", "AUTH", cfg_path)
    cfgmod.set_value("author", "openalex_id", "A999", cfg_path)
    cfgmod.set_value("storage", "db_path", str(tmp_path / "t.sqlite"), cfg_path)
    cfg = cfgmod.load(cfg_path)
    con = connect(cfg.db_path)
    init_schema(con, cfg.db_path)

    state = {"gs": [dict(w) for w in GS_WORKS], "fail": False, "h": 3}

    def fake_gs(*a, **k):
        if state["fail"]:
            raise scholar.ScholarBlocked("CAPTCHA")
        total = sum(w["citations"] for w in state["gs"])
        return {"profile": {"total": total, "h": state["h"], "i10": 3, "c5y": 30, "h5": 2, "i10_5y": 1,
                            "by_year": {2024: 10, 2025: 12}}, "works": [dict(w) for w in state["gs"]]}

    monkeypatch.setattr(scholar, "via_scholarly", fake_gs)
    monkeypatch.setattr(openalex, "fetch_works", lambda *a, **k: OA_WORKS)
    return con, cfg, state


def test_first_sync(env):
    con, cfg, _ = env
    st = syncmod.sync(con, cfg, snapshot_date="2026-10-04")
    assert st["scholar_status"] == "ok" and st["openalex_status"] == "ok"
    mt = st["matching"]
    assert mt["title_exact"] >= 1 and mt["title_fuzzy"] + mt["title_exact"] == 3
    assert mt["openalex_only"] == 1
    w1 = con.execute("SELECT * FROM works WHERE gs_id = 'AUTH:p1'").fetchone()
    assert w1["openalex_id"] == "W1" and w1["doi"] == "10.1/a" and w1["n_authors"] == 3 and w1["author_position"] == 1
    # p2 (exact) must not be stolen by its duplicate p4
    assert con.execute("SELECT openalex_id FROM works WHERE gs_id = 'AUTH:p2'").fetchone()[0] == "W2"
    assert con.execute("SELECT openalex_id FROM works WHERE gs_id = 'AUTH:p4'").fetchone()[0] is None
    # Scholar metrics: c = 40, 25, 12, 3, 2 -> h 3
    assert st["metrics_scholar"]["h"] == 3
    assert st["metrics_openalex"]["fwci_median"] == pytest.approx(0.9)
    assert "velocity_per_year" not in st["metrics_scholar"]


def test_overrides_and_checks(env):
    con, cfg, state = env
    syncmod.sync(con, cfg, snapshot_date="2026-10-04")
    overrides.add(con, "merge", "AUTH:p4", "AUTH:p2")
    overrides.add(con, "exclude", "AUTH:p5")
    state["gs"][0]["citations"] = 30          # drop of 10 -> flagged
    state["h"] = 9                            # forces an h mismatch flag
    st = syncmod.sync(con, cfg, snapshot_date="2026-10-11")
    ms = st["metrics_scholar"]
    assert ms["n_papers"] == 3                # p4 folded into p2, p5 excluded
    assert ms["citations"] == 30 + 28 + 12
    kinds = {r[0] for r in con.execute("SELECT kind FROM flags WHERE run_id = ?", (st["run_id"],))}
    assert {"drop", "h_mismatch"} <= kinds


def test_scholar_failure_keeps_openalex(env):
    con, cfg, state = env
    state["fail"] = True
    st = syncmod.sync(con, cfg, snapshot_date="2026-10-04")
    assert st["scholar_status"] == "failed" and st["openalex_status"] == "ok"
    assert "metrics_openalex" in st and "metrics_scholar" not in st
    assert con.execute("SELECT message FROM runs WHERE run_id = ?", (st["run_id"],)).fetchone()[0].startswith("Scholar")


def test_velocity_after_eight_weeks(env):
    con, cfg, state = env
    d0 = date(2026, 8, 2)
    for i in range(9):
        state["gs"][0]["citations"] = 40 + 2 * i
        st = syncmod.sync(con, cfg, snapshot_date=(d0 + timedelta(weeks=i)).isoformat())
    v = st["metrics_scholar"]["velocity_per_year"]
    assert v == pytest.approx(2 * 365.25 / 7, rel=1e-6)


def test_exports_and_pop_import(env, tmp_path):
    con, cfg, _ = env
    syncmod.sync(con, cfg, snapshot_date="2026-10-04")
    rows = export.paper_rows(con)
    assert len(rows) == 6 and rows[0]["scholar_citations"] == 40
    paths = export.annual_summary(con, 2015, tmp_path)
    assert all(p.exists() for p in paths)

    pop = tmp_path / "pop.csv"
    with pop.open("w", newline="") as f:
        w = csv.writer(f)
        w.writerow(["Cites", "Authors", "Title", "Year", "Source", "QueryDate"])
        w.writerow([20, "A Weiskittel", "A generalized Beta regression model for crown ratio", 2010, "For Sci", "2019-05-01 10:00:00"])
        w.writerow([9, "A Weiskittel", "Maximum stand density for mixed species stands", 2015, "FEM", "2019-05-01 10:00:00"])
        w.writerow([1, "Someone", "Not on the profile at all", 2012, "X", "2019-05-01 10:00:00"])
    res = export.import_pop(con, pop)
    assert res["snapshot_date"] == "2019-05-01" and res["loaded"] == 2 and len(res["skipped"]) == 1
    assert con.execute("SELECT value FROM metrics WHERE run_id = ? AND metric = 'h'", (res["run_id"],)).fetchone()[0] == 2
