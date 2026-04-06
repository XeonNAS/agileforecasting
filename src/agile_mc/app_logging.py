"""Application logging setup for AgileForecasting.

Centralised helper that:
* resolves the per-user log directory for the current platform,
* creates a RotatingFileHandler (max 5 MB × 3 backups),
* installs it on the root logger (idempotent — re-calling only updates level),
* saves/loads the log-level preference from a plain JSON app-config file.

Call ``configure_logging()`` once early in application start-up.
"""
from __future__ import annotations

import json
import logging
import os
import sys
import threading
from logging.handlers import RotatingFileHandler
from pathlib import Path
from typing import Optional

_APP_NAME = "AgileForecasting"
_CONFIG_APP_NAME = "agileforecasting"
_LOG_FILENAME = "agileforecasting.log"

# Ordered list for the UI dropdown (most → least severe).
LOG_LEVEL_OPTIONS: list[str] = ["FATAL", "ERROR", "WARNING", "INFO", "DEBUG"]

_LEVEL_MAP: dict[str, int] = {
    "FATAL": logging.CRITICAL,
    "ERROR": logging.ERROR,
    "WARNING": logging.WARNING,
    "INFO": logging.INFO,
    "DEBUG": logging.DEBUG,
}

_HANDLER_INSTALLED_ATTR = "_agileforecasting_file_handler_installed"
_CONFIGURE_LOCK = threading.Lock()


# ---------------------------------------------------------------------------
# Path resolution
# ---------------------------------------------------------------------------


def resolve_log_dir() -> Path:
    """Return the platform-appropriate per-user log directory."""
    if sys.platform == "win32":
        base = os.environ.get("LOCALAPPDATA") or str(Path.home() / "AppData" / "Local")
        return Path(base) / _APP_NAME / "logs"
    if sys.platform == "darwin":
        return Path.home() / "Library" / "Logs" / _APP_NAME
    # Linux / other — honour XDG_STATE_HOME if set.
    xdg = os.environ.get("XDG_STATE_HOME", "").strip()
    base = Path(xdg) if xdg else Path.home() / ".local" / "state"
    return base / _APP_NAME / "logs"


def resolve_log_path() -> Path:
    return resolve_log_dir() / _LOG_FILENAME


def _app_config_path() -> Path:
    """Plain (unencrypted) app config file — log level is not sensitive."""
    if sys.platform == "win32":
        base = os.environ.get("LOCALAPPDATA") or str(Path.home() / "AppData" / "Local")
        return Path(base) / _CONFIG_APP_NAME / "app_config.json"
    return Path.home() / ".config" / _CONFIG_APP_NAME / "app_config.json"


# ---------------------------------------------------------------------------
# Config persistence
# ---------------------------------------------------------------------------


def load_log_level() -> str:
    """Load the saved log level from disk. Returns ``'FATAL'`` if not set."""
    try:
        p = _app_config_path()
        if p.exists():
            data = json.loads(p.read_text(encoding="utf-8"))
            level = str(data.get("log_level", "FATAL")).upper()
            if level in _LEVEL_MAP:
                return level
    except Exception:
        pass
    return "FATAL"


def save_log_level(level_name: str) -> None:
    """Persist the log level to the plain app config file (best-effort)."""
    level_name = level_name.upper()
    if level_name not in _LEVEL_MAP:
        level_name = "FATAL"
    try:
        p = _app_config_path()
        p.parent.mkdir(parents=True, exist_ok=True)
        existing: dict = {}
        if p.exists():
            try:
                existing = json.loads(p.read_text(encoding="utf-8"))
            except Exception:
                pass
        existing["log_level"] = level_name
        p.write_text(json.dumps(existing, indent=2), encoding="utf-8")
    except Exception:
        pass


# ---------------------------------------------------------------------------
# Logging setup
# ---------------------------------------------------------------------------


def configure_logging(level_name: Optional[str] = None) -> Path:
    """Set up (or update) the rotating file handler on the root logger.

    Safe to call multiple times — the file handler is installed only once;
    subsequent calls update the effective log level without adding more handlers.

    Args:
        level_name: One of ``FATAL`` / ``ERROR`` / ``WARNING`` / ``INFO`` /
                    ``DEBUG``.  If *None*, the persisted config value is used
                    (default ``FATAL``).

    Returns:
        The resolved log file :class:`~pathlib.Path`.
    """
    if level_name is None:
        level_name = load_log_level()
    level_name = (level_name or "FATAL").upper()
    if level_name not in _LEVEL_MAP:
        level_name = "FATAL"
    level = _LEVEL_MAP[level_name]

    log_path = resolve_log_path()
    root = logging.getLogger()

    with _CONFIGURE_LOCK:
        if getattr(root, _HANDLER_INSTALLED_ATTR, False):
            # Handler already installed — only update the level.
            root.setLevel(level)
            for h in root.handlers:
                if isinstance(h, RotatingFileHandler):
                    h.setLevel(level)
            return log_path

        try:
            log_path.parent.mkdir(parents=True, exist_ok=True)
            handler = RotatingFileHandler(
                log_path,
                maxBytes=5 * 1024 * 1024,
                backupCount=3,
                encoding="utf-8",
            )
            handler.setLevel(level)
            handler.setFormatter(
                logging.Formatter(
                    fmt="%(asctime)s %(levelname)-8s %(name)s %(funcName)s:%(lineno)d  %(message)s",
                    datefmt="%Y-%m-%dT%H:%M:%S",
                )
            )
            root.setLevel(level)
            root.addHandler(handler)
            setattr(root, _HANDLER_INSTALLED_ATTR, True)

            # Capture unhandled exceptions to the log file.
            _orig_excepthook = sys.excepthook

            def _excepthook(exc_type, exc_value, exc_tb):
                if issubclass(exc_type, KeyboardInterrupt):
                    _orig_excepthook(exc_type, exc_value, exc_tb)
                    return
                logging.getLogger("agile_mc").critical(
                    "Unhandled exception", exc_info=(exc_type, exc_value, exc_tb)
                )

            sys.excepthook = _excepthook

        except Exception as setup_err:
            # File logging unavailable — fall back to stderr so diagnostics
            # are still visible in the terminal.
            logging.basicConfig(
                level=level,
                format="%(asctime)s %(levelname)-8s %(name)s %(funcName)s:%(lineno)d  %(message)s",
            )
            logging.getLogger("agile_mc").warning(
                "Could not set up file logging (%s); falling back to stderr.", setup_err
            )

    return log_path
