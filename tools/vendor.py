"""Self-host the public sites' small third-party files (scripts, stylesheets, fonts) from pinned versions.

    python tools/vendor.py lock        # after editing site_deps.yml: download each file once, record SHA-384

Used by tools/build_site.py and tools/build_showcase.py at publish time:
    v = Vendor.load()                  # site_deps.yml + site_deps.lock.json + index.yml config.vendor
    page = v.rewrite(page, prefix)     # point a page's pinned CDN URLs at the site's one shared vendor/ folder
    v.materialize(out_dir)             # download what the pages used, check each hash against the lock, write
                                       # vendor/ and vendor/THIRD_PARTY_NOTICES.md (license texts included)

Only deps listed in site_deps.yml are ever copied, and only bytes whose SHA-384 matches the lock are written:
a copy can't be hand-pasted or swapped. A dep that can't be fetched or doesn't match is left on its CDN URL and
reported (the publish gate then decides). Deps over config.vendor.max_self_host_mb stay on the CDN.
Original day snapshots in their repos are never touched; only the published copy is rewritten.

Large deps (over the threshold) stay on the CDN. A <script>/<link> that loads one gets integrity="sha384-…", which
the browser enforces on every visit. A large file that code loads (fetch, new Worker, import(); e.g. DuckDB-WASM's
.wasm and worker) can't carry an integrity attribute, and a <link rel=preload integrity> does not protect a later
fetch(). For those, protection is the nightly check that the CDN still serves the locked bytes (site_check.py):
a changed file blocks the next publish, but visitors in between get whatever the CDN serves. To close that gap,
raise the threshold for that dep's files so they are self-hosted (GitHub Pages allows up to 100 MB per file).
"""
from __future__ import annotations

import base64
import hashlib
import json
import re
import sys
import urllib.request
from dataclasses import dataclass, field
from html.parser import HTMLParser
from pathlib import Path
from urllib.parse import urlparse

import yaml

HERE = Path(__file__).resolve().parent.parent
FOLDER = "vendor"
# Google serves different CSS per browser; lock with one modern Chrome UA so every font is woff2.
FONT_UA = "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/130.0.0.0 Safari/537.36"
GSTATIC = re.compile(r"url\((https://fonts\.gstatic\.com/s/[^)\s]+)\)")
FONT_FILE = re.compile(r"https://fonts\.gstatic\.com/s/[a-z0-9]+/v\d+/[A-Za-z0-9_-]+\.woff2")   # versioned, no ..
SEGMENT = re.compile(r"[A-Za-z0-9_][A-Za-z0-9._-]*")                                     # one safe path segment
FONT_HOSTS = ("fonts.googleapis.com", "fonts.gstatic.com")


def sri(data: bytes) -> str:
    return "sha384-" + base64.b64encode(hashlib.sha384(data).digest()).decode()


def http_get(url: str, ua: str | None = None, tries: int = 3) -> bytes:
    """GET with retries (2 s, 4 s) so one CDN hiccup doesn't block a publish."""
    import time
    for i in range(tries):
        try:
            req = urllib.request.Request(url, headers={"User-Agent": ua or "index-vendor/1"})
            with urllib.request.urlopen(req, timeout=60) as r:
                return r.read()
        except Exception:
            if i == tries - 1:
                raise
            time.sleep(2 ** (i + 1))


def key(d: dict) -> str:
    return f"{d.get('name')}@{d.get('version', '')}".rstrip("@")


def font_local(gurl: str) -> str:
    """fonts.gstatic.com/s/<family>/<vN>/<file> -> fonts/<family>/<vN>/<file> (the version stays in the path)."""
    return "fonts/" + urlparse(gurl).path.removeprefix("/s/")


