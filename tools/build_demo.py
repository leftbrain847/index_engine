"""Build the public demo of the Command Center: the real page on made-up data.

    python tools/build_demo.py OUT.html

Nothing here comes from your repos. Repo names, findings, grades, history and decisions are invented
(seeded, so the demo is the same every night). Only the rubric's check definitions and settings are
real, because they describe the product, not you. Links go nowhere: the page runs in demo mode, where
taps save in the open tab only and nothing is sent anywhere.
"""
from __future__ import annotations

import hashlib
import json
import random
import sys
from datetime import date, timedelta
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[1]
TEMPLATE = ROOT / "tools" / "command_center.template.html"
PLACEHOLDER = "/*__DATA__*/null"

DEMO = {   # every knob of the demo in one place
    "seed": 11,
    "today": date(2026, 11, 4),
    "history_days": 14,
    "repos": {   # name: (theme, active, tags, what, findings to plant)
        "lantern_maze": ("games", True, ["maze", "canvas"], "Lantern-lit maze you solve by sound", ["no-tests", "unmerged-active", "todo-debt"]),
        "tide_tracker": ("data", True, ["noaa", "charts"], "Tide charts for a few favorite beaches", ["no-ci", "no-lockfile"]),
        "bread_ratio": ("personal", True, ["baking", "calculator"], "Baker's percentage calculator", ["no-readme-run"]),
        "chess_clock": ("tools", True, ["timer", "pwa"], "Chess clock with increment presets", []),
        "star_map": ("learning", False, ["astronomy", "three.js"], "Tonight's sky, rotated to where you stand", ["no-ci", "no-tests", "unmerged-stale"]),
        "budget_bot": ("data", True, ["finance", "sqlite"], "Monthly budget roll-up from CSV exports", ["secret-in-history", "no-ci"]),
        "haiku_lab": ("content", False, ["poetry", "syllables"], "Syllable counter and haiku drafts", ["no-gitignore", "no-agent-guide"]),
        "pixel_garden": ("games", True, ["idle", "pixel-art"], "Idle garden that grows while you're away", ["no-tests", "no-dependency-updates", "pending-bot-prs"]),
        "metro_eta": ("tools", False, ["transit", "api"], "Next-train board for one station", ["unmerged-stale", "no-readme-run", "no-ci"]),
        "recipe_scaler": ("personal", True, ["cooking"], "Scale a recipe to any number of servings", ["no-lockfile"]),
        "focus_timer": ("tools", True, ["pomodoro"], "Pomodoro timer with a daily streak", ["ci-skips-tests"]),
        "card_vault": ("personal", False, ["collecting", "catalog"], "Catalog for a card collection", ["empty-repo"]),
    },
    "sprint": [   # date offset from the 1st, project, repo
        (0, "Lantern Maze", "lantern_maze"), (0, "Tide Tracker", "tide_tracker"), (1, "Bread Ratio", "bread_ratio"),
        (2, "Chess Clock", "chess_clock"), (3, "Pixel Garden", "pixel_garden"),
    ],
    "decided": {"no-lockfile": "approve", "no-readme-run": "resolved", "todo-debt": "snooze"},
    "details": {   # check -> (detail, evidence) with {repo} filled in
        "no-tests": ("No test files or test command found", []),
        "no-ci": ("No workflow runs on push or pull request", []),
        "no-lockfile": ("package.json without a lockfile", ["package.json"]),
        "no-readme-run": ("README has no way to run it", ["README.md"]),
        "unmerged-active": ("3 commits not on `main`, last touched yesterday (Add hint system)", ["claude/hint-system"]),
        "unmerged-stale": ("5 commits not on `main`, last touched 2026-09-12 (Offline mode)", ["claude/offline-mode"]),
        "todo-debt": ("14 TODOs across 6 files", ["src/game.js"]),
        "secret-in-history": ("A key-like value was committed and later deleted (value never stored here)", [".env (deleted)"]),
        "no-gitignore": ("No .gitignore", []),
        "no-agent-guide": ("No CLAUDE.md or AGENTS.md for agent sessions", []),
        "no-dependency-updates": ("No Dependabot or Renovate config", []),
        "pending-bot-prs": ("2 Dependabot PRs open over 14 days", ["#4", "#6"]),
        "ci-skips-tests": ("CI runs build but not tests", [".github/workflows/ci.yml"]),
        "empty-repo": ("Repository has no commits", []),
    },
}


