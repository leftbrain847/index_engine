"""Control center: grade every repo against rubric.yml, produce findings, queue, and reports.

  python control.py SCAN_DIR      # SCAN_DIR has <repo>.git clones (blobs <=1MB), forks.txt, meta.json

Reads:  rubric.yml (the bar), index.yml (projects, active flags) + each repo's project.yml, tasks.yml (your decisions)
Writes: CONTROL.md, reports/<repo>.md, findings.json, tasks.yml (adds new findings as `open`)
Never writes to any other repo.
"""
from __future__ import annotations

import difflib
import hashlib
import json
import re
import subprocess
import sys
from dataclasses import dataclass, field, asdict
from datetime import date
from pathlib import Path
from urllib.parse import quote

import yaml

from index import needs_setup
import project_file
from sprint import entries as sprint_entries

HERE = Path(__file__).parent
SEV_RANK = {"critical": 4, "high": 3, "medium": 2, "low": 1}
AUTHORS: list[str] = []          # set from index.yml config.my_authors in main()

SECRET_PATTERNS = {  # name -> regex. Values are never stored, only a redacted hint.
    "AWS access key": r"AKIA[0-9A-Z]{16}",
    "GitHub token": r"gh[pousr]_[A-Za-z0-9]{36,}|github_pat_[A-Za-z0-9_]{50,}",
    "Anthropic/OpenAI key": r"sk-(?:ant-|proj-)?[A-Za-z0-9_\-]{32,}",
    "Slack token": r"xox[baprs]-[A-Za-z0-9-]{10,}",
    "Google API key": r"AIza[0-9A-Za-z_\-]{35}",
    "Stripe live key": r"[sr]k_live_[0-9a-zA-Z]{20,}",
    "Telegram bot token": r"\b\d{8,10}:AA[A-Za-z0-9_\-]{33}\b",
    "Private key": r"-----BEGIN (?:RSA |EC |OPENSSH |DSA )?PRIVATE KEY-----",
    "Hardcoded credential": r"(?i)\b(?:api[_-]?key|secret|token|passw(?:or)?d)\b\s*[:=]\s*['\"][A-Za-z0-9_\-/+=]{20,}['\"]",
}
# a real key is delimited (quote, space, =, :); one embedded in base64 data is a coincidence
_B = r"(?<![A-Za-z0-9+/_\-])"
_E = r"(?![A-Za-z0-9+/=_\-])"
SECRET_PATTERNS = {k: (v if k in ("Private key", "Hardcoded credential") else _B + "(?:" + v + ")" + _E)
                   for k, v in SECRET_PATTERNS.items()}
PLACEHOLDER = re.compile(r"(?i)x{6,}|your[_-]|example|placeholder|changeme|<[^>]+>|\.\.\.|dummy|test[_-]?key")
TEXT_EXT = re.compile(r"\.(py|js|ts|tsx|jsx|mjs|cjs|json|ya?ml|toml|env|cfg|ini|sh|md|txt|html|ipynb|r|R|sql|go|rb|java|kt|swift|properties|conf)$|(^|/)\.env[^/]*$|(^|/)Dockerfile$")
SENSITIVE_FILE = re.compile(r"(^|/)(\.env(\.(?!example$|sample$|template$)[^/]+)?|.*\.pem|.*\.p12|.*\.key|id_rsa[^/]*|credentials[^/]*|secrets?\.(json|ya?ml|toml))$")
TEST_FILE = re.compile(r"(^|/)(tests?|__tests__|spec)/|(^|/)test_[^/]+\.py$|_test\.(py|go)$|\.(test|spec)\.(js|ts|tsx|jsx|mjs)$")
CODE_EXT = re.compile(r"\.(py|js|ts|tsx|jsx|mjs|cjs|html|css|sh|go|rb|java|kt|swift|r|R|sql)$")
TODO = re.compile(r"\b(TODO|FIXME|HACK|XXX)\b")


# ---------------------------------------------------------------- git helpers
def git(repo: Path, *args: str, input: str | None = None) -> str:
    r = subprocess.run(["git", "-C", str(repo), *args], capture_output=True, text=True, input=input)
    return r.stdout if r.returncode == 0 else ""


def blobs_batch(repo: Path, shas: list[str]) -> dict[str, str]:
    """Read many blobs at once; skips binaries and missing (filtered-out) objects."""
    present = _present(repo)
    shas = [s for s in shas if s in present]     # never trigger a lazy fetch of filtered-out blobs
    if not shas:
        return {}
    p = subprocess.run(["git", "-C", str(repo), "cat-file", "--batch"], input=("\n".join(shas) + "\n").encode(),
                       capture_output=True)
    out, data, i = {}, p.stdout, 0
    for sha in shas:
        nl = data.find(b"\n", i)
        if nl < 0:
            break
        header = data[i:nl].decode(errors="replace").split()
        if len(header) < 3 or header[1] == "missing":
            i = nl + 1
            continue
        size = int(header[2])
        body = data[nl + 1: nl + 1 + size]
        i = nl + 1 + size + 1
        if b"\0" not in body[:8000]:
            out[sha] = body.decode(errors="replace")
    return out


_PRESENT: dict[str, set[str]] = {}


def _present(repo: Path) -> set[str]:
    if str(repo) not in _PRESENT:
        out = git(repo, "cat-file", "--batch-all-objects", "--batch-check=%(objectname) %(objecttype)")
        _PRESENT[str(repo)] = {l.split()[0] for l in out.splitlines() if l.endswith(" blob")}
    return _PRESENT[str(repo)]


# ---------------------------------------------------------------- data model
@dataclass
class Branch:
    name: str
    ahead: int
    mine_ahead: int
    behind: int
    last: str
    subject: str
    commits: int


@dataclass
class Repo:
    name: str
    meta: dict
    empty: bool = False
    default: str = ""
    root_commit: str = ""
    branches: list[Branch] = field(default_factory=list)
    work_branch: str = ""                       # branch with the most recent commit
    files: list[str] = field(default_factory=list)   # files on work_branch
    text: dict[str, str] = field(default_factory=dict)  # path -> content for key files on work_branch
    default_commits: int = 0
    secrets_tip: list[tuple[str, str]] = field(default_factory=list)      # (pattern, path)
    secrets_history: list[tuple[str, str]] = field(default_factory=list)
    todos: list[str] = field(default_factory=list)
    skill_hashes: dict[str, str] = field(default_factory=dict)            # path -> sha


