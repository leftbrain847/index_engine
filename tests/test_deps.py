"""Dependency security check (deps.py): parsers, OSV severity and fixes, findings, and the safe fallbacks.
No network: OSV is faked. The inventory tests build small git repos locally."""
import json
import subprocess
import sys
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
import deps  # noqa: E402
from deps import Dep  # noqa: E402

RUBRIC = yaml.safe_load((ROOT / "rubric.yml").read_text())


# ------------------------------------------------------------------ parsers
def test_package_lock_v3_and_v1():
    v3 = {"lockfileVersion": 3, "packages": {"": {"name": "app"},
          "node_modules/lodash": {"version": "4.17.15"},
          "node_modules/a/node_modules/@scope/b": {"version": "1.0.0"},
          "node_modules/local": {"link": True, "version": "0.0.1"}}}
    assert deps.parse_package_lock(json.dumps(v3)) == {Dep("npm", "lodash", "4.17.15"), Dep("npm", "@scope/b", "1.0.0")}
    v1 = {"dependencies": {"x": {"version": "1.2.3", "dependencies": {"y": {"version": "2.0.0"}}},
                           "z": {"version": "github:me/z#abc"}}}
    assert deps.parse_package_lock(json.dumps(v1)) == {Dep("npm", "x", "1.2.3"), Dep("npm", "y", "2.0.0")}


def test_yarn_classic_and_berry():
    classic = '# yarn lockfile v1\n\n"@babel/core@^7.0.0", "@babel/core@^7.1.0":\n  version "7.2.0"\n\nleft-pad@1:\n  version "1.3.0"\n'
    berry = '__metadata:\n  version: 6\n\n"react@npm:^18.0.0":\n  version: 18.2.0\n\n"app@workspace:.":\n  version: 0.0.0-use.local\n'
    assert deps.parse_yarn_lock(classic) == {Dep("npm", "@babel/core", "7.2.0"), Dep("npm", "left-pad", "1.3.0")}
    assert deps.parse_yarn_lock(berry) == {Dep("npm", "react", "18.2.0")}
    alias = 'foo@npm:lodash@^4:\n  version "4.17.15"\n\n"bar@npm:@scope/real@^1":\n  version "1.0.0"\n'
    assert deps.parse_yarn_lock(alias) == {Dep("npm", "lodash", "4.17.15"), Dep("npm", "@scope/real", "1.0.0")}


def test_pnpm_v5_v6_v9_keys():
    text = yaml.safe_dump({"packages": {"/a/1.0.0": {}, "/@s/b@2.0.0": {}, "c@3.0.0(react@18.2.0)": {}}})
    assert deps.parse_pnpm_lock(text) == {Dep("npm", "a", "1.0.0"), Dep("npm", "@s/b", "2.0.0"), Dep("npm", "c", "3.0.0")}


def test_python_sources_only_exact_pins():
    req = "Jinja2==2.10  # old\nrequests>=2\nnumpy[extra]==1.26.4 ; python_version<'3.13'\n-r other.txt\n"
    assert deps.parse_requirements(req) == {Dep("PyPI", "jinja2", "2.10"), Dep("PyPI", "numpy", "1.26.4")}
    hashed = "django==3.2.0 \\\n    --hash=sha256:aaa \\\n    --hash=sha256:bbb\nsix==1.16.0 --hash=sha256:ccc\n"
    assert deps.parse_requirements(hashed) == {Dep("PyPI", "django", "3.2.0"), Dep("PyPI", "six", "1.16.0")}
    pyproject = '[project]\nname="x"\ndependencies=["Flask==2.0.0","rich>=13"]\n[project.optional-dependencies]\ndev=["pytest==7.0.0"]\n'
    assert deps.parse_pyproject(pyproject) == {Dep("PyPI", "flask", "2.0.0"), Dep("PyPI", "pytest", "7.0.0")}
    uv = ('[[package]]\nname = "Typing_Extensions"\nversion = "4.0.0"\n'
          '[[package]]\nname = "me"\nversion = "0.1.0"\nsource = { editable = "." }\n')
    assert deps.parse_toml_lock(uv) == {Dep("PyPI", "typing-extensions", "4.0.0")}
    pipfile = {"default": {"django": {"version": "==3.2.0"}}, "develop": {"black": {"version": "*"}}}
    assert deps.parse_pipfile_lock(json.dumps(pipfile)) == {Dep("PyPI", "django", "3.2.0")}


