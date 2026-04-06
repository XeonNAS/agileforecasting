from __future__ import annotations

import datetime as dt
import hmac
import json
import logging
import os
import re
import time
from typing import Dict, List

import numpy as np
import pandas as pd
import requests
import streamlit as st

from agile_mc.ado_client import AdoClient, AdoRef
from agile_mc.ado_sync import (
    build_capacity_schedule,
    extract_sprint_number,
    fetch_daily_throughput_from_saved_query,
    fetch_sprints,
    weekday_indexes_from_team_settings,
)
from agile_mc.auth import get_app_password
from agile_mc.calendar_export import build_when_calendar_figure
from agile_mc.app_logging import LOG_LEVEL_OPTIONS, configure_logging, load_log_level, save_log_level
from agile_mc.chart_export import BrowserNotAvailableError, export_plotly_figure
from agile_mc.pat_store import forget_pat as pat_forget
from agile_mc.pat_store import keyring_available, load_pat, save_pat
from agile_mc.plots import how_many_figures, when_figures
from agile_mc.secure_store import forget as secure_forget
from agile_mc.secure_store import load_encrypted, save_encrypted
from agile_mc.simulation import (
    simulate_how_many_daily,
    simulate_when_daily,
    split_sample_counts,
    threshold_breakdown,
)


def st_plotly(fig):
    st.plotly_chart(fig, width="stretch")


def render_df_expander(title: str, df: pd.DataFrame, expanded: bool = False):
    with st.expander(title, expanded=expanded):
        if df is None or df.empty:
            st.caption("(empty)")
        else:
            st.dataframe(df, width="stretch")


def _to_date_series(series: pd.Series) -> pd.Series:
    if series is None:
        return pd.Series(dtype="object")
    if pd.api.types.is_datetime64_any_dtype(series):
        return pd.to_datetime(series, errors="coerce").dt.date
    return pd.to_datetime(series.astype(str), errors="coerce").dt.date


def filter_df_for_history_window(
    title: str,
    df: pd.DataFrame,
    history_start: dt.date,
    history_end: dt.date,
) -> pd.DataFrame:
    if df is None or df.empty:
        return df

    out = df.copy()

    if title == "Daily throughput (Done items by date)" and "date" in out.columns:
        dates = _to_date_series(out["date"])
        mask = dates.between(history_start, history_end)
        return out.loc[mask].reset_index(drop=True)

    if title in {
        "Sprints (iterations)",
        "Sprint capacity schedule",
        "Derived sprint throughput (completed sprints only)",
    }:
        if "end_date" in out.columns:
            end_dates = _to_date_series(out["end_date"])
            mask = end_dates >= history_start
            if title == "Derived sprint throughput (completed sprints only)":
                mask = mask & end_dates.between(history_start, history_end)
            return out.loc[mask].reset_index(drop=True)
        if "start_date" in out.columns:
            start_dates = _to_date_series(out["start_date"])
            mask = start_dates >= history_start
            return out.loc[mask].reset_index(drop=True)

    return out


def months_needed_to_cover_p95(completion_dates: List[dt.date], start_date: dt.date, cap: int = 24) -> int:
    """Number of months (from start_date month) needed to include the P95 completion month."""
    if not completion_dates:
        return 1
    ords = sorted([d.toordinal() for d in completion_dates])
    idx = int(round(0.95 * (len(ords) - 1)))
    idx = max(0, min(idx, len(ords) - 1))
    p95 = dt.date.fromordinal(ords[idx])

    start_m = start_date.year * 12 + start_date.month
    p95_m = p95.year * 12 + p95.month
    months = (p95_m - start_m) + 1
    months = max(1, months)
    if cap and months > cap:
        months = cap
    return months


st.set_page_config(page_title="AgileForecasting", layout="wide")

# ---- Logging — configured before anything else so startup errors are captured.
# configure_logging() reads the persisted level from disk (default FATAL).
_log_path = configure_logging()
_app_logger = logging.getLogger("agile_mc.app")

