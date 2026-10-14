from __future__ import annotations

import math
from dataclasses import replace
from typing import Any

from backend.app.services.analysis import require_dataset
from backend.app.services.dataset import read_active_manifest
from backend.app.services.query import (
    QueryFilters, behavior_condition, behavior_feature_source, connect, trajectory_source,
)

EARTH_RADIUS = 6_378_137.0
MAX_LATITUDE = 85.05112878
MAX_CELLS = 20_000


def density_features(cell_size_m: int = 250, filters: QueryFilters | None = None) -> dict[str, Any]:
    """Count AIS observations in stable geographic cells, without point sampling.

    The ground size is approximate at the dataset's reference latitude. Grid
    origins and reference latitude stay fixed when filters or map zoom change.
    Section filters use the selected tracks, as on the trajectory map. A behavior
    filter counts matching observations, not the extra segment endpoint.
    """
    require_dataset()
    if not 50 <= cell_size_m <= 20_000:
        raise ValueError("cell_size_m must be between 50 and 20000")
    manifest = read_active_manifest() or {}
    bounds = manifest.get("bounds") or {}
    reference_latitude = (float(bounds.get("lat_min") or 0) + float(bounds.get("lat_max") or 0)) / 2
    reference_latitude = max(-MAX_LATITUDE, min(MAX_LATITUDE, reference_latitude))
    scale = max(0.08, math.cos(math.radians(reference_latitude)))
    base_filters = replace(filters, behavior=None) if filters else None
    source, params = trajectory_source(base_filters)
    if filters and filters.behavior:
        source = f"(SELECT * FROM ({behavior_feature_source(source)}) AS features WHERE {behavior_condition(filters.behavior)}) AS matched"

    # Validate before projection; in particular, poles must not reach ln(tan()).
    projected = f"""
        WITH valid AS (
            SELECT lon, lat, mmsi FROM {source}
            WHERE isfinite(lon) AND isfinite(lat)
              AND lon BETWEEN -180 AND 180
              AND lat BETWEEN -{MAX_LATITUDE} AND {MAX_LATITUDE}
        ), projected AS (
            SELECT {EARTH_RADIUS} * radians(lon) AS px,
                   {EARTH_RADIUS} * ln(tan(pi() / 4 + radians(lat) / 2)) AS py,
                   mmsi
            FROM valid
        )
    """
    actual_size = cell_size_m
    with connect() as con:
        # Coarsen the whole grid if necessary instead of dropping less busy cells.
        while True:
            projected_size = actual_size / scale
            rows = con.execute(projected + f"""
                SELECT floor(px / {projected_size})::BIGINT AS x,
                       floor(py / {projected_size})::BIGINT AS y,
                       count(*)::BIGINT AS point_count,
                       count(DISTINCT mmsi)::BIGINT AS vessel_count
                FROM projected
                GROUP BY x, y ORDER BY x, y LIMIT {MAX_CELLS + 1}
            """, params).fetchall()
            if len(rows) <= MAX_CELLS:
                break
            actual_size *= 2

    def longitude(x: float) -> float:
        return max(-180.0, min(180.0, math.degrees(x / EARTH_RADIUS)))

    def latitude(y: float) -> float:
        return max(-MAX_LATITUDE, min(MAX_LATITUDE, math.degrees(math.atan(math.sinh(y / EARTH_RADIUS)))))

    features = []
    for x, y, count, vessels in rows:
        west, east = longitude(x * projected_size), longitude((x + 1) * projected_size)
        south, north = latitude(y * projected_size), latitude((y + 1) * projected_size)
        features.append({
            "type": "Feature", "id": f"{x}:{y}",
            "properties": {
                "point_count": int(count), "vessel_count": int(vessels),
                "center_lon": (west + east) / 2, "center_lat": (south + north) / 2,
            },
            "geometry": {"type": "Polygon", "coordinates": [
                [[west, south], [east, south], [east, north], [west, north], [west, south]]
            ]},
        })
    return {
        "type": "FeatureCollection", "features": features,
        "metadata": {
            "point_count": sum(int(row[2]) for row in rows),
            "cell_count": len(rows), "max_count": max((int(row[2]) for row in rows), default=0),
            "requested_cell_size_m": cell_size_m, "cell_size_m": actual_size,
            "coarsened": actual_size != cell_size_m,
            "reference_latitude": reference_latitude,
            "spatial_scope": "selected_tracks" if filters and filters.spatial_mode == "section" else "points",
            "behavior": filters.behavior if filters else None,
        },
    }
