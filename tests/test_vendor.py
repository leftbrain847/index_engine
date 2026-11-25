"""Self-hosting pinned third-party files on the public sites (tools/vendor.py). No network: fetch is faked."""
import json
import sys
from pathlib import Path

import pytest
import yaml

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "tools"))
from vendor import Vendor, lock, lock_entry, sri, swap, validate  # noqa: E402

LIB = "https://cdn.example/lib/1.2.3/lib.min.js"
BIG = "https://cdn.example/big/9.0.0/big.js"
FONT = "https://fonts.googleapis.com/css2?family=Test+Sans&display=swap"
WOFF = "https://fonts.gstatic.com/s/testsans/v7/abc-1_X.woff2"
LIC = "https://cdn.example/lib/1.2.3/LICENSE"
CSS = f"@font-face {{ font-family: 'Test Sans'; src: url({WOFF}) format('woff2'); }}"
FILES = {LIB: b"window.lib=1", BIG: b"x" * 2000, WOFF: b"wOF2-bytes", LIC: b"MIT License text", FONT: CSS.encode()}
DEPS = [
    {"name": "lib", "version": "1.2.3", "url": LIB, "license": "MIT", "license_files": [LIC]},
    {"name": "big", "version": "9.0.0", "url": BIG, "license": "MIT", "license_files": [LIC]},
    {"name": "fonts-test", "kind": "google-fonts", "url": FONT, "license": "OFL-1.1", "license_files": [LIC]},
]


def fake_fetch(files):
    calls = []
    def fetch(url, ua=None):
        calls.append(url)
        if url not in files:
            raise OSError(f"404 {url}")
        return files[url]
    fetch.calls = calls
    return fetch


def make(files=FILES, max_bytes=1000, rewrites=()):
    f = fake_fetch(FILES)
    lk = {d["url"]: lock_entry(d, f) for d in DEPS}           # locked against the real bytes
    return Vendor(list(DEPS), lk, list(rewrites), max_bytes, fake_fetch(files))


PAGE = (f'<head><link rel="preconnect" href="https://fonts.googleapis.com"><link rel="preconnect" '
        f'href="https://fonts.gstatic.com" crossorigin><link rel="stylesheet" href="{FONT}"></head>'
        f'<script src="{LIB}"></script><script src="{BIG}"></script><a href="https://example.com/">x</a>')


def test_small_pinned_files_are_rewritten_to_the_one_vendor_folder():
    v = make()
    out = v.rewrite(PAGE)
    assert f'src="vendor/lib/1.2.3/lib.min.js"' in out
    assert 'href="vendor/fonts/fonts-test.css"' in out
    assert "preconnect" not in out                       # no font host left, so the hints go too
    assert f'src="{BIG}"' in out                          # over the threshold: stays on the CDN
    assert 'href="https://example.com/"' in out           # ordinary links are not loads
    assert make().rewrite(PAGE, prefix="../").count('"../vendor/') == 2   # pages one folder down


def test_a_copy_that_does_not_match_the_lock_is_never_used():
    tampered = {**FILES, LIB: b"window.lib=evil"}
    v = make(files=tampered)
    out = v.rewrite(PAGE)
    assert f'src="{LIB}"' in out and LIB not in v.used
    assert any("hash does not match" in p for p in v.problems)


def test_unreachable_file_leaves_the_page_on_its_cdn():
    v = make(files={k: b for k, b in FILES.items() if k != WOFF})
    out = v.rewrite(PAGE)
    assert FONT in out and "preconnect" in out
    assert any("fonts-test" in p for p in v.problems)


def test_each_file_is_downloaded_once_per_run():
    v = make()
    v.rewrite(PAGE)
    v.rewrite(PAGE)
    assert v.fetch.calls.count(LIB) == 1


