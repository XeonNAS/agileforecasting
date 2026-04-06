from __future__ import annotations

import calendar as pycal
import datetime as dt
import math
from typing import Dict, List, Optional, Tuple

import numpy as np
import plotly.graph_objects as go
from plotly.subplots import make_subplots

from .simulation import completion_cdf_by_date


def _esc(text: str) -> str:
    """Escape HTML special characters for Plotly annotation/text fields."""
    return str(text).replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


def _month_last_day(year: int, month: int) -> dt.date:
    if month == 12:
        return dt.date(year + 1, 1, 1) - dt.timedelta(days=1)
    return dt.date(year, month + 1, 1) - dt.timedelta(days=1)


def _band_bucket(p: float) -> int:
    if p >= 0.95:
        return 4
    if p >= 0.85:
        return 3
    if p >= 0.70:
        return 2
    if p >= 0.50:
        return 1
    return 0


def _axis_name(prefix: str, idx: int) -> str:
    # idx=1 => xaxis/yaxis, idx>1 => xaxis2/yaxis2
    return f"{prefix}axis" if idx == 1 else f"{prefix}axis{idx}"


def _get_domain(fig: go.Figure, axis: str) -> Tuple[float, float]:
    obj = getattr(fig.layout, axis)
    dom = getattr(obj, "domain", None)
    if not dom:
        return (0.0, 1.0)
    return (float(dom[0]), float(dom[1]))