# ---- App-level password gate (optional — set MC_APP_PASSWORD to enable)
_app_password = get_app_password()
if _app_password is not None and not st.session_state.get("_authenticated"):
    st.title("AgileForecasting")
    st.caption("Sign in to continue.")
    with st.form("login"):
        _entered = st.text_input("Password", type="password")
        _submitted = st.form_submit_button("Sign in")
    if _submitted:
        # Escalating delay on repeated failures: 0s, 2s, 4s, 8s, … capped at 30s.
        # Runs server-side before the password check, so every guess costs at least
        # this much time regardless of how fast the client submits.
        _attempts = st.session_state.get("_login_attempts", 0)
        if _attempts > 0:
            time.sleep(min(2**_attempts, 30))
        if hmac.compare_digest(_entered or "", _app_password):
            st.session_state["_authenticated"] = True
            st.session_state.pop("_login_attempts", None)
            st.rerun()
        else:
            st.session_state["_login_attempts"] = _attempts + 1
            st.error("Incorrect password.")
    st.stop()

st.title("AgileForecasting")
st.markdown("by XeonNAS  ·  [github.com/XeonNAS/agileforecasting](https://github.com/XeonNAS/agileforecasting)")

# ---- Initialise config session-state keys before any widget is created.
# This prevents the "widget created with a default value but also had its
# value set via the Session State API" warning that fires when widgets use
# both key= and value=st.session_state.get(…) simultaneously.
st.session_state.setdefault("cfg_org", "")
st.session_state.setdefault("cfg_project", "")
st.session_state.setdefault("cfg_team", "")
st.session_state.setdefault("cfg_query", "")
st.session_state.setdefault("cfg_done_field", "AUTO")
st.session_state.setdefault("cfg_history_days", 180)
st.session_state.setdefault("cfg_seed", "")
st.session_state.setdefault("cfg_project_ratio", 80)
st.session_state.setdefault("cfg_log_level", load_log_level())


def _on_log_level_change() -> None:
    """Persist the new log level and reconfigure logging immediately."""
    new_level = str(st.session_state.get("cfg_log_level", "FATAL"))
    save_log_level(new_level)
    configure_logging(new_level)


