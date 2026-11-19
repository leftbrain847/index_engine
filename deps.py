"""Dependency security check: every library each repo uses, looked up in OSV (osv.dev). Read-only.

Called by control.py (nightly). Sources, per repo, on its default branch (what ships):
  npm   package-lock.json / npm-shrinkwrap.json, yarn.lock, pnpm-lock.yaml
  PyPI  requirements*.txt (== pins), pyproject.toml ([project] == pins), poetry.lock, uv.lock, Pipfile.lock
  CDN   pinned links in .html/.js/.mjs (jsdelivr, unpkg, esm.sh, skypack, cdnjs)
plus the public sites' dependency list (site_deps.yml `osv:`) and frozen day snapshots (sprint_log day builds pinned
with file_commit, kept in a snapshots/ folder, or separate from latest_file): those can never change, so they are
flagged separately and never reported as editable files.

Ranges that aren't pinned (package.json without a lock, `requests>=2`) can't be looked up; no-lockfile covers them.
If OSV can't be asked about a package tonight, last night's findings for it are kept (never silently resolved).

    python deps.py big-files SCAN_DIR   # `repo rev path` for lockfiles over the clone's 1 MB filter (refresh.yml)
"""
from __future__ import annotations

import json
import math
import re
import sys
import tomllib
import urllib.request
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from pathlib import Path

import yaml

HERE = Path(__file__).parent
OSV = "https://api.osv.dev/v1"
DEP_CHECKS = ("vulnerable-dependency", "vulnerable-frozen-snapshot")
SEV_RANK = {"critical": 4, "high": 3, "medium": 2, "low": 1}
ECOSYSTEMS = {"npm", "PyPI"}

LOCKFILE = re.compile(r"(^|/)(package-lock\.json|npm-shrinkwrap\.json|yarn\.lock|pnpm-lock\.yaml|requirements[^/]*\.txt|"
                      r"pyproject\.toml|poetry\.lock|uv\.lock|Pipfile\.lock)$")
WEB = re.compile(r"\.(html?|m?js)$")
SKIP_DIR = re.compile(r"(^|/)(node_modules|\.venv|venv|site-packages|vendor|dist|build)/")
EXACT = r"\d+(?:\.\d+)*(?:[-+][0-9A-Za-z.\-]+)?"     # a pinned version, not a range or tag
SEMVER = r"\d+\.\d+\.\d+(?:-[0-9A-Za-z.\-]+)?"       # npm/CDN pins are full x.y.z; `vue@3` is a range
NPM_NAME = r"(?:@[a-z0-9][\w.\-]*/)?[a-z0-9][\w.\-]*"
CDN = [
    re.compile(rf"//cdn\.jsdelivr\.net/npm/({NPM_NAME})@({SEMVER})(?=[/\"'`\s?#)]|$)", re.I),
    re.compile(rf"//unpkg\.com/({NPM_NAME})@({SEMVER})(?=[/\"'`\s?#)]|$)", re.I),
    re.compile(rf"//esm\.sh/(?:v\d+/)?({NPM_NAME})@({SEMVER})(?=[/\"'`\s?#)]|$)", re.I),
    re.compile(rf"//cdn\.skypack\.dev/({NPM_NAME})@({SEMVER})(?=[/\"'`\s?#)]|$)", re.I),
]
CDNJS = re.compile(rf"//cdnjs\.cloudflare\.com/ajax/libs/([\w.\-]+)/(r\d+|{SEMVER})/", re.I)
CDNJS_NPM = {"three.js": "three", "highlight.js": "highlight.js", "vue": "vue", "Chart.js": "chart.js",
             "socket.io": "socket.io-client", "d3": "d3", "lodash.js": "lodash", "jquery": "jquery",
             "moment.js": "moment", "tone": "tone", "pixi.js": "pixi.js", "p5.js": "p5", "react": "react",
             "react-dom": "react-dom", "dompurify": "dompurify", "marked": "marked", "mathjs": "mathjs",
             "twitter-bootstrap": "bootstrap", "underscore.js": "underscore", "babel-standalone": "@babel/standalone",
             "leaflet": "leaflet", "axios": "axios", "gsap": "gsap", "jqueryui": "jquery-ui", "handlebars.js": "handlebars"}


@dataclass(frozen=True)
class Dep:
    ecosystem: str        # npm | PyPI
    name: str
    version: str


