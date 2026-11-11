"""Academic lineage: advisors and students from lineage.yml, resolved to OpenAlex with citation metrics.

Two committed tables under data/:
  lineage_people.csv   one row per person in the tree: how they were matched to OpenAlex and when
  lineage_metrics.csv  dated OpenAlex author snapshots (works, citations, h, i10, 2 year mean citedness)

Matching order for each person: a pinned openalex_id, then ORCID, then a name search accepted only
when the candidate shares a work with the linked advisor or student (the tree is walked outward from
the tracked author, so that neighbour is already resolved). Candidates with the same name that each
share work with the neighbour are taken as one person whose OpenAlex record is split. A unique exact
name match without a shared work is kept but marked "name" (unverified). Nothing else is guessed.
"""

from __future__ import annotations

import csv
import hashlib
import json
import logging
import re
import unicodedata
from dataclasses import dataclass, field
from datetime import date, timedelta
from pathlib import Path

import yaml

from . import openalex

log = logging.getLogger("pubtrack.lineage")

PEOPLE_COLS = ["author_id", "node_key", "name", "relation", "parent_key", "openalex_ids", "method",
               "shared_works", "checked_date", "spec_hash"]
METRIC_COLS = ["snapshot_date", "openalex_id", "display_name", "works_count", "cited_by_count", "h_index",
               "i10_index", "two_yr_mean_citedness", "institution", "orcid", "fields"]
# A matched record is flagged as possibly merging two people when under half of its topic counts fall in
# the tracked author's fields AND at least MIXED_MIN_OUT counts fall outside them (small records are spared).
MIXED_SHARE = 0.5
MIXED_MIN_OUT = 20
RECHECK_UNRESOLVED_DAYS = 90
MAX_CANDIDATES = 6


# ---------------------------------------------------------------- tree

@dataclass
class Person:
    name: str
    relation: str                      # self | advisor | student
    key: str
    parent_key: str = ""
    openalex_id: str = ""
    orcid: str = ""
    degree: str = ""
    year: str = ""
    institution: str = ""
    more: bool = False
    note: str = ""
    children: list = field(default_factory=list)   # further advisors (advisor side) or students

    @property
    def pinned_ids(self) -> list[str]:
        return [x.strip() for x in self.openalex_id.split(",") if x.strip()]

    def spec_hash(self) -> str:
        s = "|".join([self.name, self.openalex_id, self.orcid, self.parent_key])
        return hashlib.sha1(s.encode()).hexdigest()[:10]


def slug(name: str) -> str:
    s = unicodedata.normalize("NFKD", name).encode("ascii", "ignore").decode().lower()
    return re.sub(r"[^a-z0-9]+", "-", s).strip("-")


def _person(raw, relation: str, parent_key: str, seen: set) -> Person:
    if isinstance(raw, str):
        raw = {"name": raw}
    if not isinstance(raw, dict) or not str(raw.get("name", "")).strip():
        raise ValueError(f"lineage entry under {parent_key} needs a name: {raw!r}")
    name = str(raw["name"]).strip()
    key = f"{parent_key}/{relation[0]}:{slug(name)}"
    if key in seen:
        raise ValueError(f"{name} appears twice under the same person ({parent_key})")
    seen.add(key)
    p = Person(name=name, relation=relation, key=key, parent_key=parent_key,
               openalex_id=str(raw.get("openalex_id") or "").strip(),
               orcid=str(raw.get("orcid") or "").strip().replace("https://orcid.org/", ""),
               degree=str(raw.get("degree") or ""), year=str(raw.get("year") or ""),
               institution=str(raw.get("institution") or ""), more=bool(raw.get("more", False)),
               note=str(raw.get("note") or ""))
    sub = "advisors" if relation == "advisor" else "students"
    other = "students" if relation == "advisor" else "advisors"
    if raw.get(other):
        raise ValueError(f"{name}: an {relation} lists {other}; only {sub} continue this side of the tree")
    p.children = [_person(c, relation, key, seen) for c in (raw.get(sub) or [])]
    return p


def load_tree(path: Path, author_id: str, name: str, openalex_ids: list[str], orcid: str = "") -> Person | None:
    """Root Person for one tracked author, or None when lineage.yml has no entry for them."""
    if not path.exists():
        return None
    raw = (yaml.safe_load(path.read_text()) or {}).get(author_id)
    if not raw:
        return None
    root = Person(name=name, relation="self", key=author_id, openalex_id=",".join(openalex_ids), orcid=orcid)
    seen = {author_id}
    root.advisors = [_person(r, "advisor", author_id, seen) for r in (raw.get("advisors") or [])]  # type: ignore[attr-defined]
    root.students = [_person(r, "student", author_id, seen) for r in (raw.get("students") or [])]  # type: ignore[attr-defined]
    return root


