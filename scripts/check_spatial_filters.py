from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from backend.app.services.analysis import summary
from backend.app.services.query import QueryFilters, normalize_spatial_filter


def main() -> None:
    full = summary()
    lon_min, lon_max = float(full["lon_min"]), float(full["lon_max"])
    lat_min, lat_max = float(full["lat_min"]), float(full["lat_max"])
    left = lon_min + (lon_max - lon_min) * 0.3
    right = lon_min + (lon_max - lon_min) * 0.7
    bottom = lat_min + (lat_max - lat_min) * 0.3
    top = lat_min + (lat_max - lat_min) * 0.7

    mode, geometry = normalize_spatial_filter(
        "region",
        json.dumps([[left, bottom], [right, bottom], [right, top], [left, top]]),
    )
    region = summary(QueryFilters(spatial_mode=mode, spatial_geometry=geometry))
    assert 0 < region["point_count"] < full["point_count"]

    center = (left + right) / 2
    mode, geometry = normalize_spatial_filter(
        "section",
        json.dumps([[center, bottom], [center, top]]),
    )
    section = summary(QueryFilters(spatial_mode=mode, spatial_geometry=geometry))
    assert section["crossing_count"] > 0
    assert section["crossing_count"] == section["forward_crossings"] + section["reverse_crossings"]
    print(
        f"spatial filters ok: region={region['point_count']} points, "
        f"section={section['crossing_count']} crossings"
    )


if __name__ == "__main__":
    main()
