import sys

import yaml
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "tools"))
from build_site import render_index, secrets_in, select  # noqa: E402

CFG = {"title": "November Sprint", "indexable": False, "block_on_severity": "high"}
LOG = [
    {"date": "2026-11-01", "project": "Pinball", "public": True, "repo": "pinball", "file": "p.html", "latest_file": "index.html", "snapshot": "x"},
    {"date": "2026-11-02", "project": "Hidden", "repo": "h", "file": "h.html"},                       # not public
    {"date": "2026-11-03", "project": "Broken", "public": True, "repo": "b", "file": "b.html", "latest_file": "b2.html"},
    {"date": "2026-11-04", "project": "Doc", "public": True, "repo": "d"},                             # nothing to copy
    {"date": "2026-11-05", "project": "Site", "public": True, "repo": "s", "file": "s.html", "latest_url": "https://example.github.io/s/"},
]
UAT = {"results": [
    {"name": "2026-11-01 - Pinball", "status": "warn", "viewed_as": "artifact", "issues": [{"check": "title-mismatch", "severity": "low"}]},
    {"name": "2026-11-03 - Broken", "status": "fail", "issues": [{"check": "js-errors", "severity": "critical"}]},
    {"name": "2026-11-05 - Site", "status": "pass", "issues": []},
], "latest": [
    {"name": "2026-11-01 - Pinball", "status": "warn", "viewed_as": "artifact", "issues": []},
    {"name": "2026-11-03 - Broken", "status": "pass", "issues": []},
]}


def test_only_public_entries_that_pass_uat_are_linked():
    ok, held = select(LOG, UAT, CFG)
    names = {e["project"]: e for e in ok}
    assert "Hidden" not in names and "Doc" not in names
    assert names["Pinball"]["day"]["href"] == "2026-11-01-pinball.html"
    assert names["Pinball"]["latest"]["href"] == "2026-11-01-pinball-latest.html"
    # A broken day build is held back, but its passing finished product still goes up.
    assert names["Broken"]["day"] is None and names["Broken"]["latest"]
    assert ("2026-11-03 - Broken", "day build: UAT: js-errors") in held


def test_latest_url_is_linked_not_copied_and_artifacts_keep_their_host_view():
    ok, _ = select(LOG, UAT, CFG)
    site = next(e for e in ok if e["project"] == "Site")
    assert site["latest"] == {"url": "https://example.github.io/s/"}
    pin = next(e for e in ok if e["project"] == "Pinball")
    assert pin["day"]["viewed_as"] == "artifact" and pin["latest"]["viewed_as"] == "artifact"


def test_no_uat_result_means_not_published():
    ok, held = select([{**LOG[0], "project": "New", "latest_file": None}], UAT, CFG)
    assert ok == [] and held[0][1].startswith("day build: no passing UAT result")


def test_secret_scan_catches_keys():
    assert secrets_in("const k = '" + "AKIA" + "Q" * 16 + "';")
    assert not secrets_in("<html><body>pinball</body></html>")


def test_calendar_page_is_private_by_default_and_newest_first():
    ok, _ = select(LOG, UAT, CFG)
    page = render_index(ok, CFG)
    assert 'name="robots" content="noindex,nofollow"' in page
    assert page.index("Nov 5") < page.index("Nov 1")
    assert "Day build · Nov 5" in page and "Current version" in page and ">Latest<" not in page


def test_a_private_day_build_never_goes_up_but_its_demo_does():
    log = [{"date": "2026-11-04", "project": "Project Index", "public": True, "public_day": False, "repo": "index",
            "file": "snapshots/real.html", "latest_file": "site_demo/demo.html", "latest_label": "Demo (sample data)", "snapshot": "x"}]
    uat = {"results": [{"name": "2026-11-04 - Project Index", "status": "pass", "issues": []}],
           "latest": [{"name": "2026-11-04 - Project Index", "status": "pass", "viewed_as": "artifact", "issues": []}]}
    ok, held = select(log, uat, CFG)
    assert ok[0]["day"] is None and held == []
    assert ok[0]["latest"]["path"] == "site_demo/demo.html"
    page = render_index(ok, CFG)
    assert "Demo (sample data)" in page and "real.html" not in page


def test_showcase_links_code_only_for_public_repos_and_rejects_bad_entries():
    from build_showcase import plan
    meta = {"portfolio": {"visibility": "public"}, "pinball": {"visibility": "private"}}
    items = [
        {"key": "guide", "title": "Guide", "tagline": "t", "file": "showcase/g.html", "owner": "me"},
        {"key": "pf", "title": "Portfolio", "tagline": "t", "url": "https://me.github.io/portfolio/", "repo": "portfolio", "owner": "me"},
        {"key": "pin", "title": "Pinball", "tagline": "t", "url": "https://me.github.io/pin/", "repo": "pinball", "owner": "me"},
        {"key": "both", "title": "Both", "tagline": "t", "file": "a.html", "url": "https://x/"},
        {"key": "bad", "title": "Bad", "tagline": "t", "url": "javascript:alert(1)"},
        {"key": "guide", "title": "Dup", "tagline": "t", "url": "https://x/"},
    ]
    ok, held = plan(items, meta)
    by = {e["key"]: e for e in ok}
    assert by["guide"]["href"] == "guide/" and by["guide"]["code"] is None
    assert by["pf"]["code"] == "https://github.com/me/portfolio"
    assert by["pin"]["code"] is None            # private source is never linked
    assert {k for k, _ in held} == {"both", "bad", "Dup"}   # the duplicate is named by its title


def test_showcase_page_escapes_text_and_is_noindex_by_default():
    from build_showcase import render
    e = {"key": "x", "title": "<b>X</b>", "tagline": "t", "href": "x/", "section": "tools", "code": None}
    page = render([e], {}, {"title": "Builds", "sections": [{"key": "tools", "title": "Tools"}]}, "Nov 6, 2026")
    assert "<b>X</b>" not in page and "&lt;b&gt;X&lt;/b&gt;" in page
    assert 'name="robots" content="noindex,nofollow"' in page


