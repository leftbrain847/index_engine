"""launch_plan tooling: sections, spoken text and the page build, on made-up demo data."""
import json, subprocess, sys
from pathlib import Path

LP = Path(__file__).resolve().parent.parent / "launch_plan"
sys.path.insert(0, str(LP))
import sections  # noqa: E402

DEMO = {
    "meta": {"title": "Demo plan", "lede": "Made-up content for tests."},
    "rules": [{"t": "Rule one", "b": "<ul><li>Keep it simple.</li><li>Ship $24/mo plans.</li></ul>"}],
    "phases": [{"id": "p", "name": "Phase", "sub": "Sub", "steps": [{"id": "p1", "t": "Do a thing", "m": "5 min", "b": "<p>Use <code>a_b</code> → done.</p>"}]}],
    "repeats": [{"id": "r", "name": "Every week", "tag": "repeat", "sub": "Sub", "items": [{"id": "r1", "t": "Review", "m": "5 min", "b": "<p>Look.</p>"}]}],
    "upkeep": [{"id": "monthly", "name": "Monthly", "items": [{"id": "u1", "t": "Pay", "m": "1 min", "b": "<p>Pay it.</p>"}]}],
}


def test_sections_cover_every_item_with_stable_keys():
    secs = sections.sections(DEMO)
    assert [s["id"] for s in secs] == ["rules", "p", "r", "upkeep"]
    assert [i["key"] for s in secs for i in s["items"]] == ["r0", "p1", "r1", "u1"]
    assert secs[-1]["items"][0]["t"] == "Monthly: Pay"


def test_spoken_text_strips_markup_and_reads_symbols():
    rule = sections.item_text(sections.sections(DEMO)[0]["items"][0])
    assert rule == "Rule one. Keep it simple. Ship $24 a month plans."
    step = sections.item_text(sections.sections(DEMO)[1]["items"][0])
    assert "<" not in step and "_" not in step and "→" not in step


def test_build_injects_data_and_keeps_script_tags_closed(tmp_path):
    plan = dict(DEMO, meta={"title": "T", "lede": "</script><b>x</b>"})
    (tmp_path / "plan.json").write_text(json.dumps(plan))
    subprocess.run([sys.executable, str(LP / "build.py"), "--data", str(tmp_path)], check=True, cwd=LP)
    page = (tmp_path / "index.html").read_text()
    assert "/*PLAN*/" not in page and "/*REC*/" not in page
    assert page.count("</script>") == 1, "data must not close the page's script tag"
    assert '"title": "T"' in page
