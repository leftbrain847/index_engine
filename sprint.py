"""The sprint log: index.yml's hand-written `sprint_log`, plus day snapshots found in app repos.

An app repo ships a sprint day by committing `snapshots/YYYY-MM-DD - Project.html` to its default branch
(the app pipeline's `npm run snapshot` writes it). The nightly refresh finds the file in its clone and treats it as a
`sprint_log` entry, so a new day needs no index.yml edit. A hand-written entry with the same date and project
wins, so you can still add links, pin a commit, or keep a day private with `public: false`.

A day's living version (the current build, the log's `latest_file`) is found the same way: if the repo also has
`live/<Project>.html` on its default branch, carrying the same opt-in tag, it becomes that project's `latest_file`. The
sprint site then links it next to the frozen day build and the homepage tile opens it. A hand-written entry still wins.

A discovered day goes on the public site only if the page opts in with `<meta name="sprint-public" content="yes">`
and, like every day, passes UAT and the secret scan. Every reader of the log (uat.py, control.py,
tools/build_site.py, tools/build_artifact.py, tools/sprint_files.py) goes through `entries()`. Turn discovery off,
or keep every discovered day private, under `config.sprint_discovery`.
"""
from __future__ import annotations

import os
import re
import subprocess
from datetime import date
from pathlib import Path

SNAPSHOT = re.compile(r"^snapshots/(\d{4}-\d{2}-\d{2}) - ([^/]{1,80})\.html$")
LIVE_DIR = "live/"   # live/<Project>.html: the project's living version, paired with its discovered day builds
# The page's own opt-in to the public site (the app pipeline's `npm run snapshot` writes it). Without it a discovered day
# is listed in the Sprint tab but never published, so a private repo's snapshots/ folder can't go public by accident.
COMMENT = re.compile(r"<!--.*?-->", re.S)
GIT_TIMEOUT_S = 60
PUBLIC_TAG = re.compile(r"""<meta\s+name=["']?sprint-public["']?\s+content=["']?yes["']?\s*/?>""", re.I)


def default_scan() -> Path | None:
    """The nightly run's clone directory, for tools that aren't handed one (refresh.yml clones into $RUNNER_TEMP/scan)."""
    for p in (os.environ.get("SPRINT_SCAN"), os.environ.get("RUNNER_TEMP") and f"{os.environ['RUNNER_TEMP']}/scan"):
        if p and Path(p).is_dir():
            return Path(p)
    return None


def discover(scan: Path | None, cfg: dict) -> list[dict]:
    """Day snapshots on each scanned repo's default branch, as sprint_log entries."""
    opts = cfg.get("sprint_discovery") or {}
    if not scan or not scan.is_dir() or opts.get("enabled") is False:
        return []
    skip = set(cfg.get("skip_repos") or []) | {"index"}
    forks = set((scan / "forks.txt").read_text().split()) if (scan / "forks.txt").exists() else set()
    found = []
    for bare in sorted(scan.glob("*.git")):
        repo = bare.name[:-4]
        if repo in skip or repo in forks:
            continue
        r = git(bare, "ls-tree", "-r", "-z", "--name-only", "HEAD", "--", "snapshots/", LIVE_DIR)
        paths = r.stdout.decode(errors="replace").split("\0") if r and r.returncode == 0 else []
        live = {p for p in paths if p.startswith(LIVE_DIR) and p.endswith(".html") and "/" not in p[len(LIVE_DIR):]}
        for path in paths:
            m = SNAPSHOT.match(path)
            if not m or not m[2].strip() or not valid_date(m[1]):
                continue   # misnamed, nameless or impossible dates (2026-02-30) are skipped, never crash the run
            e = {"date": m[1], "project": m[2].strip(), "repo": repo, "file": path,
                 "public": opts.get("public", True) and opted_in(bare, path), "artifact": True, "discovered": True}
            current = f"{LIVE_DIR}{m[2].strip()}.html"
            if current in live and opted_in(bare, current):   # the living version must opt in too
                e["latest_file"] = current
            found.append(e)
    return found


def git(bare: Path, *args: str) -> subprocess.CompletedProcess | None:
    try:
        return subprocess.run(["git", "-C", str(bare), *args], capture_output=True, timeout=GIT_TIMEOUT_S)
    except subprocess.TimeoutExpired:
        return None


def valid_date(s: str) -> bool:
    try:
        date.fromisoformat(s)
        return True
    except ValueError:
        return False


def slug(name: str) -> str:
    """Same rule as tools/build_site.py: the public page's file name, so names that differ only here collide."""
    return re.sub(r"[^a-z0-9]+", "-", name.lower()).strip("-")


def opted_in(bare: Path, path: str) -> bool:
    """True only if the file is readable here and carries the sprint-public tag (unreadable = not public)."""
    r = git(bare, "show", f"HEAD:{path}")
    return bool(r and r.returncode == 0 and PUBLIC_TAG.search(COMMENT.sub("", r.stdout[:16384].decode(errors="replace"))))


def entries(index_cfg: dict, scan: Path | str | None = None) -> list[dict]:
    """Hand-written entries first, then discovered ones that no hand-written entry already covers.

    Names are compared by their site slug, because UAT results and public pages are keyed by name: a hand-written
    entry wins over any discovered day with the same slug, and discovered days that collide with each other (two
    repos snapshotting "Settlers" the same day) get the repo added to their name, so each keeps its own UAT result.
    """
    explicit = list(index_cfg.get("sprint_log") or [])
    taken = {slug(f"{e['date']} - {e['project']}") for e in explicit}
    scan = Path(scan) if scan else default_scan()
    found = [e for e in discover(scan, index_cfg.get("config") or {}) if slug(f"{e['date']} - {e['project']}") not in taken]
    count: dict[str, int] = {}
    for e in found:
        count[slug(f"{e['date']} - {e['project']}")] = count.get(slug(f"{e['date']} - {e['project']}"), 0) + 1
    for e in found:
        if count[slug(f"{e['date']} - {e['project']}")] > 1:
            e["project"] = f"{e['project']} ({e['repo']})"
    return explicit + sorted(found, key=lambda e: (e["date"], e["project"]))


def runs_as_artifact(e: dict) -> bool:
    """A day build made as a Claude artifact (it has a snapshot link, or was discovered) is viewed with the host's viewport."""
    return bool(e.get("snapshot") or e.get("artifact"))