def test_sprint_builds_link_the_current_version_and_skip_projects_with_their_own_entry():
    from build_showcase import sprint_builds
    rows = [
        {"project": "Pinball", "date": "2026-11-01", "repo": "pinball", "current": True, "href": "2026-11-01-pinball-latest.html"},
        {"project": "Pinball", "date": "2026-11-09", "repo": "pinball", "current": False, "href": "2026-11-09-pinball.html"},
        {"project": "Portfolio", "date": "2026-11-05", "repo": "portfolio", "current": True, "href": "https://me.github.io/portfolio/"},
        {"project": "Sound Jam", "date": "2026-11-03", "repo": "jam_demo", "current": False, "href": "2026-11-03-sound-jam.html"},
    ]
    projects = [{"repo": "pinball", "what": "internal note", "showcase_what": "Six themed tables", "theme": "games",
                 "tags": ["internal"], "showcase_tags": ["pinball"]},
                {"repo": "jam_demo", "what": "internal note", "theme": "audio"}]
    entries = [{"key": "portfolio", "repo": "portfolio"}]
    meta = {"pinball": {"visibility": "private"}, "jam_demo": {"visibility": "public"}}
    tiles, newest = sprint_builds(rows, projects, entries, meta, "me", "https://me.github.io/november_sprint/")
    by = {t["key"]: t for t in tiles}
    assert set(by) == {"pinball", "sound_jam"}                       # portfolio has its own showcase entry
    assert by["pinball"]["href"] == "https://me.github.io/november_sprint/2026-11-01-pinball-latest.html"   # current beats a newer day build
    assert by["pinball"]["section"] == "games" and by["pinball"]["tagline"] == "Six themed tables"
    assert by["pinball"]["tags"] == ["pinball"]
    assert by["sound_jam"]["tagline"] == "Sprint build, Nov 3" and by["sound_jam"]["tags"] == []   # internal notes never shown
    assert by["pinball"]["code"] is None and by["sound_jam"]["code"] == "https://github.com/me/jam_demo"
    assert newest == {"pinball": "2026-11-09", "portfolio": "2026-11-05", "jam_demo": "2026-11-03"}


def test_featured_keeps_config_order_and_reports_unknown_pins():
    from build_showcase import arrange
    entries = [{"key": k, "date": d} for k, d in (("a", "2026-11-01"), ("b", "2026-11-03"), ("c", ""))]
    featured, latest, missing = arrange(entries, {"featured": ["c", "a", "nope", "a"], "layout": {"latest_count": 1}}, {})
    assert [e["key"] for e in featured] == ["c", "a"]
    assert missing == ["nope"]
    assert [e["key"] for e in latest] == ["b"]                        # newest first, capped by latest_count


def test_showcase_tiles_carry_escaped_sheet_data_and_one_shelf_per_topic():
    from build_showcase import render
    es = [{"key": "x", "title": 'X"><script>', "tagline": "t", "href": "x/", "section": "games", "tags": ["a"], "code": None},
          {"key": "y", "title": "Y", "tagline": "t", "href": "y/", "section": "data", "code": "https://github.com/me/y"}]
    cfg = {"sections": [{"key": "data", "title": "Data"}, {"key": "games", "title": "Games"}]}
    page = render(es, {}, cfg, "Nov 7", featured=[es[1]])
    assert '"><script>' not in page.split("<script>\n")[0]
    assert 'data-f="' not in page and 'class="cats"' not in page        # no filters: topic shelves instead
    assert page.index(">Data <small>") < page.index(">Games <small>")     # config order
    assert ">Featured</h2>" in page and ">Latest</h2>" not in page        # an empty row isn't drawn
    assert 'data-code="https://github.com/me/y"' in page and "2 builds" in page


def test_sprint_manifest_is_written_beside_the_site_not_in_it_and_bad_rows_are_skipped():
    import inspect, build_site
    from build_showcase import sprint_builds, layout
    src = inspect.getsource(build_site.main)
    assert 'out.with_name(out.name + "_builds.json")' in src and '(out / "builds.json")' not in src
    tiles, _ = sprint_builds([{"project": "X"}, {"date": "2026-11-01", "href": "a.html"}], [], [], {}, "me", "https://b/")
    assert tiles == []
    assert layout({"layout": {"thumb_ratio": "16:9;}body{x", "tile_min_px": "big"}}) == {**layout({}), "thumb_ratio": "4/3"}


def test_homepage_pages_come_from_merged_work_only(tmp_path):
    import subprocess
    from uat import read_file
    g = tmp_path / "r"
    run = lambda *a: subprocess.run(["git", "-C", str(g), *a], check=True, capture_output=True)
    g.mkdir(); run("init", "-q", "-b", "main"); run("config", "user.email", "t@t"); run("config", "user.name", "t")
    (g / "a.txt").write_text("x"); run("add", "."); run("commit", "-qm", "m")
    run("checkout", "-qb", "pr"); (g / "page.html").write_text("unmerged"); run("add", "."); run("commit", "-qm", "pr"); run("checkout", "-q", "main")
    subprocess.run(["git", "clone", "-q", "--mirror", str(g), str(tmp_path / "scan" / "r.git")], check=True)
    assert read_file(tmp_path / "scan", "r", "page.html") == "unmerged"            # sprint UAT may look ahead
    assert read_file(tmp_path / "scan", "r", "page.html", any_branch=False) is None  # the homepage may not
    pr_sha = subprocess.run(["git", "-C", str(g), "rev-parse", "pr"], capture_output=True, text=True).stdout.strip()
    assert read_file(tmp_path / "scan", "r", "page.html", pr_sha) == "unmerged"
    assert read_file(tmp_path / "scan", "r", "page.html", pr_sha, any_branch=False) is None   # pinning doesn't get around it


