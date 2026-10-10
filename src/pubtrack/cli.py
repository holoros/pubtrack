"""Command line entry point: `pubtrack <command>`."""

from __future__ import annotations

import argparse
import json
import logging
import shutil
import subprocess
import sys
from datetime import date
from pathlib import Path

from . import config as cfgmod
from . import export, openalex, overrides
from .db import connect, init_schema, latest_run
from .sync import sync

HERE = Path(__file__).resolve().parent
PROJECT = HERE.parent.parent  # repo root when installed with pip install -e .


def _open(cfg):
    con = connect(cfg.db_path)
    init_schema(con, cfg.db_path)
    return con


def cmd_init(a):
    p = cfgmod.write_template()
    cfg = cfgmod.load(p)
    _open(cfg).close()
    print(f"Config:   {p}\nDatabase: {cfg.db_path}")
    print("Next: set author.scholar_id and author.orcid in the config, then run `pubtrack resolve`.")


def cmd_resolve(a):
    cfg = cfgmod.load()
    cands = openalex.resolve_author(cfg.orcid, a.name or "", cfg.mailto, cfg.openalex_key)
    if not cands:
        sys.exit("No OpenAlex author found. Check the ORCID or try --name.")
    for i, c in enumerate(cands):
        inst = ", ".join(x.get("display_name", "") for x in (c.get("last_known_institutions") or [])[:2])
        print(f"[{i}] {openalex.short_id(c['id'])}  {c.get('display_name')}  works={c.get('works_count')} "
              f"cites={c.get('cited_by_count')}  orcid={c.get('orcid')}  {inst}")
    if cfg.orcid and len(cands) == 1:
        oid = openalex.short_id(cands[0]["id"])
        cfgmod.set_value("author", "openalex_id", oid)
        print(f"Saved openalex_id = {oid}")
    else:
        print("Copy the right id(s) into author.openalex_id (comma separated if OpenAlex split your record).")


def _notify(summary: str, body: str) -> None:
    if shutil.which("notify-send"):
        subprocess.run(["notify-send", "-a", "pubtrack", summary, body], check=False)


def cmd_sync(a):
    cfg = cfgmod.load()
    con = _open(cfg)
    sources = ("scholar", "openalex") if a.source == "all" else (a.source,)
    st = sync(con, cfg, sources=sources, scholar_via=a.scholar_via)
    print(f"run {st['run_id']}: scholar={st['scholar_status']} ({st.get('n_scholar', 0)} works), "
          f"openalex={st['openalex_status']} ({st.get('n_openalex', 0)} works)")
    print("matching:", json.dumps(st["matching"]))
    for src in ("scholar", "openalex"):
        m = st.get(f"metrics_{src}")
        if m:
            keys = ["citations", "h", "g", "i10", "hc", "hI_norm", "awcr", "fwci_median", "velocity_per_year"]
            print(f"{src:9s}", "  ".join(f"{k}={m[k]:.4g}" for k in keys if k in m))
    for msg in st["messages"]:
        print("note:", msg)
    last3 = [r[0] for r in con.execute("SELECT scholar_status FROM runs WHERE origin = 'sync' "
                                       "ORDER BY run_id DESC LIMIT 3")]
    if len(last3) == 3 and all(s == "failed" for s in last3):
        _notify("pubtrack: Scholar blocked 3 runs in a row",
                "Try `pubtrack sync --source scholar` by hand, or set a SerpAPI key and use --scholar-via serpapi.")
    con.close()


def cmd_status(a):
    cfg = cfgmod.load()
    con = _open(cfg)
    print("Last runs:")
    for r in con.execute("SELECT run_id, snapshot_date, origin, scholar_status, openalex_status, n_scholar, "
                         "n_openalex, message FROM runs ORDER BY run_id DESC LIMIT 6"):
        print(f"  {r['run_id']:>4} {r['snapshot_date']} {r['origin']:10s} scholar={r['scholar_status']} "
              f"openalex={r['openalex_status']} n={r['n_scholar']}/{r['n_openalex']}"
              + (f"  [{r['message'][:100]}]" if r["message"] else ""))
    last = latest_run(con)
    if last:
        print(f"\nFlags on run {last['run_id']}:")
        for r in con.execute("SELECT kind, COUNT(*) n FROM flags WHERE run_id = ? GROUP BY kind", (last["run_id"],)):
            print(f"  {r['kind']:12s} {r['n']}")
        if a.verbose:
            for r in con.execute("SELECT f.kind, w.gs_id, f.detail FROM flags f LEFT JOIN works w USING (work_id) "
                                 "WHERE f.run_id = ? ORDER BY f.kind", (last["run_id"],)):
                print(f"  {r['kind']:12s} {r['gs_id'] or '':24s} {r['detail']}")
    con.close()


