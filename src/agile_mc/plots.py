from __future__ import annotations

import datetime as dt
from typing import Dict, List

import numpy as np
import pandas as pd
import plotly.graph_objects as go

from .flow_metrics import BEFORE_CLAUDE, CYCLE_TIME_PERCENTILE, SINCE_CLAUDE, claude_period
from .simulation import at_least_threshold, completion_cdf_by_date

SPRINT_MOVING_AVERAGE_WINDOW = 3
CLAUDE_MARKER_COLOR = "#7B1FA2"


def _mark_claude_start(fig: go.Figure, x) -> None:
    fig.add_shape(
        type="line", x0=x, x1=x, xref="x", y0=0, y1=1, yref="paper", line=dict(color=CLAUDE_MARKER_COLOR, dash="dot")
    )
    fig.add_annotation(
        x=x,
        y=1,
        xref="x",
        yref="paper",
        text="Claude start",
        showarrow=False,
        yanchor="bottom",
        font=dict(color=CLAUDE_MARKER_COLOR),
    )


def _period_label(period: str) -> str:
    return period[0].lower() + period[1:]


def sprint_moving_average(done_counts: pd.Series, window: int = SPRINT_MOVING_AVERAGE_WINDOW) -> pd.Series:
    """Trailing mean over `window` sprints; NaN until a full window is available."""
    return done_counts.astype(float).rolling(window=window, min_periods=window).mean()


def sprint_trendline(done_counts: pd.Series) -> pd.Series:
    """Least-squares linear fit of done count against sprint position; NaN when fewer than two sprints."""
    if len(done_counts) < 2:
        return pd.Series(np.nan, index=done_counts.index)
    positions = np.arange(len(done_counts))
    slope, intercept = np.polyfit(positions, done_counts.astype(float), 1)
    return pd.Series(slope * positions + intercept, index=done_counts.index)


def sprint_throughput_figure(
    sprint_throughput_df: pd.DataFrame,
    window: int = SPRINT_MOVING_AVERAGE_WINDOW,
    claude_start: dt.date | None = None,
) -> go.Figure:
    df = sprint_throughput_df.sort_values("start_date").reset_index(drop=True)
    moving_avg = sprint_moving_average(df["done_count"], window)

    fig = go.Figure()
    fig.add_bar(
        x=df["sprint_name"],
        y=df["done_count"],
        name="Done items",
        hovertemplate="%{x}<br>Done: %{y}<extra></extra>",
    )
    fig.add_scatter(
        x=df["sprint_name"],
        y=moving_avg,
        mode="lines+markers",
        name=f"{window}-sprint moving average",
        hovertemplate=f"%{{x}}<br>{window}-sprint average: %{{y:.1f}}<extra></extra>",
    )
    fig.add_scatter(
        x=df["sprint_name"],
        y=sprint_trendline(df["done_count"]),
        mode="lines",
        name="Trend",
        line=dict(dash="dash"),
        hovertemplate="%{x}<br>Trend: %{y:.1f}<extra></extra>",
    )
    if claude_start is not None:
        periods = claude_period(df["start_date"], claude_start)
        for period in (BEFORE_CLAUDE, SINCE_CLAUDE):
            in_period = df[periods == period]
            if in_period.empty:
                continue
            median = float(in_period["done_count"].median())
            fig.add_scatter(
                x=[in_period["sprint_name"].iloc[0], in_period["sprint_name"].iloc[-1]],
                y=[median, median],
                mode="lines",
                name=f"Median {_period_label(period)}",
                line=dict(color=CLAUDE_MARKER_COLOR if period == SINCE_CLAUDE else "#757575", width=3),
                hovertemplate=f"Median {_period_label(period)}: {median:.1f}<extra></extra>",
            )
        since_positions = np.flatnonzero((periods == SINCE_CLAUDE).to_numpy())
        if since_positions.size:
            # Category axis: sprint i sits at x=i, so the boundary falls half a step before the first "since" sprint
            _mark_claude_start(fig, float(since_positions[0]) - 0.5)
    fig.update_layout(
        title="Sprint throughput",
        xaxis_title="Sprint",
        yaxis_title="Done items",
        xaxis=dict(type="category", automargin=True),
        yaxis=dict(rangemode="tozero", automargin=True),
        margin=dict(l=80, r=120, t=85, b=85),
        legend=dict(yanchor="top", y=0.98, xanchor="left", x=1.02),
    )
    return fig


