from __future__ import annotations

import json
import math
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import duckdb

from backend.app.services.dataset import active_parquet_path, read_active_manifest


@dataclass
class QueryFilters:
    start_ts: int | None = None
    end_ts: int | None = None
    lon_min: float | None = None
    lon_max: float | None = None
    lat_min: float | None = None
    lat_max: float | None = None
    ship_type: int | None = None
    ship_type_min: int | None = None
    ship_type_max: int | None = None
    mmsi: int | None = None
    speed_min: float | None = None
    speed_max: float | None = None
    spatial_mode: str | None = None
    spatial_geometry: str | None = None
    behavior: str | None = None


BEHAVIORS = {"berthing", "anchoring", "turning", "straight", "speed_change"}


def connect() -> duckdb.DuckDBPyConnection:
    con = duckdb.connect(database=":memory:")
    con.execute("PRAGMA threads=4")
    con.execute("PRAGMA disable_progress_bar")
    return con


def points_sql(path: Path | None = None) -> str:
    if path is None:
        path = active_parquet_path()
    escaped = str(path).replace("\\", "/").replace("'", "''")
    return f"read_parquet('{escaped}')"


def trajectory_column() -> str:
    manifest = read_active_manifest()
    columns = manifest.get("columns", []) if manifest else []
    return "clean_segment_id" if "clean_segment_id" in columns else "trajectory_id"


def where_clause(filters: QueryFilters | None, include_spatial: bool = True) -> tuple[str, list[Any]]:
    if filters is None:
        return "", []

    clauses: list[str] = []
    params: list[Any] = []
    if filters.start_ts is not None:
        clauses.append("timestamp >= ?")
        params.append(filters.start_ts)
    if filters.end_ts is not None:
        clauses.append("timestamp <= ?")
        params.append(filters.end_ts)
    if filters.lon_min is not None:
        clauses.append("lon >= ?")
        params.append(filters.lon_min)
    if filters.lon_max is not None:
        clauses.append("lon <= ?")
        params.append(filters.lon_max)
    if filters.lat_min is not None:
        clauses.append("lat >= ?")
        params.append(filters.lat_min)
    if filters.lat_max is not None:
        clauses.append("lat <= ?")
        params.append(filters.lat_max)
    if filters.ship_type is not None:
        clauses.append("ship_type = ?")
        params.append(filters.ship_type)
    if filters.ship_type_min is not None and filters.ship_type_max is not None:
        clauses.append("ship_type BETWEEN ? AND ?")
        params.append(filters.ship_type_min)
        params.append(filters.ship_type_max)
    elif filters.ship_type_min is not None:
        clauses.append("ship_type >= ?")
        params.append(filters.ship_type_min)
    elif filters.ship_type_max is not None:
        clauses.append("ship_type <= ?")
        params.append(filters.ship_type_max)
    if filters.mmsi is not None:
        clauses.append("mmsi = ?")
        params.append(filters.mmsi)
    if filters.speed_min is not None:
        clauses.append("speed >= ?")
        params.append(filters.speed_min)
    if filters.speed_max is not None:
        clauses.append("speed <= ?")
        params.append(filters.speed_max)
    if include_spatial and filters.spatial_mode == "region" and filters.spatial_geometry:
        clauses.append(_point_in_polygon_sql(spatial_points(filters)))

    if not clauses:
        return "", params
    return "WHERE " + " AND ".join(clauses), params


def normalize_spatial_filter(mode: str | None, geometry: str | None) -> tuple[str | None, str | None]:
    if not mode and not geometry:
        return None, None
    if mode not in {"region", "section"}:
        raise ValueError("spatial_mode must be 'region' or 'section'")
    if not geometry:
        raise ValueError("spatial_geometry is required when spatial_mode is set")
    try:
        payload = json.loads(geometry)
    except json.JSONDecodeError as exc:
        raise ValueError("spatial_geometry must be valid JSON") from exc

    if isinstance(payload, dict):
        geometry_type = payload.get("type")
        coordinates = payload.get("coordinates")
        if geometry_type == "Polygon" and coordinates:
            payload = coordinates[0]
        elif geometry_type == "LineString":
            payload = coordinates
        else:
            raise ValueError("spatial_geometry must be a Polygon, LineString, or coordinate array")

    minimum = 3 if mode == "region" else 2
    if not isinstance(payload, list) or not minimum <= len(payload) <= 64:
        raise ValueError(f"{mode} geometry requires {minimum} to 64 coordinate pairs")

    points: list[list[float]] = []
    for point in payload:
        if not isinstance(point, (list, tuple)) or len(point) < 2:
            raise ValueError("each spatial coordinate must be [longitude, latitude]")
        lon, lat = float(point[0]), float(point[1])
        if not math.isfinite(lon) or not math.isfinite(lat) or not -180 <= lon <= 180 or not -90 <= lat <= 90:
            raise ValueError("spatial coordinates are outside valid longitude/latitude ranges")
        points.append([round(lon, 8), round(lat, 8)])

    if mode == "region" and points[0] == points[-1]:
        points.pop()
    if len({tuple(point) for point in points}) < minimum:
        raise ValueError(f"{mode} geometry does not contain enough distinct points")
    return mode, json.dumps(points, separators=(",", ":"))


