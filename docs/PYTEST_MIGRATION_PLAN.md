# Migration plan: test suite → pytest (≥ 90 % coverage)

**Status: executed.** 297 tests pass (none skipped/xfailed), and total coverage is
**100 % (line + branch)** against a **90 % hard gate**.

| | Before | After |
|---|---|---|
| Tests | 37 (4 files `unittest.TestCase`, 2 pytest-style) | 297 passed, all pytest-style |
| Coverage | 83 % lines; `app.py` not measured, `cli.py` 0 % | 100 % lines **and** branches, incl. `app.py` |
| Config | none | `pyproject.toml` (pytest + coverage + gate) |
| Isolation | read real `~/.config`, `RATING_*` env, wrote to real `scorecards/`; passed only from repo root | fully hermetic, cwd-independent, order-independent |
| Runtime | ~1 s | ~3.5 s |

---

## 1. Audit of the starting point

| Finding | Why it matters | Resolution |
|---|---|---|
| `unittest.TestCase` + `tempfile.TemporaryDirectory()` + `self.assertX` in `test_store/paths/settings/scorecard/discovery_and_report/phase0_baseline` | Verbose, no parametrization, no fixture reuse | Converted to plain functions, `tmp_path`, bare `assert`, `@pytest.mark.parametrize` |
| `test_phase0_baseline.py` contained a Flask import stub | Dead code (Flask is a hard requirement) and masks import errors | Deleted; its 4 tests merged into the matching module files |
| `test_settings` mutated `os.environ` by hand and read the developer's real `~/.config/rating_app/config.json` | Flaky on any machine with a config or `RATING_*` vars | Autouse `_clean_env` fixture (`monkeypatch` HOME + unset `RATING_*`) |
| `test_scorecard` used cwd-relative paths (`"scorecards/default.json"`) | Only passes from the repo root | Absolute paths from `conftest.REAL_SCORECARDS`; parametrized over every shipped scorecard |
| A web test **wrote to the real `scorecards/default.json`** (restored in `finally`) | A crash mid-test corrupts the repo | Autouse `app_root` fixture copies `scorecards/` + `configs/` to a temp dir and re-points every module constant |
| Audio responses from `send_from_directory` never closed | `ResourceWarning` (surfaced once warnings became errors) | Responses used as context managers |
| No `pyproject.toml` / `conftest.py`; `app.py`, `cli.py` untested | No coverage gate, duplicated helpers (`_wav`, `make_client`) | See §2–§3 |

## 2. Target layout

```
pyproject.toml              pytest + coverage config, 90 % gate, warnings = errors
requirements-dev.txt        + pytest-cov
tests/
  conftest.py               hermetic env, sandboxed app root, wav/audio/make_client fixtures
  test_cli.py               argparse surface
  test_settings.py          layered precedence, coercion, error handling
  test_paths.py             app_path, loopback, safe directory browsing
  test_scorecard.py         validate / normalize / load / list (+ every shipped card)
  test_discovery.py         metadata sidecars, grouping, blinding, is_done
  test_store.py             atomic writes, Run, drift, legacy migration
  test_report.py            Markdown / CSV / JSON renderers + stats
  test_web_routes.py        every endpoint via Flask test client
  test_app_entrypoint.py    app.py main(), prompt flow, __main__ guard
```
One test module per source module, so a failing test points straight at its owner.

## 3. Shared fixtures (`tests/conftest.py`)

| Fixture | Scope | Purpose |
|---|---|---|
| `_clean_env` (autouse) | function | Temp `HOME`/`USERPROFILE`; removes every `RATING_*` variable |
| `app_root` (autouse) | function | Temp copy of `scorecards/` + `configs/`; monkeypatches `APP_ROOT`, `SCORECARD_DIR`, `CONFIG_DIR` in `paths`, `scorecard`, `settings`, `web` |
| `wav` / `audio` | function | Tiny real WAV files generated on the fly (no binaries committed); `armA/{t1,t2}`, `armB/t1` with metadata |
| `make_client` | function | Factory → `(test_client, settings)` with CLI-style overrides |
| `track_links`, `full_scorecard` | function | Index-page link scraper; scorecard covering all 6 question types |

## 4. Phases

