"""lineage.yml parsing, OpenAlex matching rules and the page build (no network)."""

from datetime import date
from pathlib import Path

import pytest

from pubtrack import config as cfgmod
from pubtrack import lineage, openalex

AUTHORS = """
authors:
  - id: focal
    name: Ann B. Focal
    openalex_id: A1
"""
TREE = """
focal:
  advisors:
    - name: Carl D. Mentor
      advisors: [Old E. Root]
  students:
    - Dana Pupil
    - name: Eve F. Twin
      students: [Gus Grand]
    - name: Pinned Person
      openalex_id: A90, A91
    - Nobody Known
"""

# Fake OpenAlex: candidates by name and which ids share works.
CANDS = {
    "Carl D. Mentor": [{"id": "A10", "display_name": "Carl D. Mentor", "works_count": 50},
                       {"id": "A11", "display_name": "Carl Mentor", "works_count": 80}],
    "Old E. Root": [{"id": "A20", "display_name": "Old Root", "works_count": 9}],
    "Dana Pupil": [{"id": "A30", "display_name": "Dana Pupil", "works_count": 5},
                   {"id": "A31", "display_name": "D. Pupil", "works_count": 3},
                   {"id": "A32", "display_name": "Diane Pupil", "works_count": 30}],
    "Eve F. Twin": [{"id": "A40", "display_name": "Eve Twin", "works_count": 7},
                    {"id": "A41", "display_name": "Eve Twin", "works_count": 7}],
    "Gus Grand": [{"id": "A50", "display_name": "Gus Grand", "works_count": 2}],
    "Nobody Known": [{"id": "A60", "display_name": "Nobody Known", "works_count": 1},
                     {"id": "A61", "display_name": "Nobody Known", "works_count": 2}],
}
SHARED = {frozenset({"A10", "A1"}): 4, frozenset({"A30", "A1"}): 2, frozenset({"A31", "A1"}): 1,
          frozenset({"A40", "A1"}): 3, frozenset({"A41", "A1"}): 1, frozenset({"A50", "A40|A41"}): 2}


def fake_get(path, params):
    if path == "/authors" and "search" in params:
        return {"results": [dict(c, id=f"https://openalex.org/{c['id']}") for c in CANDS.get(params["search"], [])]}
    if path == "/works":
        a, b = (x.split(":", 1)[1] for x in params["filter"].split(","))
        return {"meta": {"count": SHARED.get(frozenset({a, b}), 0)}}
    if path == "/authors":
        ids = params["filter"].split(":", 1)[1].split("|")
        return {"results": [{"id": f"https://openalex.org/{i}", "display_name": i, "works_count": 10,
                             "cited_by_count": 100 * int(i[1:]), "summary_stats": {"h_index": int(i[1:]) % 37,
                             "i10_index": 3, "2yr_mean_citedness": 1.5}, "last_known_institutions": [{"display_name": "U"}]}
                            for i in ids]}
    raise AssertionError(path)


@pytest.fixture
def repo(tmp_path, monkeypatch):
    (tmp_path / "authors.yml").write_text(AUTHORS)
    (tmp_path / "lineage.yml").write_text(TREE)
    (tmp_path / "data").mkdir()
    calls = []
    monkeypatch.setattr(openalex, "_get", lambda p, q: (calls.append((p, dict(q))), fake_get(p, q))[1])
    return tmp_path, calls


def test_names():
    assert lineage.name_compatible("Dana Pupil", "D. Pupil")
    assert not lineage.name_compatible("Dana Pupil", "Diane Pupil")
    assert lineage.name_compatible("Marcella A. Windmuller-Campione", "Marcella Windmuller-Campione")
    assert lineage.name_exact("Carl D. Mentor", "Carl Mentor") and not lineage.name_exact("Dana Pupil", "D. Pupil")


def test_tree_rules(tmp_path):
    f = tmp_path / "l.yml"
    f.write_text("x:\n  advisors:\n    - name: A\n      students: [B]\n")
    with pytest.raises(ValueError, match="only advisors"):
        lineage.load_tree(f, "x", "X", ["A1"])
    f.write_text("x:\n  students: [B, B]\n")
    with pytest.raises(ValueError, match="twice"):
        lineage.load_tree(f, "x", "X", ["A1"])
    assert lineage.load_tree(f, "other", "O", []) is None


def test_sync_matching_and_rerun(repo):
    root, calls = repo
    cfg = cfgmod.load(root / "authors.yml")
    r = lineage.sync(cfg, root / "lineage.yml", root / "data", today=date(2026, 11, 11))
    rows = {x["name"]: x for x in lineage._read(root / "data" / "lineage_people.csv")}
    assert rows["Carl D. Mentor"]["openalex_ids"] == "A10" and rows["Carl D. Mentor"]["method"] == "coauthor"
    # Old Root never published with Mentor: unique exact name match, kept but flagged
    assert rows["Old E. Root"]["method"] == "name" and rows["Old E. Root"]["openalex_ids"] == "A20"
    # both compatible Pupil records share work with the focal author: one person, split record
    assert rows["Dana Pupil"]["openalex_ids"] == "A30,A31" and rows["Dana Pupil"]["shared_works"] == "3"
    assert rows["Eve F. Twin"]["openalex_ids"] == "A40,A41"
    assert rows["Gus Grand"]["method"] == "coauthor"          # checked against both of Eve's records
    assert rows["Pinned Person"]["openalex_ids"] == "A90,A91" and rows["Pinned Person"]["method"] == "pinned"
    assert rows["Nobody Known"]["method"] == "unresolved"     # two exact candidates, no shared work
    assert r["unresolved"] == ["focal: Nobody Known"] and len(r["name_only"]) == 1
    m = lineage._read(root / "data" / "lineage_metrics.csv")
    assert {x["openalex_id"] for x in m} >= {"A1", "A10", "A30", "A31", "A90", "A91"}

    # A second run the same day looks nobody up again and replaces that day's metrics.
    calls.clear()
    lineage.sync(cfg, root / "lineage.yml", root / "data", today=date(2026, 11, 11))
    assert not [c for c in calls if "search" in c[1]]
    assert len(lineage._read(root / "data" / "lineage_metrics.csv")) == len(m)
    # Unresolved people are retried after 90 days; matched ones are not.
    calls.clear()
    lineage.sync(cfg, root / "lineage.yml", root / "data", today=date(2027, 3, 1))
    assert [c[1]["search"] for c in calls if "search" in c[1]] == ["Nobody Known"]


def test_page(repo):
    root, _ = repo
    cfg = cfgmod.load(root / "authors.yml")
    lineage.sync(cfg, root / "lineage.yml", root / "data", today=date(2026, 11, 11))
    d = lineage.page_data(cfg, root / "lineage.yml", root / "data")
    a = d["authors"][0]["root"]
    assert a["metrics"]["citations"] == 100
    pupil = next(s for s in a["students"] if s["name"] == "Dana Pupil")["metrics"]
    assert pupil["citations"] == 3000 + 3100 and pupil["split"] and pupil["h"] == max(30 % 37, 31 % 37)
    assert next(s for s in a["students"] if s["name"] == "Nobody Known")["metrics"] is None
    out = lineage.build_page(cfg, root / "lineage.yml", root / "data", root / "site")
    html = out.read_text()
    assert "/*__LINEAGE_DATA__*/null" not in html and "Carl D. Mentor" in html
