"""End to end sync against fixtures: no network."""

import csv
from datetime import date, timedelta
from pathlib import Path

import pytest

from pubtrack import config as cfgmod
from pubtrack import export, metrics, overrides, scholar, openalex, store, sync as syncmod
from pubtrack.db import connect, init_schema

FIXTURE_V1 = Path(__file__).resolve().parents[1] / "app" / "tests" / "fixtures" / "demo_v1.sqlite"
AUTHORS_YML = """
settings:
  scholar_via: scholarly
  serpapi_monthly_budget: 10
authors:
  - id: auth
    name: Aaron R. Weiskittel
    scholar_id: AUTH
    openalex_id: A999
    scholar_cadence: weekly
  - id: peer
    name: Pat Peer
    scholar_id: PEER
    openalex_id: A555
    scholar_cadence: monthly
"""

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
            "publication_year": year, "type": "article", "authorships": auths, "cited_by_count": cites,
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
    yml = tmp_path / "authors.yml"
    yml.write_text(AUTHORS_YML)
    cfg = cfgmod.load(yml)
    con = connect(":memory:")
    init_schema(con)
    state = {"gs": [dict(w) for w in GS_WORKS], "fail": False, "h": 3}

    def fake_gs(*a, **k):
        if state["fail"]:
            raise scholar.ScholarBlocked("CAPTCHA")
        total = sum(w["citations"] for w in state["gs"])
        return {"profile": {"total": total, "h": state["h"], "i10": 3, "c5y": 30, "h5": 2, "i10_5y": 1,
                            "by_year": {2024: 10, 2025: 12}}, "works": [dict(w) for w in state["gs"]]}

    monkeypatch.setattr(scholar, "via_scholarly", fake_gs)
    monkeypatch.setattr(openalex, "fetch_works", lambda *a, **k: OA_WORKS)
    monkeypatch.setattr(openalex, "search_title", lambda *a, **k: state.get("search", []))
    # by-id refetch serves whatever the state holds under that id (title search hits live here)
    monkeypatch.setattr(openalex, "fetch_by_ids",
                        lambda ids, *a, **k: [w for w in state.get("by_id", []) if openalex.short_id(w["id"]) in ids])
    return con, cfg, state


def S(con, cfg, d, author="auth", **k):
    return syncmod.sync(con, cfg, cfg.author(author), snapshot_date=d, **k)


def test_first_sync(env):
    con, cfg, _ = env
    st = S(con, cfg, "2026-10-04")
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
    S(con, cfg, "2026-10-04")
    overrides.add(con, "auth", "merge", "AUTH:p4", "AUTH:p2")
    overrides.add(con, "auth", "exclude", "AUTH:p5")
    state["gs"][0]["citations"] = 30          # drop of 10 -> flagged
    state["h"] = 9                            # forces an h mismatch flag
    st = S(con, cfg, "2026-10-11")
    ms = st["metrics_scholar"]
    assert ms["n_papers"] == 3                # p4 folded into p2, p5 excluded
    assert ms["citations"] == 30 + 28 + 12
    kinds = {r[0] for r in con.execute("SELECT kind FROM flags WHERE run_id = ?", (st["run_id"],))}
    assert {"drop", "h_mismatch"} <= kinds


def test_scholar_failure_keeps_openalex(env):
    con, cfg, state = env
    state["fail"] = True
    st = S(con, cfg, "2026-10-04")
    assert st["scholar_status"] == "failed" and st["openalex_status"] == "ok"
    assert "metrics_openalex" in st and "metrics_scholar" not in st
    assert con.execute("SELECT message FROM runs WHERE run_id = ?", (st["run_id"],)).fetchone()[0].startswith("Scholar")


def test_velocity_after_eight_weeks(env):
    con, cfg, state = env
    d0 = date(2026, 8, 2)
    for i in range(9):
        state["gs"][0]["citations"] = 40 + 2 * i
        st = S(con, cfg, (d0 + timedelta(weeks=i)).isoformat())
    v = st["metrics_scholar"]["velocity_per_year"]
    assert v == pytest.approx(2 * 365.25 / 7, rel=1e-6)