def test_url_entries_take_their_picture_from_the_preview_file(tmp_path, monkeypatch):
    import build_showcase as bs
    files = {("habla", "claude-page/habla.html"): "<html><body>Valeria</body></html>",
             ("leaky", "page.html"): "<html>" + "AKIA" + "Q" * 16 + "</html>"}
    monkeypatch.setattr(bs, "read_file", lambda scan, repo, path, rev=None, any_branch=True: files.get((repo, path)))
    shot = bs.picture_url({"key": "habla", "url": "https://claude.ai/artifact/x", "repo": "habla",
                           "preview": "claude-page/habla.html"}, tmp_path)
    assert shot.startswith("file://") and Path(shot[7:]).read_text() == files[("habla", "claude-page/habla.html")]
    assert bs.picture_url({"key": "a", "url": "https://example.com/"}, tmp_path) == "https://example.com/"
    assert bs.picture_url({"key": "b", "url": "https://x/", "repo": "habla", "preview": "missing.html"}, tmp_path) is None
    assert bs.picture_url({"key": "c", "url": "https://x/", "repo": "leaky", "preview": "page.html"}, tmp_path) is None


def test_sprint_tiles_can_share_one_section_and_take_display_names():
    from build_showcase import sprint_builds
    rows = [{"project": "Sound Jam Band", "date": "2026-11-03", "href": "a.html", "repo": "jam_demo"},
            {"project": "Project Index", "date": "2026-11-04", "href": "b.html", "repo": "index"}]
    projects = [{"repo": "jam_demo", "theme": "audio"}]
    tiles, _ = sprint_builds(rows, projects, [], {}, "me", "https://b/",
                             {"sound_jam_band": "Endless Jam"}, "november_sprint")
    by = {t["key"]: t for t in tiles}
    assert by["sound_jam_band"]["title"] == "Endless Jam"          # renamed for display, key unchanged
    assert by["project_index"]["title"] == "Project Index"          # no rename given: day name
    assert {t["section"] for t in tiles} == {"november_sprint"}
    tiles, _ = sprint_builds(rows, projects, [], {}, "me", "https://b/")
    assert {t["key"]: t["section"] for t in tiles}["sound_jam_band"] == "audio"   # default: by theme


def test_day_links_keep_each_repos_newest_day_build():
    from tools.build_showcase import day_links
    rows = [{"repo": "r", "date": "2026-11-02", "day_href": "old.html"}, {"repo": "r", "date": "2026-11-08", "day_href": "new.html"},
            {"repo": "s", "date": "2026-11-05", "day_href": None}]
    rows[0]["project"], rows[1]["project"] = "Old Game", "New Game"
    links = day_links(rows, "https://x.io/s/")
    assert links["r"] == {"date": "2026-11-08", "href": "https://x.io/s/new.html"}   # a showcase entry: the repo's newest
    assert links["r|old_game"]["href"] == "https://x.io/s/old.html"                  # a sprint tile: its own project
    assert "s" not in links


def test_homepage_has_search_surprise_and_a_day_build_link_in_the_sheet():
    from tools.build_showcase import render
    e = {"key": "la", "title": "Linear Algebra", "tagline": "Course", "href": "la/", "section": "learning", "tags": ["math"],
         "day": {"date": "2026-11-08", "href": "https://x.io/s/la.html"}}
    page = render([e], {}, {"title": "B", "sections": [{"key": "learning", "title": "Learning"}]}, "Nov 9")
    assert 'id="q"' in page and 'id="surprise"' in page and ">This week</h2>" not in page
    assert 'data-day="https://x.io/s/la.html"' in page and 'data-dayl="Day build · Nov 8"' in page
    assert 'data-q="linear algebra course math learning nov 8 "' in page


def test_sprint_tiles_and_showcase_entries_get_their_own_day_build():
    from tools.build_showcase import attach_days, day_links, sprint_builds
    rows = [{"project": "Old Game", "date": "2026-11-02", "repo": "r", "current": True, "href": "old-latest.html", "day_href": "old.html"},
            {"project": "New Game", "date": "2026-11-08", "repo": "r", "current": True, "href": "new-latest.html", "day_href": "new.html"},
            {"project": "Day Only", "date": "2026-11-05", "repo": "d", "current": False, "href": "d.html", "day_href": "d.html"}]
    tiles, _ = sprint_builds(rows, [], [], {}, "o", "https://x.io/s/")
    showcase = {"key": "app", "title": "App", "repo": "r", "href": "app/"}
    attach_days(tiles + [showcase], day_links(rows, "https://x.io/s/"))
    by = {t["key"]: t for t in tiles}
    assert by["old_game"]["day"]["href"] == "https://x.io/s/old.html"    # its own project, not the repo's newest
    assert by["new_game"]["day"]["href"] == "https://x.io/s/new.html"
    assert "day" not in by["day_only"]                                   # already opens its day build
    assert showcase["day"]["href"] == "https://x.io/s/new.html"          # showcase entry: the repo's newest


def test_tiles_show_the_light_or_dark_picture_by_the_viewers_setting():
    from tools.build_showcase import tile
    e = {"key": "k", "title": "K", "href": "k/"}
    out = tile(e, {"light": "img/k.png", "dark": "img/k.dark.png"})
    assert '<source media="(prefers-color-scheme: dark)" srcset="img/k.dark.png">' in out and 'src="img/k.png"' in out
    assert 'src="img/k.png"' in tile(e, "img/k.png")          # one picture still works
    assert ">K</div>" in tile(e, None)                          # no picture: the letter


def test_capture_takes_light_and_dark_shots_of_the_chosen_element(tmp_path):
    from tools.build_showcase import capture
    page = tmp_path / "p.html"
    page.write_text("<meta name=viewport content='width=device-width'><style>:root{color-scheme:light dark}@media (prefers-color-scheme:dark){body{background:#000}#stage{background:blue!important}}</style>"
                    "<header style='height:300px'>top</header><div id='stage' style='width:200px;height:100px;background:red'></div>"
                    "<script>document.title = location.hash</script>")
    shots, errors = capture({"p": page.as_uri()}, tmp_path, how={"p": {"hash": "eigen", "selector": "#stage", "wait_ms": 50}}, workers=2)
    assert shots == {"p": {"light": "img/p.png", "dark": "img/p.dark.png"}} and not errors
    import struct
    size = lambda f: struct.unpack(">II", (tmp_path / f).read_bytes()[16:24])   # PNG width, height
    assert size("img/p.png") == size("img/p.dark.png")
    assert (tmp_path / "img/p.png").read_bytes() != (tmp_path / "img/p.dark.png").read_bytes()   # really two themes
    assert size("img/p.png")[0] < 780     # a frame around the #stage element, not the full 390 px page


