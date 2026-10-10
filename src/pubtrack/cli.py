"""Command line entry point: `pubtrack <command>`. Run from the repository root (or set PUBTRACK_HOME)."""

from __future__ import annotations

import argparse
import json
import logging
import shutil
import subprocess
import sys
from datetime import date
from pathlib import Path

import yaml

from . import config as cfgmod
from . import export, openalex, overrides, store
from .db import latest_run
from .sync import plan, serpapi_used_this_month, sync_all

log = logging.getLogger("pubtrack")


def _cfg(a):
    return cfgmod.load(Path(a.authors) if a.authors else None)


def _store(cfg, a):
    return Path(a.store).resolve() if getattr(a, "store", None) else cfg.store_dir


def _author_arg(cfg, a):
    if a.author:
        return cfg.author(a.author).id
    if len(cfg.authors) == 1:
        return cfg.authors[0].id
    sys.exit("Several authors are tracked; pass --author ID")


# ---------------------------------------------------------------- commands

def cmd_resolve(a):
    """Find OpenAlex ids from ORCID (or name) for authors missing one, and save them."""
    cfg = _cfg(a)
    changed = 0
    for au in cfg.authors:
        if a.author and au.id != a.author:
            continue
        if au.openalex_ids and not a.force:
            continue
        try:
            cands = openalex.resolve_author(au.orcid, "" if au.orcid else au.name, cfg.settings.mailto,
                                            cfg.settings.openalex_key)
        except openalex.OpenAlexError as e:
            print(f"{au.id}: {e}")
            continue
        for i, c in enumerate(cands[:10]):
            inst = ", ".join(x.get("display_name", "") for x in (c.get("last_known_institutions") or [])[:2])
            print(f"{au.id} [{i}] {openalex.short_id(c['id'])}  {c.get('display_name')}  works={c.get('works_count')} "
                  f"cites={c.get('cited_by_count')}  orcid={c.get('orcid')}  {inst}")
        if au.orcid and len(cands) == 1:
            oid = openalex.short_id(cands[0]["id"])
            cfgmod.set_author_field(cfg.path, au.id, "openalex_id", oid)
            print(f"{au.id}: saved openalex_id = {oid}")
            changed += 1
        else:
            print(f"{au.id}: copy the right id(s) into openalex_id (comma separated if the record is split).")
    print(f"{changed} author(s) updated")


def cmd_add_author(a):
    cfg = _cfg(a)
    if any(x.id == a.id for x in cfg.authors):
        sys.exit(f"{a.id} is already tracked")
    if not cfgmod.ID_RE.match(a.id):
        sys.exit("id: lowercase letters, digits, - or _")
    oid = a.openalex_id or ""
    if not oid and a.orcid:
        try:
            cands = openalex.resolve_author(a.orcid.replace("https://orcid.org/", ""), "", cfg.settings.mailto,
                                            cfg.settings.openalex_key)
            oid = openalex.short_id(cands[0]["id"]) if cands else ""
        except openalex.OpenAlexError as e:
            print(f"OpenAlex lookup failed ({e}); add openalex_id later with `pubtrack resolve`")
    entry = {"id": a.id, "name": a.name, "scholar_id": a.scholar_id or "", "orcid": a.orcid or "",
             "openalex_id": oid, "first_year": a.first_year or 0, "scholar_cadence": a.cadence,
             "group": a.group or ""}
    block = yaml.safe_dump([entry], sort_keys=False, allow_unicode=True, default_flow_style=False)
    text = cfg.path.read_text().rstrip("\n") + "\n\n" + "\n".join("  " + ln for ln in block.splitlines()) + "\n"
    cfg.path.write_text(text)
    cfgmod.load(cfg.path)  # validate
    print(f"added {a.id} (openalex_id={oid or 'unset'}) to {cfg.path}")


def cmd_sync(a):
    cfg = _cfg(a)
    st_dir = _store(cfg, a)
    only = [x.strip() for x in a.author.split(",")] if a.author else None
    via = a.scholar_via or None
    with store.opened(st_dir, write=not a.dry_run) as con:
        plans = plan(con, cfg, only=only, force_scholar=a.force_scholar, force_openalex=a.force_openalex,
                     scholar_via=via)
        used = serpapi_used_this_month(con)
        print(f"SerpAPI used this month: {used} of {cfg.settings.serpapi_monthly_budget}")
        for p in plans:
            print(f"  {p.author.id:20s} scholar={'yes' if p.scholar else 'no ':3s} openalex={'yes' if p.openalex else 'no'}"
                  f"  est_serpapi={p.serpapi_estimate}  ({p.reason})")
        if a.dry_run:
            print("dry run: nothing fetched, nothing written")
            return
        results = sync_all(con, cfg, plans, scholar_via=via)
        for st in results:
            print(f"{st['author_id']}: run {st['run_id']} scholar={st['scholar_status']} ({st.get('n_scholar', 0)}) "
                  f"openalex={st['openalex_status']} ({st.get('n_openalex', 0)})  matching={json.dumps(st['matching'])}")
            for src in ("scholar", "openalex"):
                m = st.get(f"metrics_{src}")
                if m:
                    keys = ["citations", "h", "g", "i10", "hc", "hI_norm", "fwci_median", "velocity_per_year"]
                    print(f"    {src:9s}", "  ".join(f"{k}={m[k]:.4g}" for k in keys if k in m))
            for msg in st["messages"]:
                print("    note:", msg)
    if a.summary:
        _write_summary(Path(a.summary), results)
    if a.strict and any(st["openalex_status"] == "failed" for st in results):
        sys.exit(2)