def test_exports_and_pop_import(env, tmp_path):
    con, cfg, _ = env
    S(con, cfg, "2026-10-04")
    rows = export.paper_rows(con, "auth")
    assert len(rows) == 6 and rows[0]["scholar_citations"] == 40
    paths = export.annual_summary(con, "auth", 2015, tmp_path)
    assert all(p.exists() for p in paths)

    pop = tmp_path / "pop.csv"
    with pop.open("w", newline="") as f:
        w = csv.writer(f)
        w.writerow(["Cites", "Authors", "Title", "Year", "Source", "QueryDate"])
        w.writerow([20, "A Weiskittel", "A generalized Beta regression model for crown ratio", 2010, "For Sci", "2019-05-01 10:00:00"])
        w.writerow([9, "A Weiskittel", "Maximum stand density for mixed species stands", 2015, "FEM", "2019-05-01 10:00:00"])
        w.writerow([1, "Someone", "Not on the profile at all", 2012, "X", "2019-05-01 10:00:00"])
    res = export.import_pop(con, "auth", pop)
    assert res["snapshot_date"] == "2019-05-01" and res["loaded"] == 2 and len(res["skipped"]) == 1
    assert con.execute("SELECT value FROM metrics WHERE run_id = ? AND metric = 'h'", (res["run_id"],)).fetchone()[0] == 2



def test_two_authors_share_a_paper(env):
    con, cfg, _ = env
    a = S(con, cfg, "2026-10-04", "auth")
    b = S(con, cfg, "2026-10-04", "peer")
    assert a["openalex_status"] == b["openalex_status"] == "ok"
    # the same OpenAlex work is stored once per author, each matched to that author's Scholar entry
    rows = con.execute("SELECT author_id, gs_id FROM works WHERE openalex_id = 'W1' ORDER BY author_id").fetchall()
    assert [tuple(r) for r in rows] == [("auth", "AUTH:p1"), ("peer", "AUTH:p1")]
    assert a["metrics_scholar"]["h"] == b["metrics_scholar"]["h"] == 3
    # metrics of one author never read the other's runs
    assert con.execute("SELECT COUNT(DISTINCT author_id) FROM runs").fetchone()[0] == 2


def test_csv_round_trip(env, tmp_path):
    con, cfg, state = env
    S(con, cfg, "2026-10-04")
    overrides.add(con, "auth", "exclude", "AUTH:p5")
    S(con, cfg, "2026-10-11")
    d1 = tmp_path / "d1"
    store.save(con, d1)
    con2 = store.load(d1)
    d2 = tmp_path / "d2"
    store.save(con2, d2)
    for f in d1.glob("*.csv"):
        assert f.read_bytes() == (d2 / f.name).read_bytes(), f.name
    # ids keep counting after a reload
    st = S(con2, cfg, "2026-10-18")
    assert st["run_id"] == 3
    assert con2.execute("SELECT COUNT(*) FROM works WHERE author_id = 'auth'").fetchone()[0] == 6


def test_plan_cadence_and_budget(env):
    con, cfg, _ = env
    cfg.settings.scholar_via = "serpapi"
    today = date(2026, 10, 10)
    p = {x.author.id: x for x in syncmod.plan(con, cfg, today=today)}
    # budget 10, both new authors guessed at 5 each: both fit
    assert p["auth"].scholar and p["peer"].scholar and p["auth"].openalex
    con.execute("INSERT INTO runs(author_id, started, snapshot_date, origin, scholar_status, openalex_status, "
                "serpapi_calls, n_scholar) VALUES ('auth', ?, '2026-10-08', 'sync', 'ok', 'ok', 6, 350)",
                (today.isoformat() + "T03:00:00+00:00",))
    p = {x.author.id: x for x in syncmod.plan(con, cfg, today=today)}
    assert not p["auth"].scholar and not p["auth"].openalex           # synced 2 days ago
    assert p["peer"].scholar is False and "deferred" in p["peer"].reason  # 10 - 6 = 4 left < 5 needed
    assert p["peer"].openalex                                          # OpenAlex never waits on the budget