def test_a_missing_thumb_selector_only_warns_and_keeps_the_page(tmp_path):
    from tools.build_showcase import capture
    page = tmp_path / "p.html"
    page.write_text("<h1>hi</h1>")
    shots, errors = capture({"p": page.as_uri()}, tmp_path, how={"p": {"selector": "#nope", "wait_ms": "soon"}}, workers=2)
    assert shots == {"p": {"light": "img/p.png", "dark": "img/p.dark.png"}} and errors == {}   # top-of-page picture, not held back


def test_clips_are_re_recorded_only_when_the_script_changes_or_a_changed_page_is_stale():
    import hashlib, json
    from datetime import date
    from tools.build_showcase import clip_plan, clip_spec
    spec = clip_spec({"seconds": 20, "steps": [{"wait": 100}]})
    assert spec["seconds"] == 9 and clip_spec({"seconds": 1})["seconds"] == 4 and clip_spec("nope") is None
    script = hashlib.sha256(json.dumps(spec, sort_keys=True).encode()).hexdigest()
    old = {"file": "vid/k.webm", "script": script, "page": "p1", "recorded_on": "2026-11-05"}
    today = date(2026, 11, 9)
    assert clip_plan("k", spec, "p1", old, today) == "reuse"                                   # nothing changed
    assert clip_plan("k", spec, "p2", old, today) == "record"                                  # page changed: the clip shows the new version
    assert clip_plan("k", {**spec, "seconds": 5}, "p1", old, today) == "record"                # new demo script
    assert clip_plan("k", spec, "p1", old, today, rerecord=True) == "record"                   # asked for
    assert clip_plan("k", spec, "p1", None, today) == "record"


def test_a_recorded_clip_plays_from_its_start_and_is_cropped_to_the_element(tmp_path):
    from tools.build_showcase import clips, tile
    page = tmp_path / "p.html"
    page.write_text("<meta name=viewport content='width=device-width'><header style='height:200px'>top</header>"
                    "<div id='stage' style='height:300px;background:teal'><button id='go'>go</button></div>")
    e = {"key": "p", "title": "P", "href": "p/", "clip_script": {"seconds": 4, "steps": [{"tap": "#go"}, {"start": True}]}}
    clips([e], None, tmp_path, {}, {"p": page.as_uri()}, {"p": {"selector": "#stage"}})
    c = e["clip"]
    assert (tmp_path / c["src"]).stat().st_size > 1000 and c["dur"] == 4 and c["start"] > 0
    assert c["crop"][1] > 150 and abs(c["crop"][2] / c["crop"][3] - 4 / 3) < 0.01      # the #stage box, at the tile shape
    assert (tmp_path / "vid/manifest.json").exists()
    assert 'data-clip="vid/p.webm"' in tile(e, None)


def test_a_tampered_manifest_is_never_trusted():
    from tools.build_showcase import valid_clip
    good = {"file": "vid/k.webm", "start": 1.2, "dur": 6, "crop": [0, 0, 390, 292.5]}
    assert valid_clip(good, "k")
    for bad in ({**good, "file": "../../tools/site_check.py"}, {**good, "file": "/etc/passwd"}, {**good, "file": "vid/other.webm"},
                {**good, "start": "1"}, {**good, "crop": [0, 0, 1]}, {**good, "dur": True}, {**good, "start": float("nan")}, {**good, "crop": [0, 0, float("inf"), 1]}, {k: v for k, v in good.items() if k != "dur"}):
        assert not valid_clip(bad, "k")


def test_surprise_me_opens_a_random_builds_own_page(tmp_path):
    from playwright.sync_api import sync_playwright
    from tools.build_showcase import render
    es = [{"key": "a", "title": "A", "tagline": "a", "href": "https://claude.ai/artifact/x", "section": "tools"},
          {"key": "b", "title": "B", "tagline": "b", "href": "https://example.invalid/b", "section": "tools"}]
    page = tmp_path / "index.html"
    page.write_text(render(es, {}, {"title": "B"}, "Nov 9"))
    for k in "ab":
        (tmp_path / "builds" / k).mkdir(parents=True)
        (tmp_path / "builds" / k / "index.html").write_text(f"<title>{k}</title>")
    with sync_playwright() as p:
        b = p.chromium.launch()
        try:
            pg = b.new_page()
            external = []
            pg.on("request", lambda r: r.url.startswith("http") and external.append(r.url))
            pg.goto(page.as_uri())
            pg.click("#surprise")
            pg.wait_for_timeout(300)
            assert pg.url.startswith((tmp_path / "builds").as_uri()) and external == []   # its own page, on this site
        finally:
            b.close()


def test_search_swaps_the_shelves_for_matches_and_top_clips_play_without_a_hover(tmp_path):
    from playwright.sync_api import sync_playwright
    from tools.build_showcase import render
    es = [{"key": "old", "title": "Old", "tagline": "o", "href": "#", "section": "games", "date": "2026-11-02", "day_key": "r|old"},
          {"key": "new", "title": "New", "tagline": "n", "href": "#", "section": "games", "date": "2026-11-07", "day_key": "r|new"},
          {"key": "la", "title": "LA", "tagline": "l", "href": "#", "section": "learning", "day": {"date": "2026-11-08", "href": "d.html"},
           "clip": {"src": "vid/la.webm", "start": 0, "dur": 4, "crop": [0, 0, 390, 292.5]}},
          {"key": "tool", "title": "Tool", "tagline": "t", "href": "#", "section": "tools"}]
    page = tmp_path / "index.html"
    page.write_text(render(es, {}, {"title": "B", "sections": [{"key": "games", "title": "Games"}]}, "Nov 9"))
    with sync_playwright() as p:
        b = p.chromium.launch()
        try:
            pg = b.new_page(viewport={"width": 390, "height": 2000})   # no hover needed: the top third autoplays
            pg.goto(page.as_uri())
            pg.wait_for_timeout(300)
            assert pg.locator(".tile[data-clip] video").count() == 1
            heads = pg.eval_on_selector_all("#shelves h2", "hs => hs.map(h => h.firstChild.textContent.trim())")
            assert heads == ["Games", "Learning", "Tools"]                    # config order, then the rest
            games = pg.eval_on_selector_all("#shelves .rowblock:first-child .tile", "ts => ts.map(t => t.dataset.title)")
            assert games == ["New", "Old"]                                    # newest first
            pg.fill("#q", "nov 7")
            assert pg.is_hidden("#shelves") and pg.is_visible("#results")
            assert pg.eval_on_selector_all("#results .tile:not([hidden])", "ts => ts.map(t => t.dataset.title)") == ["New"]
            pg.fill("#q", "zzz")
            assert pg.is_visible("#none")
            pg.fill("#q", "")
            assert pg.is_visible("#shelves") and pg.is_hidden("#results") and pg.is_hidden("#none")
            assert pg.evaluate("document.documentElement.scrollWidth <= innerWidth")
        finally:
            b.close()


