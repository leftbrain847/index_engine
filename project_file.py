"""Per-repo index metadata: a root `project.yml` in each repo, read nightly from its default branch.

    # Read nightly by leftbrain847/index.
    theme: games          # one of the index's config.themes
    tags: [mobile, multiplayer]
    what: One line on what it is
    next: The next step
    active: false
    public: false         # informational; sprint-day publishing is still decided per snapshot

So starting a project needs no PR here. index.yml still wins: a field on a `projects:` entry beats the same field
in project.yml (as a hand-written sprint_log entry beats a discovered snapshot). project.yml only fills fields the
entry lacks (absent, blank or []); a repo with project.yml and no entry gets a full one. The index's readers of
`projects` (index.py, control.py, tools/build_artifact.py) go through `apply()`. The public homepage
(tools/build_showcase.py) still reads index.yml alone, on purpose: nothing from project.yml reaches a public page.

The file is untrusted input: read with yaml.safe_load, capped at config.project_file.max_bytes, never from a fork or
a skipped repo, and only these keys are kept. Bad YAML or a wrong type ignores the whole file; a theme not in
config.themes drops just the theme. Either way the repo gets a note in "Needs setup" instead of crashing the run.
Notes never echo the file's contents.
"""
from __future__ import annotations

import os
import subprocess
from pathlib import Path

import yaml

from sprint import default_scan

DEFAULTS = {                     # overridden by index.yml → config.project_file
    "enabled": True,
    "filename": "project.yml",
    "max_bytes": 4096,
}
GIT_TIMEOUT_S = 30
# key -> allowed types. Anything else in the file is ignored.
FIELDS = {"theme": (str,), "tags": (list,), "what": (str,), "next": (str,), "active": (bool,), "public": (bool,)}


def config(index_cfg: dict) -> dict:
    return {**DEFAULTS, **((index_cfg.get("config") or {}).get("project_file") or {})}


def lacks(p: dict, key: str) -> bool:
    """An index.yml entry lacks a field when it's absent, null, blank or []. `active: false` is a value."""
    return p.get(key) in (None, "", [])


def git(bare: Path, *args: str) -> subprocess.CompletedProcess | None:
    # never fetch a filtered-out blob from the network, never prompt for credentials
    env = {**os.environ, "GIT_NO_LAZY_FETCH": "1", "GIT_TERMINAL_PROMPT": "0"}
    try:
        return subprocess.run(["git", "-C", str(bare), *args], capture_output=True, timeout=GIT_TIMEOUT_S, env=env)
    except subprocess.TimeoutExpired:
        return None


def read_blob(bare: Path, filename: str, max_bytes: int) -> tuple[bytes | None, str | None]:
    """(content, None), (None, None) when the repo has no such file, or (None, note) when it can't be used."""
    r = git(bare, "ls-tree", "HEAD", "--", filename)          # default branch of a bare clone; no blob read
    line = r.stdout.decode(errors="replace").strip() if r and r.returncode == 0 else ""
    if not line:
        return None, None
    mode_type_sha = line.split("\t", 1)[0].split()
    if len(mode_type_sha) != 3 or mode_type_sha[1] != "blob":
        return None, f"{filename} ignored: not a regular file"
    sha = mode_type_sha[2]
    r = git(bare, "cat-file", "-s", sha)
    if not r or r.returncode != 0:
        return None, f"{filename} ignored: not readable in the clone"
    if int(r.stdout.strip() or 0) > max_bytes:
        return None, f"{filename} ignored: over {max_bytes} bytes"
    r = git(bare, "cat-file", "blob", sha)
    if not r or r.returncode != 0:
        return None, f"{filename} ignored: not readable in the clone"
    return r.stdout[:max_bytes], None


def parse(raw: bytes, themes: set[str], filename: str = "project.yml") -> tuple[dict, str | None]:
    """Validated fields and a note (None when clean). Pure, so tests need no git."""
    try:
        data = yaml.safe_load(raw.decode("utf-8"))
    except (UnicodeDecodeError, yaml.YAMLError):
        return {}, f"{filename} ignored: not valid YAML"
    if data is None:
        return {}, None
    if not isinstance(data, dict):
        return {}, f"{filename} ignored: not a key: value mapping"
    out = {}
    for key, types in FIELDS.items():
        v = data.get(key)
        if v is None:
            continue
        if not isinstance(v, types) or (key == "tags" and not all(isinstance(t, str) for t in v)):
            return {}, f"{filename} ignored: {key} has the wrong type"
        if isinstance(v, str):
            v = v.strip()
        if key == "tags":
            v = list(dict.fromkeys(t.strip().lower() for t in v if t.strip()))
        if v in ("", []):
            continue
        out[key] = v
    if "theme" in out and out["theme"] not in themes:
        del out["theme"]
        return out, f"{filename}: theme is not one of config.themes, so it was skipped"
    return out, None


def read_all(index_cfg: dict, scan: Path | str | None) -> tuple[dict[str, dict], dict[str, str]]:
    """repo -> fields, and repo -> note, from every scanned repo except forks, skip_repos and the index itself."""
    pf = config(index_cfg)
    scan = Path(scan) if scan else default_scan()
    if not pf["enabled"] or not scan or not scan.is_dir():
        return {}, {}
    cfg = index_cfg.get("config") or {}
    themes = set(cfg.get("themes") or [])
    skip = set(cfg.get("skip_repos") or []) | {"index"}
    forks = set((scan / "forks.txt").read_text().split()) if (scan / "forks.txt").exists() else set()
    found, notes = {}, {}
    for bare in sorted(scan.glob("*.git")):
        repo = bare.name[:-4]
        if repo in skip or repo in forks:     # a fork's file is upstream's, not yours
            continue
        raw, note = read_blob(bare, pf["filename"], int(pf["max_bytes"]))
        if raw is not None:
            fields, note = parse(raw, themes, pf["filename"])
            if fields:
                found[repo] = fields
        if note:
            notes[repo] = note
    return found, notes


def merge(projects: list[dict], found: dict[str, dict]) -> list[dict]:
    """index.yml entries (each filled from its project.yml), then repos known only from project.yml, by name."""
    out, seen = [], set()
    for p in projects or []:
        f = found.get(p.get("repo"), {})
        out.append({**p, **{k: v for k, v in f.items() if lacks(p, k)}})
        seen.add(p.get("repo"))
    out += [{"repo": r, **f} for r, f in sorted(found.items(), key=lambda kv: kv[0].lower()) if r not in seen]
    return out


def apply(index_cfg: dict, scan: Path | str | None = None) -> dict:
    """index_cfg with `projects` merged from project.yml files and `project_file_notes` (repo -> note) for
    "Needs setup". The one function every reader of `projects` goes through."""
    found, notes = read_all(index_cfg, scan)
    return {**index_cfg, "projects": merge(index_cfg.get("projects") or [], found), "project_file_notes": notes}
