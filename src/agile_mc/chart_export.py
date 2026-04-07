from __future__ import annotations

import logging
import os
import shutil
import sys
import traceback
from dataclasses import dataclass
from typing import Any, Optional

import plotly
import plotly.graph_objects as go

logger = logging.getLogger(__name__)


class BrowserNotAvailableError(RuntimeError):
    """Raised when image export fails because no Chrome/Chromium browser is installed."""


@dataclass(frozen=True)
class ChartExportResult:
    filename: str
    mime: str
    data: bytes


def _kaleido_version() -> str:
    try:
        import kaleido

        return getattr(kaleido, "__version__", "unknown (kaleido installed)")
    except ImportError:
        return "not installed"


# ---------------------------------------------------------------------------
# Browser detection helpers
# ---------------------------------------------------------------------------


def _is_snap_path(path: str) -> bool:
    """Return True if *path* resolves to a Snap-managed binary.

    Snap Chromium can fail to start when choreographer places the
    temporary ``--user-data-dir`` in a hidden directory under ``$HOME``
    (the "sneak" mode). The ``SingletonLock: Permission denied`` error
    in the logs is the typical symptom.
    """
    if not path:
        return False
    try:
        real = os.path.realpath(path)
        return "/snap/" in real
    except Exception:
        return "/snap/" in path


def _find_non_snap_chrome() -> Optional[str]:
    """Search PATH for Chrome/Chromium, skipping any Snap-managed binary.

    Returns the first usable non-Snap path found, or *None*.
    Logs a DEBUG line for each candidate explaining the accept/reject decision.
    """
    exe_names = (
        "google-chrome-stable",
        "google-chrome",
        "chrome",
        "chromium",
        "chromium-browser",
        "chrome-headless-shell",
    )
    for exe in exe_names:
        p = shutil.which(exe)
        if not p:
            logger.debug("browser candidate %-28s not found on PATH", exe)
            continue
        if _is_snap_path(p):
            logger.debug("browser candidate %-28s -> %s  [SKIP — Snap binary]", exe, p)
            continue
        logger.debug("browser candidate %-28s -> %s  [OK]", exe, p)
        return p

    # Fixed-path fallback (covers installations not on PATH)
    if sys.platform == "win32":
        # Windows: check common per-machine and per-user Chrome/Chromium locations.
        local_app_data = os.environ.get("LOCALAPPDATA", "")
        win_candidates: list[str] = []
        if local_app_data:
            # Per-user install (most common on Windows)
            win_candidates.append(os.path.join(local_app_data, "Google", "Chrome", "Application", "chrome.exe"))
        win_candidates += [
            r"C:\Program Files\Google\Chrome\Application\chrome.exe",
            r"C:\Program Files (x86)\Google\Chrome\Application\chrome.exe",
            r"C:\Program Files\Chromium\Application\chrome.exe",
            r"C:\Program Files (x86)\Chromium\Application\chrome.exe",
        ]
        for fixed in win_candidates:
            if os.path.exists(fixed):
                logger.debug("browser candidate %-28s  [OK — Windows fixed path]", fixed)
                return fixed
    else:
        for fixed in (
            "/usr/bin/google-chrome-stable",
            "/usr/bin/google-chrome",
            "/usr/bin/chrome",
            "/usr/bin/chromium",
            "/usr/bin/chromium-browser",
        ):
            if os.path.exists(fixed) and not _is_snap_path(fixed):
                logger.debug("browser candidate %-28s  [OK — fixed path]", fixed)
                return fixed

    return None