def test_only_one_clip_plays_the_top_left_one_and_pointing_switches_it(tmp_path):
    from playwright.sync_api import sync_playwright
    from tools.build_showcase import render
    clip = {"src": "vid/x.webm", "start": 0, "dur": 4, "crop": [0, 0, 390, 292.5]}
    es = [{"key": k, "title": k.upper(), "tagline": k, "href": "#", "section": "tools", "clip": clip} for k in ("a", "b", "c")]
    page = tmp_path / "index.html"
    page.write_text(render(es, {}, {"title": "B"}, "Nov 9"))
    with sync_playwright() as p:
        b = p.chromium.launch()
        try:
            pg = b.new_page(viewport={"width": 1000, "height": 2400})
            pg.goto(page.as_uri())
            pg.wait_for_timeout(300)
            playing = lambda: pg.eval_on_selector_all(".tile", "ts => ts.filter(t => t.querySelector('video')).map(t => t.dataset.title)")
            assert playing() == ["A"]                       # one at a time: the top-left
            pg.hover('#shelves .tile[data-title="C"]')
            pg.wait_for_timeout(100)
            assert playing() == ["C"]                       # pointing switches it
            pg.mouse.move(990, 2390)
            pg.wait_for_timeout(400)   # leaving waits 150 ms before switching back
            assert playing() == ["A"]                       # and back
        finally:
            b.close()



def test_day_labels_count_from_the_configured_sprint_start():
    from tools.build_showcase import render
    e = {"key": "a", "title": "A", "tagline": "a", "href": "#", "section": "s", "date": "2026-11-08", "day_key": "r|a"}
    assert 'data-sprintl="Sun · Nov 8 · Day 8"' in render([e], {}, {"title": "B", "sprint_start": "2026-11-01"}, "Nov 9")
    assert 'data-sprintl="Sun · Nov 8 · Day 1"' in render([e], {}, {"title": "B"}, "Nov 9")   # no start set: the first build is day 1


def test_every_tile_gets_a_clip_unless_it_opts_out(tmp_path):
    from tools.build_showcase import clips
    page = tmp_path / "p.html"
    page.write_text("<html><body style='background:#123'><div id=stage>hi</div></body></html>")
    plain = {"key": "p", "title": "P", "href": "p/"}
    off = {"key": "q", "title": "Q", "href": "q/", "clip_script": False}
    clips([plain, off], None, tmp_path, {}, {"p": page.as_uri(), "q": page.as_uri()}, {})
    assert plain["clip"]["dur"] == 6 and (tmp_path / "vid" / "p.webm").exists()   # no demo script: the default one
    assert "clip" not in off                                                      # `clip: false`
    private = {"key": "r", "title": "R", "href": "r/", "preview": "private.html"}
    unpictured = {"key": "s", "title": "S", "href": "s/"}
    clips([private, unpictured], None, tmp_path, {}, {"r": page.as_uri(), "s": page.as_uri()}, {}, pictured={"r"})
    assert "clip" not in private and "clip" not in unpictured   # a preview is never filmed; nor is a tile with no picture


def test_swiping_a_row_hands_the_clip_to_the_tile_that_slides_into_place_and_uat_outlines_it(tmp_path):
    from playwright.sync_api import sync_playwright
    from tools.build_showcase import render
    clip = {"src": "vid/x.webm", "start": 0, "dur": 4, "crop": [0, 0, 390, 292.5]}
    es = [{"key": k, "title": k.upper(), "tagline": k, "href": "#", "section": "tools", "date": f"2026-11-0{i + 1}", "clip": clip}
          for i, k in enumerate("abcdef")]
    page = tmp_path / "index.html"
    page.write_text(render(es, {}, {"title": "B", "featured": ["a", "b", "c", "d", "e", "f"]}, "Nov 9", featured=es))
    with sync_playwright() as p:
        b = p.chromium.launch()
        try:
            pg = b.new_page(viewport={"width": 390, "height": 1200})
            pg.goto(page.as_uri() + "?uat")
            pg.wait_for_timeout(300)
            playing = lambda: pg.eval_on_selector_all(".tile", "ts => ts.filter(t => t.querySelector('video')).map(t => t.dataset.title)")
            assert playing() == ["A"]
            assert pg.locator("#uat-zone").count() == 1 and pg.locator(".tile.uat-on").count() == 1
            pg.eval_on_selector(".row", "r => r.scrollBy(r.querySelector('.tile').offsetWidth + 12, 0)")   # swipe one tile left
            pg.wait_for_timeout(300)
            assert playing() == ["B"]
            assert pg.eval_on_selector(".tile.uat-on", "t => t.dataset.title") == "B"
        finally:
            b.close()


