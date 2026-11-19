"""The public/private split: tools/quiet.sh keeps private output out of the public Actions log, and split.yml decides
what crosses between this repo and the private data repo."""
import shutil
import subprocess
import sys
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[1]
QUIET = ROOT / "tools" / "quiet.sh"
SPLIT = yaml.safe_load((ROOT / "split.yml").read_text())
FIXTURE_REPOS = ["pf_full", "pf_partial", "pf_bad_yaml", "pf_bad_type", "pf_bad_theme", "pf_too_big", "demo_game"]


def quiet(log: Path, label: str, *cmd: str, cwd: Path = ROOT) -> subprocess.CompletedProcess:
    return subprocess.run(["bash", str(QUIET), str(log), label, *cmd], cwd=cwd, capture_output=True, text=True)


def test_output_goes_to_the_log_and_only_a_status_line_is_printed(tmp_path):
    log = tmp_path / "run_log.txt"
    r = quiet(log, "step", "sh", "-c", 'echo private_repo_a; echo "::warning::clone failed: private_repo_b" >&2')
    assert r.returncode == 0
    assert r.stdout == "step: ok\n" and r.stderr == ""
    text = log.read_text()
    assert "private_repo_a" in text and "private_repo_b" in text and "== step" in text


def test_failure_keeps_its_exit_code_and_still_prints_nothing_private(tmp_path):
    log = tmp_path / "logs" / "run_log.txt"          # the log's folder is created if missing
    r = quiet(log, "clone", "sh", "-c", "echo private_repo_c >&2; exit 7")
    assert r.returncode == 7
    assert "private_repo_c" not in r.stdout + r.stderr
    assert r.stdout.startswith("clone: failed (exit 7)")
    assert "private_repo_c" in log.read_text()


def test_log_appends_across_steps(tmp_path):
    log = tmp_path / "run_log.txt"
    quiet(log, "one", "echo", "first")
    quiet(log, "two", "echo", "second")
    text = log.read_text()
    assert text.index("first") < text.index("second")


def test_engine_run_on_made_up_repos_prints_no_repo_names(tmp_path):
    """The leak test: a real engine step, run the way the refresh runs it, says nothing about the repos it read."""
    work = tmp_path / "work"
    shutil.copytree(ROOT, work, ignore=shutil.ignore_patterns(".git", "__pycache__", "uat", "site_demo"))
    scan = tmp_path / "scan"
    subprocess.run([sys.executable, "tools/fixture_scan.py", str(scan)], cwd=work, check=True, capture_output=True)
    log = tmp_path / "run_log.txt"
    r = quiet(log, "refresh", sys.executable, "index.py", "refresh", str(scan), cwd=work)
    assert r.returncode == 0, log.read_text()
    public = r.stdout + r.stderr
    leaked = [name for name in FIXTURE_REPOS if name in public]
    assert not leaked, f"public log names repos: {leaked}"
    assert any(name in log.read_text() for name in FIXTURE_REPOS), "the private log should keep the details"


def test_split_config_keeps_real_data_over_demo_data():
    inputs, outputs = SPLIT["inputs"], SPLIT["outputs"]
    # the demo copies in this repo are always replaced by the private ones before a run
    for demo in ("index.yml", "findings.json"):
        assert (ROOT / demo).exists() and demo in inputs
    assert SPLIT["run_log"] in outputs
    assert not set(outputs) & {"index.yml", "snapshots", ".github", "README.md", "CLAUDE.md"}   # never overwritten
    assert all(isinstance(p, str) and p and not p.startswith(("/", "..")) for p in inputs + outputs)
    assert SPLIT["data_repo"] and SPLIT["commit_subject"]


def test_outputs_are_ignored_here_so_a_run_can_never_commit_them_to_this_repo():
    ignored = subprocess.run(["git", "check-ignore", "--no-index", *SPLIT["outputs"]], cwd=ROOT,
                             capture_output=True, text=True).stdout.split()
    missing = [p for p in SPLIT["outputs"] if p not in ignored and p != "findings.json"]   # findings.json: demo copy
    assert not missing, f"add to .gitignore: {missing}"


def test_inputs_are_ignored_here_too_except_the_demo_copies():
    ignored = subprocess.run(["git", "check-ignore", "--no-index", *SPLIT["inputs"]], cwd=ROOT,
                             capture_output=True, text=True).stdout.split()
    missing = [p for p in SPLIT["inputs"] if p not in ignored and p not in ("index.yml", "findings.json")]
    assert not missing, f"add to .gitignore: {missing}"
