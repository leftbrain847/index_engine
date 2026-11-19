"""The homepage's other pages: Daily builds (and, later, Collections and each build's own page).

Pure HTML from data. tools/build_showcase.py gathers the data, takes the pictures and writes the files; this module
only renders. Everything a page shows was already checked by build_showcase (merged work only, secret scan, JS
check) or by the sprint site (UAT), so these pages never publish anything new: they arrange what is already public.

Shared look: BASE_CSS (colours, the sticky header and its page tabs) is also used by the Work page, so all pages
match. Settings live in DAILY.
"""
from __future__ import annotations

import html
import re
from datetime import date, timedelta

DAILY = {
    "path": "daily",           # served at /daily/
    "title": "Daily builds",
    "intro": "One new build every day, frozen as it was finished. The full archive.",
    "calendar_label": "Open the sprint calendar ›",
}

BASE_CSS = """
:root { color-scheme: light dark; --bg:#fafaf9; --fg:#1c1917; --muted:#78716c; --line:#e7e5e4; --card:#fff; --accent:#2563eb; --chip:#f5f5f4; --gold:#a16207; --heat0:#e7e5e4; --heat1:#4ade80; --heat2:#eab308; }
@media (prefers-color-scheme: dark) { :root { --bg:#0c0a09; --fg:#f5f5f4; --muted:#a8a29e; --line:#292524; --card:#1c1917; --accent:#60a5fa; --chip:#292524; --gold:#e8c26a; --heat0:#1f1d1b; --heat1:#2a8a43; --heat2:#e8c26a; } }
* { box-sizing: border-box; }
body { margin:0; font:16px/1.5 system-ui,-apple-system,"Segoe UI",sans-serif; background:var(--bg); color:var(--fg); }
main { max-width:1040px; margin:0 auto; padding:0 16px 48px; }
header { display:flex; justify-content:space-between; align-items:baseline; gap:12px; flex-wrap:wrap; }
header h1 { font-size:24px; margin:0; }
header h1 a { color:inherit; text-decoration:none; }
header span { color:var(--muted); font-size:13px; }
.top { position:sticky; top:0; z-index:4; background:var(--bg); margin:0 -16px; padding:12px 16px 0; border-bottom:1px solid var(--line); }
.top.solo { padding-bottom:10px; }
.tabs { display:flex; gap:22px; margin-top:4px; overflow-x:auto; scrollbar-width:none; }
.tabs a { color:var(--muted); text-decoration:none; font-size:15px; padding:8px 0 10px; border-bottom:2px solid transparent; white-space:nowrap; }
.tabs a[aria-current="page"] { color:var(--fg); border-color:var(--fg); }
footer { margin-top:40px; color:var(--muted); font-size:13px; }
"""

NAV = [("work", "Work", ""), ("collections", "Collections", "collections/"), ("daily", "Daily builds", "daily/")]


def tabs(pages: list[str], active: str, root: str = "") -> str:
    """Pure: the page tabs, for the pages this build actually made (one page alone needs no tabs). `root` leads
    from the current page back to the site root (e.g. "../")."""
    items = [n for n in NAV if n[0] in pages]
    if len(items) < 2:
        return ""
    cur = ' aria-current="page"'
    return '<nav class="tabs" aria-label="Pages">' + "".join(
        f'<a href="{html.escape(root + href, quote=True) or "./"}"{cur if key == active else ""}>{label}</a>'
        for key, label, href in items) + "</nav>"


def slug(s: str) -> str:
    return re.sub(r"[^a-z0-9_]+", "_", s.lower()).strip("_")   # the same keys as build_showcase.slug


def a(v) -> str:
    return html.escape(str(v), quote=True)


def streak(days: set[str], today: date) -> int:
    """Pure: consecutive days with a build, ending today (or yesterday, if today's isn't in yet)."""
    d = today if today.isoformat() in days else today - timedelta(days=1)
    n = 0
    while d.isoformat() in days:
        n, d = n + 1, d - timedelta(days=1)
    return n