def _write_summary(path: Path, results: list[dict]) -> None:
    """Markdown for the GitHub Actions job summary."""
    lines = ["| Author | Scholar | OpenAlex | Scholar h | Citations | Notes |", "|---|---|---|---|---|---|"]
    for st in results:
        m = st.get("metrics_scholar") or {}
        lines.append(f"| {st['author_id']} | {st['scholar_status']} ({st.get('n_scholar') or 0}) | "
                     f"{st['openalex_status']} ({st.get('n_openalex') or 0}) | {m.get('h', '')} | "
                     f"{m.get('citations', '')} | {'; '.join(st['messages'])[:200]} |")
    if not results:
        lines.append("| (nothing due) | | | | | |")
    with path.open("a") as f:
        f.write("\n".join(lines) + "\n")


def cmd_status(a):
    cfg = _cfg(a)
    with store.opened(_store(cfg, a)) as con:
        print(f"SerpAPI used this month: {serpapi_used_this_month(con)} of {cfg.settings.serpapi_monthly_budget}")
        for au in cfg.authors:
            if a.author and au.id != a.author:
                continue
            print(f"\n{au.id} ({au.name})")
            for r in con.execute("SELECT run_id, snapshot_date, origin, scholar_status, openalex_status, n_scholar, "
                                 "n_openalex, message FROM runs WHERE author_id = ? ORDER BY run_id DESC LIMIT 4",
                                 (au.id,)):
                print(f"  {r['run_id']:>4} {r['snapshot_date']} {r['origin']:10s} scholar={r['scholar_status']} "
                      f"openalex={r['openalex_status']} n={r['n_scholar']}/{r['n_openalex']}"
                      + (f"  [{r['message'][:90]}]" if r["message"] else ""))
            last = latest_run(con, au.id)
            if not last:
                continue
            for r in con.execute("SELECT kind, COUNT(*) n FROM flags WHERE run_id = ? GROUP BY kind", (last["run_id"],)):
                print(f"  flag {r['kind']:12s} {r['n']}")
            if a.verbose:
                for r in con.execute("SELECT f.kind, w.gs_id, f.detail FROM flags f LEFT JOIN works w USING (work_id) "
                                     "WHERE f.run_id = ? ORDER BY f.kind", (last["run_id"],)):
                    print(f"    {r['kind']:12s} {r['gs_id'] or '':24s} {r['detail']}")


def cmd_override(a):
    cfg = _cfg(a)
    aid = _author_arg(cfg, a)
    with store.opened(_store(cfg, a), write=True) as con:
        oid = overrides.add(con, aid, a.action.replace("-", "_"), a.gs_id, a.target, a.note)
    print(f"override {oid} recorded for {aid}; it applies from the next sync")


def cmd_export(a):
    cfg = _cfg(a)
    aid = _author_arg(cfg, a)
    out = Path(a.out).expanduser()
    out.mkdir(parents=True, exist_ok=True)
    with store.opened(_store(cfg, a)) as con:
        if a.format == "csv":
            print(export.write_csv(export.paper_rows(con, aid), out / f"{date.today().isoformat()}_{aid}-papers_DATA.csv"))
        else:
            for p in export.annual_summary(con, aid, a.year or date.today().year - 1, out):
                print(p)


def cmd_import_pop(a):
    cfg = _cfg(a)
    au = cfg.author(_author_arg(cfg, a))
    with store.opened(_store(cfg, a), write=True) as con:
        res = export.import_pop(con, au.id, Path(a.file).expanduser(), au.first_year)
    print(f"run {res['run_id']} dated {res['snapshot_date']}: {res['loaded']} papers loaded, {len(res['skipped'])} unmatched")
    for t in res["skipped"][:20]:
        print("  unmatched:", t)


def cmd_import_v1(a):
    cfg = _cfg(a)
    aid = _author_arg(cfg, a)
    with store.opened(_store(cfg, a), write=True) as con:
        n = export.import_v1(con, Path(a.db).expanduser(), aid)
    print(f"imported into {aid}: " + ", ".join(f"{k}={v}" for k, v in n.items()))