def _kaleido_bundled_chrome() -> Optional[str]:
    """Return the path to the choreographer-managed Chrome, downloading it if needed.

    This is a guaranteed non-Snap binary installed inside the Python
    virtual-environment directory.
    """
    try:
        from choreographer.cli._cli_utils import get_chrome_download_path

        local = get_chrome_download_path()
        if local is not None and local.exists():
            logger.debug("Kaleido bundled Chrome already present: %s", local)
            return str(local)
    except Exception as exc:
        logger.debug("Could not check local bundled Chrome path: %s", exc)

    # Not present — download it.
    try:
        import kaleido

        logger.info("Kaleido bundled Chrome not found locally; calling kaleido.get_chrome_sync() to download…")
        path = str(kaleido.get_chrome_sync())
        if path and os.path.exists(path):
            logger.info("Kaleido bundled Chrome downloaded to: %s", path)
            return path
        logger.warning("kaleido.get_chrome_sync() returned %r but path not found", path)
    except Exception as exc:
        logger.warning("kaleido.get_chrome_sync() failed: %s", exc)

    return None


def ensure_plotly_chrome() -> Optional[str]:
    """Select and configure a working Chrome/Chromium for Kaleido image export.

    Priority order
    --------------
    1. ``BROWSER_PATH`` env var — accepted only when it is **not** a Snap binary.
       Snap Chromium is cleared from ``BROWSER_PATH`` with a warning because it
       can fail to start when choreographer uses a hidden home-directory temp path.
    2. First non-Snap Chrome/Chromium found on ``PATH``.
    3. Kaleido/Choreographer bundled Chrome (downloaded on demand via
       ``kaleido.get_chrome_sync()``).  This is always a non-Snap binary and is
       the most reliable option on systems where Snap Chromium is the only
       system-wide browser.
    4. Snap Chromium as a last resort — logged with a warning because it may
       still fail depending on the Snap confinement configuration.

    Sets ``BROWSER_PATH`` to the chosen binary so choreographer picks it up.
    Returns the chosen path, or ``None`` if nothing could be found.
    """
    # ------------------------------------------------------------------
    # 1. Existing BROWSER_PATH
    # ------------------------------------------------------------------
    existing = os.environ.get("BROWSER_PATH", "").strip()
    if existing:
        if not os.path.exists(existing):
            logger.warning("BROWSER_PATH=%s does not exist — ignoring", existing)
            del os.environ["BROWSER_PATH"]
        elif _is_snap_path(existing):
            logger.warning(
                "BROWSER_PATH=%s is a Snap binary — clearing it.  "
                "Snap Chromium fails with 'SingletonLock: Permission denied' "
                "when choreographer creates a hidden temp dir in $HOME.",
                existing,
            )
            del os.environ["BROWSER_PATH"]
        else:
            logger.info("Using browser from BROWSER_PATH: %s (non-Snap)", existing)
            return existing

    # ------------------------------------------------------------------
    # 2. Non-Snap browser on PATH
    # ------------------------------------------------------------------
    found = _find_non_snap_chrome()
    if found:
        os.environ["BROWSER_PATH"] = found
        logger.info("Selected non-Snap browser from PATH: %s", found)
        return found

    # ------------------------------------------------------------------
    # 3. Kaleido bundled Chrome (best option when PATH only has Snap)
    # ------------------------------------------------------------------
    bundled = _kaleido_bundled_chrome()
    if bundled:
        os.environ["BROWSER_PATH"] = bundled
        logger.info("Selected kaleido bundled Chrome: %s", bundled)
        return bundled

    # ------------------------------------------------------------------
    # 4. Snap as last resort
    # ------------------------------------------------------------------
    for snap_exe in ("chromium", "chromium-browser", "google-chrome"):
        snap_path = shutil.which(snap_exe)
        if snap_path and _is_snap_path(snap_path):
            logger.warning(
                "Last resort: using Snap browser %s.  "
                "This may fail with 'SingletonLock: Permission denied'.  "
                "For reliable exports install a non-Snap Chrome, e.g.:  "
                "sudo apt install --no-install-recommends chromium-browser  "
                "(apt package, not snap).",
                snap_path,
            )
            os.environ["BROWSER_PATH"] = snap_path
            return snap_path

    logger.error("No Chrome/Chromium browser found for image export.")
    return None


