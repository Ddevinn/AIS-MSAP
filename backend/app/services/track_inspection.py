from __future__ import annotations

from dataclasses import dataclass, replace
from math import cos, isfinite, radians

from backend.app.services.analysis import require_dataset
from backend.app.services.dataset import active_dataset_id, read_active_manifest
from backend.app.services.query import QueryFilters, connect, trajectory_column, trajectory_source
from backend.app.services.raster_tracks import TRACK_POINT_ORDER, track_gap_limits


MAX_VIEW_POINTS = 4_000
MAX_HIGHLIGHT_POINTS = 2_000


@dataclass(frozen=True)
class TrackAnchor:
    vessel_id: int
    track_id: int
    anchor_time: int
    anchor_lon: float
    anchor_lat: float
    data_version: str


def require_track_version(data_version: str) -> dict:
    require_dataset()
    manifest = read_active_manifest() or {}
    current = ":".join(map(str, (
        active_dataset_id() or "", manifest.get("data_role") or "source",
        manifest.get("created_at") or "", manifest.get("row_count") or 0,
    )))
    if data_version != current:
        raise RuntimeError("数据已更新，请重新选择轨迹。")
    return manifest


def _paired_points(source: str, track_col: str, columns: set[str], condition: str) -> str:
    source_delta = "time_delta_seconds" if "time_delta_seconds" in columns else "NULL::BIGINT"
    return f"""
        SELECT *, {source_delta} AS inspect_source_delta,
               lag(timestamp) OVER track_order AS inspect_prev_time,
               lag(lon::DOUBLE) OVER track_order AS inspect_prev_lon,
               lag(lat::DOUBLE) OVER track_order AS inspect_prev_lat
        FROM {source}
        WHERE timestamp IS NOT NULL AND lon BETWEEN -180 AND 180 AND lat BETWEEN -90 AND 90
          AND ({condition})
        WINDOW track_order AS (PARTITION BY {track_col}, mmsi ORDER BY {TRACK_POINT_ORDER})
    """


def _track_link(manifest: dict) -> str:
    max_distance, max_gap = track_gap_limits(manifest)
    delta = "(timestamp - inspect_prev_time)"
    return f"""
        inspect_prev_time IS NOT NULL
        AND (inspect_source_delta IS NULL OR {delta} = inspect_source_delta)
        AND {delta} <= {max_gap}
        AND 60 * sqrt(
            power((lon::DOUBLE - inspect_prev_lon) * cos(radians((lat::DOUBLE + inspect_prev_lat) / 2)), 2)
            + power(lat::DOUBLE - inspect_prev_lat, 2)
        ) <= {max_distance}
    """


def _value(value: float | None, *, minimum: float | None = None, upper: float | None = None) -> float | None:
    if value is None or not isfinite(value):
        return None
    if (minimum is not None and value < minimum) or (upper is not None and value >= upper):
        return None
    return value


