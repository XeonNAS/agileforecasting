# Changelog

All notable changes to this project will be documented here.
Format follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/).
Versioning follows [Semantic Versioning](https://semver.org/).

---

## [Unreleased]

---

## [0.1.0] — 2026-04-02

First production-ready release. The app was originally delivered as a zip with
one-off patch scripts; this release establishes it as a properly packaged,
tested, and deployable repository.

### Added
- `pyproject.toml` — hatchling build, `[dev]` optional dependency group, pytest
  and ruff config; package installable via `pip install -e ".[dev]"`
- `tests/` — 56 unit tests covering `simulation.py` and `ado_sync.py` pure
  functions; runs via `pytest` with no external services required
- `.github/workflows/ci.yml` — GitHub Actions CI: install, `ruff format --check`,
  `ruff check`, `pytest`
- `.pre-commit-config.yaml` — ruff format + lint hooks for local dev
- `Dockerfile` — production container with Chromium for chart export
- `.streamlit/config.toml` — production Streamlit server settings
- `.env.example` — documents `MC_ADO_PASSPHRASE`, `BROWSER_PATH`,
  `MC_DEBUG_TEAMDAYSOFF_PATCH`
- `src/agile_mc/__version__` (`0.1.0`) exposed as `agile_mc.__version__`
- `CHANGELOG.md`, `SECURITY.md`

### Changed
- `sys.path` hack removed from `streamlit_app/app.py`; package is now importable
  via the installed wheel
- `split_sample_counts` and `threshold_breakdown` moved from `app.py` to
  `src/agile_mc/simulation.py` (now tested and importable without Streamlit)
- Hardcoded organisation/project defaults removed from sidebar
- Duplicate `project_ratio` dict keys fixed in `save_encrypted` call and
  `summary` export dict
- All Python files reformatted with `ruff format`

### Removed / moved
- `sitecustomize.py` moved to `scripts/` — superseded by `ado_sync.py` native
  team-days-off handling; no longer risks Python auto-loading it from repo root
- One-off patch scripts (`apply_*.py`, `fix_*.py`, `make_*.py`) moved to `scripts/`
