#!/usr/bin/env python3
"""apply_fix_calendar_export_framed.py

Patch script to repair `src/agile_mc/calendar_export.py` so that:
- `from __future__ import annotations` is the FIRST line (fixes SyntaxError)
- Exported WHEN calendar uses framed month panels and month titles ABOVE the frame
- Week order is correct (first week at top) and no month name overlays day cells

Run from your repo root:
  python3 apply_fix_calendar_export_framed.py
  streamlit run streamlit_app/app.py

What it does:
- Creates a timestamped backup of calendar_export.py (if it exists)
- Overwrites calendar_export.py with a known-good version
- Validates the result parses as Python
"""

from __future__ import annotations

import ast
import datetime as dt
import shutil
from pathlib import Path

REPO = Path(__file__).resolve().parent
TARGET = REPO / "src" / "agile_mc" / "calendar_export.py"

CONTENT = 'from __future__ import annotations\n\nimport calendar as pycal\nimport datetime as dt\nfrom typing import Dict, List, Optional, Tuple\n\nimport numpy as np\nimport plotly.graph_objects as go\nfrom plotly.subplots import make_subplots\n\nfrom .simulation import completion_cdf_by_date\n\n\ndef _month_last_day(year: int, month: int) -> dt.date:\n    if month == 12:\n        return dt.date(year + 1, 1, 1) - dt.timedelta(days=1)\n    return dt.date(year, month + 1, 1) - dt.timedelta(days=1)\n\n\ndef _band_bucket(p: float) -> int:\n    if p >= 0.95:\n        return 4\n    if p >= 0.85:\n        return 3\n    if p >= 0.70:\n        return 2\n    if p >= 0.50:\n        return 1\n    return 0\n\n\ndef _axis_name(prefix: str, idx: int) -> str:\n    # idx=1 => xaxis/yaxis, idx>1 => xaxis2/yaxis2\n    return f"{prefix}axis" if idx == 1 else f"{prefix}axis{idx}"\n\n\ndef _get_domain(fig: go.Figure, axis: str) -> Tuple[float, float]:\n    obj = getattr(fig.layout, axis)\n    dom = getattr(obj, "domain", None)\n    if not dom:\n        return (0.0, 1.0)\n    return (float(dom[0]), float(dom[1]))\n\n\ndef build_when_calendar_figure(\n    completion_dates: List[dt.date],\n    sprint_label_by_date: Dict[dt.date, str],\n    months_to_show: int,\n    start_date: dt.date,\n    cols: int = 4,\n    *,\n    title: str = "When forecast calendar",\n    context_lines: Optional[List[str]] = None,\n) -> go.Figure:\n    """Export-friendly Plotly calendar for PNG/SVG (framed months, no overlaps).\n\n    - Each month panel is framed with an outline.\n    - The month name is placed ABOVE the frame (never inside the day grid).\n    - Week order is corrected so the first week appears at the top.\n    - Weekday labels are hidden to avoid collisions in export.\n    """\n    if not completion_dates:\n        return go.Figure()\n\n    cols = max(1, int(cols))\n    months_to_show = max(1, int(months_to_show))\n\n    # Build month list starting from the forecast start month\n    anchor = dt.date(start_date.year, start_date.month, 1)\n    months: List[tuple[int, int]] = []\n    y, m = anchor.year, anchor.month\n    for _ in range(months_to_show):\n        months.append((y, m))\n        m += 1\n        if m == 13:\n            y += 1\n            m = 1\n\n    first_day = dt.date(months[0][0], months[0][1], 1)\n    last_day = _month_last_day(months[-1][0], months[-1][1])\n\n    axis_days = [first_day + dt.timedelta(days=i) for i in range((last_day - first_day).days + 1)]\n    probs = completion_cdf_by_date(completion_dates, axis_days)\n    prob_by_date = {d: p for d, p in zip(axis_days, probs)}\n\n    rows = int(np.ceil(len(months) / cols))\n\n    # Do NOT use subplot_titles; draw month titles above each frame.\n    fig = make_subplots(\n        rows=rows,\n        cols=cols,\n        subplot_titles=[""] * (rows * cols),\n        horizontal_spacing=0.04,\n        vertical_spacing=0.18,\n    )\n\n    dow = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"]\n    cal = pycal.Calendar(firstweekday=0)\n\n    colors = {\n        0: "rgba(158, 158, 158, 0.15)",\n        1: "rgba(244, 67, 54, 0.30)",\n        2: "rgba(255, 193, 7, 0.35)",\n        3: "rgba(139, 195, 74, 0.35)",\n        4: "rgba(76, 175, 80, 0.35)",\n    }\n    colorscale = [\n        (0.00, colors[0]), (0.1999, colors[0]),\n        (0.20, colors[1]), (0.3999, colors[1]),\n        (0.40, colors[2]), (0.5999, colors[2]),\n        (0.60, colors[3]), (0.7999, colors[3]),\n        (0.80, colors[4]), (1.00, colors[4]),\n    ]\n\n    for idx_m, (yy, mm) in enumerate(months):\n        r = idx_m // cols + 1\n        c = idx_m % cols + 1\n        axis_idx = (r - 1) * cols + c\n\n        weeks = cal.monthdayscalendar(yy, mm)\n        while len(weeks) < 6:\n            weeks.append([0] * 7)\n\n        z: List[List[Optional[int]]] = []\n        text: List[List[str]] = []\n        for w in weeks:\n            z_row: List[Optional[int]] = []\n            t_row: List[str] = []\n            for day in w:\n                if day == 0:\n                    z_row.append(None)\n                    t_row.append("")\n                    continue\n                d = dt.date(yy, mm, day)\n                p = float(prob_by_date.get(d, 0.0))\n                z_row.append(_band_bucket(p))\n                pct = int(round(p * 100))\n                s_lbl = (sprint_label_by_date.get(d, "") or "").replace("Sprint ", "S")\n                t_row.append(f"{day}<br>{pct}%<br>{s_lbl}")\n            z.append(z_row)\n            text.append(t_row)\n\n        # Plotly categorical Y renders first category at bottom; reverse so week-1 is on top.\n        z = list(reversed(z))\n        text = list(reversed(text))\n\n        fig.add_trace(\n            go.Heatmap(\n                z=z,\n                x=dow,\n                y=["W6", "W5", "W4", "W3", "W2", "W1"],\n                text=text,\n                texttemplate="%{text}",\n                textfont={"size": 9},\n                colorscale=colorscale,\n                zmin=0,\n                zmax=4,\n                showscale=False,\n                hoverinfo="skip",\n                xgap=3,\n                ygap=3,\n            ),\n            row=r,\n            col=c,\n        )\n\n        # Hide weekday labels for export cleanliness\n        fig.update_xaxes(row=r, col=c, side="top", showticklabels=False, showgrid=False, zeroline=False, ticks="")\n        fig.update_yaxes(row=r, col=c, showticklabels=False, showgrid=False, zeroline=False, ticks="")\n\n        # Frame + month title above frame\n        xdom = _get_domain(fig, _axis_name("x", axis_idx))\n        ydom = _get_domain(fig, _axis_name("y", axis_idx))\n\n        fig.add_shape(\n            type="rect",\n            xref="paper",\n            yref="paper",\n            x0=xdom[0],\n            x1=xdom[1],\n            y0=ydom[0],\n            y1=ydom[1],\n            line=dict(color="rgba(0,0,0,0.20)", width=1),\n            fillcolor="rgba(0,0,0,0)",\n            layer="above",\n        )\n\n        month_title = dt.date(yy, mm, 1).strftime("%B %Y")\n        fig.add_annotation(\n            text=f"<b>{month_title}</b>",\n            x=(xdom[0] + xdom[1]) / 2.0,\n            y=ydom[1] + 0.03,\n            xref="paper",\n            yref="paper",\n            xanchor="center",\n            yanchor="bottom",\n            showarrow=False,\n            font=dict(size=14),\n            bgcolor="rgba(255,255,255,0.98)",\n            bordercolor="rgba(0,0,0,0.10)",\n            borderwidth=1,\n            borderpad=4,\n        )\n\n    # Global header + context\n    fig.add_annotation(\n        text=f"<b>{title}</b>",\n        x=0.5,\n        y=1.95,\n        xref="paper",\n        yref="paper",\n        xanchor="center",\n        yanchor="top",\n        showarrow=False,\n        font=dict(size=20),\n        bgcolor="rgba(255,255,255,0.98)",\n        bordercolor="rgba(0,0,0,0.12)",\n        borderwidth=1,\n        borderpad=10,\n    )\n\n    if context_lines:\n        fig.add_annotation(\n            text="<br>".join([str(x) for x in context_lines if str(x).strip()]),\n            x=0.0,\n            y=1.87,\n            xref="paper",\n            yref="paper",\n            xanchor="left",\n            yanchor="top",\n            showarrow=False,\n            align="left",\n            font=dict(size=12),\n            bgcolor="rgba(255,255,255,0.98)",\n            bordercolor="rgba(0,0,0,0.12)",\n            borderwidth=1,\n            borderpad=10,\n        )\n\n    width = int(520 * cols)\n    height = int(320 * rows + 700)\n\n    fig.update_layout(\n        margin=dict(l=18, r=18, t=700, b=18),\n        height=height,\n        width=width,\n        paper_bgcolor="white",\n        plot_bgcolor="white",\n        font=dict(size=12),\n    )\n    return fig\n'

def main() -> None:
    TARGET.parent.mkdir(parents=True, exist_ok=True)

    if TARGET.exists():
        stamp = dt.datetime.now().strftime("%Y%m%d_%H%M%S")
        bak = TARGET.with_name(TARGET.name + f".bak_{stamp}")
        shutil.copy2(TARGET, bak)
        print(f"[OK] Backup created: {bak}")

    TARGET.write_text(CONTENT, encoding="utf-8", newline="\n")

    ast.parse(TARGET.read_text(encoding="utf-8"))
    first_line = TARGET.read_text(encoding="utf-8").splitlines()[0].strip()
    if first_line != "from __future__ import annotations":
        raise SystemExit(f"[FAIL] First line is not future import: {first_line!r}")

    print(f"[OK] Patched: {TARGET}")
    print("[NEXT] Restart Streamlit:")
    print("  streamlit run streamlit_app/app.py")

if __name__ == "__main__":
    main()
