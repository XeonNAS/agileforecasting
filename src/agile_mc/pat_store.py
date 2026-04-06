"""
Secure PAT storage for AgileForecasting.

Primary backend  — OS credential store via ``keyring`` (GNOME Keyring /
SecretService on Linux, Keychain on macOS, Windows Credential Manager).

Fallback backend — AES-256 Fernet-encrypted file at
``~/.config/agileforecasting/pat.enc.json``, keyed by the user's existing
encryption passphrase.  The fallback activates automatically when the OS
keyring is unavailable (e.g. headless Linux servers without a running keyring
daemon) *and* the caller supplies a passphrase.  The file is created with
mode 0o600 and is kept separate from the non-secret settings file.

Platform notes:
  Linux  — requires a running ``gnome-keyring`` or ``kwallet`` daemon and the
            ``SecretStorage`` / ``jeepney`` Python packages (installed with
            ``keyring``).  Desktop installs work out of the box; headless
            servers fall back to the encrypted-file backend.
  macOS  — uses macOS Keychain; works out of the box.
  Windows — uses Windows Credential Manager; works out of the box.
"""
from __future__ import annotations

import json
import logging
import os
from typing import Optional

logger = logging.getLogger(__name__)

_SERVICE = "agileforecasting"
_DEFAULT_PROFILE = "default"


# ---------------------------------------------------------------------------
# Keyring helpers  (each swallows all exceptions so callers stay simple)
# ---------------------------------------------------------------------------

def _kr_set(pat: str, profile: str) -> bool:
    """Write PAT to OS keyring.  Returns True only after a verified round-trip."""
    try:
        import keyring as kr

        kr.set_password(_SERVICE, profile, pat)
        # Some backends (null / stub) accept the call but store nothing.
        return kr.get_password(_SERVICE, profile) == pat
    except Exception as exc:
        logger.debug("keyring set failed (%s): %s", type(exc).__name__, exc)
        return False


def _kr_get(profile: str) -> Optional[str]:
    try:
        import keyring as kr

        val = kr.get_password(_SERVICE, profile)
        return val or None
    except Exception as exc:
        logger.debug("keyring get failed (%s): %s", type(exc).__name__, exc)
        return None


def _kr_delete(profile: str) -> bool:
    try:
        import keyring as kr
        import keyring.errors

        kr.delete_password(_SERVICE, profile)
        return True
    except keyring.errors.PasswordDeleteError:
        return False
    except Exception as exc:
        logger.debug("keyring delete failed (%s): %s", type(exc).__name__, exc)
        return False


# ---------------------------------------------------------------------------
# Encrypted-file fallback
# ---------------------------------------------------------------------------

def _pat_enc_path():
    from agile_mc.secure_store import default_paths

    return default_paths().config_dir / "pat.enc.json"


def _file_save(pat: str, passphrase: str) -> None:
    from agile_mc.secure_store import default_paths, encrypt_json

    paths = default_paths()
    paths.config_dir.mkdir(parents=True, exist_ok=True)
    payload = encrypt_json({"pat": pat}, passphrase)
    enc_path = _pat_enc_path()
    tmp = enc_path.with_suffix(".tmp")
    tmp.write_text(json.dumps(payload, indent=2), encoding="utf-8")
    try:
        os.chmod(tmp, 0o600)
    except Exception:
        pass
    tmp.replace(enc_path)


def _file_load(passphrase: str) -> Optional[str]:
    from agile_mc.secure_store import decrypt_json

    enc_path = _pat_enc_path()
    if not enc_path.exists():
        return None
    try:
        payload = json.loads(enc_path.read_text(encoding="utf-8"))
        data = decrypt_json(payload, passphrase)
        return data.get("pat") or None
    except Exception:
        return None


def _file_delete() -> bool:
    enc_path = _pat_enc_path()
    if enc_path.exists():
        enc_path.unlink()
        return True
    return False


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def keyring_available() -> bool:
    """
    Return True if the OS keyring can durably store a credential.

    Performs a probe write/read/delete — call once per session and cache
    the result rather than calling on every render.
    """
    probe = "__agileforecasting_probe__"
    try:
        import keyring as kr

        kr.set_password(_SERVICE, probe, "1")
        ok = kr.get_password(_SERVICE, probe) == "1"
        if ok:
            try:
                kr.delete_password(_SERVICE, probe)
            except Exception:
                pass
        return ok
    except Exception:
        return False


def save_pat(pat: str, *, profile: str = _DEFAULT_PROFILE, passphrase: Optional[str] = None) -> str:
    """
    Store the PAT securely.

    Tries the OS keyring first.  Falls back to an AES-256 Fernet-encrypted
    file when a *passphrase* is supplied.  Raises ``RuntimeError`` if both
    backends are unavailable.

    Returns ``"keyring"`` or ``"file"`` to indicate which backend was used.
    """
    if _kr_set(pat, profile):
        return "keyring"
    if passphrase:
        _file_save(pat, passphrase)
        return "file"
    raise RuntimeError(
        "OS keyring unavailable and no passphrase provided for the encrypted-file fallback."
    )


def load_pat(*, profile: str = _DEFAULT_PROFILE, passphrase: Optional[str] = None) -> Optional[str]:
    """
    Load the PAT from secure storage.

    Checks the OS keyring first, then the encrypted file (only when a
    *passphrase* is supplied).  Returns ``None`` if the PAT is not found.
    """
    pat = _kr_get(profile)
    if pat:
        return pat
    if passphrase:
        return _file_load(passphrase)
    return None


def forget_pat(*, profile: str = _DEFAULT_PROFILE) -> bool:
    """
    Remove the PAT from all storage backends.

    Returns ``True`` if anything was removed.  Safe to call when nothing is
    stored.
    """
    from_keyring = _kr_delete(profile)
    from_file = _file_delete()
    return from_keyring or from_file