def collect(bare: Path, meta: dict) -> Repo:
    r = Repo(bare.name[:-4], meta)
    if not git(bare, "rev-list", "-1", "--all").strip():
        r.empty = True
        return r
    r.default = git(bare, "symbolic-ref", "--short", "HEAD").strip()
    if not git(bare, "rev-parse", "-q", "--verify", r.default).strip():   # HEAD points at a missing branch
        r.default = git(bare, "for-each-ref", "--count=1", "--format=%(refname:short)", "refs/heads").strip()
    r.root_commit = git(bare, "rev-list", "--max-parents=0", "--all").split()[0]
    r.default_commits = int(git(bare, "rev-list", "--count", r.default).strip() or 0)
    for line in git(bare, "for-each-ref", "--sort=-committerdate",
                    "--format=%(refname:short)|%(committerdate:short)|%(subject)", "refs/heads").splitlines():
        name, last, subject = line.split("|", 2)
        ab = git(bare, "rev-list", "--left-right", "--count", f"{r.default}...{name}").split()
        behind, ahead = (int(ab[0]), int(ab[1])) if len(ab) == 2 else (0, 0)
        mine = 0
        if ahead:
            who = git(bare, "log", "--format=%an|%ae", f"{r.default}..{name}").splitlines()
            mine = sum(any(a.lower() in w.lower() for a in AUTHORS) for w in who)
        r.branches.append(Branch(name, ahead, mine, behind, last, subject[:120],
                                 int(git(bare, "rev-list", "--count", name).strip() or 0)))
    r.work_branch = r.branches[0].name if r.branches else r.default

    # files + key file contents at the work branch tip
    tree = [l.split("\t", 1) for l in git(bare, "ls-tree", "-r", r.work_branch).splitlines() if "\t" in l]
    tip_paths = {path: info.split()[2] for info, path in tree}
    r.files = sorted(tip_paths)
    key = [p for p in r.files if re.search(r"(?i)(^|/)(readme[^/]*|package\.json|requirements[^/]*\.txt|pyproject\.toml|"
                                           r"\.gitignore|claude\.md|agents\.md)$|^\.github/(workflows/.*|dependabot\.ya?ml)$", p)]
    contents = blobs_batch(bare, [tip_paths[p] for p in key])
    r.text = {p: contents.get(tip_paths[p], "") for p in key}

    # scan every text blob in history for secrets; tip blobs also for TODOs
    objs = [l.split(" ", 1) for l in git(bare, "rev-list", "--all", "--objects").splitlines() if " " in l]
    cand = {sha: path for sha, path in objs if TEXT_EXT.search(path)}
    tip_shas = set(tip_paths.values())
    blobs = blobs_batch(bare, list(cand))
    seen = set()
    for sha, body in blobs.items():
        path = cand[sha]
        for name, rx in SECRET_PATTERNS.items():
            for m in re.finditer(rx, body):
                if PLACEHOLDER.search(m.group(0)) or (name, path) in seen:
                    continue
                seen.add((name, path))
                (r.secrets_tip if sha in tip_shas else r.secrets_history).append((name, path))
                break
        if sha in tip_shas and CODE_EXT.search(path):
            for i, line in enumerate(body.splitlines(), 1):
                if TODO.search(line):
                    r.todos.append(f"{path}:{i}")
        if path.endswith("SKILL.md") and sha in tip_shas:
            r.skill_hashes[path] = sha
    # a secret that appears at tip shouldn't also be reported as history-only
    r.secrets_history = [s for s in r.secrets_history if s not in r.secrets_tip]
    return r


# ---------------------------------------------------------------- checks
@dataclass
class Finding:
    id: str
    code: str
    repo: str
    check: str
    category: str
    severity: str
    title: str
    detail: str
    evidence: list[str]
    why: str
    prompt: str


def make(rubric: dict, repo: str, check: str, subject: str, detail: str, evidence: list[str],
         severity: str | None = None, **ctx) -> Finding | None:
    """severity overrides the rubric's (dependency findings take it from the advisory); ctx fills prompt placeholders."""
    c = rubric["checks"].get(check)
    if not c or c.get("enabled", True) is False:
        return None
    fid = f"{repo}/{check}/{subject}" if subject else f"{repo}/{check}"
    fmt = {"branch": "", "default": "", "repo": repo, "detail": detail,
           "paths": ", ".join(evidence[:5]) or "-", "count": len(evidence), **ctx}
    return Finding(fid, hashlib.sha1(fid.encode()).hexdigest()[:6], repo, check, c["category"], severity or c["severity"],
                   c["title"], detail, evidence, c["why"].strip(), c["prompt"].format(**fmt).strip())


