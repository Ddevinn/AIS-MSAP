from __future__ import annotations

from io import BytesIO

import matplotlib

matplotlib.use("Agg")

import matplotlib.pyplot as plt
import matplotlib.ticker as ticker
import numpy as np
from matplotlib.colors import LinearSegmentedColormap

from backend.app.services.analysis import density_grid
from backend.app.services.query import QueryFilters


def density_png(grid_res: float = 0.001, filters: QueryFilters | None = None) -> bytes:
    grid = density_grid(grid_res=grid_res, filters=filters)
    bounds = grid.get("bounds")
    cells = grid.get("cells", [])
    grid_size_x = int(grid.get("grid_size_x", 400))
    grid_size_y = int(grid.get("grid_size_y", 400))

    if not cells or not isinstance(bounds, dict):
        fig, ax = plt.subplots(figsize=(9, 7), dpi=140)
        ax.text(0.5, 0.5, "No data", ha="center", va="center", transform=ax.transAxes)
        buffer = BytesIO()
        fig.savefig(buffer, format="png")
        plt.close(fig)
        return buffer.getvalue()

    image = np.zeros((grid_size_y, grid_size_x), dtype=np.float64)
    for cell in cells:
        x, y = cell["x"], cell["y"]
        if 0 <= x < grid_size_x and 0 <= y < grid_size_y:
            image[y, x] = cell["count"]
    image[image == 0] = np.nan  # 零格 → 透明，底图可见

    lon_min = bounds["lon_min"]
    lon_max = bounds["lon_max"]
    lat_min = bounds["lat_min"]
    lat_max = bounds["lat_max"]

    # 图幅大小：固定宽度 9 英寸，高度按数据范围等比例计算
    width = 9.0
    height = max(3.0, width * (lat_max - lat_min) / (lon_max - lon_min))
    fig, ax = plt.subplots(figsize=(width, height), dpi=140)

    # Jet 色板（截取 30:230 去除极端蓝/红端，与参考代码一致）
    jet_colors = plt.cm.jet(np.linspace(0, 1, 256))
    cmap = LinearSegmentedColormap.from_list("jet_subset", jet_colors[30:230])
    cmap.set_bad(alpha=0.0)  # NaN → 完全透明

    extent = [lon_min, lon_max, lat_min, lat_max]
    valid = image[~np.isnan(image)]
    vmax = float(np.percentile(valid, 95)) if valid.size else 1.0

    im = ax.imshow(
        image,
        extent=extent,
        origin="lower",
        cmap=cmap,
        aspect="auto",
        vmin=0,
        vmax=vmax,
        alpha=0.82,
        zorder=10,
    )

    cbar = fig.colorbar(im, ax=ax, fraction=0.046, pad=0.04)
    cbar.set_label("轨迹密度", rotation=270, labelpad=20, fontsize=12)

    ax.set_xlabel("经度（°）", fontsize=12)
    ax.set_ylabel("纬度（°）", fontsize=12)
    ax.set_xlim(lon_min, lon_max)
    ax.set_ylim(lat_min, lat_max)
    ax.set_aspect("equal", adjustable="box")

    fmt = ticker.FormatStrFormatter("%.3f")
    ax.xaxis.set_major_formatter(fmt)
    ax.yaxis.set_major_formatter(fmt)
    ax.xaxis.set_major_locator(ticker.MaxNLocator(nbins=6))
    ax.yaxis.set_major_locator(ticker.MaxNLocator(nbins=6))

    # 添加 OSM 底图（需要网络；失败时静默跳过）
    try:
        import contextily as ctx
        ctx.add_basemap(
            ax,
            crs="EPSG:4326",
            source=ctx.providers.OpenStreetMap.Mapnik,
            attribution=False,
            zorder=0,
        )
    except Exception:
        pass

    ax.set_title("船舶活动密度图", fontsize=14, fontweight="bold")
    fig.tight_layout()

    buffer = BytesIO()
    fig.savefig(buffer, format="png", bbox_inches="tight", pad_inches=0)
    plt.close(fig)
    return buffer.getvalue()
