"""Publish gate for a built public site: run on the folder about to be pushed, exit 1 if it must not go up.

    python tools/site_check.py SITE_DIR [--no-browser] [--drop-bad LIST_FILE]

--no-browser  markup checks only (fast; what project repos run on their snapshots in CI).
--drop-bad    contain failures: delete each failing page, write their paths to LIST_FILE, remove links to them
              from the rest of the site (tools/unlink.py), and pass if nothing site-wide failed. One bad page
              then costs one page, not the whole site. If the front page fails, or more than MAX_DROP pages do,
              nothing is dropped and the site stays blocked as before (that looks systemic, not one bad page).

Every published page (not vendor/) must:
1. load no third-party script, stylesheet or font that is neither self-hosted (vendor/) nor pinned by hash:
   - in the markup: <script src>, <link rel=stylesheet|preload|modulepreload|prefetch>, CSS @import and font
     urls, ES module imports and workers (tools/vendor.py `unapproved`);
   - in a real browser (served over local HTTP, both viewports, after tapping controls): every request to another
     host except images, media and embedded pages must be a large dep locked in site_deps.lock.json (its CDN bytes
     re-checked against the lock right here) or a host in config.vendor.allow_data_hosts.
2. still work (UAT on the published copy, uat.py's checks): no JS errors, console errors, failed requests,
   blank render or errors after tapping controls.
"""
from __future__ import annotations

import sys
import threading
from functools import partial
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import quote, urlparse

import yaml

HERE = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(HERE))
sys.path.insert(0, str(HERE / "tools"))
from vendor import FOLDER, Vendor, unapproved  # noqa: E402

# third-party requests a page may make: pictures, audio/video and embedded pages. Anything else (scripts, styles,
# fonts, fetch/XHR, wasm, workers, beacons) must be a locked large dep, or a host in config.vendor.allow_data_hosts.
HARMLESS_TYPES = {"image", "media", "document"}
DATA_TYPES = {"fetch", "xhr"}   # the only request types allow_data_hosts exempts (never scripts, styles, fonts)
# UAT checks a vendoring mistake could trip; layout checks (overflow, tap size, ...) are judged by the nightly UAT
BREAKS = {"js-errors", "blank-render", "interaction-errors", "console-errors", "failed-requests"}


def pages(site: Path) -> list[Path]:
    return sorted(p for p in site.rglob("*.html") if FOLDER not in p.relative_to(site).parts[:1])


def runtime_violations(requests: list[tuple[str, str]], allowed: set[str], own: str = "",
                       data_hosts: set[str] = frozenset()) -> list[str]:
    """Pure: (url, resource type) pairs a page requested -> third-party loads that aren't allowed."""
    return sorted({f"loaded {u} ({t})" for u, t in requests
                   if u.startswith(("http://", "https://")) and not (own and u.startswith(own))
                   and t not in HARMLESS_TYPES and u not in allowed
                   and not (t in DATA_TYPES and urlparse(u).hostname in data_hosts)})


class _Quiet(SimpleHTTPRequestHandler):
    def log_message(self, *a):
        pass


def serve(site: Path) -> ThreadingHTTPServer:
    """Serve the built site over HTTP on localhost, as GitHub Pages will (not file://)."""
    srv = ThreadingHTTPServer(("127.0.0.1", 0), partial(_Quiet, directory=str(site)))
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    return srv


def browse(site: Path, allowed: set[str], data_hosts: set[str]) -> dict[str, list[str]]:
    """UAT on each published copy (both viewports, plus tapping its controls), watching every request it makes."""
    import uat
    cfg = yaml.safe_load((HERE / "uat.yml").read_text())
    cfg["allowed_hosts"] = sorted({urlparse(u).netloc for u in allowed} | data_hosts)
    (HERE / "uat").mkdir(exist_ok=True)
    srv = serve(site)
    origin = f"http://127.0.0.1:{srv.server_address[1]}/"
    out: dict[str, list[str]] = {}
    def one(b, rel: str) -> list[str]:
        seen: list[tuple[str, str]] = []
        issues = uat.run_one(b, origin + quote(rel), "site-check " + rel.replace("/", "_"), cfg, {},
                             on_request=lambda u, t: seen.append((u, t)))
        bad = runtime_violations(seen, allowed, origin, data_hosts)
        return bad + [f"UAT {i['check']}: {i['detail']}" for i in issues if i["check"] in BREAKS]
    try:
        rels = [p.relative_to(site).as_posix() for p in pages(site)]
        results = dict(zip(rels, uat.parallel(one, rels, cfg.get("workers", 1))))   # several pages at once
        # a page that timed out while others loaded beside it gets one more try on its own before it's blocked
        slow = [r for r, bad in results.items() if any(b.startswith("UAT js-errors: page did not load") for b in bad)]
        if slow and cfg.get("workers", 1) > 1:
            print(f"retrying alone: {', '.join(slow)}")
            for r, new in zip(slow, uat.parallel(one, slow, 1)):   # never forget an external load pass 1 saw
                results[r] = sorted(set(new) | {b for b in results[r] if b.startswith("loaded ")})
        out.update({r: bad for r, bad in results.items() if bad})
    finally:
        srv.shutdown()
    return out