def test_import_v1(env):
    con, cfg, _ = env
    S(con, cfg, "2026-10-04", "peer")
    n = export.import_v1(con, FIXTURE_V1, "auth")
    assert n["runs"] == 18 and n["works"] > 30
    assert con.execute("SELECT COUNT(*) FROM runs WHERE author_id = 'auth'").fetchone()[0] == 18
    assert con.execute("PRAGMA foreign_key_check").fetchall() == []
    # the imported author syncs on top of its history
    st = S(con, cfg, "2026-10-12", "auth")
    assert st["scholar_status"] == "ok"



def test_title_search_fallback(env):
    con, cfg, state = env
    # a Scholar paper OpenAlex did not attribute to the author, found by title search
    state["gs"].append({"gs_id": "AUTH:p6", "title": "Growth of red spruce under partial harvesting in Maine",
                        "year": 2012, "venue": "CJFR", "citations": 30})
    state["search"] = [oa_work("W42", "Growth of red spruce under partial harvesting in Maine", 2012, 22, "10.1/r",
                               3, 2, 1.3, False, [(2025, 3)])]
    st = S(con, cfg, "2026-10-04")
    assert st["matching"]["title_search"]["linked"] >= 1
    w = con.execute("SELECT openalex_id, match_method, author_position FROM works WHERE gs_id = 'AUTH:p6'").fetchone()
    assert w["openalex_id"] == "W42" and w["match_method"] == "title_search" and w["author_position"] == 2
    # each unmatched work is searched once; a second run does not search again
    n = con.execute("SELECT COUNT(*) FROM title_lookups").fetchone()[0]
    # The author filter never returns W42 (it sits on another author id); the next run must fetch it
    # by id so its snapshot and counts refresh instead of silently dropping out of the OpenAlex record.
    state["by_id"] = [oa_work("W42", "Growth of red spruce under partial harvesting in Maine", 2012, 25, "10.1/r",
                              3, 2, 1.3, False, [(2025, 3), (2026, 3)])]
    st2 = S(con, cfg, "2026-10-11")
    assert con.execute("SELECT COUNT(*) FROM title_lookups").fetchone()[0] == n
    assert st2["n_openalex_by_id"] == 1
    snap = con.execute("SELECT citations FROM work_snapshots WHERE run_id = ? AND work_id = ? AND source = 'openalex'",
                       (st2["run_id"], w_id := con.execute("SELECT work_id FROM works WHERE openalex_id = 'W42'").fetchone()[0])).fetchone()
    assert snap is not None and snap[0] == 25
    assert st2["metrics_openalex"]["n_papers"] == st["metrics_openalex"]["n_papers"]
    # a linked id that no longer resolves is flagged, not dropped silently
    state["by_id"] = []
    st3 = S(con, cfg, "2026-10-18")
    assert any(r[0] == "stale_link" for r in con.execute("SELECT kind FROM flags WHERE run_id = ?", (st3["run_id"],)))


def test_key_never_reaches_the_store(env, monkeypatch):
    con, cfg, state = env
    cfg.settings.openalex_key = "OAKEY987"

    def boom(*a, **k):
        raise openalex.OpenAlexError("OpenAlex request failed: url /works?api_key=OAKEY987")

    monkeypatch.setattr(openalex, "fetch_works", boom)
    st = S(con, cfg, "2026-10-04")
    msg = con.execute("SELECT message FROM runs WHERE run_id = ?", (st["run_id"],)).fetchone()[0]
    assert "OpenAlex" in msg and "OAKEY987" not in msg and all("OAKEY987" not in m for m in st["messages"])


def test_openalex_request_error_is_redacted(monkeypatch):
    def get(*a, **k):
        raise openalex.requests.ConnectionError("Max retries exceeded with url: /works?api_key=OAKEY987")

    monkeypatch.setattr(openalex.requests, "get", get)
    monkeypatch.setattr(openalex.time, "sleep", lambda s: None)
    with pytest.raises(openalex.OpenAlexError) as e:
        openalex.resolve_author("0000-0000-0000-0000", api_key="OAKEY987")
    assert "OAKEY987" not in str(e.value) and e.value.__cause__ is None


