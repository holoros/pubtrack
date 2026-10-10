"""Configuration: ~/.config/pubtrack/config.toml (override with PUBTRACK_CONFIG)."""

from __future__ import annotations

import os
import tomllib
from dataclasses import dataclass
from pathlib import Path

DEFAULT_CONFIG = Path(os.environ.get("XDG_CONFIG_HOME", Path.home() / ".config")) / "pubtrack" / "config.toml"
DEFAULT_DB = Path(os.environ.get("XDG_DATA_HOME", Path.home() / ".local" / "share")) / "pubtrack" / "pubtrack.sqlite"

TEMPLATE = """# pubtrack configuration. Keys stay here, never in the repository.

[author]
# Google Scholar user id: the value after user= in your profile URL
scholar_id = ""
# Surname as it appears in author lists, used to find your author position
surname = "Weiskittel"
# ORCID (preferred for resolving the OpenAlex author) and/or OpenAlex author id (A...)
orcid = ""
openalex_id = ""
# First publication year, used for academic age (A). Leave 0 to infer from the data.
first_year = 0

[openalex]
# Email for the polite pool; an API key raises the daily budget tenfold
mailto = ""
api_key = ""

[scholar]
# Seconds between Scholar page requests (randomized within this range)
min_delay = 10
max_delay = 25
# Optional SerpAPI key; if set, `pubtrack sync --scholar-via serpapi` uses it
serpapi_key = ""

[storage]
db_path = "{db}"
"""


@dataclass
class Config:
    scholar_id: str
    surname: str
    orcid: str
    openalex_id: str
    first_year: int
    mailto: str
    openalex_key: str
    min_delay: float
    max_delay: float
    serpapi_key: str
    db_path: Path
    path: Path


def config_path() -> Path:
    return Path(os.environ.get("PUBTRACK_CONFIG", DEFAULT_CONFIG))


def write_template(path: Path | None = None) -> Path:
    path = path or config_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    if not path.exists():
        path.write_text(TEMPLATE.format(db=DEFAULT_DB))
        path.chmod(0o600)
    return path


def load(path: Path | None = None) -> Config:
    path = path or config_path()
    if not path.exists():
        raise FileNotFoundError(f"No config at {path}. Run `pubtrack init` first.")
    raw = tomllib.loads(path.read_text())
    a, oa, sc, st = (raw.get(k, {}) for k in ("author", "openalex", "scholar", "storage"))
    return Config(
        scholar_id=a.get("scholar_id", "").strip(),
        surname=a.get("surname", "").strip(),
        orcid=a.get("orcid", "").strip(),
        openalex_id=a.get("openalex_id", "").strip(),
        first_year=int(a.get("first_year", 0) or 0),
        mailto=oa.get("mailto", "").strip(),
        openalex_key=oa.get("api_key", "").strip(),
        min_delay=float(sc.get("min_delay", 10)),
        max_delay=float(sc.get("max_delay", 25)),
        serpapi_key=sc.get("serpapi_key", "").strip(),
        db_path=Path(os.path.expanduser(st.get("db_path", str(DEFAULT_DB)))),
        path=path,
    )


def set_value(section: str, key: str, value: str, path: Path | None = None) -> None:
    """Minimal in place edit of one string key, preserving comments."""
    path = path or config_path()
    lines = path.read_text().splitlines()
    in_section = False
    for i, line in enumerate(lines):
        s = line.strip()
        if s.startswith("[") and s.endswith("]"):
            in_section = s[1:-1] == section
            continue
        if in_section and s.split("=")[0].strip() == key:
            lines[i] = f'{key} = "{value}"'
            path.write_text("\n".join(lines) + "\n")
            return
    raise KeyError(f"{section}.{key} not found in {path}")