def test_materialize_writes_copies_font_files_and_license_notices(tmp_path):
    v = make()
    v.rewrite(PAGE)
    written = v.materialize(tmp_path)
    vd = tmp_path / "vendor"
    assert (vd / "lib/1.2.3/lib.min.js").read_bytes() == FILES[LIB]
    assert (vd / "fonts/testsans/v7/abc-1_X.woff2").read_bytes() == FILES[WOFF]
    css = (vd / "fonts/fonts-test.css").read_text()
    assert "url(testsans/v7/abc-1_X.woff2)" in css and "gstatic" not in css   # relative to the CSS file
    notes = (vd / "THIRD_PARTY_NOTICES.md").read_text()
    assert "MIT License text" in notes and "| lib | 1.2.3 | MIT |" in notes
    assert "big" not in {p.split("/")[0] for p in written}               # not used locally, not copied


def test_nothing_used_writes_nothing(tmp_path):
    v = make()
    v.rewrite("<p>no third-party files</p>")
    assert v.materialize(tmp_path) == [] and not (tmp_path / "vendor").exists()


def test_templated_loader_is_swapped_only_when_every_version_it_can_reach_is_vendored():
    rw = [{"from": "https://cdn.example/lib/${v}/lib.min.js", "to": "{vendor}lib/${v}/lib.min.js",
           "needs": ["lib@1.2.3"], "page_has": "const V = ['1.2.3'];"}]
    page = "const V = ['1.2.3']; s.src = `https://cdn.example/lib/${v}/lib.min.js`"
    assert "`vendor/lib/${v}/lib.min.js`" in make(rewrites=rw).rewrite(page)
    rw_missing = [{**rw[0], "needs": ["lib@1.2.3", "lib@0.9.0"]}]          # 0.9.0 isn't in the list
    assert make(rewrites=rw_missing).rewrite(page) == page
    other = page.replace("['1.2.3']", "['1.2.3', '2.0.0']")                 # the page can reach a version we lack
    assert make(rewrites=rw).rewrite(other) == other


def test_dependency_list_must_pin_versions_and_licenses():
    errs = validate([
        {"name": "a", "version": "1.0.0", "url": "https://cdn.example/a/latest/a.js", "license": "MIT", "license_files": ["https://l"]},
        {"name": "b", "version": "1.0.0", "url": "http://cdn.example/b/1.0.0/b.js", "license": "MIT", "license_files": ["https://l"]},
        {"name": "c", "version": "2.0.0", "url": "https://cdn.example/c/2.0.0/c.js"},
        {"name": "f", "kind": "google-fonts", "url": "https://evil.example/css2?x", "license": "OFL", "license_files": ["https://l"]},
    ])
    assert len(errs) == 4
    sneaky = [{"name": "../..", "version": "1.0.0", "url": "https://cdn.example/x/1.0.0/a.js", "license": "MIT", "license_files": ["https://l"]},
              {"name": "y", "version": "1", "url": "https://cdn.example/y/11.0.0/a.js", "license": "MIT", "license_files": ["https://l"]},
              {"name": "z", "version": "1.0.0", "url": "https://cdn.example/z/1.0.0/", "license": "MIT", "license_files": ["https://l"]},
              {"version": "1"}, "not a mapping"]
    assert len(validate(sneaky)) == 5       # traversal, version only as a substring, no file name, no url, junk


def test_broken_config_never_crashes_the_build_it_just_vendors_nothing(tmp_path):
    (tmp_path / "index.yml").write_text("config: {vendor: {max_self_host_mb: lots}}")
    (tmp_path / "site_deps.yml").write_text(yaml.safe_dump({"deps": DEPS}))
    v = Vendor.load(tmp_path)
    assert v.deps == [] and v.problems and v.rewrite(PAGE) == PAGE
    (tmp_path / "index.yml").write_text("config: {}")
    (tmp_path / "site_deps.yml").write_text("deps: [{url: 3}]")
    assert Vendor.load(tmp_path).deps == []


def test_a_hand_edited_lock_entry_is_ignored(tmp_path):
    (tmp_path / "index.yml").write_text("config: {}")
    (tmp_path / "site_deps.yml").write_text(yaml.safe_dump({"deps": DEPS}))
    f = fake_fetch(FILES)
    lk = {d["url"]: lock_entry(d, f) for d in DEPS}
    evil = "https://fonts.gstatic.com/s/../../../index.html"
    lk[FONT]["fonts"]["files"][evil] = {"sha384": "x", "bytes": 1}
    lk[LIB]["license_files"] = []
    (tmp_path / "site_deps.lock.json").write_text(json.dumps(lk))
    v = Vendor.load(tmp_path)
    assert FONT not in v.lock and LIB not in v.lock and BIG in v.lock
    assert len(v.problems) == 2