def validate(deps: list[dict]) -> list[str]:
    """Problems with the dependency list itself; a non-empty result means nothing is vendored."""
    errs, seen = [], set()
    if not isinstance(deps, list):
        return ["deps must be a list"]
    for d in deps:
        if not isinstance(d, dict):
            errs.append(f"{d!r}: each dep must be a mapping")
            continue
        k, url, ver = key(d), d.get("url"), str(d.get("version", ""))
        if not isinstance(url, str) or not url.startswith("https://"):
            errs.append(f"{k}: needs an https url")
            continue
        if not SEGMENT.fullmatch(str(d.get("name", ""))) or ".." in str(d.get("name")):
            errs.append(f"{k}: name must be one safe path segment (letters, digits, . _ -)")
        if d.get("kind") == "google-fonts":
            if not url.startswith("https://fonts.googleapis.com/css2?"):
                errs.append(f"{k}: google-fonts url must be a fonts.googleapis.com/css2 URL")
        else:
            path = urlparse(url).path
            if not SEGMENT.fullmatch(ver) or ".." in ver or not re.search(rf"(?:/|@){re.escape(ver)}(?:/|$)", path):
                errs.append(f"{k}: url must name the pinned version as /{ver}/ or @{ver}")
            if not SEGMENT.fullmatch(path.rsplit("/", 1)[-1]):
                errs.append(f"{k}: url must end in a plain file name")
        lic = d.get("license_files")
        if not d.get("license") or not isinstance(lic, list) or not lic or \
                not all(isinstance(u, str) and u.startswith("https://") for u in lic):
            errs.append(f"{k}: needs license and https license_files")
        if url in seen:
            errs.append(f"{k}: url listed twice")
        seen.add(url)
    return errs


def lock_problems(d: dict, e: dict) -> list[str]:
    """Why a lock entry can't be trusted for this dep (the lock is generated, but a hand edit must not ship)."""
    try:
        errs = []
        if [l["url"] for l in e["license_files"]] != d["license_files"]:
            errs.append("license_files differ from site_deps.yml (re-run lock)")
        if d.get("kind") == "google-fonts":
            f = e["fonts"]
            if not all(FONT_FILE.fullmatch(u) for u in f["files"]):
                errs.append("a font file URL is not a versioned fonts.gstatic.com woff2")
            if set(GSTATIC.findall(f["css"])) != set(f["files"]):
                errs.append("font CSS and font files disagree")
            if sri(f["css"].encode()) != f["css_sha384"]:
                errs.append("font CSS was edited")
            int(e["bytes"]); [int(x["bytes"]) for x in f["files"].values()]
        else:
            int(e["bytes"]); str(e["sha384"])
        return errs
    except (KeyError, TypeError, ValueError, AttributeError) as ex:
        return [f"malformed lock entry ({ex!r})"]


