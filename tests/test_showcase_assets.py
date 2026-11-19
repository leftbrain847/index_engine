import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "tools"))
from assets import ASSETS, asset_files  # noqa: E402
from build_showcase import OFFLINE, copy_assets, offline_files  # noqa: E402


def bare_repo(scan: Path, name: str, files: dict[str, bytes]) -> Path:
    work = scan / f"_{name}"
    for p, data in files.items():
        (work / p).parent.mkdir(parents=True, exist_ok=True)
        (work / p).write_bytes(data)
    git = ["git", "-C", str(work), "-c", "user.name=t", "-c", "user.email=t@example.com"]
    subprocess.run(["git", "init", "-q", str(work)], check=True)
    subprocess.run([*git, "add", "-A"], check=True)
    subprocess.run([*git, "commit", "-qm", "x"], check=True)
    subprocess.run(["git", "clone", "-q", "--bare", str(work), str(scan / f"{name}.git")], check=True)
    return scan / f"{name}.git"


MP3 = bytes(range(256)) * 40   # binary content: must survive byte for byte


def test_copies_media_byte_for_byte_and_skips_other_files(tmp_path):
    bare_repo(tmp_path, "app", {"audio/a.mp3": MP3, "audio/sub/b.mp3": MP3[:10], "audio/notes.txt": b"x", "web/index.html": b"<p>"})
    n, why = copy_assets(tmp_path, "app", "audio", tmp_path / "out" / "app")
    assert (n, why) == (2, None)
    assert (tmp_path / "out/app/audio/a.mp3").read_bytes() == MP3
    assert (tmp_path / "out/app/audio/sub/b.mp3").exists()
    assert not (tmp_path / "out/app/audio/notes.txt").exists()


def test_holds_back_empty_unsafe_or_oversized_folders(tmp_path, monkeypatch):
    bare_repo(tmp_path, "app", {"audio/a.mp3": MP3})
    assert copy_assets(tmp_path, "app", "nothing", tmp_path / "o")[0] == 0
    assert asset_files(tmp_path / "app.git", "../etc") == []
    monkeypatch.setitem(ASSETS, "max_entry_mb", len(MP3) / 2e6)
    n, why = copy_assets(tmp_path, "app", "audio", tmp_path / "o")
    assert n == 0 and "limit" in why and not (tmp_path / "o").exists()


def test_lists_names_without_reading_blobs_and_keeps_accented_names(tmp_path):
    bare_repo(tmp_path, "app", {"audio/año.mp3": MP3})
    work = tmp_path / "filtered.git"   # a blob-filtered clone, like the nightly one: listing must not need the blob
    subprocess.run(["git", "-C", str(tmp_path / "app.git"), "config", "uploadpack.allowFilter", "true"], check=True)
    subprocess.run(["git", "clone", "-q", "--bare", "--filter=blob:limit=1", f"file://{tmp_path / 'app.git'}", str(work)], check=True)
    assert asset_files(work, "audio") == ["audio/año.mp3"]
    n, why = copy_assets(tmp_path, "filtered", "audio", tmp_path / "o")   # blob missing: held back, nothing fetched
    assert n == 0 and "not fetched" in why


def test_model_files_copy_and_stay_out_of_the_offline_copy(tmp_path):
    bare_repo(tmp_path, "app", {"audio/live/m.onnx": MP3, "audio/live/ort.wasm": MP3, "audio/live/ort.mjs": b"export{}",
                                "audio/live/ids.json": b"{}", "audio/live/dora.voice": MP3, "audio/live/notes.md": b"x"})
    n, why = copy_assets(tmp_path, "app", "audio", tmp_path / "out" / "app")
    assert (n, why) == (5, None) and not (tmp_path / "out/app/audio/live/notes.md").exists()
    (tmp_path / "out/app/index.html").write_text("<p>")
    files = offline_files(tmp_path / "out")
    assert not any(f.endswith((".onnx", ".wasm", ".voice", ".mp3")) for f in files)


def test_offline_copy_leaves_media_out(tmp_path):
    (tmp_path / "app/audio").mkdir(parents=True)
    (tmp_path / "app/index.html").write_text("<p>")
    (tmp_path / "app/audio/a.mp3").write_bytes(MP3)
    files = offline_files(tmp_path)
    assert "app/" in files and not any(f.endswith(".mp3") for f in files)
    assert ".mp3" in OFFLINE["skip_ext"]
