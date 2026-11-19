"""Build the public homepage (GitHub Pages user site): every project J chooses to show, well described,
with a screenshot, plus the November Sprint as one section.

    python tools/build_showcase.py SCAN_DIR OUT_DIR

Opt-in: only entries in index.yml `showcase:` appear. Each entry is one of:
- file: a single HTML page copied onto the site at /<key>/ (an artifact export kept in this repo, or a repo file)
- url:  a page that is already public (linked, never copied)
  preview: (url entries) a file in the entry's repo to take the tile picture from instead of the url, for links a
  headless browser can't render signed out (e.g. a claude.ai artifact). Pictured only, never published.
A copied page goes up only if it matches none of control.py's secret patterns and loads in Chromium without
a JavaScript error. A Code link appears only for repos that are public on GitHub; private source is never
linked. Screenshots are taken here, at phone size, so the tiles always show the current build.

Also lists every build the public sprint site published (its builds.json, third argument), linked to its
current version (the sprint log's latest_file / latest_url), or its frozen day build when that is all there is.
Every tile opens its build's own page (/builds/<key>/), which opens the build (new tab) and its sprint day build when it has one. Tile text for those comes only from public-facing fields on the repo's `projects:` entry:
showcase_what and showcase_tags (the `what` notes are internal and never published); theme picks the filter.

Per file entry: assets: <dir> copies the media (and in-browser model) files under <dir> in the entry's repo (merged main only, binary-safe)
next to its page, at /<key>/<dir>/, so a page can load e.g. audio/<word>.mp3 from its own site. Only ASSETS["ext"]
types, each under ASSETS["max_file_mb"], at most ASSETS["max_entry_mb"] per entry; anything else holds the assets back
(the page still goes up). They're left out of the offline copy. tools/sprint_files.py lists them so the nightly
refresh fetches them in full (the clone skips files over 1 MB).

Layout is config, not code (config.showcase):
- sprint_section: section key for every sprint build tile (else each project's theme; unset by default)
- theme_sections: {theme: section} maps a project theme to a topic shelf (e.g. audio: music); unmapped themes keep their name
- sprint_titles: {key: title} display names for sprint build tiles; the sprint log keeps the day names
- sprint_sections: {key: section} puts single sprint builds on a given topic shelf (e.g. music)
- featured: [key, ...]  pinned row at the top, in this order. Keys are showcase keys or a sprint build's
  project name in lowercase_with_underscores (e.g. pinball, 4th_down). Reorder the list to reorder the row.
- layout: tile_min_px, thumb_ratio (3/4 = portrait tiles), latest_count, show_latest
- sections: the topic shelves under Featured and Latest, in order ({key, title}); a topic not listed gets its own shelf at the end
Page: Featured, then Latest, then one sideways shelf per topic. Search swaps the shelves for a grid of matches.
Collections (/collections/, config.showcase.collections: [{key, title, blurb, color, builds: [{key, why}]}], keys are
tile keys) and Daily builds (/daily/), both in tools/site_pages.py. Daily builds: every published sprint build, described by its homepage tile, with a
year heatmap; the page tabs show only once it exists.
Per entry: thumb_y skips that many pixels from the top of the page in the screenshot (e.g. past a header).
Per entry: thumb picks the tile's picture: {hash: eigen, selector: "#stage", wait_ms: 600} opens the page at #hash,
waits wait_ms (default the usual settle), and shoots that element (centred, cut to the tile shape).
Every tile gets two pictures, taken in light and dark mode; the page shows the one matching the viewer's setting.
Clips: every tile with a picture (except `preview` ones, whose page is never published) gets a short dark-mode video. Its demo script is the showcase entry's `clip:`, else
`demo/clip.json` on the repo's default branch, else DEFAULT_CLIP (the page left running, at the picture's hash);
`clip: false` opts out. One plays at a time: the top-left tile in the first third of the screen below the header (or the tile you point
at), once, holding its last frame; scrolling or swiping a row hands it to whichever tile slides into that spot.
Add ?uat to the page address to outline the autoplay zone and the playing tile. Script: {seconds: 4-9, hash, selector, steps: [{wait: ms} | {tap: css} | {type: [css, text]}
| {key: name} | {select: [css, value]} | {scroll: px} | {hold: ms} (press at the centre) | {tapAt: [x, y]} | {start: true}]}.
config.showcase.clips {key: script} overrides any tile's script (then the entry's `clip:`, the repo file, the default); steps before `start` set the scene and aren't shown. A clip is re-recorded only when
its script changes, when the page changed (a remote url page only counts as changed when its script does), or when the entry says `rerecord: true`; otherwise last night's copy is reused from the
live site (vid/manifest.json, trusted only in its exact expected shape). Sprint tiles read their repo's demo/clip.json.

    python tools/build_showcase.py SCAN_DIR OUT_DIR [SPRINT_SITE_DIR]
"""
from __future__ import annotations

import hashlib
import html
import json
import math
import time
import urllib.parse
import urllib.request
import re
import shutil
import subprocess
import tempfile
import sys
from datetime import date, timedelta
from pathlib import Path

import yaml

HERE = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(HERE))
from control import SECRET_PATTERNS  # noqa: E402
from uat import as_artifact_host, read_file  # noqa: E402
from vendor import Vendor  # noqa: E402
from site_pages import BASE_CSS, CASE, COLLECTIONS, DAILY, NAV, case_href, collections_plan, daily_items, render_case, render_collections, render_daily, tabs  # noqa: E402,F401  (NAV, tabs: one look for every page)

SHOT = {"width": 390, "height": 640, "settle_ms": 2500}
from assets import ASSETS, NO_LAZY, asset_files  # noqa: E402  (dependency-free: sprint_files.py uses it before tools are installed)


def copy_assets(scan: Path, repo: str, folder: str, dest: Path) -> tuple[int, str | None]:
    """Copy an entry's media folder next to its page. Returns (files copied, reason held back or None)."""
    bare = scan / f"{repo}.git"
    files = asset_files(bare, folder)
    if not files:
        return 0, f"assets: no {', '.join(ASSETS['ext'])} files under {folder}/"
    blobs, total = {}, 0
    for path in files:   # all or nothing: a page must not get half its audio
        r = subprocess.run(["git", "-C", str(bare), "cat-file", "blob", f"HEAD:{path}"], capture_output=True, env=NO_LAZY)
        if r.returncode != 0:
            return 0, f"assets: {path} not fetched in full (tools/sprint_files.py should list it)"
        if len(r.stdout) > ASSETS["max_file_mb"] * 1e6:
            return 0, f"assets: {path} is over {ASSETS['max_file_mb']} MB"
        total += len(r.stdout)
        if total > ASSETS["max_entry_mb"] * 1e6:
            return 0, f"assets: over the {ASSETS['max_entry_mb']} MB limit per entry"
        blobs[path] = r.stdout
    root = folder.strip("/")
    for path, data in blobs.items():
        target = dest / root / Path(path).relative_to(root)
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(data)
    return len(blobs), None


LAYOUT = {"tile_min_px": 150, "thumb_ratio": "4/3", "latest_count": 8, "show_latest": True}   # config.showcase.layout overrides


def layout(cfg: dict) -> dict:
    """config.showcase.layout over the defaults; a bad value falls back instead of breaking the build."""
    lay = {**LAYOUT, **(cfg.get("layout") or {})}
    if not re.fullmatch(r"\d+(\.\d+)?/\d+(\.\d+)?", str(lay["thumb_ratio"]).replace(" ", "")) or "/0" in str(lay["thumb_ratio"]):
        print(f"::warning::showcase: layout.thumb_ratio {lay['thumb_ratio']!r} is not like 4/3; using 4/3")
        lay["thumb_ratio"] = "4/3"
    lay["thumb_ratio"] = str(lay["thumb_ratio"]).replace(" ", "")
    for k, lo, hi in (("tile_min_px", 100, 400), ("latest_count", 0, 30)):
        try:
            lay[k] = max(lo, min(hi, int(lay[k])))
        except (TypeError, ValueError):
            lay[k] = LAYOUT[k]
    return lay


