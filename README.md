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

| Field | Description |
|---|---|
| Organisation | e.g. `myorg` |
| Project | e.g. `MyProject` |
| Team | e.g. `MyProject Team` |
| PAT | Personal Access Token (see PAT scopes below) |
| Saved Query URL or GUID | A "Done by date" query GUID or its full URL |
| Passphrase | Encrypts settings on disk — not stored anywhere |

Settings are encrypted and saved to `~/.config/agile-montecarlo/ado_settings.enc.json`.
Set `MC_ADO_PASSPHRASE` env var to avoid typing the passphrase on every start.

### PAT scopes required

- **Work Items → Read** (Boards)
- **Work → Read** (Team Settings / Iterations / Capacities)

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
