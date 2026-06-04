# Changelog

All notable changes to this project will be documented here.
Format follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/).
Versioning follows [Semantic Versioning](https://semver.org/).

---

## [Unreleased]

---

## [0.1.5] — 2026-06-04

### Fixed

- **ADO sync failures produced a misleading "Check your connection settings" error**
  (`app.py`): The generic `except Exception` handler showed this message for *all*
  failures, including data-processing errors that occur well after the ADO API
  calls have already succeeded (HTTP 200). The handler now:
  - Calls `logger.exception()` so the **full Python traceback** is written to
    the log file on every failure — previously the stack trace was silently
    discarded, making root-cause analysis impossible.
  - Reserves the "Check your connection settings" wording for HTTP-level errors
    (`requests.HTTPError`) only.
  - Shows the actual exception type and message for all other failures, together
    with the log file path so users can inspect the full traceback.

- **`warnings` column in Sprint capacity schedule was a Python list** (`ado_sync.py`):
  `calculate_sprint_capacity` returned `"warnings": [...]` (a Python list) which
  was stored as an object-dtype list column in `cap_df`. This was inconsistent
  with the `per_user_capacity` column (a JSON string) and a latent serialisation
  risk. All three capacity-source branches (`missing_capacity`, `zero_capacity`,
  and `configured`/`carried_forward`) now return `"warnings": json.dumps([...])`.

### Added

- **10 regression tests** (`tests/test_ado_sync_parallel.py`):
  - Duplicate sprint names with different iteration IDs produce the correct row
    count and do not crash.
  - All-zero capacity rows (`zero_capacity` source) produce valid output and a
    JSON-string `warnings` column.
  - Missing capacity rows mixed with configured sprints do not raise any exception.
  - `warnings` column is always a JSON-parseable string for every `capacity_source`.
  - Carry-forward from the last configured sprint works when the final sprint has
    no capacity data.

- **App settings section in README**: documents the log-level control and log file
  location so users know where to look when a sync error directs them there.

---

## [0.1.4] — 2026-06-02

### Fixed

- **Future sprint capacity reported as zero** (`ado_sync.py` —
  `fetch_capacities_for_sprint`, `_fetch_sprint_metadata`,
  `build_capacity_schedule`): three related bugs caused future sprints with no
  capacity configured in Azure DevOps to be treated as having **zero capacity**,
  breaking Monte Carlo forecasts for any date range beyond the last configured
  sprint.

  **Bug 1 — `cap = 1.0` fallback fabricated capacity (Iteration 13 symptom).**
  When Azure returned capacity rows whose `activities` list was empty or whose
  `capacityPerDay` was 0, the code silently substituted 1.0 h/day per member.
  A team of 8 members over a 10-day sprint therefore reported
  `team_capacity_hours = 80` and `capacity_factor = 1.0` — appearing fully
  available — when Azure was actually recording 0 configured hours.
  The fallback is removed; the new `zero_capacity` source state records this
  honestly.

  **Bug 2 — `max(1.0, …)` fallback zeroed future-sprint simulation days.**
  When no capacity rows were returned at all (common for future sprints), the
  per-sprint `baseline_per_day` was forced to 1.0.  In the per-day loop this
  produced `available = 0.0 / 1.0 = 0.0` → `per_date_ratio[d] = 0.0` for
  every working day in the sprint.  The simulation then treated every day in
  future sprints as a non-working day, making all forecasts pessimistically
  short.  Removing the fallback lets `baseline_per_day = 0.0` trigger the
  `else 1.0` ratio branch, correctly treating unconfigured sprints as
  full-capacity.

  **Bug 3 — no carry-forward for future sprints.**
  A new Phase 1.5 in `build_capacity_schedule` iterates sprints chronologically
  and carries the last `azure_configured` sprint's per-member baseline forward
  to any subsequent `missing_capacity` sprint.  The target sprint's own team
  days off and individual days off (returned by Azure even when capacity is not
  yet configured) are applied on top.  `zero_capacity` sprints are not carried
  forward — they represent an explicit Azure configuration and are preserved
  as-is.

- **`iteration_summary_team_days_off_count` misnamed and incorrectly applied.**
  Azure's `iterationcapacities` → `teamTotalDaysOff` field counts every member
  day off (team-wide and individual), not only team-wide schedule days.  A
  sprint where 7 members each took one individual day off therefore reported
  `iteration_summary_team_days_off_count = 7` even though
  `team_days_off_dates` was blank.  The field has been:
  - renamed to `ado_team_total_days_off_count` (raw Azure value, kept for
    auditing);
  - never used to adjust `planned_working_days` (it never should have been).

- **`per_user_capacity` exported as `[object Object]`.**
  The column stored a Python `list` of dicts in the DataFrame.  In CSV export
  and some Streamlit display contexts this rendered as an unreadable object
  reference.  The column is now serialised with `json.dumps` so it appears as a
  proper JSON string in any export context.

### Added

- **`capacity_source` column** in the Sprint capacity schedule:
  - `azure_configured` — Azure returned capacity rows with at least one
    non-zero `capacityPerDay`.
  - `missing_capacity` — Azure returned no rows; capacity fields are `None`
    (not zero); simulation uses 1.0 ratio.
  - `zero_capacity` — Azure returned rows but every member has 0
    `capacityPerDay`; numeric fields are 0.
  - `carried_forward` — no Azure data; baseline inherited from the most recent
    `azure_configured` sprint.

- **`team_days_off_count` column** — count of working days off for the whole
  team derived from the team days-off endpoint only.  Individual member days
  off do not appear here.

- **`calculate_sprint_capacity` pure function** — all per-sprint arithmetic
  extracted into a standalone, I/O-free function.  Accepts raw ADO inputs and
  returns every capacity field plus `per_date_ratios`.  Fully testable without
  Streamlit or Azure credentials.

- **22 new tests** covering:
  - `missing_capacity` state produces `None` for numeric fields and 1.0
    per_date_ratio (not 0.0).
  - `zero_capacity` state produces 0.0 fields and 0.0 per_date_ratio.
  - Carry-forward: future sprints inherit baseline, team/individual days off
    for the target sprint are still applied.
  - `zero_capacity` is not carried forward.
  - Multiple consecutive future sprints all receive carry-forward.
  - `ado_team_total_days_off_count` is a diagnostic field only; it does not
    reduce `planned_working_days`.
  - `team_days_off_count` counts team-endpoint days only.
  - `per_user_capacity` is a JSON string.

---

## [0.1.3] — 2026-06-02

### Fixed

- **Sprint capacity calculation** (`ado_sync.py` — `fetch_sprints` and
  `build_capacity_schedule`): two bugs that caused `planned_working_days`,
  `capacity_factor`, and `end_date` to be wrong for every sprint when an ADO
  team had individual member days off configured.

  **Bug 1 — sprint end-date off by one day.**
  Azure DevOps `finishDate` is the *inclusive* last day of the iteration (the
  ADO UI shows it as the sprint end). The previous code subtracted one day
  (`end_incl = finishDate − 1`), causing every sprint to lose its final
  working day.  For a sprint the ADO UI shows as "May 14 – May 27", the app
  was storing `end_date = 2026-05-26` and `normal_working_days = 9` instead
  of the correct `2026-05-27` / `10`.

  **Bug 2 — individual member days off incorrectly reduced
  `planned_working_days`.**
  The `iterationcapacities` API field `teamTotalDaysOff` is the *total* of all
  off-days across all members (team-wide + individual).  The previous code
  used it as a "summary fallback" count of additional *team-wide schedule* days
  to subtract from `planned_working_days`.  For a sprint with 7 team members
  each having one individual day off, this produced `planned_working_days = 2`
  (9 − 7) and `capacity_factor = 0.125` instead of the correct
  `planned_working_days = 9` and `capacity_factor ≈ 0.903`.

  **Correct semantics now enforced:**
  - `planned_working_days` is reduced only by team-wide days off (explicit
    team days off or days where every member is absent).
  - Individual member days off reduce that person's available capacity only.
  - `capacity_factor = team_capacity_hours / baseline_capacity_hours` correctly
    accounts for both team-wide and individual days off via the per-day
    per-member loop already in place.
  - The `summary_fallback_count` mechanism has been removed.

### Added

- Three new columns in the Sprint capacity schedule import table:
  - `schedule_availability` — fraction of sprint working days not lost to
    team-wide days off (`planned_working_days / normal_working_days`).
  - `team_capacity_hours` — total person-hours available this sprint.
  - `baseline_capacity_hours` — total person-hours if everyone worked every day.
- `per_user_capacity` diagnostic field in each sprint row: a list of per-member
  objects containing `member_id`, `capacity_per_day`, `days_off_count`,
  `available_days`, and `available_capacity_hours`.
- `tests/test_ado_capacity_calculation.py` — 20 new focused tests covering the
  corrected date boundary, schedule vs. capacity factor separation,
  double-counting protection, and per-user diagnostic fields.

---

## [0.1.2] — 2026-04-07

### Added
- Windows native support: Chrome discovery on Windows fixed paths
  (`%LOCALAPPDATA%\Google\Chrome`, `C:\Program Files\Google\Chrome`);
  config directory now uses `%APPDATA%\agileforecasting` on Windows
  (automatic migration from old `~\.config\agileforecasting` path on first run).
- `requirements.lock` now includes `sys_platform == "linux"` markers on
  `jeepney` and `secretstorage` so the lockfile installs cleanly on Windows.
- New CI job `test-windows` (windows-latest, Python 3.12) running ruff and
  pytest on every push and pull request.
- 16 new unit tests in `tests/test_windows_compat.py` covering Windows path
  resolution, Chrome discovery, and cross-platform error messages — all tests
  run on all platforms via `sys.platform` monkeypatching.
- `docs/WINDOWS_SETUP.md` updated with Windows config paths, lock-file
  warning, Chrome troubleshooting, and `%APPDATA%` path for settings removal.
- `LICENSE` — MIT licence added.

### Fixed
- Calendar summary panel overlap corrected (When-calendar layout).
- `test_linux_uses_dot_config` test assertions made robust against implementation detail changes.
- Ruff import ordering and formatting fixes in test files.

---

## [0.1.1] — 2026-04-06

### Fixed
- Chart export no longer crashes with a raw traceback when Chrome/Chromium is not
  installed. `export_plotly_figure()` now raises `BrowserNotAvailableError` with
  installation guidance, and all three export buttons in the UI show a clean error
  message instead of an unhandled exception.
- Removed an accidental call to `plotly.io.get_chrome()` in `ensure_plotly_chrome()`.
  In Plotly 6.x that function downloads Chrome from Google CDN rather than locating
  an existing binary, which was unintended silent behaviour.

---

## [0.1.0] — 2026-04-06

First public release. The app was originally delivered as a zip with one-off patch
scripts; this release establishes it as a properly packaged, tested, deployable, and
security-reviewed repository.

### Added
- `pyproject.toml` — hatchling build, `[dev]` optional dependency group, pytest
  and ruff config; package installable via `pip install -e ".[dev]"`
- `tests/` — 61 unit tests covering `simulation.py`, `ado_sync.py`, and `auth.py`
  pure functions; runs via `pytest` with no external services required
- `.github/workflows/ci.yml` — GitHub Actions CI: install from lockfile,
  `pip-audit` CVE scan, `ruff format --check`, `ruff check`, `pytest`
- `.github/dependabot.yml` — weekly automated PRs for GitHub Actions SHA updates
  and pip dependency bumps
- `.pre-commit-config.yaml` — ruff format + lint hooks for local dev
- `Dockerfile` — production container; non-root user, Chromium for chart export,
  base image pinned to SHA digest for reproducible builds
- `.streamlit/config.toml` — production Streamlit server settings
  (`headless`, `fileWatcherType = "none"`, `gatherUsageStats = false`)
- `.env.example` — documents `MC_ADO_PASSPHRASE`, `BROWSER_PATH`, `MC_APP_PASSWORD`
- `CHANGELOG.md`, `SECURITY.md` with full security posture documentation
- `docs/WINDOWS_SETUP.md` — step-by-step Windows installation guide

### Security
- PAT stored in OS keyring (GNOME Keyring / macOS Keychain / Windows Credential Manager);
  AES-256 Fernet-encrypted file fallback (`pat.enc.json`, mode 0o600)
- Non-secret ADO settings encrypted with PBKDF2 (200k iterations) + Fernet
- App-level shared-password gate via `MC_APP_PASSWORD` env var; comparison via
  `hmac.compare_digest`; per-session escalating login delay (brute-force deterrent)
- ADO org/project/team inputs validated against a character allowlist before use in URLs
- Exception messages sanitized — HTTP status code only shown, never the response body
- `unsafe_allow_html=True` removed; Plotly annotation text escaped via `_esc()`
  (escapes `&`, `<`, `>`, `"`, `'`)
- GitHub Actions workflow: `permissions: contents: read`; actions pinned to SHA digests
- `pip-audit` scans `requirements.lock` for CVEs on every CI run
- `sitecustomize.py` removed from the repository (was intercepting HTTP at runtime;
  presence in `scripts/` was an accidental-activation risk via PYTHONPATH)

### Changed
- `sys.path` hack removed from `streamlit_app/app.py`; package importable via wheel
- `split_sample_counts` and `threshold_breakdown` moved to `src/agile_mc/simulation.py`
- Hardcoded organisation/project defaults removed from sidebar
- Duplicate `project_ratio` dict keys fixed in `save_encrypted` call and summary export
- Query field label updated with clear guidance on ADO query URL format and requirements
- Calendar layout fixed: whitespace explosion and tile shrinkage eliminated
- All Python files reformatted with `ruff format`

### Removed
- `sitecustomize.py` and associated `README.txt` (zip-patch delivery artefacts) removed
- One-off patch scripts (`apply_*.py`, `fix_*.py`) moved to `scripts/` for reference
