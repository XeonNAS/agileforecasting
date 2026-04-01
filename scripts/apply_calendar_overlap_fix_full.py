#!/usr/bin/env python3
"""apply_calendar_overlap_fix_full.py

Fixes BOTH:
1) In-app HTML calendar overlap:
   - clips overflow so a day cell can't bleed into adjacent month tiles
   - forces grid items to shrink (min-width:0)
   - ellipsizes sprint labels so they don't force wider cells

2) Exported PNG/SVG calendar overlap:
   - this patch zip also includes src/agile_mc/calendar_export.py (export-safe layout).
     Unzipping into repo root will overwrite it.

Run from your repo root (folder containing streamlit_app/ and src/):
  python3 apply_calendar_overlap_fix_full.py
  streamlit run streamlit_app/app.py
"""

from __future__ import annotations

import shutil
from pathlib import Path

REPO = Path(__file__).resolve().parent
APP = REPO / "streamlit_app" / "app.py"
BAK_APP = REPO / "streamlit_app" / "app.py.bak_overlap_fix_full_v2"

CSS_SNIPPET = (
    "\n/* PATCH: prevent calendar tile overlap */\n"
    ".mcmonths { min-width: 0; }\n"
    ".mcmonth { min-width: 0; overflow: hidden; position: relative; }\n"
    ".mccal { min-width: 0; }\n"
    ".mccell { min-width: 0; box-sizing: border-box; }\n"
    ".mccell .s { white-space: nowrap; overflow: hidden; text-overflow: ellipsis; }\n"
)

def main() -> None:
    if not APP.exists():
        raise SystemExit(f"Cannot find {APP}. Run this from the repo root (folder containing streamlit_app/).")

    if not BAK_APP.exists():
        shutil.copy2(APP, BAK_APP)
        print(f"[OK] Backup created: {BAK_APP}")

    src = APP.read_text(encoding="utf-8")

    if "PATCH: prevent calendar tile overlap" in src:
        print("[OK] In-app calendar overlap CSS already present.")
        return

    marker = "</style>"
    idx = src.find(marker)
    if idx < 0:
        raise SystemExit("Could not find </style> in app.py to inject CSS.")

    src = src[:idx] + CSS_SNIPPET + src[idx:]
    APP.write_text(src, encoding="utf-8")
    print("[OK] Injected in-app calendar overlap CSS.")
    print("[NEXT] Restart Streamlit:")
    print("  streamlit run streamlit_app/app.py")

if __name__ == "__main__":
    main()
