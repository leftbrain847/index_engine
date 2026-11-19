""""Needs setup": repos the nightly refresh found that index.yml hasn't sorted yet."""
import sys
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
import control as c  # noqa: E402
import index as ix  # noqa: E402

INDEX = yaml.safe_load((ROOT / "index.yml").read_text())


def cfg(projects, **needs):
    return {"config": {"themes": ["games", "data"], "skip_repos": ["index"], "tags": ["3d"],
                       **({"needs_setup": needs} if needs else {})},
            "projects": projects}


SORTED = {"repo": "done", "theme": "games", "tags": ["3d"], "what": "x", "next": "y", "active": True}


def test_new_repo_needs_every_field():
    [it] = ix.needs_setup(cfg([SORTED]), ["done", "brand_new"])
    assert it == {"repo": "brand_new", "listed": False, "missing": ["theme", "tags", "what", "next", "active"],
                  "line": "update my index: brand_new theme=… tags=… what=… next=… active=…"}


def test_sorted_and_skipped_repos_are_not_listed():
    assert ix.needs_setup(cfg([SORTED]), ["done", "index"]) == []


def test_unsorted_or_unknown_theme_lists_only_what_is_missing():
    ps = [{"repo": "a", "theme": "unsorted", "tags": ["3d"], "what": "w", "active": False},
          {"repo": "b", "theme": "gamez", "tags": [], "what": "w", "next": "n", "active": True},
          {"repo": "c", "tags": ["3d"]}]
    got = {it["repo"]: it for it in ix.needs_setup(cfg(ps), ["a", "b", "c"])}
    assert got["a"]["missing"] == ["theme", "next"] and got["a"]["listed"]   # active: false is a decision
    assert got["b"]["missing"] == ["theme", "tags"]
    assert got["c"]["missing"] == ["theme", "what", "next", "active"]
    assert got["a"]["line"] == "update my index: a theme=… next=…"


def test_a_sorted_project_with_blanks_is_not_flagged():
    # only an unsorted theme triggers setup; next: blank on a sorted project is normal
    assert ix.needs_setup(cfg([{"repo": "d", "theme": "data", "next": None}]), ["d"]) == []


def test_everything_is_configurable():
    c_ = cfg([{"repo": "a", "theme": "inbox"}], unsorted_theme="inbox", fields=["theme", "active"],
             line_prefix="file:", placeholder="?")
    assert ix.needs_setup(c_, ["a"])[0]["line"] == "file: a theme=? active=?"
    assert ix.needs_setup(cfg([], enabled=False), ["x"]) == []


def test_control_md_section_is_hidden_when_empty_and_has_paste_lines():
    c_ = cfg([SORTED])
    assert c.setup_block([], c_, lambda n: n) == []
    L = c.setup_block(ix.needs_setup(c_, ["new_one"]), c_, lambda n: f"reports/{n}.md")
    text = "\n".join(L)
    assert L[0] == "### Needs setup · 1"
    assert "[new_one](reports/new_one.md)" in text and "`update my index: new_one theme=…" in text
    assert "Themes: games · data" in text and "Tags: 3d" in text


def test_curated_tags_cover_every_tag_on_two_or_more_projects():
    counts = {}
    for p in INDEX["projects"]:
        for t in set(p.get("tags") or []):
            counts[t] = counts.get(t, 0) + 1
    curated = INDEX["config"]["tags"]
    assert len(curated) == len(set(curated))
    assert {t for t, n in counts.items() if n >= 2} <= set(curated)
