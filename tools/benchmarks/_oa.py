"""Shared OpenAlex helper: API key from OPENALEX_API_KEY, retries, h index."""
from __future__ import annotations
import os, time, requests

BASE = "https://api.openalex.org"


def get(path: str, **params) -> dict:
    key = os.environ.get("OPENALEX_API_KEY")
    if key:
        params["api_key"] = key
    if os.environ.get("OPENALEX_MAILTO"):
        params["mailto"] = os.environ["OPENALEX_MAILTO"]
    last = None
    for attempt in range(5):
        r = requests.get(BASE + path, params=params, timeout=120)
        if r.status_code == 200:
            return r.json()
        last = r
        time.sleep(2 + 2 * attempt)
    raise SystemExit(f"OpenAlex {path} failed: {last.status_code} {last.text[:200]}")


def h_of(scores) -> int:
    s = sorted((float(x) for x in scores), reverse=True)
    h = 0
    for i, v in enumerate(s, start=1):
        if v >= i:
            h = i
        else:
            break
    return h


SKIP_TYPES = {"dataset", "software", "paratext", "erratum", "peer-review", "retraction", "grant",
              "supplementary-materials", "libguides", "standard"}