# ---- Sidebar
with st.sidebar:
    if _app_password is not None:
        if st.button("Sign out", key="btn_sign_out"):
            st.session_state["_authenticated"] = False
            st.rerun()
        st.divider()

    st.header("Azure DevOps settings")

    env_pass = os.environ.get("MC_ADO_PASSPHRASE", "")
    st.text_input("Encryption passphrase (for saved settings)", value=env_pass, type="password", key="cfg_passphrase")

    col1, col2, col3 = st.columns(3)
    with col1:
        if st.button("Load saved", key="btn_load_saved"):
            if not st.session_state.get("cfg_passphrase"):
                st.warning("Enter a passphrase first.")
            else:
                try:
                    data = load_encrypted(st.session_state["cfg_passphrase"]) or {}
                    data.pop("pat", None)  # PAT is no longer saved; discard if present in older files
                    for k, v in data.items():
                        st.session_state[f"cfg_{k}"] = v
                    st.success("Loaded saved settings.")
                    st.rerun()
                except Exception:
                    st.error("Could not load saved settings. Check that your passphrase is correct.")

    with col2:
        if st.button("Save now", key="btn_save_now"):
            if not st.session_state.get("cfg_passphrase"):
                st.warning("Enter a passphrase first.")
            else:
                try:
                    save_encrypted(
                        {
                            "org": st.session_state.get("cfg_org", ""),
                            "project": st.session_state.get("cfg_project", ""),
                            "team": st.session_state.get("cfg_team", ""),
                            "query": st.session_state.get("cfg_query", ""),
                            "done_field": st.session_state.get("cfg_done_field", "AUTO"),
                            "history_days": int(st.session_state.get("cfg_history_days", 180)),
                            "seed": st.session_state.get("cfg_seed", ""),
                            "project_ratio": int(st.session_state.get("cfg_project_ratio", 80)),
                        },
                        st.session_state["cfg_passphrase"],
                    )
                    st.success("Saved settings (encrypted). PAT is stored separately via the keyring/PAT controls.")
                except Exception:
                    st.error("Could not save settings. Check your passphrase and try again.")

    with col3:
        if st.button("Forget saved", key="btn_forget_saved"):
            if secure_forget():
                st.success("Deleted saved settings.")
            else:
                st.info("No saved settings found.")

    st.toggle("Auto-save on refresh", value=True, key="cfg_auto_save")

    st.divider()
    st.subheader("Connect to Azure DevOps")

    # ---- PAT security controls --------------------------------------------------
    # Check keyring once per session to avoid a probe call on every rerun.
    if "_keyring_ok" not in st.session_state:
        st.session_state["_keyring_ok"] = keyring_available()
    _kr_ok: bool = st.session_state["_keyring_ok"]

    if _kr_ok:
        st.caption("Your PAT is stored in the OS credential store (keyring) and loaded automatically.")
    else:
        st.caption(
            "OS keyring not available on this machine. PAT can be saved encrypted to disk — enter a passphrase above."
        )

    st.toggle(
        "Save PAT to OS keyring" if _kr_ok else "Save PAT (encrypted file, needs passphrase)",
        value=_kr_ok,
        key="cfg_save_pat",
        help=(
            "When on, your PAT is saved to the OS credential store after a successful sync "
            "and loaded automatically on the next run."
            if _kr_ok
            else "When on, your PAT is saved to an encrypted file after sync. Requires the passphrase above to be set."
        ),
    )

    if st.button("Forget saved PAT", key="btn_forget_pat"):
        if pat_forget():
            st.success("Saved PAT removed from secure storage.")
            st.session_state["_clear_pat_on_next_run"] = True
            st.rerun()
        else:
            st.info("No saved PAT found.")

    # ---- Deferred PAT clear + keyring auto-load --------------------------------
    # Deferred clear: set by the post-sync block (can't write to a widget-owned
    # key after it has been instantiated in the same run).  We pop cfg_pat here
    # (before the widget) so Streamlit allows the write, then immediately try to
    # reload from the keyring so the field stays populated if a PAT was saved.
    if st.session_state.pop("_clear_pat_on_next_run", False):
        st.session_state.pop("cfg_pat", None)

    # Auto-load from keyring/fallback on first render or after a sync-triggered
    # clear.  Only runs when cfg_pat is absent from session state.
    if "cfg_pat" not in st.session_state:
        _pre_pat = load_pat(passphrase=st.session_state.get("cfg_passphrase") or None)
        if _pre_pat:
            st.session_state["cfg_pat"] = _pre_pat

    with st.form("ado_connection"):
        st.text_input("Org", key="cfg_org")
        st.text_input("Project", key="cfg_project")
        st.text_input("Team", key="cfg_team")
        st.text_input("PAT", type="password", key="cfg_pat")
        st.text_input(
            "Query",
            key="cfg_query",
            placeholder="https://dev.azure.com/{org}/{project}/_queries/query/{id}/",
            help=(
                "Enter the URL of a saved Azure DevOps query that returns the team's completed work items "
                "for the timeframe you want to analyse. "
                "The query should only include items with **State = Done** and a **State Changed Date** within that period. "
                "Make sure the query shows these columns: **ID, Work Item Type, Title, State, and State Changed Date**."
            ),
        )
        st.selectbox(
            "Done date field",
            options=[
                "AUTO",
                "Microsoft.VSTS.Common.ClosedDate",
                "Microsoft.VSTS.Common.ResolvedDate",
                "Microsoft.VSTS.Common.StateChangeDate",
                "System.ChangedDate",
            ],
            index=0,
            key="cfg_done_field",
        )
        st.number_input(
            "History days",
            min_value=30,
            max_value=730,
            step=30,
            key="cfg_history_days",
        )
        refresh = st.form_submit_button("Refresh from Azure DevOps", type="primary")

    st.divider()
    st.header("Forecast settings")

    st.selectbox("Forecast basis", ["throughput_daily", "throughput_sprint"], index=0, key="cfg_basis")
    st.selectbox("Forecast type", ["How Many (by date)", "When (finish scope)"], index=0, key="cfg_mode")
    st.slider(
        "Project work %",
        min_value=0,
        max_value=100,
        step=5,
        key="cfg_project_ratio",
        help="Percentage of team capacity spent on backlog/project work. The remainder is treated as BAU/non-project work.",
    )
    st.number_input("Simulations", min_value=1000, max_value=200000, value=10000, step=1000, key="cfg_sims")
    st.text_input("Random seed (optional)", key="cfg_seed")

    st.date_input("Forecast start date", value=dt.date.today(), key="cfg_forecast_start")

    if st.session_state["cfg_mode"].startswith("How Many"):
        st.date_input(
            "Target date", value=st.session_state["cfg_forecast_start"] + dt.timedelta(days=14), key="cfg_target_date"
        )
    else:
        st.number_input("Items remaining", min_value=1, value=50, step=1, key="cfg_items_remaining")

    st.slider("Calendar months to show (When)", min_value=1, max_value=24, value=3, key="cfg_months")

    st.divider()
    st.subheader("App settings")
    st.selectbox(
        "Log level",
        options=LOG_LEVEL_OPTIONS,
        key="cfg_log_level",
        on_change=_on_log_level_change,
        help=(
            "Controls the verbosity of the application log file. "
            "FATAL logs only critical errors; DEBUG logs everything including export details."
        ),
    )
    st.caption(f"Log file: `{_log_path}`")