def heatmap(dates: dict[str, str], year: int, today: date) -> str:
    """Pure: one year as a GitHub-style grid, Monday-first columns. `dates` maps a day to its state
    ("built" or "featured"). Future days are drawn empty."""
    start = date(year, 1, 1)
    start -= timedelta(days=start.weekday())
    end = date(year, 12, 31)
    cells, months, col, d = [], [], 0, start
    while d <= end:
        if d.weekday() == 0:
            if d.day <= 7 and d.year == year:
                months.append((col, d.strftime("%b")))
            col += 1
        iso = d.isoformat()
        state = dates.get(iso, "")
        cls = " ".join(c for c in (state, "fut" if d > today else "", "today" if d == today else "", "out" if d.year != year else "") if c)
        label = d.strftime("%b %-d") + (" · built" if state == "built" else " · now featured" if state == "featured" else "")
        cells.append(f'<button type="button" class="{cls}" data-d="{iso}" aria-label="{a(label)}"{"" if state else " disabled"}></button>')
        d += timedelta(days=1)
    heads = "".join(f'<span style="grid-column:{c + 1}">{m}</span>' for c, m in months)
    return (f'<div class="year"><h3>{year}</h3><div class="heatwrap"><div class="months" style="grid-template-columns:repeat({col},12px)">{heads}</div>'
            f'<div class="heatbox"><div class="days"><span></span><span>M</span><span></span><span>W</span><span></span><span>F</span><span></span></div>'
            f'<div class="heat">{"".join(cells)}</div></div></div></div>')


DAILY_CSS = """
.intro h2 { font-size:28px; letter-spacing:-.02em; margin:20px 0 4px; }
.intro p { margin:0; color:var(--muted); }
.stats { display:grid; grid-template-columns:repeat(3,1fr); gap:8px; margin:16px 0 14px; }
.stat { background:var(--card); border:1px solid var(--line); border-radius:12px; padding:10px; }
.stat b { display:block; font-size:22px; } .stat span { color:var(--muted); font-size:12px; }
.year h3 { font-size:13px; color:var(--muted); margin:10px 0 4px; font-weight:600; }
.heatwrap { overflow-x:auto; margin:0 -16px; padding:0 16px 6px; scrollbar-width:none; }
.months { display:grid; gap:3px; font-size:10px; color:var(--muted); margin-left:22px; height:14px; }
.heatbox { display:flex; gap:6px; } .days { display:grid; grid-template-rows:repeat(7,12px); gap:3px; font-size:9px; color:var(--muted); width:16px; }
.heat { display:grid; grid-template-rows:repeat(7,12px); grid-auto-flow:column; grid-auto-columns:12px; gap:3px; }
.heat button { width:12px; height:12px; border-radius:3px; border:0; padding:0; background:var(--heat0); }
.heat button.out { visibility:hidden; }
.heat button.built { background:var(--heat1); cursor:pointer; } .heat button.featured { background:var(--heat2); cursor:pointer; }
.heat button.fut { background:transparent; box-shadow:inset 0 0 0 1px var(--heat0); }
.heat button.today { outline:2px solid var(--fg); outline-offset:1px; } .heat button.sel { outline:2px solid var(--accent); outline-offset:1px; }
.legend { display:flex; align-items:center; gap:6px; font-size:11px; color:var(--muted); margin:6px 0 0; justify-content:flex-end; flex-wrap:wrap; }
.legend i { width:10px; height:10px; border-radius:2px; display:inline-block; }
.legend a { color:var(--accent); margin-right:auto; font-size:13px; }
.tools { display:flex; gap:8px; margin:18px 0 10px; }
.tools input { flex:1; min-width:0; font:inherit; font-size:15px; min-height:44px; padding:0 14px; border-radius:12px; border:1px solid var(--line); background:var(--card); color:var(--fg); }
.tools select { font:inherit; font-size:14px; min-height:44px; padding:0 10px; border-radius:12px; border:1px solid var(--line); background:var(--card); color:var(--fg); }
.chips { display:flex; gap:6px; overflow-x:auto; margin:0 -16px 4px; padding:2px 16px 6px; scrollbar-width:none; }
.chips button { font:inherit; font-size:14px; min-height:36px; padding:0 13px; border-radius:999px; border:1px solid var(--line); background:none; color:var(--fg); white-space:nowrap; cursor:pointer; }
.chips button[aria-pressed="true"] { background:var(--fg); color:var(--bg); border-color:var(--fg); }
.month { display:flex; justify-content:space-between; align-items:baseline; margin:20px 0 2px; }
.month b { font-size:17px; } .month span { color:var(--muted); font-size:13px; }
.r { display:flex; gap:12px; align-items:center; padding:10px 0; border-bottom:1px solid var(--line); }
.r[hidden], .month[hidden] { display:none; }
.r .d { font:600 11px ui-monospace,SFMono-Regular,Menlo,monospace; color:var(--muted); width:34px; flex:none; text-align:center; line-height:1.2; }
.r .d big { display:block; font-size:18px; color:var(--fg); }
.r .thumb { width:44px; height:58px; flex:none; border-radius:8px; overflow:hidden; background:var(--chip); border:1px solid var(--line); display:flex; align-items:center; justify-content:center; color:var(--muted); font-weight:600; }
.r .thumb img { width:100%; height:100%; object-fit:cover; object-position:top; display:block; } .r .thumb picture { display:contents; }
.r .txt { flex:1; min-width:0; }
.r .txt a { color:inherit; text-decoration:none; font-weight:600; font-size:15.5px; }
.r .txt p { margin:1px 0 0; color:var(--muted); font-size:13.5px; white-space:nowrap; overflow:hidden; text-overflow:ellipsis; }
.r .day { font-size:13px; color:var(--accent); white-space:nowrap; text-decoration:none; }
.star { font:600 10px ui-monospace,SFMono-Regular,Menlo,monospace; color:var(--gold); border:1px solid currentColor; border-radius:6px; padding:1px 5px; margin-left:6px; vertical-align:2px; letter-spacing:.04em; }
.only { font-size:13px; color:var(--muted); margin:14px 0 0; } .only[hidden] { display:none; } .only a { color:var(--accent); }
.empty { color:var(--muted); font-size:14px; padding:12px 0; } .empty[hidden] { display:none; }
"""

