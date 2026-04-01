# Security notes

This document summarises the security posture of the app and known risks to address before a wider or public deployment.

---

## Current protections

| Area | What's in place |
|---|---|
| ADO credentials | Stored encrypted on disk with PBKDF2-derived Fernet key; passphrase never written to disk |
| Secrets in session | PAT lives only in `st.session_state`; never logged or serialised to files |
| HTML rendering | `unsafe_allow_html=True` is used for calendar and table rendering (see below) |
| Environment variables | `.env.example` documents all env vars; `.gitignore` excludes `.env` and `secrets.toml` |

---

## Known risks — prioritised

### 1. PAT logged via `st.session_state` introspection (medium)
Streamlit's debug tools and exception tracebacks can expose `st.session_state`,
which contains the raw PAT string while the session is active. **Mitigation:** run
behind a private network or authenticated reverse proxy; avoid exposing the debug
port (8501) publicly.

### 2. `unsafe_allow_html=True` (low in current form, watch on change)
The calendar and table HTML is generated entirely from internal data (no user text
is injected into the markup). **Risk:** if sprint names or ADO field values are ever
interpolated directly into the HTML template they must be escaped first. The current
code does not do this interpolation, but future changes should be audited.

### 3. No authentication layer (high for shared deployment)
The app has no login. Anyone who can reach the URL can enter credentials and query
any ADO organisation they have a PAT for. **Mitigation:** deploy behind an
authenticating reverse proxy (nginx + OAuth2 Proxy, Azure AD App Proxy, Cloudflare
Access) or restrict network access to the team.

### 4. Encrypted settings file readable by OS user (low)
`~/.config/agile-montecarlo/ado_settings.enc.json` is created with mode `0o600`
(owner-read only). The passphrase is not stored. Risk is limited to local disk
compromise.

### 5. PAT scope not enforced by the app (informational)
The app requests whatever the PAT can access. Follow least-privilege: scope the PAT
to Work Items (Read) and Work (Read) only. See README for the exact scopes.

### 6. Log file created in `cwd` (low)
`sitecustomize.py` (now in `scripts/`, inactive) wrote `mc_teamdaysoff_patch.log`
to the working directory. It no longer runs, but if re-activated, the log contains
URL paths (not credentials). Add `*.log` to `.gitignore` (already present).

### 7. Dependency pinning (informational)
`requirements.txt` uses `>=` lower bounds. For a production deployment, generate a
pinned lockfile with `pip-compile` (pip-tools) or `uv pip compile` and test against
it in CI before deploying.

---

## Recommended pre-production actions

- [ ] Place app behind an authenticated reverse proxy or restrict network access
- [ ] Generate a pinned requirements lockfile (`pip-compile pyproject.toml`)
- [ ] Review sprint name / ADO value interpolation before any HTML template changes
- [ ] Set `STREAMLIT_SERVER_HEADLESS=true` and `STREAMLIT_SERVER_FILE_WATCHER_TYPE=none` in production (both covered by `.streamlit/config.toml`)
- [ ] Rotate the PAT used in testing before first production use

---

## Reporting vulnerabilities

Open a GitHub issue marked **[SECURITY]** or contact the maintainer directly.