@dataclass
class Vendor:
    deps: list[dict]
    lock: dict
    rewrites: list[dict] = field(default_factory=list)
    max_bytes: int = 5 * 1024 * 1024
    fetch: callable = http_get
    used: set = field(default_factory=set)            # urls of deps some page now loads locally
    hashed: set = field(default_factory=set)          # urls of large deps left on the CDN with an integrity hash
    dropped_fonts: set = field(default_factory=set)   # unlisted Google Fonts urls removed from published copies
    off: bool = False                                 # config failed to load: pages are left exactly as they are
    problems: list = field(default_factory=list)
    _bytes: dict = field(default_factory=dict)        # url -> verified bytes (one download per run)

    @classmethod
    def load(cls, root: Path = HERE, fetch=http_get) -> "Vendor":
        """Never raises: a broken list, lock or config means nothing is vendored (pages stay on their CDN) and a
        problem is reported, so a typo can't stop the nightly refresh."""
        try:
            spec = yaml.safe_load((root / "site_deps.yml").read_text()) or {}
            cfg = ((yaml.safe_load((root / "index.yml").read_text()) or {}).get("config") or {}).get("vendor") or {}
            lock_path = root / "site_deps.lock.json"
            lock = json.loads(lock_path.read_text()) if lock_path.exists() else {}
            v = cls(spec.get("deps") or [], lock, spec.get("rewrites") or [],
                    int(float(cfg.get("max_self_host_mb", 5)) * 1024 * 1024), fetch)
        except Exception as ex:
            return cls([], {}, problems=[f"vendoring off: {ex!r}"[:200]], off=True)
        if errs := validate(v.deps):
            v.problems += [f"site_deps.yml: {e}" for e in errs]
            v.deps = []
            v.off = True
        for d in v.deps:
            if d["url"] in v.lock and (bad := lock_problems(d, v.lock[d["url"]])):
                v.problems.append(f"{key(d)}: lock not used: {'; '.join(bad)}")
                del v.lock[d["url"]]
        return v

    # -------------------------------------------------------------- what may be self-hosted
    def total_bytes(self, d: dict) -> int:
        e = self.lock.get(d["url"]) or {}
        return e.get("bytes", 0) + sum(f["bytes"] for f in (e.get("fonts") or {}).get("files", {}).values())

    def self_hosted(self, d: dict) -> bool:
        return d["url"] in self.lock and self.total_bytes(d) <= self.max_bytes

    def on_cdn(self) -> dict[str, str]:
        """Large deps that stay on their CDN: url -> the SHA-384 integrity value pages must carry."""
        return {d["url"]: self.lock[d["url"]]["sha384"] for d in self.deps
                if d["url"] in self.lock and not self.self_hosted(d) and d.get("kind") != "google-fonts"}

    def local_path(self, d: dict) -> str:
        if d.get("kind") == "google-fonts":
            return f"fonts/{d['name']}.css"
        return f"{d['name']}/{d['version']}/{urlparse(d['url']).path.rsplit('/', 1)[-1]}"

    def _verified(self, url: str, want: str, ua: str | None = None) -> bytes:
        if url not in self._bytes:
            data = self.fetch(url, ua) if ua else self.fetch(url)
            if sri(data) != want:
                raise ValueError(f"{url}: hash does not match the lock (got {sri(data)[:20]}…)")
            self._bytes[url] = data
        return self._bytes[url]

    def _ready(self, d: dict) -> bool:
        """Download and verify everything this dep needs, once; False (and a problem) if anything fails."""
        if d["url"] in self._bytes:
            return True
        e = self.lock[d["url"]]
        try:
            if d.get("kind") == "google-fonts":
                for gurl, f in e["fonts"]["files"].items():
                    self._verified(gurl, f["sha384"])
                self._bytes[d["url"]] = self.font_css(d).encode()
            else:
                self._verified(d["url"], e["sha384"])
            for lf in e.get("license_files", []):
                self._verified(lf["url"], lf["sha384"])
        except Exception as ex:   # network, 404, hash mismatch: leave the page on the CDN and say why
            self.problems.append(f"{key(d)}: not self-hosted: {str(ex)[:160]}")
            self._bytes.pop(d["url"], None)
            return False
        return True

    def font_css(self, d: dict) -> str:
        e = self.lock[d["url"]]["fonts"]
        if sri(e["css"].encode()) != e["css_sha384"]:
            raise ValueError("locked font CSS was edited by hand")
        # the CSS file sits in vendor/fonts/, so font files are referenced relative to it
        return GSTATIC.sub(lambda m: f"url({font_local(m.group(1)).removeprefix('fonts/')})", e["css"])

    # -------------------------------------------------------------- page rewrite
    def rewrite(self, page: str, prefix: str = "") -> str:
        """Point every self-hostable pinned URL in `page` at <prefix>vendor/…; everything else is untouched."""
        base = f"{prefix}{FOLDER}/"
        for d in self.deps:
            if d["url"] in page or html_escape(d["url"]) in page:
                if self.self_hosted(d) and self._ready(d):
                    page, n = swap(page, d["url"], base + self.local_path(d))
                    if n:
                        self.used.add(d["url"])
        for rw in self.rewrites:
            if rw["from"] not in page or rw.get("page_has", "") not in page:
                continue   # page_has: the exact version list the loader can reach, so no version is missed
            need = [d for d in self.deps if key(d) in rw.get("needs", [])]
            if len(need) == len(rw.get("needs", [])) and all(self.self_hosted(d) and self._ready(d) for d in need):
                page = page.replace(rw["from"], rw["to"].replace("{vendor}", base))
                self.used.update(d["url"] for d in need)
        # large files stay on the CDN, pinned by hash: the browser refuses them if the bytes ever change.
        # Checked here too, so a CDN that already serves different bytes is caught before anything is published.
        for d in self.deps:
            if (d["url"] in page or html_escape(d["url"]) in page) and d["url"] in self.on_cdn() and self._ready(d):
                page = add_integrity(page, d["url"], self.lock[d["url"]]["sha384"])
                self.hashed.add(d["url"])
        # Google Fonts that site_deps.yml doesn't list are dropped from the published copy, never fetched: the
        # page's own CSS fallback stack (system fonts) takes over. Listed ones keep the self-host path above.
        dropped: set[str] = set()
        if not self.off:
            page, dropped = drop_unlisted_fonts(page, {d["url"] for d in self.deps if d.get("kind") == "google-fonts"})
        self.dropped_fonts.update(dropped)
        # resource hints for font hosts, once this page's fonts are self-hosted or dropped and nothing else uses them
        fonts_local = bool(dropped) or any(d.get("kind") == "google-fonts" and d["url"] in self.used and
                                           base + self.local_path(d) in page for d in self.deps)
        for host in FONT_HOSTS:
            if fonts_local and f"//{host}/" not in page:
                page = re.sub(rf'<link\b[^>]*rel=["\']?(?:preconnect|dns-prefetch)["\']?[^>]*//{re.escape(host)}/?["\'][^>]*>\s*',
                              "", page, flags=re.I)
                page = re.sub(rf'<link\b[^>]*//{re.escape(host)}/?["\'][^>]*rel=["\']?(?:preconnect|dns-prefetch)["\']?[^>]*>\s*',
                              "", page, flags=re.I)
        return page

    # -------------------------------------------------------------- write vendor/
    def materialize(self, out: Path) -> list[str]:
        """Write the used copies and their license notices into out/vendor/. Returns the files written."""
        root = (out / FOLDER).resolve()
        written = []
        def put(rel: str, data: bytes):
            p = (root / rel).resolve()
            if root not in p.parents:   # names and font paths are validated; this is the last line of defence
                raise ValueError(f"refusing to write outside vendor/: {rel}")
            p.parent.mkdir(parents=True, exist_ok=True)
            p.write_bytes(data)
            written.append(rel)
        notices = ["# Third-party notices", "",
                   "Files in this folder are unmodified copies of the pinned versions below, checked against SHA-384",
                   "hashes recorded when each was added (site_deps.lock.json in leftbrain847/index_engine). The only edit:",
                   "each fonts/*.css (Google's CSS for that page) points its font URLs at the copies in this folder.", "",
                   "| Name | Version | License | Source |", "|---|---|---|---|"]
        texts: dict[str, bytes] = {}
        for d in sorted((d for d in self.deps if d["url"] in self.used), key=key):
            e = self.lock[d["url"]]
            put(self.local_path(d), self._bytes[d["url"]])
            if d.get("kind") == "google-fonts":
                for gurl in e["fonts"]["files"]:
                    put(font_local(gurl), self._bytes[gurl])
            for lf in e.get("license_files", []):
                texts[lf["url"]] = self._bytes[lf["url"]]
            notices.append(f"| {d['name']} | {d.get('version') or 'Google Fonts (pinned files)'} | {d['license']} | {d['url']} |")
        if not written:
            return []
        notices += ["", "## License texts", ""]
        for url, data in texts.items():
            notices += [f"### {url}", "", "```", data.decode(errors="replace").strip(), "```", ""]
        put("THIRD_PARTY_NOTICES.md", "\n".join(notices).encode())
        return written


