from __future__ import annotations

from io import BytesIO

import matplotlib

matplotlib.use("Agg")

import matplotlib.pyplot as plt
import numpy as np

from backend.app.services.analysis import density_grid
from backend.app.services.query import QueryFilters


def density_png(bins: int = 400, filters: QueryFilters | None = None) -> bytes:
    grid = density_grid(bins=bins, filters=filters)
    bounds = grid.get("bounds")
    cells = grid.get("cells", [])
    bin_count = int(grid.get("bins", bins))

    image = np.zeros((bin_count, bin_count), dtype=np.float64)
    for cell in cells:
        x = cell["x"]
        y = cell["y"]
        if 0 <= x < bin_count and 0 <= y < bin_count:
            image[y, x] = cell["count"]

    fig, ax = plt.subplots(figsize=(9, 7), dpi=140)
    if cells and isinstance(bounds, dict):
        values = np.log1p(image)
        extent = [bounds["lon_min"], bounds["lon_max"], bounds["lat_min"], bounds["lat_max"]]
        plot = ax.imshow(values, origin="lower", extent=extent, cmap="inferno", aspect="auto")
        fig.colorbar(plot, ax=ax, label="log(1 + points)")
        ax.set_xlim(bounds["lon_min"], bounds["lon_max"])
        ax.set_ylim(bounds["lat_min"], bounds["lat_max"])
    else:
        ax.text(0.5, 0.5, "No data", ha="center", va="center", transform=ax.transAxes)
    ax.set_xlabel("Longitude")
    ax.set_ylabel("Latitude")
    ax.set_title("AIS vessel activity density")
    ax.grid(color="#ffffff", linewidth=0.3, alpha=0.35)
    fig.tight_layout()

    buffer = BytesIO()
    fig.savefig(buffer, format="png")
    plt.close(fig)
    return buffer.getvalue()
