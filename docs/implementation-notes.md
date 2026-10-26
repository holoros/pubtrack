# Implementation notes: pubtrack 0.2

Build of 10 to 11 October 2026, from pubtrack 0.1 (single author, local SQLite, local Shiny dashboard) found in `~/Documents/Claude/tools/pubtrack`.

## Content inventory against 0.1

**Kept.** Every 0.1 collector (scholarly and SerpAPI Scholar routes, OpenAlex), the matching rules (DOI, exact title, fuzzy title with margin), overrides (merge, exclude, include, set year, set DOI, set OpenAlex), data checks (h mismatch, Scholar to OpenAlex ratio, citation drops, unmatched), every index (h, g, i10, e, hc, hI norm, hI annual, annual h, AWCR, AW, AWCRpA, m quotient, velocity, interim rate, FWCI median and mean, top 10% and top 1% shares), the CSV paper export, the annual summary export, the Publish or Perish import, the systemd timer, and every dashboard view (overview, trajectory with year end reconstruction, papers with history, h core, field impact, data quality).

**Changed.** Single author became multi author (`authors.yml`, `author_id` on runs, works and overrides; schema v2). Storage moved from a local SQLite file to committed CSVs under `data/`, loaded into an in memory SQLite per run. `config.toml` became `authors.yml` plus environment secrets. The Shiny app moved from `r/` to `app/`, reads a prebuilt bundle, and runs in the browser through Shinylive. The field impact view became the portfolio map (FWCI tiers plus projected citations). The systemd timer now runs `scripts/local_scholar_push.sh`. Requires Python 3.9 or later (was 3.11).

**Added.** Comparison across authors (percentile heatmap, career age curves with Hirsch m references, concentration curves and Gini, output per year, authorship roles, shared works). Per paper and per author forecasts with 80% and 95% intervals and an annual backtest gate. Author and publication reports (in app, printable; static HTML per author). Copy ready impact statements. Publish or Perish metrics missing from 0.1 (hm, acc1 to acc20, citations and papers per author, authors per paper). SerpAPI monthly budget planner and cadence per author. OpenAlex title search fallback for works missing from the author's OpenAlex record. Weekly GitHub Actions refresh, test, build and deploy. MIT license.

**Removed.** `smoke-png/` (generated 0.1 outputs; the smoke test now writes to a temporary folder and CI uploads the figures as an artifact). `scripts/make_demo_db.py` (replaced by `make_demo_store.py`, three synthetic authors). The 0.1 demo database survives as `app/tests/fixtures/demo_v1.sqlite`, used to test `pubtrack import-v1`.

## Deviations

- R checks ran locally from the Ubuntu R build and in CI, not on firebreather (SSH refused on 10 October) or Cardinal (connector dropping). Cardinal did run the first Shinylive export successfully.
- `bsicons` is optional at run time because the local R build lacks it; CI installs it.

## Discovered unknowns

- OpenAlex attributes about 30% of the Scholar record to other author ids or omits it. The title search fallback closes part of this; the rest is listed under Data.
- OpenAlex keeps duplicate records for one work (preprint and article). Matching now treats near identical OpenAlex titles as one work.
- OpenAlex title search returns HTTP 500 on parentheses and ellipses. Queries are reduced to plain words.
- The `github-pages` environment was created while `dev` was the default branch and allowed only `dev`; `main` was added.

## Failed repairs

- First forecast (independent per paper Poisson log linear fits, bounded slope): projected 1,514 new citations in 12 months against 1,077 received in 2025. Rejected on the magnitude check. Replaced by empirical Bayes slope shrinkage plus a damped trend; the backtest then predicted 780 (80% interval 733 to 833) against 739 observed for 2025.
- Wrapping every `plot_` function at source time to wrap captions failed under Shiny's autoload environment; replaced by explicit `wrap_caption()` calls at static render sites.

## Deferred

- Semantic Scholar influential citations and intents, OpenCitations cross check, self citation tiers, Ioannidis c-score, Unpaywall OA share, VOSviewer export (see README roadmap).
- Reconstructing Scholar unit year end h before tracking began (the h outlook shows OpenAlex reconstructions in grey).

# Implementation notes: pubtrack 0.2.1 (26 October 2026)

**Why.** The weekly runs of 19 and 26 October refreshed Scholar but failed OpenAlex with HTTP 401: OpenAlex now requires an API key for cursor paging past the first page. The failure sat in `runs.csv` and the job summary while the job stayed green, so every OpenAlex index was stale from 11 October.

**Changed.** `openalex.fetch_works` pages by cursor only when `OPENALEX_API_KEY` is set and by page number otherwise (200 per page, free up to 10,000 results); verified against the live API from firebreather on 26 October (cursor without key: 401 past page 1; `page=2`: 200). `pubtrack sync` gained `--failed-flag`, writes a `::error` annotation per failed source under GitHub Actions, and `--strict` now covers Scholar as well. The workflow fails the sync job after the data commit when the flag file exists and still builds and deploys from the committed data.

**Added.** Annualized indices in the collector (`metrics.compute`, both sources) and in the bundle (`annualized_block` in `app/R/10_data.R`, computed from the bundle so every author has them at once): papers, g and i10 per career year; mean and median citations per paper per year since publication; last five complete years window: citations received, citations per year, h5 and i10 (OpenAlex from counts by year; Scholar from the reported h5 and i10 and the complete year histogram); recent output: papers published in the window, citations to them, their h, citations per recent paper. Exposed in the trajectory metric list, the comparison heatmap (eight new rows), and the key indices table in the app report and the static report. Tests: `tests/test_openalex_paging.py` (three cases) and annualized checks in `test_metrics.py` and `test_pipeline.py`.

**Removed.** Nothing.

**Known limits.** Stored annualized metrics begin with the first sync after this change; earlier runs do not carry them, so their trajectories start on 2 November 2026 unless a run is forced. The bundle values exist immediately. Scholar h5 and i10 (5 y) are as reported (window includes the partial current year); OpenAlex h5 uses five complete years, so the two are not strictly comparable across sources, only across authors within a source.