def walk(root: Person):
    """Breadth first from the tracked author, so every person's neighbour is resolved before them."""
    queue = list(getattr(root, "advisors", [])) + list(getattr(root, "students", []))
    while queue:
        p = queue.pop(0)
        yield p
        queue.extend(p.children)


# ---------------------------------------------------------------- names

def _tokens(s: str) -> list[str]:
    s = unicodedata.normalize("NFKD", s).encode("ascii", "ignore").decode().lower()
    return [t for t in re.split(r"[^a-z]+", s) if t]


def name_compatible(target: str, cand: str) -> bool:
    """Same surname and a first name that agrees (full or initial). Hyphenated surnames match either part set."""
    t, c = _tokens(target), _tokens(cand)
    if not t or not c:
        return False
    if t[-1] != c[-1]:
        return False
    a, b = t[0], c[0]
    if len(a) == 1 or len(b) == 1:
        return a[0] == b[0]
    return a == b


def name_exact(target: str, cand: str) -> bool:
    """First and last name tokens identical (middle initials may differ or be missing)."""
    t, c = _tokens(target), _tokens(cand)
    return bool(t and c) and t[0] == c[0] and t[-1] == c[-1] and len(t[0]) > 1


def _all_names(c: dict) -> list[str]:
    return [c.get("display_name") or ""] + list(c.get("display_name_alternatives") or [])


# ---------------------------------------------------------------- OpenAlex

def shared_works(a_ids: list[str], b_ids: list[str], mailto: str, key: str) -> int:
    flt = f"authorships.author.id:{'|'.join(a_ids)},authorships.author.id:{'|'.join(b_ids)}"
    js = openalex._get("/works", openalex._params(mailto, key, filter=flt, per_page=1, select="id"))
    return int((js.get("meta") or {}).get("count") or 0)


def resolve_person(p: Person, neighbour_ids: list[str], mailto: str, key: str) -> tuple[list[str], str, int]:
    """(openalex ids, method, shared works with the neighbour)."""
    if p.pinned_ids:
        return p.pinned_ids, "pinned", -1
    if p.orcid:
        try:
            js = openalex._get(f"/authors/orcid:{p.orcid}", openalex._params(mailto, key))
            return [openalex.short_id(js["id"])], "orcid", -1
        except openalex.OpenAlexError as e:
            log.warning("%s: ORCID lookup failed (%s); trying the name", p.name, e)
    js = openalex._get("/authors", openalex._params(mailto, key, search=p.name, per_page=25))
    cands = [c for c in js.get("results", []) if any(name_compatible(p.name, n) for n in _all_names(c))]
    cands.sort(key=lambda c: -(c.get("works_count") or 0))
    cands = cands[:MAX_CANDIDATES]
    if neighbour_ids:
        hits = []
        for c in cands:
            n = shared_works([openalex.short_id(c["id"])], neighbour_ids, mailto, key)
            if n > 0:
                hits.append((c, n))
        if hits:
            hits.sort(key=lambda x: -x[1])
            return [openalex.short_id(c["id"]) for c, _ in hits], "coauthor", sum(n for _, n in hits)
    exact = [c for c in cands if any(name_exact(p.name, n) for n in _all_names(c))]
    if len(exact) == 1:
        return [openalex.short_id(exact[0]["id"])], "name", 0
    return [], "unresolved", 0


def fetch_metrics(ids: list[str], mailto: str, key: str, today: str) -> list[dict]:
    rows = []
    sel = "id,display_name,works_count,cited_by_count,summary_stats,last_known_institutions,orcid,topics"
    for i in range(0, len(ids), 50):
        chunk = ids[i:i + 50]
        js = openalex._get("/authors", openalex._params(mailto, key, filter="ids.openalex:" + "|".join(chunk),
                                                          per_page=50, select=sel))
        for a in js.get("results", []):
            ss = a.get("summary_stats") or {}
            inst = (a.get("last_known_institutions") or [{}])
            rows.append({
                "snapshot_date": today, "openalex_id": openalex.short_id(a["id"]),
                "display_name": a.get("display_name") or "", "works_count": a.get("works_count"),
                "cited_by_count": a.get("cited_by_count"), "h_index": ss.get("h_index"),
                "i10_index": ss.get("i10_index"),
                "two_yr_mean_citedness": round(float(ss.get("2yr_mean_citedness") or 0), 3),
                "institution": (inst[0] or {}).get("display_name", "") if inst else "",
                "orcid": (a.get("orcid") or "").replace("https://orcid.org/", ""),
                "fields": _fields(a.get("topics") or []),
            })
    return rows


