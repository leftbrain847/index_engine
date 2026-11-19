"""project.yml: per-repo index metadata, merged under index.yml's `projects` (project_file.py)."""
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "tools"))
import control as c  # noqa: E402
import fixture_scan  # noqa: E402
import index as ix  # noqa: E402
import project_file as pf  # noqa: E402

THEMES = {"games", "data"}


def cfg(projects=(), **project_file):
    return {"config": {"themes": sorted(THEMES), "skip_repos": ["index"], "tags": [],
                       **({"project_file": project_file} if project_file else {})},
            "projects": list(projects)}


# ---------------------------------------------------------------- parse (pure)
def test_parse_keeps_known_keys_and_lowercases_tags():
    got, note = pf.parse(fixture_scan.FULL.encode(), THEMES)
    assert note is None
    assert got == {"theme": "games", "tags": ["mobile", "multiplayer"], "what": "A made-up game for the dry run",
                   "next": "Ship the first level", "active": True, "public": False}


@pytest.mark.parametrize("raw, why", [
    (b"theme: [games\n", "not valid YAML"),
    (b"\xff\xfe", "not valid YAML"),
    (b"- a\n- b\n", "not a key: value mapping"),
    (b"active: 'yes'\n", "active has the wrong type"),
    (b"tags: mobile\n", "tags has the wrong type"),
    (b"tags: [1, 2]\n", "tags has the wrong type"),
    (b"what: [a]\ntheme: games\n", "what has the wrong type"),
    (b"theme: yes\n", "theme has the wrong type"),             # YAML 1.1 bool
    (b"!!python/object/apply:os.system ['echo hi']\n", "not valid YAML"),   # safe_load refuses tags
])
def test_bad_files_are_ignored_whole_with_a_note(raw, why):
    got, note = pf.parse(raw, THEMES)
    assert got == {} and note == f"project.yml ignored: {why}"


def test_unknown_theme_drops_only_the_theme():
    got, note = pf.parse(b"theme: gamez\nwhat: w\n", THEMES)
    assert got == {"what": "w"} and "theme" in note and "gamez" not in note   # notes never echo the file


def test_empty_file_and_blank_values_give_nothing():
    assert pf.parse(b"", THEMES) == ({}, None)
    assert pf.parse(b"# only a comment\nnext:\nwhat: '  '\ntags: []\n", THEMES) == ({}, None)


# ---------------------------------------------------------------- merge
def test_index_yml_wins_and_project_yml_fills_what_it_lacks():
    projects = [{"repo": "a", "theme": "data", "what": "", "next": None, "active": False, "status_lock": "archived"}]
    found = {"a": {"theme": "games", "what": "from file", "next": "n", "active": True, "tags": ["x"]},
             "b": {"theme": "games"}}
    got = pf.merge(projects, found)
    assert got == [{"repo": "a", "theme": "data", "what": "from file", "next": "n", "active": False,
                    "status_lock": "archived", "tags": ["x"]},
                   {"repo": "b", "theme": "games"}]


# ---------------------------------------------------------------- reading the scan (git)
@pytest.fixture(scope="module")
def scan(tmp_path_factory):
    return fixture_scan.build(tmp_path_factory.mktemp("pf") / "scan")


def test_read_all_on_the_fixture_scan(scan):
    found, notes = pf.read_all(cfg(), scan)
    assert set(found) == {"pf_full", "pf_partial", "pf_bad_theme", "demo_game"}      # never the fork
    assert found["pf_full"]["tags"] == ["mobile", "multiplayer"] and "extra_key" not in found["pf_full"]
    assert set(notes) == {"pf_bad_yaml", "pf_bad_type", "pf_bad_theme", "pf_too_big"}
    assert notes["pf_too_big"] == "project.yml ignored: over 4096 bytes"


def test_config_turns_it_off_or_changes_the_name_and_cap(scan):
    assert pf.read_all(cfg(enabled=False), scan) == ({}, {})
    assert pf.read_all(cfg(filename="meta.yml"), scan) == ({}, {})
    found, notes = pf.read_all(cfg(max_bytes=16), scan)
    assert "pf_full" not in found and notes["pf_full"] == "project.yml ignored: over 16 bytes"


def test_skip_repos_are_never_read(scan):
    c_ = cfg()
    c_["config"]["skip_repos"] += ["pf_full"]
    assert "pf_full" not in pf.read_all(c_, scan)[0]


def test_no_scan_dir_means_index_yml_alone(tmp_path, monkeypatch):
    monkeypatch.delenv("SPRINT_SCAN", raising=False)
    monkeypatch.delenv("RUNNER_TEMP", raising=False)
    c_ = cfg([{"repo": "a", "theme": "data"}])
    assert pf.apply(c_) == {**c_, "project_file_notes": {}}
    assert pf.apply(c_, tmp_path / "missing") == {**c_, "project_file_notes": {}}


# ---------------------------------------------------------------- Needs setup
def test_needs_setup_counts_project_yml_fields_and_shows_notes(scan):
    c_ = pf.apply(cfg([{"repo": "demo_game", "theme": "games", "tags": ["football"], "what": "w", "next": "", "active": True}]), scan)
    demo = next(p for p in c_["projects"] if p["repo"] == "demo_game")
    assert demo["theme"] == "games" and demo["active"] is True and demo["what"] == "w"   # index.yml wins
    names = [p.name[:-4] for p in scan.glob("*.git")]
    got = {it["repo"]: it for it in ix.needs_setup(c_, names)}
    assert "pf_full" not in got and "pf_partial" not in got and "demo_game" not in got
    assert got["pf_bad_yaml"]["note"] == "project.yml ignored: not valid YAML" and not got["pf_bad_yaml"]["listed"]
    assert got["pf_bad_theme"]["listed"] and got["pf_bad_theme"]["missing"] == ["theme"]
    assert "note" not in got["pf_fork"]


def test_a_sorted_repo_with_a_broken_file_still_gets_a_note():
    c_ = {**cfg([{"repo": "a", "theme": "data", "tags": ["x"], "what": "w", "next": "n", "active": True}]),
          "project_file_notes": {"a": "project.yml ignored: not valid YAML"}}
    [it] = ix.needs_setup(c_, ["a"])
    assert it["listed"] and it["missing"] == [] and c.setup_label(it) == "check project.yml"
    text = "\n".join(c.setup_block([it], c_, lambda n: n))
    assert "check project.yml" in text and "project.yml ignored: not valid YAML" in text


def test_refresh_does_not_call_project_yml_repos_new(scan, tmp_path, monkeypatch, capsys):
    import yaml
    idx = tmp_path / "index.yml"
    idx.write_text(yaml.safe_dump({**cfg(), "config": {**cfg()["config"], "my_authors": []}}))
    monkeypatch.setattr(ix, "INDEX", idx)
    monkeypatch.setattr(ix, "STATS", tmp_path / "stats.yml")
    ix.refresh(str(scan))
    out = capsys.readouterr().out
    assert "pf_full" not in out and "pf_bad_yaml" in out