def test_plan_puts_today_last_and_never_synced_first(env):
    con, cfg, _ = env
    cfg.settings.scholar_via = "serpapi"
    cfg.settings.serpapi_monthly_budget = 5
    today = date(2026, 10, 10)
    con.execute("INSERT INTO runs(author_id, started, snapshot_date, origin, scholar_status, serpapi_calls, n_scholar) "
                "VALUES ('auth', '2026-09-01T03:00:00+00:00', ?, 'sync', 'ok', 0, 350)", (today.isoformat(),))
    # auth synced today (0 days), peer never: with room for one, the forced refresh goes to peer
    p = {x.author.id: x for x in syncmod.plan(con, cfg, today=today, force_scholar=True)}
    assert p["peer"].scholar and not p["auth"].scholar


def test_set_author_field_adds_missing_key(tmp_path):
    yml = tmp_path / "authors.yml"
    yml.write_text("authors:\n  - id: a\n    name: A\n    orcid: 1\n\n  - id: b\n    name: B\n    openalex_id: A1\n")
    cfgmod.set_author_field(yml, "a", "openalex_id", "A5")
    cfgmod.set_author_field(yml, "b", "openalex_id", 'A2"x')
    cfg = cfgmod.load(yml)
    assert cfg.author("a").openalex_id == "A5" and cfg.author("b").openalex_id == 'A2"x'
    assert cfg.author("a").orcid == "1" and cfg.author("b").name == "B"
    with pytest.raises(KeyError):
        cfgmod.set_author_field(yml, "zz", "openalex_id", "A9")



def test_title_search_skips_non_research_hit(env):
    con, cfg, state = env
    state["gs"].append({"gs_id": "AUTH:p7", "title": "Correction to growth of red spruce under partial harvesting",
                        "year": 2013, "venue": "CJFR", "citations": 0})
    hit = oa_work("W43", "Correction to growth of red spruce under partial harvesting", 2013, 0, "10.1/e", 2, 1, None, False, [])
    hit["type"] = "erratum"
    state["search"] = [hit]
    st = S(con, cfg, "2026-10-04")
    assert "title search fallback" not in " ".join(st["messages"])
    assert con.execute("SELECT openalex_id FROM works WHERE gs_id = 'AUTH:p7'").fetchone()[0] is None


def test_annualized_metrics(env):
    con, cfg, _ = env
    st = S(con, cfg, "2026-10-04")
    ms, mo = st["metrics_scholar"], st["metrics_openalex"]
    # Scholar: 5 papers, first year 2009 -> academic age 18 (2009..2026)
    assert ms["academic_age"] == 18
    assert ms["papers_per_year"] == pytest.approx(5 / 18)
    assert ms["i10_per_year"] == pytest.approx(ms["i10"] / 18)
    # the window and recent blocks are OpenAlex only; Scholar keeps its histogram under its own name
    assert "papers_5y" not in ms and "cites_5y" not in ms
    assert ms["cites_5y_scholar"] == 22 and ms["cites_per_year_5y_scholar"] == pytest.approx(22 / 5)
    # Scholar's reported five year values keep their own names: their window is six calendar years
    assert ms["h5_reported"] == 2 and ms["i10_5y_reported"] == 1 and ms["cites_since_reported"] == 30
    assert "h5" not in ms and "i10_5y" not in ms
    # c/y for dated papers: 40/17, 25/12, 12/9, 3/12, 2/18
    rates = sorted([40 / 17, 25 / 12, 12 / 9, 3 / 12, 2 / 18])
    assert ms["cites_per_paper_year_mean"] == pytest.approx(sum(rates) / 5)
    assert ms["cites_per_paper_year_median"] == pytest.approx(rates[2])
    # OpenAlex window counts: W1 9, W2 5, W3 2, W9 0 -> cites_5y 16, h5 2, i10_5y 0
    assert mo["cites_5y"] == 16 and mo["h5"] == 2 and mo["i10_5y"] == 0
    assert mo["cites_per_year_5y"] == pytest.approx(16 / 5)
    # OpenAlex recent papers (2021..2025): none of W1..W9 (2010, 2015, 2019, 2020)
    assert mo["papers_5y"] == 0 and mo["cites_recent_papers"] == 0 and mo["h_recent"] == 0
    assert mo["n_with_percentile"] == 4 and mo["top10_share"] == pytest.approx(1 / 4)
    # per author indices use known author counts only: Scholar p4 and p5 have none
    assert ms["n_no_authors"] == 2 and ms["author_count_coverage"] == pytest.approx(3 / 5)
    # matched: p1 40/3, p2 25/4, p3 12/2 -> papers_per_author 1/3 + 1/4 + 1/2
    assert ms["papers_per_author"] == pytest.approx(1 / 3 + 1 / 4 + 1 / 2)
    assert ms["cites_per_author"] == pytest.approx(40 / 3 + 25 / 4 + 12 / 2)
    assert ms["min_year"] == 2009


