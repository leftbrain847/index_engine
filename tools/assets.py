"""Media folders copied next to a homepage page (showcase `assets:`). Dependency-free on purpose: refresh.yml runs
tools/sprint_files.py, which uses asset_files(), before it installs the Python tools."""
from __future__ import annotations

import os
import subprocess
from pathlib import Path

# Media, plus what an in-browser model needs to run from the same site (an .onnx model, the onnxruntime-web .wasm and
# its .mjs loader, small .json/.voice data). Each file stays under GitHub's 100 MB limit.
MEDIA = (".mp3", ".m4a", ".ogg", ".opus", ".wav", ".webm")
ASSETS = {"ext": MEDIA + (".onnx", ".wasm", ".mjs", ".json", ".voice"), "media": MEDIA, "max_file_mb": 95, "max_entry_mb": 450}


# Never let git fetch a missing blob on its own: the nightly clone skips blobs over 1 MB and holds no credentials, so a
# lazy fetch would go out unauthenticated (failing on a private repo). refresh.yml fetches the listed files itself.
NO_LAZY = {**os.environ, "GIT_NO_LAZY_FETCH": "1"}


def asset_files(bare: Path, folder: str) -> list[str]:
    """Paths of every allowed media file under `folder` on the clone's default branch. Names only: listing reads tree
    objects, never the (possibly filtered-out) blobs."""
    folder = folder.strip("/")
    if not folder or ".." in folder.split("/"):
        return []
    r = subprocess.run(["git", "-C", str(bare), "ls-tree", "-r", "-z", "--name-only", "HEAD", "--", folder + "/"],
                       capture_output=True, env=NO_LAZY)
    names = [n.decode("utf-8", "surrogateescape") for n in r.stdout.split(b"\0") if n]
    return [n for n in names if n.lower().endswith(ASSETS["ext"])]