def spatial_points(filters: QueryFilters) -> list[tuple[float, float]]:
    if not filters.spatial_geometry:
        return []
    return [(float(point[0]), float(point[1])) for point in json.loads(filters.spatial_geometry)]


def analysis_source(filters: QueryFilters | None) -> tuple[str, list[Any]]:
    """Return the exact table used by statistics and exports.

    Region mode returns points inside the polygon. Section mode returns only
    trajectory segments that actually cross the selected section.
    """
    if filters and filters.spatial_mode == "section" and filters.spatial_geometry:
        return _section_source(filters, matched_trajectories=False)
    where, params = where_clause(filters)
    return f"(SELECT * FROM {points_sql()} {where}) AS filtered_points", params


def trajectory_source(filters: QueryFilters | None) -> tuple[str, list[Any]]:
    """Return points to draw as tracks for the current filter.

    A section selects every point belonging to a crossing trajectory so the
    map shows the complete selected tracks, while statistics count crossings.
    """
    if filters and filters.spatial_mode == "section" and filters.spatial_geometry:
        source, params = _section_source(filters, matched_trajectories=True)
    else:
        source, params = analysis_source(filters)
    if filters and filters.behavior:
        source = _behavior_segment_source(source, filters.behavior)
    return source, params


def normalize_behavior(behavior: str | None) -> str | None:
    if behavior is None or behavior == "":
        return None
    if behavior not in BEHAVIORS:
        raise ValueError(f"behavior must be one of: {', '.join(sorted(BEHAVIORS))}")
    return behavior


def behavior_feature_source(source: str) -> str:
    columns = set((read_active_manifest() or {}).get("columns", []))
    if {"time_delta_seconds", "implied_speed_knots", "acceleration_knots_per_min", "turn_angle_deg"} <= columns:
        return f"""
            SELECT *,
                   lag(timestamp) OVER (PARTITION BY {trajectory_column()} ORDER BY timestamp) AS behavior_prev_timestamp
            FROM {source}
        """
    track_col = trajectory_column()
    return f"""
        WITH ordered AS (
            SELECT *,
                   lag(timestamp) OVER (PARTITION BY {track_col} ORDER BY timestamp) AS behavior_prev_timestamp,
                   lag(lon) OVER (PARTITION BY {track_col} ORDER BY timestamp) AS prev_lon,
                   lag(lat) OVER (PARTITION BY {track_col} ORDER BY timestamp) AS prev_lat,
                   lag(speed) OVER (PARTITION BY {track_col} ORDER BY timestamp) AS prev_speed,
                   lag(course) OVER (PARTITION BY {track_col} ORDER BY timestamp) AS prev_course
            FROM {source}
        )
        SELECT *,
               timestamp - behavior_prev_timestamp AS time_delta_seconds,
               CASE WHEN timestamp > behavior_prev_timestamp THEN
                   2 * 3440.065 * asin(sqrt(least(1.0,
                       sin(radians(lat - prev_lat) / 2) ^ 2 +
                       cos(radians(prev_lat)) * cos(radians(lat)) *
                       sin(radians(lon - prev_lon) / 2) ^ 2
                   ))) / ((timestamp - behavior_prev_timestamp) / 3600.0)
               END AS implied_speed_knots,
               CASE WHEN timestamp > behavior_prev_timestamp THEN
                   abs(speed - prev_speed) / ((timestamp - behavior_prev_timestamp) / 60.0)
               END AS acceleration_knots_per_min,
               CASE WHEN prev_course IS NOT NULL THEN
                   abs(((course - prev_course + 540) % 360) - 180)
               END AS turn_angle_deg
        FROM ordered
    """


def behavior_condition(behavior: str) -> str:
    conditions = {
        "berthing": "speed <= 0.5 AND coalesce(implied_speed_knots, 0) <= 0.2",
        "anchoring": "speed <= 0.5 AND implied_speed_knots > 0.2",
        "turning": "speed > 0.5 AND time_delta_seconds BETWEEN 1 AND 600 AND turn_angle_deg >= 15",
        "straight": "speed > 0.5 AND time_delta_seconds BETWEEN 1 AND 600 AND turn_angle_deg <= 5 AND coalesce(acceleration_knots_per_min, 0) < 0.5",
        "speed_change": "speed > 0.5 AND time_delta_seconds BETWEEN 1 AND 600 AND acceleration_knots_per_min >= 0.5",
    }
    return conditions[normalize_behavior(behavior)]


def _behavior_segment_source(source: str, behavior: str) -> str:
    track_col = trajectory_column()
    features = behavior_feature_source("base")
    condition = behavior_condition(behavior)
    return f"""
        (
            WITH base AS MATERIALIZED (SELECT * FROM {source}),
            features AS MATERIALIZED ({features}),
            matched AS (
                SELECT mmsi, {track_col}, timestamp, behavior_prev_timestamp
                FROM features
                WHERE {condition}
            )
            SELECT DISTINCT base.*
            FROM base
            JOIN matched
              ON base.mmsi = matched.mmsi
             AND base.{track_col} = matched.{track_col}
             AND base.timestamp IN (matched.behavior_prev_timestamp, matched.timestamp)
        ) AS behavior_segments
    """


