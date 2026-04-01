#!/usr/bin/env python3
"""Fix 'No module named agile_mc' by ensuring src/ is on sys.path before any agile_mc imports.

Run from repo root:
  python3 apply_fix_agile_mc_import_and_init.py
"""

from __future__ import annotations

from pathlib import Path
import re

REPO_ROOT = Path(__file__).resolve().parent
APP = REPO_ROOT / "streamlit_app" / "app.py"
PKG_INIT = REPO_ROOT / "src" / "agile_mc" / "__init__.py"

SYS_PATH_BLOCK = [
    "import sys\n",
    "from pathlib import Path\n",
    "\n",
    "REPO_ROOT = Path(__file__).resolve().parents[1]\n",
    "SRC = REPO_ROOT / \"src\"\n",
    "if str(SRC) not in sys.path:\n",
    "    sys.path.insert(0, str(SRC))\n",
    "\n",
]

def main() -> None:
    if not APP.exists():
        raise SystemExit(f"Cannot find {APP}")

    lines = APP.read_text(encoding="utf-8").splitlines(keepends=True)

    # ensure package init exists (so agile_mc is a package even without installation)
    PKG_INIT.parent.mkdir(parents=True, exist_ok=True)
    if not PKG_INIT.exists():
        PKG_INIT.write_text("# Package marker for agile_mc\n", encoding="utf-8")
        print(f"[OK] Created {PKG_INIT}")

    # Find the first agile_mc import
    agile_idx = None
    agile_lines = []
    agile_pat = re.compile(r"^\s*(from\s+agile_mc\b|import\s+agile_mc\b)")

    for i, ln in enumerate(lines):
        if agile_pat.match(ln):
            agile_idx = i
            break

    # Find existing sys.path insertion referencing SRC or /src
    sys_idx = None
    for i, ln in enumerate(lines):
        if "sys.path.insert" in ln and "SRC" in "".join(lines[max(0, i-3):i+3]):
            sys_idx = i
            break
        if "sys.path.insert" in ln and "src" in ln:
            sys_idx = i
            break

    # If there are agile_mc imports before sys.path block, move them to after sys.path block.
    if agile_idx is not None:
        # capture contiguous agile_mc import lines at top region
        i = agile_idx
        while i < len(lines) and agile_pat.match(lines[i]):
            agile_lines.append(lines[i])
            i += 1
        if agile_lines:
            del lines[agile_idx:agile_idx+len(agile_lines)]
            print(f"[OK] Lifted {len(agile_lines)} agile_mc import line(s) for reinsertion.")

    # If sys.path block exists but is after where agile imports were, we still ensure block is early.
    # We'll insert sys.path block right after __future__ import if present; otherwise at top.
    insert_at = 0
    for i, ln in enumerate(lines[:30]):
        if ln.startswith("from __future__ import"):
            insert_at = i + 1
            break

    # Remove any existing duplicate SYS_PATH_BLOCK (conservative): remove a block that sets REPO_ROOT/SRC and sys.path.insert
    text = "".join(lines)
    dup_pat = re.compile(r"REPO_ROOT\s*=\s*Path\(__file__\)\.resolve\(\)\.parents\[1\].*?sys\.path\.insert\(0,\s*str\(SRC\)\)", re.DOTALL)
    m = dup_pat.search(text)
    if m:
        # rebuild without that matched chunk by deleting corresponding line spans
        start = text[:m.start()].count("\n")
        end = text[:m.end()].count("\n")
        del lines[start:end+1]
        print("[OK] Removed existing sys.path block (will reinsert cleanly).")

    # Insert sys.path block
    lines[insert_at:insert_at] = [l.replace("\n", "\n") for l in SYS_PATH_BLOCK]
    print("[OK] Inserted sys.path block near top.")

    # Reinsert agile imports after sys.path block if captured
    if agile_lines:
        # find end of inserted sys block: after the 'sys.path.insert' and blank line
        # we inserted at insert_at, length is len(SYS_PATH_BLOCK)
        rein_at = insert_at + len(SYS_PATH_BLOCK)
        lines[rein_at:rein_at] = agile_lines + ["\n"] if (rein_at < len(lines) and lines[rein_at] != "\n") else agile_lines
        print("[OK] Reinserted agile_mc imports after sys.path block.")

    APP.write_text("".join(lines), encoding="utf-8")
    print(f"[DONE] Updated {APP}")

if __name__ == "__main__":
    main()