def test_swap_touches_only_whole_values():
    page = (f'<script src="{LIB}"></script><link href={LIB}> see {LIB} or "{LIB}?v=2" '
            f'<link href="{FONT.replace("&", "&amp;")}">')
    out, n = swap(page, LIB, "vendor/l.js")
    assert n == 2 and f"see {LIB} or" in out and f'"{LIB}?v=2"' in out
    url = "https://fonts.googleapis.com/css2?family=A&display=swap"
    out, n = swap(f'<link href="{url.replace("&", "&amp;")}">', url, "vendor/f.css")
    assert n == 1 and out == '<link href="vendor/f.css">'


def test_lock_keeps_existing_pins_and_adds_new_ones(tmp_path):
    (tmp_path / "site_deps.yml").write_text(yaml.safe_dump({"deps": DEPS[:1]}))
    first = lock(tmp_path, fake_fetch(FILES))
    changed = {**FILES, LIB: b"window.lib=2"}                # upstream changed the bytes behind the URL
    (tmp_path / "site_deps.yml").write_text(yaml.safe_dump({"deps": DEPS[:2]}))
    second = lock(tmp_path, fake_fetch(changed))
    assert second[LIB] == first[LIB] and BIG in second       # the pin did not move


def test_showcase_cannot_take_the_vendor_folder_name():
    from build_showcase import plan
    ok, held = plan([{"key": "vendor", "title": "V", "tagline": "t", "url": "https://x.example/"}], {})
    assert ok == [] and held


def test_font_files_must_be_versioned_urls():
    css = "src: url(https://fonts.gstatic.com/s/testsans/abc.woff2)"
    with pytest.raises(ValueError):
        lock_entry(DEPS[2], fake_fetch({**FILES, FONT: css.encode()}))


def test_repo_dependency_list_is_valid_and_fully_locked():
    spec = yaml.safe_load((ROOT / "site_deps.yml").read_text())
    lk = json.loads((ROOT / "site_deps.lock.json").read_text())
    assert validate(spec["deps"]) == []
    for d in spec["deps"]:
        assert d["url"] in lk, f"{d['name']}: run python tools/vendor.py lock"
        assert [l["url"] for l in lk[d["url"]]["license_files"]] == d["license_files"]
        fonts = lk[d["url"]].get("fonts")
        if fonts:
            assert sri(fonts["css"].encode()) == fonts["css_sha384"]
    names = {f"{d['name']}@{d.get('version', '')}" for d in spec["deps"]}
    for rw in spec.get("rewrites") or []:
        assert set(rw["needs"]) <= names


UNLISTED = "https://fonts.googleapis.com/css2?family=Other+Sans:wght@600;700&display=swap"


def test_unlisted_google_fonts_are_dropped_so_pages_fall_back_to_system_fonts():
    page = (f'<head><link rel="preconnect" href="https://fonts.googleapis.com">'
            f'<link rel="preconnect" href="https://fonts.gstatic.com" crossorigin>'
            f'<link rel="stylesheet" href="{UNLISTED.replace("&", "&amp;")}">'
            f'<style>@import url("{UNLISTED}");\nbody{{font-family:"Other Sans",system-ui,sans-serif}}</style></head>')
    v = make()
    out = v.rewrite(page)
    assert "fonts.googleapis.com" not in out and "fonts.gstatic.com" not in out   # links, import and hints gone
    assert 'font-family:"Other Sans",system-ui,sans-serif' in out                  # the page's own fallback stays
    assert v.dropped_fonts == {UNLISTED}
    assert not v.used                                                             # nothing fetched or vendored


def test_listed_google_fonts_still_take_the_self_host_path():
    v = make()
    out = v.rewrite(f'<link rel="stylesheet" href="{FONT}"><link rel="stylesheet" href="{UNLISTED}">')
    assert 'href="vendor/fonts/fonts-test.css"' in out and UNLISTED not in out
    assert v.dropped_fonts == {UNLISTED}
