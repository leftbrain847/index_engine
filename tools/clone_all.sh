#!/usr/bin/env bash
# Bare-clone every repo the owner has into SCAN_DIR, for the refresh. Run it through tools/quiet.sh: it names
# private repos. Needs GH_TOKEN (read access to every repo) and OWNER.
#
#   GH_TOKEN=... OWNER=leftbrain847 tools/clone_all.sh "$RUNNER_TEMP/scan"
set -euo pipefail
scan="$1"
: "${GH_TOKEN:?}" "${OWNER:?}"
auth="Authorization: basic $(printf 'x-access-token:%s' "$GH_TOKEN" | base64 -w0)"
mkdir -p "$scan"
# every repo you own; index.yml (include_forks, skip_repos) decides what's shown
gh api --paginate "user/repos?affiliation=owner&per_page=100" > "$scan/../repos.json"
jq -r '.[].name' "$scan/../repos.json" > "$scan/../repos.txt"
jq -r '.[] | select(.fork) | .name' "$scan/../repos.json" > "$scan/forks.txt"
jq -s 'add | map({key: .name, value: {visibility, fork, archived, description, topics, default_branch}}) | from_entries' \
  "$scan/../repos.json" > "$scan/meta.json"
echo "found $(wc -l < "$scan/../repos.txt") repos, $(wc -l < "$scan/forks.txt") forks"
failed=0
while read -r r; do
  GIT_LFS_SKIP_SMUDGE=1 timeout 300 git -c "http.extraheader=$auth" clone -q --bare --filter=blob:limit=1m \
    "https://github.com/$OWNER/$r" "$scan/$r.git" || { echo "clone failed: $r"; failed=$((failed + 1)); }
done < "$scan/../repos.txt"
# Sprint builds can be over the 1 MB clone filter: fetch just those files in full.
python tools/sprint_files.py | while read -r repo rev path; do
  git -c "http.extraheader=$auth" -C "$scan/$repo.git" cat-file -p "$rev:$path" > /dev/null \
    || echo "could not fetch $repo:$path"
done
# Same for lockfiles over 1 MB (a big package-lock.json), so the dependency check (deps.py) can read them.
python deps.py big-files "$scan" | while read -r repo rev path; do
  git -c "http.extraheader=$auth" -C "$scan/$repo.git" cat-file -p "$rev:$path" > /dev/null \
    || echo "could not fetch $repo:$path"
done
echo "$failed clones failed"
echo "$failed" > "$scan/../clone_failures"   # the workflow prints this count (no names) publicly