def test_merge_target_without_snapshot_keeps_duplicate(env):
    con, cfg, _ = env
    S(con, cfg, "2026-10-04")
    # p5 is Scholar only; merge the OpenAlex only W9 into it: Scholar folds nothing in (W9 has no
    # Scholar snapshot) and OpenAlex must not lose W9's citation because its target has no OpenAlex row
    p5 = con.execute("SELECT work_id FROM works WHERE gs_id = 'AUTH:p5'").fetchone()[0]
    w9 = con.execute("SELECT work_id FROM works WHERE openalex_id = 'W9'").fetchone()[0]
    con.execute("UPDATE works SET merged_into = ? WHERE work_id = ?", (p5, w9))
    con.commit()
    st = S(con, cfg, "2026-10-11")
    assert st["metrics_openalex"]["n_papers"] == 4 and st["metrics_openalex"]["citations"] == 61


def test_author_filter_drop_is_flagged_not_refetched(env, monkeypatch):
    con, cfg, state = env
    S(con, cfg, "2026-10-04")
    # OpenAlex moves W9 to another author id: the author filter stops returning it
    monkeypatch.setattr(openalex, "fetch_works", lambda *a, **k: [w for w in OA_WORKS if not w["id"].endswith("W9")])
    state["by_id"] = OA_WORKS  # would serve W9 if asked
    st = S(con, cfg, "2026-10-11")
    assert "n_openalex_by_id" not in st  # nothing is title search linked, so nothing is refetched
    assert st["metrics_openalex"]["n_papers"] == 3
    flags = con.execute("SELECT kind, detail FROM flags WHERE run_id = ? AND kind = 'left_record'", (st["run_id"],)).fetchall()
    assert len(flags) == 1 and "W9" in flags[0][1]


def test_standin_duplicate_counts_in_window_and_fwci(env):
    con, cfg, _ = env
    S(con, cfg, "2026-10-04")
    # W3 (2025 count 2, fwci 0.7) merged into Scholar only p5: OpenAlex keeps W3 as the stand in everywhere
    p5 = con.execute("SELECT work_id FROM works WHERE gs_id = 'AUTH:p5'").fetchone()[0]
    w3 = con.execute("SELECT work_id FROM works WHERE openalex_id = 'W3'").fetchone()[0]
    con.execute("UPDATE works SET merged_into = ? WHERE work_id = ?", (p5, w3))
    con.commit()
    mo = S(con, cfg, "2026-10-11")["metrics_openalex"]
    assert mo["cites_5y"] == 16 and mo["fwci_median"] == pytest.approx(0.9)


def test_datasets_skipped_and_versions_collapsed(env, monkeypatch):
    con, cfg, _ = env
    ds = oa_work("W50", "PERSEUS intercomparison data", 2026, 0, "10.5281/z1", 3, 1, None, False, [])
    ds["type"] = "dataset"
    v1 = oa_work("W60", "A preprint and its article", 2024, 3, "10.1/pre", 2, 1, 0.5, False, [(2025, 3)])
    v1["type"] = "preprint"
    v2 = oa_work("W61", "A preprint and its article", 2024, 9, "10.1/art", 2, 1, 1.2, False, [(2025, 9)])
    v2["type"] = "article"
    monkeypatch.setattr(openalex, "fetch_works", lambda *a, **k: OA_WORKS + [ds, v1, v2])
    st = S(con, cfg, "2026-10-04")
    assert con.execute("SELECT COUNT(*) FROM works WHERE openalex_id = 'W50'").fetchone()[0] == 0
    assert st["matching"]["oa_duplicate"] == 1
    t = con.execute("SELECT type, merged_into FROM works WHERE openalex_id = 'W60'").fetchone()
    assert t["type"] == "preprint" and t["merged_into"] == con.execute(
        "SELECT work_id FROM works WHERE openalex_id = 'W61'").fetchone()[0]
    mo = st["metrics_openalex"]
    assert mo["n_papers"] == 5 and mo["papers_5y"] == 1 and mo["cites_recent_papers"] == 12  # 9 + 3 folded


