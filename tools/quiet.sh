#!/usr/bin/env bash
# Run a command with all of its output kept out of the public Actions log.
#
#   tools/quiet.sh LOG LABEL command [args...]
#
# Everything the command prints (stdout and stderr) is appended to LOG, which the refresh commits to the private
# index repo. The public log gets one line: "LABEL: ok" or "LABEL: failed (exit N), details in the run log".
# The command's exit code is passed through. Workflow commands (::warning:: etc.) inside the output are not
# interpreted, because they never reach the public log.
set -uo pipefail
[ $# -ge 3 ] || { echo "usage: tools/quiet.sh LOG LABEL command [args...]" >&2; exit 2; }
log="$1"; label="$2"; shift 2
mkdir -p "$(dirname "$log")"
{ echo; echo "== $label ($(date -u +%FT%TZ))"; } >> "$log"
"$@" >> "$log" 2>&1
code=$?
if [ "$code" -eq 0 ]; then echo "$label: ok"; else echo "$label: failed (exit $code), details in the run log"; fi
exit "$code"