# ---------------------------------------------------------------------------
# Failure classification
# ---------------------------------------------------------------------------


def _looks_like_browser_failure(exc: Exception) -> bool:
    # Exact type check — most reliable
    try:
        from choreographer.errors import BrowserFailedError, ChromeNotFoundError

        if isinstance(exc, (BrowserFailedError, ChromeNotFoundError)):
            return True
    except ImportError:
        pass

    # String-based fallback for other kaleido / wrapper errors
    name = exc.__class__.__name__
    msg = str(exc)
    blob = f"{name} {msg}".lower()
    return any(
        s in blob
        for s in (
            "browserfailederror",
            "chromenotfounderror",
            "the browser seemed to close",
            "browser seemed to close",
            "choreographer",
            "kaleido",
            "chromium",
            "chrome",
            "singletonlock",
        )
    )


# ---------------------------------------------------------------------------
# Figure preparation
# ---------------------------------------------------------------------------


def _as_margin_dict(margin_obj: Any) -> dict[str, Any]:
    if margin_obj is None:
        return {}
    if isinstance(margin_obj, dict):
        return dict(margin_obj)
    if hasattr(margin_obj, "to_plotly_json"):
        try:
            data = margin_obj.to_plotly_json()
            if isinstance(data, dict):
                return dict(data)
        except Exception:
            pass

    out: dict[str, Any] = {}
    for key in ("l", "r", "t", "b", "pad", "autoexpand"):
        try:
            value = getattr(margin_obj, key)
        except Exception:
            continue
        if value is not None:
            out[key] = value
    return out


def _prepared_export_figure(fig: go.Figure, fmt: str) -> tuple[go.Figure, int, int, int]:
    export_fig = go.Figure(fig)

    current_margin = _as_margin_dict(getattr(export_fig.layout, "margin", None))
    width = 1600 if fmt == "png" else 1400
    height = 1000 if fmt == "png" else 900

    export_fig.update_layout(
        autosize=False,
        width=width,
        height=height,
        margin=dict(
            l=max(int(current_margin.get("l") or 0), 110),
            r=max(int(current_margin.get("r") or 0), 220),
            t=max(int(current_margin.get("t") or 0), 110),
            b=max(int(current_margin.get("b") or 0), 110),
            pad=max(int(current_margin.get("pad") or 0), 8),
        ),
        legend=dict(
            x=1.02,
            xanchor="left",
            y=1.0,
            yanchor="top",
            tracegroupgap=8,
        ),
    )

    export_fig.update_xaxes(automargin=True)
    export_fig.update_yaxes(automargin=True)

    scale = 2 if fmt == "png" else 1
    return export_fig, width, height, scale


# ---------------------------------------------------------------------------
# Main export entry point
# ---------------------------------------------------------------------------


