"""Record Listen's audio: one MP3 per section, plus marks for each item. Copied from field_guide's build/audio.py.

    pip install kokoro-onnx==0.6.1
    python3 audio.py --data DATA --models DIR   # DATA = the content folder (plan.json); DIR holds kokoro-v1.0.onnx
                                                # and voices-v1.0.bin from the kokoro-onnx releases

Writes DATA/audio/<section>.mp3 and DATA/audio/marks.json ({section: {hash, file, marks: [{key, start, end}]}}).
A section is re-recorded only when its spoken text or AUDIO settings change.
"""
import hashlib, json, os, subprocess, sys
from sections import data_dir, item_text, load_plan, sections

AUDIO = {
    "engine": "kokoro", "voice": "af_heart", "lang": "en-us",   # Field Guide's voice
    "model_files": ["kokoro-v1.0.onnx", "voices-v1.0.bin"],
    "lead_in_s": 0.3, "gap_s": 1.0, "level_rms_dbfs": -20,
    "fade_in_s": 0.03, "fade_out_s": 0.05, "bitrate_kbps": 48, "sample_rate": 24000,
}
SETTINGS = [AUDIO[k] for k in ("engine", "voice", "lead_in_s", "gap_s", "level_rms_dbfs", "fade_in_s", "fade_out_s", "bitrate_kbps", "sample_rate")]


def section_hash(sec):
    return hashlib.sha256(json.dumps([[item_text(i) for i in sec["items"]], SETTINGS]).encode()).hexdigest()[:16]


def record(sec, k, out_dir):
    import numpy as np
    rate = 24000
    lead = np.zeros(int(AUDIO["lead_in_s"] * rate), np.float32)
    gap = np.zeros(int(AUDIO["gap_s"] * rate), np.float32)
    parts, marks, pos = [lead], [], len(lead)
    for it in sec["items"]:
        x, rate = k.create(item_text(it), voice=AUDIO["voice"], speed=1.0, lang=AUDIO["lang"])
        x = np.asarray(x, np.float32)
        act = x[np.abs(x) > 0.01]
        if len(act):
            x = np.clip(x * (10 ** (AUDIO["level_rms_dbfs"] / 20) / float(np.sqrt(np.mean(act ** 2)))), -1, 1)
        fi, fo = min(len(x), int(AUDIO["fade_in_s"] * rate)), min(len(x), int(AUDIO["fade_out_s"] * rate))
        if fi: x[:fi] *= np.linspace(0, 1, fi, dtype=np.float32)
        if fo: x[-fo:] *= np.linspace(1, 0, fo, dtype=np.float32)
        marks.append({"key": it["key"], "start": round(pos / rate, 3), "end": round((pos + len(x)) / rate, 3)})
        parts += [x, gap]; pos += len(x) + len(gap)
    pcm = (np.concatenate(parts) * 32767).astype("<i2").tobytes()
    out = os.path.join(out_dir, sec["id"] + ".mp3")
    subprocess.run(["ffmpeg", "-y", "-loglevel", "error", "-f", "s16le", "-ar", str(rate), "-ac", "1", "-i", "pipe:0",
                    "-ar", str(AUDIO["sample_rate"]), "-ac", "1", "-b:a", f"{AUDIO['bitrate_kbps']}k", out], input=pcm, check=True)
    return marks


def main(argv):
    models = argv[argv.index("--models") + 1] if "--models" in argv else "models"
    data = data_dir(argv)
    out_dir = os.path.join(data, "audio")
    os.makedirs(out_dir, exist_ok=True)
    marks_path = os.path.join(out_dir, "marks.json")
    old = json.load(open(marks_path)) if os.path.exists(marks_path) else {}
    out, voice = {}, None
    for sec in sections(load_plan(data)):
        h = section_hash(sec)
        if old.get(sec["id"], {}).get("hash") == h and os.path.exists(os.path.join(out_dir, sec["id"] + ".mp3")):
            out[sec["id"]] = old[sec["id"]]; continue
        if voice is None:
            from kokoro_onnx import Kokoro
            voice = Kokoro(*[os.path.join(models, f) for f in AUDIO["model_files"]])
        out[sec["id"]] = {"hash": h, "file": f"audio/{sec['id']}.mp3", "marks": record(sec, voice, out_dir)}
        print(sec["id"], len(sec["items"]), "items", round(out[sec["id"]]["marks"][-1]["end"] / 60, 1), "min", flush=True)
    json.dump(out, open(marks_path, "w"), indent=0)


if __name__ == "__main__":
    main(sys.argv[1:])
