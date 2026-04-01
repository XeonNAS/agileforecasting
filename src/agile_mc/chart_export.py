from __future__ import annotations

import os
import shutil
from dataclasses import dataclass
from typing import Any, Optional

import plotly.graph_objects as go


@dataclass(frozen=True)
class ChartExportResult:
    filename: str
    mime: str
    data: bytes


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
    """Ensure a Chrome/Chromium binary is available for Kaleido image export."""
    existing = os.environ.get("BROWSER_PATH")
    if existing and os.path.exists(existing):
        return existing

    found = _find_chrome_on_path()
    if found:
        os.environ["BROWSER_PATH"] = found
        return found

    try:
        import plotly.io as pio

        chrome_path = str(pio.get_chrome())
        if chrome_path and os.path.exists(chrome_path):
            os.environ["BROWSER_PATH"] = chrome_path
            return chrome_path
    except Exception:
        return None

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

    export_fig, width, height, scale = _prepared_export_figure(fig, fmt)

    try:
        data = export_fig.to_image(format=fmt, width=width, height=height, scale=scale)
        return ChartExportResult(filename=filename, mime=mime, data=data)
    except Exception as e:
        if _looks_like_browser_failure(e):
            ensure_plotly_chrome()
            data = export_fig.to_image(format=fmt, width=width, height=height, scale=scale)
            return ChartExportResult(filename=filename, mime=mime, data=data)
        raise
