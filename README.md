# Agile Monte Carlo (ADO-first) — Streamlit App (Full Replacement)

This is a complete replacement for your Streamlit app, rebuilt to:
- Source **throughput + calendar/capacity** from **Azure DevOps** (no CSV uploads)
- Use your **saved query** to build daily throughput (missing working days => 0)
- Use **iterations + capacities + team days off** to compute sprint capacity schedule (team + individual days off)
- Remove Streamlit `use_container_width` warnings (uses runtime-safe helpers / HTML tables)
- Provide ActionableAgile-style copy for **How Many**
- Provide ActionableAgile-style **When** calendar (color bands) + "Sprint dd" per day
- Allow chart downloads as **PNG or SVG** (Plotly + Kaleido)

## Setup (Ubuntu)

```bash
sudo apt update
sudo apt install -y python3 python3-venv python3-pip unzip
```

```bash
unzip agile-montecarlo-streamlit-full-replacement.zip
cd agile-montecarlo-streamlit-full-replacement
python3 -m venv .venv
source .venv/bin/activate
pip install -U pip
pip install -r requirements.txt
streamlit run streamlit_app/app.py
```

## Azure DevOps details

In the app sidebar, enter:
- Org, Project, Team
- PAT
- Saved Query URL or GUID (your "Done by date" query)
- Passphrase (to encrypt and save these settings at rest)

Notes:
- The app stores encrypted settings in `~/.config/agile-montecarlo/ado_settings.enc.json`
- The passphrase is **not** stored. Optionally set `MC_ADO_PASSPHRASE` env var.

## PAT scopes

You need read access to:
- Work items + Queries (Boards)
- Team settings / Iterations / Capacities (Work)

If ADO endpoints fail, the app shows the HTTP status and message.

