# rating_app

A general listening-rating app (Flask). Point it at any folder of rendered audio
variants — sub-folders are the arms/variants — rate each track on a scorecard you can
swap without touching code, and export a report (Markdown / CSV / JSON).

## Run (Windows)

```powershell
py -m pip install -r requirements.txt
py app.py                      # prompts for the track folder
py app.py --audio "C:\path\to\audio" --label my_run
py app.py --audio "C:\path\to\audio" --scorecard arabic_vocal --blind
```

Open the URL it prints (default `http://127.0.0.1:5000`; if the port is busy it moves to
the next free one, or use `--strict-port`). It works from any directory:
`scorecards/`, `configs/` and `runs/` always live next to `app.py`.

## Concepts

| Thing | What it is | Where |
|---|---|---|
| **Scorecard** | The questions (reusable) | `scorecards/*.json` |
| **Settings** | How the app behaves | CLI / env / config file |
| **Run** | One evaluation: audio folder + scorecard snapshot + results | `runs/<run_id>/` |

A run folder holds `run.json`, `results.json` and `scorecard.snapshot.json` (the
questions as they were when the run started). Same label + a different audio folder
gets its own run, so results never mix. Writes are atomic.

### Scorecards
JSON with `name`, `summary_metric`, and `criteria`. Question types: `rating` (with
optional `scale_labels`), `choice`, `multi_choice`, `boolean`, `number`, `notes`.
Optional per-question fields: `required`, `help`, `show_if` (`eq`, `neq`, `lt`, `lte`,
`gt`, `gte` on another question's key). Edit on the **criteria** page; saving to an
existing name needs "overwrite existing" ticked. Invalid scorecards are rejected with
readable errors — nothing silently falls back.

### Settings precedence
CLI flag > `RATING_*` env var > `--config` file > `<audio>/_rating.json` >
`~/.config/rating_app/config.json` > defaults. The **settings** page shows every
effective value and where it came from. A missing/broken `--config` file is an error.

Legacy files still work when you don't pick a scorecard: `<out>/criteria.json`, then
`<audio>/_criteria.json`; an old `<out>/evaluations.json` is imported once.

### Track metadata
Per-folder `_meta.json` / `_knob.json` and per-track `<track>.json` sidecars are shown in
a "Generation settings" panel and can be report columns (`report_columns`).

### Blind mode (`--blind`)
Order is shuffled with a seed stored in `run.json` (reproducible). Pages and URLs use
opaque IDs (`/track/b01`); folder names, filenames and metadata are hidden while rating.
Reports reveal everything.

### Reports
`/report` (view), `/report.md`, `/report.csv`, `/report.json`: per-arm mean / stddev / N
for every rating question, choice distributions, score table, notes, orphaned
(removed-question) answers, and the raw data.

## Safety notes
- Default host is `127.0.0.1`. The server-side folder browser is unrestricted only on
  loopback; on any other host it is confined to the working directory.
- Audio is served by track id, never by user-supplied path.

## Tests
```powershell
py -m pip install -r requirements-dev.txt
py -m pytest                       # runs with coverage; fails if total coverage < 90%
py -m pytest -k blind --no-cov     # quick, targeted run
py -m pytest --cov-report=html     # browse htmlcov/index.html
```
Tests are hermetic (see `tests/conftest.py`): they never read your real `~/.config`,
never see `RATING_*` env vars, and never write into the repo's `scorecards/`, `configs/`
or `runs/`. Coverage settings, warnings-as-errors and the 90% gate live in `pyproject.toml`.
Design notes and the migration history: [`docs/PYTEST_MIGRATION_PLAN.md`](docs/PYTEST_MIGRATION_PLAN.md).
