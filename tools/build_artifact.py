"""Build command_center.html: the live Command Center artifact.

  python tools/build_artifact.py            # -> command_center.html

Everything the page shows is gathered here into one JSON blob and embedded in
tools/command_center.template.html. The page itself holds no data and no
settings of its own beyond what this blob carries.
"""
from __future__ import annotations

import csv
import json
import sys
from datetime import date
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from sprint import entries as sprint_entries  # noqa: E402
from index import needs_setup  # noqa: E402
import project_file  # noqa: E402
TEMPLATE = ROOT / "tools" / "command_center.template.html"
OUT = ROOT / "command_center.html"
PLACEHOLDER = "/*__DATA__*/null"


def load_yaml(name: str) -> dict:
    p = ROOT / name
    return (yaml.safe_load(p.read_text()) or {}) if p.exists() else {}


def build_data(scan: str | None = None) -> dict:
    findings = json.loads((ROOT / "findings.json").read_text())
    rubric = load_yaml("rubric.yml")
    index = project_file.apply(load_yaml("index.yml"), scan)   # projects: index.yml entries + each repo's project.yml
    tasks = load_yaml("tasks.yml").get("decisions") or {}
    stats = (load_yaml("stats.yml").get("repos") or {})
    metrics = list(csv.DictReader((ROOT / "metrics.csv").open())) if (ROOT / "metrics.csv").exists() else []
    uat = json.loads((ROOT / "uat_results.json").read_text()) if (ROOT / "uat_results.json").exists() else {"results": []}
    S = rubric.get("settings", {})

    auto = {r["name"]: r for r in uat.get("results", [])}
    sprint = []
    for e in sprint_entries(index, scan):
        name = f"{e['date']} - {e['project']}"
        a = auto.get(name, {})
        sprint.append({"name": name, "date": str(e["date"]), "project": e["project"], "repo": e.get("repo"),
                       "snapshot": e.get("snapshot"), "live": e.get("live"), "file": e.get("file"),
                       "build_path": e.get("build_path"),
                       "manual": (e.get("uat") or {}).get("manual"), "manual_on": str((e.get("uat") or {}).get("date") or ""),
                       "auto_status": a.get("status", "not run"), "auto_issues": a.get("issues", []),
                       "auto_note": a.get("note"), "viewed_as": a.get("viewed_as")})
    sp = S.get("sprint", {})
    start, end = str(sp.get("start", "")), str(sp.get("end", ""))
    started = sorted((str(s["started"]), n) for n, s in stats.items()
                     if s.get("started") and start <= str(s["started"]) <= end and not s.get("fork"))

    return {
        "generated": findings.get("stamp") or findings.get("graded"),
        "graded": findings.get("graded"),
        "owner": index.get("config", {}).get("owner", ""),
        "settings": {k: S.get(k) for k in ("category_order", "rank_by", "severity_weight", "grades", "sla_days",
                                            "always_surface", "sprint", "timezone")},
        "checks": {k: {kk: v.get(kk) for kk in ("category", "severity", "title", "why")} for k, v in rubric.get("checks", {}).items()},
        "repos": findings["repos"],
        "grades": findings["grades"],
        "findings": findings["findings"],
        "decisions": {c: {k: str(v) for k, v in d.items()} for c, d in tasks.items()},
        "metrics": metrics,
        "toolbox": findings.get("toolbox", []),
        "sprint": sprint,
        "started": [{"date": d, "repo": n} for d, n in started],
        "uat_run": uat.get("run"),
        "uat_checks": load_yaml("uat.yml").get("checks", {}),
        # "Needs setup" (top of Now): unsorted repos, what's missing, the line to send Claude. [] hides the section.
        "needs_setup": [{**it, "last": (stats.get(it["repo"]) or {}).get("last"),
                         "started": str((stats.get(it["repo"]) or {}).get("started") or "")}
                        for it in needs_setup(index, findings["repos"])],
        "setup_hints": {"themes": index.get("config", {}).get("themes") or [], "tags": index.get("config", {}).get("tags") or []},
    }


def main() -> int:
    data = build_data(sys.argv[1] if len(sys.argv) > 1 else None)   # the nightly scan dir, for discovered days
    tpl = TEMPLATE.read_text()
    if PLACEHOLDER not in tpl:
        sys.exit("template is missing the data placeholder")
    blob = json.dumps(data, separators=(",", ":"), default=str).replace("</", "<\\/")   # never close the script tag
    OUT.write_text(tpl.replace(PLACEHOLDER, blob))
    print(f"wrote {OUT.name}: {len(data['findings'])} findings, {len(data['repos'])} repos, {len(data['sprint'])} sprint builds")
    return 0


if __name__ == "__main__":
    sys.exit(main())
