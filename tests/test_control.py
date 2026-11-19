"""Tests for the command center: secret detection, rubric integrity, and grading a real git repo."""
import re
import subprocess
import sys
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
import control as c  # noqa: E402

RUBRIC = yaml.safe_load((ROOT / "rubric.yml").read_text())


def hits(text):
    return [k for k, rx in c.SECRET_PATTERNS.items() if re.search(rx, text)]


def test_real_key_formats_are_caught():
    # fakes are assembled at run time so no key-shaped string sits in the source (secret scanners stay quiet)
    assert "Google API key" in hits('key = "' + "AIza" + "Sy" + "Dx9" * 11 + '"')
    assert "GitHub token" in hits("token: ghp_" + "a" * 36)
    assert "AWS access key" in hits("AWS=" + "AKIA" + "Q" * 16)
    assert "Telegram bot token" in hits("BOT=123456789:AA" + "b" * 33)
    assert "Private key" in hits("-----BEGIN RSA PRIVATE KEY-----")


def test_base64_data_is_not_a_secret():
    # the false positives from the first scan: key-shaped runs inside encoded model/image data
    assert hits("u/qNFz0AxKM7ADpoPJIdt72ALWi88LxzPRAeyb2AciA8gF9VPNAScTwAZ/07AIzaO5" + "x" * 35 + "e+brAkxG8ACQ") == []


def test_placeholders_are_ignored():
    assert c.PLACEHOLDER.search("sk-your-key-here-xxxxxxxxxxxxxxxxxxxxxxxx")


def test_rubric_is_consistent():
    S = RUBRIC["settings"]
    assert set(S["category_order"]) == {"security", "lost-work", "reliability", "reuse"}
    for sev in S["sla_days"]:
        assert sev in c.SEV_RANK
    for tab in S["tabs"]:
        assert tab in c.TABS, f"unknown tab {tab}"
    fmt = dict(repo="r", detail="d", paths="p", count=1, branch="b", default="main")
    for name, chk in RUBRIC["checks"].items():
        assert chk["category"] in S["category_order"], name
        assert chk["severity"] in c.SEV_RANK, name
        assert chk["title"] and chk["why"], name
        extra = dict(package="pk", version="1.0.0", fix="1.0.1", snapshot="s") if name.startswith("vulnerable-") else {}
        chk["prompt"].format(**fmt, **extra)  # every placeholder in a prompt must be one we fill (deps.py fills extra)


def git(repo, *args):
    subprocess.run(["git", "-C", str(repo), *args], check=True, capture_output=True)


def make_repo(tmp_path):
    """A typical sprint repo on day one: one commit on main, the real work on a branch, no tests or CI."""
    work = tmp_path / "work"
    work.mkdir()
    git(work, "init", "-q", "-b", "main")
    git(work, "config", "user.email", "t@t")
    git(work, "config", "user.name", "t")
    (work / "README.md").write_text("# demo\n")
    git(work, "add", "-A")
    git(work, "commit", "-qm", "init")
    git(work, "checkout", "-qb", "claude/feature")
    for i in range(6):
        (work / f"f{i}.js").write_text(f"// {i}\n")
        git(work, "add", "-A")
        git(work, "commit", "-qm", f"work {i}")
    git(work, "checkout", "-q", "main")
    bare = tmp_path / "demo.git"
    subprocess.run(["git", "clone", "-q", "--bare", str(work), str(bare)], check=True)
    return bare


def test_grading_a_hollow_repo(tmp_path):
    repo = c.collect(make_repo(tmp_path), {"visibility": "private", "fork": False})
    assert repo.default == "main" and repo.default_commits == 1
    checks = {f.check for f in c.check_repo(repo, RUBRIC)}
    assert "main-is-hollow" in checks          # stranded work is critical
    assert {"no-tests", "no-ci", "no-gitignore"} <= checks
    assert "secret-in-tree" not in checks