def _section_source(filters: QueryFilters, matched_trajectories: bool) -> tuple[str, list[Any]]:
    base_where, params = where_clause(filters, include_spatial=False)
    track_col = trajectory_column()
    points = spatial_points(filters)
    conditions = [_segment_intersection_sql(start, end) for start, end in zip(points, points[1:])]
    intersection = "(" + " OR ".join(conditions) + ")"
    direction = _section_direction_sql(points[0], points[-1])

    if matched_trajectories:
        sql = f"""
        (
            WITH base AS MATERIALIZED (
                SELECT * FROM {points_sql()} {base_where}
            ),
            sequenced AS (
                SELECT *,
                       lag(lon) OVER (PARTITION BY {track_col} ORDER BY timestamp) AS prev_lon,
                       lag(lat) OVER (PARTITION BY {track_col} ORDER BY timestamp) AS prev_lat
                FROM base
            ),
            matched AS (
                SELECT DISTINCT {track_col}
                FROM sequenced
                WHERE prev_lon IS NOT NULL AND prev_lat IS NOT NULL AND {intersection}
            )
            SELECT base.*
            FROM base
            JOIN matched USING ({track_col})
        ) AS filtered_tracks
        """
    else:
        sql = f"""
        (
            WITH base AS (
                SELECT * FROM {points_sql()} {base_where}
            ),
            sequenced AS (
                SELECT *,
                       lag(lon) OVER (PARTITION BY {track_col} ORDER BY timestamp) AS prev_lon,
                       lag(lat) OVER (PARTITION BY {track_col} ORDER BY timestamp) AS prev_lat
                FROM base
            )
            SELECT *, {direction} AS crossing_direction
            FROM sequenced
            WHERE prev_lon IS NOT NULL AND prev_lat IS NOT NULL AND {intersection}
        ) AS section_crossings
        """
    return sql, params


def _point_in_polygon_sql(points: list[tuple[float, float]]) -> str:
    lon_values = [point[0] for point in points]
    lat_values = [point[1] for point in points]
    crossings: list[str] = []
    for current, previous in zip(points, [points[-1], *points[:-1]]):
        x1, y1 = current
        x2, y2 = previous
        crossings.append(
            f"CASE WHEN (({_num(y1)} > lat) <> ({_num(y2)} > lat)) "
            f"AND lon < (({_num(x2)} - {_num(x1)}) * (lat - {_num(y1)}) "
            f"/ NULLIF({_num(y2)} - {_num(y1)}, 0) + {_num(x1)}) THEN 1 ELSE 0 END"
        )
    parity = " + ".join(crossings)
    return (
        f"lon BETWEEN {_num(min(lon_values))} AND {_num(max(lon_values))} "
        f"AND lat BETWEEN {_num(min(lat_values))} AND {_num(max(lat_values))} "
        f"AND (({parity}) % 2 = 1)"
    )


def _segment_intersection_sql(start: tuple[float, float], end: tuple[float, float]) -> str:
    x1, y1 = start
    x2, y2 = end
    first_a = f"((lon - prev_lon) * ({_num(y1)} - prev_lat) - (lat - prev_lat) * ({_num(x1)} - prev_lon))"
    first_b = f"((lon - prev_lon) * ({_num(y2)} - prev_lat) - (lat - prev_lat) * ({_num(x2)} - prev_lon))"
    second_a = f"(({_num(x2 - x1)}) * (prev_lat - {_num(y1)}) - ({_num(y2 - y1)}) * (prev_lon - {_num(x1)}))"
    second_b = f"(({_num(x2 - x1)}) * (lat - {_num(y1)}) - ({_num(y2 - y1)}) * (lon - {_num(x1)}))"
    return (
        f"greatest(least(prev_lon, lon), {_num(min(x1, x2))}) <= "
        f"least(greatest(prev_lon, lon), {_num(max(x1, x2))}) AND "
        f"greatest(least(prev_lat, lat), {_num(min(y1, y2))}) <= "
        f"least(greatest(prev_lat, lat), {_num(max(y1, y2))}) AND "
        f"({first_a} * {first_b}) <= 1e-14 AND ({second_a} * {second_b}) <= 1e-14"
    )


def _section_direction_sql(start: tuple[float, float], end: tuple[float, float]) -> str:
    x1, y1 = start
    x2, y2 = end
    side_previous = f"(({_num(x2 - x1)}) * (prev_lat - {_num(y1)}) - ({_num(y2 - y1)}) * (prev_lon - {_num(x1)}))"
    side_current = f"(({_num(x2 - x1)}) * (lat - {_num(y1)}) - ({_num(y2 - y1)}) * (lon - {_num(x1)}))"
    return f"CASE WHEN {side_previous} <= 0 AND {side_current} > 0 THEN 1 ELSE -1 END"


def _num(value: float) -> str:
    return format(float(value), ".12g")
