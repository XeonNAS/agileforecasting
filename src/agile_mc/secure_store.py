from __future__ import annotations

import base64
import json
import os
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, Optional

from cryptography.fernet import Fernet
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.kdf.pbkdf2 import PBKDF2HMAC


@dataclass(frozen=True)
class SecureStorePaths:
    config_dir: Path
    enc_file: Path


_APP_NAME = "agileforecasting"
_OLD_APP_NAME = "agile-montecarlo"  # pre-rename; kept only for migration


def default_paths(app_name: str = _APP_NAME) -> SecureStorePaths:
    # Linux-friendly default (~/.config/agileforecasting/); works on all OSes.
    base = Path(os.path.expanduser("~")) / ".config" / app_name
    paths = SecureStorePaths(config_dir=base, enc_file=base / "ado_settings.enc.json")

    # One-time migration: copy settings from the old directory if the new one
    # does not yet exist.  Leaves the old file in place (user can delete it).
    if not paths.enc_file.exists():
        old_enc = Path(os.path.expanduser("~")) / ".config" / _OLD_APP_NAME / "ado_settings.enc.json"
        if old_enc.exists():
            import shutil

            paths.config_dir.mkdir(parents=True, exist_ok=True)
            try:
                shutil.copy2(old_enc, paths.enc_file)
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

    # Best-effort restrictive permissions
    try:
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