def cmd_dashboard(a):
    root = cfgmod.repo_root()
    if not (root / "app" / "app.R").exists():
        sys.exit(f"Dashboard not found under {root / 'app'}")
    subprocess.run(["Rscript", "-e", f"shiny::runApp('{root / 'app'}', port={a.port}, launch.browser=TRUE)"], check=False)


def cmd_install_timer(a):
    """Local Scholar route: a weekly user timer that scrapes Scholar from this machine and pushes the data."""
    root = cfgmod.repo_root()
    unit_dir = Path.home() / ".config" / "systemd" / "user"
    unit_dir.mkdir(parents=True, exist_ok=True)
    script = root / "scripts" / "local_scholar_push.sh"
    for name in ("pubtrack.service", "pubtrack.timer"):
        text = (root / "systemd" / name).read_text().replace("@SCRIPT@", str(script))
        (unit_dir / name).write_text(text)
    for cmd in (["daemon-reload"], ["enable", "--now", "pubtrack.timer"], ["list-timers", "pubtrack.timer"]):
        subprocess.run(["systemctl", "--user", *cmd], check=False)


def main(argv=None):
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    p = argparse.ArgumentParser(prog="pubtrack", description=__doc__)
    p.add_argument("--authors", help="path to authors.yml (default: found from the working directory)")
    sub = p.add_subparsers(dest="cmd", required=True)

    s = sub.add_parser("resolve", help="fill missing OpenAlex ids from ORCID or name")
    s.add_argument("--author")
    s.add_argument("--force", action="store_true")
    s.set_defaults(fn=cmd_resolve)

    s = sub.add_parser("add-author", help="append an author to authors.yml")
    s.add_argument("id")
    s.add_argument("--name", required=True)
    s.add_argument("--scholar-id")
    s.add_argument("--orcid")
    s.add_argument("--openalex-id")
    s.add_argument("--first-year", type=int)
    s.add_argument("--cadence", choices=list(cfgmod.CADENCE_DAYS), default="monthly")
    s.add_argument("--group")
    s.set_defaults(fn=cmd_add_author)

    s = sub.add_parser("sync", help="refresh every author that is due")
    s.add_argument("--author", help="comma separated author ids (default: all)")
    s.add_argument("--store")
    s.add_argument("--scholar-via", choices=["serpapi", "scholarly", "none"])
    s.add_argument("--force-scholar", action="store_true")
    s.add_argument("--force-openalex", action="store_true")
    s.add_argument("--dry-run", action="store_true")
    s.add_argument("--summary", help="append a markdown summary to this file (GITHUB_STEP_SUMMARY)")
    s.add_argument("--strict", action="store_true", help="exit 2 if any OpenAlex fetch failed")
    s.set_defaults(fn=cmd_sync)

    s = sub.add_parser("status", help="recent runs and data quality flags")
    s.add_argument("--author")
    s.add_argument("--store")
    s.add_argument("-v", "--verbose", action="store_true")
    s.set_defaults(fn=cmd_status)

    s = sub.add_parser("override", help="record a manual correction")
    s.add_argument("action", choices=["merge", "exclude", "include", "set-year", "set-doi", "set-openalex"])
    s.add_argument("gs_id", help="Scholar id of the paper to correct")
    s.add_argument("target", nargs="?", help="merge target gs_id, year, DOI, or OpenAlex W id")
    s.add_argument("--author")
    s.add_argument("--store")
    s.add_argument("--note")
    s.set_defaults(fn=cmd_override)

    s = sub.add_parser("export", help="paper table CSV or annual summary")
    s.add_argument("--author")
    s.add_argument("--store")
    s.add_argument("--format", choices=["csv", "report"], default="csv")
    s.add_argument("--year", type=int)
    s.add_argument("--out", default=".")
    s.set_defaults(fn=cmd_export)

    s = sub.add_parser("import-pop", help="seed history from a Publish or Perish CSV export")
    s.add_argument("file")
    s.add_argument("--author")
    s.add_argument("--store")
    s.set_defaults(fn=cmd_import_pop)

    s = sub.add_parser("import-v1", help="import a pubtrack 0.1 single author database")
    s.add_argument("db")
    s.add_argument("--author")
    s.add_argument("--store")
    s.set_defaults(fn=cmd_import_v1)

    s = sub.add_parser("dashboard", help="launch the Shiny app locally")
    s.add_argument("--port", type=int, default=7461)
    s.set_defaults(fn=cmd_dashboard)

    sub.add_parser("install-timer", help="weekly local Scholar scrape that pushes to GitHub").set_defaults(fn=cmd_install_timer)

    a = p.parse_args(argv)
    a.fn(a)


if __name__ == "__main__":
    main()