def test_on_a_phone_the_first_featured_clip_plays_on_load_below_the_header(tmp_path):
    from playwright.sync_api import sync_playwright
    from tools.build_showcase import render
    clip = {"src": "vid/x.webm", "start": 0, "dur": 4, "crop": [0, 0, 390, 292.5]}
    es = [{"key": k, "title": k.upper(), "tagline": k, "href": "#", "section": "tools", "date": f"2026-11-0{i + 1}", "clip": clip}
          for i, k in enumerate("abcdef")]
    page = tmp_path / "index.html"
    page.write_text(render(es, {}, {"title": "B"}, "Nov 9", featured=es[:3], latest=es[3:]))
    with sync_playwright() as p:
        b = p.chromium.launch()
        try:
            pg = b.new_page(viewport={"width": 390, "height": 844})
            pg.goto(page.as_uri())
            pg.wait_for_timeout(300)
            assert pg.eval_on_selector_all(".tile", "ts => ts.filter(t => t.querySelector('video')).map(t => t.dataset.title)") == ["A"]
        finally:
            b.close()


def test_tile_frames_hold_the_element_and_follow_the_tile_shape():
    from tools.build_showcase import frame
    x, y, w, h = frame({"x": 16, "y": 300, "width": 358, "height": 100}, 390, 640, 3, 4)   # a wide card: a portrait frame around it
    assert (w, h) == (358, 358 * 4 / 3) and y <= 300 and y + h >= 400
    x, y, w, h = frame({"x": 0, "y": 100, "width": 390, "height": 2000}, 390, 640, 3, 4)  # taller than the screen: its visible part
    assert (w, h) == (390, 520) and 0 <= y <= 120
    x, y, w, h = frame({"x": 0, "y": 0, "width": 390, "height": 300}, 390, 640, 4, 3)    # landscape still works
    assert abs(w / h - 4 / 3) < 1e-9 and w <= 390


def test_config_clips_win_new_steps_run_and_the_tile_shape_is_part_of_the_script(tmp_path):
    from tools.build_showcase import clips
    page = tmp_path / "p.html"
    page.write_text("<html><body><select id=s><option>a</option><option>b</option></select>"
                    "<div id=stage style='height:2000px'>hi</div><script>addEventListener('click',()=>document.title='tapped')</script></body></html>")
    steps = [{"select": ["#s", "b"]}, {"scroll": 100}, {"hold": 50}, {"tapAt": [10, 10]}, {"start": True}]
    e = {"key": "p", "title": "P", "href": "p/", "clip_script": {"seconds": 4, "steps": [{"tap": "#missing"}]}}   # would fail: config wins
    clips([e], None, tmp_path, {"clips": {"p": {"seconds": 4, "steps": steps}}}, {"p": page.as_uri()}, {}, ratio="3/4")
    assert e["clip"]["crop"][2:] == [390, 520]                     # portrait, centred on the screen
    import json
    m = json.loads((tmp_path / "vid" / "manifest.json").read_text())["p"]
    e2 = {"key": "p", "title": "P", "href": "p/"}
    from tools.build_showcase import clip_plan, clip_spec
    from datetime import date
    old = {**m, "file": "vid/p.webm"}
    spec = clip_spec({"seconds": 4, "steps": steps})
    assert clip_plan("p", {**spec, "ratio": "3/4"}, m["page"], old, date.today()) == "reuse"
    assert clip_plan("p", {**spec, "ratio": "4/3"}, m["page"], old, date.today()) == "record"   # new tile shape: re-record


def test_offline_copy_lists_hosted_pages_and_skips_clips(tmp_path):
    from build_showcase import offline_manifest, service_worker
    for f, body in {"index.html": "home", "habla/index.html": "h", "img/a.png": "p", "vendor/x.js": "v",
                    "vid/a.mp4": "clip", "robots.txt": "r", ".nojekyll": ""}.items():
        (tmp_path / f).parent.mkdir(parents=True, exist_ok=True)
        (tmp_path / f).write_text(body)
    man = offline_manifest(tmp_path)
    assert sorted(man["files"]) == sorted(["./", "habla/", "img/a.png", "vendor/x.js"])
    sw = service_worker(man["files"])
    assert '"/habla/"' in sw and '"/"' in sw and "november_sprint" not in sw and "/vid/" not in sw
    (tmp_path / "habla/index.html").write_text("changed")
    assert offline_manifest(tmp_path)["version"] != man["version"]   # a changed page means a new version


def test_showcase_page_has_offline_button():
    from build_showcase import render, OFFLINE
    page = render([], {}, {"title": "Builds"}, "Nov 9")
    assert 'id="offline"' in page and f'"sw": "{OFFLINE["sw"]}"' in page


def test_offline_kill_switch_and_daily_stamp(tmp_path, monkeypatch):
    import build_showcase as bs
    (tmp_path / "index.html").write_text("<span>3 builds · updated Nov 8</span>")
    v1 = bs.offline_manifest(tmp_path)["version"]
    (tmp_path / "index.html").write_text("<span>3 builds · updated Nov 9</span>")
    assert bs.offline_manifest(tmp_path)["version"] == v1   # the date alone is not a new version
    monkeypatch.setitem(bs.OFFLINE, "enabled", False)
    sw = bs.service_worker(["./"])
    assert "unregister" in sw and "PATHS" not in sw


def test_offline_button_hidden_when_switched_off(monkeypatch):
    import build_showcase as bs
    monkeypatch.setitem(bs.OFFLINE, "enabled", False)
    assert '"enabled": false' in bs.render([], {}, {"title": "B"}, "Nov 9")