@dataclass
class Use:
    dep: Dep
    repo: str
    paths: set = field(default_factory=set)
    frozen: str = ""      # "<date> - <project>" when the use is a frozen day snapshot


# ------------------------------------------------------------------ parsers (pure: text in, deps out)
def norm_py(name: str) -> str:
    return re.sub(r"[-_.]+", "-", name).lower()


def parse_package_lock(text: str) -> set[Dep]:
    d, out = json.loads(text), set()
    for path, p in (d.get("packages") or {}).items():
        if path and "node_modules/" in path and p.get("version") and not p.get("link"):
            out.add(Dep("npm", p.get("name") or path.rsplit("node_modules/", 1)[1], p["version"]))
    def walk(deps: dict):
        for name, p in (deps or {}).items():
            if isinstance(p, dict) and p.get("version") and re.fullmatch(EXACT, p["version"]):
                out.add(Dep("npm", name, p["version"]))
                walk(p.get("dependencies"))
    if not d.get("packages"):
        walk(d.get("dependencies"))
    return out


def parse_yarn_lock(text: str) -> set[Dep]:
    out = set()
    for block in re.split(r"\n(?=\S)", text):
        head, _, body = block.partition("\n")
        m = re.search(r'^\s+version:?\s+"?([^"\s]+)"?', body, re.M)
        if not m or head.startswith(("#", "__metadata")) or not head.rstrip().endswith(":"):
            continue
        spec = head.strip().rstrip(":").split(",")[0].strip().strip('"')
        if "@workspace:" in spec or "@link:" in spec or "@file:" in spec or "@patch:" in spec:
            continue
        if "@npm:" in spec and re.match(r"@?[a-z0-9]", spec.split("@npm:", 1)[1], re.I):
            spec = spec.split("@npm:", 1)[1]              # an alias (foo@npm:lodash@^4) installs the real package
        name = spec[: spec.rindex("@")] if spec.rfind("@") > 0 else spec
        out.add(Dep("npm", name, m.group(1)))
    return out


def parse_pnpm_lock(text: str) -> set[Dep]:
    d, out = yaml.safe_load(text) or {}, set()
    for k in (d.get("packages") or {}):
        k = re.sub(r"\(.*$", "", str(k)).lstrip("/")          # drop peer suffixes, v5/v6 leading slash
        m = re.fullmatch(rf"({NPM_NAME})[@/]({EXACT})", k, re.I)
        if m:
            out.add(Dep("npm", m.group(1), m.group(2)))
    return out


def parse_requirements(text: str) -> set[Dep]:
    out = set()
    for line in text.splitlines():
        # pip-compile --generate-hashes: `name==1.0 \` then `--hash=...` lines (or hashes on the same line)
        m = re.match(rf"^\s*([A-Za-z0-9][\w.\-]*)(?:\[[^\]]*\])?\s*===?\s*({EXACT})\s*"
                     rf"(?:;[^#\\]*?)?(?:\s+--hash=\S+)*\s*\\?\s*(?:#.*)?$", line)
        if m:
            out.add(Dep("PyPI", norm_py(m.group(1)), m.group(2)))
    return out


def parse_pyproject(text: str) -> set[Dep]:
    d = tomllib.loads(text)
    reqs = list((d.get("project") or {}).get("dependencies") or [])
    for group in ((d.get("project") or {}).get("optional-dependencies") or {}).values():
        reqs += group
    return parse_requirements("\n".join(reqs))


def parse_toml_lock(text: str) -> set[Dep]:            # poetry.lock, uv.lock
    return {Dep("PyPI", norm_py(p["name"]), str(p["version"])) for p in tomllib.loads(text).get("package") or []
            if p.get("name") and p.get("version") and not (p.get("source") or {}).get("editable")
            and not (p.get("source") or {}).get("virtual")}


def parse_pipfile_lock(text: str) -> set[Dep]:
    d, out = json.loads(text), set()
    for sec in ("default", "develop"):
        for name, p in (d.get(sec) or {}).items():
            v = str(p.get("version", ""))
            if v.startswith("=="):
                out.add(Dep("PyPI", norm_py(name), v[2:]))
    return out


def cdnjs_version(lib: str, ver: str) -> str:
    return f"0.{ver[1:]}.0" if lib == "three.js" and ver.startswith("r") else ver