def cmd_override(a):
    cfg = cfgmod.load()
    con = _open(cfg)
    oid = overrides.add(con, a.action.replace("-", "_"), a.gs_id, a.target, a.note)
    print(f"override {oid} recorded; it applies from the next sync")
    con.close()


def cmd_export(a):
    cfg = cfgmod.load()
    con = _open(cfg)
    out = Path(a.out).expanduser()
    out.mkdir(parents=True, exist_ok=True)
    if a.format == "csv":
        p = export.write_csv(export.paper_rows(con), out / f"{date.today().isoformat()}_scholar-papers_DATA.csv")
        print(p)
    else:
        for p in export.annual_summary(con, a.year or date.today().year - 1, out):
            print(p)
    con.close()


def cmd_import_pop(a):
    cfg = cfgmod.load()
    con = _open(cfg)
    res = export.import_pop(con, Path(a.file).expanduser(), cfg.first_year)
    print(f"run {res['run_id']} dated {res['snapshot_date']}: {res['loaded']} papers loaded, "
          f"{len(res['skipped'])} unmatched")
    for t in res["skipped"][:20]:
        print("  unmatched:", t)
    con.close()


def cmd_dashboard(a):
    cfg = cfgmod.load()
    app = PROJECT / "r" / "app.R"
    if not app.exists():
        sys.exit(f"Dashboard not found at {app}")
    env_cmd = (f"Sys.setenv(PUBTRACK_DB='{cfg.db_path}'); "
               f"shiny::runApp('{app.parent}', port={a.port}, launch.browser=TRUE)")
    subprocess.run(["Rscript", "-e", env_cmd], check=False)


def cmd_install_timer(a):
    unit_dir = Path.home() / ".config" / "systemd" / "user"
    unit_dir.mkdir(parents=True, exist_ok=True)
    exe = shutil.which("pubtrack") or sys.argv[0]
    for name in ("pubtrack.service", "pubtrack.timer"):
        text = (PROJECT / "systemd" / name).read_text().replace("@PUBTRACK@", exe)
        (unit_dir / name).write_text(text)
    subprocess.run(["systemctl", "--user", "daemon-reload"], check=False)
    subprocess.run(["systemctl", "--user", "enable", "--now", "pubtrack.timer"], check=False)
    subprocess.run(["systemctl", "--user", "list-timers", "pubtrack.timer"], check=False)


def main(argv=None):
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    p = argparse.ArgumentParser(prog="pubtrack", description=__doc__)
    sub = p.add_subparsers(dest="cmd", required=True)

    sub.add_parser("init", help="create config and database").set_defaults(fn=cmd_init)

    s = sub.add_parser("resolve", help="find your OpenAlex author id from ORCID or name")
    s.add_argument("--name", default="")
    s.set_defaults(fn=cmd_resolve)

    s = sub.add_parser("sync", help="fetch, match, check and compute metrics")
    s.add_argument("--source", choices=["all", "scholar", "openalex"], default="all")
    s.add_argument("--scholar-via", choices=["scholarly", "serpapi"], default="scholarly")
    s.set_defaults(fn=cmd_sync)

    s = sub.add_parser("status", help="recent runs and data quality flags")
    s.add_argument("-v", "--verbose", action="store_true")
    s.set_defaults(fn=cmd_status)

    s = sub.add_parser("override", help="record a manual correction")
    s.add_argument("action", choices=["merge", "exclude", "include", "set-year", "set-doi", "set-openalex"])
    s.add_argument("gs_id", help="Scholar author_pub_id of the paper to correct")
    s.add_argument("target", nargs="?", help="merge target gs_id, year, DOI, or OpenAlex W id")
    s.add_argument("--note")
    s.set_defaults(fn=cmd_override)

    s = sub.add_parser("export", help="paper table CSV or annual summary")
    s.add_argument("--format", choices=["csv", "report"], default="csv")
    s.add_argument("--year", type=int)
    s.add_argument("--out", default=".")
    s.set_defaults(fn=cmd_export)

    s = sub.add_parser("import-pop", help="seed history from a Publish or Perish CSV export")
    s.add_argument("file")
    s.set_defaults(fn=cmd_import_pop)

    s = sub.add_parser("dashboard", help="launch the Shiny dashboard")
    s.add_argument("--port", type=int, default=7461)
    s.set_defaults(fn=cmd_dashboard)

    sub.add_parser("install-timer", help="install and start the weekly systemd user timer").set_defaults(fn=cmd_install_timer)

    a = p.parse_args(argv)
    a.fn(a)


if __name__ == "__main__":
    main()
