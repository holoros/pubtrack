"""Configuration: authors.yml at the repository root lists who is tracked and how.

Secrets never live in the file. They come from the environment (GitHub Actions secrets locally
exported the same way): SERPAPI_KEY, OPENALEX_API_KEY, OPENALEX_MAILTO.
"""

from __future__ import annotations

import os
import re
from dataclasses import dataclass, field
from pathlib import Path

import yaml

ID_RE = re.compile(r"^[a-z0-9][a-z0-9_-]{0,39}$")
CADENCE_DAYS = {"weekly": 6, "monthly": 27, "quarterly": 85, "never": None}


def repo_root() -> Path:
    env = os.environ.get("PUBTRACK_HOME")
    if env:
        return Path(env).expanduser().resolve()
    here = Path.cwd().resolve()
    for p in (here, *here.parents):
        if (p / "authors.yml").exists():
            return p
    return here


@dataclass
class Settings:
    scholar_via: str = "serpapi"            # serpapi | scholarly | none
    serpapi_monthly_budget: int = 240        # free tier is 250; keep a margin
    openalex_cadence: str = "weekly"
    min_delay: float = 10                    # scholarly route only
    max_delay: float = 25
    store: str = "data"
    serpapi_key: str = ""
    openalex_key: str = ""
    mailto: str = ""


@dataclass
class Author:
    id: str
    name: str
    surname: str = ""
    scholar_id: str = ""
    orcid: str = ""
    openalex_id: str = ""                    # comma separated if OpenAlex split the record
    first_year: int = 0
    scholar_cadence: str = "weekly"          # weekly | monthly | quarterly | never
    group: str = ""
    note: str = ""

    @property
    def openalex_ids(self) -> list[str]:
        return [x.strip() for x in str(self.openalex_id or "").split(",") if x.strip()]


@dataclass
class Config:
    settings: Settings
    authors: list[Author] = field(default_factory=list)
    path: Path | None = None

    def author(self, author_id: str) -> Author:
        for a in self.authors:
            if a.id == author_id:
                return a
        raise KeyError(f"author {author_id!r} is not in {self.path}")

    @property
    def store_dir(self) -> Path:
        base = self.path.parent if self.path else repo_root()
        return (base / self.settings.store).resolve()


def _clean_orcid(x: str) -> str:
    return str(x or "").strip().replace("https://orcid.org/", "")


def load(path: Path | None = None) -> Config:
    path = Path(path) if path else repo_root() / "authors.yml"
    if not path.exists():
        raise FileNotFoundError(f"No authors.yml at {path}")
    raw = yaml.safe_load(path.read_text()) or {}
    s = raw.get("settings") or {}
    settings = Settings(
        scholar_via=str(s.get("scholar_via", "serpapi")),
        serpapi_monthly_budget=int(s.get("serpapi_monthly_budget", 240)),
        openalex_cadence=str(s.get("openalex_cadence", "weekly")),
        min_delay=float(s.get("min_delay", 10)),
        max_delay=float(s.get("max_delay", 25)),
        store=str(s.get("store", "data")),
        serpapi_key=os.environ.get("SERPAPI_KEY", "").strip(),
        openalex_key=os.environ.get("OPENALEX_API_KEY", "").strip(),
        mailto=os.environ.get("OPENALEX_MAILTO", str(s.get("openalex_mailto", "") or "")).strip(),
    )
    if settings.scholar_via not in ("serpapi", "scholarly", "none"):
        raise ValueError("settings.scholar_via must be serpapi, scholarly or none")
    authors, seen = [], set()
    for a in raw.get("authors") or []:
        aid = str(a.get("id", "")).strip()
        if not ID_RE.match(aid):
            raise ValueError(f"author id {aid!r}: use lowercase letters, digits, - or _")
        if aid in seen:
            raise ValueError(f"author id {aid!r} appears twice")
        seen.add(aid)
        cad = str(a.get("scholar_cadence", "weekly"))
        if cad not in CADENCE_DAYS:
            raise ValueError(f"{aid}: scholar_cadence must be one of {list(CADENCE_DAYS)}")
        name = str(a.get("name", aid))
        authors.append(Author(
            id=aid, name=name, surname=str(a.get("surname") or name.split()[-1]),
            scholar_id=str(a.get("scholar_id") or "").strip(), orcid=_clean_orcid(a.get("orcid")),
            openalex_id=str(a.get("openalex_id") or "").strip(), first_year=int(a.get("first_year") or 0),
            scholar_cadence=cad, group=str(a.get("group") or ""), note=str(a.get("note") or ""),
        ))
    return Config(settings=settings, authors=authors, path=path)


def set_author_field(path: Path, author_id: str, key: str, value: str) -> None:
    """Edit one scalar under one author in place, keeping comments and layout."""
    lines = path.read_text().splitlines()
    in_author, indent = False, None
    for i, line in enumerate(lines):
        m = re.match(r"^(\s*)-\s+id:\s*['\"]?([^'\"\s#]+)", line)
        if m:
            in_author = m.group(2) == author_id
            indent = len(m.group(1)) + 2
            continue
        if in_author:
            m2 = re.match(rf"^\s{{{indent}}}{re.escape(key)}:", line)
            if m2:
                lines[i] = f"{' ' * indent}{key}: \"{value}\""
                path.write_text("\n".join(lines) + "\n")
                return
            if re.match(r"^\S", line):
                break
    raise KeyError(f"{author_id}.{key} not found in {path}")
