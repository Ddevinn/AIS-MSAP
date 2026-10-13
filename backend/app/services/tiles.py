from __future__ import annotations

import math
from functools import lru_cache
from typing import Any

from mapbox_vector_tile import encode
from shapely.geometry import LineString

from backend.app.services.analysis import require_dataset
from backend.app.services.query import QueryFilters, connect, trajectory_column, trajectory_source, where_clause

WEB_MERCATOR_RADIUS = 6_378_137.0
WEB_MERCATOR_LIMIT = WEB_MERCATOR_RADIUS * math.pi
TRACK_LAYER_NAME = "tracks"
TILE_EXTENT = 4096
MIN_TRACK_ZOOM = 8


def tracks_mvt(
    z: int,
    x: int,
    y: int,
    filters: QueryFilters | None = None,
    max_points: int = 30_000,
) -> bytes:
    require_dataset()
    _validate_tile(z, x, y)
    if z < MIN_TRACK_ZOOM:
        return _empty_tile()

    filters = filters or QueryFilters()
    if _is_empty_filter_window(filters):
        return _empty_tile()

    key = _cache_key(z, x, y, filters, max_points)
    return _tracks_mvt_cached(*key)


def clear_tile_cache() -> None:
    _tracks_mvt_cached.cache_clear()


def tile_bbox_wgs84(z: int, x: int, y: int) -> tuple[float, float, float, float]:
    n = 2.0**z
    lon_min = x / n * 360.0 - 180.0
    lon_max = (x + 1) / n * 360.0 - 180.0
    lat_max = math.degrees(math.atan(math.sinh(math.pi * (1 - 2 * y / n))))
    lat_min = math.degrees(math.atan(math.sinh(math.pi * (1 - 2 * (y + 1) / n))))
    return lon_min, lat_min, lon_max, lat_max


def tile_bbox_mercator(z: int, x: int, y: int) -> tuple[float, float, float, float]:
    n = 2.0**z
    tile_size = (WEB_MERCATOR_LIMIT * 2) / n
    minx = -WEB_MERCATOR_LIMIT + x * tile_size
    maxx = minx + tile_size
    maxy = WEB_MERCATOR_LIMIT - y * tile_size
    miny = maxy - tile_size
    return minx, miny, maxx, maxy


