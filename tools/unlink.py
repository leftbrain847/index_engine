"""Remove every link to pages that were held back, so a published site never points at a missing page.

    python tools/unlink.py SITE_DIR LIST_FILE [URL_PREFIX]

LIST_FILE holds one held-back page path per line (relative to the root of the site that dropped it).
Without URL_PREFIX (the same site): links are matched relative to each page's own folder.
With URL_PREFIX (another site, e.g. the homepage linking to the sprint site): links are matched as URL_PREFIX + path.
An <a> whose href is one of them is removed with its text; a data-* attribute holding one is emptied, together with
its label attribute on the same tag (data-day -> data-dayl), so the page's script shows no link.
"""
from __future__ import annotations

import argparse
import html
import os
import re
from pathlib import Path
from urllib.parse import quote

ATTR = r'(?<![\w-])'   # so "href" never matches inside "data-href"


def _forms(target: str) -> set[str]:
    out = {target, quote(target), quote(target, safe="/:")}
    return out | {html.escape(t) for t in out}


def _alt(targets: list[str]) -> str:
    return "|".join(sorted((re.escape(f) for t in targets for f in _forms(t)), key=len, reverse=True))


def unlink_text(text: str, targets: list[str]) -> str:
    if not targets:
        return text
    alt = _alt(targets)
    value = rf'["\'](?:\./)?(?:{alt})(?:#[^"\']*)?["\']'
    text = re.sub(rf'<a\b[^>]*{ATTR}href={value}[^>]*>.*?</a>', "", text, flags=re.S | re.I)

    def tag(m: re.Match) -> str:
        t = m.group(0)
        names = re.findall(rf'{ATTR}(data-[\w-]+)={value}', t)
        for n in names:
            t = re.sub(rf'{ATTR}{re.escape(n)}={value}', f'{n}=""', t)
            t = re.sub(rf'{ATTR}{re.escape(n)}l=["\'][^"\']*["\']', f'{n}l=""', t)   # its label, same tag only
        return t
    return re.sub(r'<[a-zA-Z][^<>]*>', tag, text)


def unlink(site: Path, rels: list[str], prefix: str = "") -> list[str]:
    """Rewrite every .html file under site; returns the files that changed."""
    if not rels:
        return []
    changed = []
    for p in sorted(site.rglob("*.html")):
        here = p.parent.relative_to(site).as_posix()
        if prefix:
            targets = [prefix + r for r in rels]
        else:
            targets = [os.path.relpath(r, here).replace(os.sep, "/") for r in rels] + ["/" + r for r in rels]
        old = p.read_text(errors="replace")
        new = unlink_text(old, targets)
        if new != old:
            p.write_text(new)
            changed.append(p.relative_to(site).as_posix())
    return changed


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="unlink.py")
    ap.add_argument("site", type=Path)
    ap.add_argument("listing", type=Path)
    ap.add_argument("prefix", nargs="?", default="")
    a = ap.parse_args(argv)
    rels = [ln.strip() for ln in a.listing.read_text().splitlines() if ln.strip()] if a.listing.exists() else []
    changed = unlink(a.site, rels, a.prefix)
    print(f"unlinked {len(rels)} held-back page(s) in {len(changed)} file(s) of {a.site.name}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
