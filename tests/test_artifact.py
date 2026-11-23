"""The Command Center artifact: build integrity, data completeness, and a phone-size smoke test."""
import json
import re
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tools"))
import build_artifact as ba  # noqa: E402

TABS = ["now", "queue", "lost", "health", "sprint", "toolbox", "cleanup", "portfolio", "reports", "guide"]


@pytest.fixture(scope="module")
def built():
    subprocess.run([sys.executable, str(ROOT / "tools" / "build_artifact.py")], check=True, capture_output=True)
    html = (ROOT / "command_center.html").read_text()
    m = re.search(r"const D = (\{.*?\});\nconst S = ", html, re.S)
    assert m, "embedded data not found"
    return html, json.loads(m.group(1))


def test_valid_document(built):
    html, _ = built
    assert html.startswith("<!DOCTYPE html>")
    assert "<title>Command Center</title>" in html
    assert ba.PLACEHOLDER not in html
    assert html.count("<script>") == html.count("</script>") == 1   # the data can't break out of its script tag


def test_every_finding_is_in_the_page(built):
    _, data = built
    source = json.loads((ROOT / "findings.json").read_text())
    assert {f["code"] for f in data["findings"]} == {f["code"] for f in source["findings"]}
    assert set(data["repos"]) == set(source["repos"])


def test_config_is_carried_not_hardcoded(built):
    html, data = built
    rubric = ba.load_yaml("rubric.yml")["settings"]
    assert data["settings"]["category_order"] == rubric["category_order"]
    assert data["settings"]["sla_days"] == rubric["sla_days"]
    for tab in TABS:
        assert f'["{tab}",' in html


def test_sprint_log_carries_uat(built):
    _, data = built
    names = {s["name"] for s in data["sprint"]}
    log = ba.load_yaml("index.yml").get("sprint_log") or []
    assert names == {f"{e['date']} - {e['project']}" for e in log}
    for s in data["sprint"]:
        assert s["auto_status"] in {"pass", "warn", "fail", "skipped", "error", "not run"}


def test_needs_setup_is_carried(built):
    import index as ix
    _, data = built
    want = ix.needs_setup(ba.load_yaml("index.yml"), data["repos"])
    assert [(i["repo"], i["line"]) for i in data["needs_setup"]] == [(i["repo"], i["line"]) for i in want]
    assert data["setup_hints"]["tags"] == ba.load_yaml("index.yml")["config"].get("tags", [])


@pytest.mark.skipif(not shutil.which("chromium") and not shutil.which("google-chrome")
                    and not Path.home().joinpath(".cache/ms-playwright").exists(), reason="no browser")
def test_phone_smoke(built):
    _, data = built
    pw = pytest.importorskip("playwright.sync_api")
    with pw.sync_playwright() as p:
        exe = shutil.which("google-chrome")
        browser = p.chromium.launch(**({"executable_path": exe} if exe else {}))
        page = browser.new_page(viewport={"width": 390, "height": 844}, is_mobile=True, has_touch=True)
        errors = []
        page.on("pageerror", lambda e: errors.append(str(e)))
        page.route("https://fonts.**", lambda r: r.fulfill(status=200, body=""))
        page.goto((ROOT / "command_center.html").as_uri())
        for tab in TABS:
            page.click(f"nav button[data-tab={tab}]")
            assert page.inner_text("#view").strip(), f"{tab} is empty"
            assert page.evaluate("document.documentElement.scrollWidth") <= 392, f"{tab} overflows a phone"
        page.click("nav button[data-tab=queue]")
        total = page.inner_text(".count")
        page.fill("#q", "zzz-no-such-repo")
        assert page.inner_text(".count").startswith("0 findings") and total != page.inner_text(".count")
        page.click("nav button[data-tab=now]")
        first_h2 = page.inner_text("#view h2")
        if data["needs_setup"]:   # at the top of Now, one card per repo, each with its paste line
            assert first_h2.lower().startswith("needs setup")
            assert page.locator("[data-setup-line]").count() == len(data["needs_setup"])
            assert page.input_value("[data-setup-line]") == data["needs_setup"][0]["line"]
        else:
            assert "needs setup" not in page.inner_text("#view").lower()
        # no db in a plain browser: every decision and setup-save button renders disabled
        assert page.evaluate("[...document.querySelectorAll('[data-act],[data-setup-send]')].every(b => b.disabled)")
        heights = page.evaluate("[...document.querySelectorAll('nav button, .btn')].filter(b => b.offsetParent).map(b => b.getBoundingClientRect().height)")
        assert min(heights) >= 44, "tap targets under 44px"
        assert errors == []
        browser.close()


def test_pinned_keeps_only_https_links():
    index = {"config": {"pinned": [
        {"title": "Plan", "url": "https://claude.ai/artifact/demo", "note": "the launch plan"},
        {"title": "Script", "url": "javascript:alert(1)"},
        {"title": "Plain", "url": "http://example.com"},
        {"title": "Quote", "url": 'https://example.com/"onmouseover=x'},
        "not a dict",
        {"url": "https://example.com/" + "a" * 600, "title": "T" * 200},
    ]}}
    got = ba.pinned(index)
    assert [p["title"] for p in got] == ["Plan", "T" * 80]
    assert got[0] == {"title": "Plan", "url": "https://claude.ai/artifact/demo", "note": "the launch plan"}
    assert len(got[1]["url"]) == 500
    assert ba.pinned({}) == [] and ba.pinned({"config": {"pinned": None}}) == []


def test_pinned_is_in_the_page_data(built):
    _, data = built
    assert isinstance(data["pinned"], list)