def test_cdn_links_in_html_and_js():
    page = ('<script src="https://cdn.jsdelivr.net/npm/echarts@6.1.0/dist/echarts.min.js"></script>'
            '<script src="https://unpkg.com/@scope/pkg@1.2.3/x.js"></script>'
            "import * as d from 'https://cdn.jsdelivr.net/npm/@duckdb/duckdb-wasm@1.32.0/+esm';"
            '<script src="https://cdnjs.cloudflare.com/ajax/libs/three.js/r128/three.min.js"></script>'
            '<script src="https://cdnjs.cloudflare.com/ajax/libs/highlight.js/11.11.1/highlight.min.js"></script>'
            '<script src="https://cdnjs.cloudflare.com/ajax/libs/twitter-bootstrap/5.3.3/js/bootstrap.min.js"></script>'
            '<script src="https://cdn.jsdelivr.net/npm/vue@3/dist/vue.js"></script>'           # a range: skipped
            '<script src="https://cdn.jsdelivr.net/npm/d3@latest"></script>')                  # a tag: skipped
    assert deps.parse_web(page) == {Dep("npm", "echarts", "6.1.0"), Dep("npm", "@scope/pkg", "1.2.3"),
                                    Dep("npm", "@duckdb/duckdb-wasm", "1.32.0"), Dep("npm", "three", "0.128.0"),
                                    Dep("npm", "highlight.js", "11.11.1"), Dep("npm", "bootstrap", "5.3.3")}


def test_a_broken_file_is_skipped_not_fatal():
    assert deps.parse("package-lock.json", "{not json") == set()


# ------------------------------------------------------------------ severity + fixes
def test_severity_prefers_github_rating_then_cvss():
    assert deps.severity({"database_specific": {"severity": "MODERATE"}}) == ("medium", True)
    assert deps.severity({"database_specific": {"severity": "CRITICAL"}}) == ("critical", True)
    crit = {"severity": [{"type": "CVSS_V3", "score": "CVSS:3.1/AV:N/AC:L/PR:N/UI:N/S:U/C:H/I:H/A:H"}]}
    assert deps.severity(crit) == ("critical", True)                           # 9.8
    assert deps.cvss3("CVSS:3.1/AV:N/AC:L/PR:N/UI:R/S:C/C:L/I:L/A:N") == 6.1   # the classic XSS vector
    assert deps.cvss3("CVSS:3.1/AV:L/AC:L/PR:L/UI:N/S:U/C:H/I:N/A:N") == 5.5
    assert deps.severity({"severity": [{"type": "CVSS_V4", "score": "CVSS:4.0/AV:N"}]}) == ("medium", False)


def test_fixed_in_picks_the_nearest_fix_above_the_pinned_version():
    v = {"affected": [{"package": {"ecosystem": "npm", "name": "lodash"},
                       "ranges": [{"events": [{"introduced": "0"}, {"fixed": "4.17.12"}]},
                                  {"events": [{"introduced": "4.17.13"}, {"fixed": "4.17.21"}]}]},
                      {"package": {"ecosystem": "npm", "name": "lodash-es"}, "ranges": [{"events": [{"fixed": "9.9.9"}]}]}]}
    assert deps.fixed_in(v, Dep("npm", "lodash", "4.17.15")) == "4.17.21"
    assert deps.fixed_in(v, Dep("npm", "lodash", "4.17.21")) is None


