# Security notes

This document summarises the security posture of the app and known risks to address before a wider or public deployment.

---

## Current protections

| Area | What's in place |
|---|---|
| PAT storage | Saved to OS credential store (GNOME Keyring / macOS Keychain / Windows Credential Manager) via `keyring`. Falls back to a separate AES-256 Fernet-encrypted file (`pat.enc.json`, mode 0o600) when the OS keyring is unavailable. PAT is **never** stored in plaintext, in the non-secret settings file, in logs, or in exports. |
| PAT in session state | Present only during the render cycle of a sync. Cleared from `st.session_state` via a deferred-flag pattern immediately after a successful sync; retained on error so the user can retry without re-entering it. Error messages never include the PAT value. |
| Non-secret settings | Org, project, team, query stored encrypted on disk with PBKDF2-derived Fernet key; passphrase never written to disk. |
| HTML rendering | `unsafe_allow_html=True` is no longer used; tables use `st.dataframe`, calendar uses Plotly heatmap. ADO-derived text in Plotly annotations is escaped via `_esc()` in `calendar_export.py`. |
| App password gate | Optional `MC_APP_PASSWORD` env var blocks all content behind a login form; uses `hmac.compare_digest`; per-session failed-attempt counter with escalating delay deters brute force; stores only `_authenticated` boolean in session state. |
| Input validation | Org, project, and team fields are validated against an allowlist of safe characters before use in ADO API URLs. |
| Bind address | Local `streamlit run` binds to `localhost` only (`.streamlit/config.toml` leaves `address` unset); Docker overrides to `0.0.0.0` via `STREAMLIT_SERVER_ADDRESS` env var. |
| Streamlit production settings | `headless = true`, `fileWatcherType = "none"`, `gatherUsageStats = false` set in `.streamlit/config.toml`. |
| Environment variables | `.env.example` documents all env vars; `.gitignore` excludes `.env` and `secrets.toml`. |
| Dependency pinning | `requirements.lock` pins every transitive runtime dependency to an exact version via `pip-compile`. CI runs `pip-audit` against the lockfile on every push. |

---

## Known risks — prioritised

### 1. PAT in session state during the fetch (low — window minimised)
The PAT exists in `st.session_state["cfg_pat"]` only during the render cycle in
which the form is submitted and the ADO sync runs. It is cleared from session
state via a deferred-flag pattern on the following rerun. On error the PAT is
retained so the user can retry without re-entering it; error messages never
include the PAT value. **Residual risk:** a crash dump or Streamlit debug
session opened during that one render could capture the PAT.
**Mitigation:** run behind a private network or authenticated reverse proxy.

### 2. ~~`unsafe_allow_html=True`~~ (resolved)
`unsafe_allow_html=True` has been removed from the app. Tables now use
`st.dataframe` and the on-screen calendar uses the existing Plotly heatmap
(`build_when_calendar_figure`). ADO-derived text (sprint names, org/project/team)
that flows into Plotly annotation and text fields is escaped via a dedicated
`_esc()` helper in `calendar_export.py`.

### 3. Authentication layer (low for shared team use; high for public deployment)
A shared-password gate is implemented via the `MC_APP_PASSWORD` environment
variable. When set, all app content is blocked behind a login form with a
per-session failed-attempt counter and an escalating delay on failed attempts.
The comparison uses `hmac.compare_digest` to avoid timing attacks.

**Limitations of this gate:**
- It is a shared secret, not per-user authentication. Anyone with the password can use the app.
- It provides no audit trail or brute-force lockout (only a delay per session).
- Session state is per-browser tab; another tab in the same browser is a fresh session.

**Recommendation for public or regulated deployments:** place the app behind an
authenticating reverse proxy (nginx + OAuth2 Proxy, Azure AD App Proxy, Cloudflare
Access) in addition to, or instead of, this gate. Network-level access control
(VPN, private subnet) remains the strongest mitigation.

### 4. Encrypted files readable by OS user (low)
`~/.config/agileforecasting/ado_settings.enc.json` (non-secret settings) and
`~/.config/agileforecasting/pat.enc.json` (PAT fallback, when OS keyring is
unavailable) are both created with mode `0o600` (owner-read only). The
passphrase is not stored. Risk is limited to local disk compromise. When the OS
keyring is used (default on desktop installations), no PAT file is written at all.

### 5. PAT scope not enforced by the app (informational)
The app requests whatever the PAT can access. Follow least-privilege: scope the PAT
to Work Items (Read) and Work (Read) only. See README for the exact scopes.

### 6. Encryption passphrase in session state (low)
The passphrase entered by the user (or pre-loaded from `MC_ADO_PASSPHRASE`) is
stored in `st.session_state["cfg_passphrase"]` for the lifetime of the browser
session so that settings can be auto-saved on each ADO refresh. This is a wider
exposure window than the PAT (which is cleared after each sync). Risk is limited
to session-state inspection scenarios (crash dump, future Streamlit debug
vulnerability). `*.log` files are in `.gitignore`. Mitigation: run behind an
authenticated reverse proxy or on a private network.

### 7. Dependency pinning and CVE scanning (resolved)
`requirements.lock` pins every transitive runtime dependency to an exact version,
generated by `pip-compile` from `pyproject.toml`. Docker installs from
`requirements.lock`. CI installs from `requirements.lock` and then runs
`pip-audit -r requirements.lock` before tests on every push.
Regenerate with `pip-compile pyproject.toml --output-file requirements.lock`
whenever `pyproject.toml` changes.

---

## Recommended pre-production actions

- [x] Add shared-password gate (`MC_APP_PASSWORD`) with per-session failed-attempt delay
- [ ] Place app behind an authenticated reverse proxy or restrict network access (required for public deployments)
- [x] Generate a pinned requirements lockfile (`requirements.lock` via `pip-compile`)
- [x] Add `pip-audit` CVE scanning to CI
- [x] Sprint name / ADO value escaping reviewed and completed (`_esc()` in `calendar_export.py`)
- [x] Set `headless = true`, `fileWatcherType = "none"`, `gatherUsageStats = false` in `.streamlit/config.toml`
- [x] Local `streamlit run` defaults to `localhost`; Docker binds `0.0.0.0` via `STREAMLIT_SERVER_ADDRESS` env var
- [x] PAT least-privilege scopes documented in README (Work Items: Read, Work: Read)
- [ ] Rotate the PAT used in testing before first production use

---

## Reporting vulnerabilities

Please use GitHub's private Security Advisory feature so the issue can be
reviewed and a fix prepared before any public disclosure:

**https://github.com/XeonNAS/agileforecasting/security/advisories/new**

Do **not** open a public GitHub issue for unpatched security vulnerabilities —
doing so discloses the vulnerability before a fix is available.
For questions about existing mitigations or general security feedback, a public issue is fine.
