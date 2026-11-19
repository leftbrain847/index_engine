"""Project index.

  python index.py refresh SCAN_DIR   # read git stats from bare clones in SCAN_DIR/<repo>.git -> stats.yml

index.yml is yours (themes, tags, what, next). A repo can also carry its own in a root project.yml (project_file.py);
index.yml wins field by field. stats.yml is generated; never edit it.
"""
from __future__ import annotations

import json
import subprocess
import sys
from datetime import date
from pathlib import Path

import yaml

import project_file

HERE = Path(__file__).parent
INDEX, STATS = HERE / "index.yml", HERE / "stats.yml"


def git(repo: Path, *args: str) -> str:
    r = subprocess.run(["git", "-C", str(repo), *args], capture_output=True, text=True)
    return r.stdout.strip() if r.returncode == 0 else ""


def repo_stats(bare: Path, authors: list[str]) -> dict:
    total = int(git(bare, "rev-list", "--count", "--all") or 0)
    if not total:
        return {"commits": 0, "total_commits": 0, "branches": 0, "started": None, "updated": None, "last": None}
    who = git(bare, "log", "--all", "--format=%an|%ae").splitlines()
    mine = sum(any(a.lower() in w.lower() for a in authors) for w in who)
    return {
        "commits": mine,                      # yours (incl. Claude-authored), per config.my_authors
        "total_commits": total,               # everyone's, incl. upstream history on forks
        "branches": len(git(bare, "for-each-ref", "--format=%(refname)", "refs/heads").splitlines()),
        "started": git(bare, "log", "--all", "--reverse", "--format=%cs").splitlines()[0],
        "updated": git(bare, "log", "--all", "-1", "--format=%cs"),
        "last": git(bare, "log", "--all", "-1", "--format=%s")[:160],
    }


def refresh(scan_dir: str) -> None:
    """scan_dir holds <repo>.git bare clones, plus forks.txt listing which repos are forks."""
    idx = yaml.safe_load(INDEX.read_text())
    cfg = idx["config"]
    skip = set(cfg["skip_repos"])
    forks_file = Path(scan_dir) / "forks.txt"
    forks = set(forks_file.read_text().split()) if forks_file.exists() else set()
    old = yaml.safe_load(STATS.read_text()) if STATS.exists() else {}
    stats = dict(old.get("repos", {}))
    for bare in sorted(Path(scan_dir).glob("*.git")):
        name = bare.name[:-4]
        if name in skip or (name in forks and not cfg.get("include_forks", True)):
            stats.pop(name, None)
            continue
        stats[name] = {**repo_stats(bare, cfg.get("my_authors", [])), "fork": name in forks}
    STATS.write_text(yaml.safe_dump({"refreshed": date.today().isoformat(), "repos": stats}, sort_keys=False))
    known = {p["repo"] for p in project_file.apply(idx, scan_dir)["projects"]}
    new = sorted(set(stats) - known)
    print(f"refreshed {len(stats)} repos ({len(forks & set(stats))} forks)"
          + (f"; NOT IN INDEX: {', '.join(new)}" if new else ""))


def status_for(p: dict, s: dict, rules: dict, today: date) -> str:
    if p.get("status_lock"):
        return p["status_lock"]
    if not s.get("total_commits", s.get("commits")):
        return "empty"
    age = (today - date.fromisoformat(str(s["updated"]))).days
    return "active" if age <= rules["active"] else "paused" if age <= rules["paused"] else "dormant"


STAT_KEYS = ("commits", "total_commits", "branches", "started", "updated", "last")


NEEDS_SETUP_DEFAULTS = {          # overridden by index.yml → config.needs_setup
    "enabled": True,
    "unsorted_theme": "unsorted",
    "fields": ["theme", "tags", "what", "next", "active"],
    "line_prefix": "update my index:",
    "placeholder": "…",
}