# ---- Read connection inputs (PAT read here; cleared after successful fetch)
org = st.session_state.get("cfg_org", "").strip()
project = st.session_state.get("cfg_project", "").strip()
team = st.session_state.get("cfg_team", "").strip()
pat = st.session_state.get("cfg_pat", "").strip()
query = st.session_state.get("cfg_query", "").strip()

forecast_start: dt.date = st.session_state["cfg_forecast_start"]
history_days: int = int(st.session_state["cfg_history_days"])
done_field: str = st.session_state.get("cfg_done_field", "AUTO")
auto_save: bool = bool(st.session_state.get("cfg_auto_save", True))
passphrase2: str = st.session_state.get("cfg_passphrase", "")

data_already_loaded = "ado_loaded" in st.session_state

# Allowlist patterns for ADO connection fields.
# Org names: letters, digits, and hyphens (Azure DevOps organisation naming rules).
# Project / team names: letters, digits, spaces, hyphens, underscores, and dots.
_ADO_ORG_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9\-]*$")
_ADO_FIELD_RE = re.compile(r"^[A-Za-z0-9][\w .\-]*$")


def _ado_field_error(name: str, value: str, pattern: re.Pattern) -> str | None:
    """Return an error string if *value* does not match *pattern*, else None."""
    if not pattern.match(value):
        return f"{name} contains unexpected characters. Check the value and try again."
    return None


