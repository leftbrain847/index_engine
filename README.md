# index_engine

The engine behind J's project index: it scans his GitHub repos, grades them against `rubric.yml`, builds the Command Center page, and publishes the public homepage.

## How it's split
- **This repo (public): code.** Scanner, grader, page builders, tests and workflows.
- **`index` (private): data.** The real `index.yml` (projects, notes, homepage tiles, sprint log), the generated reports and findings, and day snapshots. The nightly refresh reads it and writes its results back to it.
- **Demo data here.** `index.yml` and `findings.json` at the root are made up, so the tests and local dry runs work without private data. The `config:` block in `index.yml` mirrors the real settings.

## Test
```
pip install -r requirements-ci.txt
python -m playwright install chromium
pytest -q
```
CI runs this on every PR and after each merge to `main`.

## Dry run (made-up repos, nothing pushed)
```
python tools/fixture_scan.py /tmp/scan && tools/dry_run.sh /tmp/scan
```

## Nightly refresh
Added in a follow-up PR: a scheduled workflow that checks out `index`, runs the engine, commits the results back to `index`, and publishes the homepage.

## Contributing
Outside PRs are welcome, but their workflow runs wait for the owner's approval and they are never merged automatically.
