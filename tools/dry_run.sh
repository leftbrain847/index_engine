#!/usr/bin/env bash
# Local dry run: rebuild the index against a scan directory in a throwaway copy of this repo.
# Nothing in this checkout changes and nothing is pushed or published (the public sites are not built).
#
#   python tools/fixture_scan.py /tmp/scan && tools/dry_run.sh /tmp/scan        # made-up repos
#   mkdir /tmp/scan && git clone --bare https://github.com/<owner>/<repo> /tmp/scan/<repo>.git && tools/dry_run.sh /tmp/scan
#
# control.py looks up dependency versions on api.osv.dev (read-only); everything else is local.
set -euo pipefail
[ $# -eq 1 ] && [ -d "$1" ] || { echo "usage: tools/dry_run.sh SCAN_DIR" >&2; exit 2; }
scan="$(cd "$1" && pwd)"
here="$(cd "$(dirname "$0")/.." && pwd)"
out="$(mktemp -d)"
tar --exclude=.git --exclude=__pycache__ -C "$here" -cf - . | tar -C "$out" -xf -
cd "$out"
export RUNNER_TEMP="$out/.runner" SPRINT_SCAN="$scan"
mkdir -p "$RUNNER_TEMP"
python index.py refresh "$scan"
python control.py "$scan"
python tools/build_artifact.py "$scan"
echo
echo "---- Needs setup (CONTROL.md) ----"
sed -n '/^### Needs setup/,/^<sub>/p' CONTROL.md
echo
echo "outputs (CONTROL.md, reports/, findings.json, command_center.html) are in: $out"
