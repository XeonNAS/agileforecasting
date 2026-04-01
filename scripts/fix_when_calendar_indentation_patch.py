#!/usr/bin/env python3
"""fix_when_calendar_indentation_patch.py

Repairs streamlit_app/app.py after a bad patch caused IndentationError around:
- Download calendar section
- Show distribution charts expander

This script replaces the WHEN forecast segment from:
    st.subheader("Download calendar")
up to (but not including):
    summary = {

with a known-good, correctly-indented block.

Run from repo root:
  python3 fix_when_calendar_indentation_patch.py
"""

from __future__ import annotations

import ast
import re
import shutil
from pathlib import Path

REPO = Path(__file__).resolve().parent
APP = REPO / "streamlit_app" / "app.py"
BAK = REPO / "streamlit_app" / "app.py.bak_fix_when_indent"

REPLACEMENT = """    st.subheader("Download calendar")
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

"""

def main() -> None:
    if not APP.exists():
        raise SystemExit(f"Cannot find {APP}")

    src = APP.read_text(encoding="utf-8")

    if not BAK.exists():
        shutil.copy2(APP, BAK)
        print(f"[OK] Backup created: {BAK}")

    pat = re.compile(
        r'\n\s*st\.subheader\(\s*"Download calendar"\s*\)\s*\n[\s\S]*?\n\s*summary\s*=\s*\{',
        re.MULTILINE,
    )

    def repl(m: re.Match) -> str:
        return "\n" + REPLACEMENT + "    summary = {"

    src2, n = pat.subn(repl, src, count=1)
    if n == 0:
        raise SystemExit("Could not find the 'Download calendar' section to replace. app.py may have diverged.")

    try:
        ast.parse(src2)
    except SyntaxError as e:
        raise SystemExit(f"Patched file still invalid: {e}")

    APP.write_text(src2, encoding="utf-8")
    print("[OK] Fixed WHEN calendar export indentation and restored chart expander block.")
    print("Now restart Streamlit: streamlit run streamlit_app/app.py")

if __name__ == "__main__":
    main()