GFONT_LINK = re.compile(r'<link\b[^>]*\bhref=["\']?(https://fonts\.googleapis\.com/[^"\'\s>]*)[^>]*>\s*', re.I)
GFONT_IMPORT = re.compile(r'@import\s+(?:url\(\s*)?(?:"(https://fonts\.googleapis\.com/[^"]*)"|\'(https://fonts\.googleapis\.com/[^\']*)\'|'
                          r'(https://fonts\.googleapis\.com/[^)\s;]*))\s*\)?[^;]*;\s*', re.I)


def drop_unlisted_fonts(page: str, listed: set[str]) -> tuple[str, set[str]]:
    """Pure: remove Google Fonts stylesheet <link>s and @imports whose URL isn't in `listed`.
    Returns (page, dropped urls). Preconnect hints to the bare host are left for rewrite() to clear."""
    dropped: set[str] = set()
    def cut(m):
        url = next(g for g in m.groups() if g).replace("&amp;", "&")
        if url.rstrip("/") in ("https://fonts.googleapis.com", "") or url in listed:
            return m.group(0)
        dropped.add(url)
        return ""
    page = GFONT_LINK.sub(cut, page)
    page = GFONT_IMPORT.sub(cut, page)
    return page, dropped


def html_escape(url: str) -> str:
    return url.replace("&", "&amp;")