def parse_web(text: str) -> set[Dep]:
    """Pinned library versions loaded from CDNs in HTML or JS."""
    out = {Dep("npm", m.group(1).lower(), m.group(2)) for rx in CDN for m in rx.finditer(text)}
    for m in CDNJS.finditer(text):
        lib, ver = m.group(1), cdnjs_version(m.group(1), m.group(2))
        if re.fullmatch(SEMVER, ver):
            out.add(Dep("npm", CDNJS_NPM.get(lib, lib.lower()), ver))
    return out


def parse(path: str, text: str) -> set[Dep]:
    name = path.rsplit("/", 1)[-1]
    try:
        if name in ("package-lock.json", "npm-shrinkwrap.json"):
            return parse_package_lock(text)
        if name == "yarn.lock":
            return parse_yarn_lock(text)
        if name == "pnpm-lock.yaml":
            return parse_pnpm_lock(text)
        if name.startswith("requirements") and name.endswith(".txt"):
            return parse_requirements(text)
        if name == "pyproject.toml":
            return parse_pyproject(text)
        if name in ("poetry.lock", "uv.lock"):
            return parse_toml_lock(text)
        if name == "Pipfile.lock":
            return parse_pipfile_lock(text)
        if WEB.search(name):
            return parse_web(text)
    except Exception as ex:   # one unreadable file never stops the nightly run
        print(f"::warning::deps: could not parse {path}: {str(ex)[:100]}")
    return set()


# ------------------------------------------------------------------ inventory
def tree(bare: Path, rev: str) -> dict[str, str]:
    from control import git
    return {p: info.split()[2] for info, p in (l.split("\t", 1) for l in git(bare, "ls-tree", "-r", rev).splitlines() if "\t" in l)}


def wanted(path: str) -> bool:
    return not SKIP_DIR.search(path) and (bool(LOCKFILE.search(path)) or bool(WEB.search(path)))


def is_frozen(e: dict) -> bool:
    """A sprint_log day build that is frozen on purpose: pinned to a commit, kept in a snapshots/ folder, or a
    separate file from the one that keeps evolving (latest_file)."""
    f = e.get("file")
    return bool(f and e.get("repo") and (e.get("file_commit") or "snapshots/" in f
                                         or (e.get("latest_file") and e["latest_file"] != f)))


def frozen_files(index_cfg: dict) -> set[tuple[str, str]]:
    """(repo, path) of frozen copies at the branch tip: those are never reported as editable files.
    A file_commit entry's path at the tip is the live build, so it stays in the normal scan."""
    return {(e["repo"], e["file"]) for e in index_cfg.get("sprint_log") or [] if is_frozen(e) and not e.get("file_commit")}


def inventory(scan: Path, repos: dict, index_cfg: dict, root: Path = HERE) -> list[Use]:
    """Every pinned library each repo uses, where, and the frozen snapshots."""
    from control import blobs_batch
    from uat import read_file
    uses: dict[tuple, Use] = {}
    def add(repo: str, path: str, deps: set[Dep], frozen: str = ""):
        for d in deps:
            u = uses.setdefault((repo, d, frozen), Use(d, repo, frozen=frozen))
            u.paths.add(path)
    frozen = frozen_files(index_cfg)
    for n, r in repos.items():
        if r.empty:
            continue
        bare = scan / f"{n}.git"
        # the default branch is what ships; a feature branch bumping a library must not resolve the finding
        files = {p: s for p, s in tree(bare, r.default or r.work_branch).items()
                 if wanted(p) and (n, p) not in frozen}      # frozen copies are judged below, never as editable
        blobs = blobs_batch(bare, list(set(files.values())))
        for p, sha in files.items():
            if sha in blobs:
                add(n, p, parse(p, blobs[sha]))
    # the public sites' dependency list (what the published pages really load)
    spec = yaml.safe_load((root / "site_deps.yml").read_text()) if (root / "site_deps.yml").exists() else {}
    for d in (spec or {}).get("deps") or []:
        o = d.get("osv")
        if isinstance(o, dict) and o.get("ecosystem") in ECOSYSTEMS and o.get("package") and o.get("version"):
            add("index", "site_deps.yml", {Dep(o["ecosystem"], o["package"], str(o["version"]))})
        elif o:
            print(f"::warning::deps: site_deps.yml {d.get('name')}: osv needs ecosystem (npm|PyPI), package, version")
    # frozen day snapshots
    for e in index_cfg.get("sprint_log") or []:
        if not is_frozen(e):
            continue
        f = e["file"]
        text = read_file(scan, e["repo"], f, e.get("file_commit"))
        if text:
            add(e["repo"], f + (f" @ {e['file_commit']}" if e.get("file_commit") else ""), parse_web(text),
                frozen=f"{e['date']} - {e['project']}")
    return list(uses.values())