# ------------------------------------------------------------------ OSV + findings
def fake_osv(vulns_by_dep: dict, records: dict, pages: dict | None = None):
    calls = []
    def get(url, body=None):
        calls.append(url)
        if url.endswith("/querybatch"):
            out = []
            for q in body["queries"]:
                d = Dep(q["package"]["ecosystem"], q["package"]["name"], q["version"])
                r = {"vulns": [{"id": i} for i in vulns_by_dep.get(d, [])]}
                if pages and d in pages:
                    r["next_page_token"] = "p2"
                out.append(r)
            return {"results": out}
        if url.endswith("/query"):
            d = Dep(body["package"]["ecosystem"], body["package"]["name"], body["version"])
            return {"vulns": [{"id": i} for i in pages[d]]}
        return records[url.rsplit("/", 1)[1]]
    get.calls = calls
    return get


LODASH = Dep("npm", "lodash", "4.17.15")
REC = {
    "GHSA-high": {"id": "GHSA-high", "summary": "Prototype pollution", "database_specific": {"severity": "HIGH"},
                  "affected": [{"package": {"ecosystem": "npm", "name": "lodash"}, "ranges": [{"events": [{"fixed": "4.17.19"}]}]}]},
    "GHSA-crit": {"id": "GHSA-crit", "summary": "Command injection", "database_specific": {"severity": "CRITICAL"},
                  "affected": [{"package": {"ecosystem": "npm", "name": "lodash"}, "ranges": [{"events": [{"fixed": "4.17.21"}]}]}]},
    "GHSA-gone": {"id": "GHSA-gone", "withdrawn": "2024-01-01T00:00:00Z", "database_specific": {"severity": "CRITICAL"}},
}


def test_osv_lookup_pages_and_drops_withdrawn():
    get = fake_osv({LODASH: ["GHSA-high", "GHSA-gone"]}, REC, pages={LODASH: ["GHSA-crit"]})
    out, failed = deps.osv_lookup([LODASH, Dep("PyPI", "safe", "1.0.0")], get)
    assert sorted(v["id"] for v in out[LODASH]) == ["GHSA-crit", "GHSA-high"] and len(out) == 1 and not failed


def test_one_bad_package_or_advisory_does_not_blind_the_rest():
    bad = Dep("npm", "broken", "1.0.0")
    def get(url, body=None):
        if url.endswith("/querybatch"):
            raise OSError("400 for the whole batch")                        # one bad query fails the batch
        if url.endswith("/query"):
            if body["package"]["name"] == "broken":
                raise OSError("400")
            if body["package"]["name"] == "flaky":
                return {"vulns": [{"id": "GHSA-missing"}]}
            return {"vulns": [{"id": "GHSA-high"}]} if body["package"]["name"] == "lodash" else {}
        vid = url.rsplit("/", 1)[1]
        if vid == "GHSA-missing":
            raise OSError("timeout")
        return REC[vid]
    flaky = Dep("npm", "flaky", "1.0.0")
    out, failed = deps.osv_lookup([LODASH, bad, flaky], get)
    assert list(out) == [LODASH] and failed == {bad, flaky}


def test_a_short_batch_reply_is_not_trusted():
    def get(url, body=None):
        if url.endswith("/querybatch"):
            return {"results": [{}]}                                        # 1 answer for 2 questions
        if url.endswith("/query"):
            return {"vulns": [{"id": "GHSA-high"}]} if body["package"]["name"] == "lodash" else {}
        return REC[url.rsplit("/", 1)[1]]
    out, failed = deps.osv_lookup([Dep("npm", "a", "1.0.0"), LODASH], get)
    assert list(out) == [LODASH] and not failed


def test_aliases_count_once_and_malicious_packages_are_critical():
    recs = [{"id": "PYSEC-1", "aliases": ["GHSA-x", "CVE-1"]}, {"id": "GHSA-x", "aliases": ["PYSEC-1", "CVE-1"]},
            {"id": "GHSA-y"}]
    assert [r["id"] for r in deps.dedupe(recs)] == ["GHSA-x", "GHSA-y"]
    assert deps.severity({"id": "MAL-2022-1"}) == ("critical", True)