def needs_setup_config(index_cfg: dict) -> dict:
    return {**NEEDS_SETUP_DEFAULTS, **((index_cfg.get("config") or {}).get("needs_setup") or {})}


def field_missing(field: str, p: dict, themes: set[str], unsorted: str) -> bool:
    """active counts as decided once the key is there (true or false); theme must be one of config.themes."""
    if field == "active":
        return "active" not in p
    if field == "theme":
        return p.get("theme") in (None, "", unsorted) or p.get("theme") not in themes
    return not p.get(field)


def needs_setup(index_cfg: dict, found) -> list[dict]:
    """Repos the nightly refresh found that index.yml hasn't sorted: no `projects` entry, or a missing/unsorted theme.

    Each item: repo, listed (has a projects entry), missing (fields from config.needs_setup.fields), line (paste to Claude),
    and note when the repo's project.yml was ignored or partly skipped (index_cfg["project_file_notes"], set by
    project_file.apply; pass index_cfg through it first so project.yml fields count as present).
    """
    ns = needs_setup_config(index_cfg)
    if not ns["enabled"]:
        return []
    cfg = index_cfg.get("config") or {}
    themes = set(cfg.get("themes") or [])
    skip = set(cfg.get("skip_repos") or [])
    projects = {p["repo"]: p for p in index_cfg.get("projects") or []}
    notes = index_cfg.get("project_file_notes") or {}
    out = []
    for name in sorted(set(found) - skip, key=str.lower):
        p = projects.get(name)
        if p is not None and not field_missing("theme", p, themes, ns["unsorted_theme"]) and name not in notes:
            continue
        missing = [f for f in ns["fields"] if field_missing(f, p or {}, themes, ns["unsorted_theme"])]
        line = " ".join([ns["line_prefix"], name, *(f"{f}={ns['placeholder']}" for f in missing)])
        out.append({"repo": name, "listed": p is not None, "missing": missing, "line": line,
                    **({"note": notes[name]} if name in notes else {})})
    return out


def merged(scan: str | None = None) -> tuple[dict, list[dict]]:
    idx = project_file.apply(yaml.safe_load(INDEX.read_text()), scan)
    cfg = idx["config"]
    st = yaml.safe_load(STATS.read_text()) if STATS.exists() else {"repos": {}}
    today = date.today()
    label = cfg.get("fork_label", "FORK")

    def row(p: dict, s: dict) -> dict:
        fork = bool(s.get("fork"))
        return {
            **{k: p.get(k) for k in ("repo", "theme", "what", "next")},
            "title": f"{label} · {p['repo']}" if fork else p["repo"],
            "fork": fork,
            "tags": (p.get("tags") or []) + (["fork"] if fork else []),
            **{k: s.get(k) for k in STAT_KEYS},
            "status": status_for(p, s, cfg["status_rules"], today),
            "url": f"https://github.com/{cfg['owner']}/{p['repo']}",
        }

    rows = [row(p, st["repos"][p["repo"]]) for p in idx["projects"] if p["repo"] in st["repos"]]
    known = {p["repo"] for p in idx["projects"]} | set(cfg["skip_repos"])
    for name, s in st["repos"].items():          # new repos show up before you've sorted them
        if name not in known:
            rows.append(row({"repo": name, "theme": needs_setup_config(idx)["unsorted_theme"], "what": "New — not in index.yml yet"}, s))
    meta = {"refreshed": st.get("refreshed"), "themes": cfg["themes"] + [needs_setup_config(idx)["unsorted_theme"]],
            "ideas": idx.get("ideas") or [], "today": today.isoformat()}
    for r in rows:
        for k in ("started", "updated"):
            r[k] = str(r[k]) if r[k] else None
    for i in meta["ideas"]:
        i["added"] = str(i.get("added", ""))
    return meta, rows


if __name__ == "__main__":
    if len(sys.argv) < 3 or sys.argv[1] != "refresh":
        sys.exit("usage: python index.py refresh SCAN_DIR")
    refresh(sys.argv[2])
