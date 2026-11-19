"""Build a small local scan directory (bare clones, like the nightly run's $RUNNER_TEMP/scan) for a dry run.

    python tools/fixture_scan.py /tmp/scan        # then: tools/dry_run.sh /tmp/scan

Every repo is made up and local; nothing is fetched or pushed. Each one exercises a project.yml case:
  pf_full      full project.yml, no index.yml entry      -> gets a full entry, not in Needs setup
  pf_partial   theme + what only                          -> gets an entry with those two; sorted (it has a theme)
  pf_bad_yaml  not valid YAML                             -> ignored, Needs setup note
  pf_bad_type  active: "yes" (a string)                   -> ignored, Needs setup note
  pf_bad_theme theme not in config.themes                 -> theme skipped, Needs setup note
  pf_too_big   over config.project_file.max_bytes        -> ignored, Needs setup note
  pf_fork      a fork carrying project.yml               -> never read (forks.txt)
  demo_game    has an index.yml entry; its project.yml tries other values -> index.yml wins
"""
from __future__ import annotations

import subprocess
import sys
import tempfile
from pathlib import Path

FULL = """# Read nightly by leftbrain847/index.
theme: games
tags: [Mobile, multiplayer]
what: A made-up game for the dry run
next: Ship the first level
active: true
public: false
extra_key: ignored
"""
REPOS = {
    "pf_full": FULL,
    "pf_partial": "theme: data\nwhat: Half filled in\n",
    "pf_bad_yaml": "theme: [games\n",
    "pf_bad_type": "theme: games\nactive: \"yes\"\n",
    "pf_bad_theme": "theme: gamez\nwhat: Typo in the theme\ntags: [x]\nnext: n\nactive: false\n",
    "pf_too_big": "what: big\n# " + "x" * 5000 + "\n",
    "pf_fork": FULL,
    "demo_game": "theme: data\nwhat: Should lose to index.yml\nactive: false\n",
}


def bare_repo(scan: Path, name: str, files: dict[str, str]) -> None:
    with tempfile.TemporaryDirectory() as tmp:
        work = Path(tmp) / name
        work.mkdir()
        for path, text in files.items():
            (work / path).write_text(text)
        git = ["git", "-C", str(work), "-c", "user.name=fixture", "-c", "user.email=fixture@example.com"]
        subprocess.run(["git", "init", "-q", "-b", "main", str(work)], check=True)
        subprocess.run([*git, "add", "-A"], check=True)
        subprocess.run([*git, "commit", "-qm", "fixture"], check=True)
        subprocess.run(["git", "clone", "-q", "--bare", str(work), str(scan / f"{name}.git")], check=True)


def build(scan: Path) -> Path:
    scan.mkdir(parents=True, exist_ok=True)
    if any(scan.iterdir()):
        sys.exit(f"{scan} is not empty")
    for name, text in REPOS.items():
        bare_repo(scan, name, {"project.yml": text, "README.md": f"# {name}\n"})
    (scan / "forks.txt").write_text("pf_fork\n")
    (scan / "meta.json").write_text("{}")
    return scan


if __name__ == "__main__":
    if len(sys.argv) != 2:
        sys.exit("usage: python tools/fixture_scan.py OUT_DIR")
    print(f"built {build(Path(sys.argv[1]))}")
