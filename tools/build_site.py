"""Build the public, no-sign-in sprint site (GitHub Pages) from the frozen day builds.

    python tools/build_site.py SCAN_DIR OUT_DIR

Lists sprint log entries (index.yml plus discovered day snapshots, see sprint.py) with `public: true`. Each gets up to two links:
- the day build (`file`, pinned with `file_commit` when the file keeps changing), frozen as it was finished;
- Current version (the log's `latest`), the finished product: `latest_file` (copied like the day build) or
  `latest_url` (already public).
A copied file goes up only if it passed tonight's automated UAT with no issue at or above
config.site.block_on_severity and matches none of the secret patterns control.py grades repos with. Anything
held back is listed in the run log, never on the site. No source, history or findings are copied.
Pinned third-party scripts, styles and fonts (site_deps.yml) are copied into the site's vendor/ folder and the
published pages point there (tools/vendor.py); the snapshots in their repos are never changed.
"""
from __future__ import annotations

import html
import json
import re
import shutil
import sys
from pathlib import Path

import yaml

HERE = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(HERE))
from control import SECRET_PATTERNS  # noqa: E402
from uat import SEV_RANK, as_artifact_host, read_file  # noqa: E402
from sprint import entries as sprint_entries  # noqa: E402
from vendor import Vendor  # noqa: E402