# ------------------------------------------------------------------ OSV
def http_json(url: str, body: dict | None = None, tries: int = 3) -> dict:
    """POST/GET JSON with retries (2 s, 4 s)."""
    import time
    data = json.dumps(body).encode() if body is not None else None
    for i in range(tries):
        try:
            req = urllib.request.Request(url, data=data, headers={"Content-Type": "application/json",
                                                                    "User-Agent": "index-deps/1"})
            with urllib.request.urlopen(req, timeout=60) as r:
                return json.loads(r.read())
        except Exception:
            if i == tries - 1:
                raise
            time.sleep(2 ** (i + 1))


def _query(d: Dep) -> dict:
    return {"package": {"name": d.name, "ecosystem": d.ecosystem}, "version": d.version}


def _ids_for(d: Dep, first: dict, get) -> list[str]:
    ids, r = [v["id"] for v in first.get("vulns") or []], first
    while r.get("next_page_token"):                      # rare: very many advisories for one package
        r = get(f"{OSV}/query", {**_query(d), "page_token": r["next_page_token"]})
        ids += [v["id"] for v in r.get("vulns") or []]
    return ids


def osv_lookup(deps: list[Dep], get=http_json) -> tuple[dict[Dep, list[dict]], set[Dep]]:
    """(Dep -> OSV records affecting that exact version, deps that couldn't be checked tonight).
    A failed batch falls back to one query per package, so one bad entry can't blind the whole check."""
    ids: dict[Dep, list[str]] = {}
    failed: set[Dep] = set()
    for i in range(0, len(deps), 1000):
        chunk = deps[i:i + 1000]
        try:
            res = get(f"{OSV}/querybatch", {"queries": [_query(d) for d in chunk]}).get("results") or []
            if len(res) != len(chunk):
                raise ValueError(f"OSV answered {len(res)} of {len(chunk)} queries")
            for d, r in zip(chunk, res):
                ids[d] = _ids_for(d, r or {}, get)
        except Exception:
            for d in chunk:
                try:
                    ids[d] = _ids_for(d, get(f"{OSV}/query", _query(d)), get)
                except Exception:
                    failed.add(d)
    if deps and len(failed) == len(deps):
        raise ConnectionError("OSV unreachable for every package")
    unique = sorted({v for vs in ids.values() for v in vs})
    def detail(v: str):
        try:
            return get(f"{OSV}/vulns/{v}")
        except Exception:
            return None
    with ThreadPoolExecutor(8) as pool:
        full = dict(zip(unique, pool.map(detail, unique)))
    out = {}
    for d, vs in ids.items():
        if any(full[v] is None for v in vs):
            failed.add(d)                                   # don't judge a package on half its advisories
            continue
        recs = dedupe([full[v] for v in vs if not full[v].get("withdrawn")])
        if recs:
            out[d] = recs
    return out, failed


def dedupe(recs: list[dict]) -> list[dict]:
    """One record per vulnerability: OSV lists the same issue under several ids (PYSEC-…, GHSA-…, CVE-…).
    GitHub's copy is kept when there is one (it carries the severity rating)."""
    out, seen = [], set()
    for r in sorted(recs, key=lambda r: (not r["id"].startswith("GHSA-"), r["id"])):
        names = {r["id"], *(r.get("aliases") or [])}
        if names & seen:
            continue
        seen |= names
        out.append(r)
    return out


# CVSS v3.x base score (FIRST spec) for advisories that only carry a vector
_W = {"AV": {"N": .85, "A": .62, "L": .55, "P": .2}, "AC": {"L": .77, "H": .44}, "UI": {"N": .85, "R": .62},
      "C": {"H": .56, "L": .22, "N": 0}, "I": {"H": .56, "L": .22, "N": 0}, "A": {"H": .56, "L": .22, "N": 0}}


