"""Integrity hashes for large CDN files, and the publish gate (tools/vendor.py + tools/site_check.py).
No network needed: fetch is faked; the browser test passes offline too."""
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "tools"))
sys.path.insert(0, str(ROOT))
from site_check import check, runtime_violations  # noqa: E402
from vendor import Vendor, add_integrity, external_loads, lock_entry, sri, unapproved  # noqa: E402

DUCK = "https://cdn.jsdelivr.net/npm/@duckdb/duckdb-wasm@1.29.0/dist/duckdb-browser.mjs"   # a large dep
LIC = "https://cdn.jsdelivr.net/npm/@duckdb/duckdb-wasm@1.29.0/LICENSE"
BYTES = {DUCK: b"export const duck = 1;" * 100, LIC: b"MIT"}
DEP = {"name": "duckdb-wasm", "version": "1.29.0", "url": DUCK, "license": "MIT", "license_files": [LIC]}


def vendor(files=BYTES, max_bytes=10):
    fetch = lambda url, ua=None: files[url]
    return Vendor([DEP], {DUCK: lock_entry(DEP, lambda u, ua=None: BYTES[u])}, [], max_bytes, fetch)


def test_large_files_stay_on_the_cdn_with_the_locked_hash():
    v = vendor()
    out = v.rewrite(f'<script type="module" src="{DUCK}"></script>')
    assert f'src="{DUCK}" integrity="{sri(BYTES[DUCK])}" crossorigin="anonymous"' in out
    assert DUCK in v.hashed and unapproved(out, v.on_cdn()) == []


def test_cdn_serving_different_bytes_gets_no_hash_and_fails_the_gate():
    v = vendor(files={**BYTES, DUCK: b"changed upstream"})
    out = v.rewrite(f'<script src="{DUCK}"></script>')
    assert "integrity" not in out and v.problems
    assert unapproved(out, v.on_cdn())                     # the gate refuses the page


def test_existing_integrity_and_crossorigin_are_kept():
    page = f"<link rel=modulepreload href='{DUCK}' crossorigin=use-credentials integrity='sha384-old'>"
    assert add_integrity(page, DUCK, "sha384-new") == page
    page2 = f'<script src="{DUCK}" crossorigin></script>'
    assert add_integrity(page2, DUCK, "sha384-new").count("crossorigin") == 1


def test_gate_rejects_every_external_load_that_is_neither_self_hosted_nor_hashed():
    on_cdn = {DUCK: "sha384-good"}
    page = (f'<script src="{DUCK}" integrity="sha384-good" crossorigin="anonymous"></script>'   # ok
            '<script src="vendor/three/r128/three.min.js"></script>'                             # ok, local
            '<link rel="preconnect" href="https://fonts.gstatic.com">'                           # a hint, not a load
            '<a href="https://example.com/lib.js">link</a>'                                      # a link, not a load
            '<script src="https://unpkg.com/x@1/x.js"></script>'
            f'<script src="{DUCK}"></script>'                                                    # no hash
            f'<script src="{DUCK}" integrity="sha384-evil" crossorigin></script>'                # wrong hash
            '<link rel="stylesheet" href="//fonts.googleapis.com/css2?family=X">'
            '<style>@import url("https://cdn.x/a.css"); @font-face{src:url(https://cdn.x/f.woff2)}</style>'
            '<script type=module>import * as d from "https://esm.sh/d3@7";'
            f'new Worker("https://cdn.x/w.js"); import("{DUCK}")</script>')                        # dynamic + locked: ok
    bad = unapproved(page, on_cdn)
    assert len(bad) == 7, bad
    assert not any("example.com" in b or "fonts.gstatic" in b or "import " + DUCK in b for b in bad)


def test_amp_escaped_urls_are_recognised():
    page = '<link rel="stylesheet" href="https://fonts.googleapis.com/css2?family=A&amp;display=swap">'
    assert external_loads(page)[0]["url"] == "https://fonts.googleapis.com/css2?family=A&display=swap"


def test_runtime_check_allows_own_site_hashed_files_and_pictures_only():
    reqs = [("http://127.0.0.1:5/vendor/x.js", "script"), (DUCK, "script"), ("https://img.x/a.png", "image"),
            ("https://cdn.x/duckdb-eh.wasm", "fetch"), ("https://api.x/data.json", "fetch"),
            ("https://cdn.jsdelivr.net/npm/lodash@4.17.21", "fetch"),            # no extension, still code
            ("https://fonts.gstatic.com/s/a/v1/a.woff2", "font")]
    assert runtime_violations(reqs, {DUCK}, "http://127.0.0.1:5/") == [
        "loaded https://api.x/data.json (fetch)", "loaded https://cdn.jsdelivr.net/npm/lodash@4.17.21 (fetch)",
        "loaded https://cdn.x/duckdb-eh.wasm (fetch)", "loaded https://fonts.gstatic.com/s/a/v1/a.woff2 (font)"]
    assert runtime_violations(reqs, {DUCK}, "http://127.0.0.1:5/", {"api.x"}) == [   # an allowed data API
        "loaded https://cdn.jsdelivr.net/npm/lodash@4.17.21 (fetch)", "loaded https://cdn.x/duckdb-eh.wasm (fetch)",
        "loaded https://fonts.gstatic.com/s/a/v1/a.woff2 (font)"]
    code = [("https://api.x/evil.js", "script"), ("https://api.x/s.css", "stylesheet"), ("https://api.x/f.woff2", "font")]
    assert runtime_violations(code, set(), "", {"api.x"}) == [   # a data host never exempts code, styles or fonts
        "loaded https://api.x/evil.js (script)", "loaded https://api.x/f.woff2 (font)", "loaded https://api.x/s.css (stylesheet)"]


