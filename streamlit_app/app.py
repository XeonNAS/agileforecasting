from __future__ import annotations

import datetime as dt
import json
import os
import re
from typing import Dict, List

import numpy as np
import pandas as pd
import streamlit as st

from agile_mc.ado_client import AdoClient, AdoRef
from agile_mc.ado_sync import (
    build_capacity_schedule,
    extract_sprint_number,
    fetch_daily_throughput_from_saved_query,
    fetch_sprints,
    weekday_indexes_from_team_settings,
)
from agile_mc.calendar_export import build_when_calendar_figure
from agile_mc.chart_export import export_plotly_figure
from agile_mc.plots import how_many_figures, when_figures
from agile_mc.secure_store import forget as secure_forget
from agile_mc.secure_store import load_encrypted, save_encrypted
from agile_mc.simulation import (
    completion_cdf_by_date,
    simulate_how_many_daily,
    simulate_when_daily,
    split_sample_counts,
    threshold_breakdown,
)


def st_plotly(fig):
    import inspect

    sig = inspect.signature(st.plotly_chart)
    if "width" in sig.parameters:
        st.plotly_chart(fig, width="stretch")
    else:
        st.plotly_chart(fig, use_container_width=True)


def df_to_wrapped_html(df: pd.DataFrame, max_rows: int = 500) -> str:
    if df is None or df.empty:
        return "<div class='mcwrap'><em>(empty)</em></div>"
    if len(df) > max_rows:
        df = df.head(max_rows)
    return f"<div class='mcwrap'>{df.to_html(index=False, escape=True)}</div>"


def render_df_expander(title: str, df: pd.DataFrame, expanded: bool = False):
    with st.expander(title, expanded=expanded):
        st.markdown(df_to_wrapped_html(df), unsafe_allow_html=True)


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


def inject_css():
    st.markdown(
        """<style>
        .block-container { padding-top: 1.1rem; padding-bottom: 1.2rem; }

        /* wrapped html tables */
        .mcwrap table { width: 100% !important; table-layout: fixed !important; border-collapse: collapse !important; }
        .mcwrap th, .mcwrap td {
            border: 1px solid rgba(49, 51, 63, 0.2);
            padding: 6px 8px;
            vertical-align: top;
            white-space: normal !important;
            word-break: break-word !important;
            overflow-wrap: anywhere !important;
        }
        .mcwrap th { font-weight: 600; }

        /* Calendar layout: 4 months across, compact tiles */
        .mcmonths {
          display: grid;
          grid-template-columns: repeat(4, minmax(200px, 1fr));
          gap: 12px;
          align-items: start;
        }
        @media (max-width: 1200px) {
          .mcmonths { grid-template-columns: repeat(3, minmax(190px, 1fr)); }
        }
        @media (max-width: 900px) {
          .mcmonths { grid-template-columns: repeat(2, minmax(180px, 1fr)); }
        }
        @media (max-width: 650px) {
          .mcmonths { grid-template-columns: 1fr; }
        }

        .mcmonth {
          border: 1px solid rgba(49,51,63,0.18);
          border-radius: 10px;
          padding: 8px 8px 10px 8px;
        }
        .mcmonth h3 {
          margin: 2px 2px 8px 2px;
          font-size: 16px;
        }

        /* Day grid inside a month */
        .mccal {
          display: grid;
          grid-template-columns: repeat(7, 1fr);
          gap: 4px;
        }
        .mccal .hdr {
          font-weight: 600;
          text-align: center;
          opacity: 0.9;
          font-size: 11px;
        }
        .mccell {
          border: 1px solid rgba(49, 51, 63, 0.20);
          border-radius: 9px;
          padding: 5px 5px;
          min-height: 46px;
        }
        .mccell .d { font-size: 11px; font-weight: 800; line-height: 1.05; }
        .mccell .p { font-size: 11px; margin-top: 3px; font-weight: 800; line-height: 1.05; }
        .mccell .s { font-size: 9px; margin-top: 3px; opacity: 0.95; line-height: 1.05; }

        .mclegend { display:flex; gap:10px; flex-wrap:wrap; margin: 8px 0 12px 0; }
        .mcchip { display:inline-flex; align-items:center; gap:8px; border:1px solid rgba(49,51,63,0.25); border-radius:999px; padding:4px 10px; font-size:12px; }
        .mcswatch { width:14px; height:14px; border-radius:3px; border:1px solid rgba(0,0,0,0.15); }
        </style>""",
        unsafe_allow_html=True,
    )