def viewport_track_points(
    filters: QueryFilters, data_version: str, west: float, south: float, east: float, north: float,
) -> dict:
    manifest = require_track_version(data_version)
    track_col = trajectory_column()
    columns = set(manifest.get("columns", []))
    source, params = trajectory_source(filters)
    max_distance, _ = track_gap_limits(manifest)
    lat_pad = max_distance / 60
    lon_pad = lat_pad / max(0.1, cos(radians(max(abs(south), abs(north)))))
    pairs = _paired_points(source, track_col, columns, """
        lon BETWEEN ? AND ? AND lat BETWEEN ? AND ?
    """)
    heading = "heading::DOUBLE" if "heading" in columns else "NULL::DOUBLE"
    with connect() as con:
        rows = con.execute(f"""
            WITH pairs AS ({pairs}), linked AS (
                SELECT *, ({_track_link(manifest)}) AS linked FROM pairs
            ), view AS (SELECT ? AS west, ? AS south, ? AS east, ? AS north)
            SELECT mmsi, {track_col}, timestamp, lon::DOUBLE, lat::DOUBLE, speed::DOUBLE,
                   course::DOUBLE, {heading}, length::DOUBLE, width::DOUBLE,
                   CASE WHEN linked THEN inspect_prev_lon END,
                   CASE WHEN linked THEN inspect_prev_lat END
            FROM linked CROSS JOIN view
            WHERE (lon BETWEEN west AND east AND lat BETWEEN south AND north)
               OR (linked AND least(lon, inspect_prev_lon) <= east
                          AND greatest(lon, inspect_prev_lon) >= west
                          AND least(lat, inspect_prev_lat) <= north
                          AND greatest(lat, inspect_prev_lat) >= south)
            ORDER BY abs(lon - (west + east) / 2) + abs(lat - (south + north) / 2),
                     mmsi, timestamp, lon, lat
            LIMIT {MAX_VIEW_POINTS + 1}
        """, [*params, west - lon_pad, east + lon_pad, south - lat_pad, north + lat_pad,
              west, south, east, north]).fetchall()
    points = [
        {
            "mmsi": mmsi, "track_id": track_id, "timestamp": timestamp, "lon": lon, "lat": lat,
            "speed": _value(speed, minimum=0), "course": _value(course, minimum=0, upper=360),
            "heading": _value(heading, minimum=0, upper=360), "length": _value(length, minimum=0),
            "width": _value(width, minimum=0), "previous": [prev_lon, prev_lat] if prev_lon is not None else None,
        }
        for mmsi, track_id, timestamp, lon, lat, speed, course, heading, length, width, prev_lon, prev_lat
        in rows[:MAX_VIEW_POINTS]
    ]
    return {"points": points, "limited": len(rows) > MAX_VIEW_POINTS}


def selected_track_source(filters: QueryFilters, anchor: TrackAnchor) -> tuple[str, list]:
    manifest = require_track_version(anchor.data_version)
    track_col = trajectory_column()
    columns = set(manifest.get("columns", []))
    if filters.mmsi is not None and filters.mmsi != anchor.vessel_id:
        raise RuntimeError("选中船舶与当前筛选条件不一致。")
    source, params = trajectory_source(replace(filters, mmsi=anchor.vessel_id))
    pairs = _paired_points(source, track_col, columns, f"mmsi = ? AND {track_col} = ?")
    sql = f"""
        (
            WITH pairs AS ({pairs}), numbered AS (
                SELECT *, sum(CASE WHEN {_track_link(manifest)} THEN 0 ELSE 1 END)
                    OVER (ORDER BY {TRACK_POINT_ORDER} ROWS UNBOUNDED PRECEDING) AS inspect_run
                FROM pairs
            )
            SELECT * EXCLUDE (inspect_source_delta, inspect_prev_time, inspect_prev_lon, inspect_prev_lat, inspect_run)
            FROM numbered
            WHERE inspect_run = (
                SELECT inspect_run FROM numbered WHERE timestamp = ? AND lon::DOUBLE = ? AND lat::DOUBLE = ?
                ORDER BY inspect_run LIMIT 1
            )
        ) AS selected_track
    """
    return sql, [*params, anchor.vessel_id, anchor.track_id, anchor.anchor_time, anchor.anchor_lon, anchor.anchor_lat]


def selected_track_details(filters: QueryFilters, anchor: TrackAnchor) -> dict | None:
    source, params = selected_track_source(filters, anchor)
    with connect() as con:
        count, speed, length, width, path = con.execute(f"""
            WITH numbered AS (
                SELECT *, row_number() OVER (ORDER BY {TRACK_POINT_ORDER}) AS row_id,
                       count(*) OVER () AS total
                FROM {source}
            )
            SELECT count(*), avg(speed::DOUBLE) FILTER (WHERE isfinite(speed) AND speed >= 0),
                   first(length::DOUBLE ORDER BY timestamp DESC) FILTER (WHERE isfinite(length) AND length >= 0),
                   first(width::DOUBLE ORDER BY timestamp DESC) FILTER (WHERE isfinite(width) AND width >= 0),
                   list([lon::DOUBLE, lat::DOUBLE] ORDER BY row_id) FILTER (
                       WHERE row_id = total OR (row_id - 1) % ceil(total / {MAX_HIGHLIGHT_POINTS - 1}.0)::BIGINT = 0
                   )
            FROM numbered
        """, params).fetchone()
    if not count:
        return None
    return {
        "mmsi": anchor.vessel_id, "point_count": count, "speed_avg": _value(speed),
        "length": length, "width": width, "path": path,
    }
