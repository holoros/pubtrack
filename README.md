# pubtrack

Weekly Google Scholar and OpenAlex citation snapshots for one author, with impact indices computed locally and an R Shiny dashboard. A replacement for running Publish or Perish under Wine. The design specification is the Claude Docs page "Scholar Impact Tracker: Design Specification".

## Install

```bash
cd ~/Documents/Claude/tools/pubtrack
./install.sh                 # .venv, editable install, unit tests, ~/.local/bin/pubtrack, R packages
pubtrack init                # writes ~/.config/pubtrack/config.toml and the database
```

Edit `~/.config/pubtrack/config.toml`: set `scholar_id` (the value after `user=` in your Scholar profile URL), `orcid`, and `mailto`. A free OpenAlex API key is optional. Then:

```bash
pubtrack resolve             # finds your OpenAlex author id from the ORCID and saves it
pubtrack sync                # first snapshot
pubtrack status -v           # runs, flags, unmatched works
pubtrack install-timer       # weekly, Sunday 03:00 plus up to 2 h random delay
pubtrack dashboard           # opens the Shiny app in a browser
```

If OpenAlex has split your record across several author ids, list them comma separated in `openalex_id`.

## First run checklist

1. `pubtrack status -v` should show no `h_mismatch` flag. If it does, the profile list and Scholar's own h disagree; check for hidden or duplicated entries.
2. Resolve unmatched works: `pubtrack override set-openalex <gs_id> W123456789`, or `set-doi`, or `exclude` for items that are not papers.
3. Merge Scholar duplicates: `pubtrack override merge <duplicate_gs_id> <main_gs_id>`. Merged citations are added to the main record, which can double count citing documents that cite both versions.
4. If you have an old Publish or Perish CSV export, `pubtrack import-pop file.csv` seeds a dated historical point.

Overrides are recorded once and reapplied on every run. Gs ids appear in `pubtrack status -v` and in the CSV export.

## Commands

| Command | Does |
| --- | --- |
| `pubtrack sync [--source scholar\|openalex] [--scholar-via serpapi]` | Fetch, match, apply overrides, check, compute metrics |
| `pubtrack status [-v]` | Recent runs and flags |
| `pubtrack override ACTION GS_ID [TARGET]` | merge, exclude, include, set-year, set-doi, set-openalex |
| `pubtrack export --format csv\|report [--year Y] [--out DIR]` | Paper table, or annual summary CSVs for activity reports |
| `pubtrack import-pop FILE` | Load a Publish or Perish export as history |
| `pubtrack dashboard` | Launch the Shiny app |

## Layout

```
src/pubtrack/   schema.sql, db, scholar, openalex, matching, overrides, metrics, sync, export, cli
tests/          hand computed index checks and an offline end to end pipeline test
r/app.R         Shiny dashboard (read only); r/tests/smoke.R renders every view headless
scripts/        make_demo_db.py builds a synthetic database for previewing the dashboard
systemd/        user service and timer templates (installed by `pubtrack install-timer`)
```

## Notes

Scholar is reached only through its profile pages (about one request per 100 works), never per article pages. A blocked Scholar fetch is logged and the OpenAlex half of the run still completes; after three consecutive Scholar failures a desktop notification suggests a manual run or the SerpAPI route. Scholar and OpenAlex counts are never mixed within one metric.

`scholarly` imports the bibtexparser 1.x API, so bibtexparser is pinned below 2.

Preview the dashboard without your data:

```bash
.venv/bin/python scripts/make_demo_db.py /tmp/pubtrack_demo.sqlite
PUBTRACK_DB=/tmp/pubtrack_demo.sqlite Rscript -e "shiny::runApp('r')"
```