def swap(page: str, url: str, local: str) -> tuple[str, int]:
    """Replace `url` only where it is a whole quoted value or unquoted attribute value (also its &amp; form),
    never inside prose or as the start of a longer URL."""
    n = 0
    for u in dict.fromkeys((url, html_escape(url))):
        rx = re.compile(rf"""(["'`]){re.escape(u)}\1|(=){re.escape(u)}(?=[\s>])""")
        page, k = rx.subn(lambda m: (m.group(1) + local + m.group(1)) if m.group(1) else "=" + local, page)
        n += k
    return page, n


# ------------------------------------------------------------------ integrity + what a page loads
LOAD_RELS = {"stylesheet", "preload", "modulepreload", "prefetch"}
EXTERNAL = r"(?:https?:)?//[^\s\"'`()<>]+"
# loads that aren't tags: CSS @import / font urls, ES module imports, workers
CODE_LOADS = [
    ("css @import", re.compile(rf"""@import\s+(?:url\()?\s*["']?({EXTERNAL})""", re.I)),
    ("css font", re.compile(rf"""url\(\s*["']?({EXTERNAL}\.(?:woff2?|ttf|otf|eot)(?:\?[^"')]*)?)["']?\s*\)""", re.I)),
    ("js import", re.compile(rf"""\bimport\s*(?:[\w*{{}},\s]+from\s*)?\(?\s*["'`]({EXTERNAL})["'`]""")),
    ("js worker", re.compile(rf"""\b(?:new\s+(?:Shared)?Worker|importScripts)\s*\(\s*["'`]({EXTERNAL})["'`]""")),
]


class _Tags(HTMLParser):
    """Every <script>/<link> start tag as the browser would read it (any spacing, quoting, `/` separators,
    `>` inside quotes, SVG <script href>); the first of a repeated attribute wins, as in browsers."""
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.tags: list[tuple[str, dict, str]] = []

    def handle_starttag(self, tag, attrs):
        if tag in ("script", "link"):
            a: dict[str, str] = {}
            for k, v in attrs:
                a.setdefault(k.lower(), v if v is not None else "")
            self.tags.append((tag, a, self.get_starttag_text() or ""))

    handle_startendtag = handle_starttag


def tags(page: str) -> list[tuple[str, dict, str]]:
    p = _Tags()
    p.feed(page)
    p.close()
    return p.tags


def tag_url(name: str, a: dict) -> str | None:
    """The URL a script/link tag makes the browser fetch, if any."""
    if name == "script":
        return a.get("src") or a.get("href") or a.get("xlink:href")
    return a.get("href") if set(a.get("rel", "").lower().split()) & LOAD_RELS else None