DAILY_JS = """
(() => {
  const $ = id => document.getElementById(id);
  const rows = [...document.querySelectorAll(".r")], list = $("list");
  // month headings with their builds, newest first as built
  const groups = [...document.querySelectorAll(".month")].map(m => ({ m, rows: rows.filter(r => r.dataset.m === m.dataset.m) }));
  let topic = "all", only = "";
  const apply = () => {
    const words = $("q").value.toLowerCase().trim().split(/\\s+/).filter(Boolean), sort = $("sort").value;
    let shown = 0;
    rows.forEach(r => {
      r.hidden = !((topic === "all" || r.dataset.t === topic) && (!only || r.dataset.d === only) && words.every(w => r.dataset.q.includes(w)));
      if (!r.hidden) shown++;
    });
    if (sort === "az") {   // one list, no month headings
      groups.forEach(g => g.m.hidden = true);
      rows.slice().sort((x, y) => x.dataset.title.localeCompare(y.dataset.title)).forEach(r => list.appendChild(r));
    } else {
      const gs = sort === "old" ? groups.slice().reverse() : groups;
      gs.forEach(g => {
        g.m.hidden = !g.rows.some(r => !r.hidden);
        list.appendChild(g.m);
        (sort === "old" ? g.rows.slice().reverse() : g.rows).forEach(r => list.appendChild(r));
      });
    }
    $("none").hidden = shown > 0;
    $("only").hidden = !only;
    if (only) $("onlyd").textContent = document.querySelector('.heat button[data-d="' + only + '"]').getAttribute("aria-label").split(" · ")[0];
  };
  document.querySelectorAll(".chips button").forEach(c => c.onclick = () => {
    topic = c.dataset.t; document.querySelectorAll(".chips button").forEach(x => x.setAttribute("aria-pressed", String(x === c))); apply();
  });
  document.querySelectorAll(".heat").forEach(h => h.addEventListener("click", ev => {
    const b = ev.target.closest("button[data-d]"); if (!b || b.disabled) return;
    document.querySelectorAll(".heat .sel").forEach(x => x.classList.remove("sel"));
    only = only === b.dataset.d ? "" : b.dataset.d; if (only) b.classList.add("sel");
    apply(); if (only) $("only").scrollIntoView({ block: "nearest" });
  }));
  $("clear").onclick = ev => { ev.preventDefault(); only = ""; document.querySelectorAll(".heat .sel").forEach(x => x.classList.remove("sel")); apply(); };
  $("q").addEventListener("input", apply); $("sort").addEventListener("change", apply);
  // the newest weeks are on the right: start the first year scrolled there
  const w = document.querySelector(".heatwrap"), t = w && (w.querySelector(".heat .today") || [...w.querySelectorAll(".heat button:not([disabled])")].pop());
  if (w && t) w.scrollLeft = t.getBoundingClientRect().left - w.getBoundingClientRect().left + w.scrollLeft - w.clientWidth + 80;
})();
"""


