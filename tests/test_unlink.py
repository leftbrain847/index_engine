"""Containing a bad page: site_check --drop-bad holds it back and tools/unlink.py removes every link to it."""
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "tools"))
sys.path.insert(0, str(ROOT))
import site_check  # noqa: E402
from unlink import unlink, unlink_text  # noqa: E402

DAY = "https://o.github.io/sprint/2026-11-27-x.html"


def test_links_to_a_held_back_page_are_removed_and_others_kept():
    page = (f'<a class="tile" href="builds/x/" data-day="{DAY}" data-dayl="Day build · Nov 27" data-clip="v">X</a>'
            f'<a class="build" href="{DAY}">Day build · Nov 27</a>'
            '<a class="build" href="https://o.github.io/sprint/2026-11-26-y.html">Day build · Nov 26</a>')
    out = unlink_text(page, [DAY])
    assert DAY not in out
    assert 'data-day="" data-dayl=""' in out and 'href="builds/x/"' in out   # the tile stays, its day link goes
    assert "2026-11-26-y.html" in out


def test_relative_links_inside_the_same_site(tmp_path):
    (tmp_path / "index.html").write_text('<li><a class="build" href="2026-11-27-x.html">Day</a>'
                                         '<a class="build" href="./2026-11-27-x.html#top">Day</a>'
                                         '<a href="2026-11-26-y.html">Y</a></li>')
    assert unlink(tmp_path, ["2026-11-27-x.html"]) == ["index.html"]
    assert (tmp_path / "index.html").read_text() == '<li><a href="2026-11-26-y.html">Y</a></li>'


def test_prefix_and_escaped_forms(tmp_path):
    rel = "2026-11-27 - x.html"
    (tmp_path / "a.html").write_text('<a href="https://o.github.io/sprint/2026-11-27%20-%20x.html">x</a><p>keep</p>')
    unlink(tmp_path, [rel], "https://o.github.io/sprint/")
    assert (tmp_path / "a.html").read_text() == "<p>keep</p>"


def test_nothing_held_back_changes_nothing(tmp_path):
    (tmp_path / "a.html").write_text(f'<a href="{DAY}">x</a>')
    assert unlink(tmp_path, []) == []


def test_drop_bad_publishes_the_rest_and_unlinks(tmp_path, monkeypatch):
    (tmp_path / "index.html").write_text('<a href="bad.html">bad</a><a href="good.html">good</a>')
    (tmp_path / "good.html").write_text("<p>ok</p>")
    (tmp_path / "bad.html").write_text('<script src="https://unpkg.com/x@1/x.js"></script>')
    listing = tmp_path.parent / "dropped.txt"
    assert site_check.main([str(tmp_path), "--no-browser", "--drop-bad", str(listing)]) == 0
    assert not (tmp_path / "bad.html").exists() and (tmp_path / "good.html").exists()
    assert listing.read_text() == "bad.html\n"
    assert (tmp_path / "index.html").read_text() == '<a href="good.html">good</a>'


def test_without_drop_bad_one_bad_page_still_blocks_the_site(tmp_path):
    (tmp_path / "bad.html").write_text('<script src="https://unpkg.com/x@1/x.js"></script>')
    assert site_check.main([str(tmp_path), "--no-browser"]) == 1
    assert (tmp_path / "bad.html").exists()


def test_site_wide_problems_are_not_dropped(monkeypatch, tmp_path):
    (tmp_path / "a.html").write_text("<p>a</p>")
    monkeypatch.setattr(site_check, "check", lambda *a, **k: {"site_deps": ["lock mismatch"]})
    assert site_check.main([str(tmp_path), "--no-browser", "--drop-bad", str(tmp_path.parent / "d.txt")]) == 1
    assert (tmp_path / "a.html").exists()


def test_front_page_or_too_many_failures_block_the_site_instead(tmp_path):
    bad = '<script src="https://unpkg.com/x@1/x.js"></script>'
    for name in ("index.html", "a.html"):
        (tmp_path / name).write_text(bad)
    assert site_check.main([str(tmp_path), "--no-browser", "--drop-bad", str(tmp_path.parent / "d.txt")]) == 1
    assert (tmp_path / "index.html").exists() and (tmp_path / "a.html").exists()
    many = tmp_path.parent / "many"
    many.mkdir()
    for i in range(site_check.MAX_DROP + 1):
        (many / f"p{i}.html").write_text(bad)
    assert site_check.main([str(many), "--no-browser", "--drop-bad", str(tmp_path.parent / "d2.txt")]) == 1
    assert len(list(many.glob("*.html"))) == site_check.MAX_DROP + 1


def test_review_cases_data_href_unrelated_labels_and_subfolders(tmp_path):
    out = unlink_text('<a data-href="x.html" href="y.html">Y</a><div data-k="" data-kl="keep"></div>', ["x.html"])
    assert out == '<a data-href="" href="y.html">Y</a><div data-k="" data-kl="keep"></div>'   # link to y stays
    (tmp_path / "sub").mkdir()
    (tmp_path / "sub" / "x.html").write_text("<p>a different page</p>")
    (tmp_path / "sub" / "index.html").write_text('<a href="x.html">mine</a><a href="../x.html">root</a>')
    unlink(tmp_path, ["x.html"])
    assert (tmp_path / "sub" / "index.html").read_text() == '<a href="x.html">mine</a>'


def test_bad_arguments_exit_cleanly(tmp_path):
    import pytest
    with pytest.raises(SystemExit):
        site_check.main([str(tmp_path), "--drop-bad"])