def _fields(topics: list[dict]) -> str:
    """Topic counts summed by OpenAlex field, largest first: "Environmental Science:140;Medicine:12"."""
    acc: dict[str, int] = {}
    for t in topics:
        f = ((t.get("field") or {}).get("display_name") or "").replace(";", ",").replace(":", " ")
        if f:
            acc[f] = acc.get(f, 0) + int(t.get("count") or 0)
    return ";".join(f"{k}:{v}" for k, v in sorted(acc.items(), key=lambda kv: -kv[1]))


def parse_fields(s: str) -> dict[str, int]:
    out = {}
    for part in (s or "").split(";"):
        if ":" in part:
            k, v = part.rsplit(":", 1)
            out[k] = int(v or 0)
    return out


def core_fields(fields: dict[str, int], min_share: float = 0.02) -> set[str]:
    """Fields holding at least `min_share` of an author's topic counts."""
    tot = sum(fields.values())
    return {k for k, v in fields.items() if tot and v / tot >= min_share}


def out_of_field(fields: dict[str, int], core: set[str]) -> int:
    return sum(v for k, v in fields.items() if k not in core)


def field_share(fields: dict[str, int], core: set[str]) -> float | None:
    tot = sum(fields.values())
    return None if not tot or not core else sum(v for k, v in fields.items() if k in core) / tot


# ---------------------------------------------------------------- store

def _read(path: Path) -> list[dict]:
    if not path.exists():
        return []
    with path.open(newline="", encoding="utf-8") as f:
        return list(csv.DictReader(f))


