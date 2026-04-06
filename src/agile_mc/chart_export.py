from __future__ import annotations

import logging
import os
import shutil
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


def _looks_like_browser_failure(exc: Exception) -> bool:
    name = exc.__class__.__name__
    msg = f"{exc}"
    blob = f"{name} {msg}".lower()
    return any(
        s in blob
        for s in (
            "browserfailederror",
            "the browser seemed to close",
            "choreographer",
            "kaleido",
            "chromium",
            "chrome",
            "browser seemed to close",
            "chromenotfounderror",
        )
    )


def _find_chrome_on_path() -> Optional[str]:
    for exe in (
        "google-chrome-stable",
        "google-chrome",
        "chrome",
        "chromium",
        "chromium-browser",
        "chrome-headless-shell",
    ):
        p = shutil.which(exe)
        if p:
            return p

    for p in ("/usr/bin/google-chrome-stable", "/usr/bin/google-chrome", "/usr/bin/chrome"):
        if os.path.exists(p):
            return p

    return None


def ensure_plotly_chrome() -> Optional[str]:
    """Ensure a Chrome/Chromium binary is available for Kaleido image export.

    Checks BROWSER_PATH env var, then PATH, then attempts to auto-download
    via kaleido's bundled downloader (kaleido v1+).
    """
    existing = os.environ.get("BROWSER_PATH")
    if existing and os.path.exists(existing):
        logger.debug("Chrome found via BROWSER_PATH: %s", existing)
        return existing

    found = _find_chrome_on_path()
    if found:
        os.environ["BROWSER_PATH"] = found
        logger.debug("Chrome found on PATH: %s", found)
        return found

    # kaleido v1+ ships its own downloader — try it before giving up.
    try:
        import kaleido

        logger.info("Chrome not found on PATH; attempting kaleido.get_chrome_sync() download…")
        path = str(kaleido.get_chrome_sync())
        logger.info("Chrome downloaded to: %s", path)
        return path
    except Exception as exc:
        logger.warning("kaleido.get_chrome_sync() failed: %s", exc)

    return None


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

    logger.info(
        "export_plotly_figure: fmt=%s filename=%s title=%r fig_type=%s "
        "width=%d height=%d scale=%d plotly=%s kaleido=%s",
        fmt,
        filename,
        chart_title,
        type(fig).__name__,
        width,
        height,
        scale,
        plotly.__version__,
        _kaleido_version(),
    )

    try:
        data = export_fig.to_image(format=fmt, width=width, height=height, scale=scale)
        logger.info("export_plotly_figure: success — %d bytes returned", len(data))
        return ChartExportResult(filename=filename, mime=mime, data=data)
    except Exception as e:
        if _looks_like_browser_failure(e):
            logger.warning(
                "export_plotly_figure: browser/Chrome failure (%s: %s); attempting Chrome auto-install",
                type(e).__name__,
                e,
            )
            found = ensure_plotly_chrome()
            if found:
                logger.info("export_plotly_figure: retrying with Chrome at %s", found)
                try:
                    data = export_fig.to_image(format=fmt, width=width, height=height, scale=scale)
                    logger.info("export_plotly_figure: retry success — %d bytes returned", len(data))
                    return ChartExportResult(filename=filename, mime=mime, data=data)
                except Exception as retry_exc:
                    logger.error(
                        "export_plotly_figure: retry failed — %s: %s\n%s",
                        type(retry_exc).__name__,
                        retry_exc,
                        traceback.format_exc(),
                    )
                    raise
            logger.error(
                "export_plotly_figure: Chrome not found and auto-install failed — %s: %s",
                type(e).__name__,
                e,
            )
            raise BrowserNotAvailableError(
                "Chart export requires Chrome or Chromium.\n"
                "Install it (e.g. 'sudo apt install chromium') and restart the app, "
                "or run 'plotly_get_chrome' to download a compatible version and set "
                "BROWSER_PATH to the executable path."
            ) from e
        logger.error(
            "export_plotly_figure: unexpected error — %s: %s\n%s",
            type(e).__name__,
            e,
            traceback.format_exc(),
        )
        raise