def render_daily(items: list[dict], cfg: dict, updated: str, pages: list[str], today: date, root: str = "../") -> str:
    """Pure: the Daily builds page. `items`: one per published sprint build {date, title, tagline, topic, topic_title,
    href, day_href, featured, thumb (picture HTML, paths already relative to this page)}."""
    items = sorted(items, key=lambda i: (i["date"], i["title"].lower()), reverse=True)
    days = {i["date"] for i in items}
    state: dict[str, str] = {}
    for i in items:
        state[i["date"]] = "featured" if i.get("featured") or state.get(i["date"]) == "featured" else "built"
    years = sorted({int(d[:4]) for d in days} | ({today.year} if days else set()), reverse=True)
    maps = "".join(heatmap(state, y, today) for y in years)
    n_feat = sum(1 for i in items if i.get("featured"))
    topics: dict[str, str] = {}
    for i in items:
        topics.setdefault(i.get("topic") or "other", i.get("topic_title") or "Other")
    chips = '<button type="button" data-t="all" aria-pressed="true">All</button>' + "".join(
        f'<button type="button" data-t="{a(k)}" aria-pressed="false">{a(v)}</button>' for k, v in sorted(topics.items(), key=lambda kv: kv[1]))
    out, month = [], ""
    for i in items:
        d = date.fromisoformat(i["date"])
        m = d.strftime("%Y-%m")
        if m != month:
            month = m
            n = sum(1 for x in items if x["date"].startswith(m))
            out.append(f'<div class="month" data-m="{m}"><b>{d.strftime("%B %Y")}</b><span>{n} build{"" if n == 1 else "s"}</span></div>')
        words = " ".join([i["title"], i.get("tagline") or "", i.get("topic_title") or "", d.strftime("%b %-d"), d.strftime("%B")]).lower()
        star = '<span class="star">FEATURED</span>' if i.get("featured") else ""
        day = f'<a class="day" href="{a(i["day_href"])}" target="_blank" rel="noopener">Day build</a>' if i.get("day_href") and i["day_href"] != i["href"] else ""
        out.append(f'<div class="r" data-d="{i["date"]}" data-m="{m}" data-t="{a(i.get("topic") or "other")}" data-title="{a(i["title"].lower())}" data-q="{a(words)}">'
                   f'<span class="d">{d.strftime("%b").upper()}<big>{d.day}</big></span><div class="thumb">{i.get("thumb") or ""}</div>'
                   f'<div class="txt"><a href="{a(case_href(i["key"], "../") if i.get("key") else i["href"])}">{a(i["title"])}</a>{star}<p>{a(i.get("tagline") or "")}</p></div>{day}</div>')
    cal = cfg.get("sprint_url")
    robots = "" if cfg.get("indexable") else '<meta name="robots" content="noindex,nofollow">\n'
    nav = tabs(pages, "daily", root)
    title = html.escape(cfg.get("title", "Builds"))
    return f"""<!doctype html>
<html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
{robots}<title>{a(DAILY["title"])} · {title}</title>
<style>{BASE_CSS}{DAILY_CSS}</style></head>
<body><main>
<div class="top{"" if nav else " solo"}"><header><h1><a href="{a(root)}">{title}</a></h1><span>{len(items)} daily builds · updated {a(updated)}</span></header>{nav}</div>
<div class="intro"><h2>{a(DAILY["title"])}</h2><p>{a(DAILY["intro"])}</p></div>
<div class="stats"><div class="stat"><b>{len(days)}</b><span>days shipped</span></div><div class="stat"><b>{streak(days, today)}</b><span>day streak</span></div><div class="stat"><b>{n_feat}</b><span>now featured</span></div></div>
{maps}
<div class="legend">{f'<a href="{a(cal)}">{a(DAILY["calendar_label"])}</a>' if cal else ""}<i style="background:var(--heat1)"></i>built <i style="background:var(--heat2)"></i>now featured</div>
<p class="only" id="only" hidden>Showing <span id="onlyd"></span> · <a href="#" id="clear">show all</a></p>
<div class="tools"><label for="q" style="position:absolute;left:-9999px">Search the archive</label><input id="q" type="search" placeholder="Search the archive" autocomplete="off">
<label for="sort" style="position:absolute;left:-9999px">Sort</label><select id="sort"><option value="new">Newest</option><option value="old">Oldest</option><option value="az">A–Z</option></select></div>
<div class="chips" aria-label="Topics">{chips}</div>
<div id="list">{"".join(out)}</div>
<p class="empty" id="none" hidden>No builds match.</p>
<footer>Each build opens its own page; "Day build" opens it as it was the day it shipped.</footer>
</main>
<script>{DAILY_JS}</script></body></html>
"""


