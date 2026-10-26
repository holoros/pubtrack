# pubtrack

Google Scholar and OpenAlex bibliometrics for a set of tracked authors: weekly snapshots, author comparison, per paper and per author citation forecasts with prediction intervals, printable impact reports, and copy ready impact statements. Everything refreshes itself on GitHub and publishes as a browser app on GitHub Pages.

**Dashboard:** https://holoros.github.io/pubtrack/  
**Static reports:** https://holoros.github.io/pubtrack/reports/

## How it works

```
authors.yml ──► GitHub Actions (Mondays, on edits to authors.yml, or on demand)
                  1. sync     Python collector: OpenAlex for everyone due, Scholar through SerpAPI
                              within a monthly search budget; matching, overrides, checks, indices
                  2. commit   data/*.csv (append only snapshots, one small diff per week)
                  3. build    R: smoke test, data bundle with forecasts, per author HTML reports,
                              Shinylive export of the Shiny app
                  4. deploy   GitHub Pages
```

The committed CSVs in `data/` are the source of truth. Each run loads them into an in memory SQLite database, works there, and writes them back sorted, so history is plain text, diffable, and survives any tool change.

### Scholar data routes

| Route | Cost | Where it runs | Notes |
| --- | --- | --- | --- |
| SerpAPI (default) | free tier, 250 searches a month | GitHub Actions | one search per 100 papers per profile; failed and cached searches are not charged; the budget guard defers Scholar for the stalest authors first and never fails a run |
| Local scrape | free | your machine, weekly user timer | `pubtrack install-timer`; scrapes with `scholarly` and pushes the data. Scholar's robots.txt disallows the `cstart=` paging a full profile pull needs, and Google's terms bar automated access that ignores robots.txt, so treat this as a fallback you run knowingly |
| None | free | GitHub Actions | OpenAlex only; every chart and statement switches to OpenAlex units |

Without a `SERPAPI_KEY` secret the workflow refreshes OpenAlex only and says so in the run summary.

## Add or change authors

Edit `authors.yml` on GitHub and commit. The workflow fetches anyone new and redeploys. An author needs at least an ORCID or an OpenAlex id; the Scholar id (the value after `user=` in the profile URL) is optional. Peers are cheaper at `scholar_cadence: monthly`. From a clone:

```bash
pubtrack add-author smith --name "Jane Q. Smith" --orcid 0000-0000-0000-0000 --scholar-id AbCdEfGAAAAJ --cadence monthly
pubtrack resolve              # fills openalex_id from ORCID for anyone missing one
```

## What the dashboard shows

**Overview** value boxes with 4 and 52 week change, an interactive publication portfolio (age against citations, coloured by field percentile tier, sized by projected next year citations), citations per year, and three year outlooks for h and total citations.
**Compare** a percentile shaded profile across authors, careers aligned on academic age with Hirsch m reference lines, projected h with 80% intervals, citation concentration curves, output per year, authorship roles, and a downloadable indices table.
**Trajectory** any stored index over time for the selected authors, with OpenAlex year end reconstructions before tracking began.
**Publications** a searchable paper table; selecting a paper shows its weekly counts, yearly counts with the fitted trend and 12 month forecast windows, and a publication statement.
**Forecast** fans for h and citations, papers closest to lifting h, and the fastest risers.
**Report** an author (and optional publication) report that prints cleanly to PDF, with a copy button on a 4 to 6 sentence impact statement for letters and emails. Statements can highlight papers by citations, field weighted impact, or current momentum.
**Data** runs, data quality flags, shared works between tracked authors, and methods.

The Methods card in the app documents every index and the forecast model. In short: each paper's rate comes from a Poisson log linear fit to its last six complete years of OpenAlex counts with parameter and overdispersion uncertainty, or a Gamma posterior rate when data are thin; rates convert to Scholar units per paper; 2,000 simulations give the intervals. Only existing papers are projected, so totals are conservative for active authors.

## Indices computed

h, g, i10, e, contemporary h, hI norm, hI annual, annual h, AWCR, AW, AWCRpA, m quotient, hm (Schreiber 2008, fractional counting), acc1, acc2, acc5, acc10, acc20, citations and papers per author, authors per paper, citations per paper and per career year, citation velocity, and from OpenAlex the median and mean FWCI and the shares of papers in the top 10% and 1% of their field. The comparison view adds the Gini coefficient of citations across papers, authorship role shares, and 12 month projections of h and citations.

**Annualized indices** put authors at different career stages on the same footing. Rates per career year: papers, g, i10 (alongside the m quotient and citations per career year). Rates per paper year: the mean and median of citations per paper divided by years since publication. A fixed window, the last five complete calendar years: citations received (h5, i10 and citations per year over that window; from OpenAlex counts by year, and from Scholar's reported h5 and i10 with its year histogram), and recent output (papers published in the window, the citations they hold, their h, citations per recent paper). Scholar's reported h5 and i10 window also includes the partial current year; the citation totals use complete years in both sources. These appear in the comparison heatmap, the key indices table of every report, and as trajectories.

## Roadmap

From a survey of Publish or Perish and comparable tools (October 2026), ranked by value:

1. Semantic Scholar Graph API: influential citation counts and citation intents (background, method, result) per paper.
2. Self citation and co author citation tiers through OpenAlex, ORCID and ROR, following the MIT licensed citation-constellation method.
3. OpenCitations Index v2 as an open cross check on per paper counts (no key, 180 requests a minute).
4. Ioannidis composite citation indicator (c-score) from the authorship role data already held, with and without self citations.
5. Unpaywall open access status and an OA share panel.
6. VOSviewer JSON export of co authorship and keyword networks for embedding.

## Local use

```bash
./install.sh                          # .venv, tests, R packages
pubtrack sync --dry-run               # show what is due and the SerpAPI budget
SERPAPI_KEY=... pubtrack sync         # fetch
pubtrack status -v                    # runs, flags, unmatched works per author
pubtrack override exclude GS_ID --author weiskittel
pubtrack export --author weiskittel --format report --year 2025
pubtrack import-v1 ~/.local/share/pubtrack/pubtrack.sqlite --author weiskittel   # bring in pubtrack 0.1 history
pubtrack dashboard                    # shiny::runApp("app") against data/
```

Preview the app on synthetic data: `python scripts/make_demo_store.py app/tests/fixtures/demo_store`, then `PUBTRACK_DATA=app/tests/fixtures/demo_store Rscript -e 'shiny::runApp("app")'`.

## Layout

```
authors.yml        tracked authors and settings (no secrets)
data/              the committed CSV store
src/pubtrack/      collector: scholar, openalex, matching, overrides, metrics, sync (planning and budget), store, cli
tests/             index checks and offline end to end tests
app/               Shiny app (app.R, R/ analytics, forecasts, statements, plots), build_bundle.R, tests/smoke.R
reports/           R Markdown author report and its renderer
.github/workflows/ refresh.yml
```

## Secrets (repository settings, never in files)

`SERPAPI_KEY` for Scholar through SerpAPI. Optional: `OPENALEX_MAILTO` (polite pool) and `OPENALEX_API_KEY` (free at openalex.org/settings/api). Since October 2026 OpenAlex refuses cursor paging beyond the first page without a key; without one the collector pages by number instead, which is free up to 10,000 works per author. A failed Scholar or OpenAlex fetch now fails the sync job after the data commit, so it shows up as a failed workflow email instead of quietly going stale.