def check_repo(r: Repo, rubric: dict) -> list[Finding]:
    S, out = rubric["settings"], []
    add = lambda *a, **k: out.append(make(rubric, r.name, *a, **k))
    if r.empty:
        add("empty-repo", "", "no commits", [])
        return [f for f in out if f]
    today = date.today()
    files, low = r.files, [f.lower() for f in r.files]
    has = lambda rx: [f for f in files if re.search(rx, f, re.I)]

    # security
    for name, path in r.secrets_tip:
        add("secret-in-tree", f"{name}@{path}", name, [path])
    for name, path in r.secrets_history:
        add("secret-in-history", f"{name}@{path}", name, [path])
    sens = [f for f in files if SENSITIVE_FILE.search(f)]
    if r.meta.get("visibility") == "public" and sens:
        add("public-sensitive", "", "public repo", sens)
    manifests = has(r"(^|/)package\.json$") + has(r"(^|/)requirements[^/]*\.txt$") + has(r"(^|/)pyproject\.toml$")
    manifests = [m for m in manifests if "node_modules/" not in m]
    if manifests:
        unlocked = []
        for m in manifests:
            d = m.rsplit("/", 1)[0] + "/" if "/" in m else ""
            if m.endswith("package.json") and not any(f"{d}{lf}" in files for lf in
                                                      ("package-lock.json", "pnpm-lock.yaml", "yarn.lock", "bun.lockb", "bun.lock")):
                if not any(f.endswith(("package-lock.json", "pnpm-lock.yaml")) for f in files):  # workspace root lock
                    unlocked.append(m)
            if "requirements" in m:
                body = r.text.get(m, "")
                reqs = [l for l in body.splitlines() if l.strip() and not l.startswith(("#", "-"))]
                if reqs and any("==" not in l for l in reqs):
                    unlocked.append(m)
            if m.endswith("pyproject.toml") and not any(f"{d}{lf}" in files for lf in ("uv.lock", "poetry.lock", "pdm.lock")):
                unlocked.append(m)
        if unlocked:
            add("no-lockfile", "", "", unlocked)
        if not has(r"^\.github/dependabot\.ya?ml$") and not any(b.name.startswith("dependabot/") for b in r.branches):
            eco = sorted({"npm" if m.endswith("package.json") else "pip" for m in manifests})
            add("no-dependency-updates", "", " and ".join(eco) + " dependencies", manifests)
    if ".gitignore" not in files:
        add("no-gitignore", "", "", [])

    # lost work
    for b in r.branches:
        if b.name == r.default or b.ahead == 0 or (r.meta.get("fork") and b.mine_ahead == 0):
            continue   # forks: upstream's branches aren't your lost work
        age = (today - date.fromisoformat(b.last)).days
        detail = f"{b.ahead} commits not on `{r.default}`, last touched {b.last} ({b.subject})"
        if b.name.startswith("dependabot/"):
            continue
        if r.default_commits <= 3 and b.ahead >= 5:
            add("main-is-hollow", b.name, f"{b.ahead} commits vs {r.default_commits} on `{r.default}`",
                [b.name], branch=b.name, default=r.default)
        elif age > S["stale_branch_days"]:
            add("unmerged-stale", b.name, detail, [b.name], branch=b.name, default=r.default)
        else:
            add("unmerged-active", b.name, detail, [b.name], branch=b.name, default=r.default)
    bots = [b.name for b in r.branches if b.name.startswith("dependabot/") and b.ahead]
    if bots:
        add("pending-bot-prs", "", ", ".join(f"`{b}`" for b in bots), bots)
    if r.todos:
        add("todo-debt", "", "", r.todos)

    # reliability (judged on the work branch, since that's where the code is)
    ctx = {"branch": r.work_branch, "default": r.default}
    tests = [f for f in files if TEST_FILE.search(f)]
    workflows = {p: t for p, t in r.text.items() if p.startswith(".github/workflows/")}
    if not tests:
        add("no-tests", "", "", [], **ctx)
    if not workflows:
        add("no-ci", "", "", [], **ctx)
    elif tests and not any(re.search(r"(?i)\b(pytest|unittest|vitest|jest|npm (run )?test|pnpm (run )?test|yarn test|go test|cargo test|make test)\b", t)
                           for t in workflows.values()):
        add("ci-skips-tests", "", "", list(workflows), **ctx)
    readme = next((t for p, t in r.text.items() if re.match(r"(?i)readme", p)), "")
    guide = next((t for p, t in r.text.items() if re.match(r"(?i)(claude|agents)\.md$", p)), "")
    if readme and re.search(r"(?i)claude\.md", readme) and "```" in guide:
        readme += "\n```"            # README hands off to CLAUDE.md, which has the commands
    if not readme or not re.search(r"(?im)```|^#+\s*(setup|install|usage|run|running|getting started|quick ?start|commands)", readme):
        add("no-readme-run", "", "missing README" if not readme else "README has no run instructions", [], **ctx)
    if not any(re.search(r"(?i)(^|/)(claude|agents)\.md$", f) for f in files):
        add("no-agent-guide", "", "", [], **ctx)

    # reuse (per-repo part; cross-repo checks happen in cross_checks)
    if r.meta.get("visibility") == "public" and not r.meta.get("fork") and not any(re.match(r"(?i)licen[cs]e", f) for f in files):
        add("public-no-license", "", "", [])
    return [f for f in out if f]


RETIRED_CHECKS = {"secret-in-tree", "secret-in-history", "public-sensitive",   # always on, even for retired repos
                  "vulnerable-frozen-snapshot"}                                   # published pages, whatever the repo's visibility


def retired_repos(repos: dict[str, Repo], index_cfg: dict) -> set[str]:
    """Repos done being worked on: archived on GitHub, forks, `status_lock: archived` in index.yml, or
    config.output_repos (publish targets rebuilt by a workflow, never edited by hand).
    They get security checks only; hygiene (CI, tests, docs, branches) no longer applies."""
    locked = {p["repo"] for p in index_cfg.get("projects", []) if p.get("status_lock") == "archived"}
    locked |= set(index_cfg.get("config", {}).get("output_repos", []))
    return {n for n, r in repos.items() if r.meta.get("archived") or r.meta.get("fork") or n in locked}


def keep_finding(f: Finding, repos: dict[str, Repo], retired: set[str]) -> bool:
    """Retired repos keep security findings; vulnerable libraries only matter where the code is public."""
    if f.repo not in retired:
        return True
    if f.check in RETIRED_CHECKS:
        return True
    if f.check.startswith("vulnerable-"):
        return repos.get(f.repo) is None or repos[f.repo].meta.get("visibility") == "public"
    return False


def cross_checks(repos: dict[str, Repo], rubric: dict) -> list[Finding]:
    out = []
    live = [r for r in repos.values() if not r.empty]
    # same root commit = one repo was copied from another
    by_root: dict[str, list[str]] = {}
    for r in live:
        by_root.setdefault(r.root_commit, []).append(r.name)
    pairs = {tuple(sorted(v)) for v in by_root.values() if len(v) > 1}
    # very similar names
    names = [r.name for r in live if not r.meta.get("fork")]
    for i, a in enumerate(names):
        for b in names[i + 1:]:
            if difflib.SequenceMatcher(None, a.lower(), b.lower()).ratio() >= 0.8:
                pairs.add(tuple(sorted((a, b))))
    groups: dict[str, set[str]] = {}
    for a, b in pairs:
        g = groups.get(a) or groups.get(b) or set()
        g |= {a, b}
        for n in g:
            groups[n] = g
    for g in {frozenset(v) for v in groups.values()}:
        for name in sorted(g):
            others = ", ".join(sorted(g - {name}))
            out.append(make(rubric, name, "duplicate-lineage", "", others, sorted(g)))
    # identical SKILL.md in several repos
    by_hash: dict[str, list[tuple[str, str]]] = {}
    for r in live:
        for path, sha in r.skill_hashes.items():
            by_hash.setdefault(sha, []).append((r.name, path))
    for copies in by_hash.values():
        if len(copies) > 1:
            for name, path in copies[1:]:
                out.append(make(rubric, name, "copied-skill", "", copies[0][0], [path]))
    return [f for f in out if f]


# ---------------------------------------------------------------- scoring + state
def score(findings: list[Finding], rubric: dict) -> tuple[int, str]:
    w = rubric["settings"]["severity_weight"]
    s = max(0, 100 - sum(w[f.severity] for f in findings))
    grade = next((g for g, floor in rubric["settings"]["grades"].items() if s >= floor), "F")
    return s, grade


def load_tasks() -> dict:
    p = HERE / "tasks.yml"
    return (yaml.safe_load(p.read_text()) or {}) if p.exists() else {}