def build_data() -> dict:
    rnd = random.Random(DEMO["seed"])
    rubric = yaml.safe_load((ROOT / "rubric.yml").read_text())
    S, checks = rubric["settings"], rubric["checks"]
    today = DEMO["today"]
    repos, grades, findings, decisions = {}, {}, [], {}
    for name, (theme, active, tags, what, planted) in DEMO["repos"].items():
        repos[name] = {"visibility": "private", "fork": False, "empty": "empty-repo" in planted, "default": "main",
                       "work_branch": "main", "default_commits": rnd.randint(4, 80), "branches": rnd.randint(1, 5),
                       "files": rnd.randint(3, 60), "active": active, "theme": theme, "tags": tags, "what": what,
                       "portfolio": False}
        for check in planted:
            c = checks.get(check)
            if not c:
                continue
            code = hashlib.sha1(f"{name}/{check}".encode()).hexdigest()[:6]
            age = rnd.randint(0, 12)
            sla = S["sla_days"][c["severity"]]
            detail, evidence = DEMO["details"].get(check, (c["title"], []))
            f = {"id": f"{name}/{check}", "code": code, "repo": name, "check": check, "category": c["category"],
                 "severity": c["severity"], "title": c["title"], "detail": detail, "evidence": evidence,
                 "why": c.get("why", ""), "prompt": f"In {name}: {c['title'].lower()}. {detail}. Fix it in one small PR.",
                 "active": active, "first_seen": str(today - timedelta(days=age)), "age": age, "due_in": sla - age,
                 "overdue": age > sla, "pr": None, "status": "open"}
            f["in_queue"] = active or c["severity"] in ("critical", "high")
            how = DEMO["decided"].get(check)
            if how == "resolved":
                f["status"] = "resolved"
                decisions[code] = {"status": "resolved", "finding": f["id"], "first_seen": f["first_seen"], "resolved_on": str(today)}
            elif how:
                decisions[code] = {"status": "approved" if how == "approve" else "snoozed", "finding": f["id"], "first_seen": f["first_seen"]}
            findings.append(f)
        w = sum(S["severity_weight"][f["severity"]] for f in findings if f["repo"] == name and f["status"] == "open")
        score = max(0, 100 - w)
        grades[name] = {"score": score, "grade": "—" if repos[name]["empty"] else
                        next((g for g, floor in S["grades"].items() if score >= floor), "F")}

    open_f = [f for f in findings if f["status"] == "open"]
    metrics = []
    for i in range(DEMO["history_days"], -1, -1):   # a steady burn-down to today's real-looking numbers
        d = today - timedelta(days=i)
        extra = i
        sev = {k: sum(1 for f in open_f if f["severity"] == k) for k in ("critical", "high", "medium", "low")}
        sev["medium"] += extra // 2
        sev["low"] += extra - extra // 2
        metrics.append({"date": str(d), "open": str(sum(sev.values())), **{k: str(v) for k, v in sev.items()},
                        "queue": str(sum(1 for f in open_f if f["in_queue"]) + extra // 3), "in_review": "0",
                        "overdue": str(sum(1 for f in open_f if f["overdue"])), "resolved_total": str(DEMO["history_days"] - i + 3),
                        "resolved_7d": str(min(7, DEMO["history_days"] - i + 1)),
                        "avg_score": f"{sum(g['score'] for g in grades.values()) / len(grades) - extra * 0.4:.1f}",
                        "repos": str(len(repos))})
    start = date(today.year, today.month, 1)
    sprint = [{"name": f"{start + timedelta(days=o)} - {p}", "date": str(start + timedelta(days=o)), "project": p, "repo": r,
               "snapshot": "#demo", "live": "#demo", "file": f"{p}.html", "build_path": None, "manual": "passed",
               "manual_on": str(today), "auto_status": "pass", "auto_issues": [], "auto_note": None, "viewed_as": "artifact"}
              for o, p, r in DEMO["sprint"]]
    stamp = today.strftime("%a %b %-d") + " · 1:47 AM EST"
    return {
        "demo": True,
        "generated": stamp, "graded": str(today), "owner": "demo",
        "settings": {k: S.get(k) for k in ("category_order", "rank_by", "severity_weight", "grades", "sla_days",
                                            "always_surface", "sprint", "timezone")},
        "checks": {k: {kk: v.get(kk) for kk in ("category", "severity", "title", "why")} for k, v in checks.items()},
        "repos": repos, "grades": grades, "findings": findings, "decisions": decisions, "metrics": metrics,
        "toolbox": [{"kind": "workflow", "name": "ci", "repo": "chess_clock", "url": "#demo"},
                    {"kind": "skill", "name": "release-notes", "repo": "pixel_garden", "url": "#demo"}],
        "sprint": sprint,
        "started": [{"date": s["date"], "repo": s["repo"]} for s in sprint],
        "uat_run": str(today),
        "uat_checks": (yaml.safe_load((ROOT / "uat.yml").read_text()) or {}).get("checks", {}),
    }


def main(argv: list[str]) -> int:
    out = Path(argv[0]) if argv else ROOT / "demo_command_center.html"
    tpl = TEMPLATE.read_text()
    if PLACEHOLDER not in tpl:
        sys.exit("template is missing the data placeholder")
    blob = json.dumps(build_data(), separators=(",", ":"), default=str).replace("</", "<\\/")
    out.write_text(tpl.replace(PLACEHOLDER, blob))
    print(f"wrote {out}")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
