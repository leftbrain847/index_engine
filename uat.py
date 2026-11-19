"""Automated UAT for sprint builds.

  python uat.py SCAN_DIR [--shim URL_SUBSTRING=LOCAL_FILE ...]

For each sprint log entry (index.yml's `sprint_log` plus discovered day snapshots, see sprint.py) that names a `file` (path inside its repo), loads that HTML from
SCAN_DIR/<repo>.git (or this repo for `repo: index`) in headless Chromium at every viewport in uat.yml,
runs the checks, and writes uat_results.json. --shim serves a URL from a local file (for offline runs).
"""
from __future__ import annotations

import hashlib
import json
import queue
import threading
import re
import subprocess
import sys
import tempfile
import time
from datetime import date
from pathlib import Path
from urllib.parse import urlparse

import yaml
from playwright.sync_api import sync_playwright

from sprint import entries as sprint_entries, runs_as_artifact

HERE = Path(__file__).parent
SEV_RANK = {"critical": 4, "high": 3, "medium": 2, "low": 1}

INTERACTIVE = "button, a[href], input, select, textarea, [role=button], [role=tab], [onclick]"
MEASURE_JS = """(sel) => {
  const out = []; const W = innerWidth, H = innerHeight;
  for (const el of document.querySelectorAll(sel)) {
    const r = el.getBoundingClientRect(), cs = getComputedStyle(el);
    if (r.width === 0 || r.height === 0 || cs.visibility === 'hidden' || cs.display === 'none') continue;
    if (r.bottom < 0 || r.right < 0 || r.top > H * 3 || r.left > W) continue;
    if (Math.min(r.width, r.height) < %d) out.push((el.innerText || el.getAttribute('aria-label') || el.tagName).trim().slice(0, 24) + ` ${Math.round(r.width)}×${Math.round(r.height)}`);
  }
  return out;
}"""


def read_file(scan: Path, repo: str, path: str, rev: str | None = None, any_branch: bool = True) -> str | None:
    """`path` from the repo's clone: at `rev` if given (a pinned day build), else the default branch.
    any_branch=False never falls back to an unmerged branch (the public homepage only takes merged work)."""
    if repo == "index" and not rev:
        p = HERE / path
        return p.read_text() if p.exists() else None
    g = str(scan / f"{repo}.git")
    if rev:
        if not any_branch and subprocess.run(["git", "-C", g, "merge-base", "--is-ancestor", rev, "HEAD"],
                                             capture_output=True).returncode != 0:
            return None   # a pinned commit that isn't on the default branch is unmerged work
        r = subprocess.run(["git", "-C", g, "show", f"{rev}:{path}"], capture_output=True)
        return r.stdout.decode(errors="replace") if r.returncode == 0 else None
    r = subprocess.run(["git", "-C", g, "show", f"HEAD:{path}"], capture_output=True)
    if r.returncode != 0 and any_branch:    # not on the default branch yet (e.g. a rename PR still open): newest commit on any branch
        c = subprocess.run(["git", "-C", g, "log", "--all", "-1", "--format=%H", "--", path], capture_output=True, text=True).stdout.strip()
        r = subprocess.run(["git", "-C", g, "show", f"{c}:{path}"], capture_output=True) if c else r
    return r.stdout.decode(errors="replace") if r.returncode == 0 else None


ARTIFACT_HOST_HEAD = ('<meta charset=utf8><meta name=viewport content="width=device-width,initial-scale=1,viewport-fit=cover">')


def as_artifact_host(html: str) -> str:
    """Claude's artifact viewer wraps a page with a viewport tag; test what the viewer actually sees."""
    if re.search(r"<meta[^>]+name=[\"']?viewport", html, re.I):
        return html
    return ARTIFACT_HOST_HEAD + html