def save_tasks(tasks: dict, findings: list[Finding], first_default: str | None = None, retired: set[str] = frozenset()) -> dict:
    dec = tasks.setdefault("decisions", {}) or {}
    tasks["decisions"] = dec
    live = {f.code for f in findings}
    for f in findings:
        d = dec.setdefault(f.code, {"status": "open", "finding": f.id, "first_seen": date.today().isoformat()})
        d.setdefault("first_seen", first_default or date.today().isoformat())   # backfill for findings older than aging
        if d.get("status") in ("resolved", "retired"):   # it fired again: reopen, keep the history
            d["status"], d["reopened_on"] = "open", date.today().isoformat()
    for code, d in dec.items():           # a finding that no longer fires is resolved
        if code not in live and d.get("status") in ("open", "approved"):
            repo, check = (str(d.get("finding", "")).split("/") + [""])[:2]
            if repo in retired and check not in RETIRED_CHECKS and not check.startswith("vulnerable-"):   # hidden by retiring
                d["status"], d["retired_on"] = "retired", date.today().isoformat()   # repo retired, not fixed: no credit
            else:
                d["status"] = "resolved"
                d["resolved_on"] = date.today().isoformat()
    header = ("# Your decisions. Change `status` to: approved | dismissed | open.\n"
              "# approved -> Claude does it and opens a PR (prompt included). dismissed -> hidden.\n"
              "# resolved is set automatically when a check stops firing; retired when its repo was retired instead.\n")
    (HERE / "tasks.yml").write_text(header + yaml.safe_dump(tasks, sort_keys=False, width=120))
    return dec


# ---------------------------------------------------------------- rendering
def md_escape(s: str) -> str:
    return s.replace("|", "\\|")


CHECK_VERB = {"main-is-hollow": "merge stranded work into main", "unmerged-stale": "merge or close a cold branch",
              "unmerged-active": "merge in-progress work", "no-tests": "add tests", "no-ci": "add CI"}
SEV_ICON = {"critical": "🔴", "high": "🟠", "medium": "🟡", "low": "⚪"}
CAT_SHORT = {"security": "Sec", "lost-work": "Lost", "reliability": "Rel", "reuse": "Reuse"}
SPARK = "▁▂▃▄▅▆▇█"
METRIC_FIELDS = ["date", "open", "critical", "high", "medium", "low", "queue", "in_review", "overdue",
                 "resolved_total", "resolved_7d", "avg_score", "repos"]


def spark(vals: list[float]) -> str:
    vals = [v for v in vals if v is not None]
    if not vals:
        return ""
    lo, hi = min(vals), max(vals)
    return "".join(SPARK[0 if hi == lo else round((v - lo) / (hi - lo) * 7)] for v in vals)


def trend(hist: list[dict], key: str, days: int = 7, better: str = "down") -> str:
    """Sparkline of the last `days` points plus change vs the oldest of them."""
    pts = [float(r[key]) for r in hist[-days:] if r.get(key) not in (None, "")]
    if len(pts) < 2:
        return "—"
    d = pts[-1] - pts[0]
    sign = "+" if d > 0 else ""
    good = (d < 0) if better == "down" else (d > 0)
    mark = "" if d == 0 else (" ✓" if good else " ↑" if better == "down" else " ↓")
    return f"{spark(pts)} {sign}{d:g}{mark}"


def load_metrics() -> list[dict]:
    p = HERE / "metrics.csv"
    if not p.exists():
        return []
    import csv
    return list(csv.DictReader(p.open()))


def save_metrics(rows: list[dict]) -> None:
    import csv
    with (HERE / "metrics.csv").open("w", newline="") as fh:
        w = csv.DictWriter(fh, fieldnames=METRIC_FIELDS)
        w.writeheader()
        w.writerows(sorted(rows, key=lambda r: r["date"]))
CAT_LABEL = {"security": "Security", "lost-work": "Lost work", "reliability": "Reliability", "reuse": "Reuse & sprawl"}
TABS = {  # slug -> (label, file). Order and visibility come from rubric settings.tabs
    "now": ("Now", "CONTROL.md"), "queue": ("Queue", "control/queue.md"), "lost-work": ("Lost work", "control/lost-work.md"),
    "health": ("Health", "control/health.md"), "sprint": ("Sprint", "control/sprint.md"),
    "toolbox": ("Toolbox", "control/toolbox.md"), "cleanup": ("Cleanup", "control/cleanup.md"),
    "portfolio": ("Portfolio", "control/portfolio.md"), "guide": ("Guide", "control/guide.md"),
}


def rel(from_file: str, to_file: str) -> str:
    """Relative link between two repo-root paths."""
    up = "../" * from_file.count("/")
    return up + to_file


def tabbar(current: str, order: list[str], counts: dict[str, int], here: str) -> str:
    parts = []
    for slug in order:
        label, file = TABS[slug]
        n = counts.get(slug)
        text = f"{label} {n}" if n else label
        parts.append(f"**{text}**" if slug == current else f"[{text}]({rel(here, file)})")
    return " · ".join(parts)


def h(s: str) -> str:
    return s.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


def details(summary: str, body: list[str], open_: bool = False) -> list[str]:
    return [f"<details{' open' if open_ else ''}><summary>{summary}</summary>", "", *body, "", "</details>", ""]


def setup_label(it: dict) -> str:
    return "not in index.yml" if not it["listed"] else "unsorted" if "theme" in it["missing"] else "check project.yml"


def setup_block(items: list[dict], index_cfg: dict, link) -> list[str]:
    """CONTROL.md's "Needs setup" section (top of Now). Empty list = nothing to show."""
    if not items:
        return []
    cfg = index_cfg.get("config") or {}
    L = [f"### Needs setup · {len(items)}", "",
         "The nightly refresh found these, but neither index.yml nor the repo's project.yml has sorted them. "
         "Fill in the blanks and send the line to Claude, or commit a project.yml to the repo.", ""]
    for it in items:
        L += [f"- **[{it['repo']}]({link(it['repo'])})** · {setup_label(it)}"
              f" · missing {', '.join(it['missing']) or 'nothing'}  ", f"  `{it['line']}`"]
        if it.get("note"):
            L[-1] += "  "
            L += [f"  {md_escape(it['note'])}"]
    hints = [f"Themes: {' · '.join(cfg['themes'])}"] if cfg.get("themes") else []
    hints += [f"Tags: {' · '.join(cfg['tags'])}"] if cfg.get("tags") else []
    return L + ([""] + [f"<sub>{' — '.join(hints)}</sub>"] if hints else []) + [""]