def daily_items(rows: list[dict], tiles: list[dict], featured: set[str], section_titles: dict[str, str], base: str,
                thumb, root: str = "../") -> list[dict]:
    """Pure (with `thumb(tile) -> html`): one item per published sprint build, described by its homepage tile (a
    sprint tile by its own project, a showcase entry by its repo), so it never shows anything the homepage doesn't.
    A build with no tile is left out."""
    by_day = {t["day_key"]: t for t in tiles if t.get("day_key")}
    by_repo = {t["repo"]: t for t in tiles if t.get("repo") and not t.get("day_key")}
    absolute = lambda h: h if not h or h.startswith("https://") else base.rstrip("/") + "/" + h
    here = lambda h: h if h.startswith(("https://", "/")) else root + h   # a hosted page, seen from this page
    out = []
    for r in rows or []:
        if not (r.get("project") and r.get("date")):
            continue
        t = by_day.get(f"{r.get('repo')}|{slug(r['project'])}") or by_repo.get(r.get("repo"))
        if not t:
            continue
        sec = t.get("section") or "other"
        out.append({"date": str(r["date"]), "title": t["title"], "tagline": t.get("tagline") or "", "topic": sec,
                    "topic_title": section_titles.get(sec, sec.replace("_", " ").title()), "href": here(t["href"]),
                    "day_href": absolute(r.get("day_href")), "featured": t["key"] in featured, "thumb": thumb(t), "key": t["key"]})
    return out




# ---- Collections -------------------------------------------------------------------------------------------------

COLLECTIONS = {
    "path": "collections",     # served at /collections/ and /collections/<key>/
    "title": "Collections",
    "intro": "Builds that work together, and the story of why.",
    "min_builds": 2,           # a collection with fewer known builds isn't shown
}

COLL_CSS = """
.intro h2 { font-size:28px; letter-spacing:-.02em; margin:20px 0 4px; }
.intro p { margin:0; color:var(--muted); }
.cl { display:block; text-decoration:none; color:inherit; background:var(--card); border:1px solid var(--line); border-left:4px solid var(--c); border-radius:16px; padding:16px; margin:14px 0 0; }
.cl .k, .hero .k { font:600 11px ui-monospace,SFMono-Regular,Menlo,monospace; letter-spacing:.08em; text-transform:uppercase; color:var(--c); }
.cl h3 { font-size:20px; margin:4px 0; letter-spacing:-.02em; } .cl p { margin:0; color:var(--muted); font-size:14px; }
.stack { display:flex; margin:12px 0 0; }
.stack .thumb { width:34px; height:44px; border-radius:8px; border:2px solid var(--card); margin-right:-8px; overflow:hidden; background:var(--chip); display:flex; align-items:center; justify-content:center; font-size:13px; font-weight:600; color:var(--muted); }
.thumb img { width:100%; height:100%; object-fit:cover; object-position:top; display:block; } .thumb picture { display:contents; }
.back { display:inline-block; color:var(--accent); text-decoration:none; margin:14px 0 4px; }
.hero { border-radius:16px; padding:18px; border:1px solid var(--line); background:linear-gradient(135deg, color-mix(in srgb, var(--c) 28%, transparent), var(--card) 65%); }
.hero h2 { font-size:26px; margin:4px 0 6px; letter-spacing:-.02em; } .hero p { margin:0; }
.step { display:flex; gap:12px; padding:14px 0; border-bottom:1px solid var(--line); }
.step .n { font:700 13px ui-monospace,SFMono-Regular,Menlo,monospace; color:var(--muted); width:16px; padding-top:18px; flex:none; }
.step .thumb { width:52px; height:68px; flex:none; border-radius:9px; overflow:hidden; background:var(--chip); border:1px solid var(--line); display:flex; align-items:center; justify-content:center; font-weight:600; color:var(--muted); }
.step .txt { flex:1; min-width:0; } .step .txt > a { color:inherit; text-decoration:none; font-weight:600; font-size:16px; }
.step p { margin:2px 0 0; color:var(--muted); font-size:13.5px; }
.why { font-size:13.5px; background:var(--chip); border-radius:8px; padding:7px 9px; margin-top:7px; }
.star { font:600 10px ui-monospace,SFMono-Regular,Menlo,monospace; color:var(--gold); border:1px solid currentColor; border-radius:6px; padding:1px 5px; margin-left:6px; vertical-align:2px; letter-spacing:.04em; }
.more { margin-top:26px; } .more h3 { font-size:13px; letter-spacing:.06em; text-transform:uppercase; color:var(--muted); margin:0 0 4px; }
"""


