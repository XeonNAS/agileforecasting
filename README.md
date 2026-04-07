# AgileForecasting

Monte Carlo forecasting for Agile teams using Azure DevOps data.

- Sources **throughput + calendar/capacity** directly from **Azure DevOps** (no CSV uploads)
- Uses a **saved query** to build daily throughput (missing working days → 0)
- Uses **iterations + capacities + team days off** to compute a sprint capacity schedule
- Provides ActionableAgile-style **How Many** and **When** forecasts
- Downloads charts as **PNG or SVG** (Plotly + Kaleido)

---

## Windows setup

See **[docs/WINDOWS_SETUP.md](docs/WINDOWS_SETUP.md)** for the full
step-by-step Windows guide, including PowerShell commands, first-run
configuration, and troubleshooting.

Quick summary:

```powershell
git clone https://github.com/XeonNAS/agileforecasting.git
cd agileforecasting
python -m venv .venv
.venv\Scripts\Activate.ps1      # allow scripts first if needed: Set-ExecutionPolicy RemoteSigned -Scope CurrentUser
python -m pip install --upgrade pip
pip install -r requirements.txt
streamlit run streamlit_app\app.py
```

Open <http://localhost:8501> in your browser.

---

## Linux (Ubuntu) setup

```bash
sudo apt update
sudo apt install -y python3.12 python3.12-venv python3-pip
```

```bash
git clone https://github.com/XeonNAS/agileforecasting.git
cd agileforecasting
python3.12 -m venv .venv
source .venv/bin/activate
pip install --upgrade pip
pip install -r requirements.txt   # installs deps AND the agile_mc package from src/
```

---

## Run locally

```bash
# Linux / macOS
source .venv/bin/activate
streamlit run streamlit_app/app.py
```

```powershell
# Windows PowerShell
.venv\Scripts\Activate.ps1
streamlit run streamlit_app\app.py
```

Open <http://localhost:8501> in your browser.

---

## Sidebar — settings overview

### Settings management (top of sidebar)

| Field / button | Description |
|---|---|
| Encryption passphrase | Encrypts/decrypts non-secret settings on disk. Never stored itself. |
| Load saved | Load org/project/team/query settings from the encrypted file. |
| Save now | Save non-secret settings to the encrypted file immediately. |
| Forget saved | Delete the encrypted settings file. |
| Auto-save on refresh | Automatically saves settings after each successful ADO sync. |

### PAT storage (Connect to Azure DevOps section)

| Control | Description |
|---|---|
| Save PAT toggle | When on, the PAT is saved securely after a successful sync. |
| Forget saved PAT | Removes the PAT from all secure storage backends. |

### Connect to Azure DevOps (form)

| Field | Description |
|---|---|
| Org | e.g. `myorg` |
| Project | e.g. `MyProject` |
| Team | e.g. `MyProject Team` |
| PAT | Personal Access Token. Auto-populated from secure storage if saved. |
| Saved Query URL or GUID | A "Done by date" query GUID or its full URL. |

---

## PAT handling

### How it works