def cycle_time_figure(done_items_df: pd.DataFrame, claude_start: dt.date | None = None) -> go.Figure:
    timed = done_items_df.dropna(subset=["cycle_time_days"]).sort_values("done_date")
    cycle = timed["cycle_time_days"].astype(float)

    fig = go.Figure()
    fig.add_scatter(
        x=timed["done_date"],
        y=cycle,
        mode="markers",
        name="Work items",
        customdata=timed["work_item_id"],
        hovertemplate="#%{customdata}<br>Done %{x}<br>Cycle time: %{y} days<extra></extra>",
    )

    if claude_start is None:
        groups = [("", timed.index)]
    else:
        periods = claude_period(timed["done_date"], claude_start)
        groups = [(f" {_period_label(p)}", timed.index[periods == p]) for p in (BEFORE_CLAUDE, SINCE_CLAUDE)]
    for suffix, idx in groups:
        if len(idx) == 0:
            continue
        level = float(np.percentile(cycle.loc[idx], CYCLE_TIME_PERCENTILE))
        name = f"{CYCLE_TIME_PERCENTILE}th percentile{suffix}"
        fig.add_scatter(
            x=[timed.loc[idx, "done_date"].iloc[0], timed.loc[idx, "done_date"].iloc[-1]],
            y=[level, level],
            mode="lines",
            name=name,
            line=dict(dash="dash", color=CLAUDE_MARKER_COLOR if suffix == f" {_period_label(SINCE_CLAUDE)}" else None),
            hovertemplate=f"{name}: {level:.1f} days<extra></extra>",
        )
    if claude_start is not None:
        _mark_claude_start(fig, claude_start)

    fig.update_layout(
        title="Cycle time (activated → done)",
        xaxis_title="Done date",
        yaxis_title="Cycle time (days)",
        xaxis=dict(automargin=True),
        yaxis=dict(rangemode="tozero", automargin=True),
        margin=dict(l=80, r=120, t=85, b=85),
        legend=dict(yanchor="top", y=0.98, xanchor="left", x=1.02),
    )
    return fig


