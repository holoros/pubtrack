# Benchmark tools

Standalone OpenAlex scripts that extend pubtrack's per-author indices with external comparisons. They read
`OPENALEX_API_KEY` from the environment (free at openalex.org/settings/api; the free keyless budget is 1,000
requests a day per IP and runs out quickly on cohort pulls). Python 3.9+ with `requests`.

| Script | What it does | Cost |
|---|---|---|
| `cohort.py` | Reference cohort (default: US authors in OpenAlex subfield 1107 Forestry, 15 to 1,500 works, ORCID) with first publication year and ten years of citations and works | about 1 call per author (3,575 authors for the default) |
| `bench.py` | Percentiles of target authors against cohort members at the same career stage (years since PhD), reported under four stage rules so results read as a range | no API calls |
| `affiliation_match.py` | Recovers an author's works by raw name plus institution and career years when OpenAlex has split or merged the record with namesakes | a few calls per spell |
| `if_weighted.py` | Citations weighted by the citing journal's 2-year mean citedness, and own papers weighted by venue | about 1 call per 200 citing works |

Notes from the first use (8 November 2026). Career-stage rules built from first publication year miss the
true PhD year by about three years, as wide as a plus or minus 3 year comparison window, so read percentiles
as ranges. The cohort's 15-work floor understates percentiles for targets with fewer works. Impact-factor
weighting tracked raw counts almost exactly (rho 0.98) and mostly adds field differences; FWCI is the
field-fair alternative. No author lists, personnel data or outputs are committed here.
