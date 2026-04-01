# scripts/

## sitecustomize.py — retired

This was a Python `sitecustomize` runtime patch that did two things:

1. **Disabled Streamlit `@st.cache_data` / `@st.cache_resource`** — no longer needed;
   `app.py` uses session-state-based loading and has no cache decorators.

2. **Intercepted ADO capacity HTTP responses** to fold team days off into per-member
   `daysOff` arrays — no longer needed; `ado_sync.build_capacity_schedule()` calls
   `fetch_team_days_off_for_sprint()` natively and merges the data itself.

Kept here for reference. Do **not** move it back to the repo root — Python's `site`
module auto-loads `sitecustomize.py` from `sys.path` on startup.

## apply_*.py / fix_*.py — retired

One-off patch scripts used to migrate an older version of the codebase. The patches
they applied are now part of the committed source. Kept for reference only.

## make_repo_snapshot.py / make_upload_zip.py

Distribution helpers for generating a shareable zip of the repo. May still be useful
but are not part of the normal dev/CI workflow.