if refresh:
    if not (org and project and team and pat and query):
        st.warning("All connection fields — including PAT and a saved query URL — are required to refresh.")
        if not data_already_loaded:
            st.stop()
    elif _err := (
        _ado_field_error("Org", org, _ADO_ORG_RE)
        or _ado_field_error("Project", project, _ADO_FIELD_RE)
        or _ado_field_error("Team", team, _ADO_FIELD_RE)
    ):
        st.warning(_err)
        if not data_already_loaded:
            st.stop()
    else:
        try:
            _t_sync_start = time.perf_counter()
            ado = AdoClient(AdoRef(org, project, team), pat)
            team_settings = ado.get_team_settings()
            working_days = team_settings.get("workingDays") or ["monday", "tuesday", "wednesday", "thursday", "friday"]
            working_weekdays = weekday_indexes_from_team_settings(list(working_days))

            sprints = fetch_sprints(ado)
            sprints_df, capacity_df, per_date_ratio = build_capacity_schedule(ado, sprints, working_weekdays)

            team_days_off_all: set = set()
            if not capacity_df.empty and "team_days_off_dates" in capacity_df.columns:
                for dates_str in capacity_df["team_days_off_dates"].fillna("").tolist():
                    for part in [p.strip() for p in str(dates_str).split(",") if p.strip()]:
                        try:
                            team_days_off_all.add(dt.date.fromisoformat(part))
                        except Exception:
                            pass

            history_end = forecast_start - dt.timedelta(days=1)
            history_start = history_end - dt.timedelta(days=history_days)

            daily_df = fetch_daily_throughput_from_saved_query(
                ado=ado,
                saved_query_url_or_guid=query,
                history_start=history_start,
                history_end=history_end,
                working_weekdays=working_weekdays,
                team_days_off_all=team_days_off_all,
                done_date_field=done_field,
            )

            sprint_rows = []
            for sp in sprints:
                if sp.end_inclusive >= forecast_start:
                    continue
                mask = (
                    (daily_df["date"] >= sp.start_date)
                    & (daily_df["date"] <= sp.end_inclusive)
                    & (daily_df["is_working_day"])
                )
                done_sum = int(daily_df.loc[mask, "done_count"].sum()) if "done_count" in daily_df.columns else 0
                sprint_rows.append(
                    {
                        "iteration_id": sp.iteration_id,
                        "sprint_name": sp.name,
                        "sprint_num": extract_sprint_number(sp.name),
                        "start_date": sp.start_date,
                        "end_date": sp.end_inclusive,
                        "done_count": done_sum,
                    }
                )
            sprint_throughput_df = (
                pd.DataFrame(sprint_rows).sort_values(["start_date", "sprint_name"])
                if sprint_rows
                else pd.DataFrame(columns=["iteration_id", "sprint_name", "start_date", "end_date", "done_count"])
            )

            _app_logger.info(
                "ADO sync complete in %.1fs (org=%s project=%s team=%s history=%dd)",
                time.perf_counter() - _t_sync_start,
                org,
                project,
                team,
                history_days,
            )
            st.session_state["ado_loaded"] = True
            st.session_state["working_weekdays"] = working_weekdays
            st.session_state["sprints"] = sprints
            st.session_state["ado_sprints_df"] = sprints_df
            st.session_state["ado_capacity_df"] = capacity_df
            st.session_state["ado_daily_df"] = daily_df
            st.session_state["ado_sprint_throughput_df"] = sprint_throughput_df
            st.session_state["per_date_ratio"] = per_date_ratio
            st.session_state["ado_loaded_history_start"] = history_start
            st.session_state["ado_loaded_history_end"] = history_end

            if auto_save and passphrase2:
                save_encrypted(
                    {
                        "org": org,
                        "project": project,
                        "team": team,
                        "query": query,
                        "done_field": done_field,
                        "history_days": history_days,
                        "seed": st.session_state.get("cfg_seed", ""),
                    },
                    passphrase2,
                )

            # Save PAT to secure storage if requested.  Do this before the
            # deferred-clear flag so pat is still in scope.
            if st.session_state.get("cfg_save_pat"):
                try:
                    _backend = save_pat(pat, passphrase=passphrase2 or None)
                    _label = "OS keyring" if _backend == "keyring" else "encrypted file"
                    st.toast(f"PAT saved to {_label}.", icon="🔒")
                except RuntimeError:
                    st.warning(
                        "PAT not saved: OS keyring unavailable and no passphrase entered. "
                        "Enter a passphrase above and try again."
                    )

            # Deferred PAT clear: we cannot write to cfg_pat here because the
            # widget that owns that key was instantiated earlier in this run.
            # The flag is consumed before the form on the next rerun.
            st.session_state["_clear_pat_on_next_run"] = True

        except requests.HTTPError as e:
            _status = e.response.status_code if e.response is not None else "unknown"
            st.error(f"ADO request failed (HTTP {_status}). Check your Org, Project, Team, PAT, and Query settings.")
            if not data_already_loaded:
                st.stop()
        except Exception as e:
            st.error(f"ADO sync failed ({type(e).__name__}). Check your connection settings.")
            if not data_already_loaded:
                st.stop()

