import os
from pathlib import Path

PROJECT_ROOT = Path(__file__).parent


def patch_app(path: Path):
    text = path.read_text()

    if "Project vs BAU Chart" in text:
        print("✅ Already patched")
        return

    if "st.plotly_chart" not in text:
        print("⚠️ Could not find chart rendering")
        return

    new_chart = """
# --- Project vs BAU Chart ---
import plotly.graph_objects as go
import numpy as np

bins = np.arange(min(samples), max(samples) + 2) - 0.5

proj_hist, _ = np.histogram(project_samples, bins=bins, density=True)
bau_hist, _ = np.histogram(non_project_samples, bins=bins, density=True)

x = (bins[:-1] + bins[1:]) / 2

fig = go.Figure()

fig.add_bar(x=x, y=proj_hist, name="Project")
fig.add_bar(x=x, y=bau_hist, name="BAU")

fig.update_layout(
    barmode="stack",
    title="How many items (Project vs BAU)",
    xaxis_title="Items completed",
    yaxis_title="Probability",
)

st.plotly_chart(fig, use_container_width=True)
"""

    # Replace ONLY first chart occurrence
    parts = text.split("st.plotly_chart", 1)

    if len(parts) == 2:
        text = parts[0] + new_chart + "\n# original chart disabled\n# st.plotly_chart" + parts[1]
        path.write_text(text)
        print("✅ Chart replaced safely")
    else:
        print("⚠️ Could not safely patch chart")


def run():
    for root, _, files in os.walk(PROJECT_ROOT):
        for f in files:
            if f == "app.py":
                patch_app(Path(root) / f)


if __name__ == "__main__":
    run()
