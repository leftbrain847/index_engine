"""Print `repo rev path` for every sprint file the UAT and the public site read, and every homepage (showcase)
`file:` page, so refresh.yml can fetch them in full (the nightly clone skips files over 1 MB; a page over that was
reported "not found" and dropped from the homepage). Includes discovered day snapshots (see sprint.py)."""
import sys
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
from sprint import entries  # noqa: E402
sys.path.insert(0, str(ROOT / "tools"))
from assets import asset_files  # noqa: E402  (no third-party imports)

import os  # noqa: E402

index = yaml.safe_load(Path(os.environ.get("INDEX_YML") or ROOT / "index.yml").read_text())   # INDEX_YML: tests
# Showcase `assets:` folders (media copied next to a page) are listed from the clone's tree, so the scan folder is
# needed: the first argument, else $RUNNER_TEMP/scan (where refresh.yml clones). Without either, they're skipped.
scan_dir = Path(sys.argv[1]) if len(sys.argv) > 1 else (Path(os.environ["RUNNER_TEMP"]) / "scan" if os.environ.get("RUNNER_TEMP") else None)
for e in index.get("showcase") or []:
    if isinstance(e, dict) and e.get("file") and e.get("repo") and e["repo"] != "index":
        print(e["repo"], e.get("file_commit") or "HEAD", e["file"])
        if e.get("assets") and scan_dir and (scan_dir / f"{e['repo']}.git").exists():
            for path in asset_files(scan_dir / f"{e['repo']}.git", str(e["assets"])):
                print(e["repo"], "HEAD", path)
for e in entries(index, sys.argv[1] if len(sys.argv) > 1 else None):
    if not e.get("repo") or e["repo"] == "index":
        continue
    if e.get("file"):
        print(e["repo"], e.get("file_commit") or "HEAD", e["file"])
    if e.get("latest_file"):
        print(e["repo"], "HEAD", e["latest_file"])