elif not data_already_loaded:
    st.info("Enter your Org, Project, Team, PAT, and a saved query URL, then click **Refresh from Azure DevOps**.")
    st.stop()

display_history_end = forecast_start - dt.timedelta(days=1)
display_history_start = display_history_end - dt.timedelta(days=history_days)

loaded_history_start = st.session_state.get("ado_loaded_history_start")
if isinstance(loaded_history_start, dt.date) and display_history_start < loaded_history_start:
    st.info(
        f"History Days is set to {history_days}, but the currently loaded throughput data starts on "
        f"{loaded_history_start.isoformat()}. Click Refresh from Azure DevOps to load the full requested window."
    )

imports_to_render = [
    ("Sprints (iterations)", st.session_state["ado_sprints_df"]),
    ("Sprint capacity schedule", st.session_state["ado_capacity_df"]),
    ("Daily throughput (Done items by date)", st.session_state["ado_daily_df"]),
    ("Derived sprint throughput (completed sprints only)", st.session_state["ado_sprint_throughput_df"]),
]

st.subheader("Azure DevOps imports")
for title, raw_df in imports_to_render:
    filtered_df = filter_df_for_history_window(
        title=title,
        df=raw_df,
        history_start=display_history_start,
        history_end=display_history_end,
    )
    render_df_expander(title, filtered_df)

st.subheader("Forecast")

daily_df = st.session_state["ado_daily_df"].copy()
working_weekdays = st.session_state["working_weekdays"]
per_date_ratio: Dict[dt.date, float] = st.session_state["per_date_ratio"]

hist_daily_counts = (
    daily_df.loc[daily_df["is_working_day"], "done_count"].to_numpy(dtype=int)
    if "done_count" in daily_df.columns
    else np.array([], dtype=int)
)
if hist_daily_counts.size > 0:
    nz = np.nonzero(hist_daily_counts)[0]
    if nz.size > 0:
        hist_daily_counts = hist_daily_counts[nz[0] :]


def is_working_day(d: dt.date) -> bool:
    return (d.weekday() in working_weekdays) and (per_date_ratio.get(d, 1.0) > 0.0)


mode = st.session_state["cfg_mode"]
basis = st.session_state["cfg_basis"]
n_sims = int(st.session_state["cfg_sims"])
seed_val = st.session_state.get("cfg_seed", "")
seed = int(seed_val) if str(seed_val).strip().isdigit() else None
project_ratio = float(int(st.session_state.get("cfg_project_ratio", 80))) / 100.0

