from __future__ import annotations

import base64
import json
import os
import shutil
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, List, Optional

from cryptography.fernet import Fernet
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.kdf.pbkdf2 import PBKDF2HMAC


@dataclass(frozen=True)
class SecureStorePaths:
    config_dir: Path
    enc_file: Path


_APP_NAME = "agileforecasting"
_OLD_APP_NAME = "agile-montecarlo"  # pre-rename; kept only for migration


def _config_base(app_name: str) -> Path:
    """Return the platform-appropriate config directory for *app_name*.

    - Windows: ``%APPDATA%\\<app_name>``  (roaming profile)
    - Linux / other: ``~/.config/<app_name>``
    """
    if sys.platform == "win32":
        appdata = os.environ.get("APPDATA") or str(Path.home() / "AppData" / "Roaming")
        return Path(appdata) / app_name
    return Path(os.path.expanduser("~")) / ".config" / app_name


def _migration_candidates(app_name: str) -> List[Path]:
    """Old settings paths to check when the canonical file does not yet exist.

    Candidates are evaluated in order; the first existing file is migrated.

    Sources (all best-effort, leaves originals untouched):
      Windows only — ``~\\.config\\<app>`` written by pre-fix versions of the app
                     that used the Linux path convention on Windows.
      All platforms — ``~/.config/<old_app_name>`` from the agile-montecarlo rename.
    """
    candidates: List[Path] = []
    if sys.platform == "win32":
        # Versions before the Windows-path fix stored settings under ~/.config on Windows.
        candidates.append(Path(os.path.expanduser("~")) / ".config" / app_name / "ado_settings.enc.json")
        # Old app name under %APPDATA% (unlikely, but covers the case where someone
        # renamed the app while already on the Windows-native path).
        appdata = os.environ.get("APPDATA") or str(Path.home() / "AppData" / "Roaming")
        candidates.append(Path(appdata) / _OLD_APP_NAME / "ado_settings.enc.json")
    # All platforms: app-rename migration (agile-montecarlo → agileforecasting).
    candidates.append(Path(os.path.expanduser("~")) / ".config" / _OLD_APP_NAME / "ado_settings.enc.json")
    return candidates


def default_paths(app_name: str = _APP_NAME) -> SecureStorePaths:
    base = _config_base(app_name)
    paths = SecureStorePaths(config_dir=base, enc_file=base / "ado_settings.enc.json")

    # One-time migration: copy settings from an old location when the canonical
    # file does not yet exist.  Leaves old files in place (user can delete them).
    if not paths.enc_file.exists():
        for old_enc in _migration_candidates(app_name):
            if old_enc.exists():
                paths.config_dir.mkdir(parents=True, exist_ok=True)
                try:
                    shutil.copy2(old_enc, paths.enc_file)
                    break  # stop after the first successful migration
                except Exception:
                    pass  # migration is best-effort; user can re-save manually

    return paths


def _derive_key(passphrase: str, salt: bytes, iterations: int = 200_000) -> bytes:
    kdf = PBKDF2HMAC(
        algorithm=hashes.SHA256(),
        length=32,
        salt=salt,
        iterations=iterations,
    )
    return base64.urlsafe_b64encode(kdf.derive(passphrase.encode("utf-8")))


def encrypt_json(data: Dict[str, Any], passphrase: str) -> Dict[str, str]:
    salt = os.urandom(16)
    key = _derive_key(passphrase, salt)
    f = Fernet(key)
    token = f.encrypt(json.dumps(data).encode("utf-8"))
    return {
        "v": "1",
        "salt_b64": base64.b64encode(salt).decode("ascii"),
        "token_b64": base64.b64encode(token).decode("ascii"),
    }


def decrypt_json(payload: Dict[str, str], passphrase: str) -> Dict[str, Any]:
    salt = base64.b64decode(payload["salt_b64"])
    token = base64.b64decode(payload["token_b64"])
    key = _derive_key(passphrase, salt)
    f = Fernet(key)
    raw = f.decrypt(token)
    return json.loads(raw.decode("utf-8"))


def save_encrypted(data: Dict[str, Any], passphrase: str, paths: Optional[SecureStorePaths] = None) -> Path:
    paths = paths or default_paths()
    paths.config_dir.mkdir(parents=True, exist_ok=True)

    payload = encrypt_json(data, passphrase)
    tmp = paths.enc_file.with_suffix(".tmp")
    tmp.write_text(json.dumps(payload, indent=2), encoding="utf-8")

    try:
        # Best-effort: restricts file access to the owner on Linux/macOS.
        # os.chmod has no meaningful effect on Windows (ACL-based permissions);
        # Fernet encryption is the primary protection on all platforms.
        os.chmod(tmp, 0o600)
    except Exception:
        pass

    tmp.replace(paths.enc_file)
    return paths.enc_file


def load_encrypted(passphrase: str, paths: Optional[SecureStorePaths] = None) -> Optional[Dict[str, Any]]:
    paths = paths or default_paths()
    if not paths.enc_file.exists():
        return None
    payload = json.loads(paths.enc_file.read_text(encoding="utf-8"))
    return decrypt_json(payload, passphrase)


def forget(paths: Optional[SecureStorePaths] = None) -> bool:
    paths = paths or default_paths()
    if paths.enc_file.exists():
        paths.enc_file.unlink()
        return True
    return False