def run_one(browser, html_path: Path | str, name: str, cfg: dict, shims: dict[str, str], offline: bool = False,
            on_request=None, shot: str | None = None) -> list[dict]:
    """on_request(url, resource_type) sees every request, including ones a tap triggers (the publish gate uses it)."""
    C, issues = cfg["checks"], []
    add = lambda chk, detail, vp: issues.append({"check": chk, "severity": C[chk]["severity"], "title": C[chk]["title"],
                                                  "detail": detail, "viewport": vp})
    for vp_name, vp in cfg["viewports"].items():
        ctx = browser.new_context(viewport={"width": vp["width"], "height": vp["height"]}, is_mobile=vp["mobile"],
                                  has_touch=vp["mobile"], device_scale_factor=2 if vp["mobile"] else 1)
        page = ctx.new_page()
        errs, cons, failed, hosts = [], [], [], set()
        page.on("pageerror", lambda e: errs.append(str(e).split("\n")[0][:140]))
        page.on("console", lambda m: m.type == "error" and cons.append(m.text[:140]))
        if on_request:
            page.on("request", lambda r: on_request(r.url, r.resource_type))
        page.on("requestfailed", lambda r: failed.append(f"{urlparse(r.url).netloc or 'local'}{urlparse(r.url).path[-40:]}"))

        def route(r):
            url = r.request.url
            if url.startswith("http"):
                hosts.add(urlparse(url).netloc)
                for sub, local in shims.items():
                    if sub in url:
                        return r.fulfill(path=local)
                if offline and urlparse(url).netloc in cfg["allowed_hosts"]:
                    return r.fulfill(status=200, body="")   # sandbox can't reach CDNs; don't report the sandbox as a bug
            return r.continue_()
        page.route("**/*", route)
        t0 = time.time()
        try:
            page.goto(html_path if isinstance(html_path, str) else html_path.as_uri(), wait_until="load", timeout=15000)
        except Exception as e:
            add("js-errors", f"page did not load: {str(e)[:100]}", vp_name)
            ctx.close()
            continue
        load_ms = int((time.time() - t0) * 1000)
        page.wait_for_timeout(cfg["settle_ms"])
        if errs:
            add("js-errors", "; ".join(dict.fromkeys(errs))[:220], vp_name)
        # blank: little text and no drawn canvas/svg/img
        text_len = page.evaluate("document.body ? document.body.innerText.trim().length : 0")
        visual = page.evaluate("document.querySelectorAll('canvas, svg, img, video').length")
        if text_len < C["blank-render"].get("min_text_chars", 20) and not visual:
            add("blank-render", f"{text_len} characters of text, no canvas or images", vp_name)
        sw = page.evaluate("Math.max(document.documentElement.scrollWidth, document.body ? document.body.scrollWidth : 0)")
        if vp["mobile"] and sw > vp["width"] + C["horizontal-overflow"].get("tolerance_px", 2):
            add("horizontal-overflow", f"content is {sw}px wide on a {vp['width']}px screen", vp_name)
        if vp["mobile"]:
            small = page.evaluate(MEASURE_JS % C["small-tap-targets"].get("min_px", 44), INTERACTIVE)
            if len(small) > C["small-tap-targets"].get("allow", 0):
                add("small-tap-targets", f"{len(small)} controls: " + ", ".join(small[:6]), vp_name)
            if not page.evaluate("!!document.querySelector('meta[name=viewport]')"):
                add("no-viewport-meta", "no <meta name=viewport>", vp_name)
        if load_ms > C["slow-load"].get("max_ms", 3000):
            add("slow-load", f"{load_ms} ms to load", vp_name)
        # interaction pass: tap visible controls, watch for new errors
        before = len(errs)
        for el in page.query_selector_all("button, [role=button], [role=tab], nav a")[: cfg["click_limit"]]:
            try:
                if el.is_visible():
                    el.click(timeout=1500, no_wait_after=True)
                    page.wait_for_timeout(250)
            except Exception:
                pass
        if len(errs) > before:
            add("interaction-errors", "; ".join(dict.fromkeys(errs[before:]))[:220], vp_name)
        if cons:
            add("console-errors", "; ".join(dict.fromkeys(cons))[:220], vp_name)
        if failed:
            add("failed-requests", ", ".join(dict.fromkeys(failed))[:220], vp_name)
        extra = sorted(h for h in hosts if h not in cfg["allowed_hosts"])
        if extra:
            add("external-hosts", ", ".join(extra), vp_name)
        title = page.title()
        if vp_name == "phone" and title.strip() != name:
            add("title-mismatch", f"title is \"{title[:60]}\"", vp_name)
        page.screenshot(path=str(HERE / "uat" / f"{shot or name}·{vp_name}.png"))
        ctx.close()
    # same problem at several viewports = one issue
    merged: dict[str, dict] = {}
    for i in issues:
        k = i["check"] + i["detail"]
        if k in merged:
            merged[k]["viewport"] += f", {i['viewport']}"
        else:
            merged[k] = dict(i)
    return sorted(merged.values(), key=lambda i: -SEV_RANK[i["severity"]])