def slug(s: str) -> str:
    return re.sub(r"[^a-z0-9_]+", "_", s.lower()).strip("_")   # lowercase_with_underscores


def secrets_in(text: str) -> list[str]:
    return sorted({k for k, rx in SECRET_PATTERNS.items() if re.search(rx, text)})


RESERVED = {"vendor", "img", "vid", DAILY["path"], COLLECTIONS["path"], CASE["path"]}   # folders the site itself uses


def plan(items: list[dict], meta: dict) -> tuple[list[dict], list[tuple[str, str]]]:
    """Pure: which entries can be shown and how they link; anything malformed is held back with a reason."""
    ok, held = [], []
    seen = set()
    for it in items or []:
        key = slug(it.get("key") or it.get("title", ""))
        if not key or key in seen or key in RESERVED:   # the site's own folders
            held.append((it.get("title", "?"), "missing or duplicate key"))
            continue
        seen.add(key)
        if not it.get("title") or not it.get("tagline"):
            held.append((key, "needs a title and a one-line tagline"))
            continue
        if bool(it.get("file")) == bool(it.get("url")):
            held.append((key, "needs exactly one of file or url"))
            continue
        if it.get("url") and not (it["url"].startswith("https://") or it["url"].startswith("/")):
            held.append((key, "url must be https:// or a site path"))
            continue
        repo = it.get("repo")
        public = bool(repo) and (meta.get(repo) or {}).get("visibility") == "public"
        ok.append({**it, "key": key, "href": f"{key}/" if it.get("file") else it["url"], "date": str(it.get("date") or ""),
                   "code": f"https://github.com/{it['owner']}/{repo}" if public and it.get("owner") else None})
    return ok, held


def sprint_builds(rows: list[dict], projects: list[dict], entries: list[dict], meta: dict, owner: str,
                  base: str, titles: dict | None = None, section: str | None = None,
                  sections: dict | None = None) -> tuple[list[dict], dict[str, str]]:
    """Pure: one tile per sprint project (its newest build, current version preferred), skipping projects a
    showcase entry already covers (same repo). `titles` renames tiles by key (display only: the sprint log and its
    frozen day names stay as they are); `section` puts every sprint tile in one section instead of its theme;
    `sections` ({key: section}) moves single builds out of it (they still list under the Sprint filter, by day).
    Returns the tiles and repo -> newest build date for every row."""
    by_repo = {p.get("repo"): p for p in projects or []}
    covered = {e.get("repo") for e in entries if e.get("repo")}
    newest: dict[str, str] = {}
    best: dict[str, dict] = {}
    for r in rows or []:
        if not all(r.get(f) for f in ("project", "date", "href")):
            continue
        r = {**r, "current": bool(r.get("current"))}
        if r.get("repo"):
            newest[r["repo"]] = max(newest.get(r["repo"], ""), r["date"])
        if r.get("repo") in covered:
            continue
        k = slug(r["project"])
        cur = best.get(k)
        if cur is None or (r["current"], r["date"]) > (cur["current"], cur["date"]):
            best[k] = r
    taken = {e["key"] for e in entries} | RESERVED
    tiles = []
    seen_projects: dict[str, str] = {}
    for r in rows or []:   # two different project names that slug to one key would silently merge
        if r.get("project"):
            k = slug(r["project"])
            if seen_projects.setdefault(k, r["project"]) != r["project"]:
                print(f"::warning::showcase: sprint projects {seen_projects[k]!r} and {r['project']!r} share the key {k}; showing one")
    for k, r in best.items():
        p = by_repo.get(r.get("repo")) or {}
        key = k if k not in taken else f"{k}_build"
        taken.add(key)
        href = r["href"] if r["href"].startswith("https://") else base.rstrip("/") + "/" + r["href"]
        public = (meta.get(r.get("repo")) or {}).get("visibility") == "public"
        tiles.append({"key": key, "title": (titles or {}).get(k) or r["project"],
                      "tagline": p.get("showcase_what") or f"Sprint build, {date.fromisoformat(r['date']).strftime('%b %-d')}",
                      "section": (sections or {}).get(k) or section or p.get("theme") or "series", "tags": p.get("showcase_tags") or [], "href": href,
                      "date": r["date"], "current": r["current"], "sprint_href": r["href"], "project": r["project"],
                      "day_key": f"{r.get('repo')}|{k}", "clip_repo": r.get("repo"),
                      "code": f"https://github.com/{owner}/{r['repo']}" if public and r.get("repo") else None})
    return tiles, newest


def arrange(entries: list[dict], cfg: dict, newest: dict[str, str]) -> tuple[list[dict], list[dict], list[str]]:
    """Pure: the pinned Featured row (config order), the Latest row (newest build dates) and unknown pins."""
    by = {e["key"]: e for e in entries}
    pins = [slug(str(k)) for k in cfg.get("featured") or []]
    featured = [by[k] for k in dict.fromkeys(pins) if k in by]
    missing = [k for k in pins if k not in by]
    lay = layout(cfg)
    dated = [(newest.get(e.get("repo")) or e.get("date") or "", e) for e in entries]
    latest = [e for d, e in sorted(dated, key=lambda x: x[0], reverse=True) if d][:int(lay["latest_count"])] if lay["show_latest"] else []
    for d, e in dated:
        e["when"] = d
    return featured, latest, missing



def day_links(rows: list[dict], base: str) -> dict[str, dict]:
    """Pure: each repo's newest sprint day build {date, href}, keyed both "repo|project-slug" (for sprint tiles, so
    two projects in one repo never swap day builds) and "repo" (for showcase entries, which name only a repo)."""
    out: dict[str, dict] = {}
    for r in rows or []:
        if not (r.get("repo") and r.get("day_href") and r.get("date")):
            continue
        href = r["day_href"] if r["day_href"].startswith("https://") else base.rstrip("/") + "/" + r["day_href"]
        for k in [r["repo"]] + ([f"{r['repo']}|{slug(r['project'])}"] if r.get("project") else []):
            if r["date"] >= out.get(k, {}).get("date", ""):
                out[k] = {"date": r["date"], "href": href}
    return out


def attach_days(entries: list[dict], days: dict[str, dict]) -> None:
    """Give each tile its sprint day build: sprint tiles by their own project (day_key), showcase entries by repo.
    A tile whose only version is its day build (current is False) already opens it, so it gets no second link."""
    for e in entries:
        day = days.get(e.get("day_key") or e.get("repo") or "")
        if day and e.get("current") is not False:
            e["day"] = day


