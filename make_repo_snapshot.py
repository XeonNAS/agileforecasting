#!/usr/bin/env python3
from __future__ import annotations

import os
import sys
import zipfile
from pathlib import Path

ROOT = Path.cwd()
OUT = ROOT / 'repo_snapshot_for_chatgpt.zip'

INCLUDE_EXTS = {
    '.py', '.toml', '.yaml', '.yml', '.json', '.md', '.txt', '.ini', '.cfg', '.env.example'
}
INCLUDE_NAMES = {
    'Dockerfile', 'docker-compose.yml', 'docker-compose.yaml', 'requirements.txt',
    'pyproject.toml', 'poetry.lock', 'Pipfile', 'Pipfile.lock'
}
SKIP_DIRS = {
    '.git', '.venv', 'venv', '__pycache__', '.mypy_cache', '.pytest_cache',
    'node_modules', '.idea', '.vscode', 'dist', 'build', '.streamlit',
    '.ruff_cache', '.cache'
}

if not (ROOT / 'streamlit_app' / 'app.py').exists():
    print('ERROR: This folder does not contain streamlit_app/app.py')
    print('Open Terminal in the real repo root first, then run this script again.')
    sys.exit(1)

with zipfile.ZipFile(OUT, 'w', zipfile.ZIP_DEFLATED) as zf:
    for path in ROOT.rglob('*'):
        rel = path.relative_to(ROOT)
        parts = set(rel.parts)
        if parts & SKIP_DIRS:
            continue
        if path.is_dir():
            continue
        if path.name in INCLUDE_NAMES or path.suffix.lower() in INCLUDE_EXTS:
            zf.write(path, rel.as_posix())

print(f'Created: {OUT}')