def _write(path: Path, rows: list[dict], cols: list[str], sort_keys: list[str]) -> None:
    rows = sorted(rows, key=lambda r: tuple(str(r.get(k, "")) for k in sort_keys))
    with path.open("w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=cols, extrasaction="ignore", lineterminator="\n")
        w.writeheader()
        for r in rows:
            w.writerow({c: ("" if r.get(c) is None else r.get(c)) for c in cols})


def sync(cfg, lineage_path: Path, store_dir: Path, force: bool = False, today: date | None = None) -> dict:
    """Resolve new or changed people, then snapshot metrics for everyone resolved. Returns counts."""
    today = today or date.today()
    ppath, mpath = store_dir / "lineage_people.csv", store_dir / "lineage_metrics.csv"
    old = {(r["author_id"], r["node_key"]): r for r in _read(ppath)}
    people, root_ids, out = [], set(), {"resolved": 0, "rechecked": 0, "unresolved": [], "name_only": [], "authors": 0}
    mailto, key = cfg.settings.mailto, cfg.settings.openalex_key
    for au in cfg.authors:
        root = load_tree(lineage_path, au.id, au.name, au.openalex_ids, au.orcid)
        if root is None:
            continue
        out["authors"] += 1
        root_ids.update(au.openalex_ids)
        ids_by_key = {root.key: au.openalex_ids}
        for p in walk(root):
            prev = old.get((au.id, p.key))
            stale = (prev is None or prev["spec_hash"] != p.spec_hash() or force or
                     (prev["method"] == "unresolved" and
                      date.fromisoformat(prev["checked_date"]) < today - timedelta(days=RECHECK_UNRESOLVED_DAYS)))
            if stale:
                ids, method, n = resolve_person(p, ids_by_key.get(p.parent_key, []), mailto, key)
                row = {"author_id": au.id, "node_key": p.key, "name": p.name, "relation": p.relation,
                       "parent_key": p.parent_key, "openalex_ids": ",".join(ids), "method": method,
                       "shared_works": n, "checked_date": today.isoformat(), "spec_hash": p.spec_hash()}
                out["rechecked"] += 1
            else:
                row = prev
            ids_by_key[p.key] = [x for x in row["openalex_ids"].split(",") if x]
            if row["method"] == "unresolved":
                out["unresolved"].append(f"{au.id}: {p.name}")
            elif row["method"] == "name":
                out["name_only"].append(f"{au.id}: {p.name} ({row['openalex_ids']})")
            out["resolved"] += row["method"] != "unresolved"
            people.append(row)
    _write(ppath, people, PEOPLE_COLS, ["author_id", "node_key"])
    want = sorted({i for r in people for i in r["openalex_ids"].split(",") if i} | root_ids)
    mrows = [r for r in _read(mpath) if r["snapshot_date"] != today.isoformat()]
    if want:
        mrows += fetch_metrics(want, mailto, key, today.isoformat())
    _write(mpath, mrows, METRIC_COLS, ["snapshot_date", "openalex_id"])
    out["metric_rows"] = len(want)
    return out


# ---------------------------------------------------------------- page data

def _latest_metrics(store_dir: Path) -> dict[str, dict]:
    latest = {}
    for r in _read(store_dir / "lineage_metrics.csv"):
        if r["openalex_id"] not in latest or r["snapshot_date"] >= latest[r["openalex_id"]]["snapshot_date"]:
            latest[r["openalex_id"]] = r
    return latest


def _num(x):
    try:
        f = float(x)
        return int(f) if f.is_integer() else f
    except (TypeError, ValueError):
        return None


def _combine(ids: list[str], latest: dict) -> dict | None:
    """One person's metrics. A split record sums works and citations; h and i10 take the largest part
    (a lower bound on the true value) and the record says so."""
    parts = [latest[i] for i in ids if i in latest]
    if not parts:
        return None
    m = {"works": sum(_num(p["works_count"]) or 0 for p in parts),
         "citations": sum(_num(p["cited_by_count"]) or 0 for p in parts),
         "h": max(_num(p["h_index"]) or 0 for p in parts),
         "i10": max(_num(p["i10_index"]) or 0 for p in parts),
         "mean2yr": _num(parts[0]["two_yr_mean_citedness"]) if len(parts) == 1 else None,
         "institution": parts[0]["institution"], "as_of": max(p["snapshot_date"] for p in parts),
         "split": len(parts) > 1, "_fields": {}}
    for p in parts:
        for k, v in parse_fields(p.get("fields", "")).items():
            m["_fields"][k] = m["_fields"].get(k, 0) + v
    return m


def page_data(cfg, lineage_path: Path, store_dir: Path) -> dict:
    people = {(r["author_id"], r["node_key"]): r for r in _read(store_dir / "lineage_people.csv")}
    latest = _latest_metrics(store_dir)

    def node(p: Person, aid: str) -> dict:
        if p.relation == "self":
            ids, method = [i for i in p.openalex_id.split(",") if i], "tracked"
        else:
            r = people.get((aid, p.key))
            ids = [i for i in (r["openalex_ids"] if r else "").split(",") if i]
            method = r["method"] if r else "pending"
        d = {"name": p.name, "key": p.key, "relation": p.relation, "degree": p.degree, "year": p.year,
             "institution": p.institution, "more": p.more, "note": p.note, "openalex_ids": ids,
             "method": method, "metrics": _combine(ids, latest)}
        d["children"] = [node(c, aid) for c in p.children]
        return d

    def flag_mixed(d: dict, core: set[str]) -> None:
        m = d.get("metrics")
        if m is not None:
            f = m.pop("_fields", {})
            share = field_share(f, core)
            m["field_share"] = None if share is None else round(share, 2)
            m["mixed"] = (d["relation"] != "self" and d["method"] in ("coauthor", "name") and share is not None
                          and share < MIXED_SHARE and out_of_field(f, core) >= MIXED_MIN_OUT)
        for c in d["children"] + d.get("advisors", []) + d.get("students", []):
            flag_mixed(c, core)

    out = {"built": date.today().isoformat(), "authors": []}
    for au in cfg.authors:
        root = load_tree(lineage_path, au.id, au.name, au.openalex_ids, au.orcid)
        if root is None:
            continue
        r = node(root, au.id)
        r["advisors"] = [node(c, au.id) for c in root.advisors]   # type: ignore[attr-defined]
        r["students"] = [node(c, au.id) for c in root.students]   # type: ignore[attr-defined]
        flag_mixed(r, core_fields((r["metrics"] or {}).get("_fields", {})))
        out["authors"].append({"id": au.id, "name": au.name, "root": r})
    dates = [m["snapshot_date"] for m in latest.values()]
    out["metrics_as_of"] = max(dates) if dates else ""
    return out


def mixed_records(data: dict) -> list[str]:
    """Matched people whose OpenAlex record sits mostly outside the tracked author's fields."""
    out = []

    def rec(d, aid):
        if (d.get("metrics") or {}).get("mixed"):
            out.append(f"{aid}: {d['name']} ({','.join(d['openalex_ids'])}, {d['metrics']['field_share']:.0%} in field)")
        for c in d["children"] + d.get("advisors", []) + d.get("students", []):
            rec(c, aid)
    for a in data["authors"]:
        rec(a["root"], a["id"])
    return out


def build_page(cfg, lineage_path: Path, store_dir: Path, out_dir: Path) -> Path:
    data = page_data(cfg, lineage_path, store_dir)
    tpl = (Path(__file__).parent / "lineage_template.html").read_text(encoding="utf-8")
    js = json.dumps(data, ensure_ascii=False).replace("</", "<\\/")
    out_dir.mkdir(parents=True, exist_ok=True)
    f = out_dir / "index.html"
    f.write_text(tpl.replace("/*__LINEAGE_DATA__*/null", js), encoding="utf-8")
    return f
