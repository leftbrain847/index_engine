import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from sprint import discover, entries, runs_as_artifact  # noqa: E402


def bare_repo(scan: Path, name: str, files: dict[str, str]) -> None:
    work = scan.parent / f"work-{name}"
    work.mkdir()
    for path, text in files.items():
        (work / path).parent.mkdir(parents=True, exist_ok=True)
        (work / path).write_text(text)
    git = ["git", "-C", str(work), "-c", "user.name=t", "-c", "user.email=t@example.com"]
    subprocess.run(["git", "init", "-q", str(work)], check=True)
    subprocess.run([*git, "add", "-A"], check=True)
    subprocess.run([*git, "commit", "-qm", "x"], check=True)
    subprocess.run(["git", "clone", "-q", "--bare", str(work), str(scan / f"{name}.git")], check=True)


def make_scan(tmp_path: Path) -> Path:
    scan = tmp_path / "scan"
    scan.mkdir()
    bare_repo(scan, "chess", {"snapshots/2026-11-07 - Chess Clock.html": '<title>x</title><meta name="sprint-public" content="yes">', "README.md": "x"})
    bare_repo(scan, "quiet", {"snapshots/2026-11-07 - Quiet.html": "<title>no opt-in</title>"})
    bare_repo(scan, "notes", {"snapshots/scratch.html": "x", "snapshots/2026-11-07 - Deep/x.html": "x", "2026-11-07 - Root.html": "x"})
    bare_repo(scan, "skipme", {"snapshots/2026-11-07 - Skip.html": "x"})
    bare_repo(scan, "forked", {"snapshots/2026-11-07 - Fork.html": "x"})
    (scan / "forks.txt").write_text("forked\n")
    return scan


def test_discovers_dated_snapshots_on_the_default_branch_only_where_named_right(tmp_path):
    scan = make_scan(tmp_path)
    found = discover(scan, {"skip_repos": ["skipme"]})
    assert [(e["repo"], e["date"], e["project"], e["file"]) for e in found] == [
        ("chess", "2026-11-07", "Chess Clock", "snapshots/2026-11-07 - Chess Clock.html"),
        ("quiet", "2026-11-07", "Quiet", "snapshots/2026-11-07 - Quiet.html")]
    assert found[0]["public"] is True and runs_as_artifact(found[0])
    # No opt-in tag in the page: listed, never published.
    assert found[1]["public"] is False


def test_hand_written_entry_wins_and_discovery_can_be_turned_off_or_kept_private(tmp_path):
    scan = make_scan(tmp_path)
    mine = {"date": "2026-11-07", "project": "Chess Clock", "repo": "chess", "public": False, "snapshot": "https://x"}
    log = entries({"config": {"skip_repos": []}, "sprint_log": [mine]}, scan)
    assert [e for e in log if e["project"] == "Chess Clock"] == [mine]
    assert entries({"config": {"sprint_discovery": {"enabled": False}}, "sprint_log": []}, scan) == []
    private = entries({"config": {"skip_repos": [], "sprint_discovery": {"public": False}}}, scan)
    assert private and all(e["public"] is False for e in private)


def test_no_scan_means_only_hand_written_entries(monkeypatch):
    monkeypatch.delenv("SPRINT_SCAN", raising=False)
    monkeypatch.delenv("RUNNER_TEMP", raising=False)
    assert entries({"sprint_log": [{"date": "2026-11-01", "project": "A"}]}) == [{"date": "2026-11-01", "project": "A"}]


def test_bad_names_and_impossible_dates_are_skipped_not_fatal(tmp_path):
    scan = tmp_path / "scan"
    scan.mkdir()
    bare_repo(scan, "odd", {"snapshots/2026-02-30 - Bad Date.html": "x", "snapshots/2026-11-07 -  .html": "x",
                            "snapshots/2026-11-07 - Café.html": '<meta name="sprint-public" content="yes">'})
    found = discover(scan, {})
    assert [(e["date"], e["project"], e["public"]) for e in found] == [("2026-11-07", "Café", True)]


def test_names_compare_by_slug_and_colliding_discoveries_keep_separate_names(tmp_path):
    scan = tmp_path / "scan"
    scan.mkdir()
    tag = '<meta name="sprint-public" content="yes">'
    bare_repo(scan, "a", {"snapshots/2026-11-07 - Same.html": tag, "snapshots/2026-11-07 - chess-clock.html": tag})
    bare_repo(scan, "b", {"snapshots/2026-11-07 - Same.html": "no tag"})
    mine = {"date": "2026-11-07", "project": "Chess Clock", "repo": "a", "public": False}
    log = entries({"config": {}, "sprint_log": [mine]}, scan)
    assert [e for e in log if "hess" in e["project"]] == [mine]            # hand-written wins over chess-clock
    same = {e["project"]: e["public"] for e in log if e["project"].startswith("Same")}
    assert same == {"Same (a)": True, "Same (b)": False}                   # each keeps its own name, UAT and slug


def test_opt_in_inside_a_comment_does_not_count(tmp_path):
    scan = tmp_path / "scan"
    scan.mkdir()
    bare_repo(scan, "c", {"snapshots/2026-11-07 - Hidden.html": '<!-- <meta name="sprint-public" content="yes"> -->'})
    assert discover(scan, {})[0]["public"] is False


def test_a_living_version_in_live_pairs_with_the_projects_day_builds(tmp_path):
    scan = tmp_path / "scan"
    scan.mkdir()
    tag = '<meta name="sprint-public" content="yes">'
    bare_repo(scan, "kart", {"snapshots/2026-11-21 - Kart.html": f"<title>day</title>{tag}", "live/Kart.html": f"<title>Kart</title>{tag}"})
    bare_repo(scan, "pong", {"snapshots/2026-11-02 - Pong.html": f"<title>day</title>{tag}"})                      # no living version
    bare_repo(scan, "shy", {"snapshots/2026-11-03 - Shy.html": f"<title>day</title>{tag}", "live/Shy.html": "<title>no opt-in</title>"})
    bare_repo(scan, "other", {"snapshots/2026-11-04 - Other.html": f"<title>day</title>{tag}", "live/Different.html": tag, "live/x/Other.html": tag})
    by = {e["project"]: e for e in discover(scan, {})}
    assert by["Kart"]["latest_file"] == "live/Kart.html"
    assert "latest_file" not in by["Pong"]
    assert "latest_file" not in by["Shy"]      # a living version that doesn't opt in is never linked
    assert "latest_file" not in by["Other"]    # only live/<same project name>.html, never another name or a subfolder
    assert len(by) == 4                        # live/ files are never days themselves


def test_a_hand_written_entry_still_wins_over_a_discovered_living_version(tmp_path):
    scan = tmp_path / "scan"
    scan.mkdir()
    tag = '<meta name="sprint-public" content="yes">'
    bare_repo(scan, "kart", {"snapshots/2026-11-21 - Kart.html": tag, "live/Kart.html": tag})
    mine = {"date": "2026-11-21", "project": "Kart", "repo": "kart", "latest_url": "https://example.com/kart"}
    assert [e for e in entries({"config": {}, "sprint_log": [mine]}, scan) if e["project"] == "Kart"] == [mine]