if mode.startswith("How Many"):
    target_date: dt.date = st.session_state["cfg_target_date"]
    if target_date < forecast_start:
        st.error("Target date must be on/after forecast start date.")
        st.stop()

    forecast_dates = [
        d
        for d in (forecast_start + dt.timedelta(days=i) for i in range((target_date - forecast_start).days + 1))
        if is_working_day(d)
    ]
    samples = simulate_how_many_daily(hist_daily_counts, forecast_dates, per_date_ratio, n_sims=n_sims, seed=seed)
    project_samples, bau_samples = split_sample_counts(samples, project_ratio, seed=seed)

    n50, p50_project, p50_bau = threshold_breakdown(samples, project_samples, bau_samples, 0.50)
    n85, p85_project, p85_bau = threshold_breakdown(samples, project_samples, bau_samples, 0.85)
    n95, p95_project, p95_bau = threshold_breakdown(samples, project_samples, bau_samples, 0.95)

    timebox_label = target_date.isoformat()
    st.markdown("This is a probabilistic forecast based on historical throughput and many simulations.")
    st.markdown(
        f"By {timebox_label}, there is a 50% chance we will finish at least {n50} items "
        f"({p50_project} Project, {p50_bau} BAU)."
    )
    st.markdown(
        f"By {timebox_label}, there is an 85% chance we will finish at least {n85} items "
        f"({p85_project} Project, {p85_bau} BAU)."
    )
    st.markdown(
        f"By {timebox_label}, there is a 95% chance we will finish at least {n95} items "
        f"({p95_project} Project, {p95_bau} BAU)."
    )
    st.markdown(
        "Higher confidence means fewer items; lower confidence means more risk—choose the confidence level that fits the decision."
    )
    st.markdown(
        "The first chart below matches the forecast lines above: it shows the chance of finishing **at least** N items. The second chart shows the **exact** probability of landing on each total."
    )

    figs = how_many_figures(samples, project_samples=project_samples, bau_samples=bau_samples)
    st.divider()
    st.subheader("Charts")
    for _, fig in figs.items():
        st_plotly(fig)

    st.divider()
    st.subheader("Download charts")
    fmt = st.selectbox("Download format", ["png", "svg"], index=0, key="dl_fmt_hm")
    chart_name = st.selectbox("Chart", list(figs.keys()), index=0, key="dl_chart_hm")
    if st.button("Prepare download", key="dl_btn_hm"):
        try:
            ex = export_plotly_figure(
                figs[chart_name], fmt=fmt, base_name=re.sub(r"[^A-Za-z0-9_-]+", "_", chart_name.lower())
            )
            st.download_button("Download", data=ex.data, file_name=ex.filename, mime=ex.mime, key="dl_real_hm")
        except BrowserNotAvailableError as e:
            st.error(f"Chart export unavailable: {e}")
        except Exception:
            _app_logger.error("How Many chart export failed", exc_info=True)
            st.error(f"Chart export failed. See app log: {_log_path}")

    summary = {
        "forecast_type": "how_many",
        "timebox_label": timebox_label,
        "target_date": target_date.isoformat(),
        "forecast_start": forecast_start.isoformat(),
        "basis": basis,
        "simulations": int(n_sims),
        "project_ratio": float(project_ratio),
        "n50": int(n50),
        "n85": int(n85),
        "n95": int(n95),
        "p50_project": int(p50_project),
        "p50_bau": int(p50_bau),
        "p85_project": int(p85_project),
        "p85_bau": int(p85_bau),
        "p95_project": int(p95_project),
        "p95_bau": int(p95_bau),
    }
    st.download_button(
        "Download summary.json",
        data=json.dumps(summary, indent=2).encode("utf-8"),
        file_name="forecast_summary.json",
        mime="application/json",
    )
    st.download_button(
        "Download samples.csv",
        data=pd.DataFrame(
            {
                "items_completed": samples,
                "project_items_completed": project_samples,
                "bau_items_completed": bau_samples,
            }
        )
        .to_csv(index=False)
        .encode("utf-8"),
        file_name="forecast_samples.csv",
        mime="text/csv",
    )

