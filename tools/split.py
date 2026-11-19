"""Move files across the public/private split, as listed in split.yml.

    python tools/split.py inputs             # print the input paths (space-separated)
    python tools/split.py outputs            # print the output paths
    python tools/split.py copy-in DATA_DIR   # private data repo -> here (replaces the demo copies)
    python tools/split.py copy-out DATA_DIR  # here -> private data repo (only outputs this run produced)
    python tools/split.py scrub FILE VAR...  # replace the values of these env vars in FILE with ***

Prints counts only, never file names, so it is safe in the public Actions log.
"""
from __future__ import annotations

import base64
import os
import shutil
import sys
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[1]


def load(root: Path = ROOT) -> dict:
    cfg = yaml.safe_load((root / "split.yml").read_text())
    for p in cfg["inputs"] + cfg["outputs"]:
        if not p or Path(p).is_absolute() or ".." in Path(p).parts:
            raise ValueError(f"split.yml: unsafe path {p!r}")
    return cfg


def _replace(src: Path, dst: Path) -> None:
    if dst.is_dir() and not dst.is_symlink():
        shutil.rmtree(dst)
    elif dst.exists() or dst.is_symlink():
        dst.unlink()
    dst.parent.mkdir(parents=True, exist_ok=True)
    if src.is_dir():
        shutil.copytree(src, dst, symlinks=True)
    else:
        shutil.copy2(src, dst)


def copy(paths: list[str], src_root: Path, dst_root: Path) -> int:
    """Copy each path that exists under src_root over dst_root. Paths missing from src_root are left alone."""
    n = 0
    for p in paths:
        src = src_root / p
        if src.exists() and not src.is_symlink():
            _replace(src, dst_root / p)
            n += 1
    return n


def scrub(path: Path, values: list[str]) -> int:
    """Blank out secret values that a command may have echoed into the private run log."""
    if not path.exists():
        return 0
    text = path.read_text(errors="replace")
    hits = 0
    forms = []
    for v in values:
        if v and len(v) >= 8:   # the raw value and the basic-auth header form git and curl send
            forms += [v, base64.b64encode(f"x-access-token:{v}".encode()).decode()]
    for f in forms:
        if f in text:
            hits += text.count(f)
            text = text.replace(f, "***")
    if hits:
        path.write_text(text)
    return hits


def main(argv: list[str]) -> int:
    try:
        return _main(argv)
    except Exception as e:   # never a traceback: it would print private paths into the public log
        print(f"split.py: {argv[1] if len(argv) > 1 else ''} failed ({type(e).__name__})", file=sys.stderr)
        return 1


def _main(argv: list[str]) -> int:
    cfg = load()
    cmd = argv[1] if len(argv) > 1 else ""
    if cmd in ("inputs", "outputs"):
        print(" ".join(cfg[cmd]))
    elif cmd == "copy-in" and len(argv) == 3:
        print(f"copied in {copy(cfg['inputs'], Path(argv[2]), ROOT)} of {len(cfg['inputs'])} inputs")
    elif cmd == "copy-out" and len(argv) == 3:
        print(f"copied out {copy(cfg['outputs'], ROOT, Path(argv[2]))} of {len(cfg['outputs'])} outputs")
    elif cmd == "scrub" and len(argv) >= 4:
        print(f"scrubbed {scrub(Path(argv[2]), [os.environ.get(v, '') for v in argv[3:]])} secret values")
    else:
        print(__doc__, file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
