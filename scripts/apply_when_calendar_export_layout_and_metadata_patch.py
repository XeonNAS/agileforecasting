#!/usr/bin/env python3
"""apply_when_calendar_export_layout_and_metadata_patch.py

Applies two changes:
1) Writes/overwrites: src/agile_mc/calendar_export.py
2) Patches: streamlit_app/app.py
   - Ensures import: from agile_mc.calendar_export import build_when_calendar_figure
   - Replaces the WHEN "Prepare calendar download" handler to pass context_lines
     (start date, items remaining, basis, sims, org/project/team, etc.)

Run from repo root:
  python3 apply_when_calendar_export_layout_and_metadata_patch.py
"""

from __future__ import annotations

import re
import shutil
from pathlib import Path

REPO = Path(__file__).resolve().parent
APP = REPO / "streamlit_app" / "app.py"
BAK = REPO / "streamlit_app" / "app.py.bak_cal_export_meta"

CAL_EXPORT_SRC = REPO / "src" / "agile_mc" / "calendar_export.py"

def _ensure_import(src: str) -> str:
    imp = "from agile_mc.calendar_export import build_when_calendar_figure\n"
    if imp in src:
        return src

    # Insert after the last 'from agile_mc.' import if present, else after sys.path block.
    lines = src.splitlines(True)
    last_idx = -1
    for i, ln in enumerate(lines):
        if ln.startswith("from agile_mc.") or ln.startswith("import agile_mc"):
            last_idx = i
    if last_idx >= 0:
        lines.insert(last_idx + 1, imp)
        return "".join(lines)

    # Fallback: insert after sys.path insert block if present
    text = "".join(lines)
    m = re.search(r"sys\.path\.insert\(0,\s*str\(SRC\)\)\s*\n", text)
    if m:
        insert_at = text[:m.end()].count("\n")
        lines.insert(insert_at + 1, imp)
        return "".join(lines)

    # Fallback: top
    return imp + src

def _replace_calendar_download_block(src: str) -> str:
    # Replace the contents of:
    # st.subheader("Download calendar") ... until the next 'figs = when_figures'
    pat = re.compile(
        r"st\.subheader\(\s*\"Download calendar\"\s*\)\s*\n"
        r"[\s\S]*?"
        r"\n\s*figs\s*=\s*when_figures\(",
        re.MULTILINE,
    )

    replacement = (
        'st.subheader("Download calendar")\n'
        'cal_fmt = st.selectbox("Download format", ["png", "svg"], index=0, key="dl_fmt_calendar")\n'
        'if st.button("Prepare calendar download", key="dl_btn_calendar"):\n'
        '    context_lines = [\n'
        '        f"ADO: {org}/{project}/{team}",\n'
        '        f"Start date: {forecast_start.isoformat()}",\n'
        '        f"Items remaining: {items_remaining}",\n'
        '        f"Forecast basis: {basis}",\n'
        '        f"Simulations: {n_sims}",\n'
        '        f"Generated: {dt.datetime.now().strftime(\'%Y-%m-%d %H:%M\')}",\n'
        '    ]\n'
        '    if seed is not None:\n'
        '        context_lines.append(f"Seed: {seed}")\n'
        '    cal_fig = build_when_calendar_figure(\n'
        '        completion_dates=completion_dates,\n'
        '        sprint_label_by_date=sprint_label_by_date,\n'
        '        months_to_show=months_show,\n'
        '        start_date=forecast_start,\n'
        '        cols=4,\n'
        '        title="When forecast calendar",\n'
        '        context_lines=context_lines,\n'
        '    )\n'
        '    ex = export_plotly_figure(cal_fig, fmt=cal_fmt, base_name="when_calendar")\n'
        '    st.download_button("Download calendar", data=ex.data, file_name=ex.filename, mime=ex.mime, key="dl_real_calendar")\n'
        '\n'
        'figs = when_figures('
    )

    new_src, n = pat.subn(replacement, src, count=1)
    if n == 0:
        raise RuntimeError("Could not find the 'Download calendar' block to patch. Your app.py may have diverged.")
    return new_src

def main() -> None:
    if not APP.exists():
        raise SystemExit(f"Cannot find {APP}")
    if not CAL_EXPORT_SRC.exists():
        raise SystemExit(f"Cannot find {CAL_EXPORT_SRC}. Make sure you unzipped this patch into repo root.")

    if not BAK.exists():
        shutil.copy2(APP, BAK)
        print(f"[OK] Backup created: {BAK}")

    src = APP.read_text(encoding="utf-8")
    src = _ensure_import(src)
    src = _replace_calendar_download_block(src)

    APP.write_text(src, encoding="utf-8")
    print("[OK] Patched app.py for calendar export metadata + improved layout.")
    print("Restart Streamlit and re-export the calendar PNG/SVG.")

if __name__ == "__main__":
    main()
