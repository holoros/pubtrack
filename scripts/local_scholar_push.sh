#!/usr/bin/env bash
# Optional second Scholar route: scrape Scholar from this machine (a home or campus address is
# blocked far less than a cloud runner), commit the data, and push. GitHub Actions then rebuilds
# the dashboard. Installed as a weekly user timer by `pubtrack install-timer`.
set -euo pipefail
cd "$(dirname "$0")/.."
git pull --rebase --quiet
.venv/bin/pubtrack sync --scholar-via scholarly
if [ -n "$(git status --porcelain data)" ]; then
  git add data
  git commit -q -m "data: local Scholar refresh $(date +%F)"
  git push -q || echo "push failed; data committed locally"
else
  echo "no data changes"
fi
