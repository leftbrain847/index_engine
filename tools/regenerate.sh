#!/usr/bin/env bash
# Rebuild every generated output from the repo clones in $1. Run by refresh.yml, and again on top of the latest
# main if your edits or merges land mid-run, so the refresh never throws its work away.
set -euo pipefail
scan="$1"
python index.py refresh "$scan" | tee "$RUNNER_TEMP/refresh.log"
if grep -q "NOT IN INDEX" "$RUNNER_TEMP/refresh.log"; then
  echo "::notice::$(grep -o 'NOT IN INDEX.*' "$RUNNER_TEMP/refresh.log") — add them to index.yml"
fi
python control.py "$scan"
mkdir -p site_demo && python tools/build_demo.py site_demo/command_center_demo.html   # before UAT, which tests it
python uat.py "$scan" || echo "::warning::UAT run failed; keeping the last results"
python tools/build_artifact.py "$scan"
# the public sprint site: built here, published by its own step (never committed to this repo)
python tools/build_site.py "$scan" "$RUNNER_TEMP/site"
python tools/build_showcase.py "$scan" "$RUNNER_TEMP/showcase" "$RUNNER_TEMP/site"   # after the sprint site: lists its builds (from site_builds.json beside it)