| # | Phase | Output | Gate |
|---|---|---|---|
| 0 | Baseline | 37 pass, 83 % (app.py unmeasured) | recorded above |
| 1 | Tooling | `pyproject.toml`, `pytest-cov`, `conftest.py`; strict mode (`--strict-markers`, `--strict-config`, `filterwarnings=error`) | suite still green |
| 2 | Mechanical conversion | unittest → pytest, flask stub removed, phase-0 tests merged | same behaviours asserted, no coverage loss |
| 3 | Isolation | env/HOME/app-root sandboxing; cwd-independent paths | passes from any cwd, any file order, each file alone |
| 4 | Gap-filling | error branches, CLI, `app.py`, web endpoints, report/CSV edge cases | ≥ 90 % → achieved 100 % |
| 5 | Verify the tests, not just the lines | 8-mutant spot check (inverted comparison, removed traversal guard, leaked blind metadata, …) | all mutants killed |
| 6 | Docs / CI | README "Tests" section; optional CI snippet below | – |

## 5. Coverage policy

* `pyproject.toml`: `source = ["rating_app", "app"]`, **branch coverage on**, `--cov-fail-under=90`.
* Entry point covered three ways: `main()` with a fake Flask app, the interactive prompt (fake TTY),
  and `runpy` with `__name__ == "__main__"` and the repo root removed from `sys.path`.
* No `# pragma: no cover` and no omitted files.
* The gate is a floor; the suite is at 100 %, so any new untested code is visible immediately.

## 6. Bugs found along the way (all fixed; were strict `xfail`s)

A strict `xfail` fails the build the day the bug is fixed, forcing the marker to be removed.
All three below were fixed and their markers deleted; the tests now assert the correct
behaviour instead of pinning the bug.

| Test | Bug | Fix applied |
|---|---|---|
| `test_cli_default_for_out_must_not_mask_env` | `--out` had an argparse default (`./rating_out`), so it was always a "CLI argument" and **silently overrode `RATING_OUT` and config-file values** (README promises CLI > env > file) | `default=None` in `cli.py` (the `Settings` default already is `./rating_out`) |
| `test_run_id_flag_reaches_settings` | `Settings` had no `run_id` field, so **`--run-id` was silently dropped** (`web.create_app` reads `getattr(settings, "run_id", None)`) | added `run_id: str = ""` to `Settings` |
| `test_scorecard_library_is_independent_of_cwd[/setup, /criteria]` | `list_scorecards(settings.scorecard_dirs)` got raw relative dirs; the **scorecard library was empty unless cwd was the app folder** (`resolve_scorecard` correctly uses `app_path`) | `list_scorecards([app_path(d) for d in settings.scorecard_dirs])` at both call sites |

Also noted (not asserted): `POST /criteria` writes straight into the real `scorecards/` folder
(now sandboxed in tests), and a blank `--label ""` is replaced by the folder name while its
source still reads "CLI argument".

## 7. Running

```bash
pip install -r requirements-dev.txt
pytest                         # full suite + coverage + 90 % gate
pytest -k "blind or drift" --no-cov
pytest --cov-report=html       # open htmlcov/index.html
```

## 8. Optional CI (`.github/workflows/tests.yml`)

```yaml
name: tests
on: [push, pull_request]
jobs:
  pytest:
    strategy:
      matrix: { os: [ubuntu-latest, windows-latest], python: ["3.10", "3.12"] }
    runs-on: ${{ matrix.os }}
    steps:
      - uses: actions/checkout@v4
      - uses: actions/setup-python@v5
        with: { python-version: "${{ matrix.python }}" }
      - run: pip install -r requirements-dev.txt
      - run: pytest
```
(First Windows run, Python 3.12 / pytest-cov 6.1.1, surfaced two problems that were then fixed: an `/etc`-based browse assertion, and a coverage `DataError` because pytest-cov 6.x does not forward `branch` from the config to subprocesses. The suite now sets `--cov-branch` explicitly and strips `COV_CORE_*` from the subprocess test. The symlink tests self-skip where symlinks aren't permitted, e.g. Windows without Developer Mode.)

## 9. Follow-ups worth considering

1. ~~Fix the three bugs above and delete their `xfail` markers.~~ **Done** (see §6).
2. Add `pytest-randomly` to keep order-independence honest.
3. Browser-side `show_if` logic lives in template JS and is untested; a Playwright smoke test would cover it.