def test_type_survives_csv_round_trip_and_skipped_type_not_flagged(env, monkeypatch, tmp_path):
    con, cfg, _ = env
    S(con, cfg, "2026-10-04")
    store.save(con, tmp_path)
    assert "type" in (tmp_path / "works.csv").read_text().splitlines()[0]
    con2 = store.load(tmp_path)
    assert con2.execute("SELECT type FROM works WHERE openalex_id = 'W1'").fetchone()[0] == "article"
    # OpenAlex reclassifies W9 as a dataset: it stops getting snapshots but is not "left_record"
    w9 = [dict(w) for w in OA_WORKS if w["id"].endswith("W9")][0]
    w9["type"] = "dataset"
    monkeypatch.setattr(openalex, "fetch_works", lambda *a, **k: [w for w in OA_WORKS if not w["id"].endswith("W9")] + [w9])
    st = S(con, cfg, "2026-10-11")
    assert st["metrics_openalex"]["n_papers"] == 3
    assert con.execute("SELECT type FROM works WHERE openalex_id = 'W9'").fetchone()[0] == "dataset"
    assert con.execute("SELECT COUNT(*) FROM flags WHERE run_id = ? AND kind = 'left_record'", (st["run_id"],)).fetchone()[0] == 0


def test_title_search_miss_is_retried_when_young(env):
    con, cfg, state = env
    state["gs"].append({"gs_id": "AUTH:p8", "title": "A brand new paper that OpenAlex has not indexed yet", "year": 2026,
                        "venue": "CJFR", "citations": 1})
    S(con, cfg, "2026-01-10")
    n0 = con.execute("SELECT COUNT(*) FROM title_lookups WHERE gs_id = 'AUTH:p8'").fetchone()[0]
    assert n0 == 1
    # two months later: not retried
    S(con, cfg, "2026-03-10")
    assert con.execute("SELECT searched FROM title_lookups WHERE gs_id = 'AUTH:p8'").fetchone()[0] == "2026-01-10"
    # four months later OpenAlex has it: retried and linked
    state["search"] = [oa_work("W77", "A brand new paper that OpenAlex has not indexed yet", 2026, 2, "10.1/n", 2, 1, None, False, [])]
    S(con, cfg, "2026-05-10")
    assert con.execute("SELECT openalex_id FROM works WHERE gs_id = 'AUTH:p8'").fetchone()[0] == "W77"


def test_fuzzy_match_and_margin(env, monkeypatch):
    con, cfg, state = env
    state["gs"].append({"gs_id": "AUTH:f1", "title": "Modelling crown recession of balsam fir in Maine", "year": 2014,
                        "venue": "FEM", "citations": 8})
    state["gs"].append({"gs_id": "AUTH:f2", "title": "Growth responses of red spruce to thinning", "year": 2016,
                        "venue": "CJFR", "citations": 6})
    extra = [
        # one spelling away: fuzzy, no runner up
        oa_work("W80", "Modeling crown recession of balsam fir in Maine", 2014, 5, "10.1/f1", 2, 1, 1.0, False, []),
        # two near identical candidates for f2 that are not copies of each other: blocked by the margin
        oa_work("W81", "Growth responses of red spruce to thinning in Maine", 2016, 4, "10.1/f2a", 2, 1, 1.0, False, []),
        oa_work("W82", "Growth responses of red spruce to thinning in Quebec", 2016, 4, "10.1/f2b", 2, 1, 1.0, False, []),
    ]
    monkeypatch.setattr(openalex, "fetch_works", lambda *a, **k: OA_WORKS + extra)
    st = S(con, cfg, "2026-10-04")
    assert st["matching"]["title_fuzzy"] == 1
    assert con.execute("SELECT openalex_id, match_method FROM works WHERE gs_id = 'AUTH:f1'").fetchone()[:] == ("W80", "title_fuzzy")
    assert con.execute("SELECT openalex_id FROM works WHERE gs_id = 'AUTH:f2'").fetchone()[0] is None
