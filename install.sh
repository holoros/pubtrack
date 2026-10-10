#!/usr/bin/env bash
# Local install: Python collector in .venv (with the optional Scholar scraper), tests, and R packages for the app.
set -euo pipefail
cd "$(dirname "$0")"
python3 -m venv .venv
.venv/bin/pip install -q --upgrade pip
.venv/bin/pip install -q -e ".[test,scrape]"
.venv/bin/python -m pytest -q tests
mkdir -p "$HOME/.local/bin"
ln -sf "$PWD/.venv/bin/pubtrack" "$HOME/.local/bin/pubtrack"
echo "pubtrack installed: $(command -v pubtrack || echo "$HOME/.local/bin/pubtrack")"
if command -v Rscript >/dev/null; then
  Rscript -e 'need <- c("shiny","bslib","bsicons","plotly","DT","dplyr","tidyr","tibble","ggplot2","scales","yaml","ragg","rmarkdown");
              miss <- need[!vapply(need, requireNamespace, logical(1), quietly = TRUE)];
              if (length(miss)) install.packages(miss, repos = "https://cloud.r-project.org") else message("R packages present")'
fi