def slug(name: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", name.lower()).strip("-")


def secrets_in(text: str) -> list[str]:
    return sorted({k for k, rx in SECRET_PATTERNS.items() if re.search(rx, text)})


def gate(r: dict | None, limit: int) -> str | None:
    """Why a UAT result keeps a file off the site, or None if it may go up."""
    if not r or r.get("status") in (None, "skipped", "error"):
        return f"no passing UAT result ({(r or {}).get('status', 'not run')})"
    bad = sorted({i["check"] for i in r.get("issues", []) if SEV_RANK.get(i["severity"], 0) >= limit})
    return "UAT: " + ", ".join(bad) if bad else None


def select(log: list[dict], uat: dict, cfg: dict) -> tuple[list[dict], list[tuple[str, str]]]:
    """Pure: what each public entry may link (before the secret scan), and what is held back and why."""
    limit = SEV_RANK.get(cfg.get("block_on_severity", "high"), 3)
    day_uat = {r["name"]: r for r in uat.get("results", [])}
    latest_uat = {r["name"]: r for r in uat.get("latest", [])}
    ok, held = [], []
    for e in log:
        if not e.get("public"):
            continue
        name = f"{e['date']} - {e['project']}"
        item = {**e, "name": name, "slug": slug(name), "day": None, "latest": None}
        if e.get("public_day") is False:
            pass   # the day build stays private (e.g. it shows real findings); only the latest/demo link is listed
        elif not e.get("file"):
            held.append((name, "day build: no single file to publish (doc, video or artifact-only)"))
        elif why := gate(day_uat.get(name), limit):
            held.append((name, f"day build: {why}"))
        else:
            item["day"] = {"repo": e["repo"], "path": e["file"], "rev": e.get("file_commit"),
                           "viewed_as": day_uat[name].get("viewed_as", "file"), "href": f"{item['slug']}.html"}
        if e.get("latest_url", "").startswith("https://"):
            item["latest"] = {"url": e["latest_url"]}
        elif e.get("latest_file"):
            if why := gate(latest_uat.get(name), limit):
                held.append((name, f"latest: {why}"))
            else:
                item["latest"] = {"repo": e["repo"], "path": e["latest_file"], "rev": None, "label": e.get("latest_label"),
                                  "viewed_as": latest_uat[name].get("viewed_as", "file"),
                                  "href": f"{item['slug']}-latest.html"}
        if item["day"] or item["latest"]:
            ok.append(item)
    return ok, held


PAGE = """<!doctype html>
<html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
{robots}<title>{title}</title>
<style>
:root {{ color-scheme: light dark; --bg:#fafaf9; --fg:#1c1917; --muted:#78716c; --line:#e7e5e4; --card:#fff; --accent:#2563eb; }}
@media (prefers-color-scheme: dark) {{ :root {{ --bg:#0c0a09; --fg:#f5f5f4; --muted:#a8a29e; --line:#292524; --card:#1c1917; --accent:#60a5fa; }} }}
* {{ box-sizing: border-box; }}
body {{ margin:0; font:16px/1.5 system-ui,-apple-system,sans-serif; background:var(--bg); color:var(--fg); }}
main {{ max-width:640px; margin:0 auto; padding:24px 16px 48px; }}
h1 {{ font-size:28px; margin:0 0 4px; }} .sub {{ color:var(--muted); margin:0 0 24px; }}
details {{ border:1px solid var(--line); border-radius:12px; background:var(--card); margin:0 0 12px; }}
summary {{ min-height:48px; padding:12px 16px; font-weight:600; cursor:pointer; display:flex; align-items:center; }}
ul {{ list-style:none; margin:0; padding:0 8px 8px; }}
li {{ display:flex; align-items:center; gap:4px; flex-wrap:wrap; }}
li .name {{ flex:1 1 100%; padding:8px 8px 0; font-weight:600; }}
a.build {{ display:inline-flex; align-items:center; min-height:48px; padding:8px; border-radius:8px; color:var(--accent); text-decoration:none; font-weight:500; }}
a.build:active, a.build:hover {{ background:var(--line); }}
footer {{ color:var(--muted); font-size:13px; margin-top:32px; }}
</style></head>
<body><main>
<h1>{title}</h1>
<p class="sub">One new build a day, {n} so far. <b>Day build</b> is frozen exactly as it shipped that day; <b>Current version</b> is where it is now.</p>
{days}
<footer>Frozen copies, updated nightly.</footer>
</main></body></html>
"""


CURRENT_LABEL = "Current version"   # the sprint log's `latest` link, as visitors see it


def render_index(entries: list[dict], cfg: dict) -> str:
    days: dict[str, list[dict]] = {}
    for e in sorted(entries, key=lambda e: (str(e["date"]), e["project"])):
        days.setdefault(str(e["date"]), []).append(e)
    from datetime import date as _d
    blocks = []
    for i, (d, items) in enumerate(sorted(days.items(), reverse=True)):
        label = _d.fromisoformat(d).strftime("%a %b %-d")
        def li(e: dict) -> str:
            parts = [f'<span class="name">{html.escape(e["project"])}</span>']
            if e.get("day"):
                parts.append(f'<a class="build" href="{html.escape(e["day"]["href"])}">Day build · {_d.fromisoformat(d).strftime("%b %-d")}</a>')
            if e.get("latest"):
                href = e["latest"].get("href") or e["latest"]["url"]
                parts.append(f'<a class="build" href="{html.escape(href)}">{html.escape(e["latest"].get("label") or CURRENT_LABEL)}</a>')
            return f"<li>{''.join(parts)}</li>"
        links = "".join(li(e) for e in items)
        blocks.append(f'<details{" open" if i == 0 else ""}><summary>{label}</summary><ul>{links}</ul></details>')
    robots = "" if cfg.get("indexable") else '<meta name="robots" content="noindex,nofollow">\n'
    return PAGE.format(title=html.escape(cfg.get("title", "Sprint")), robots=robots, n=len(entries), days="\n".join(blocks))


def manifest(published: list[dict]) -> list[dict]:
    """What the homepage needs about each published build: its current version first, the day build only if
    there is no latest. Written NEXT TO the site folder (<out>_builds.json), never inside it: it names repos,
    some of them private, so it is read by tools/build_showcase.py and never published."""
    rows = []
    for e in published:
        cur, day = e.get("latest"), e.get("day")
        rows.append({"project": e["project"], "date": str(e["date"]), "repo": e.get("repo"), "current": bool(cur),
                     "href": (cur.get("href") or cur["url"]) if cur else day["href"],
                     "day_href": day["href"] if day else None})
    return rows


def main(argv: list[str]) -> int:
    scan, out = Path(argv[0]), Path(argv[1])
    index = yaml.safe_load((HERE / "index.yml").read_text())
    cfg = index.get("config", {}).get("site", {})
    uat = json.loads((HERE / "uat_results.json").read_text()) if (HERE / "uat_results.json").exists() else {}
    ok, held = select(sprint_entries(index, scan), uat, cfg)
    out.mkdir(parents=True, exist_ok=True)
    for old in out.glob("*.html"):
        old.unlink()
    shutil.rmtree(out / "vendor", ignore_errors=True)
    vendor = Vendor.load()   # pinned third-party files are served from this site's vendor/ folder
    published = []
    for e in ok:
        for kind in ("day", "latest"):
            f = e.get(kind)
            if not f or "path" not in f:
                continue
            text = read_file(scan, f["repo"], f["path"], f["rev"])
            why = f"{f['path']} not found" if text is None else ("secret scan: " + ", ".join(h)) if (h := secrets_in(text)) else None
            if why:
                held.append((e["name"], f"{kind}: {why}"))
                e[kind] = None
                continue
            page = as_artifact_host(text) if f["viewed_as"] == "artifact" else text
            page = vendor.rewrite(page)
            if not cfg.get("indexable"):
                tag = '<meta name="robots" content="noindex,nofollow">'
                page = page.replace("<head>", f"<head>{tag}", 1) if "<head>" in page else tag + page   # artifact pages have no <head>
            (out / f["href"]).write_text(page)
        if e.get("day") or e.get("latest"):
            published.append(e)
    vendor.materialize(out)
    (out / "index.html").write_text(render_index(published, cfg))
    out.with_name(out.name + "_builds.json").write_text(json.dumps(manifest(published), indent=1))   # not published
    (out / ".nojekyll").write_text("")
    if not cfg.get("indexable"):
        (out / "robots.txt").write_text("User-agent: *\nDisallow: /\n")
    for e in published:
        print(f"published  {e['name']}  ({', '.join(k for k in ('day', 'latest') if e.get(k))})")
    for name, why in held:
        print(f"held back  {name}: {why}")
        print(f"::notice::site: held back {name} ({why})")
    print(f"vendored   {len(vendor.used)} pinned files into vendor/")
    for p in vendor.problems:
        print(f"::warning::site: {p}")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