def build_when_calendar_figure(
    completion_dates: List[dt.date],
    sprint_label_by_date: Dict[dt.date, str],
    months_to_show: int,
    start_date: dt.date,
    cols: int = 4,
    *,
    title: str = "When forecast calendar",
    context_lines: Optional[List[str]] = None,
) -> go.Figure:
    """Plotly calendar for the When forecast (screen and PNG/SVG export).

    Layout is computed dynamically from minimum tile dimensions so the
    calendar stays readable regardless of how many months are shown:

    - ``vertical_spacing`` is kept at 6 % per gap (was 18 %) so whitespace
      does not balloon as the row count grows.
    - Figure height is derived from a per-tile minimum (``_TILE_H_PX``) so
      tiles never shrink below the point where three lines of text fit.
    - Month-title annotation offset is adaptive so it always sits inside
      the inter-row gap without clipping into the subplot above.
    - Export header annotations (title + context block) use y-coordinates
      computed from the actual top margin, fixing silent clipping at ≥ 3 rows.
    - For screen rendering (no ``context_lines``) a small 60 px top margin is
      used and no header annotation is added — Streamlit's own heading suffices.
    """
    if not completion_dates:
        return go.Figure()

    cols = max(1, int(cols))
    months_to_show = max(1, int(months_to_show))

    # Build month list starting from the forecast start month.
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

    axis_days = [first_day + dt.timedelta(days=i) for i in range((last_day - first_day).days + 1)]
    probs = completion_cdf_by_date(completion_dates, axis_days)
    prob_by_date = {d: p for d, p in zip(axis_days, probs)}

    rows = int(np.ceil(len(months) / cols))

    # ── Layout constants ──────────────────────────────────────────────────
    # vertical_spacing is the fraction of total paper height consumed by each
    # inter-row gap.  0.06 (6 %) replaces the original 0.18 (18 %) which made
    # the total gap space 54 % of the figure for 4 rows, leaving tiles too small.
    _V_SPACING = 0.06
    _H_SPACING = 0.04   # horizontal gap between columns (unchanged)
    _TILE_H_PX = 44     # minimum pixel height per heatmap week row (6 per month)
    _TILE_W_PX = 60     # minimum pixel width per day column (7 per month)
    _L, _R, _B = 16, 16, 16

    # ── Compute paper dimensions that guarantee minimum tile sizes ────────
    # Fraction of paper height / width that is actual subplot content.
    row_content_frac = (1.0 - max(0, rows - 1) * _V_SPACING) / rows
    col_content_frac = (1.0 - max(0, cols - 1) * _H_SPACING) / cols

    paper_h = int(math.ceil(6 * _TILE_H_PX / row_content_frac))
    paper_w = int(math.ceil(7 * _TILE_W_PX / col_content_frac))

    # ── Adaptive month-title annotation offset ────────────────────────────
    # The month title sits between rows in the inter-row gap.  Its offset
    # (in paper coords) must keep the annotation box within that gap so it
    # does not bleed into the subplot above.
    # Estimated annotation height: font 12 pt ≈ 16 px + 2×3 borderpad + 2 border = 24 px.
    _ANN_H_PX = 24
    _ann_h_frac = _ANN_H_PX / paper_h
    # offset = bottom of annotation above subplot top; capped so top stays in gap.
    _ann_offset = max(0.004, min(0.020, _V_SPACING - _ann_h_frac - 0.008))

    # ── Top margin and export-header y positions ──────────────────────────
    # Header annotations are only added for export (context_lines provided).
    # Using yanchor="top": pixel_from_figure_top = T − (y − 1) × paper_h
    _TITLE_H_PX = 44  # 20 pt text + 2×10 borderpad + 2 px border ≈ 44 px
    if context_lines:
        _ctx_visible = [_esc(str(x)) for x in context_lines if str(x).strip()]
        _CTX_H_PX = max(22, len(_ctx_visible) * 16 + 8)
        # Stack: 10 px top pad → title → 8 px gap → context block → 10 px bottom pad.
        T = max(60, 10 + _TITLE_H_PX + 8 + _CTX_H_PX + 10)
        # y so annotation top lands 10 px below figure top edge.
        title_y = 1.0 + (T - 10) / paper_h
        ctx_y = 1.0 + (T - 10 - _TITLE_H_PX - 8) / paper_h
    else:
        # Screen mode: small margin, no header annotation.
        T = 60

    width = max(paper_w + _L + _R, 360 * cols)
    height = paper_h + T + _B

    # ── Create subplot grid ───────────────────────────────────────────────
    fig = make_subplots(
        rows=rows,
        cols=cols,
        subplot_titles=[""] * (rows * cols),
        horizontal_spacing=_H_SPACING,
        vertical_spacing=_V_SPACING,
    )

    dow = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"]
    cal = pycal.Calendar(firstweekday=0)

    colors = {
        0: "rgba(158, 158, 158, 0.15)",
        1: "rgba(244, 67, 54, 0.30)",
        2: "rgba(255, 193, 7, 0.35)",
        3: "rgba(139, 195, 74, 0.35)",
        4: "rgba(76, 175, 80, 0.35)",
    }
    colorscale = [
        (0.00, colors[0]),
        (0.1999, colors[0]),
        (0.20, colors[1]),
        (0.3999, colors[1]),
        (0.40, colors[2]),
        (0.5999, colors[2]),
        (0.60, colors[3]),
        (0.7999, colors[3]),
        (0.80, colors[4]),
        (1.00, colors[4]),
    ]

    for idx_m, (yy, mm) in enumerate(months):
        r = idx_m // cols + 1
        c = idx_m % cols + 1
        axis_idx = (r - 1) * cols + c

        weeks = cal.monthdayscalendar(yy, mm)
        while len(weeks) < 6:
            weeks.append([0] * 7)

        z: List[List[Optional[int]]] = []
        text: List[List[str]] = []
        for w in weeks:
            z_row: List[Optional[int]] = []
            t_row: List[str] = []
            for day in w:
                if day == 0:
                    z_row.append(None)
                    t_row.append("")
                    continue
                d = dt.date(yy, mm, day)
                p = float(prob_by_date.get(d, 0.0))
                z_row.append(_band_bucket(p))
                pct = int(round(p * 100))
                s_lbl = _esc((sprint_label_by_date.get(d, "") or "").replace("Sprint ", "S"))
                t_row.append(f"{day}<br>{pct}%<br>{s_lbl}")
            z.append(z_row)
            text.append(t_row)

        # Plotly categorical Y renders first category at bottom; reverse so week-1 is on top.
        z = list(reversed(z))
        text = list(reversed(text))

        fig.add_trace(
            go.Heatmap(
                z=z,
                x=dow,
                y=["W6", "W5", "W4", "W3", "W2", "W1"],
                text=text,
                texttemplate="%{text}",
                textfont={"size": 9},
                colorscale=colorscale,
                zmin=0,
                zmax=4,
                showscale=False,
                hoverinfo="skip",
                xgap=3,
                ygap=3,
            ),
            row=r,
            col=c,
        )

        # Hide weekday labels for export cleanliness.
        fig.update_xaxes(row=r, col=c, side="top", showticklabels=False, showgrid=False, zeroline=False, ticks="")
        fig.update_yaxes(row=r, col=c, showticklabels=False, showgrid=False, zeroline=False, ticks="")

        # Frame + month title above frame.
        xdom = _get_domain(fig, _axis_name("x", axis_idx))
        ydom = _get_domain(fig, _axis_name("y", axis_idx))

        fig.add_shape(
            type="rect",
            xref="paper",
            yref="paper",
            x0=xdom[0],
            x1=xdom[1],
            y0=ydom[0],
            y1=ydom[1],
            line=dict(color="rgba(0,0,0,0.20)", width=1),
            fillcolor="rgba(0,0,0,0)",
            layer="above",
        )

        month_title = dt.date(yy, mm, 1).strftime("%B %Y")
        fig.add_annotation(
            text=f"<b>{month_title}</b>",
            x=(xdom[0] + xdom[1]) / 2.0,
            y=ydom[1] + _ann_offset,   # adaptive offset — stays within inter-row gap
            xref="paper",
            yref="paper",
            xanchor="center",
            yanchor="bottom",
            showarrow=False,
            font=dict(size=12),        # slightly smaller than before (was 14) for compact fit
            bgcolor="rgba(255,255,255,0.98)",
            bordercolor="rgba(0,0,0,0.10)",
            borderwidth=1,
            borderpad=3,               # was 4
        )

    # ── Export header (title + context block) ─────────────────────────────
    # Only added when context_lines is provided (export path).
    # For screen rendering the Streamlit heading already labels the chart.
    if context_lines:
        fig.add_annotation(
            text=f"<b>{_esc(title)}</b>",
            x=0.5,
            y=title_y,
            xref="paper",
            yref="paper",
            xanchor="center",
            yanchor="top",
            showarrow=False,
            font=dict(size=20),
            bgcolor="rgba(255,255,255,0.98)",
            bordercolor="rgba(0,0,0,0.12)",
            borderwidth=1,
            borderpad=10,
        )
        fig.add_annotation(
            text="<br>".join(_ctx_visible),
            x=0.0,
            y=ctx_y,
            xref="paper",
            yref="paper",
            xanchor="left",
            yanchor="top",
            showarrow=False,
            align="left",
            font=dict(size=12),
            bgcolor="rgba(255,255,255,0.98)",
            bordercolor="rgba(0,0,0,0.12)",
            borderwidth=1,
            borderpad=10,
        )

    fig.update_layout(
        margin=dict(l=_L, r=_R, t=T, b=_B),
        height=height,
        width=width,
        paper_bgcolor="white",
        plot_bgcolor="white",
        font=dict(size=12),
    )
    return fig
