"""The refresh's speed-ups: unchanged pages reuse last night's result, and pages are checked in parallel."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import uat  # noqa: E402


def test_fingerprint_changes_with_the_page_the_checks_or_the_settings():
    cfg = {"settle_ms": 1200, "workers": 4}
    f = lambda html="<p>a</p>", name="n", c=cfg, shims=None, off=False: uat.fingerprint(html, name, c, shims or {}, off)
    base = f()
    assert base == f(c=dict(cfg))                                   # same page, same checks: reuse
    assert base == f(c={**cfg, "workers": 1})                       # parallelism isn't a reason to retest
    assert base != f(html="<p>b</p>")                               # page changed: retest
    assert base != f(name="renamed")                                # expected title changed: retest
    assert base != f(c={**cfg, "settle_ms": 900})                   # uat.yml changed: retest
    assert base != f(shims={"x": "y"}) and base != f(off=True)


def test_only_finished_recent_results_without_night_dependent_issues_are_reused():
    from datetime import date
    today = date(2026, 11, 9)
    ok = {"status": "warn", "tested_on": "2026-11-05", "issues": [{"check": "small-tap-targets", "detail": "x"}]}
    assert uat.reusable(ok, today)
    assert not uat.reusable({**ok, "tested_on": "2026-11-01"}, today)                         # too old
    assert not uat.reusable({**ok, "tested_on": None}, today)
    assert not uat.reusable({**ok, "status": "error"}, today)
    assert not uat.reusable({**ok, "issues": [{"check": "failed-requests", "detail": "cdn"}]}, today)   # a CDN hiccup
    assert not uat.reusable({**ok, "issues": [{"check": "js-errors", "detail": "page did not load: Timeout"}]}, today)


def test_parallel_keeps_order_and_uses_a_real_browser_per_worker():
    items = list(range(6))
    out = uat.parallel(lambda b, i: (i * i, b.is_connected()), items, workers=3)
    assert out == [(i * i, True) for i in items]
    assert uat.parallel(lambda b, i: i, [], workers=3) == []


def test_a_crashing_check_is_reported_not_swallowed():
    def boom(b, i):
        raise RuntimeError("bad page")
    try:
        uat.parallel(boom, [1, 2], workers=2)
    except RuntimeError as e:
        assert "bad page" in str(e)
    else:
        raise AssertionError("expected the worker's error")