def parallel(fn, items: list, workers: int, launch: dict | None = None) -> list:
    """fn(browser, item) for every item, `workers` at a time, results in the items' order. Playwright isn't
    thread-safe, so each worker thread runs its own Playwright and browser."""
    out: list = [None] * len(items)
    if not items:
        return out
    todo: queue.Queue = queue.Queue()
    for i, it in enumerate(items):
        todo.put((i, it))
    errors: list[BaseException] = []

    def work() -> None:
        try:
            with sync_playwright() as p:
                b = p.chromium.launch(**(launch or {}))
                while True:
                    try:
                        i, it = todo.get_nowait()
                    except queue.Empty:
                        break
                    out[i] = fn(b, it)
                b.close()
        except BaseException as ex:   # surface a worker's crash instead of returning silent gaps
            errors.append(ex)
    threads = [threading.Thread(target=work) for _ in range(max(1, min(int(workers or 1), len(items))))]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    if errors:
        raise errors[0]
    return out


SELF = hashlib.sha256(Path(__file__).read_bytes()).hexdigest()[:16]   # a change to these checks retests everything
# Results that depend on the night, not the page (a CDN hiccup, a slow runner): never reused, always retested.
VOLATILE = {"failed-requests", "slow-load"}
REUSE_DAYS = 7   # even an unchanged page is retested this often (its CDN files or the browser may have changed)


def fingerprint(html: str, name: str, cfg: dict, shims: dict, offline: bool) -> str:
    """Same page bytes, expected title, checks, settings, shim files and browser: same result."""
    try:
        from importlib.metadata import version
        pw = version("playwright")
    except Exception:
        pw = "?"
    shim_bytes = {k: hashlib.sha256(Path(v).read_bytes()).hexdigest() if Path(v).is_file() else v for k, v in shims.items()}
    key = json.dumps({"html": hashlib.sha256(html.encode()).hexdigest(), "name": name,
                      "cfg": {k: v for k, v in cfg.items() if k != "workers"}, "shims": shim_bytes,
                      "offline": offline, "uat": SELF, "playwright": pw}, sort_keys=True, default=str)
    return hashlib.sha256(key.encode()).hexdigest()


def reusable(rec: dict, today: date) -> bool:
    """A past result may stand in for tonight's: it finished, has no night-dependent issue, and isn't stale."""
    if rec.get("status") not in ("pass", "warn", "fail") or not rec.get("tested_on"):
        return False
    if any(i.get("check") in VOLATILE or "did not load" in str(i.get("detail", "")) for i in rec.get("issues") or []):
        return False
    try:
        return (today - date.fromisoformat(rec["tested_on"])).days < REUSE_DAYS
    except ValueError:
        return False


