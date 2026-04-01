# Agile Monte Carlo — Streamlit App

Monte Carlo forecasting for Agile teams using Azure DevOps data.

- Sources **throughput + calendar/capacity** directly from **Azure DevOps** (no CSV uploads)
- Uses a **saved query** to build daily throughput (missing working days → 0)
- Uses **iterations + capacities + team days off** to compute a sprint capacity schedule
- Provides ActionableAgile-style **How Many** and **When** forecasts
- Downloads charts as **PNG or SVG** (Plotly + Kaleido)

---

## Ubuntu setup

```bash
sudo apt update
sudo apt install -y python3.12 python3.12-venv python3-pip
```

```bash
git clone <repo-url>
cd agileforecasting
python3.12 -m venv .venv
source .venv/bin/activate
pip install --upgrade pip
pip install -r requirements.txt
```

---

## Run locally

```bash
source .venv/bin/activate
streamlit run streamlit_app/app.py
```

Open <http://localhost:8501> in your browser.

In the sidebar enter your Azure DevOps details:

**Settings management** (top of sidebar)

| Field | Description |
|---|---|
| Encryption passphrase | Encrypts/decrypts non-secret settings saved to disk. Not stored. |
| Load saved / Save now / Forget | Manage the encrypted settings file. |

**Connect to Azure DevOps** (form — submitted with the Refresh button)

| Field | Description |
|---|---|
| Org | e.g. `myorg` |
| Project | e.g. `MyProject` |
| Team | e.g. `MyProject Team` |
| PAT | Personal Access Token. **Never saved to disk.** Cleared from memory after each refresh. |
| Saved Query URL or GUID | A "Done by date" query GUID or its full URL |

> **PAT handling:** the PAT is entered in a form and discarded from session state
> immediately after a successful Azure DevOps sync. It is never written to the
> encrypted settings file. Re-enter it only when you need to refresh data.

Non-secret settings (org, project, team, query, history window) are encrypted and
saved to `~/.config/agile-montecarlo/ado_settings.enc.json`.
Set `MC_ADO_PASSPHRASE` env var to avoid typing the passphrase on every start.

### PAT scopes required

- **Work Items → Read** (Boards)
- **Work → Read** (Team Settings / Iterations / Capacities)

---

## App-level password gate

For shared team deployments where you want a lightweight login screen, set the
`MC_APP_PASSWORD` environment variable:

```bash
export MC_APP_PASSWORD=your-shared-password
streamlit run streamlit_app/app.py
```

When set, the app shows a password prompt before any content. A **Sign out**
button appears in the sidebar. When unset, no login screen is shown (default
for local dev).

> **Important:** this is a convenience gate for internal use. It is **not** a
> substitute for a proper authenticating reverse proxy (nginx + OAuth2 Proxy,
> Cloudflare Access, Azure AD App Proxy) in any public-facing deployment. For
> production, combine both.

---

## Deployment

### Bind address

| Context | Bind address | How |
|---|---|---|
| `streamlit run` (local) | `127.0.0.1` (loopback) | Default — `.streamlit/config.toml` leaves `address` unset |
| Docker container | `0.0.0.0` | `STREAMLIT_SERVER_ADDRESS=0.0.0.0` in `Dockerfile` |

Local runs are only reachable from the same machine. If you need to expose the
app on a local network (e.g. a VM), pass `--server.address 0.0.0.0` explicitly:

```bash
streamlit run streamlit_app/app.py --server.address 0.0.0.0
```

### Docker

```bash
docker build -t agile-mc .
docker run -p 8501:8501 agile-mc
```

Open <http://localhost:8501>.

To pass environment variables into the container:

```bash
docker run -p 8501:8501 \
  -e MC_APP_PASSWORD=your-shared-password \
  -e MC_ADO_PASSPHRASE=your-passphrase \
  agile-mc
```

### File watcher

`fileWatcherType = "none"` is set in `.streamlit/config.toml`. This disables
auto-reload on file changes, which is appropriate for production and has no
downside for a containerised deployment. For local development with hot reload:

```bash
streamlit run streamlit_app/app.py --server.fileWatcherType auto
```

### Reverse proxy (recommended for shared / public deployments)

The app itself has no TLS, rate limiting, or per-user authentication. For any
deployment reachable by more than one person, place it behind a reverse proxy:

- **nginx + OAuth2 Proxy** — open-source, self-hosted
- **Cloudflare Access** — zero-config zero-trust, free tier available
- **Azure AD App Proxy** — native if the team is already on Microsoft 365

The `MC_APP_PASSWORD` shared-password gate is a lightweight complement, not a
replacement. See [SECURITY.md](SECURITY.md) for the full risk assessment.

### PAT least-privilege scopes

Create the Azure DevOps PAT with only these scopes — no broader access is needed:

| Scope | Permission | Why |
|---|---|---|
| Work Items | Read | Fetch items from saved query |
| Work | Read | Team settings, iterations, capacities, days off |

---

## Development setup

Install the package in editable mode with dev dependencies (pytest, ruff):

```bash
source .venv/bin/activate
pip install -e ".[dev]"
```

### Run tests

```bash
pytest
```

### Lint

```bash
ruff check src/ tests/
```

### Dependency lockfile

`requirements.lock` pins every transitive runtime dependency to an exact version.
Docker and CI both install from it. To regenerate after changing `pyproject.toml`:

```bash
source .venv/bin/activate
pip install -e ".[dev]"      # ensures pip-tools is available
pip-compile pyproject.toml \
  --output-file requirements.lock \
  --no-emit-index-url \
  --strip-extras \
  --annotation-style line
```

Commit the updated `requirements.lock` alongside any `pyproject.toml` changes.

---

## Project structure

```
agileforecasting/
├── src/agile_mc/          # Reusable domain logic (no Streamlit imports)
│   ├── ado_client.py      # Azure DevOps REST client
│   ├── ado_sync.py        # Throughput & capacity data fetching
│   ├── simulation.py      # Monte Carlo simulation engine
│   ├── plots.py           # Plotly chart builders
│   ├── calendar_export.py # When-calendar Plotly figure
│   ├── chart_export.py    # PNG/SVG export via Kaleido
│   └── secure_store.py    # Encrypted settings storage
├── streamlit_app/
│   └── app.py             # Streamlit UI entrypoint
├── tests/                 # Unit tests for src/agile_mc
├── pyproject.toml         # Package metadata, tool config
└── requirements.txt       # Pinned runtime dependencies
```