PAGE = """<!doctype html>
<html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
{robots}<title>{title}</title>
<meta name="description" content="{tagline}">
<style>
{base_css}
:root {{ --tile:{tile}px; --ratio:{ratio}; }}
.tagline {{ color:var(--muted); font-size:14px; margin:2px 0 0; }}
h2 {{ font-size:20px; color:var(--fg); margin:22px 0 10px; font-weight:650; letter-spacing:-.01em; display:flex; justify-content:space-between; align-items:baseline; }}
h2 small {{ font-size:13px; font-weight:400; color:var(--muted); }}
.offline {{ font-size:13px; color:var(--muted); margin:0 0 12px; }} .offline button {{ font:inherit; font-size:13px; min-height:32px; padding:0 12px; border-radius:999px; border:1px solid var(--line); background:none; color:var(--fg); cursor:pointer; }}
#uat-zone {{ position:fixed; left:0; right:0; border:2px dashed #f43f5e; pointer-events:none; z-index:3; }}
.tile.uat-on .thumb {{ outline:3px solid #f43f5e; outline-offset:2px; }}
.row {{ display:flex; gap:12px; overflow-x:auto; scroll-snap-type:x mandatory; scroll-padding-inline:16px; margin:0 -16px; padding:0 16px 4px; scrollbar-width:none; }}
.row .tile {{ flex:0 0 42%; max-width:220px; scroll-snap-align:start; }}
.grid {{ display:grid; grid-template-columns:repeat(auto-fill,minmax(var(--tile),1fr)); gap:14px 12px; }}
.tile {{ display:block; color:inherit; text-decoration:none; min-width:0; }}
.thumb {{ aspect-ratio:var(--ratio); border-radius:10px; overflow:hidden; background:var(--chip); border:1px solid var(--line); display:flex; align-items:center; justify-content:center; color:var(--muted); font-size:28px; font-weight:600; }}
.thumb img {{ width:100%; height:100%; object-fit:cover; object-position:top; display:block; }}
.thumb picture {{ display:contents; }}
.thumb {{ position:relative; }}
.thumb video {{ position:absolute; max-width:none; pointer-events:none; object-fit:fill; }}
.tile b {{ display:block; font-size:14px; margin-top:6px; line-height:1.3; overflow:hidden; text-overflow:ellipsis; white-space:nowrap; }}
.tile small {{ display:block; font-size:12px; color:var(--muted); line-height:1.35; display:-webkit-box; -webkit-line-clamp:2; -webkit-box-orient:vertical; overflow:hidden; }}
.tile[hidden] {{ display:none; }}
.tools {{ display:flex; gap:8px; margin-top:14px; }}
.tools input {{ flex:1; min-width:0; font:inherit; font-size:15px; min-height:44px; padding:0 14px; border-radius:12px; border:1px solid var(--line); background:var(--card); color:var(--fg); }}
.tools button {{ font:inherit; font-size:14px; font-weight:600; min-height:44px; padding:0 14px; border-radius:12px; border:1px solid var(--line); background:var(--chip); color:var(--fg); cursor:pointer; white-space:nowrap; }}
.empty {{ color:var(--muted); font-size:14px; padding:12px 0; }}
#results {{ margin-top:16px; }} #results[hidden], #shelves[hidden] {{ display:none; }}
</style></head>
<body><main>
<div class="top{solo}"><header><h1>{title}</h1><span>{count} builds · updated {updated}</span></header>{tabs}</div>
<div class="tools"><label for="q" style="position:absolute;left:-9999px">Search builds</label><input id="q" type="search" placeholder="Search builds, tags or dates" autocomplete="off"><button id="surprise" type="button">Surprise me</button></div>
<div id="shelves">{rows}</div>
<div class="grid" id="results" hidden>{grid}</div>
<p class="empty" id="none" hidden>No builds match.</p>
<footer><p class="offline" id="offline-row" hidden><button id="offline" type="button">Download offline copy</button> <span id="offline-s"></span></p>
Every tile opens its build's page: open it there, or its daily build. Pages are frozen copies, checked before they go up.</footer>
</main>
<script>
(() => {{
  const $ = id => document.getElementById(id);
  const q = $("q"), grid = $("results"), shelves = $("shelves");
  const orig = [...grid.querySelectorAll(".tile")];
  let refresh = () => {{}};
  // search: the shelves give way to a grid of every build that matches all the words
  const apply = () => {{
    const words = q.value.toLowerCase().trim().split(/\\s+/).filter(Boolean);
    let shown = 0;
    orig.forEach(t => {{ t.hidden = !words.every(w => t.dataset.q.includes(w)); if (!t.hidden) shown++; }});
    shelves.hidden = words.length > 0; grid.hidden = !words.length;
    $("none").hidden = !words.length || shown > 0;
    requestAnimationFrame(() => refresh());
  }};
  q.addEventListener("input", apply);
  // Offline copy: the button saves the homepage and every page it hosts (not clips, not the sprint site) into the
  // browser's Cache Storage; sw.js serves them when the network is gone. A new download replaces the old copy only
  // once every file has arrived, so a failed update never leaves you with half a site.
  const OFF = {offline_cfg};
  if (OFF.enabled && "serviceWorker" in navigator && "caches" in window) {{
    const row = $("offline-row"), btn = $("offline"), st = $("offline-s");
    row.hidden = false;
    const register = () => navigator.serviceWorker.register(OFF.sw).catch(() => {{ row.hidden = true; }});
    const meta = async () => {{
      for (const k of await caches.keys()) if (k.startsWith(OFF.prefix)) {{
        const r = await (await caches.open(k)).match(OFF.meta); if (r) return r.json();
      }}
      return null;
    }};
    meta().then(m => {{ if (m) register(); }}).catch(() => {{}});   // visitors who never save get no worker
    const show = async (current) => {{
      const m = await meta().catch(() => null);
      btn.textContent = m ? "Update offline copy" : "Download offline copy";
      st.textContent = m ? "Saved " + new Date(m.saved).toLocaleDateString(undefined, {{ month: "short", day: "numeric" }}) +
        (current && current !== m.version ? " · newer version available" : "") : "";
    }};
    fetch(OFF.manifest, {{ cache: "no-store" }}).then(r => r.json()).then(j => show(j.version)).catch(() => show(null));
    btn.onclick = async () => {{
      btn.disabled = true;
      register();
      const name = OFF.prefix + Date.now();
      try {{
        const j = await (await fetch(OFF.manifest, {{ cache: "no-store" }})).json();
        const c = await caches.open(name);
        let done = 0;
        for (const f of j.files) {{
          st.textContent = "Saving " + (++done) + " of " + j.files.length + "…";
          await c.add(new Request(f, {{ cache: "reload" }}));
        }}
        await c.put(OFF.meta, new Response(JSON.stringify({{ saved: Date.now(), version: j.version }}), {{ headers: {{ "content-type": "application/json" }} }}));
        for (const k of await caches.keys()) if (k.startsWith(OFF.prefix) && k !== name) await caches.delete(k);
        if (navigator.storage && navigator.storage.persist) navigator.storage.persist().catch(() => {{}});
        await show(j.version);
      }} catch (e) {{
        await caches.delete(name);
        st.textContent = "Couldn't save. Your previous copy (if any) is unchanged.";
      }} finally {{ btn.disabled = false; }}
    }};
  }}
  $("surprise").onclick = () => {{
    const pool = orig.filter(t => !t.hidden);
    if (pool.length) location.href = pool[Math.floor(Math.random() * pool.length)].getAttribute("href");   // a random build's own page
  }};
  // Clips: one at a time. The top-left clip in the top third of the screen plays at once and holds its last frame;
  // pointing at another tile plays that one instead. A stopped clip is removed, so one video is loaded at most.
  // No autoplay with reduced motion or data saver on (pointing still plays, since the viewer chose it).
  const W = {clip_w}, H = {clip_h};
  const play = t => {{
    const box = t.querySelector(".thumb"); if (!box || !t.dataset.clip) return;
    const [x, y, w, h] = t.dataset.crop.split(",").map(Number);
    const v = Object.assign(document.createElement("video"), {{ muted: true, playsInline: true, preload: "auto", src: t.dataset.clip }});
    v.setAttribute("muted", ""); v.setAttribute("playsinline", "");
    Object.assign(v.style, {{ width: W / w * 100 + "%", height: H / h * 100 + "%", left: -x / w * 100 + "%", top: -y / h * 100 + "%" }});
    const end = +t.dataset.start + +t.dataset.dur;
    v.addEventListener("timeupdate", () => {{ if (v.currentTime >= end) v.pause(); }});
    v.addEventListener("loadedmetadata", () => {{ v.currentTime = +t.dataset.start; v.play().catch(() => {{}}); }}, {{ once: true }});
    box.appendChild(v);
  }};
  // a fully loaded clip is unloaded to free memory; one still downloading is just paused and removed (aborting it
  // would show up as a failed request)
  const stop = t => t.querySelectorAll(".thumb video").forEach(v => {{
    v.pause(); if (v.readyState === 4) {{ v.removeAttribute("src"); v.load(); }} v.remove();
  }});
  const clips = [...document.querySelectorAll(".tile[data-clip]")];
  const auto = !matchMedia("(prefers-reduced-motion: reduce)").matches && !(navigator.connection && navigator.connection.saveData);
  // the autoplay zone: the first third of the screen below the sticky header
  const zone = () => {{ const c = document.querySelector(".top"), top = c ? Math.max(0, c.getBoundingClientRect().bottom) : 0;
                        return {{ top, bottom: top + innerHeight / 3 }}; }};
  // how much of a tile's picture is really on screen: cut by the screen and by its row when the row is swiped
  const shown = t => {{
    const box = t.querySelector(".thumb") || t, r = box.getBoundingClientRect(), row = t.closest(".row");
    const c = row ? row.getBoundingClientRect() : {{ left: 0, right: innerWidth }};
    const w = Math.min(r.right, c.right, innerWidth) - Math.max(r.left, c.left, 0);
    const h = Math.min(r.bottom, innerHeight) - Math.max(r.top, zone().top);
    return [r, w > 0 && h > 0 ? (w * h) / (r.width * r.height) : 0];
  }};
  const uat = new URLSearchParams(location.search).has("uat");
  let hovered = null, active = null, mark = null;
  const choose = () => {{
    let pick = hovered && !hovered.hidden && hovered.offsetParent ? hovered : null;
    if (!pick && auto) {{
      const z = zone();
      const rows = clips.filter(t => !t.hidden && t.offsetParent).map(t => [t, ...shown(t)])
                        .filter(([, r, f]) => f >= 0.5 && r.top < z.bottom);   // in the zone, at least half on screen
      rows.sort((p, q) => (Math.round(p[1].top / 20) - Math.round(q[1].top / 20)) || (p[1].left - q[1].left));
      pick = rows.length ? rows[0][0] : null;
    }}
    if (uat) {{
      const z = zone();
      Object.assign(mark.style, {{ top: z.top + "px", height: Math.max(0, z.bottom - z.top) + "px" }});
      clips.forEach(t => t.classList.toggle("uat-on", t === pick));
    }}
    if (pick === active) return;
    if (active) stop(active);
    active = pick;
    if (pick) play(pick);
  }};
  if (uat) {{
    mark = Object.assign(document.createElement("div"), {{ id: "uat-zone", ariaHidden: "true" }});
    document.body.appendChild(mark);
  }}
  refresh = choose;   // search changes what's on screen
  // any scroll, of the page or of a row swiped sideways, can slide a new tile into the top-left spot
  let queued = false;
  const later = () => {{ if (!queued) {{ queued = true; requestAnimationFrame(() => {{ queued = false; choose(); }}); }} }};
  addEventListener("scroll", later, {{ capture: true, passive: true }});
  addEventListener("resize", later, {{ passive: true }});
  choose();
  if (matchMedia("(hover: hover)").matches) {{
    clips.forEach(t => {{
      t.addEventListener("mouseenter", () => {{ hovered = t; choose(); }});
      t.addEventListener("mouseleave", () => {{   // a short pause, so crossing the gap between tiles doesn't restart a clip
        if (hovered === t) hovered = null;
        setTimeout(choose, 150);
      }});
    }});
  }}
}})();
</script></body></html>
"""


