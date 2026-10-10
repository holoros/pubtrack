"""SerpAPI parsing and call accounting against canned responses (no network)."""

import pytest

from pubtrack import scholar


class R:
    def __init__(self, status, js):
        self.status_code, self._js, self.text = status, js, str(js)

    def json(self):
        return self._js


def page(n, start, with_profile=False):
    js = {"articles": [{"citation_id": f"X:{start + i}", "title": f"Paper {start + i}", "year": "2010",
                        "publication": "Forest Science 56", "cited_by": {"value": 100 - (start + i) % 100}}
                       for i in range(n)]}
    if with_profile:
        js["cited_by"] = {"table": [{"citations": {"all": 9677, "since_2021": 5698}},
                                    {"h_index": {"all": 50, "since_2021": 37}},
                                    {"i10_index": {"all": 187, "since_2021": 160}}],
                          "graph": [{"year": 2024, "citations": 900}, {"year": 2025, "citations": 950}]}
    return js


def test_pages_profile_and_calls(monkeypatch):
    pages = [R(200, page(100, 0, True)), R(200, page(100, 100)), R(200, page(37, 200))]
    monkeypatch.setattr(scholar.requests, "get", lambda *a, **k: pages.pop(0))
    d = scholar.via_serpapi("X", "key")
    assert d["serpapi_calls"] == 3 and len(d["works"]) == 237
    p = d["profile"]
    assert (p["total"], p["c5y"], p["h"], p["h5"], p["i10"], p["i10_5y"]) == (9677, 5698, 50, 37, 187, 160)
    assert p["by_year"] == {2024: 900, 2025: 950}
    assert d["works"][0] == {"gs_id": "X:0", "title": "Paper 0", "year": 2010, "venue": "Forest Science 56", "citations": 100}


def test_failure_reports_charged_calls(monkeypatch):
    pages = [R(200, page(100, 0, True)), R(200, {"error": "Your account has run out of searches."})]
    monkeypatch.setattr(scholar.requests, "get", lambda *a, **k: pages.pop(0))
    with pytest.raises(scholar.ScholarBlocked) as e:
        scholar.via_serpapi("X", "key")
    assert e.value.serpapi_calls == 1 and "run out" in str(e.value)


def test_missing_key():
    with pytest.raises(scholar.ScholarBlocked):
        scholar.via_serpapi("X", "")