def add_integrity(page: str, url: str, value: str) -> str:
    """Add integrity + crossorigin to every <script src=url> / <link href=url> that has no integrity yet."""
    for name, a, raw in tags(page):
        if tag_url(name, a) != url or "integrity" in a or not raw:
            continue
        extra = f' integrity="{value}"' + ("" if "crossorigin" in a else ' crossorigin="anonymous"')
        page = page.replace(raw, re.sub(r"\s*/?>$", lambda e: extra + e.group(0), raw, count=1))
    return page


def external_loads(page: str) -> list[dict]:
    """Every third-party script, stylesheet or font a page's markup asks for: {kind, url, integrity, crossorigin}."""
    out = []
    for name, a, _ in tags(page):
        url = tag_url(name, a)
        if url and re.match(EXTERNAL, url.strip()):
            out.append({"kind": f"<{name}>", "url": url.strip(), "integrity": a.get("integrity"),
                        "crossorigin": "crossorigin" in a})
    for kind, rx in CODE_LOADS:
        out += [{"kind": kind, "url": u, "integrity": None, "crossorigin": False} for u in rx.findall(page)]
    return out


def unapproved(page: str, on_cdn: dict[str, str]) -> list[str]:
    """Why each external load in `page` is neither self-hosted nor pinned by hash (empty = the page may go up).
    A tag must carry the locked integrity value (and crossorigin); a load in code can't carry one, so its exact
    URL must be a locked large dep (its bytes are checked against the lock at publish)."""
    bad = []
    for l in external_loads(page):
        want = on_cdn.get(l["url"])
        if not want:
            bad.append(f"{l['kind']} {l['url']}: not in site_deps.yml (self-host it or pin it by hash)")
        elif l["kind"].startswith("<") and (l["integrity"] != want or not l["crossorigin"]):
            bad.append(f"{l['kind']} {l['url']}: integrity missing or not the locked hash")
    return sorted(set(bad))


# ------------------------------------------------------------------ lock
def lock_entry(d: dict, fetch=http_get) -> dict:
    lic = [{"url": u, "sha384": sri(fetch(u))} for u in d["license_files"]]
    if d.get("kind") == "google-fonts":
        css = fetch(d["url"], FONT_UA).decode()
        files = {}
        for gurl in dict.fromkeys(GSTATIC.findall(css)):
            if not FONT_FILE.fullmatch(gurl):
                raise ValueError(f"{gurl}: not a versioned fonts.gstatic.com woff2 URL")
            data = fetch(gurl)
            files[gurl] = {"sha384": sri(data), "bytes": len(data)}
        return {"bytes": len(css.encode()), "fonts": {"css": css, "css_sha384": sri(css.encode()), "files": files},
                "license_files": lic}
    data = fetch(d["url"])
    return {"sha384": sri(data), "bytes": len(data), "license_files": lic}


def lock(root: Path = HERE, fetch=http_get, refresh: bool = False) -> dict:
    """Add lock entries for new deps; existing entries are kept as they are unless refresh (pins stay pinned)."""
    spec = yaml.safe_load((root / "site_deps.yml").read_text()) or {}
    deps = spec.get("deps") or []
    if errs := validate(deps):
        raise SystemExit("site_deps.yml:\n  " + "\n  ".join(errs))
    path = root / "site_deps.lock.json"
    old = json.loads(path.read_text()) if path.exists() else {}
    new = {}
    for d in deps:
        if d["url"] in old and not refresh and [l["url"] for l in old[d["url"]]["license_files"]] == d["license_files"]:
            new[d["url"]] = old[d["url"]]
        else:
            new[d["url"]] = lock_entry(d, fetch)
            print(f"locked {key(d)}")
    path.write_text(json.dumps(new, indent=1, sort_keys=True) + "\n")
    return new


if __name__ == "__main__":
    if sys.argv[1:2] == ["lock"]:
        lock(refresh="--refresh" in sys.argv)
    else:
        print(__doc__)