def collections_plan(raw: list, tiles: dict[str, dict]) -> tuple[list[dict], list[str]]:
    """Pure: config.showcase.collections, checked. Each: {key, title, blurb, color, builds: [{key, why}]}. Builds are
    homepage tile keys; unknown ones are skipped with a warning, and a collection left with fewer than
    COLLECTIONS["min_builds"] isn't shown. Returns the collections and the warnings."""
    out, warn, seen = [], [], set()
    color_ok = lambda c: isinstance(c, str) and re.fullmatch(r"#[0-9a-fA-F]{3,8}", c)
    if raw and not isinstance(raw, list):
        return [], ["collections: should be a list; none shown"]
    for c in raw or []:
        if not isinstance(c, dict):
            warn.append("collection: each one should be a mapping with key, title and builds; skipped")
            continue
        key = slug(str(c.get("key") or c.get("title") or ""))
        if not key or key in seen or not c.get("title"):
            warn.append(f"collection {key or '?'}: needs a unique key and a title")
            continue
        seen.add(key)
        builds = []
        if not isinstance(c.get("builds") or [], list):
            warn.append(f"collection {key}: builds should be a list; not shown")
            continue
        for b in c.get("builds") or []:
            b = b if isinstance(b, dict) else {"key": b}
            k = slug(str(b.get("key") or ""))
            if k in tiles and k not in {x["key"] for x in builds}:
                builds.append({"key": k, "why": str(b.get("why") or "")})
            else:
                warn.append(f"collection {key}: build {k!r} is not on the homepage; skipped")
        if len(builds) < COLLECTIONS["min_builds"]:
            warn.append(f"collection {key}: fewer than {COLLECTIONS['min_builds']} builds; not shown")
            continue
        out.append({"key": key, "title": str(c["title"]), "blurb": str(c.get("blurb") or ""),
                    "color": c.get("color") if color_ok(c.get("color")) else "#78716c", "builds": builds})
    return out, warn


def shell(page_title: str, cfg: dict, updated: str, pages: list[str], active: str, root: str, css: str, body: str, count: str) -> str:
    """Pure: a full page with the shared header and tabs."""
    robots = "" if cfg.get("indexable") else '<meta name="robots" content="noindex,nofollow">\n'
    nav = tabs(pages, active, root)
    title = html.escape(cfg.get("title", "Builds"))
    return f"""<!doctype html>
<html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
{robots}<title>{a(page_title)} · {title}</title>
<style>{BASE_CSS}{css}</style></head>
<body><main>
<div class="top{"" if nav else " solo"}"><header><h1><a href="{a(root)}">{title}</a></h1><span>{a(count)} · updated {a(updated)}</span></header>{nav}</div>
{body}
</main></body></html>
"""


