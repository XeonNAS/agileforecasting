# Changelog

All notable changes to this project will be documented here.
Format follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/).
Versioning follows [Semantic Versioning](https://semver.org/).

---

## [Unreleased]

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