def check(site: Path, v: Vendor, browser: bool = True, data_hosts: set[str] | None = None) -> dict[str, list[str]]:
    """Every problem that keeps this site from publishing, by page ("site_deps" = the dependency list)."""
    on_cdn = v.on_cdn()
    texts = {p.relative_to(site).as_posix(): p.read_text(errors="replace") for p in pages(site)}
    for d in v.deps:   # large deps the pages use: the CDN must still serve exactly the locked bytes
        if d["url"] in on_cdn and any(d["url"] in t or d["url"].replace("&", "&amp;") in t for t in texts.values()):
            v._ready(d)   # on failure, v.problems says why
    verified = {u: h for u, h in on_cdn.items() if u in v._bytes}
    problems: dict[str, list[str]] = {"site_deps": list(v.problems)}
    for rel, text in texts.items():
        if bad := unapproved(text, verified):      # a dep whose CDN copy changed counts as not pinned
            problems.setdefault(rel, []).extend(bad)
    if browser:
        for rel, bad in browse(site, set(verified), set(data_hosts or ())).items():
            problems.setdefault(rel, []).extend(bad)
    return {k: sorted(set(b)) for k, b in problems.items() if b}


MAX_DROP = 3                     # more failing pages than this looks systemic (bad dep, flaky browser): block instead
KEEP = {"index.html"}            # never published without its front page


def drop_bad(site: Path, problems: dict[str, list[str]], max_drop: int = MAX_DROP) -> list[str]:
    """Delete every page with its own problems; the site-wide "site_deps" group can't be fixed by dropping.
    Drops nothing (so the whole site stays blocked) if the front page fails or too many pages do."""
    dropped = sorted(rel for rel in problems if rel != "site_deps" and (site / rel).is_file())
    if len(dropped) > max_drop or KEEP & set(dropped):
        return []
    for rel in dropped:
        (site / rel).unlink()
    return dropped


def main(argv: list[str]) -> int:
    import argparse
    ap = argparse.ArgumentParser(prog="site_check.py")
    ap.add_argument("site", type=Path)
    ap.add_argument("--no-browser", action="store_true", help="markup checks only")
    ap.add_argument("--drop-bad", type=Path, metavar="LIST_FILE", help="hold back failing pages, list them here")
    a = ap.parse_args(argv)
    site, browser, drop_to = a.site, not a.no_browser, a.drop_bad
    cfg = ((yaml.safe_load((HERE / "index.yml").read_text()) or {}).get("config") or {}).get("vendor") or {}
    problems = check(site, Vendor.load(), browser=browser, data_hosts=set(cfg.get("allow_data_hosts") or []))
    if drop_to is not None:
        from unlink import unlink
        dropped = drop_bad(site, problems)
        drop_to.write_text("".join(f"{r}\n" for r in dropped))
        unlink(site, dropped)
        for rel in dropped:
            for b in problems.pop(rel):
                print(f"::warning::{site.name}/{rel} held back: {b}")
        if dropped:
            print(f"held back {len(dropped)} page(s) from {site.name}; the rest can publish")
    n = len(pages(site))
    if not problems:
        works = "pages work" if browser else "markup only, not opened in a browser"
        print(f"site check passed: {n} pages; third-party files self-hosted or pinned by hash; {works}")
        return 0
    for where, bad in problems.items():
        for b in bad:
            print(f"::error::{site.name}/{where}: {b}")
    print(f"site check FAILED for {site.name}: {len(problems)} problem group(s); this site is not published")
    return 1


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