else:
    items_remaining: int = int(st.session_state["cfg_items_remaining"])
    horizon_days = 180
    dates = [forecast_start + dt.timedelta(days=i) for i in range(horizon_days)]
    working_dates = [d for d in dates if is_working_day(d)]
    if not working_dates:
        st.error("No working days in the forecast window. Check team settings / days off.")
        st.stop()

    completion_dates = simulate_when_daily(
        history_counts=np.rint(hist_daily_counts.astype(float) * project_ratio).astype(int),
        forecast_dates=working_dates,
        per_date_ratio=per_date_ratio,
        items_remaining=items_remaining,
        n_sims=n_sims,
        seed=seed,
        max_days=800,
    )

    sprints = st.session_state["sprints"]
    sprint_label_by_date: Dict[dt.date, str] = {}
    for sp in sprints:
        num = extract_sprint_number(sp.name)
        label = f"Sprint {num:02d}" if isinstance(num, int) else (sp.name[:14] if sp.name else "")
        for d in (sp.start_date + dt.timedelta(days=i) for i in range((sp.end_inclusive - sp.start_date).days + 1)):
            sprint_label_by_date[d] = label

    st.markdown("This is a probabilistic forecast based on historical throughput and many simulations.")
    st.subheader("Calendar")

    months_ui = int(st.session_state["cfg_months"])
    months_p95 = months_needed_to_cover_p95(completion_dates, forecast_start, cap=24)
    months_show = max(months_ui, months_p95)

    if not completion_dates:
        st.info("No completion dates were generated.")
    else:
        cols_screen = 3
        cal_fig = build_when_calendar_figure(
            completion_dates=completion_dates,
            sprint_label_by_date=sprint_label_by_date,
            months_to_show=months_show,
            start_date=forecast_start,
            cols=cols_screen,
        )
        st_plotly(cal_fig)

    st.divider()
    st.subheader("Download calendar")
    cal_fmt = st.selectbox("Download format", ["png", "svg"], index=0, key="dl_fmt_calendar")
    if st.button("Prepare calendar download", key="dl_btn_calendar"):
        context_lines = [
            f"ADO: {org}/{project}/{team}",
            f"Start date: {forecast_start.isoformat()}",
            f"Items remaining: {items_remaining}",
            f"Forecast basis: {basis}",
            f"Simulations: {n_sims}",
            f"Generated: {dt.datetime.now().strftime('%Y-%m-%d %H:%M')}",
        ]
        if seed is not None:
            context_lines.append(f"Seed: {seed}")

        try:
            _app_logger.info(
                "Calendar export requested: fmt=%s months=%d dates=%d",
                cal_fmt,
                months_show,
                len(completion_dates),
            )
            # Uses Plotly calendar export (PNG/SVG)
            cal_fig = build_when_calendar_figure(
                completion_dates=completion_dates,
                sprint_label_by_date=sprint_label_by_date,
                months_to_show=months_show,
                start_date=forecast_start,
                cols=4,
                title="When forecast calendar",
                context_lines=context_lines,
            )
            ex = export_plotly_figure(cal_fig, fmt=cal_fmt, base_name="when_calendar")
            st.download_button(
                "Download calendar",
                data=ex.data,
                file_name=ex.filename,
                mime=ex.mime,
                key="dl_real_calendar",
            )
        except BrowserNotAvailableError as e:
            st.error(f"Chart export unavailable: {e}")
        except Exception:
            _app_logger.error("Calendar export failed", exc_info=True)
            st.error(f"Chart export failed. See app log: {_log_path}")

    figs = when_figures(completion_dates)
    with st.expander("Show distribution charts", expanded=False):
        for _, fig in figs.items():
            st_plotly(fig)

        st.subheader("Download charts")
        fmt = st.selectbox("Download format", ["png", "svg"], index=0, key="dl_fmt_when")
        chart_name = st.selectbox("Chart", list(figs.keys()), index=0, key="dl_chart_when")
        if st.button("Prepare download", key="dl_btn_when"):
            try:
                ex = export_plotly_figure(
                    figs[chart_name],
                    fmt=fmt,
                    base_name=re.sub(r"[^A-Za-z0-9_-]+", "_", chart_name.lower()),
                )
                st.download_button(
                    "Download",
                    data=ex.data,
                    file_name=ex.filename,
                    mime=ex.mime,
                    key="dl_real_when",
                )
            except BrowserNotAvailableError as e:
                st.error(f"Chart export unavailable: {e}")
            except Exception:
                _app_logger.error("When chart export failed", exc_info=True)
                st.error(f"Chart export failed. See app log: {_log_path}")

    summary = {
        "forecast_type": "when",
        "items_remaining": int(items_remaining),
        "simulations": int(n_sims),
        "basis": basis,
        "forecast_start": forecast_start.isoformat(),
    }
    st.download_button(
        "Download summary.json",
        data=json.dumps(summary, indent=2).encode("utf-8"),
        file_name="forecast_summary.json",
        mime="application/json",
    )
    st.download_button(
        "Download completion_dates.csv",
        data=pd.DataFrame({"completion_date": [d.isoformat() for d in completion_dates]})
        .to_csv(index=False)
        .encode("utf-8"),
        file_name="completion_dates.csv",
        mime="text/csv",
    )
