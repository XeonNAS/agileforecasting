#!/usr/bin/env python3
from __future__ import annotations

import os
import zipfile
from pathlib import Path

REPO = Path(__file__).resolve().parent
OUT = REPO / "source_upload.zip"

# Patterns to exclude (directories and files)
EXCLUDE_DIRS = {
    ".venv", "venv", "__pycache__", ".pytest_cache", ".mypy_cache", ".ruff_cache",
    ".git", ".idea", ".vscode", ".streamlit", "node_modules", "dist", "build"
}
EXCLUDE_FILES = {
    ".DS_Store",
    "settings.enc",          # encrypted settings file (if present)
    "secrets.toml",          # streamlit secrets (if present)
    ".env", ".env.local", ".envrc",
}

def should_skip(path: Path) -> bool:
    # Skip excluded directories anywhere in the tree
    for part in path.parts:
        if part in EXCLUDE_DIRS:
            return True

    # Skip excluded filenames
    if path.name in EXCLUDE_FILES:
        return True

    # Skip common large/binary artifacts
    if path.suffix.lower() in {".pyc", ".pyo", ".pyd"}:
        return True

    return False

def main() -> None:
    if OUT.exists():
        OUT.unlink()

    with zipfile.ZipFile(OUT, "w", compression=zipfile.ZIP_DEFLATED) as z:
        for p in REPO.rglob("*"):
            if not p.is_file():
                continue
            if should_skip(p):
                continue

            arcname = p.relative_to(REPO).as_posix()
            z.write(p, arcname)

    print(f"[OK] Created: {OUT}")
    print(f"Size: {OUT.stat().st_size / (1024*1024):.2f} MB")

if __name__ == "__main__":
    main()