def test_versions_order_prereleases_before_releases():
    k = deps.vkey
    assert k("1.0.0-beta.1") < k("1.0.0") < k("1.0.1") < k("1.10.0")
    assert k("2.0rc1") < k("2.0") < k("2.0.post1") and k("2.0.dev1") < k("2.0a1") < k("2.0b2") < k("2.0rc1")
    v = {"affected": [{"package": {"ecosystem": "npm", "name": "x"}, "ranges": [{"events": [{"fixed": "1.0.0"}]}]}]}
    assert deps.fixed_in(v, Dep("npm", "x", "1.0.0-beta.1")) == "1.0.0"


def test_findings_take_the_worst_severity_and_the_fix_that_clears_all():
    uses = [deps.Use(LODASH, "game", {"index.html"}),
            deps.Use(LODASH, "game", {"day.html @ abc123"}, frozen="2026-11-03 - Game")]
    fs = deps.findings(uses, {LODASH: [REC["GHSA-high"], REC["GHSA-crit"]]}, RUBRIC)
    live, frozen = sorted(fs, key=lambda f: f.check)
    assert live.check == "vulnerable-dependency" and frozen.check == "vulnerable-frozen-snapshot"
    assert live.severity == frozen.severity == "critical" and live.category == "security"
    assert "Fixed in 4.17.21" in live.detail and "GHSA-crit" in live.detail
    assert "Found in: index.html." in live.prompt and "osv.dev" not in live.prompt.split("Found in:")[1].split("\n")[0]
    assert "https://osv.dev/vulnerability/GHSA-crit" in live.evidence
    assert "Never edit the snapshot" in frozen.prompt and "2026-11-03 - Game" in frozen.prompt
    assert live.code != frozen.code


def test_deadlines_follow_the_rubric():
    assert RUBRIC["settings"]["sla_days"] == {"critical": 2, "high": 7, "medium": 30, "low": 90}


def test_carried_findings_survive_an_old_findings_file(tmp_path):
    (tmp_path / "findings.json").write_text(json.dumps({"findings": [{"check": "vulnerable-dependency", "id": "x"}]}))
    assert deps.carried(RUBRIC, tmp_path / "findings.json") == []
    (tmp_path / "findings.json").write_text("not json")
    assert deps.carried(RUBRIC, tmp_path / "findings.json") == []


def test_osv_down_keeps_last_nights_findings(tmp_path):
    from control import make
    prior = make(RUBRIC, "game", "vulnerable-dependency", "npm:lodash@4.17.15", "lodash bad", ["index.html"],
                 severity="high", package="lodash", version="4.17.15", fix="4.17.21", snapshot="")
    from dataclasses import asdict
    (tmp_path / "findings.json").write_text(json.dumps({"findings": [asdict(prior), {**asdict(prior), "check": "no-ci"}]}))
    (tmp_path / "site_deps.yml").write_text("deps: [{name: x, osv: {ecosystem: npm, package: x, version: 1.0.0}}]")
    def down(url, body=None):
        raise OSError("network unreachable")
    out = deps.check(tmp_path, {}, {}, RUBRIC, get=down, root=tmp_path)
    assert [f.code for f in out] == [prior.code] and out[0].severity == "high"


def git(cwd, *a):
    subprocess.run(["git", "-c", "user.name=t", "-c", "user.email=t@t", *a], cwd=cwd, check=True, capture_output=True)