# Offline copy. Settings in one place; the page, sw.js and offline.json all read from here.
OFFLINE = {
    "sw": "sw.js",                 # service worker, at the site root so its scope covers every hosted page
    "manifest": "offline.json",    # the file list the button downloads, rebuilt every night
    "meta": "offline-meta.json",   # cache-only record of when the copy was saved (never a real file)
    "cache_prefix": "agentic-builds-offline-",
    "skip_dirs": ("vid",),         # clips stay online-only; tiles fall back to their still picture
    "skip_files": ("robots.txt", ".nojekyll"),
    "skip_ext": ASSETS["media"] + (".onnx", ".wasm", ".voice"),   # copied media and models (assets:) stay online only; the offline copy stays small
    "enabled": True,               # kill switch: False ships a sw.js that removes itself and every saved copy
    "network_timeout_ms": 4000,    # on a weak connection, use the saved copy after this long
}


def offline_files(out: Path) -> list[str]:
    """Pure over the built site folder: the URLs to save, as the browser requests them. A hosted page
    <key>/index.html is saved as <key>/ (what its tile links to); the homepage as ./."""
    files = []
    for p in sorted(out.rglob("*")):
        rel = p.relative_to(out)
        if not p.is_file() or rel.parts[0] in OFFLINE["skip_dirs"] or rel.parts[0].startswith("."):
            continue
        name = rel.as_posix()
        if name in OFFLINE["skip_files"] or name in (OFFLINE["sw"], OFFLINE["manifest"]) or name.lower().endswith(OFFLINE["skip_ext"]):
            continue
        if name == "index.html":
            name = "./"
        elif rel.name == "index.html":
            name = rel.parent.as_posix() + "/"
        files.append(name)
    return files


def offline_manifest(out: Path) -> dict:
    files = offline_files(out)
    h = hashlib.sha256()
    for f in files:
        p = out / ("index.html" if f == "./" else f + "index.html" if f.endswith("/") else f)
        data = p.read_bytes()
        if f == "./":   # the homepage's "updated Nov 9" changes nightly; it isn't a new version by itself
            data = re.sub(rb"builds \xc2\xb7 updated [^<]*", b"", data)
        h.update(f.encode() + b"\0" + data)
    return {"version": h.hexdigest()[:16], "files": files}


def service_worker(files: list[str]) -> str:
    """Network first, saved copy when offline or slow, and only for the files the offline copy holds (so the sprint
    site and other project sites on this origin are never touched). With OFFLINE["enabled"] off it removes itself."""
    prefix = json.dumps(OFFLINE["cache_prefix"])
    if not OFFLINE["enabled"]:
        return f"""// Generated by index/tools/build_showcase.py. Offline copy switched off: remove saved copies and this worker.
self.addEventListener("install", () => self.skipWaiting());
self.addEventListener("activate", (e) => e.waitUntil((async () => {{
  for (const k of await caches.keys()) if (k.startsWith({prefix})) await caches.delete(k);
  await self.registration.unregister();
}})()));
"""
    paths = sorted({"/" + ("" if f == "./" else f) for f in files} | {"/index.html"})
    return f"""// Generated by index/tools/build_showcase.py. Serves the saved offline copy when the network is gone or slow.
const PATHS = new Set({json.dumps(paths)}), PREFIX = {prefix}, TIMEOUT_MS = {int(OFFLINE["network_timeout_ms"])};
self.addEventListener("install", () => self.skipWaiting());
self.addEventListener("activate", (e) => e.waitUntil(self.clients.claim()));
async function saved(key) {{
  for (const k of await caches.keys()) if (k.startsWith(PREFIX)) {{
    const m = await (await caches.open(k)).match(key, {{ ignoreSearch: true }});
    if (m) return m;
  }}
  return null;
}}
self.addEventListener("fetch", (e) => {{
  const r = e.request, u = new URL(r.url);
  if (r.method !== "GET" || u.origin !== location.origin || !PATHS.has(u.pathname)) return;
  const key = u.pathname === "/index.html" ? "/" : u.pathname;
  e.respondWith((async () => {{
    const net = fetch(r);
    const slow = new Promise((res) => setTimeout(res, TIMEOUT_MS, "slow"));
    try {{
      const first = await Promise.race([net, slow]);
      if (first !== "slow") return first;
      return (await saved(key)) || (await net);   // slow: the saved copy if there is one, else keep waiting
    }} catch (err) {{
      return (await saved(key)) || Response.error();
    }}
  }})());
}});
"""


