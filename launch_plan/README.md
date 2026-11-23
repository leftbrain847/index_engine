# launch_plan

Tooling for a private checklist page with Listen mode: collapsible steps you can tick, and recorded audio for each
section. The audio uses Kokoro `af_heart`, with one MP3 per section plus marks per step, and plays in a bottom player
bar with a queue, repeat, previous/next and speed. The player is copied from field_guide.

**Code here, content elsewhere.** This folder is public and holds no plan content. The content lives in a data
folder in the private `index` repo (`launch_plan/`): `plan.json` and `audio/`. Every script takes `--data DIR`.

| File | What it does |
| --- | --- |
| `template.html` | The page: layout, ticks (saved in the viewer's browser), Listen player. Data is injected at `/*PLAN*/` and `/*REC*/`. |
| `sections.py` | The sections and their spoken text. One source for both scripts below. |
| `audio.py` | Records `DATA/audio/<section>.mp3` and `marks.json`. A section is re-recorded only when its text changes. |
| `build.py` | Writes `DATA/index.html` from the template plus the data. |
| `smoke_listen.py` | A phone-width browser check of the built page. |

## Update the plan

```sh
pip install kokoro-onnx==0.6.1                     # recording only; models from the kokoro-onnx GitHub releases
python3 audio.py --data ../index/launch_plan --models MODELS_DIR
python3 build.py --data ../index/launch_plan
python3 smoke_listen.py --data ../index/launch_plan
```

Then publish `DATA/index.html` and `DATA/audio/*.mp3` to the existing claude.ai artifact, keeping it private. The
built `index.html` is an output and isn't committed.

## plan.json shape

`meta` holds `title` and `lede` (HTML). `rules` is a list of `{t, b}`. `phases` is a list of
`{id, name, sub, steps: [{id, t, m, b}]}`. `repeats` is a list of `{id, name, tag, sub, items}`. `upkeep` is a list of
`{id, name, items}`. `t` is a title, `m` is a time estimate and `b` is the body (HTML). Step ids are also tick keys, so
keep them stable across edits.