def main(argv: list[str]) -> None:
    scan = Path(argv[0])
    shims = dict(a.split("=", 1) for a in argv[1:] if "=" in a and not a.startswith("--"))
    offline = "--offline" in argv
    cfg = yaml.safe_load((HERE / "uat.yml").read_text())
    log = sprint_entries(yaml.safe_load((HERE / "index.yml").read_text()), scan)
    (HERE / "uat").mkdir(exist_ok=True)
    try:   # last run's results, reused for pages whose fingerprint hasn't changed
        prev = json.loads((HERE / "uat_results.json").read_text())
    except (OSError, ValueError):
        prev = {}
    today = date.today()
    seen = {r["fingerprint"]: r for r in (prev.get("results") or []) + (prev.get("latest") or [])
            if r.get("fingerprint") and reusable(r, today)}
    launch = {"executable_path": a.split("=", 1)[1]} if (a := next((x for x in argv if x.startswith("--chrome=")), None)) else {}
    results, latest, jobs = [], [], []   # jobs: (record, html, label) still to run in a browser
    tmp = tempfile.TemporaryDirectory()

    def prepare(rec: dict, html: str, e: dict, label: str) -> None:
        rec["viewed_as"] = "artifact" if runs_as_artifact(e) and cfg.get("emulate_artifact_host", True) else "file"
        page = as_artifact_host(html) if rec["viewed_as"] == "artifact" else html
        rec["fingerprint"] = fingerprint(page, label, cfg, shims, offline)
        if (old := seen.get(rec["fingerprint"])):
            rec.update(issues=old["issues"], status=old["status"], tested_on=old["tested_on"], cached=True)
            return
        f = Path(tmp.name) / f"{len(jobs)}.html"
        f.write_text(page)
        jobs.append((rec, f, label))

    for e in log:
        name = f"{e['date']} - {e['project']}"
        entry = {"name": name, "date": str(e["date"]), "repo": e.get("repo"), "file": e.get("file"),
                 "manual_uat": e.get("uat"), "issues": [], "status": "skipped"}
        if not e.get("file"):
            entry["note"] = "no file to test (doc or artifact-only)"
        elif (html := read_file(scan, e["repo"], e["file"], e.get("file_commit"))) is None:
            entry.update(status="error", note=f"{e['file']} not found in {e['repo']}")
        else:
            prepare(entry, html, e, name)
        results.append(entry)
        if e.get("latest_file"):   # the finished product, checked the same way before the public site links it
            lt = {"name": name, "repo": e.get("repo"), "file": e["latest_file"], "issues": [], "status": "error"}
            if (html := read_file(scan, e["repo"], e["latest_file"])) is None:
                lt["note"] = f"{e['latest_file']} not found in {e['repo']}"
            else:
                prepare(lt, html, e, name + " (latest)")
            latest.append(lt)

    def run(browser, job):
        rec, f, label = job
        try:
            issues = run_one(browser, f, rec["name"], cfg, shims, offline, shot=label)
        except Exception as ex:   # one broken page is recorded as an error (never reused), not the whole run lost
            return [], "error", f"UAT crashed: {str(ex)[:160]}"
        worst = max((SEV_RANK[i["severity"]] for i in issues), default=0)
        return issues, "fail" if worst >= 3 else "warn" if worst else "pass", None
    t0 = time.time()
    for (rec, _, _), (issues, status, note) in zip(jobs, parallel(run, jobs, cfg.get("workers", 1), launch)):
        rec.update(issues=issues, status=status, tested_on=today.isoformat())
        if note:
            rec["note"] = note
    tmp.cleanup()
    for rec in results:
        print(f"{rec['status']:>7}  {rec['name']}  ({len(rec['issues'])} issues){'  cached' if rec.get('cached') else ''}")
    for rec in latest:
        print(f"{rec['status']:>7}  {rec['name']} (latest)  ({len(rec['issues'])} issues){'  cached' if rec.get('cached') else ''}")
    print(f"tested {len(jobs)} pages in {time.time() - t0:.0f}s ({cfg.get('workers', 1)} at a time); "
          f"{sum(bool(r.get('cached')) for r in results + latest)} unchanged, reused")
    (HERE / "uat_results.json").write_text(json.dumps({"run": date.today().isoformat(), "results": results, "latest": latest}, indent=1, default=str))


if __name__ == "__main__":
    main(sys.argv[1:])