def cvss3(vector: str) -> float | None:
    try:
        m = dict(p.split(":") for p in vector.split("/")[1:])
        changed = m["S"] == "C"
        pr = {"N": .85, "L": .68 if changed else .62, "H": .5 if changed else .27}[m["PR"]]
        iss = 1 - (1 - _W["C"][m["C"]]) * (1 - _W["I"][m["I"]]) * (1 - _W["A"][m["A"]])
        impact = 7.52 * (iss - .029) - 3.25 * (iss - .02) ** 15 if changed else 6.42 * iss
        expl = 8.22 * _W["AV"][m["AV"]] * _W["AC"][m["AC"]] * pr * _W["UI"][m["UI"]]
        if impact <= 0:
            return 0.0
        raw = min(1.08 * (impact + expl), 10) if changed else min(impact + expl, 10)
        return math.ceil(raw * 10 - 1e-9) / 10
    except (KeyError, ValueError, IndexError):
        return None


def severity(v: dict) -> tuple[str, bool]:
    """(critical|high|medium|low, rated). GitHub's rating first, then a CVSS v3 score; unrated counts as medium."""
    if str(v.get("id", "")).startswith("MAL-"):          # a malicious package: no rating needed
        return "critical", True
    s = str((v.get("database_specific") or {}).get("severity") or "").upper()
    for a in v.get("affected") or []:
        s = s or str((a.get("ecosystem_specific") or {}).get("severity") or (a.get("database_specific") or {}).get("severity") or "").upper()
    if s in ("CRITICAL", "HIGH", "LOW"):
        return s.lower(), True
    if s in ("MODERATE", "MEDIUM"):
        return "medium", True
    for sv in v.get("severity") or []:
        if sv.get("type") == "CVSS_V3" and (score := cvss3(sv.get("score", ""))) is not None:
            return ("critical" if score >= 9 else "high" if score >= 7 else "medium" if score >= 4 else "low"), True
    return "medium", False


def vkey(v: str) -> tuple:
    """Sort key for npm (semver) and PyPI (PEP 440) versions: 1.0.0-beta.1 < 1.0.0 < 1.0.1, 2.0rc1 < 2.0 < 2.0.post1."""
    v = v.strip().lower().lstrip("v").split("+", 1)[0]
    m = re.match(r"(\d+(?:\.\d+)*)(.*)$", v)
    if not m:
        return ((), 0, ())
    nums = tuple(int(x) for x in m.group(1).split("."))
    nums = nums + (0,) * (6 - len(nums))
    rest = m.group(2).lstrip(".-_")
    if not rest:
        return (nums, 1, ())                                   # a release
    if p := re.fullmatch(r"(?:post|rev|r)\.?(\d*)", rest):
        return (nums, 2, (int(p.group(1) or 0),))
    tag = re.match(r"(dev|a|alpha|b|beta|c|rc|pre|preview)?\.?(\d*)(.*)", rest)
    rank = {"dev": -4, "a": -3, "alpha": -3, "b": -2, "beta": -2, "c": -1, "rc": -1, "pre": -1, "preview": -1}
    if tag and tag.group(1):
        return (nums, 0, (rank[tag.group(1)], int(tag.group(2) or 0)))
    return (nums, 0, (0, rest))                                # other pre-release text: still before the release


def fixed_in(v: dict, d: Dep) -> str | None:
    """The lowest fixed version above `d.version` for this package, if OSV knows one."""
    fixes = []
    for a in v.get("affected") or []:
        p = a.get("package") or {}
        if p.get("ecosystem") != d.ecosystem or (norm_py(p.get("name", "")) if d.ecosystem == "PyPI" else p.get("name")) != d.name:
            continue
        for r in a.get("ranges") or []:
            fixes += [e["fixed"] for e in r.get("events") or [] if "fixed" in e]
    above = [f for f in fixes if vkey(f) > vkey(d.version)]
    return min(above, key=vkey) if above else None