def render_collections(colls: list[dict], tiles: dict[str, dict], featured: set[str], cfg: dict, updated: str,
                       pages: list[str], thumb) -> dict[str, str]:
    """Pure (with `thumb(tile, prefix) -> html`): the Collections list and one page per collection, as
    {relative path: html}. Each build links to its own page."""
    def card(c, root, here):
        stack = "".join(f'<span class="thumb">{thumb(tiles[b["key"]], root)}</span>' for b in c["builds"][:6])
        n = len(c["builds"])
        return (f'<a class="cl" style="--c:{c["color"]}" href="{a(here + c["key"] + "/")}"><div class="k">{n} build{"" if n == 1 else "s"}</div>'
                f'<h3>{a(c["title"])}</h3><p>{a(c["blurb"])}</p><div class="stack">{stack}</div></a>')

    count = f"{len(colls)} collection{'' if len(colls) == 1 else 's'}"
    out = {"index.html": shell(COLLECTIONS["title"], cfg, updated, pages, "collections", "../", COLL_CSS,
                               f'<div class="intro"><h2>{a(COLLECTIONS["title"])}</h2><p>{a(COLLECTIONS["intro"])}</p></div>'
                               + "".join(card(c, "../", "") for c in colls), count)}
    for c in colls:
        root = "../../"
        steps = []
        for i, b in enumerate(c["builds"], 1):
            t = tiles[b["key"]]
            star = '<span class="star">FEATURED</span>' if b["key"] in featured else ""
            why = f'<div class="why">{a(b["why"])}</div>' if b["why"] else ""
            steps.append(f'<div class="step"><span class="n">{i}</span><span class="thumb">{thumb(t, root)}</span><div class="txt">'
                         f'<a href="{a(case_href(t["key"], root))}">{a(t["title"])}</a>{star}<p>{a(t.get("tagline") or "")}</p>{why}</div></div>')
        others = [x for x in colls if x["key"] != c["key"]]
        more = (f'<div class="more"><h3>More collections</h3>{"".join(card(x, root, "../") for x in others)}</div>' if others else "")
        body = (f'<a class="back" href="../">‹ {a(COLLECTIONS["title"])}</a>'
                f'<div class="hero" style="--c:{c["color"]}"><div class="k">Collection · {len(c["builds"])} builds</div><h2>{a(c["title"])}</h2><p>{a(c["blurb"])}</p></div>'
                + "".join(steps) + more)
        out[f'{c["key"]}/index.html'] = shell(c["title"], cfg, updated, pages, "collections", root, COLL_CSS, body, count)
    return out


# ---- A page per build ---------------------------------------------------------------------------------------------

CASE = {
    "path": "builds",          # served at /builds/<key>/; every tile and list row opens its build's page
}

CASE_CSS = """
.back { display:inline-block; color:var(--accent); text-decoration:none; margin:14px 0 8px; }
.shot { border-radius:16px; overflow:hidden; border:1px solid var(--line); background:var(--chip); aspect-ratio:3/4; max-height:56vh; margin:16px auto 0;
        display:flex; align-items:center; justify-content:center; font-size:56px; font-weight:700; color:var(--muted); }
.shot img { width:100%; height:100%; object-fit:cover; object-position:top; display:block; } .shot picture { display:contents; }
.kicker { font:600 11px ui-monospace,SFMono-Regular,Menlo,monospace; letter-spacing:.08em; text-transform:uppercase; color:var(--muted); margin:6px 0 2px; }
.kicker .star { color:var(--gold); }
h2.t { font-size:28px; letter-spacing:-.02em; margin:0; line-height:1.15; }
.tagline { font-size:16px; margin:6px 0 0; } .desc { color:var(--muted); margin:8px 0 0; }
.btns { display:flex; gap:8px; margin:16px 0 0; flex-wrap:wrap; }
.btns a { flex:1; min-width:100px; text-align:center; text-decoration:none; font-weight:600; padding:12px; border-radius:12px; border:1px solid var(--line); color:var(--fg); }
.btns a.primary { background:var(--accent); border-color:var(--accent); color:#fff; }
.tags { display:flex; flex-wrap:wrap; gap:6px; margin:14px 0 0; } .tags span { background:var(--chip); border-radius:999px; padding:2px 10px; font-size:12px; color:var(--muted); }
.sec { margin:24px 0 0; } .sec h3 { font-size:13px; letter-spacing:.06em; text-transform:uppercase; color:var(--muted); margin:0 0 8px; }
.nums { display:grid; grid-template-columns:repeat(3,1fr); gap:8px; }
.num { background:var(--card); border:1px solid var(--line); border-radius:12px; padding:10px; } .num b { display:block; font-size:19px; } .num span { color:var(--muted); font-size:12px; }
.pill { display:inline-block; text-decoration:none; color:var(--fg); border:1px solid var(--line); border-radius:999px; padding:6px 12px; font-size:14px; margin:0 6px 6px 0; }
.dayrow { display:flex; gap:12px; align-items:center; text-decoration:none; color:inherit; background:var(--card); border:1px solid var(--line); border-radius:12px; padding:10px 12px; }
.dayrow .d { font:600 11px ui-monospace,SFMono-Regular,Menlo,monospace; color:var(--muted); text-align:center; line-height:1.2; } .dayrow .d big { display:block; font-size:18px; color:var(--fg); }
.dayrow span.go { margin-left:auto; color:var(--muted); font-size:20px; }
"""