CLIP = {"width": 390, "height": 640, "min_s": 4, "max_s": 9, "default_s": 6}
DEFAULT_CLIP = {"seconds": 6, "steps": [{"wait": 600}, {"start": True}]}   # a build without a demo script: the page left running


def clip_spec(raw) -> dict | None:
    """A demo script, checked and clamped; None if it isn't one."""
    if not isinstance(raw, dict) or not isinstance(raw.get("steps", []), list):
        return None
    try:
        secs = float(raw.get("seconds", CLIP["default_s"]))
    except (TypeError, ValueError):
        secs = CLIP["default_s"]
    return {**raw, "seconds": max(CLIP["min_s"], min(CLIP["max_s"], secs)), "steps": raw.get("steps") or []}


def clip_plan(key: str, spec: dict, page_hash: str, old: dict | None, today: date, rerecord: bool = False) -> str:
    """Pure: "reuse" last night's clip or "record" a new one: when the script or the page changed, or it was asked
    for. A remote url page is hashed by its address, so it only re-records when its script changes."""
    script = hashlib.sha256(json.dumps(spec, sort_keys=True).encode()).hexdigest()
    if rerecord or not old or old.get("script") != script or not old.get("file"):
        return "record"
    return "reuse" if old.get("page") == page_hash else "record"


def clip_attrs(c: dict | None) -> str:
    """data-* attributes the page script uses to show a clip, cropped to the tile, without loading it up front."""
    if not c:
        return ""
    a = lambda v: html.escape(str(v), quote=True)
    x, y, w, h = c["crop"]
    return (f' data-clip="{a(c["src"])}" data-start="{c["start"]:.2f}" data-dur="{c["dur"]:.2f}"'
            f' data-crop="{x:.1f},{y:.1f},{w:.1f},{h:.1f}"')


def picture(shot, alt_letter: str, prefix: str = "") -> str:
    """A tile's picture: light and dark screenshots, the browser picks by the viewer's color scheme."""
    a = lambda v: html.escape(str(v), quote=True)
    if not shot:
        return a((alt_letter[:1] or "?").upper())
    if isinstance(shot, str):
        return f'<img src="{a(prefix + shot)}" alt="" loading="lazy">'
    light, dark = shot.get("light"), shot.get("dark")
    light, dark = light and prefix + light, dark and prefix + dark
    src = f'<source media="(prefers-color-scheme: dark)" srcset="{a(dark)}">' if dark and light else ""
    return f'<picture>{src}<img src="{a(light or dark)}" alt="" loading="lazy"></picture>'


def tile(e: dict, shot, section_title: str = "", sprint_start: str = "") -> str:
    a = lambda v: html.escape(str(v), quote=True)
    day = e.get("day") or {}
    when = date.fromisoformat(day["date"]).strftime("%b %-d") if day.get("date") else ""
    words = " ".join([e["title"], e.get("tagline") or "", " ".join(map(str, e.get("tags") or [])), section_title,
                      when, date.fromisoformat(str(e["date"])).strftime("%b %-d") if e.get("date") else ""]).lower()
    thumb = picture(shot, e["title"])
    sub = e.get("tagline") or ""
    sprint = str(e.get("date") or "") if e.get("day_key") else str(day.get("date") or "")   # the day it shipped
    sprint_label = ""
    if sprint:
        d = date.fromisoformat(sprint)
        n = (d - date.fromisoformat(sprint_start)).days + 1 if sprint_start else 0
        sprint_label = d.strftime("%a · %b %-d") + (f" · Day {n}" if n > 0 else "")
    return (f'<a class="tile" href="{a(case_href(e["key"]))}" data-section="{a(e.get("section") or "other")}" '
            f'data-sprint="{a(sprint)}" data-sprintl="{a(sprint_label)}" '
            f'data-title="{a(e["title"])}" data-desc="{a(e.get("description") or sub)}" '
            f'data-tags="{a("|".join(map(str, (e.get("tags") or [])[:5])))}" data-code="{a(e.get("code") or "")}" '
            f'data-open="{a(e.get("open_label") or "Open")}" data-q="{a(words)}" '
            f'data-day="{a(day.get("href") or "")}" data-dayl="{a(f"Day build · {when}" if when else "")}"'
            f'{clip_attrs(e.get("clip"))}>'
            f'<div class="thumb">{thumb}</div><b>{a(e["title"])}</b><small>{a(sub)}</small></a>')


def shelf_order(entries: list[dict], cfg: dict) -> list[tuple[str, str, list[dict]]]:
    """Pure: the topic shelves, in config.showcase.sections order, then any topic not listed (alphabetical).
    Each is (key, title, builds newest first; undated ones after, by title)."""
    titles = {s["key"]: s["title"] for s in cfg.get("sections") or []}
    order = [s["key"] for s in cfg.get("sections") or []]
    groups: dict[str, list[dict]] = {}
    for e in entries:
        groups.setdefault(e.get("section") or "other", []).append(e)
    keys = [k for k in order if k in groups] + sorted(k for k in groups if k not in order)
    newest = lambda e: str(e.get("when") or e.get("date") or "")
    return [(k, titles.get(k, k.replace("_", " ").title()),
             sorted(sorted(groups[k], key=lambda e: e.get("title", "").lower()), key=newest, reverse=True)) for k in keys]


def render(entries: list[dict], shots: dict, cfg: dict, updated: str, featured: list[dict] | None = None,
           latest: list[dict] | None = None, pages: list[str] | None = None) -> str:
    lay = layout(cfg)
    starts = [str((e.get("date") if e.get("day_key") else (e.get("day") or {}).get("date")) or "") for e in entries]
    sprint_start = str(cfg.get("sprint_start") or min([d for d in starts if d] or [""]))   # day 1
    a = lambda v: html.escape(str(v), quote=True)
    shelf = lambda name, items, note="": (f'<div class="rowblock"><h2>{a(name)}{f" <small>{a(note)}</small>" if note else ""}</h2><div class="row">'
                                          + "".join(tile(e, shots.get(e["key"]), "", sprint_start) for e in items) + "</div></div>")
    rows = ""
    if featured:
        rows += shelf("Featured", featured)
    if latest:
        rows += shelf("Latest", latest)
    titles = {}
    for key, title, items in shelf_order(entries, cfg):
        titles[key] = title
        rows += shelf(title, items, str(len(items)))
    grid = "".join(tile(e, shots.get(e["key"]), titles.get(e.get("section") or "other", ""), sprint_start)
                   for e in sorted(entries, key=lambda e: e.get("title", "").lower()))
    nav = tabs(pages or ["work"], "work")
    robots = "" if cfg.get("indexable") else '<meta name="robots" content="noindex,nofollow">\n'
    return PAGE.format(title=html.escape(cfg.get("title", "Builds")), tagline=html.escape(cfg.get("tagline", "")),
                       robots=robots, rows=rows, grid=grid, clip_w=CLIP["width"], clip_h=CLIP["height"],
                       tabs=nav, solo="" if nav else " solo", base_css=BASE_CSS, updated=updated, count=len(entries),
                       tile=lay["tile_min_px"], ratio=lay["thumb_ratio"],   # both validated in layout()
                       offline_cfg=json.dumps({"enabled": bool(OFFLINE["enabled"]), "sw": OFFLINE["sw"], "manifest": OFFLINE["manifest"], "meta": OFFLINE["meta"],
                                               "prefix": OFFLINE["cache_prefix"]}))