# ------------------------------------------------------------------ findings
def findings(uses: list[Use], vulns: dict[Dep, list[dict]], rubric: dict) -> list:
    from control import make
    out = []
    for u in sorted(uses, key=lambda u: (u.repo, u.frozen, u.dep.name, u.dep.version)):
        vs = vulns.get(u.dep)
        if not vs:
            continue
        rated = [(severity(v), v) for v in vs]
        worst = max(rated, key=lambda x: SEV_RANK[x[0][0]])
        sev = worst[0][0]
        fixes = [f for v in vs if (f := fixed_in(v, u.dep))]
        fix = max(fixes, key=vkey) if fixes else None       # the version that clears every advisory listed
        ids = sorted(v["id"] for v in vs)
        summary = (worst[1].get("summary") or "").strip()[:90]
        detail = (f"{u.dep.name} {u.dep.version} ({u.dep.ecosystem}): {len(vs)} advisor{'y' if len(vs) == 1 else 'ies'}, "
                  f"worst {worst[1]['id']} ({sev}{'' if worst[0][1] else ', unrated'})"
                  + (f": {summary}" if summary else "") + (f". Fixed in {fix}" if fix else ". No fixed version yet"))
        evidence = sorted(u.paths) + [f"https://osv.dev/vulnerability/{i}" for i in ids]
        check = "vulnerable-frozen-snapshot" if u.frozen else "vulnerable-dependency"
        subject = f"{u.frozen}/{u.dep.ecosystem}:{u.dep.name}@{u.dep.version}" if u.frozen else f"{u.dep.ecosystem}:{u.dep.name}@{u.dep.version}"
        f = make(rubric, u.repo, check, subject, detail, evidence, severity=sev, paths=", ".join(sorted(u.paths)),
                 fix=fix or "none yet", snapshot=u.frozen, package=u.dep.name, version=u.dep.version)
        if f:
            out.append(f)
    return out


def carried(rubric: dict, p: Path = HERE / "findings.json", only: set[Dep] | None = None) -> list:
    """Last night's dependency findings, unchanged (all of them, or just those for `only`): used when OSV couldn't
    be asked, so a finding is never resolved by an outage. Never raises."""
    from control import Finding
    try:
        prev = json.loads(p.read_text()).get("findings", []) if p.exists() else []
    except Exception:
        return []
    subj = {f"{d.ecosystem}:{d.name}@{d.version}" for d in only} if only is not None else None
    out = []
    for f in prev:
        if f.get("check") not in DEP_CHECKS or rubric["checks"].get(f["check"], {}).get("enabled", True) is False:
            continue
        if subj is not None and not any(str(f.get("id", "")).endswith(s) for s in subj):
            continue
        try:
            out.append(Finding(**{k: f[k] for k in Finding.__dataclass_fields__}))
        except (KeyError, TypeError):
            continue
    return out


def check(scan: Path, repos: dict, index_cfg: dict, rubric: dict, get=http_json, root: Path = HERE) -> list:
    if not any(rubric["checks"].get(c, {}).get("enabled", True) is not False and c in rubric["checks"] for c in DEP_CHECKS):
        return []
    try:
        uses = inventory(scan, repos, index_cfg, root)
    except Exception as ex:   # never let this check break the nightly grade
        print(f"::warning::deps: inventory failed ({str(ex)[:120]}); keeping last night's dependency findings")
        return carried(rubric, root / "findings.json")
    deps = sorted({u.dep for u in uses}, key=lambda d: (d.ecosystem, d.name, d.version))
    try:
        vulns, failed = osv_lookup(deps, get)
    except Exception as ex:
        print(f"::warning::deps: OSV unreachable ({str(ex)[:120]}); keeping last night's dependency findings")
        return carried(rubric, root / "findings.json")
    out = findings([u for u in uses if u.dep not in failed], vulns, rubric)
    if failed:
        print(f"::warning::deps: OSV failed for {len(failed)} packages; keeping last night's findings for those")
        out += carried(rubric, root / "findings.json", only=failed)
    print(f"deps: {len(deps)} pinned libraries in {len({u.repo for u in uses})} repos; "
          f"{len({u.dep for u in uses if u.dep in vulns})} with advisories; {len(out)} findings")
    return out


def big_files(scan: Path) -> None:
    """`repo rev path` for lockfiles the 1 MB clone filter left out, so refresh.yml can fetch just those."""
    from control import _present, git
    for bare in sorted(scan.glob("*.git")):
        rev = git(bare, "symbolic-ref", "--short", "HEAD").strip()        # the default branch, as inventory() reads
        if not rev:
            continue
        present = _present(bare)
        for p, sha in tree(bare, rev).items():
            if LOCKFILE.search(p) and not SKIP_DIR.search(p) and sha not in present:
                print(bare.name[:-4], rev, p)


if __name__ == "__main__":
    if sys.argv[1:2] == ["big-files"]:
        big_files(Path(sys.argv[2]))
    else:
        print(__doc__)