def test_a_builds_page_opens_it_and_its_day_build_and_shows_its_numbers_and_collections():
    from tools.build_showcase import render
    from tools.site_pages import render_case
    t = {"key": "rg", "title": "Roster <Grader>", "tagline": "Grades", "href": "rg/", "section": "data", "tags": ["nfl"],
         "code": "https://github.com/me/rg", "day": {"date": "2026-11-09", "href": "https://x.io/s/rg.html"}}
    page = render_case(t, {"title": "B"}, "Nov 18", ["work", "collections", "daily"], "<img src='../../img/rg.png'>", featured=True,
                       topic_title="Data", stats={"commits": 96, "started": "2026-11-09", "updated": "2026-11-17", "last": "private message"},
                       collections=[{"key": "nfl", "title": "NFL tools"}], sprint_label="Day 9 build")
    assert 'href="../../rg/" target="_blank"' in page and 'href="https://x.io/s/rg.html" target="_blank"' in page
    assert 'title="As it was on Nov 9">Day build' in page and 'href="https://github.com/me/rg"' in page and "Day 9 build" in page
    assert "<b>96</b><span>commits</span>" in page and "Nov 17" in page and "private message" not in page   # never the commit text
    assert 'href="../../collections/nfl/"' in page and "Roster &lt;Grader&gt;" in page and "Featured" in page
    assert 'aria-current="page">Work' in page and 'href="../../"' in page
    bare = render_case({"key": "x", "title": "X", "tagline": "x", "href": "https://s.io/x.html"}, {"title": "B"}, "Nov 18", ["work"], "X")
    assert "Day build" not in bare and "Code" not in bare and "Built with agents" not in bare and 'class="tabs"' not in bare
    cur = render_case({"key": "y", "title": "Y", "tagline": "y", "href": "https://s.io/y.html", "day_key": "r|y", "date": "2026-11-05",
                       "sprint_href": "https://s.io/y.html"}, {"title": "B"}, "Nov 18", ["work"], "Y")
    assert "Started as" not in cur and "Day build" not in cur   # its only link is the current version
    home = render([t], {}, {"title": "B"}, "Nov 18")
    assert 'href="builds/rg/"' in home and 'target="_blank"' not in home.split("<script>")[0]   # tiles open the build's page


def test_tabs_show_only_the_pages_that_were_built():
    from tools.build_showcase import tabs
    assert tabs(["work"], "work") == ""                                   # one page needs no tabs
    t = tabs(["work", "daily"], "daily", "../")
    assert 'href="../"' in t and 'href="../daily/"' in t and "Collections" not in t
    assert t.count('aria-current="page"') == 1 and 'aria-current="page">Daily builds' in t


def test_homepage_pages_are_fetched_in_full_even_over_the_clone_filter(tmp_path):
    import os
    import subprocess
    yml = tmp_path / "index.yml"
    yml.write_text(yaml.safe_dump({"config": {}, "sprint_log": [], "showcase": [
        {"key": "big", "repo": "app", "file": "web/app page.html"},
        {"key": "pinned", "repo": "app2", "file": "web/a.html", "file_commit": "abc123"},
        {"key": "here", "file": "showcase/x.html"},          # this repo's own file: already in the checkout
        {"key": "link", "repo": "app3", "url": "https://x.io/"},
        "not an entry"]}))
    out = subprocess.run([sys.executable, str(Path(__file__).resolve().parent.parent / "tools" / "sprint_files.py")],
                         capture_output=True, text=True, check=True, env={**os.environ, "INDEX_YML": str(yml)}).stdout.splitlines()
    assert out == ["app HEAD web/app page.html", "app2 abc123 web/a.html"]


def test_sprint_sections_move_single_builds_into_their_own_section():
    from tools.build_showcase import sprint_builds
    rows = [{"project": "Simon Says Piano", "repo": "simon_says", "date": "2026-11-16", "href": "a.html"},
            {"project": "Ludo", "repo": "ludo", "date": "2026-11-10", "href": "b.html"}]
    tiles, _ = sprint_builds(rows, [], [], {}, "me", "https://b/", section="november_sprint",
                             sections={"simon_says_piano": "music"})
    by = {t["key"]: t["section"] for t in tiles}
    assert by == {"simon_says_piano": "music", "ludo": "november_sprint"}


def test_streak_counts_back_from_today_or_yesterday():
    from datetime import date
    from tools.site_pages import streak
    days = {"2026-11-15", "2026-11-16", "2026-11-17"}
    assert streak(days, date(2026, 11, 17)) == 3
    assert streak(days, date(2026, 11, 18)) == 3          # today's build isn't in yet
    assert streak(days, date(2026, 11, 19)) == 0


def test_daily_items_describe_each_build_by_its_homepage_tile():
    from tools.site_pages import daily_items
    rows = [{"project": "Simon Says Piano", "date": "2026-11-16", "repo": "simon_says", "day_href": "2026-11-16-simon.html"},
            {"project": "Roster Grader", "date": "2026-11-09", "repo": "nfl", "day_href": None},
            {"project": "Secret Thing", "date": "2026-11-10", "repo": "private_repo", "day_href": "x.html"}]
    tiles = [{"key": "simon_says_piano", "title": "Simon Says Piano", "tagline": "Listen", "href": "https://s.io/a.html", "section": "music",
              "day_key": "simon_says|simon_says_piano", "repo": None},
             {"key": "nfl_roster_grader", "title": "Roster Grader", "tagline": "Grades", "href": "nfl_roster_grader/", "section": "data", "repo": "nfl"}]
    items = daily_items(rows, tiles, {"nfl_roster_grader"}, {"music": "Music"}, "https://s.io/", lambda t: t["key"])
    by = {i["title"]: i for i in items}
    assert set(by) == {"Simon Says Piano", "Roster Grader"}                  # no tile, not listed: nothing new goes public
    assert by["Simon Says Piano"]["day_href"] == "https://s.io/2026-11-16-simon.html" and by["Simon Says Piano"]["topic_title"] == "Music"
    assert by["Roster Grader"]["href"] == "../nfl_roster_grader/" and by["Roster Grader"]["featured"]
    assert by["Roster Grader"]["topic_title"] == "Data" and by["Roster Grader"]["thumb"] == "nfl_roster_grader"


