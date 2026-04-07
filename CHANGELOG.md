# Changelog

All notable changes to this project will be documented here.
Format follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/).
Versioning follows [Semantic Versioning](https://semver.org/).

---

## [Unreleased]

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
