"""fetch_works pages by number without an API key and by cursor with one (no network)."""

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