def frame(box: dict, vw: float, vh: float, rw: float, rh: float) -> tuple[float, float, float, float]:
    """Pure: the tile-shaped (rw:rh) rectangle that holds the on-screen part of the element `box`, centred on it
    and kept inside the vw x vh screen. Returns x, y, width, height."""
    l, t = max(0.0, box["x"]), max(0.0, box["y"])
    r, b = min(vw, box["x"] + box["width"]), min(vh, box["y"] + box["height"])
    bw, bh = max(1.0, r - l), max(1.0, b - t)
    cw = min(vw, max(bw, bh * rw / rh))
    ch = cw * rh / rw
    if ch > vh:
        ch, cw = vh, vh * rw / rh
    x = min(max(0.0, l + (bw - cw) / 2), vw - cw)
    y = min(max(0.0, t + (bh - ch) / 2), vh - ch)
    return x, y, cw, ch


def capture(targets: dict[str, str], out: Path, crop: dict[str, int] | None = None, ratio: str = "4/3",
            how: dict[str, dict] | None = None, workers: int = 4) -> tuple[dict, dict]:
    """Phone-size screenshots of each page in light and dark mode, cut to the tile shape: the top of the page
    (skipping thumb_y px; without thumb_y, the middle of the screen), or the element `how[key]["selector"]` (centred), after opening `#hash` if given.
    Also records JS and load errors so a broken page can be held back (a picture problem only warns). Returns key -> {"light", "dark"} and key -> errors."""
    import uat
    w, h = (float(x) for x in str(ratio).split("/"))
    (out / "img").mkdir(parents=True, exist_ok=True)
    jobs = [(key, url, scheme) for key, url in targets.items() for scheme in ("light", "dark")]

    def shoot(b, job):
        key, url, scheme = job
        opt = (how or {}).get(key) or {}
        try:
            wait = int(opt.get("wait_ms") or SHOT["settle_ms"])
        except (TypeError, ValueError):
            wait = SHOT["settle_ms"]
        ctx = b.new_context(viewport={"width": SHOT["width"], "height": SHOT["height"]}, device_scale_factor=2,
                            is_mobile=True, has_touch=True, color_scheme=scheme)
        errs: list[str] = []
        ok = False
        try:
            pg = ctx.new_page()
            pg.on("pageerror", lambda e: errs.append(str(e)[:200]))
            path = out / "img" / f"{key}{'' if scheme == 'light' else '.dark'}.png"
            try:
                pg.goto(url.split("#")[0] + (f"#{opt['hash']}" if opt.get("hash") else ""), wait_until="load", timeout=30000)
                pg.wait_for_timeout(wait)
            except Exception as ex:   # an unreachable page still gets a tile, just no picture (and is held back)
                errs.append(f"load: {ex}"[:200])
                return ok, errs
            vw, vh = SHOT["width"], SHOT["height"]
            clip = None
            if opt.get("selector"):
                # the chosen element, centred and cut to the tile shape. A picture problem is only a warning:
                # it must never hold back a page that loads fine.
                try:
                    loc = pg.locator(opt["selector"]).first
                    loc.scroll_into_view_if_needed(timeout=3000)
                    box = loc.bounding_box(timeout=3000)
                    if box and box["width"] > 0 and box["height"] > 0:
                        x, y, cw, ch = frame(box, vw, vh, w, h)
                        clip = {"x": x, "y": y, "width": cw, "height": ch}
                except Exception as ex:
                    print(f"::warning::showcase: {key} thumb selector {opt['selector']!r}: {str(ex).splitlines()[0][:120]}; using the top of the page")
            if clip is None and key not in (crop or {}):   # no thumb_y: the middle of the screen, where the app is
                ch = min(vw * h / w, vh)
                clip = {"x": 0, "y": (vh - ch) / 2, "width": vw, "height": ch}
            if clip is None:
                try:
                    y = int((crop or {}).get(key, 0))
                except (TypeError, ValueError):
                    y = 0
                y = max(0, min(y, vh - 100))
                clip = {"x": 0, "y": y, "width": vw, "height": min(vw * h / w, vh - y)}
            try:
                pg.screenshot(path=str(path), clip=clip)
                ok = True
            except Exception as ex:
                print(f"::warning::showcase: {key} ({scheme}) screenshot failed: {str(ex).splitlines()[0][:120]}")
        finally:
            ctx.close()
        return ok, errs

    shots: dict[str, dict] = {}
    errors: dict[str, list[str]] = {}
    for (key, _, scheme), (ok, errs) in zip(jobs, uat.parallel(shoot, jobs, workers)):
        if ok:
            shots.setdefault(key, {})[scheme] = f"img/{key}{'' if scheme == 'light' else '.dark'}.png"
        if errs:
            errors.setdefault(key, [])
            errors[key] += [x for x in errs if x not in errors[key]]
    return shots, errors


def record_clips(jobs: list[dict], out: Path, workers: int = 4, ratio: str = "4/3") -> dict[str, dict]:
    """Record each job's clip in dark mode (job: key, url, spec, selector). Returns key -> clip meta. A clip that
    can't be recorded is only a warning: the tile keeps its pictures."""
    import uat
    (out / "vid").mkdir(parents=True, exist_ok=True)
    tmp = tempfile.mkdtemp()
    rw, rh = (float(x) for x in str(ratio).split("/"))

    def rec(b, job):
        spec = job["spec"]
        vw, vh = CLIP["width"], CLIP["height"]
        ctx = pg = None
        try:
            ctx = b.new_context(viewport={"width": vw, "height": vh}, is_mobile=True, has_touch=True, color_scheme="dark",
                                record_video_dir=tmp, record_video_size={"width": vw, "height": vh})
            t0 = time.time()   # the video starts with the page
            pg = ctx.new_page()
            pg.goto(job["url"].split("#")[0] + (f"#{spec['hash']}" if spec.get("hash") else ""), wait_until="load", timeout=30000)
            pg.wait_for_timeout(800)
            start = None
            for st in spec["steps"]:
                if not isinstance(st, dict):
                    continue
                if st.get("start"):
                    start = time.time() - t0
                elif "wait" in st:
                    pg.wait_for_timeout(min(int(st["wait"]), 15000))
                elif "tap" in st:
                    pg.locator(str(st["tap"])).first.tap(timeout=3000)
                elif "type" in st:
                    pg.locator(str(st["type"][0])).first.fill(str(st["type"][1]), timeout=3000)
                elif "key" in st:
                    pg.keyboard.press(str(st["key"]))
                elif "select" in st:
                    pg.locator(str(st["select"][0])).first.select_option(str(st["select"][1]), timeout=3000)
                elif "scroll" in st:
                    pg.mouse.wheel(0, float(st["scroll"]))
                elif "hold" in st:
                    pg.mouse.move(vw / 2, vh / 2); pg.mouse.down()
                    pg.wait_for_timeout(min(int(st["hold"]), 5000)); pg.mouse.up()
                elif "tapAt" in st:
                    pg.mouse.click(float(st["tapAt"][0]), float(st["tapAt"][1]))
            start = (time.time() - t0) if start is None else start
            left = spec["seconds"] - (time.time() - t0 - start)
            if left > 0:
                pg.wait_for_timeout(int(left * 1000))
            crop = None
            sel = spec.get("selector") or job.get("selector")
            if sel:
                try:
                    box = pg.locator(sel).first.bounding_box(timeout=2000)
                    if box and box["width"] > 0 and box["height"] > 0:
                        crop = list(frame(box, vw, vh, rw, rh))
                except Exception:
                    crop = None
            ch = min(vh, vw * rh / rw)   # no selector: the middle of the screen, like the picture
            return {"start": start, "dur": spec["seconds"], "crop": crop or [0, (vh - ch) / 2, vw, ch]}
        except Exception as ex:
            print(f"::warning::showcase: clip for {job['key']} not recorded: {str(ex).splitlines()[0][:120]}")
            return None
        finally:
            if ctx is not None:
                ctx.close()
            if pg is not None and pg.video:
                job["_video"] = pg.video.path()

    metas = {}
    for job, meta in zip(jobs, uat.parallel(rec, jobs, workers)):
        src = job.get("_video")
        if meta and src and Path(src).exists():
            shutil.move(str(src), out / "vid" / f"{job['key']}.webm")
            metas[job["key"]] = {**meta, "src": f"vid/{job['key']}.webm"}
    shutil.rmtree(tmp, ignore_errors=True)
    return metas