def render(repos, findings, decisions, rubric, index_cfg) -> None:
    S = rubric["settings"]
    cat_order = S["category_order"]
    order = S.get("tabs") or list(TABS)
    owner = index_cfg["config"]["owner"]
    projects = {p["repo"]: p for p in index_cfg["projects"]}
    active = {n for n, p in projects.items() if p.get("active")}
    by_repo: dict[str, list[Finding]] = {}
    for f in findings:
        by_repo.setdefault(f.repo, []).append(f)
    grades = {n: (0, "—") if repos[n].empty else score(by_repo.get(n, []), rubric) for n in repos}
    if S.get("rank_by", "severity") == "severity":     # severity tier first, your category order within a tier
        rank = lambda f: (-SEV_RANK[f.severity], cat_order.index(f.category), f.repo)
    else:                                               # strict category order, then severity
        rank = lambda f: (cat_order.index(f.category), -SEV_RANK[f.severity], f.repo)
    dec = lambda f: decisions.get(f.code, {})
    status = lambda f: dec(f).get("status", "open")
    surf = S.get("always_surface") or {}
    surfaces = lambda f: f.category in surf and SEV_RANK[f.severity] >= SEV_RANK[surf[f.category]]
    in_queue = lambda f: status(f) == "open" and (f.repo in active or surfaces(f))
    queue = sorted([f for f in findings if in_queue(f)], key=rank)
    review = sorted([f for f in findings if status(f) == "approved"], key=rank)
    lost = sorted([f for f in findings if f.category == "lost-work" and f.check != "todo-debt" and status(f) in ("open", "approved")], key=rank)
    clean = sorted([f for f in findings if f.category == "reuse" and status(f) == "open"], key=rank)
    today = date.today()
    # findings on the public sites' dependency list (index isn't graded, so it has no report page) link to that list
    report = lambda n, here: rel(here, f"reports/{n}.md" if n in repos else "site_deps.yml")
    line = lambda f, here: (f"{SEV_ICON[f.severity]} [{f.repo}]({report(f.repo, here)}) · {f.title}"
                            f"{' — ' + md_escape(f.detail[:80]) if f.detail and f.check not in ('no-tests','no-ci') else ''} · `{f.code}`")

    def groups(fs: list[Finding]) -> list[tuple[str, list[Finding]]]:
        """Same check across repos = one decision. Ordered by the best-ranked finding in each group."""
        g: dict[str, list[Finding]] = {}
        for f in sorted(fs, key=rank):
            g.setdefault(f.check, []).append(f)
        return sorted(g.items(), key=lambda kv: rank(kv[1][0]))

    def group_block(check: str, fs: list[Finding], here: str, open_: bool = False) -> list[str]:
        f0 = fs[0]
        repos_ = sorted({f.repo for f in fs})
        who = repos_[0] if len(repos_) == 1 else f"{len(repos_)} repos"
        due = due_txt([f for f in fs if in_queue(f)]) if any(in_queue(f) for f in fs) else ""
        flag = " · <b>overdue</b>" if any(overdue(f) for f in fs) else ""
        summary = f"{SEV_ICON[f0.severity]} {h(f0.title)} · {h(who)}{flag}"
        body = [f"{f0.why}" + (f"  \n<sub>{due}</sub>" if due else ""), ""]
        body += [f"- [{f.repo}]({report(f.repo, here)})" + (f" — {md_escape(f.detail[:70])}" if f.detail and len(fs) > 1 else "")
                 + f" · `{f.code}`" for f in fs]
        body += ["", (f"`approve {check}` · all {len(fs)}  ·  `approve <code>` · one" if len(fs) > 1
                      else f"`approve {f0.code}`  ·  `dismiss {f0.code}`"), ""]
        body += details("Prompt (per repo)" if len(fs) > 1 else "Prompt", ["```", f0.prompt, "```"])
        return details(summary, body, open_)

    # aging + SLAs
    sla = S.get("sla_days") or {}
    seen = lambda f: date.fromisoformat(str(dec(f).get("first_seen") or today))
    age = lambda f: (today - seen(f)).days
    due_in = lambda f: (sla[f.severity] - age(f)) if f.severity in sla else None
    overdue = lambda f: in_queue(f) and due_in(f) is not None and due_in(f) < 0
    def due_txt(fs: list[Finding]) -> str:
        worst = min((due_in(f) for f in fs if due_in(f) is not None), default=None)
        if worst is None:
            return ""
        oldest = max(age(f) for f in fs)
        return (f"overdue {-worst}d" if worst < 0 else f"due in {worst}d") + f" · open {oldest}d"
    # overdue items float to the top of their severity tier
    base_rank = rank
    rank = lambda f: (base_rank(f)[0], not overdue(f), *base_rank(f)[1:])
    queue.sort(key=rank)

    # metrics history (one row per day; reruns replace today's row)
    resolved = [d for d in decisions.values() if d.get("status") == "resolved"]
    retired = retired_repos(repos, index_cfg)
    graded = [grades[n][0] for n in repos if not repos[n].empty and n not in retired]
    row = {"date": today.isoformat(),
           "open": sum(1 for f in findings if status(f) == "open"),
           **{sev: sum(1 for f in findings if status(f) == "open" and f.severity == sev) for sev in ("critical", "high", "medium", "low")},
           "queue": len(queue), "in_review": len(review), "overdue": sum(1 for f in queue if overdue(f)),
           "resolved_total": len(resolved),
           "resolved_7d": sum(1 for d in resolved if d.get("resolved_on") and (today - date.fromisoformat(str(d["resolved_on"]))).days < 7),
           "avg_score": round(sum(graded) / len(graded), 1) if graded else 0, "repos": len(repos)}
    hist = [r for r in load_metrics() if r["date"] != row["date"]] + [{k: str(v) for k, v in row.items()}]
    save_metrics(hist)
    hist.sort(key=lambda r: r["date"])
    from datetime import datetime
    try:
        from zoneinfo import ZoneInfo
        stamp = datetime.now(ZoneInfo(S.get("timezone", "America/New_York"))).strftime("%a %b %-d · %-I:%M %p %Z")
    except Exception:
        stamp = f"{today:%a %b %-d}"

    # sprint data (shared by Now + Sprint)
    sp = S["sprint"]
    start, end = date.fromisoformat(str(sp["start"])), date.fromisoformat(str(sp["end"]))
    stats = yaml.safe_load((HERE / "stats.yml").read_text())["repos"] if (HERE / "stats.yml").exists() else {}
    ships = []
    for n, s in stats.items():
        st = s.get("started")
        if n in repos and st and start <= date.fromisoformat(str(st)) <= min(end, today):
            fs = {f.check for f in by_repo.get(n, [])}
            ships.append((str(st), n, "no-tests" not in fs, not ({"no-ci", "ci-skips-tests"} & fs), "no-readme-run" not in fs))
    ships.sort()
    met = sum(1 for *_, t, c, d in ships if t and c and d)

    counts = {"queue": len(queue), "lost-work": len(lost), "cleanup": len(clean)}
    status_line = f"<sub>Updated {stamp} · {len(repos)} repos · {len(active)} active · {row['overdue']} overdue</sub>"
    page = lambda slug, title, intro: [tabbar(slug, order, counts, TABS[slug][1]), "", f"# {title}", status_line, "",
                                       *([f"{intro}", ""] if intro else [])]
    write = lambda slug, lines: ((HERE / TABS[slug][1]).parent.mkdir(exist_ok=True), (HERE / TABS[slug][1]).write_text("\n".join(lines) + "\n"))

    # ---------------- Now
    here = TABS["now"][1]
    L = page("now", "Command Center", "")
    L += setup_block(needs_setup(index_cfg, repos), index_cfg, lambda n: report(n, here))
    L += ["| Queue | Critical | High | Overdue | Resolved 7d | Score |", "|:-:|:-:|:-:|:-:|:-:|:-:|",
          f"| **{row['queue']}** | **{row['critical']}** | **{row['high']}** | **{row['overdue']}** | **{row['resolved_7d']}** | **{row['avg_score']:g}** |",
          f"| {trend(hist, 'queue')} | {trend(hist, 'critical')} | {trend(hist, 'high')} | {trend(hist, 'overdue')} | "
          f"{trend(hist, 'resolved_7d', better='up')} | {trend(hist, 'avg_score', better='up')} |", "",
          f"<sub>Queue and overdue count active projects; critical and high count every repo. Bottom row: last 7 days and the change across them (✓ = right direction).</sub>", ""]
    if review:
        L += ["### Waiting on you"]
        L += [f"- [{f.repo} · PR #{dec(f)['pr'].rsplit('/', 1)[-1]}]({dec(f)['pr']}) — {CHECK_VERB.get(f.check, f.title.lower())}" if dec(f).get("pr")
              else f"- {f.repo} — approved, PR not opened yet · `{f.code}`" for f in review]
        L.append("")
    L += ["### Up next"]
    if queue:
        gs = groups(queue)
        for check, fs in gs[:5]:
            L += group_block(check, fs, here)
        if len(gs) > 5:
            L += [f"[All {len(queue)} tasks in {len(gs)} groups →]({rel(here, TABS['queue'][1])})", ""]
    else:
        L += ["_Queue is clear._", ""]
    dist = {}
    for n in repos:
        dist[grades[n][1]] = dist.get(grades[n][1], 0) + 1
    parked = sum(1 for f in findings if status(f) == 'open' and not in_queue(f))
    L += ["### Areas", "",
          "| Area | Status |", "|---|---|",
          f"| [Health]({rel(here, TABS['health'][1])}) | " + " · ".join(f"{g} {dist[g]}" for g in ["A", "B", "C", "D", "F", "—"] if g in dist) + " |",
          f"| [Lost work]({rel(here, TABS['lost-work'][1])}) | {len(lost)} branch{'es' if len(lost) != 1 else ''} off main |",
          f"| [Sprint]({rel(here, TABS['sprint'][1])}) | {met} of {len(ships)} new repos meet the daily-ship bar |",
          f"| [Cleanup]({rel(here, TABS['cleanup'][1])}) | {len(clean)} open |",
          f"| Parked | {parked} findings in inactive projects |", ""]
    write("now", L)

    # ---------------- Queue
    here = TABS["queue"][1]
    L = page("queue", "Queue", ("Severity first, then " if S.get("rank_by", "severity") == "severity" else "Ranked ")
             + " → ".join(CAT_LABEL[c] for c in cat_order) + ". "
             "Inactive projects only surface " + ", ".join(f"{CAT_LABEL[c].lower()} ≥ {v}" for c, v in surf.items()) + ".")
    if review:
        L += ["### In review", ""] + [f"- **[{f.repo} PR]({dec(f)['pr']})** · {f.title} · `{f.code}`" if dec(f).get("pr") else f"- {line(f, here)}" for f in review] + [""]
    first = True
    for c in cat_order:
        fs = [f for f in queue if f.category == c]
        if not fs:
            continue
        body = []
        for check, g in groups(fs):
            body += group_block(check, g, here)
        L += [f"### {CAT_LABEL[c]} · {len(fs)}", "", *body]
        first = False
    write("queue", L)

    # ---------------- Lost work
    here = TABS["lost-work"][1]
    L = page("lost-work", "Lost work", "Work that isn't on the default branch. Decide: merge, cherry-pick, or delete.")
    if not lost:
        L += ["_Nothing stranded._"]
    for f in lost:
        tag = f" · [PR open]({dec(f)['pr']})" if dec(f).get("pr") else (" · approved" if status(f) == "approved" else "")
        tag = " · PR open" if dec(f).get("pr") else (" · approved" if status(f) == "approved" else "")
        L += details(f"{SEV_ICON[f.severity]} {h(f.repo)} · <code>{h((f.evidence or ['-'])[0])}</code>{tag}",
                     [f"{f.detail}", "", f"_{f.why}_", "", (f"[Review the PR]({dec(f)['pr']}) · " if dec(f).get("pr") else f"`approve {f.code}` · ")
                      + f"[report]({report(f.repo, here)})"])
    write("lost-work", L)

    # ---------------- Health
    here = TABS["health"][1]
    L = page("health", "Health", "Score = 100 minus finding weights (" + ", ".join(f"{k} {v}" for k, v in S["severity_weight"].items()) +
             "). One bar for every repo. ● = active.")
    for g in ["F", "D", "C", "B", "A", "—"]:
        names = sorted([n for n in repos if grades[n][1] == g], key=lambda n: (grades[n][0], n))
        if not names:
            continue
        rows = []
        for n in names:
            fs = by_repo.get(n, [])
            mix = " · ".join(f"{CAT_SHORT[c]} {k}" for c in CAT_SHORT if (k := sum(1 for f in fs if f.category == c)))
            rows.append(f"- {'● ' if n in active else ''}[{n}]({report(n, here)}){' _(fork)_' if repos[n].meta.get('fork') else ''}"
                        f" · {grades[n][0]} · {mix or 'clean'}")
        L += details(f"<b>{g}</b> · {len(names)} repo{'s' if len(names) > 1 else ''}", rows, open_=g in ("F", "D"))
    write("health", L)

    # ---------------- Sprint
    here = TABS["sprint"][1]
    L = page("sprint", f"Sprint · {start:%b %-d}–{end:%b %-d}",
             "Bar: new input/transformation/output, runs from a clean clone, passing test. Checked: tests · CI runs them · run docs.")
    log = sorted(index_cfg.get("sprint_log") or [], key=lambda e: str(e["date"]))
    if log:
        months: dict[str, list[dict]] = {}
        for e in log:
            months.setdefault(f"{date.fromisoformat(str(e['date'])):%B %Y}", []).append(e)
        for i, (m, es) in enumerate(reversed(months.items())):
            rows, last = [], None
            for e in es:
                d = date.fromisoformat(str(e["date"]))
                if d != last:
                    rows += ["", f"**{d:%a %b %-d}**"]
                    last = d
                snap = e.get("snapshot")
                name = f"{d.isoformat()} - {e['project']}"
                bp = e.get("build_path")
                title = (f"[{name}]({snap})" if snap and str(snap).startswith("http")
                         else f"[{name}](https://github.com/{owner}/{e['repo']}/blob/HEAD/{quote(e['file'])})" if e.get("discovered")
                         else f"[{name}](https://github.com/{owner}/{e['repo']}/tree/main/{bp}) _(no snapshot)_" if bp
                         else f"{name} _(no snapshot)_")
                extra = [f"[repo](https://github.com/{owner}/{e['repo']})"] if e.get("repo") else []
                if e.get("live"):
                    extra.append(f"[live]({e['live']})")
                rows.append(f"- {title} · " + " · ".join(extra))
            L += details(f"{m} · {len(es)} builds · {len({str(x['date']) for x in es})} days", rows, open_=i == 0)
        L += ["<sub>Title opens the snapshot frozen that day · repo shows where it went · live is the current build · add a day by committing `snapshots/YYYY-MM-DD - Project.html` to an app repo, or edit `index.yml` → `sprint_log`</sub>", ""]
    L += ["### Daily-ship bar", ""]
    if ships:
        days = (min(end, today) - start).days + 1
        L += [f"**{met} of {len(ships)} meet the bar · {len({s[0] for s in ships})} of {days} days shipped a new repo**", ""]
        yn = lambda b: "✅" if b else "❌"
        L += [f"- {'✓' if t and c and d else '–'} **{st[5:]}** [{n}]({report(n, here)}) · tests {yn(t)} · CI {yn(c)} · docs {yn(d)}" for st, n, t, c, d in ships]
        L += ["", "_Builds that continue an existing repo aren't counted yet._"]
    else:
        L += ["_No repos started in the sprint window yet._"]
    write("sprint", L)

    # ---------------- Toolbox
    here = TABS["toolbox"][1]
    L = page("toolbox", "Toolbox", "Reusable pieces. Check here before building something new.")
    kinds: dict[str, list[str]] = {}
    for n, r in sorted(repos.items()):
        for p in r.files:
            if p.endswith("SKILL.md"):
                kinds.setdefault("Skills", []).append(f"- **{p.rsplit('/', 2)[-2] if '/' in p else n}** · [{n}](https://github.com/{owner}/{n}/blob/{r.work_branch}/{p})")
            elif p.startswith(".github/workflows/") and n != "index":
                kinds.setdefault("Workflows", []).append(f"- **{Path(p).stem}** · [{n}](https://github.com/{owner}/{n}/blob/{r.work_branch}/{p})")
    for n, p in sorted(projects.items()):
        t = set(p.get("tags") or []) & {"pipeline", "skill", "library", "platform"}
        if t and n in repos:
            kinds.setdefault("Platforms & pipelines", []).append(f"- **{n}** · {', '.join(sorted(t))} · {p.get('what') or ''}")
    for k, v in kinds.items():
        L += details(f"{k} · {len(v)}", v, open_=True)
    tags: dict[str, list[str]] = {}
    for n, p in projects.items():
        for t in p.get("tags") or []:
            tags.setdefault(t, []).append(n)
    L += details(f"By tag · {len(tags)}", [f"- `{t}` · " + ", ".join(f"[{n}]({report(n, here)})" for n in sorted(v)) for t, v in sorted(tags.items())])
    write("toolbox", L)

    # ---------------- Cleanup
    here = TABS["cleanup"][1]
    L = page("cleanup", "Cleanup", "Duplicates, empties and copies. Most of these are one decision, not a session.")
    for check, g in groups(clean):
        L += group_block(check, g, here)
    if not clean:
        L += ["_Nothing to clean up._"]
    write("cleanup", L)

    # ---------------- Portfolio
    here = TABS["portfolio"][1]
    L = page("portfolio", "Portfolio", "Scaffold. Strongest first; set `portfolio: true` in index.yml to pin. "
             "Ready = no critical/high findings, tests + CI, run docs, live link or demo.")
    cands = [n for n in repos if not repos[n].meta.get("fork") and not repos[n].empty and n not in retired_repos(repos, index_cfg)]
    cands.sort(key=lambda n: (not projects.get(n, {}).get("portfolio"), -grades[n][0]))
    for n in cands[:10]:
        fs = by_repo.get(n, [])
        checks = {f.check for f in fs}
        gaps = [lab for chk, lab in (("no-tests", "tests"), ("no-ci", "CI"), ("no-readme-run", "run docs"), ("no-agent-guide", "CLAUDE.md")) if chk in checks]
        blockers = sum(1 for f in fs if SEV_RANK[f.severity] >= 3)
        pin = "pinned · " if projects.get(n, {}).get("portfolio") else ""
        ready = "●" if not blockers and not gaps[:3] else "○"
        L.append(f"- {ready} {pin}**[{n}]({report(n, here)})** · {grades[n][1]} · " + (f"needs {', '.join(gaps)}" if gaps else "ready") +
                 (f" · {blockers} blocker{'s' if blockers > 1 else ''}" if blockers else ""))
    write("portfolio", L)

    # ---------------- Guide
    here = TABS["guide"][1]
    L = page("guide", "Guide", "How the command center works. Everything here is configured in files you own; nothing is hard-coded.")
    L += ["### The loop", "",
          "1. **Nightly** every repo is cloned read-only and graded against [rubric.yml](../rubric.yml).",
          "2. Projects are **inactive by default**. Set `active: true` in [index.yml](../index.yml) to pull a project's tasks into the queue. "
          "Inactive projects only surface " + ", ".join(f"{CAT_LABEL[c].lower()} ≥ {v}" for c, v in surf.items()) + ".",
          "3. **Decide**: tell Claude `approve <code>`, `approve <check>` (a whole group) or `dismiss <code>` — or edit [tasks.yml](../tasks.yml).",
          "4. Claude fixes it and **opens a PR** with the prompt in the description. You review and merge.",
          "5. When a check stops firing, the next run marks it **resolved**; if it fires again it reopens.", "",
          "### Ranking", "",
          ("Severity first, then category (" if S.get("rank_by", "severity") == "severity" else "Category first (")
          + " → ".join(CAT_LABEL[c] for c in cat_order) + "). Overdue items lead their severity tier.", "",
          "### Service levels", "", "| Severity | Fix within | Weight |", "|---|---|---|"]
    L += [f"| {SEV_ICON[k]} {k} | {sla.get(k, '—')} days | {S['severity_weight'][k]} |" for k in ("critical", "high", "medium", "low")]
    L += ["", "Age counts from the day a finding first appeared (`first_seen` in tasks.yml). Edit `sla_days` in rubric.yml.", "",
          "### Grades", "", "Score = 100 − the weights of open findings. " + " · ".join(f"{g} ≥ {v}" for g, v in S["grades"].items()) + " · F below.", "",
          "### Trends", "", "Each run writes one row per day to [metrics.csv](../metrics.csv): open findings by severity, queue, in review, overdue, "
          "resolved, average score. The Command Center shows the last 7 days.", "",
          "### Files", "", "| File | Who edits | What |", "|---|---|---|",
          "| [rubric.yml](../rubric.yml) | you | checks, severities, SLAs, tabs, ranking |",
          "| [index.yml](../index.yml) | you | projects, `active`, tags, sprint log |",
          "| [tasks.yml](../tasks.yml) | you + bot | one decision per finding |",
          "| [metrics.csv](../metrics.csv) | bot | daily history |",
          "| [findings.json](../findings.json) | bot | everything, machine-readable |", ""]
    write("guide", L)

    # ---------------- per-repo reports
    rep = HERE / "reports"
    rep.mkdir(exist_ok=True)
    for old in rep.glob("*.md"):
        if old.stem not in repos:
            old.unlink()
    for n, r in repos.items():
        here = f"reports/{n}.md"
        fs = sorted(by_repo.get(n, []), key=rank)
        p = projects.get(n, {})
        R = [tabbar("", order, counts, here), "", f"# {n}",
             f"**{grades[n][1]}** · {grades[n][0]} · {'✅ active' if n in active else 'inactive'} · {r.meta.get('visibility', '?')}"
             f"{' · fork' if r.meta.get('fork') else ''} · [GitHub](https://github.com/{owner}/{n})", ""]
        if p.get("what"):
            R += [f"_{p['what']}_", ""]
        if not r.empty:
            R += [f"<sub>Checked `{r.work_branch}` · default `{r.default}` ({r.default_commits} commits) · {len(r.branches)} branches · {len(r.files)} files</sub>", ""]
        if not fs:
            R += ["✅ Meets the bar. Nothing to fix.", ""]
        for c in cat_order:
            group = [f for f in fs if f.category == c]
            if not group:
                continue
            R += [f"### {CAT_LABEL[c]}", ""]
            for f in group:
                st = status(f)
                badge = "" if st == "open" else f" · _{st}_"
                body = []
                if f.detail:
                    body.append(f"**What:** {f.detail}  ")
                if f.evidence:
                    body.append("**Where:** " + ", ".join(f"`{e}`" for e in f.evidence[:8]) + (f" (+{len(f.evidence) - 8} more)" if len(f.evidence) > 8 else "") + "  ")
                body += [f"**Why:** {f.why}", ""]
                if dec(f).get("pr"):
                    body += [f"**PR:** {dec(f)['pr']}", ""]
                if status(f) == "open":
                    body.append(f"<sub>first seen {seen(f)} · {('overdue ' + str(-due_in(f)) + 'd') if (due_in(f) or 0) < 0 else ('due in ' + str(due_in(f)) + 'd') if due_in(f) is not None else ''}</sub>")
                    body.append("")
                body += [f"`approve {f.code}` · or run it yourself:", "", "```", f.prompt, "```"]
                R += details(f"{SEV_ICON[f.severity]} {h(f.title)} · <code>{f.code}</code>{h(badge.replace('_', ''))}", body)
        (rep / f"{n}.md").write_text("\n".join(R) + "\n")

    # ---------------- machine-readable (future GitHub Issues sync)
    toolbox = []
    for n, r in sorted(repos.items()):
        for p in r.files:
            if p.endswith("SKILL.md"):
                toolbox.append({"kind": "skill", "name": p.rsplit("/", 2)[-2] if "/" in p else n, "repo": n,
                                "url": f"https://github.com/{owner}/{n}/blob/{r.work_branch}/{p}"})
            elif p.startswith(".github/workflows/") and n != "index":
                toolbox.append({"kind": "workflow", "name": Path(p).stem, "repo": n,
                                "url": f"https://github.com/{owner}/{n}/blob/{r.work_branch}/{p}"})
    repo_info = {n: {"visibility": r.meta.get("visibility"), "fork": bool(r.meta.get("fork")), "empty": r.empty,
                     "default": r.default, "work_branch": r.work_branch, "default_commits": r.default_commits,
                     "branches": len(r.branches), "files": len(r.files), "active": n in active,
                     "theme": projects.get(n, {}).get("theme"), "tags": projects.get(n, {}).get("tags") or [],
                     "what": projects.get(n, {}).get("what"), "portfolio": bool(projects.get(n, {}).get("portfolio"))}
                 for n, r in repos.items()}
    (HERE / "findings.json").write_text(json.dumps(
        {"graded": today.isoformat(), "stamp": stamp, "repos": repo_info, "toolbox": toolbox,
         "grades": {n: {"score": s, "grade": g} for n, (s, g) in grades.items()},
         "findings": [{**asdict(f), "status": status(f), "active": f.repo in active, "in_queue": in_queue(f), "pr": dec(f).get("pr"),
                       "first_seen": str(seen(f)), "age": age(f), "due_in": due_in(f), "overdue": overdue(f)} for f in sorted(findings, key=rank)]},
        indent=1))