def case_href(key: str, root: str = "") -> str:
    """Where a build's own page lives, seen from a page `root` away from the site root."""
    return f"{root}{CASE['path']}/{key}/"


def render_case(t: dict, cfg: dict, updated: str, pages: list[str], picture_html: str, *, featured: bool = False,
                topic_title: str = "", stats: dict | None = None, collections: list[dict] | None = None,
                sprint_label: str = "", count: str = "") -> str:
    """Pure: one build's page: its picture, what it is, Open / Day build / Code, its numbers, the collections it's
    in and the day it shipped. Shows only what its homepage tile already carries, plus public repo stats."""
    root = "../../"
    href = t["href"] if t["href"].startswith(("https://", "/")) else root + t["href"]
    day = t.get("day") or ({"href": t.get("sprint_href"), "date": t.get("date")} if t.get("day_key") else {})
    day_href = day.get("href") or ""
    if day_href and not day_href.startswith("https://") and cfg.get("sprint_url"):
        day_href = cfg["sprint_url"].rstrip("/") + "/" + day_href
    kicker = " · ".join(([a(topic_title)] if topic_title else []) + (['<span class="star">Featured</span>'] if featured else []))
    btns = [f'<a class="primary" href="{a(href)}" target="_blank" rel="noopener">{a(t.get("open_label") or "Open")}</a>']
    if day_href and day_href != href:
        when = date.fromisoformat(str(day["date"])).strftime("%b %-d") if day.get("date") else ""
        btns.append(f'<a href="{a(day_href)}" target="_blank" rel="noopener" title="As it was on {a(when)}">Day build</a>')
    if t.get("code"):
        btns.append(f'<a href="{a(t["code"])}" target="_blank" rel="noopener">Code</a>')
    tags = "".join(f"<span>{a(x)}</span>" for x in (t.get("tags") or [])[:6])
    desc = t.get("description") or ""
    parts = [f'<a class="back" href="{root}">‹ Work</a>', f'<div class="kicker">{kicker}</div>' if kicker else "",
             f'<h2 class="t">{a(t["title"])}</h2>', f'<p class="tagline">{a(t.get("tagline") or "")}</p>',
             f'<div class="btns">{"".join(btns)}</div>', f'<div class="shot">{picture_html}</div>',
             f'<p class="desc">{a(desc)}</p>' if desc and desc != t.get("tagline") else "", f'<div class="tags">{tags}</div>' if tags else ""]
    s = stats or {}
    nums = [(str(s["commits"]), "commits")] if isinstance(s.get("commits"), int) else []
    for k, label in (("started", "started"), ("updated", "last changed")):
        try:
            nums.append((date.fromisoformat(str(s[k])).strftime("%b %-d"), label))
        except (KeyError, ValueError):
            pass
    if nums:
        parts.append('<div class="sec"><h3>Built with agents</h3><div class="nums">' + "".join(f'<div class="num"><b>{a(v)}</b><span>{a(l)}</span></div>' for v, l in nums) + "</div></div>")
    if collections:
        parts.append('<div class="sec"><h3>Part of</h3>' + "".join(f'<a class="pill" href="{root}collections/{a(c["key"])}/">{a(c["title"])} ›</a>' for c in collections) + "</div>")
    if day_href and day_href != href and day.get("date"):   # a current version is not "the day it shipped"
        d = date.fromisoformat(str(day["date"]))
        parts.append(f'<div class="sec"><h3>Started as</h3><a class="dayrow" href="{a(day_href)}" target="_blank" rel="noopener">'
                     f'<span class="d">{d.strftime("%b").upper()}<big>{d.day}</big></span><div><b>{a(sprint_label or "Daily build")}</b>'
                     f'<div style="color:var(--muted);font-size:13px">As it was the day it shipped</div></div><span class="go">›</span></a></div>')
    return shell(t["title"], cfg, updated, pages, "work", root, CASE_CSS, "".join(parts), count)