def test_findings_have_stable_codes(tmp_path):
    repo = c.collect(make_repo(tmp_path), {"visibility": "private", "fork": False})
    a = {f.id: f.code for f in c.check_repo(repo, RUBRIC)}
    b = {f.id: f.code for f in c.check_repo(repo, RUBRIC)}
    assert a == b and all(len(code) == 6 for code in a.values())


def test_sparkline():
    assert c.spark([1, 2, 3]) == "▁▅█"
    assert c.trend([{"q": "10"}, {"q": "7"}], "q") .startswith("█▁ -3")


def test_retired_repos_keep_only_security_findings(tmp_path):
    bare = make_repo(tmp_path)
    meta = {"visibility": "public", "fork": False}
    live = c.collect(bare, meta)
    repos = {"demo": live, "old": live,
             "fork": c.collect(bare, {**meta, "fork": True}),
             "gone": c.collect(bare, {**meta, "archived": True})}
    cfg = {"projects": [{"repo": "old", "status_lock": "archived"}, {"repo": "demo"}]}
    assert c.retired_repos(repos, cfg) == {"old", "fork", "gone"}

    f = lambda repo, check: c.Finding(f"{repo}/{check}", "abc123", repo, check, "security", "high", "", "", [], "", "")
    retired = {"old"}
    assert c.keep_finding(f("demo", "no-ci"), repos, retired)                  # live repos keep everything
    assert not c.keep_finding(f("old", "no-ci"), repos, retired)               # retired: hygiene gone
    assert not c.keep_finding(f("old", "no-lockfile"), repos, retired)
    assert c.keep_finding(f("old", "secret-in-tree"), repos, retired)          # secrets always count
    assert c.keep_finding(f("old", "vulnerable-dependency"), repos, retired)   # public: vulnerable libs count
    repos["old"] = c.collect(bare, {"visibility": "private", "fork": False})
    assert not c.keep_finding(f("old", "vulnerable-dependency"), repos, retired)  # private and retired: not reachable


def test_retiring_a_repo_is_not_counted_as_fixing_it(tmp_path, monkeypatch):
    monkeypatch.setattr(c, "HERE", tmp_path)
    tasks = {"decisions": {"aaa111": {"status": "open", "finding": "old/no-ci"},
                           "bbb222": {"status": "open", "finding": "live/no-ci"}}}
    dec = c.save_tasks(tasks, [], None, retired={"old"})
    assert dec["aaa111"]["status"] == "retired" and "resolved_on" not in dec["aaa111"]
    assert dec["bbb222"]["status"] == "resolved"


def test_output_repos_are_retired_and_published_snapshots_always_count(tmp_path):
    repo = c.collect(make_repo(tmp_path), {"visibility": "private", "fork": False})
    repos = {"site": repo, "app": repo}
    assert c.retired_repos(repos, {"config": {"output_repos": ["site"]}, "projects": []}) == {"site"}
    f = c.Finding("app/vulnerable-frozen-snapshot/x", "abc123", "app", "vulnerable-frozen-snapshot",
                  "security", "high", "", "", [], "", "")
    assert c.keep_finding(f, repos, {"app"})     # private source repo, but the snapshot is on the public site


def test_a_retired_finding_reopens_when_it_fires_again(tmp_path, monkeypatch):
    monkeypatch.setattr(c, "HERE", tmp_path)
    f = c.Finding("old/vulnerable-dependency/x", "ccc333", "old", "vulnerable-dependency", "security", "high", "", "", [], "", "")
    tasks = {"decisions": {"ccc333": {"status": "retired", "finding": f.id}}}
    assert c.save_tasks(tasks, [f], None, retired={"old"})["ccc333"]["status"] == "open"
    # a security finding that stops firing in a retired repo was fixed, so it gets credit
    assert c.save_tasks(tasks, [], None, retired={"old"})["ccc333"]["status"] == "resolved"