@lru_cache(maxsize=512)
def _tracks_mvt_cached(
    z: int,
    x: int,
    y: int,
    start_ts: int | None,
    end_ts: int | None,
    lon_min: float | None,
    lon_max: float | None,
    lat_min: float | None,
    lat_max: float | None,
    ship_type: int | None,
    ship_type_min: int | None,
    ship_type_max: int | None,
    mmsi: int | None,
    speed_min: float | None,
    speed_max: float | None,
    spatial_mode: str | None,
    spatial_geometry: str | None,
    behavior: str | None,
    max_points: int,
) -> bytes:
    filters = QueryFilters(
        start_ts=start_ts,
        end_ts=end_ts,
        lon_min=lon_min,
        lon_max=lon_max,
        lat_min=lat_min,
        lat_max=lat_max,
        ship_type=ship_type,
        ship_type_min=ship_type_min,
        ship_type_max=ship_type_max,
        mmsi=mmsi,
        speed_min=speed_min,
        speed_max=speed_max,
        spatial_mode=spatial_mode,
        spatial_geometry=spatial_geometry,
        behavior=behavior,
    )
    if filters.spatial_mode == "section" or filters.behavior:
        source, source_params = trajectory_source(filters)
        lon_min, lat_min, lon_max, lat_max = tile_bbox_wgs84(z, x, y)
        tile_filters = QueryFilters(lon_min=lon_min, lon_max=lon_max, lat_min=lat_min, lat_max=lat_max)
        where, tile_params = where_clause(tile_filters, include_spatial=False)
        params = [*source_params, *tile_params]
    else:
        merged = _merge_filters_with_tile_bbox(filters, z, x, y)
        if _is_empty_filter_window(merged):
            return _empty_tile()
        source, params = trajectory_source(merged)
        where = ""
    max_points = max(2_000, min(int(max_points), 100_000))
    base_step = _zoom_step(z)
    track_col = trajectory_column()

    with connect() as con:
        raw_count = con.execute(
            f"SELECT count(*)::UBIGINT FROM {source} {where}",
            params,
        ).fetchone()[0]
        if raw_count < 2:
            return _empty_tile()
        # 按段单独抽稀：先统计每段点数，再对每段限制最多 per_seg_max 个点
        # 这样可以避免全局步长过大导致每段只保留 2 个点的蜘蛛网问题
        seg_count = con.execute(
            f"SELECT count(DISTINCT {track_col})::UBIGINT FROM {source} {where}",
            params,
        ).fetchone()[0]
        if seg_count == 0:
            return _empty_tile()
        per_seg_max = max(8, max_points // max(seg_count, 1))
        step = max(base_step, math.ceil(raw_count / max_points))
        rows = con.execute(
            f"""
            WITH filtered AS (
                SELECT {track_col} AS trajectory_id, mmsi, timestamp, lon, lat, speed, course, ship_type
                FROM {source}
                {where}
            ),
            seg_counts AS (
                SELECT trajectory_id, count(*) AS seg_pts
                FROM filtered
                GROUP BY trajectory_id
            ),
            numbered AS (
                SELECT f.*,
                       row_number() OVER (PARTITION BY f.trajectory_id ORDER BY f.timestamp) AS rn,
                       s.seg_pts
                FROM filtered f
                JOIN seg_counts s ON f.trajectory_id = s.trajectory_id
            )
            SELECT trajectory_id, mmsi, timestamp, lon, lat, speed, course, ship_type
            FROM numbered
            WHERE ((rn - 1) % greatest(1, seg_pts / {per_seg_max})) = 0
            ORDER BY trajectory_id, timestamp
            """,
            params,
        ).fetchall()

    features = _rows_to_features(rows, z, raw_count, per_seg_max)
    if not features:
        return _empty_tile()

    return encode(
        [{"name": TRACK_LAYER_NAME, "features": features}],
        default_options={
            "quantize_bounds": tile_bbox_mercator(z, x, y),
            "extents": TILE_EXTENT,
        },
    )


def _rows_to_features(rows: list[tuple[Any, ...]], z: int, raw_count: int, step: int) -> list[dict[str, Any]]:
    features: list[dict[str, Any]] = []
    current_key: tuple[int, int] | None = None
    current_points: list[tuple[float, float]] = []
    current_meta: dict[str, Any] = {}
    tolerance = _simplify_tolerance_meters(z)

    def flush() -> None:
        nonlocal current_points, current_meta
        if len(current_points) < 2:
            current_points = []
            return
        line = LineString(current_points)
        if tolerance > 0:
            line = line.simplify(tolerance, preserve_topology=False)
        if line.is_empty or len(line.coords) < 2:
            current_points = []
            return
        features.append(
            {
                "geometry": line,
                "properties": {
                    **current_meta,
                    "raw_points": int(raw_count),
                    "step": int(step),
                    "vertices": int(len(line.coords)),
                },
            }
        )
        current_points = []

    for trajectory_id, mmsi, timestamp, lon, lat, speed, course, ship_type in rows:
        key = (int(trajectory_id), int(mmsi))
        if current_key is not None and key != current_key:
            flush()
        if key != current_key:
            current_key = key
            current_meta = {
                "trajectory_id": int(trajectory_id),
                "mmsi": int(mmsi),
                "ship_type": int(ship_type),
            }
        current_points.append(lonlat_to_mercator(float(lon), float(lat)))
    flush()
    return features


def lonlat_to_mercator(lon: float, lat: float) -> tuple[float, float]:
    lat = max(min(lat, 85.05112878), -85.05112878)
    x = WEB_MERCATOR_RADIUS * math.radians(lon)
    y = WEB_MERCATOR_RADIUS * math.log(math.tan(math.pi / 4 + math.radians(lat) / 2))
    return x, y


def _zoom_step(z: int) -> int:
    if z <= 8:
        return 64
    if z <= 9:
        return 32
    if z <= 10:
        return 16
    if z <= 11:
        return 8
    if z <= 12:
        return 4
    if z <= 13:
        return 2
    return 1


def _simplify_tolerance_meters(z: int) -> float:
    tile_width = (WEB_MERCATOR_LIMIT * 2) / (2.0**z)
    pixel_width = tile_width / TILE_EXTENT
    if z <= 9:
        return pixel_width * 8
    if z <= 11:
        return pixel_width * 4
    if z <= 13:
        return pixel_width * 2
    return pixel_width * 0.5


def _merge_filters_with_tile_bbox(filters: QueryFilters | None, z: int, x: int, y: int) -> QueryFilters:
    lon_min, lat_min, lon_max, lat_max = tile_bbox_wgs84(z, x, y)
    lon_pad = (lon_max - lon_min) * 0.08
    lat_pad = (lat_max - lat_min) * 0.08
    lon_min -= lon_pad
    lon_max += lon_pad
    lat_min -= lat_pad
    lat_max += lat_pad

    if filters is None:
        filters = QueryFilters()

    return QueryFilters(
        start_ts=filters.start_ts,
        end_ts=filters.end_ts,
        lon_min=max_value(filters.lon_min, lon_min),
        lon_max=min_value(filters.lon_max, lon_max),
        lat_min=max_value(filters.lat_min, lat_min),
        lat_max=min_value(filters.lat_max, lat_max),
        ship_type=filters.ship_type,
        ship_type_min=filters.ship_type_min,
        ship_type_max=filters.ship_type_max,
        mmsi=filters.mmsi,
        speed_min=filters.speed_min,
        speed_max=filters.speed_max,
        spatial_mode=filters.spatial_mode,
        spatial_geometry=filters.spatial_geometry,
        behavior=filters.behavior,
    )


def max_value(left: float | None, right: float) -> float:
    return max(left, right) if left is not None else right


def min_value(left: float | None, right: float) -> float:
    return min(left, right) if left is not None else right


def _is_empty_filter_window(filters: QueryFilters) -> bool:
    if filters.lon_min is not None and filters.lon_max is not None and filters.lon_min > filters.lon_max:
        return True
    if filters.lat_min is not None and filters.lat_max is not None and filters.lat_min > filters.lat_max:
        return True
    if filters.start_ts is not None and filters.end_ts is not None and filters.start_ts > filters.end_ts:
        return True
    return False


def _cache_key(
    z: int,
    x: int,
    y: int,
    filters: QueryFilters,
    max_points: int,
) -> tuple[Any, ...]:
    def rounded(value: float | None) -> float | None:
        return round(value, 8) if value is not None else None

    return (
        z,
        x,
        y,
        filters.start_ts,
        filters.end_ts,
        rounded(filters.lon_min),
        rounded(filters.lon_max),
        rounded(filters.lat_min),
        rounded(filters.lat_max),
        filters.ship_type,
        filters.ship_type_min,
        filters.ship_type_max,
        filters.mmsi,
        filters.speed_min,
        filters.speed_max,
        filters.spatial_mode,
        filters.spatial_geometry,
        filters.behavior,
        max_points,
    )


def _validate_tile(z: int, x: int, y: int) -> None:
    if z < 0 or z > 22:
        raise ValueError("z must be between 0 and 22")
    limit = 2**z
    if x < 0 or x >= limit or y < 0 or y >= limit:
        raise ValueError("x/y are outside the z tile range")


def _empty_tile() -> bytes:
    return encode([{"name": TRACK_LAYER_NAME, "features": []}])