def fetch(url: str, timeout: int = 20) -> bytes | None:
    try:
        with urllib.request.urlopen(urllib.request.Request(url, headers={"User-Agent": "index-showcase"}), timeout=timeout) as r:
            return r.read()
    except Exception:
        return None


def picture_url(e: dict, scan: Path) -> str | None:
    """Where to take a url entry's tile picture: its `preview` repo file (copied to a temp file, never published)
    when set, else the url itself. A preview that is missing or fails the secret scan gets no picture at all,
    rather than a picture of a sign-in page."""
    if e.get("preview"):
        text = read_file(scan, e.get("repo") or "index", e["preview"], None, any_branch=False)
        if text is None or secrets_in(text):
            return None
        tmp = Path(tempfile.mkdtemp()) / f"{e['key']}.html"
        tmp.write_text(text)
        return tmp.resolve().as_uri()
    return e["url"] if e["url"].startswith("https://") else None


def valid_clip(prev: dict, key: str) -> bool:
    """A manifest entry from the live site is only trusted in this exact shape: our own file name, numbers only."""
    num = lambda v: isinstance(v, (int, float)) and not isinstance(v, bool) and math.isfinite(v)
    crop = prev.get("crop")
    return (prev.get("file") == f"vid/{key}.webm" and num(prev.get("start")) and num(prev.get("dur"))
            and isinstance(crop, list) and len(crop) == 4 and all(num(c) for c in crop))


def clips(final: list[dict], scan: Path, out: Path, cfg: dict, urls: dict[str, str], thumbs: dict,
          ratio: str = "4/3", pictured: set | None = None) -> None:
    """Give every tile its clip (its demo script, else DEFAULT_CLIP; `clip: false` skips it): reused from the live
    site when allowed, else recorded."""
    site = (cfg.get("site_url") or (f"https://{cfg['repo']}/" if str(cfg.get("repo", "")).endswith(".github.io") else "")).rstrip("/")
    try:
        old = json.loads(fetch(site + "/vid/manifest.json") or b"{}") if site else {}
    except ValueError:
        old = {}
    today, manifest, jobs = date.today(), {}, []
    for e in final:
        url = urls.get(e["key"])
        raw = (cfg.get("clips") or {}).get(e["key"])   # config.showcase.clips wins
        raw = e.get("clip_script") if raw is None else raw
        repo = e.get("repo") or e.get("clip_repo")
        if raw is None and repo and scan:
            text = read_file(scan, repo, "demo/clip.json", None, any_branch=False)
            try:
                raw = json.loads(text) if text else None
            except ValueError:
                print(f"::warning::showcase: {e['key']}: demo/clip.json is not valid JSON; no clip")
                raw = False
        if raw is None:
            # the default only films pages that are public anyway: never a `preview` (a repo file that is pictured,
            # never published), never a tile whose picture failed
            if e.get("preview") or (pictured is not None and e["key"] not in pictured):
                continue
            t = thumbs.get(e["key"]) if isinstance(thumbs.get(e["key"]), dict) else {}
            raw = {**DEFAULT_CLIP, **({"hash": t["hash"]} if t.get("hash") else {})}
            if t.get("wait_ms"):
                raw["steps"] = [{"wait": t["wait_ms"]}, {"start": True}]
        spec = clip_spec(raw)
        if not spec or not url:
            continue
        spec = {**spec, "ratio": ratio}   # a new tile shape means a new crop: part of the script, so it re-records
        # a remote page is hashed by its address, so it only re-records when its script changes (or on request)
        local = Path(urllib.request.url2pathname(urllib.parse.urlparse(url).path)) if url.startswith("file://") else None
        page = local.read_bytes() if local and local.exists() else url.encode()
        page_hash = hashlib.sha256(page).hexdigest()
        script = hashlib.sha256(json.dumps(spec, sort_keys=True).encode()).hexdigest()
        prev = old.get(e["key"]) if isinstance(old, dict) else None
        prev = prev if isinstance(prev, dict) and valid_clip(prev, e["key"]) else {}
        plan = clip_plan(e["key"], spec, page_hash, prev, today, bool(e.get("rerecord")))
        if plan == "reuse" and (data := fetch(f"{site}/vid/{e['key']}.webm")):
            (out / "vid").mkdir(parents=True, exist_ok=True)
            (out / "vid" / f"{e['key']}.webm").write_bytes(data)   # our own name, never a path from the manifest
            e["clip"] = {"src": prev["file"], "start": prev["start"], "dur": prev["dur"], "crop": prev["crop"]}
            manifest[e["key"]] = {k: prev[k] for k in ("file", "start", "dur", "crop", "script", "page", "recorded_on") if k in prev}
            continue
        t = thumbs.get(e["key"])
        jobs.append({"key": e["key"], "url": url, "spec": spec, "selector": t.get("selector") if isinstance(t, dict) else None,
                     "_page": page_hash, "_script": script})
    recorded = record_clips(jobs, out, ratio=ratio) if jobs else {}
    for job in jobs:
        if (m := recorded.get(job["key"])):
            e = next(x for x in final if x["key"] == job["key"])
            e["clip"] = m
            manifest[job["key"]] = {"file": m["src"], "start": m["start"], "dur": m["dur"], "crop": m["crop"],
                                    "script": job["_script"], "page": job["_page"], "recorded_on": today.isoformat()}
    if manifest:
        (out / "vid").mkdir(parents=True, exist_ok=True)
        (out / "vid" / "manifest.json").write_text(json.dumps(manifest, indent=1))
    print(f"clips      {len(manifest)} ({len(recorded)} recorded, {len(manifest) - len(recorded)} reused)")