def test_the_daily_page_filters_by_topic_day_and_search_and_sorts(tmp_path):
    from datetime import date
    from playwright.sync_api import sync_playwright
    from tools.site_pages import render_daily
    mk = lambda d, t, topic, feat=False: {"date": d, "title": t, "tagline": t.lower(), "topic": topic, "topic_title": topic.title(),
                                          "href": "#", "day_href": "https://s.io/d.html", "featured": feat, "thumb": t[0]}
    items = [mk("2026-10-30", "Zeta", "games"), mk("2026-11-01", "Alpha", "games"), mk("2026-11-02", "Mid", "data", True), mk("2026-11-03", "Beta", "data")]
    html = render_daily(items, {"title": "B", "sprint_url": "https://s.io/"}, "Nov 3", ["work", "daily"], date(2026, 11, 3))
    assert 'aria-current="page">Daily builds' in html and 'href="../"' in html
    assert '<b>4</b><span>days shipped</span>' in html
    assert '<b>3</b><span>day streak</span>' in html   # Oct 31 is missing
    assert "<b>1</b><span>now featured</span>" in html
    page = tmp_path / "index.html"
    page.write_text(html)
    with sync_playwright() as p:
        b = p.chromium.launch()
        try:
            pg = b.new_page(viewport={"width": 390, "height": 844})
            pg.goto(page.as_uri())
            shown = lambda: pg.eval_on_selector_all("#list .r:not([hidden])", "rs => rs.map(r => r.querySelector('.txt a').textContent)")
            months = lambda: pg.eval_on_selector_all("#list .month:not([hidden]) b", "bs => bs.map(b => b.textContent)")
            assert shown() == ["Beta", "Mid", "Alpha", "Zeta"] and months() == ["November 2026", "October 2026"]
            assert pg.get_attribute('.heat button[data-d="2026-11-02"]', "class") == "featured"
            assert pg.is_disabled('.heat button[data-d="2026-11-04"]')
            pg.click('.chips button[data-t="data"]')
            assert shown() == ["Beta", "Mid"] and months() == ["November 2026"]
            pg.click('.chips button[data-t="all"]')
            pg.click('.heat button[data-d="2026-11-01"]')
            assert shown() == ["Alpha"] and pg.is_visible("#only")
            pg.click("#clear")
            pg.select_option("#sort", "old")
            assert shown() == ["Zeta", "Alpha", "Mid", "Beta"] and months() == ["October 2026", "November 2026"]
            pg.select_option("#sort", "az")
            assert shown() == ["Alpha", "Beta", "Mid", "Zeta"] and months() == []
            pg.fill("#q", "nov 2")
            assert shown() == ["Mid"]
            assert pg.evaluate("document.documentElement.scrollWidth <= innerWidth")
        finally:
            b.close()


def test_the_work_page_shows_tabs_once_the_daily_page_exists():
    from tools.build_showcase import render
    e = {"key": "a", "title": "A", "tagline": "a", "href": "a/", "section": "tools"}
    assert 'class="tabs"' not in render([e], {}, {"title": "B"}, "Nov 9")
    page = render([e], {}, {"title": "B"}, "Nov 9", pages=["work", "daily"])
    assert 'aria-current="page">Work' in page and 'href="daily/"' in page


def test_collections_keep_only_homepage_builds_and_skip_thin_ones():
    from tools.site_pages import collections_plan
    tiles = {"a": {}, "b": {}, "c": {}}
    raw = [{"key": "one", "title": "One", "color": "#abc", "builds": [{"key": "a", "why": "first"}, "b", {"key": "ghost"}]},
           {"key": "thin", "title": "Thin", "builds": ["a", "nope"]},
           {"key": "one", "title": "Dup", "builds": ["a", "b"]},
           {"key": "bad", "title": "Bad color", "color": "red;}x{", "builds": ["a", "c"]}]
    colls, warn = collections_plan(raw, tiles)
    assert [c["key"] for c in colls] == ["one", "bad"]
    assert [b["key"] for b in colls[0]["builds"]] == ["a", "b"] and colls[0]["builds"][0]["why"] == "first"
    assert colls[1]["color"] == "#78716c"                                 # only a plain hex colour reaches the page
    assert any("ghost" in w for w in warn) and any("thin" in w for w in warn) and any("unique key" in w for w in warn)
    assert collections_plan(5, tiles)[0] == [] and collections_plan([{"key": "x", "title": "X", "builds": 3}], tiles)[0] == []   # bad shapes never crash


def test_entries_cant_take_the_sites_own_folders():
    from tools.build_showcase import plan
    ok, held = plan([{"key": k, "title": k, "tagline": "t", "url": "https://x.io/"} for k in ("daily", "collections", "img", "fine")], {})
    assert [e["key"] for e in ok] == ["fine"] and len(held) == 3


def test_collection_pages_link_builds_from_their_own_depth(tmp_path):
    from playwright.sync_api import sync_playwright
    from tools.site_pages import collections_plan, render_collections
    tiles = {"rg": {"key": "rg", "title": "Roster <Grader>", "tagline": "Grades", "href": "rg/"},
             "fd": {"key": "fd", "title": "4th Down", "tagline": "Go or kick", "href": "https://s.io/fd.html"}}
    colls, _ = collections_plan([{"key": "nfl", "title": "NFL", "blurb": "Calls", "builds": [{"key": "rg", "why": "Start"}, "fd"]},
                                 {"key": "two", "title": "Two", "builds": ["fd", "rg"]}], tiles)
    pages = render_collections(colls, tiles, {"rg"}, {"title": "B"}, "Nov 18", ["work", "collections", "daily"],
                               lambda t, pre: f'<img src="{pre}img/{t["key"]}.png">')
    assert set(pages) == {"index.html", "nfl/index.html", "two/index.html"}
    nfl = pages["nfl/index.html"]
    assert 'href="../../builds/rg/"' in nfl and 'href="../../builds/fd/"' in nfl and 'src="../../img/rg.png"' in nfl
    assert "Roster &lt;Grader&gt;" in nfl and "<Grader>" not in nfl and nfl.count("FEATURED") == 1
    assert 'aria-current="page">Collections' in nfl and 'href="../../daily/"' in nfl and 'href="../two/"' in nfl
    assert 'href="nfl/"' in pages["index.html"] and 'src="../img/rg.png"' in pages["index.html"]
    for rel, html in pages.items():
        (tmp_path / rel).parent.mkdir(parents=True, exist_ok=True)
        (tmp_path / rel).write_text(html)
    with sync_playwright() as p:
        b = p.chromium.launch()
        try:
            pg = b.new_page(viewport={"width": 390, "height": 844})
            pg.goto((tmp_path / "index.html").as_uri())
            pg.click('a.cl[href="nfl/"]')
            assert pg.url.endswith("/nfl/index.html") or pg.url.endswith("/nfl/")
            assert pg.evaluate("document.documentElement.scrollWidth <= innerWidth")
        finally:
            b.close()

