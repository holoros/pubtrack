"""Place target authors against a cohort at the same career stage.

targets.csv columns: name, phd_year (blank allowed), first_year, cites_window, h
Cohort career stage = current year minus (first publication year - OFFSET), where OFFSET is the median
(first publication year - PhD year) among targets with a known PhD year. Percentiles compare each target
with cohort members within --window years of the same stage, and are repeated with the cohort rule applied
to the target and with a wider window, so results can be read as a range.

Usage: python3 bench.py --cohort cohort.csv --targets targets.csv [--window 3] [--out bench.csv]
"""
from __future__ import annotations
import argparse, csv, statistics
from datetime import date


def pct(vals, x):
    return 100 * (sum(v < x for v in vals) + 0.5 * sum(v == x for v in vals)) / len(vals)


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--cohort", default="cohort.csv")
    ap.add_argument("--targets", default="targets.csv")
    ap.add_argument("--window", type=int, default=3)
    ap.add_argument("--out", default="bench.csv")
    a = ap.parse_args()
    cur = date.today().year
    w0, w1 = cur - 5, cur - 1
    T = list(csv.DictReader(open(a.targets)))
    off = statistics.median(int(t["first_year"]) - int(t["phd_year"]) for t in T if t.get("phd_year"))
    coh = []
    for r in csv.DictReader(open(a.cohort)):
        if not r["first_year"] or int(r["first_year"]) < 1960:
            continue
        cw = sum(int(r.get(f"c{y}", 0)) for y in range(w0, w1 + 1))
        if sum(int(r.get(f"w{y}", 0)) for y in range(w0, w1 + 1)) == 0:
            continue
        coh.append(dict(stage=cur - (int(r["first_year"]) - off), cw=cw, h=int(r["h"] or 0)))
    rows = []
    for t in T:
        stage_phd = cur - int(t["phd_year"]) if t.get("phd_year") else cur - (int(t["first_year"]) - off)
        stage_rule = cur - (int(t["first_year"]) - off)
        cw, h = int(t["cites_window"]), int(t["h"])
        res = dict(name=t["name"], years_since_phd=stage_phd)
        for lab, st, win in (("phd", stage_phd, a.window), ("rule", stage_rule, a.window),
                             ("phd_wide", stage_phd, a.window + 2), ("rule_wide", stage_rule, a.window + 2)):
            band = [c for c in coh if abs(c["stage"] - st) <= win]
            res[f"pct_cites_{lab}"] = round(pct([c["cw"] for c in band], cw), 1)
        band = [c for c in coh if abs(c["stage"] - stage_phd) <= a.window]
        res.update(cohort_n=len(band), cohort_median_cites=statistics.median(c["cw"] for c in band),
                   pct_h=round(pct([c["h"] for c in band], h), 1))
        rows.append(res)
    with open(a.out, "w", newline="") as f:
        wr = csv.DictWriter(f, fieldnames=list(rows[0])); wr.writeheader(); wr.writerows(rows)
    print(f"offset {off}; active cohort {len(coh)}; wrote {a.out}")


if __name__ == "__main__":
    main()