def band_color(p: float) -> str:
    if p >= 0.95:
        return "rgba(76, 175, 80, 0.35)"
    if p >= 0.85:
        return "rgba(139, 195, 74, 0.35)"
    if p >= 0.70:
        return "rgba(255, 193, 7, 0.35)"
    if p >= 0.50:
        return "rgba(244, 67, 54, 0.30)"
    return "rgba(158, 158, 158, 0.15)"


def _month_last_day(year: int, month: int) -> dt.date:
    if month == 12:
        return dt.date(year + 1, 1, 1) - dt.timedelta(days=1)
    return dt.date(year, month + 1, 1) - dt.timedelta(days=1)


def render_calendar_month_html(
    year: int,
    month: int,
    prob_by_date: Dict[dt.date, float],
    sprint_label_by_date: Dict[dt.date, str],
) -> str:
    first = dt.date(year, month, 1)
    last = _month_last_day(year, month)

    dow = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"]
    header = "".join([f"<div class='hdr'>{d}</div>" for d in dow])

    pad = first.weekday()  # Mon=0
    cells = ""
    for _ in range(pad):
        cells += "<div></div>"

    cur = first
    while cur <= last:
        p = float(prob_by_date.get(cur, 0.0))
        bg = band_color(p)
        pct = int(round(p * 100))
        sprint_lbl = sprint_label_by_date.get(cur, "")
        sprint_lbl = sprint_lbl.replace("Sprint ", "S")
        cells += f"""<div class='mccell' style='background:{bg}'>
            <div class='d'>{cur.day}</div>
            <div class='p'>{pct}%</div>
            <div class='s'>{sprint_lbl}</div>
        </div>"""
        cur += dt.timedelta(days=1)

    return f"""<div class='mcmonth'>
        <h3>{first.strftime("%B %Y")}</h3>
        <div class='mccal'>{header}{cells}</div>
    </div>"""


def render_calendar(
    completion_dates: List[dt.date],
    sprint_label_by_date: Dict[dt.date, str],
    months_to_show: int,
    start_date: dt.date,
):
    if not completion_dates:
        st.info("No completion dates were generated.")
        return

    anchor = dt.date(start_date.year, start_date.month, 1)
    months: List[tuple[int, int]] = []
    y, m = anchor.year, anchor.month
    for _ in range(months_to_show):
        months.append((y, m))
        m += 1
        if m == 13:
            y += 1
            m = 1

    first_day = dt.date(months[0][0], months[0][1], 1)
    last_day = _month_last_day(months[-1][0], months[-1][1])

    axis = [first_day + dt.timedelta(days=i) for i in range((last_day - first_day).days + 1)]
    probs = completion_cdf_by_date(completion_dates, axis)
    prob_by_date = {d: p for d, p in zip(axis, probs)}

    st.markdown(
        """<div class='mclegend'>
            <span class='mcchip'><span class='mcswatch' style='background:rgba(76,175,80,0.35)'></span>95%+</span>
            <span class='mcchip'><span class='mcswatch' style='background:rgba(139,195,74,0.35)'></span>85–95%</span>
            <span class='mcchip'><span class='mcswatch' style='background:rgba(255,193,7,0.35)'></span>70–85%</span>
            <span class='mcchip'><span class='mcswatch' style='background:rgba(244,67,54,0.30)'></span>50–70%</span>
        </div>""",
        unsafe_allow_html=True,
    )

    html = "<div class='mcmonths'>"
    for y, m in months:
        html += render_calendar_month_html(y, m, prob_by_date, sprint_label_by_date)
    html += "</div>"
    st.markdown(html, unsafe_allow_html=True)


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


st.set_page_config(page_title="Agile Monte Carlo (ADO-first)", layout="wide")
inject_css()

st.title("Agile Monte Carlo (Azure DevOps)")
st.caption("Monte Carlo forecasts based on Azure DevOps throughput and iteration capacity.")

