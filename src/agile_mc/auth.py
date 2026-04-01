from __future__ import annotations

import os


def get_app_password() -> str | None:
    """Return the required app password, or None if the password gate is disabled.

    Set the MC_APP_PASSWORD environment variable to enable the gate.
    Returns None (gate disabled) when the variable is unset or blank.
    """
    pw = os.environ.get("MC_APP_PASSWORD", "").strip()
    return pw or None