def test_inventory_reads_repos_site_list_and_frozen_snapshots(tmp_path):
    from control import collect
    src, scan = tmp_path / "src", tmp_path / "scan"
    src.mkdir()
    scan.mkdir()
    git(src, "init", "-q", "-b", "main")
    (src / "requirements.txt").write_text("jinja2==2.10\n")
    (src / "day.html").write_text('<script src="https://cdn.jsdelivr.net/npm/lodash@4.17.15/lodash.js"></script>')
    (src / "node_modules").mkdir()
    (src / "node_modules/x.html").write_text('<script src="https://unpkg.com/ignored@1.0.0/x.js"></script>')
    git(src, "add", "-A")
    git(src, "commit", "-qm", "day")
    frozen = subprocess.run(["git", "rev-parse", "--short", "HEAD"], cwd=src, capture_output=True, text=True).stdout.strip()
    (src / "day.html").write_text('<script src="https://cdn.jsdelivr.net/npm/lodash@4.17.21/lodash.js"></script>')
    (src / "web/snapshots").mkdir(parents=True)
    (src / "web/snapshots/day4.html").write_text('<script src="https://cdn.jsdelivr.net/npm/echarts@5.0.0/e.js"></script>')
    (src / "Day 5.html").write_text('<script src="https://unpkg.com/d3@7.0.0/d3.js"></script>')
    (src / "app.html").write_text('<script src="https://unpkg.com/vue@3.4.0/vue.js"></script>')
    git(src, "add", "-A")
    git(src, "commit", "-qam", "later")
    git(src, "checkout", "-qb", "feature")                              # newer, but not what ships
    (src / "app.html").write_text('<script src="https://unpkg.com/vue@3.5.0/vue.js"></script>')
    git(src, "commit", "-qam", "bump on a branch")
    git(src, "checkout", "-q", "main")
    git(tmp_path, "clone", "-q", "--bare", str(src), str(scan / "game.git"))
    root = tmp_path / "root"
    root.mkdir()
    (root / "site_deps.yml").write_text(yaml.safe_dump({"deps": [{"name": "three", "osv": {"ecosystem": "npm", "package": "three", "version": "0.128.0"}}]}))
    idx = {"sprint_log": [
        {"date": "2026-11-03", "project": "Game", "repo": "game", "file": "day.html", "file_commit": frozen},
        {"date": "2026-11-04", "project": "Copy", "repo": "game", "file": "web/snapshots/day4.html", "latest_url": "https://x/"},
        {"date": "2026-11-05", "project": "Sep", "repo": "game", "file": "Day 5.html", "latest_file": "app.html"}]}
    uses = deps.inventory(scan, {"game": collect(scan / "game.git", {})}, idx, root)
    got = {(u.repo, u.dep.name, u.dep.version, u.frozen) for u in uses}
    assert got == {("game", "jinja2", "2.10", ""), ("game", "lodash", "4.17.21", ""),    # day.html at the tip is live
                   ("game", "lodash", "4.17.15", "2026-11-03 - Game"), ("index", "three", "0.128.0", ""),
                   ("game", "echarts", "5.0.0", "2026-11-04 - Copy"), ("game", "d3", "7.0.0", "2026-11-05 - Sep"),
                   ("game", "vue", "3.4.0", "")}                                         # feature branch ignored
    assert not any(u.frozen == "" and u.dep.name in ("echarts", "d3") for u in uses)       # frozen copies never editable


def test_big_lockfiles_left_out_by_the_clone_filter_are_listed(tmp_path, capsys):
    src, scan = tmp_path / "src", tmp_path / "scan"
    src.mkdir()
    scan.mkdir()
    git(src, "init", "-q", "-b", "main")
    big = {"packages": {f"node_modules/p{i}": {"version": "1.0.0", "pad": "x" * 200} for i in range(6000)}}
    (src / "package-lock.json").write_text(json.dumps(big))          # ~1.5 MB
    (src / "requirements.txt").write_text("x==1.0\n")
    git(src, "add", "-A")
    git(src, "commit", "-qm", "c")
    git(src, "config", "uploadpack.allowfilter", "true")              # let the local "server" honour the filter
    git(tmp_path, "clone", "-q", "--bare", "--no-local", "--filter=blob:limit=1m",
        src.as_uri(), str(scan / "app.git"))
    deps.big_files(scan)
    assert capsys.readouterr().out.strip() == "app main package-lock.json"