def main(argv: list[str]) -> int:
    scan, out = Path(argv[0]), Path(argv[1])
    sprint_dir = Path(argv[2]) if len(argv) > 2 else None
    index = yaml.safe_load((HERE / "index.yml").read_text())
    cfg = (index.get("config") or {}).get("showcase") or {}
    site_cfg = (index.get("config") or {}).get("site") or {}
    owner = (index.get("config") or {}).get("owner", "")
    meta = json.loads((scan / "meta.json").read_text()) if (scan / "meta.json").exists() else {}
    # an entry's `clip:` is its demo script; e["clip"] later holds the recorded clip
    items = [{**{k: v for k, v in it.items() if k != "clip"}, "clip_script": it.get("clip"), "owner": owner}
             for it in (index.get("showcase") or [])]
    entries, held = plan(items, meta)
    out.mkdir(parents=True, exist_ok=True)
    for old in out.glob("**/index.html"):
        old.unlink()
    shutil.rmtree(out / "vendor", ignore_errors=True)
    shutil.rmtree(out / "img", ignore_errors=True)   # pictures are retaken each night
    shutil.rmtree(out / "vid", ignore_errors=True)   # clips are reused from the live site or re-recorded
    vendor = Vendor.load()   # pinned third-party files are served from this site's one vendor/ folder
    targets, shown = {}, []
    for e in entries:
        if e.get("file"):
            text = read_file(scan, e.get("repo") or "index", e["file"], e.get("file_commit"), any_branch=False)   # merged work only
            if text is None:
                held.append((e["key"], f"{e['file']} not found"))
                continue
            if hits := secrets_in(text):
                held.append((e["key"], "secret scan: " + ", ".join(hits)))
                continue
            page = as_artifact_host(text) if e.get("artifact") else text
            page = vendor.rewrite(page, prefix="../")   # pages live at /<key>/index.html
            if not cfg.get("indexable"):
                page = page.replace("<head>", '<head><meta name="robots" content="noindex,nofollow">', 1)
            (out / e["key"]).mkdir(parents=True, exist_ok=True)
            (out / e["key"] / "index.html").write_text(page)
            targets[e["key"]] = (out / e["key"] / "index.html").resolve().as_uri()
            if e.get("assets") and e.get("repo"):
                n, why = copy_assets(scan, e["repo"], str(e["assets"]), out / e["key"])
                print(f"assets     {e['key']}: {n} files from {e['repo']}/{e['assets']}" if not why else f"::warning::showcase: {e['key']} {why}")
        elif (where := picture_url(e, scan)):
            targets[e["key"]] = where
        shown.append(e)
    # sprint builds: only what the sprint site already published (it ran UAT + secret scan), linked to its current
    # version. The manifest sits next to the sprint site folder, not in it (it names private repos).
    manifest = sprint_dir.with_name(sprint_dir.name + "_builds.json") if sprint_dir else None
    rows = json.loads(manifest.read_text()) if manifest and manifest.exists() else []
    if manifest and not manifest.exists():
        print(f"::warning::showcase: {manifest.name} not found; no sprint builds listed")
    base = cfg.get("sprint_url") or f"https://{owner}.github.io/{site_cfg.get('repo', 'november_sprint')}/"
    vendor.materialize(out)   # before screenshots, so they show the self-hosted copies working
    lay = layout(cfg)
    crop = {e["key"]: e["thumb_y"] for e in shown if e.get("thumb_y")}
    how = {e["key"]: e["thumb"] for e in shown if isinstance(e.get("thumb"), dict)}
    shots, errors = capture(targets, out, crop, lay["thumb_ratio"], how) if targets else ({}, {})
    final = []
    for e in shown:
        if e.get("file") and errors.get(e["key"]):   # a copied page that throws never goes up
            held.append((e["key"], "JS error: " + errors[e["key"]][0]))
            (out / e["key"] / "index.html").unlink(missing_ok=True)
            continue
        final.append(e)
    tiles, newest = sprint_builds(rows, index.get("projects") or [], final, meta, owner, base,   # after the JS check
                                  cfg.get("sprint_titles"), cfg.get("sprint_section"), cfg.get("sprint_sections"))
    tmap = {str(k): str(v) for k, v in (cfg.get("theme_sections") or {}).items()}
    for t in tiles:   # a project's theme names its topic shelf, mapped where the shelf has another name (audio: music)
        t["section"] = tmap.get(t["section"], t["section"])
    sprint_targets = {}
    for t in tiles:
        local = sprint_dir / t["sprint_href"] if not t["sprint_href"].startswith("https://") else None
        sprint_targets[t["key"]] = local.resolve().as_uri() if local and local.exists() else t["href"]
    if sprint_targets:   # pictures only: these pages were already checked and published by the sprint site
        more, _ = capture(sprint_targets, out, {}, lay["thumb_ratio"])
        shots.update(more)
    final += tiles
    days = day_links(rows, base)
    attach_days(final, days)
    clips(final, scan, out, cfg, {**targets, **sprint_targets}, {e["key"]: e.get("thumb") for e in final}, lay["thumb_ratio"],
          pictured=set(shots))
    featured, latest, missing = arrange(final, cfg, newest)
    for k in missing:
        print(f"::warning::showcase: featured pin {k!r} matches no entry or sprint build")
    titles = {sec["key"]: sec["title"] for sec in cfg.get("sections") or []}
    stats = yaml.safe_load((HERE / "stats.yml").read_text()) if (HERE / "stats.yml").exists() else {}
    daily = daily_items(rows, final, {e["key"] for e in featured}, titles, base,
                        lambda t: picture(shots.get(t["key"]), t["title"], "../"))
    colls, why = collections_plan(cfg.get("collections"), {e["key"]: e for e in final})
    for w in why:
        print(f"::warning::showcase: {w}")
    pages = ["work"] + (["collections"] if colls else []) + (["daily"] if daily else [])
    stamp = date.today().strftime("%b %-d")
    (out / "index.html").write_text(render(final, shots, {**cfg, "sprint_url": base}, stamp, featured, latest, pages))
    if daily:   # only builds the homepage already shows, described the same way
        (out / DAILY["path"]).mkdir(parents=True, exist_ok=True)
        (out / DAILY["path"] / "index.html").write_text(render_daily(daily, {**cfg, "sprint_url": base}, stamp, pages, date.today()))
    print(f"daily      {len(daily)} builds on /{DAILY['path']}/")
    if colls:   # builds the homepage already shows, grouped with a line on why each is there
        for rel, page in render_collections(colls, {e["key"]: e for e in final}, {e["key"] for e in featured}, {**cfg, "sprint_url": base},
                                            stamp, pages, lambda t, pre: picture(shots.get(t["key"]), t["title"], pre)).items():
            (out / COLLECTIONS["path"] / rel).parent.mkdir(parents=True, exist_ok=True)
            (out / COLLECTIONS["path"] / rel).write_text(page)
    print(f"collections {len(colls)} on /{COLLECTIONS['path']}/")
    feat_keys = {e["key"] for e in featured}
    starts = sorted(str(e["date"]) for e in final if e.get("day_key") and e.get("date"))
    first = str(cfg.get("sprint_start") or (starts[0] if starts else ""))
    for e in final:   # one page per build: what its tile carries, its repo's public numbers, its collections
        sec = e.get("section") or "other"
        day = (e.get("day") or {}).get("date") or (e.get("date") if e.get("day_key") else "")
        n = (date.fromisoformat(str(day)) - date.fromisoformat(first)).days + 1 if day and first else 0
        page = render_case(e, {**cfg, "sprint_url": base}, stamp, pages, picture(shots.get(e["key"]), e["title"], "../../"),
                           featured=e["key"] in feat_keys, topic_title=titles.get(sec, sec.replace("_", " ").title()),
                           stats=((stats or {}).get("repos") or {}).get(e.get("repo") or e.get("clip_repo") or ""),
                           collections=[c for c in colls if any(b["key"] == e["key"] for b in c["builds"])],
                           sprint_label=f"Day {n} build" if n > 0 else "Daily build", count=f"{len(final)} builds")
        (out / CASE["path"] / e["key"]).mkdir(parents=True, exist_ok=True)
        (out / CASE["path"] / e["key"] / "index.html").write_text(page)
    print(f"builds     {len(final)} pages on /{CASE['path']}/")
    (out / ".nojekyll").write_text("")
    if not cfg.get("indexable"):
        (out / "robots.txt").write_text("User-agent: *\nDisallow: /\n")
    man = offline_manifest(out)   # after every page, picture and vendored file is in place
    (out / OFFLINE["manifest"]).write_text(json.dumps(man))
    (out / OFFLINE["sw"]).write_text(service_worker(man["files"]))
    print(f"offline    {len(man['files'])} files in the offline copy ({man['version']})")
    for e in final:
        print(f"shown      {e['key']}{'' if shots.get(e['key']) else '  (no screenshot)'}{'  (day build: no latest)' if e.get('current') is False else ''}")
    print(f"featured   {', '.join(e['key'] for e in featured) or '(none pinned)'}")
    for k, why in held:
        print(f"held back  {k}: {why}")
        print(f"::notice::showcase: held back {k} ({why})")
    print(f"vendored   {len(vendor.used)} pinned files into vendor/")
    for p in vendor.problems:
        print(f"::warning::showcase: {p}")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