def how_many_figures(
    samples: np.ndarray,
    project_samples: np.ndarray | None = None,
    bau_samples: np.ndarray | None = None,
) -> Dict[str, go.Figure]:
    samples = samples.astype(int)
    if samples.size == 0:
        samples = np.zeros(1, dtype=int)

    vals, counts = np.unique(samples, return_counts=True)
    probs = counts / counts.sum()

    x_min, x_max = int(vals.min()), int(vals.max())
    xs = np.arange(x_min, x_max + 1)

    exceed = np.array([float(np.mean(samples >= x)) for x in xs], dtype=float)
    n50 = at_least_threshold(samples, 0.50)
    n85 = at_least_threshold(samples, 0.85)
    n95 = at_least_threshold(samples, 0.95)

    fig_at_least = go.Figure()
    fig_at_least.add_scatter(
        x=xs,
        y=exceed,
        mode="lines",
        name="P(items ≥ x)",
    )

    threshold_specs = [("50%", 0.50, n50), ("85%", 0.85, n85), ("95%", 0.95, n95)]
    for label, p, n in threshold_specs:
        actual = float(np.mean(samples >= n))
        fig_at_least.add_hline(y=p, line_dash="dot", opacity=0.45)
        fig_at_least.add_vline(x=n, line_dash="dash", opacity=0.55)
        fig_at_least.add_scatter(
            x=[n],
            y=[actual],
            mode="markers",
            marker=dict(size=7),
            name=label,
            showlegend=False,
            cliponaxis=False,
            hovertemplate=f"{label}: at least %{{x}} items<br>Chance: %{{y:.1%}}<extra></extra>",
        )
        text_y = min(1.035, actual + 0.035)
        fig_at_least.add_annotation(
            x=n,
            y=text_y,
            xref="x",
            yref="y",
            text=f"{label}: ≥{n}",
            showarrow=False,
            xanchor="left",
            yanchor="bottom",
            align="left",
            bgcolor="rgba(255,255,255,0.90)",
            bordercolor="rgba(0,0,0,0.20)",
            borderpad=2,
        )

    fig_at_least.update_layout(
        title="Chance of finishing at least N items",
        xaxis_title="Items completed",
        yaxis_title="P(items ≥ x)",
        yaxis=dict(range=[0, 1.06], tickformat=".0%", automargin=True),
        xaxis=dict(automargin=True),
        margin=dict(l=80, r=120, t=85, b=85),
        legend=dict(yanchor="top", y=0.98, xanchor="left", x=1.02),
    )

    fig_pmf = go.Figure()
    if (
        project_samples is not None
        and bau_samples is not None
        and len(project_samples) == len(samples)
        and len(bau_samples) == len(samples)
    ):
        project_samples = project_samples.astype(int)
        bau_samples = bau_samples.astype(int)

        project_probs = []
        bau_probs = []
        for v, total_prob in zip(vals, probs):
            mask = samples == v
            if not np.any(mask):
                avg_project_ratio = 0.0
            else:
                totals_here = samples[mask].astype(float)
                with np.errstate(divide="ignore", invalid="ignore"):
                    ratios = np.where(totals_here > 0, project_samples[mask] / totals_here, 0.0)
                avg_project_ratio = float(np.nanmean(ratios)) if ratios.size else 0.0
                avg_project_ratio = min(max(avg_project_ratio, 0.0), 1.0)

            project_probs.append(total_prob * avg_project_ratio)
            bau_probs.append(total_prob * (1.0 - avg_project_ratio))

        fig_pmf.add_bar(x=vals, y=project_probs, name="Project", marker_color="#1f77b4")
        fig_pmf.add_bar(x=vals, y=bau_probs, name="BAU", marker_color="#ff7f0e")
        fig_pmf.update_layout(barmode="stack")
    else:
        fig_pmf.add_bar(x=vals, y=probs, name="Exact probability")

    pmf = np.zeros_like(xs, dtype=float)
    idx_map = {v: i for i, v in enumerate(xs)}
    for v, p in zip(vals, probs):
        pmf[idx_map[int(v)]] = float(p)
    sigma = max(1.0, (x_max - x_min) / 30.0)
    kx = np.arange(-15, 16)
    kernel = np.exp(-(kx**2) / (2 * sigma**2))
    kernel /= kernel.sum()
    smooth = np.convolve(pmf, kernel, mode="same")

    fig_pmf.add_scatter(x=xs, y=smooth, mode="lines", name="Smoothed exact probability")
    fig_pmf.update_layout(
        title="Exact probability of finishing with N items",
        xaxis_title="Items completed",
        yaxis_title="P(items = x)",
        yaxis=dict(tickformat=".0%", automargin=True),
        xaxis=dict(automargin=True),
        bargap=0.05,
        margin=dict(l=80, r=120, t=85, b=85),
        legend=dict(yanchor="top", y=0.98, xanchor="left", x=1.02),
    )

    return {
        "How Many - At Least Chance": fig_at_least,
        "How Many - Exact Probability": fig_pmf,
    }


def when_figures(completion_dates: List[dt.date]) -> Dict[str, go.Figure]:
    if not completion_dates:
        completion_dates = [dt.date.today()]

    ords = np.array([d.toordinal() for d in completion_dates], dtype=int)
    min_o, max_o = int(ords.min()), int(ords.max())
    xs = np.arange(min_o, max_o + 1)
    bins = max(10, min(80, int((max_o - min_o + 1))))
    fig_hist = go.Figure()
    fig_hist.add_histogram(x=ords, nbinsx=bins, name="Completion dates")
    fig_hist.update_layout(
        title="When (distribution)",
        xaxis_title="Date (ordinal)",
        yaxis_title="Count",
        xaxis=dict(automargin=True),
        yaxis=dict(automargin=True),
        margin=dict(l=80, r=120, t=85, b=85),
        legend=dict(yanchor="top", y=0.98, xanchor="left", x=1.02),
    )

    date_axis = [dt.date.fromordinal(int(o)) for o in xs]
    cdf = completion_cdf_by_date(completion_dates, date_axis)
    fig_cdf = go.Figure()
    fig_cdf.add_scatter(x=date_axis, y=cdf, mode="lines", name="P(finish by date)")
    fig_cdf.update_layout(
        title="Probability of finishing by date",
        xaxis_title="Date",
        yaxis_title="Probability",
        yaxis=dict(range=[0, 1], tickformat=".0%", automargin=True),
        xaxis=dict(automargin=True),
        margin=dict(l=80, r=120, t=85, b=85),
        legend=dict(yanchor="top", y=0.98, xanchor="left", x=1.02),
    )

    return {"When - Distribution": fig_hist, "When - CDF": fig_cdf}
