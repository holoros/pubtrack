#!/usr/bin/env bash
# Install pubtrack into a project virtual environment and put the command on PATH.
set -euo pipefail
cd "$(dirname "$0")"

python3 -m venv .venv
.venv/bin/pip install -q --upgrade pip
.venv/bin/pip install -q -e ".[test]"
.venv/bin/python -m pytest -q tests

mkdir -p "$HOME/.local/bin"
ln -sf "$PWD/.venv/bin/pubtrack" "$HOME/.local/bin/pubtrack"
echo "pubtrack installed: $(command -v pubtrack || echo "$HOME/.local/bin/pubtrack (add ~/.local/bin to PATH)")"

if command -v Rscript >/dev/null; then
  Rscript -e 'need <- c("shiny","bslib","DT","DBI","RSQLite","dplyr","tidyr","ggplot2","scales","ragg");
              miss <- need[!vapply(need, requireNamespace, logical(1), quietly = TRUE)];
              if (length(miss)) { message("Installing R packages: ", paste(miss, collapse = ", "));
                install.packages(miss, repos = "https://cloud.r-project.org") } else message("R packages present")'
else
  echo "Rscript not found; the dashboard needs R with shiny, bslib, DT, RSQLite, dplyr, tidyr, ggplot2, scales."
fi
