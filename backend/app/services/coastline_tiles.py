
"""Render the indexed WGS84 coastline as Web Mercator vector tiles."""
from __future__ import annotations

from functools import lru_cache
from pathlib import Path

import numpy as np
from mapbox_vector_tile import encode
from shapely import box, clip_by_rect, transform

from backend.app.services.coastline import Coastline
from backend.app.services.tiles import WEB_MERCATOR_RADIUS, _validate_tile, tile_bbox_mercator, tile_bbox_wgs84


def coastline_tile(z: int, x: int, y: int, source: tuple[Path, str]) -> bytes:
    _validate_tile(z, x, y)
    return _render(z, x, y, source)


@lru_cache(maxsize=256)
def _render(z: int, x: int, y: int, source: tuple[Path, str]) -> bytes:
    west, south, east, north = tile_bbox_wgs84(z, x, y)
    bounds = tile_bbox_mercator(z, x, y)
    # Clip with a buffer so strokes remain continuous across tile edges.
    dx, dy = (east - west) / 128, (north - south) / 128
    window = (west - dx, south - dy, east + dx, north + dy)
    tolerance = max(east - west, north - south) / 1024
    boundary = box(*window)
    geometries = []
    with Coastline(source) as coast:
        for line in coast.lines(*window):
            if not boundary.intersects(line):
                continue
            clipped = clip_by_rect(line, *window)
            if not clipped.is_empty and clipped.geom_type in {"LineString", "MultiLineString"}:
                geometries.append({"geometry": transform(clipped.simplify(tolerance), _mercator)})

    return encode(
        [{"name": "coastline", "features": geometries}],
        default_options={"quantize_bounds": bounds, "extents": 4096},
    )


def _mercator(coordinates):
    longitude, latitude = np.radians(coordinates).T
    max_latitude = np.arctan(np.sinh(np.pi))
    latitude = np.clip(latitude, -max_latitude, max_latitude)
    return WEB_MERCATOR_RADIUS * np.column_stack((longitude, np.arcsinh(np.tan(latitude))))