def main(scan_dir: str) -> None:
    scan = Path(scan_dir)
    rubric = yaml.safe_load((HERE / "rubric.yml").read_text())
    import os, time                     # every date (first_seen, resolved_on, metrics) is in your timezone, not the server's
    os.environ["TZ"] = rubric["settings"].get("timezone", "America/New_York")
    time.tzset()
    index_cfg = yaml.safe_load((HERE / "index.yml").read_text())
    index_cfg = {**index_cfg, "sprint_log": sprint_entries(index_cfg, scan)}   # hand-written + discovered day snapshots, for every check below
    index_cfg = project_file.apply(index_cfg, scan)                             # projects: index.yml entries + each repo's project.yml
    meta = json.loads((scan / "meta.json").read_text()) if (scan / "meta.json").exists() else {}
    AUTHORS[:] = index_cfg["config"].get("my_authors", [])
    skip = set(index_cfg["config"]["skip_repos"])
    forks = set((scan / "forks.txt").read_text().split()) if (scan / "forks.txt").exists() else set()
    repos = {}
    for bare in sorted(scan.glob("*.git")):
        n = bare.name[:-4]
        if n in skip or (n in forks and not index_cfg["config"].get("include_forks", True)):
            continue
        repos[n] = collect(bare, {**meta.get(n, {}), "fork": n in forks})
    retired = retired_repos(repos, index_cfg)
    findings = [f for r in repos.values() for f in check_repo(r, rubric)]
    findings += cross_checks({n: r for n, r in repos.items() if n not in retired}, rubric)
    import deps                          # libraries with known vulnerabilities (OSV); read-only
    findings += deps.check(scan, repos, index_cfg, rubric)
    findings = [f for f in findings if keep_finding(f, repos, retired)]
    decisions = save_tasks(load_tasks(), findings, str(rubric["settings"].get("history_start", "")) or None, retired)
    render(repos, findings, decisions, rubric, index_cfg)
    print(f"graded {len(repos)} repos, {len(findings)} findings, "
          f"{sum(1 for f in findings if f.severity == 'critical')} critical")


if __name__ == "__main__":
    main(sys.argv[1] if len(sys.argv) > 1 else "scan")