AgileForecasting stores your Azure DevOps PAT in the **OS credential store**
via the [`keyring`](https://pypi.org/project/keyring/) library:

| Platform | Backend |
|---|---|
| Linux (desktop) | GNOME Keyring / KWallet via SecretService |
| macOS | macOS Keychain |
| Windows | Windows Credential Manager |

**Workflow:**

1. Enter your PAT in the sidebar form.
2. Enable **Save PAT to OS keyring** (on by default when keyring is available).
3. Click **Refresh from Azure DevOps**.
4. On success the PAT is saved to the credential store and cleared from the
   browser session.
5. On the next run the PAT is loaded automatically — just click Refresh.

### Removing a saved PAT

Click **Forget saved PAT** in the sidebar, or run from the terminal:

```python
python -c "from agile_mc.pat_store import forget_pat; forget_pat(); print('done')"
```

### Headless / no-keyring fallback

On servers without a running keyring daemon, the toggle label changes to
**Save PAT (encrypted file, needs passphrase)**. Enter your encryption
passphrase in the field above; the PAT is then saved to a separate
AES-256 Fernet-encrypted file.

File locations by platform:

| Platform | Path |
|---|---|
| Linux / macOS | `~/.config/agileforecasting/pat.enc.json` |
| Windows | `%APPDATA%\agileforecasting\pat.enc.json` |

Non-secret settings are stored alongside:

| Platform | Path |
|---|---|
| Linux / macOS | `~/.config/agileforecasting/ado_settings.enc.json` |
| Windows | `%APPDATA%\agileforecasting\ado_settings.enc.json` |

The passphrase is never written to disk. Set `MC_ADO_PASSPHRASE` to avoid
typing it on every start.

### What is never stored

- The PAT in plaintext — anywhere (no files, no logs, no exports, no session
  state after a sync).
- The encryption passphrase.
- Any PAT in the non-secret settings file (`ado_settings.enc.json`).

### PAT scopes required

Create the PAT with only these scopes:

| Scope | Permission | Why |
|---|---|---|
| Work Items | Read | Fetch items from saved query |
| Work | Read | Team settings, iterations, capacities, days off |

---

## Encrypted settings file

Non-secret settings (org, project, team, query, history window) are encrypted
with PBKDF2-derived Fernet (AES-256-CBC) and saved to:

| Platform | Path |
|---|---|
| Linux / macOS | `~/.config/agileforecasting/ado_settings.enc.json` |
| Windows | `%APPDATA%\agileforecasting\ado_settings.enc.json` |

Set `MC_ADO_PASSPHRASE` to pre-fill the passphrase and enable auto-save:

```bash
# Linux / macOS
export MC_ADO_PASSPHRASE=your-passphrase
streamlit run streamlit_app/app.py
```

```powershell
# Windows PowerShell (current session only)
$env:MC_ADO_PASSPHRASE = "your-passphrase"
streamlit run streamlit_app\app.py
```

To set it permanently on Windows so it persists across sessions:

```powershell
[System.Environment]::SetEnvironmentVariable("MC_ADO_PASSPHRASE", "your-passphrase", "User")
```

> **Migration note:** if you used an earlier version of this app, your
> settings were stored at `~/.config/agile-montecarlo/ado_settings.enc.json`.
> The app migrates them automatically on first run. You can delete the old
> directory once the new one is confirmed working.

---

## App-level password gate

For shared team deployments, set `MC_APP_PASSWORD`:

```bash
# Linux / macOS
export MC_APP_PASSWORD=your-shared-password
streamlit run streamlit_app/app.py
```

```powershell
# Windows PowerShell
$env:MC_APP_PASSWORD = "your-shared-password"
streamlit run streamlit_app\app.py
```

When set, the app shows a password prompt before any content. A **Sign out**
button appears in the sidebar. When unset, no login screen is shown (default
for local dev).

> **Important:** this is a convenience gate for internal use, not a substitute
> for a proper authenticating reverse proxy (nginx + OAuth2 Proxy, Cloudflare
> Access, Azure AD App Proxy) for public-facing deployments.

---

## Deployment

### Bind address

| Context | Bind address | How |
|---|---|---|
| `streamlit run` (local) | `127.0.0.1` (loopback) | Default — `.streamlit/config.toml` leaves `address` unset |
| Docker container | `0.0.0.0` | `STREAMLIT_SERVER_ADDRESS=0.0.0.0` in `Dockerfile` |

### Docker

```bash
docker build -t agileforecasting .
docker run -p 8501:8501 agileforecasting
```

To pass environment variables:

```bash
docker run -p 8501:8501 \
  -e MC_APP_PASSWORD=your-shared-password \
  -e MC_ADO_PASSPHRASE=your-passphrase \
  agileforecasting
```

> **Note:** the OS keyring is not available inside a Docker container. Use the
> encrypted-file fallback (set `MC_ADO_PASSPHRASE`) or enter the PAT manually
> each session.

### File watcher

`fileWatcherType = "none"` is set in `.streamlit/config.toml`. For local dev
with hot reload:

```bash
streamlit run streamlit_app/app.py --server.fileWatcherType auto
```

### Reverse proxy

See [SECURITY.md](SECURITY.md) for the full risk assessment and reverse-proxy
recommendations.

---

## Development setup

```bash
# Linux / macOS
source .venv/bin/activate
pip install -e ".[dev]"
```

```powershell
# Windows
.venv\Scripts\Activate.ps1
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

`requirements.lock` pins every transitive runtime dependency for Linux.
Docker and the Ubuntu CI job install from it.

> **Windows note:** `requirements.lock` is generated on Linux. Windows users
> should install from `requirements.txt` instead (the Windows CI job does this
> automatically). Do not run `pip install -r requirements.lock` on Windows.

To regenerate after changing `pyproject.toml` (run on Linux):

```bash
source .venv/bin/activate
pip install -e ".[dev]"
pip-compile pyproject.toml \
  --output-file requirements.lock \
  --no-emit-index-url \
  --strip-extras \
  --annotation-style line
```

Commit `requirements.lock` alongside any `pyproject.toml` changes.

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
│   ├── secure_store.py    # Encrypted settings storage (non-secrets)
│   └── pat_store.py       # Secure PAT storage (keyring + encrypted fallback)
├── streamlit_app/
│   └── app.py             # Streamlit UI entrypoint
├── tests/                 # Unit tests for src/agile_mc
├── pyproject.toml         # Package metadata, tool config
└── requirements.txt       # Pinned runtime dependencies
```


---

## License

[MIT](LICENSE) — Copyright (c) 2026 John Fraser
