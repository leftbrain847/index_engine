"""Remove every link to pages that were held back, so a published site never points at a missing page.

    python tools/unlink.py SITE_DIR LIST_FILE [URL_PREFIX]

LIST_FILE holds one held-back page path per line (relative to the site that dropped it). Links are matched by that
relative path (inside the same site) and by URL_PREFIX + path (from another site, e.g. the homepage linking to the
sprint site). An <a> pointing at one is removed with its text; data-* attributes holding one are emptied, together
with their label attribute (data-day -> data-dayl), so the page's script shows no link.
"""
from __future__ import annotations

import html
import re
import sys
from pathlib import Path
from urllib.parse import quote


def _forms(target: str) -> set[str]:
    out = {target, quote(target), quote(target, safe="/:")}
    return out | {html.escape(t) for t in out}


def unlink_text(text: str, targets: list[str]) -> str:
    for t in targets:
        for f in _forms(t):
            href = re.escape(f)
            text = re.sub(rf'<a\b[^>]*\bhref=["\'](?:\./)?{href}(?:#[^"\']*)?["\'][^>]*>.*?</a>', "", text, flags=re.S | re.I)
            def empty(m):
                return f'{m.group(1)}=""'
            text, n = re.subn(rf'(\bdata-[\w-]+)=["\']{href}["\']', empty, text)
            if n:   # the attribute's label (data-day -> data-dayl) goes too
                text = re.sub(r'\b(data-[\w-]+)=""([^>]*?)\b\1l=["\'][^"\']*["\']', r'\1=""\2\1l=""', text)
    return text


def unlink(site: Path, rels: list[str], prefix: str = "") -> list[str]:
    """Rewrite every .html file under site; returns the files that changed."""
    targets = [prefix + r for r in rels] if prefix else list(rels)
    if not targets:
        return []
    changed = []
    for p in sorted(site.rglob("*.html")):
        old = p.read_text(errors="replace")
        new = unlink_text(old, targets)
        if new != old:
            p.write_text(new)
            changed.append(p.relative_to(site).as_posix())
    return changed


def main(argv: list[str]) -> int:
    site, listing = Path(argv[0]), Path(argv[1])
    prefix = argv[2] if len(argv) > 2 else ""
    rels = [line.strip() for line in listing.read_text().splitlines() if line.strip()] if listing.exists() else []
    changed = unlink(site, rels, prefix)
    print(f"unlinked {len(rels)} held-back page(s) in {len(changed)} file(s) of {site.name}")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
