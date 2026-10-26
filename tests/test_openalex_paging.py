"""fetch_works pages by number without an API key and by cursor with one (no network)."""

import pytest

from pubtrack import openalex


class R:
    def __init__(self, js, status=200):
        self.status_code, self._js, self.text = status, js, str(js)

    def json(self):
        return self._js


def _works(n, start):
    return [{"id": f"https://openalex.org/W{start + i}"} for i in range(n)]


def test_page_paging_without_key(monkeypatch):
    seen = []

    def get(url, params=None, timeout=None):
        seen.append(dict(params))
        page = params["page"]
        assert params["sort"] == "publication_date:desc"
        res = _works(200, 0) if page == 1 else _works(63, 200) if page == 2 else []
        return R({"results": res, "meta": {"count": 263}})

    monkeypatch.setattr(openalex.requests, "get", get)
    out = openalex.fetch_works(["A1"], mailto="x@y.z", api_key="")
    assert len(out) == 263
    assert [p["page"] for p in seen] == [1, 2]
    assert all("cursor" not in p and "api_key" not in p for p in seen)


def test_cursor_paging_with_key(monkeypatch):
    seen = []

    def get(url, params=None, timeout=None):
        seen.append(dict(params))
        cur = params["cursor"]
        if cur == "*":
            return R({"results": _works(200, 0), "meta": {"count": 263, "next_cursor": "c2"}})
        return R({"results": _works(63, 200), "meta": {"count": 263, "next_cursor": None}})

    monkeypatch.setattr(openalex.requests, "get", get)
    out = openalex.fetch_works(["A1"], api_key="k")
    assert len(out) == 263
    assert [p["cursor"] for p in seen] == ["*", "c2"]
    assert all(p["api_key"] == "k" for p in seen)


def test_unkeyed_401_surfaces_as_error(monkeypatch):
    monkeypatch.setattr(openalex.requests, "get",
                        lambda url, params=None, timeout=None: R({"error": "API key required"}, 401))
    try:
        openalex.fetch_works(["A1"])
    except openalex.OpenAlexError as e:
        assert "401" in str(e)
    else:
        raise AssertionError("expected OpenAlexError")


def test_missing_meta_count_is_an_error(monkeypatch):
    monkeypatch.setattr(openalex.requests, "get",
                        lambda url, params=None, timeout=None: R({"results": _works(200, 0)}))
    with pytest.raises(openalex.OpenAlexError, match="meta.count"):
        openalex.fetch_works(["A1"])


def test_short_read_is_an_error(monkeypatch):
    def get(url, params=None, timeout=None):
        return R({"results": _works(10, 0) if params["page"] == 1 else [], "meta": {"count": 263}})

    monkeypatch.setattr(openalex.requests, "get", get)
    with pytest.raises(openalex.OpenAlexError, match="10 distinct works of 263"):
        openalex.fetch_works(["A1"])


def test_over_limit_refused_before_paging(monkeypatch):
    calls = []

    def get(url, params=None, timeout=None):
        calls.append(1)
        return R({"results": _works(200, 0), "meta": {"count": 10_001}})

    monkeypatch.setattr(openalex.requests, "get", get)
    with pytest.raises(openalex.OpenAlexError, match="OPENALEX_API_KEY"):
        openalex.fetch_works(["A1"])
    assert len(calls) == 1


def test_fetch_by_ids_chunks_of_fifty(monkeypatch):
    seen = []

    def get(url, params=None, timeout=None):
        ids = params["filter"].split(":", 1)[1].split("|")
        seen.append(len(ids))
        return R({"results": [{"id": f"https://openalex.org/{i}"} for i in ids], "meta": {"count": len(ids)}})

    monkeypatch.setattr(openalex.requests, "get", get)
    out = openalex.fetch_by_ids([f"W{i}" for i in range(120)] + ["W1"])
    assert seen == [50, 50, 20] and len(out) == 120


def test_truncated_authorships_leave_count_unknown():
    w = {"authorships": [{"author": {"id": "https://openalex.org/A1", "display_name": "A"}}] * 100,
         "is_authors_truncated": True}
    names, n, pos, last = openalex._author_fields(w, {"A1"}, "")
    assert n is None and last is None and pos == 1 and names.endswith("; ...")


def test_cursor_branch_requires_meta_count(monkeypatch):
    monkeypatch.setattr(openalex.requests, "get",
                        lambda url, params=None, timeout=None: R({"results": _works(10, 0), "meta": {"next_cursor": None}}))
    with pytest.raises(openalex.OpenAlexError, match="meta.count"):
        openalex.fetch_works(["A1"], api_key="k")