def export_plotly_figure(fig: go.Figure, fmt: str, base_name: str) -> ChartExportResult:
    fmt = (fmt or "png").lower().strip()
    if fmt not in ("png", "svg"):
        fmt = "png"

    mime = "image/png" if fmt == "png" else "image/svg+xml"
    filename = f"{base_name}.{fmt}"

    chart_title = ""
    try:
        chart_title = str(fig.layout.title.text or "")
    except Exception:
        pass

    export_fig, width, height, scale = _prepared_export_figure(fig, fmt)

    # ----------------------------------------------------------------
    # Proactively resolve the browser BEFORE the first render attempt.
    # Without this, choreographer runs its own discovery, may pick
    # /snap/bin/chromium, and fails before we can intervene.
    # ----------------------------------------------------------------
    browser = ensure_plotly_chrome()
    browser_is_snap = _is_snap_path(browser or "")

    logger.info(
        "export_plotly_figure: fmt=%s filename=%s title=%r fig_type=%s "
        "width=%d height=%d scale=%d plotly=%s kaleido=%s "
        "browser=%s snap=%s",
        fmt,
        filename,
        chart_title,
        type(fig).__name__,
        width,
        height,
        scale,
        plotly.__version__,
        _kaleido_version(),
        browser or "none",
        browser_is_snap,
    )

    try:
        data = export_fig.to_image(format=fmt, width=width, height=height, scale=scale)
        logger.info(
            "export_plotly_figure: success — %d bytes  browser=%s",
            len(data),
            browser or "none",
        )
        return ChartExportResult(filename=filename, mime=mime, data=data)

    except Exception as first_exc:
        if not _looks_like_browser_failure(first_exc):
            # Not a browser problem — log and re-raise immediately.
            logger.error(
                "export_plotly_figure: non-browser error — %s: %s\n%s",
                type(first_exc).__name__,
                first_exc,
                traceback.format_exc(),
            )
            raise

        logger.warning(
            "export_plotly_figure: browser failure on first attempt (browser=%s snap=%s): %s: %s",
            browser or "none",
            browser_is_snap,
            type(first_exc).__name__,
            first_exc,
        )

        # ----------------------------------------------------------------
        # Retry with a genuinely different browser.
        # Clear BROWSER_PATH so ensure_plotly_chrome() is forced to
        # re-evaluate, but exclude the browser that just failed.
        # ----------------------------------------------------------------
        failed_browser = browser or ""
        os.environ.pop("BROWSER_PATH", None)

        retry_browser: Optional[str] = None

        # If we were using Snap (or something non-bundled), force the
        # kaleido bundled Chrome now regardless of PATH contents.
        bundled = _kaleido_bundled_chrome()
        if bundled and bundled != failed_browser:
            os.environ["BROWSER_PATH"] = bundled
            retry_browser = bundled
            logger.info(
                "export_plotly_figure: retry strategy — kaleido bundled Chrome: %s",
                retry_browser,
            )
        else:
            # No distinct fallback available.
            logger.error(
                "export_plotly_figure: no alternative browser available for retry "
                "(failed=%s bundled=%s).  Raising BrowserNotAvailableError.",
                failed_browser,
                bundled,
            )
            raise BrowserNotAvailableError(
                "Chart export requires Chrome or Chromium but no usable browser "
                "was found or the available browser failed to start.\n"
                "Recovery options (choose one):\n"
                "  • Set the BROWSER_PATH environment variable to point to an "
                "existing Chrome/Chromium binary and restart the app.\n"
                "  • Run  plotly_get_chrome  in the active venv to download a "
                "bundled browser, then set BROWSER_PATH to the path it prints.\n"
                "  Linux:          sudo apt install chromium  (apt package, not snap)\n"
                "  macOS/Windows:  install Chrome from https://www.google.com/chrome/"
            ) from first_exc

        try:
            data = export_fig.to_image(format=fmt, width=width, height=height, scale=scale)
            logger.info(
                "export_plotly_figure: retry success — %d bytes  browser=%s",
                len(data),
                retry_browser,
            )
            return ChartExportResult(filename=filename, mime=mime, data=data)

        except Exception as retry_exc:
            if _looks_like_browser_failure(retry_exc):
                logger.error(
                    "export_plotly_figure: retry also failed (browser=%s): %s: %s\n%s",
                    retry_browser,
                    type(retry_exc).__name__,
                    retry_exc,
                    traceback.format_exc(),
                )
                raise BrowserNotAvailableError(
                    "Chart export failed on both the initial attempt and the retry.\n"
                    "Set BROWSER_PATH to a working Chrome or Chromium binary and restart.\n"
                    "  Linux:          sudo apt install chromium  (apt package, not snap)\n"
                    "  macOS/Windows:  install Chrome from https://www.google.com/chrome/"
                ) from retry_exc
            logger.error(
                "export_plotly_figure: retry — unexpected error — %s: %s\n%s",
                type(retry_exc).__name__,
                retry_exc,
                traceback.format_exc(),
            )
            raise