# ---- Sidebar
with st.sidebar:
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
                    for k, v in data.items():
                        st.session_state[f"cfg_{k}"] = v
                    st.success("Loaded saved settings.")
                    st.rerun()
                except Exception as e:
                    st.error(f"Could not load saved settings: {e}")

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
                            "pat": st.session_state.get("cfg_pat", ""),
                            "query": st.session_state.get("cfg_query", ""),
                            "done_field": st.session_state.get("cfg_done_field", "AUTO"),
                            "history_days": int(st.session_state.get("cfg_history_days", 180)),
                            "seed": st.session_state.get("cfg_seed", ""),
                            "project_ratio": int(st.session_state.get("cfg_project_ratio", 80)),
                        },
                        st.session_state["cfg_passphrase"],
                    )
                    st.success("Saved settings (encrypted).")
                except Exception as e:
                    st.error(f"Could not save: {e}")

    with col3:
        if st.button("Forget saved", key="btn_forget_saved"):
            if secure_forget():
                st.success("Deleted saved settings.")
            else:
                st.info("No saved settings found.")

    st.toggle("Auto-save on refresh", value=True, key="cfg_auto_save")

    st.text_input("Org", value=st.session_state.get("cfg_org", ""), key="cfg_org")
    st.text_input("Project", value=st.session_state.get("cfg_project", ""), key="cfg_project")
    st.text_input("Team", value=st.session_state.get("cfg_team", ""), key="cfg_team")
    st.text_input("PAT", value=st.session_state.get("cfg_pat", ""), type="password", key="cfg_pat")
    st.text_input("Saved query URL or GUID", value=st.session_state.get("cfg_query", ""), key="cfg_query")
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
        value=int(st.session_state.get("cfg_history_days", 180)),
        step=30,
        key="cfg_history_days",
    )

    st.divider()
    st.header("Forecast settings")

    st.selectbox("Forecast basis", ["throughput_daily", "throughput_sprint"], index=0, key="cfg_basis")
    st.selectbox("Forecast type", ["How Many (by date)", "When (finish scope)"], index=0, key="cfg_mode")
    st.slider(
        "Project work %",
        min_value=0,
        max_value=100,
        value=int(st.session_state.get("cfg_project_ratio", 80)),
        step=5,
        key="cfg_project_ratio",
        help="Percentage of team capacity spent on backlog/project work. The remainder is treated as BAU/non-project work.",
    )
    st.number_input("Simulations", min_value=1000, max_value=200000, value=10000, step=1000, key="cfg_sims")
    st.text_input("Random seed (optional)", value=st.session_state.get("cfg_seed", ""), key="cfg_seed")

    st.date_input("Forecast start date", value=dt.date.today(), key="cfg_forecast_start")

    if st.session_state["cfg_mode"].startswith("How Many"):
        st.date_input(
            "Target date", value=st.session_state["cfg_forecast_start"] + dt.timedelta(days=14), key="cfg_target_date"
        )
    else:
        st.number_input("Items remaining", min_value=1, value=50, step=1, key="cfg_items_remaining")

    st.slider("Calendar months to show (When)", min_value=1, max_value=24, value=3, key="cfg_months")

    refresh = st.button("Refresh from Azure DevOps", type="primary", key="btn_refresh")

# ---- Validate inputs
org = st.session_state.get("cfg_org", "").strip()
project = st.session_state.get("cfg_project", "").strip()
team = st.session_state.get("cfg_team", "").strip()
pat = st.session_state.get("cfg_pat", "").strip()
query = st.session_state.get("cfg_query", "").strip()

if not (org and project and team and pat and query):
    st.info("Enter Org/Project/Team/PAT and saved query, then click **Refresh from Azure DevOps**.")
    st.stop()

ado = AdoClient(AdoRef(org, project, team), pat)

forecast_start: dt.date = st.session_state["cfg_forecast_start"]
history_days: int = int(st.session_state["cfg_history_days"])
done_field: str = st.session_state.get("cfg_done_field", "AUTO")
auto_save: bool = bool(st.session_state.get("cfg_auto_save", True))
passphrase2: str = st.session_state.get("cfg_passphrase", "")

if refresh or "ado_loaded" not in st.session_state:
    try:
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
                    "pat": pat,
                    "query": query,
                    "done_field": done_field,
                    "history_days": history_days,
                    "seed": st.session_state.get("cfg_seed", ""),
                },
                passphrase2,
            )

    except Exception as e:
        st.error(f"ADO sync failed: {e}")
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
        ex = export_plotly_figure(
            figs[chart_name], fmt=fmt, base_name=re.sub(r"[^A-Za-z0-9_-]+", "_", chart_name.lower())
        )
        st.download_button("Download", data=ex.data, file_name=ex.filename, mime=ex.mime, key="dl_real_hm")

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

    render_calendar(completion_dates, sprint_label_by_date, months_to_show=months_show, start_date=forecast_start)

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

    figs = when_figures(completion_dates)
    with st.expander("Show distribution charts", expanded=False):
        for _, fig in figs.items():
            st_plotly(fig)

        st.subheader("Download charts")
        fmt = st.selectbox("Download format", ["png", "svg"], index=0, key="dl_fmt_when")
        chart_name = st.selectbox("Chart", list(figs.keys()), index=0, key="dl_chart_when")
        if st.button("Prepare download", key="dl_btn_when"):
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