def test_markup_scan_reads_tags_the_way_a_browser_does():
    page = ('<script/src=https://a.x/1.js></script>'
            '<script data-x=" src=local.js" src=https://b.x/2.js></script>'
            '<link rel=stylesheet title="a>b" href=//c.x/3.css>'
            '<svg><script href="https://d.x/4.js"/></svg>'
            '<script src="vendor/ok.js" SRC="https://e.x/5.js"></script>')          # first attribute wins
    assert sorted(l["url"] for l in external_loads(page)) == [
        "//c.x/3.css", "https://a.x/1.js", "https://b.x/2.js", "https://d.x/4.js"]


def test_published_copies_are_opened_in_a_browser_and_bad_pages_fail(tmp_path):
    site = tmp_path / "site"
    (site / "vendor/lib/1.0.0").mkdir(parents=True)
    (site / "vendor/lib/1.0.0/lib.js").write_text("window.LIB = 1;")
    good = ('<!doctype html><html><head><meta name="viewport" content="width=device-width"><title>Good</title>'
            '<script src="vendor/lib/1.0.0/lib.js"></script></head><body><h1>Works</h1>'
            '<p id=o>waiting for the library to load</p>'
            '<script>document.getElementById("o").textContent = "library " + LIB + " loaded fine";</script></body></html>')
    (site / "good.html").write_text(good)
    (site / "sub").mkdir()
    (site / "sub/index.html").write_text(good.replace("vendor/", "../vendor/"))
    # the code builds the URL, so only the browser pass can see it
    (site / "sneaky.html").write_text(good.replace("</body>", '<script>const s = document.createElement("script");'
                                                   's.src = "https://cdn" + ".invalid/x.js"; document.head.append(s);</script></body>'))
    (site / "broken.html").write_text(good.replace("vendor/lib/1.0.0/lib.js", "vendor/missing.js"))
    # loads only when tapped, and with no file extension
    (site / "tap.html").write_text(good.replace("</body>", '<button onclick="fetch(\'https://cdn.invalid/lodash@4\')'
                                                '.catch(() => {})">Load more</button></body>'))
    # a large dep whose CDN copy no longer matches the lock: its hash can't be trusted, so the page fails
    big = vendor(files={**BYTES, DUCK: b"changed upstream"})
    (site / "big.html").write_text(good.replace("</head>", f'<script type="module" src="{DUCK}" '
                                                f'integrity="{sri(BYTES[DUCK])}" crossorigin="anonymous"></script></head>'))
    problems = check(site, big)
    assert set(problems) == {"sneaky.html", "broken.html", "tap.html", "big.html", "site_deps"}, problems
    assert any("cdn.invalid" in p for p in problems["sneaky.html"])
    assert any("js-errors" in p or "failed-requests" in p for p in problems["broken.html"])
    assert any("cdn.invalid/lodash@4" in p for p in problems["tap.html"])
    assert any("not in site_deps.yml" in p for p in problems["big.html"])
    assert "good.html" not in problems and "sub/index.html" not in problems


def test_a_page_that_times_out_in_parallel_gets_one_retry_alone(tmp_path, monkeypatch):
    import site_check
    import uat
    (tmp_path / "a.html").write_text("<p>a</p>")
    (tmp_path / "b.html").write_text("<p>b</p>")
    calls = []

    def fake_parallel(fn, items, workers, launch=None):
        calls.append((list(items), workers))
        if workers > 1:   # first pass: b times out
            return [[] if i == "a.html" else ["UAT js-errors: page did not load: Timeout"] for i in items]
        return [[] for _ in items]   # alone, it loads
    monkeypatch.setattr(uat, "parallel", fake_parallel)
    assert site_check.browse(tmp_path, set(), set()) == {}
    assert calls[1] == (["b.html"], 1)



def test_a_retry_keeps_blocking_a_page_that_fails_again_and_remembers_external_loads(tmp_path, monkeypatch):
    import site_check
    import uat
    (tmp_path / "a.html").write_text("<p>a</p>")
    (tmp_path / "b.html").write_text("<p>b</p>")
    timeout = "UAT js-errors: page did not load: Timeout"

    def fake_parallel(fn, items, workers, launch=None):
        if workers > 1:
            return [[timeout, "loaded https://evil.example/x.js (script)"] if i == "a.html" else [timeout] for i in items]
        return [[] if i == "a.html" else [timeout] for i in items]   # alone: a loads cleanly, b still fails
    monkeypatch.setattr(uat, "parallel", fake_parallel)
    out = site_check.browse(tmp_path, set(), set())
    assert out["a.html"] == ["loaded https://evil.example/x.js (script)"]   # the load it saw isn't forgotten
    assert out["b.html"] == [timeout]
