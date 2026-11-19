"""The refresh workflow and tools/split.py: secrets stay out of PR runs, private output stays out of the public log,
and data crosses the split only as split.yml says."""
import re
import subprocess
import sys
from pathlib import Path

import pytest
import yaml

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tools"))
import split  # noqa: E402

WF_DIR = ROOT / ".github" / "workflows"
REFRESH_TEXT = (WF_DIR / "refresh.yml").read_text()
REFRESH = yaml.safe_load(REFRESH_TEXT)
SPLIT = split.load()


def triggers(wf: dict) -> dict:
    return wf.get("on") or wf.get(True) or {}   # PyYAML reads the bare key `on` as True


def test_settings_match_split_yml():
    assert REFRESH["env"]["DATA_REPO"] == SPLIT["data_repo"]
    assert REFRESH["env"]["REFRESH_SUBJECT"] == SPLIT["commit_subject"]
    assert f"{{{{ github.repository_owner }}}}/{SPLIT['data_repo']}" in REFRESH_TEXT


def test_refresh_never_runs_code_from_a_pull_request():
    on = triggers(REFRESH)
    assert set(on) == {"schedule", "workflow_dispatch"}
    for wf in WF_DIR.glob("*.yml"):
        assert "pull_request_target" not in wf.read_text() and "workflow_run" not in wf.read_text(), wf.name


def test_secrets_appear_only_in_the_refresh_workflow():
    for wf in WF_DIR.glob("*.yml"):
        if wf.name != "refresh.yml":
            assert "secrets." not in wf.read_text(), wf.name


def test_every_action_is_pinned_to_a_commit():
    for wf in WF_DIR.glob("*.yml"):
        for ref in re.findall(r"uses:\s*(\S+)", wf.read_text()):
            assert re.fullmatch(r"[\w.-]+/[\w.-]+@[0-9a-f]{40}", ref), f"{wf.name}: {ref}"


def test_gate_runs_on_the_backup_schedule_only():
    crons = [c["cron"] for c in triggers(REFRESH)["schedule"]]
    assert REFRESH["jobs"]["gate"]["if"] == f"github.event.schedule == '{crons[1]}'"


def test_engine_steps_that_see_private_data_go_through_quiet():
    # every call that reads the clones or prints repo names is wrapped, so its output lands in the private log
    for cmd in ("tools/clone_all.sh", "tools/regenerate.sh", "tools/site_check.py"):
        lines = [ln for ln in REFRESH_TEXT.splitlines() if cmd in ln and not ln.strip().startswith("#")]
        assert lines, cmd
        assert all("tools/quiet.sh" in ln for ln in lines), f"{cmd} runs without tools/quiet.sh"
    assert "set -x" not in REFRESH_TEXT


def secret_steps():
    for st in REFRESH["jobs"]["refresh"]["steps"]:
        keys = [k for k, v in (st.get("env") or {}).items() if "secrets." in str(v)]
        if keys:
            yield st, keys


def test_read_token_only_reaches_quiet_steps_and_scrubbing():
    for st, keys in secret_steps():
        if "INDEX_READ_TOKEN" in str(st["env"].get("GH_TOKEN", "")):
            assert "tools/quiet.sh" in st["run"], st.get("name")


ENGINE_CALL = re.compile(r"\bpython\b|tools/(?!quiet\.sh)\S+\.(sh|py)")


def test_engine_never_runs_with_a_token_in_its_environment():
    # A step that holds tokens either strips them for each engine call ("${clean[@]}" = env -u ...) or moves them
    # into shell variables and unsets them first, so the engine, Chromium and third-party packages never see them.
    # The clone step is the exception: its token is what clone_all.sh clones with.
    for st, keys in secret_steps():
        if "GH_TOKEN" in keys:
            continue
        lines = [ln.strip() for ln in st["run"].splitlines() if ln.strip() and not ln.strip().startswith("#")]
        unset_at = next((i for i, ln in enumerate(lines) if ln.split("#")[0].strip().endswith("unset " + " ".join(keys))
                         or re.search(r"\bunset " + " ".join(keys) + r"\b", ln)), None)
        m = re.search(r"clean=\(env((?: -u \w+)+)\)", st["run"])
        for i, ln in enumerate(lines):
            if not ENGINE_CALL.search(ln) or "split.py scrub" in ln or ln.startswith(("clean=", "b64=")):
                continue
            stripped = '"${clean[@]}"' in ln and m and set(re.findall(r"-u (\w+)", m.group(1))) == set(keys)
            unset_before = unset_at is not None and unset_at < i
            assert stripped or unset_before, f"{st.get('name')}: runs with a token: {ln}"


def test_private_checkout_keeps_no_credentials_on_disk():
    for st in REFRESH["jobs"]["refresh"]["steps"]:
        if "checkout" in str(st.get("uses", "")):
            assert st["with"]["persist-credentials"] is False


# ---- tools/split.py ----

def tree(root: Path, files: dict[str, str]) -> Path:
    for p, text in files.items():
        (root / p).parent.mkdir(parents=True, exist_ok=True)
        (root / p).write_text(text)
    return root


def test_copy_replaces_files_and_folders_and_skips_missing(tmp_path):
    src = tree(tmp_path / "data", {"index.yml": "real", "snapshots/a.html": "snap"})
    dst = tree(tmp_path / "engine", {"index.yml": "demo", "findings.json": "demo", "snapshots/old.html": "x"})
    n = split.copy(["index.yml", "findings.json", "snapshots"], src, dst)
    assert n == 2
    assert (dst / "index.yml").read_text() == "real"
    assert (dst / "findings.json").read_text() == "demo"           # not in the data repo: left alone
    assert sorted(p.name for p in (dst / "snapshots").iterdir()) == ["a.html"]   # folder replaced, not merged


def test_copy_ignores_symlinks(tmp_path):
    src = tmp_path / "data"
    src.mkdir()
    (src / "secret.txt").write_text("x")
    (src / "index.yml").symlink_to(src / "secret.txt")
    dst = tmp_path / "engine"
    dst.mkdir()
    assert split.copy(["index.yml"], src, dst) == 0 and not (dst / "index.yml").exists()


def test_scrub_blanks_secret_values(tmp_path):
    log = tmp_path / "run_log.txt"
    fake = "fake-value-for-test-only"
    header = __import__("base64").b64encode(f"x-access-token:{fake}".encode()).decode()
    log.write_text(f"token={fake} and again {fake}; header {header}; short=abc")
    assert split.scrub(log, [fake, "abc", ""]) == 3
    assert log.read_text() == "token=*** and again ***; header ***; short=abc"


def test_unsafe_paths_are_refused(tmp_path):
    (tmp_path / "split.yml").write_text(yaml.safe_dump({**SPLIT, "inputs": ["../outside"]}))
    with pytest.raises(ValueError):
        split.load(tmp_path)


def test_cli_lists_the_outputs(tmp_path):
    tree(tmp_path / "data", {"index.yml": "real"})
    r = subprocess.run([sys.executable, "tools/split.py", "outputs"], cwd=ROOT, capture_output=True, text=True)
    assert r.stdout.split() == SPLIT["outputs"]


def test_cli_errors_print_no_paths(tmp_path, capsys, monkeypatch):
    monkeypatch.setattr(split, "copy", lambda *a: (_ for _ in ()).throw(OSError("reports/private_repo.md")))
    assert split.main(["split.py", "copy-out", str(tmp_path)]) == 1
    err = capsys.readouterr().err
    assert "private_repo" not in err and "OSError" in err
