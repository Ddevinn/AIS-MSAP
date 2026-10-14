from __future__ import annotations

from math import ceil, isfinite
from typing import Any

from backend.app.config import MAX_BROWSER_TRACK_POINTS
from backend.app.services.dataset import dataset_ready, read_active_manifest
from backend.app.services.query import behavior_condition, behavior_feature_source, QueryFilters, analysis_source, connect, points_sql, trajectory_column


def require_dataset() -> None:
    if not dataset_ready():
        raise RuntimeError("Dataset is not built yet. Run POST /api/build first.")


def summary(filters: QueryFilters | None = None) -> dict[str, Any]:
    require_dataset()
    source, params = analysis_source(filters)
    track_col = trajectory_column()
    direction_stats = (
        ", count(*) FILTER (WHERE crossing_direction = 1)::UBIGINT AS forward_crossings, "
        "count(*) FILTER (WHERE crossing_direction = -1)::UBIGINT AS reverse_crossings"
        if filters and filters.spatial_mode == "section"
        else ", 0::UBIGINT AS forward_crossings, 0::UBIGINT AS reverse_crossings"
    )
    sql = f"""
        SELECT
            count(*)::UBIGINT AS point_count,
            count(DISTINCT mmsi)::UBIGINT AS vessel_count,
            min(timestamp)::BIGINT AS timestamp_min,
            max(timestamp)::BIGINT AS timestamp_max,
            min(lon) AS lon_min,
            max(lon) AS lon_max,
            min(lat) AS lat_min,
            max(lat) AS lat_max,
            avg(speed) AS speed_avg,
            min(speed) AS speed_min,
            max(speed) AS speed_max,
            count(DISTINCT {track_col})::UBIGINT AS trajectory_count
            {direction_stats}
        FROM {source}
    """
    with connect() as con:
        row = con.execute(sql, params).fetchone()
    keys = [
        "point_count",
        "vessel_count",
        "timestamp_min",
        "timestamp_max",
        "lon_min",
        "lon_max",
        "lat_min",
        "lat_max",
        "speed_avg",
        "speed_min",
        "speed_max",
        "trajectory_count",
        "forward_crossings",
        "reverse_crossings",
    ]
    result = dict(zip(keys, row))
    result["spatial_mode"] = filters.spatial_mode if filters else None
    result["crossing_count"] = result["point_count"] if filters and filters.spatial_mode == "section" else None
    result["manifest"] = read_active_manifest()
    return result


def _single_value_histogram(value: float, count: int, bin_width: float | None, default_width: float) -> dict[str, Any]:
    """Keep constant observations in one positive-width, nonnegative bin."""
    width = float(bin_width) if bin_width is not None else default_width
    if not isfinite(width) or width <= 0:
        width = default_width
    lower = max(0.0, float(value) - width / 2)
    return {"bins": [lower, lower + width], "counts": [int(count)]}


def speed_histogram(bins: int = 40, bin_width: float | None = None, filters: QueryFilters | None = None) -> dict[str, Any]:
    require_dataset()
    source, params = analysis_source(filters)
    with connect() as con:
        bounds = con.execute(
            f"""
            SELECT min(speed)::DOUBLE, max(speed)::DOUBLE
            FROM {source}
            """,
            params,
        ).fetchone()
        min_speed, max_speed = bounds
        if min_speed is None or max_speed is None:
            return {"bins": [], "counts": []}
        if min_speed == max_speed:
            count = con.execute(f"SELECT count(speed)::UBIGINT FROM {source}", params).fetchone()[0]
            return _single_value_histogram(min_speed, count, bin_width, default_width=1.0)
        if bin_width is not None and float(bin_width) > 0:
            computed_bins = max(5, min(int(ceil((max_speed - min_speed) / float(bin_width))), 200))
        else:
            computed_bins = max(5, min(int(bins), 200))
        rows = con.execute(
            f"""
            WITH filtered AS (
                SELECT speed
                FROM {source}
            )
            SELECT
                least(CAST(floor((speed - ?) / NULLIF(? - ?, 0) * ?) AS INTEGER), ? - 1) AS bin,
                count(*)::UBIGINT AS count
            FROM filtered
            GROUP BY bin
            ORDER BY bin
            """,
            [*params, min_speed, max_speed, min_speed, computed_bins, computed_bins],
        ).fetchall()

    counts = [0] * computed_bins
    for bin_idx, count in rows:
        if bin_idx is not None and 0 <= int(bin_idx) < computed_bins:
            counts[int(bin_idx)] = int(count)
    edges = [min_speed + (max_speed - min_speed) * i / computed_bins for i in range(computed_bins + 1)]
    return {"bins": edges, "counts": counts}


def ship_type_distribution(limit: int = 20, filters: QueryFilters | None = None) -> list[dict[str, Any]]:
    require_dataset()
    limit = max(1, min(int(limit), 100))
    source, params = analysis_source(filters)
    with connect() as con:
        rows = con.execute(
            f"""
            SELECT ship_type::INTEGER AS ship_type, count(*)::UBIGINT AS point_count,
                   count(DISTINCT mmsi)::UBIGINT AS vessel_count
            FROM {source}
            GROUP BY ship_type
            ORDER BY point_count DESC
            LIMIT ?
            """,
            [*params, limit],
        ).fetchall()
    return [
        {"ship_type": int(ship_type), "point_count": int(point_count), "vessel_count": int(vessel_count)}
        for ship_type, point_count, vessel_count in rows
    ]


def nav_state_distribution(filters: QueryFilters | None = None) -> list[dict[str, Any]]:
    require_dataset()
    source, params = analysis_source(filters)
    manifest = read_active_manifest() or {}
    columns = manifest.get("columns", [])
    state_expr = (
        "nav_state"
        if "nav_state" in columns
        else """
        CASE
            WHEN speed <= 0.5 THEN 'stopped'
            WHEN speed <= 3.0 THEN 'low_speed'
            ELSE 'underway'
        END
        """
    )
    with connect() as con:
        rows = con.execute(
            f"""
            WITH states AS (
                SELECT {state_expr} AS nav_state
                FROM {source}
            ),
            counted AS (
                SELECT nav_state, count(*)::UBIGINT AS point_count
                FROM states
                GROUP BY nav_state
            )
            SELECT
                nav_state,
                point_count,
                point_count * 100.0 / NULLIF(sum(point_count) OVER (), 0) AS percentage
            FROM counted
            ORDER BY point_count DESC
            """,
            params,
        ).fetchall()
    labels = {"underway": "航行", "low_speed": "低速", "stopped": "停留"}
    return [
        {
            "state": str(state),
            "label": labels.get(str(state), str(state)),
            "point_count": int(count),
            "percentage": float(percentage or 0),
        }
        for state, count, percentage in rows
    ]


def behavior_distribution(filters: QueryFilters | None = None) -> list[dict[str, Any]]:
    """Recognize common vessel behaviors from consecutive AIS points."""
    require_dataset()
    source, params = analysis_source(filters)
    feature_source = behavior_feature_source(source)

    with connect() as con:
        rows = con.execute(
            f"""
            WITH features AS MATERIALIZED ({feature_source}),
            total AS (SELECT count(*)::DOUBLE AS point_count FROM features),
            recognized AS (
                SELECT 'berthing' AS behavior, mmsi FROM features
                WHERE {behavior_condition('berthing')}
                UNION ALL
                SELECT 'anchoring', mmsi FROM features
                WHERE {behavior_condition('anchoring')}
                UNION ALL
                SELECT 'turning', mmsi FROM features
                WHERE {behavior_condition('turning')}
                UNION ALL
                SELECT 'straight', mmsi FROM features
                WHERE {behavior_condition('straight')}
                UNION ALL
                SELECT 'speed_change', mmsi FROM features
                WHERE {behavior_condition('speed_change')}
            )
            SELECT behavior, count(*)::UBIGINT, count(DISTINCT mmsi)::UBIGINT,
                   count(*) * 100.0 / NULLIF(total.point_count, 0)
            FROM recognized CROSS JOIN total
            GROUP BY behavior, total.point_count
            """,
            params,
        ).fetchall()

    labels = {
        "berthing": "靠泊",
        "anchoring": "锚泊",
        "turning": "转向",
        "straight": "直航",
        "speed_change": "变速",
    }
    counts = {
        behavior: {
            "behavior": behavior,
            "label": labels[behavior],
            "point_count": int(point_count),
            "vessel_count": int(vessel_count),
            "percentage": float(percentage or 0),
        }
        for behavior, point_count, vessel_count, percentage in rows
    }
    return [
        counts.get(behavior, {"behavior": behavior, "label": label, "point_count": 0, "vessel_count": 0, "percentage": 0.0})
        for behavior, label in labels.items()
    ]


def time_series(grain: str = "day", filters: QueryFilters | None = None) -> list[dict[str, Any]]:
    require_dataset()
    formats = {
        "hour": "%Y-%m-%d %H:00",
        "day": "%Y-%m-%d",
        "month": "%Y-%m",
    }
    fmt = formats.get(grain, formats["day"])
    source, params = analysis_source(filters)
    with connect() as con:
        rows = con.execute(
            f"""
            SELECT
                strftime(to_timestamp(timestamp), '{fmt}') AS bucket,
                count(*)::UBIGINT AS point_count,
                count(DISTINCT mmsi)::UBIGINT AS vessel_count,
                avg(speed)::DOUBLE AS avg_speed
            FROM {source}
            GROUP BY bucket
            ORDER BY bucket
            """,
            params,
        ).fetchall()
    return [
        {
            "bucket": bucket,
            "point_count": int(point_count),
            "vessel_count": int(vessel_count),
            "avg_speed": float(avg_speed) if avg_speed is not None else None,
        }
        for bucket, point_count, vessel_count, avg_speed in rows
    ]


def length_distribution(bins: int = 30, bin_width: float | None = None, filters: QueryFilters | None = None) -> dict[str, Any]:
    """每艘独立船舶的船长分布直方图（以 MMSI 去重）。支持 bin_width（米）指定组距。"""
    require_dataset()
    source, params = analysis_source(filters)
    valid_cond = "length > 0 AND length IS NOT NULL AND isfinite(length) AND length <= 500"
    full_where = f"WHERE {valid_cond}"
    with connect() as con:
        bounds = con.execute(
            f"SELECT min(length)::DOUBLE, max(length)::DOUBLE FROM {source} {full_where}",
            params,
        ).fetchone()
        min_len, max_len = bounds
        if min_len is None or max_len is None:
            return {"bins": [], "counts": []}
        if min_len == max_len:
            count = con.execute(
                f"SELECT count(*)::UBIGINT FROM (SELECT DISTINCT mmsi FROM {source} {full_where}) AS vessels",
                params,
            ).fetchone()[0]
            return _single_value_histogram(min_len, count, bin_width, default_width=10.0)
        if bin_width is not None and float(bin_width) > 0:
            computed_bins = max(5, min(int(ceil((max_len - min_len) / float(bin_width))), 200))
        else:
            computed_bins = max(5, min(int(bins), 100))
        rows = con.execute(
            f"""
            WITH vessels AS (
                SELECT mmsi, any_value(length) AS length
                FROM {source}
                {full_where}
                GROUP BY mmsi
            )
            SELECT
                least(CAST(floor((length - ?) / NULLIF(? - ?, 0) * ?) AS INTEGER), ? - 1) AS bin,
                count(*)::UBIGINT AS vessel_count
            FROM vessels
            GROUP BY bin
            ORDER BY bin
            """,
            [*params, min_len, max_len, min_len, computed_bins, computed_bins],
        ).fetchall()
    counts = [0] * computed_bins
    for bin_idx, count in rows:
        if bin_idx is not None and 0 <= int(bin_idx) < computed_bins:
            counts[int(bin_idx)] = int(count)
    edges = [min_len + (max_len - min_len) * i / computed_bins for i in range(computed_bins + 1)]
    return {"bins": edges, "counts": counts}


def density_grid(grid_res: float = 0.001, filters: QueryFilters | None = None) -> dict[str, Any]:
    require_dataset()
    # grid_res: 每格的经纬度跨度（度），约 0.0001°≈10m ~ 0.1°≈10km
    grid_res = max(0.0001, min(float(grid_res), 0.5))
    source, params = analysis_source(filters)
    with connect() as con:
        bounds = con.execute(
            f"""
            SELECT min(lon)::DOUBLE, max(lon)::DOUBLE, min(lat)::DOUBLE, max(lat)::DOUBLE
            FROM {source}
            """,
            params,
        ).fetchone()
        lon_min, lon_max, lat_min, lat_max = bounds
        if None in bounds or lon_min == lon_max or lat_min == lat_max:
            return {"bounds": bounds, "grid_size_x": 1, "grid_size_y": 1, "cells": []}

        grid_size_x = max(1, ceil((lon_max - lon_min) / grid_res))
        grid_size_y = max(1, ceil((lat_max - lat_min) / grid_res))
        # 防止内存爆炸：单轴超过 8000 格时等比压缩
        if max(grid_size_x, grid_size_y) > 8000:
            factor = max(grid_size_x, grid_size_y) / 8000
            grid_size_x = max(1, int(grid_size_x / factor))
            grid_size_y = max(1, int(grid_size_y / factor))

        rows = con.execute(
            f"""
            WITH filtered AS (
                SELECT lon, lat
                FROM {source}
            )
            SELECT
                least(CAST(floor((lon - ?) / NULLIF(? - ?, 0) * ?) AS INTEGER), ? - 1) AS x,
                least(CAST(floor((lat - ?) / NULLIF(? - ?, 0) * ?) AS INTEGER), ? - 1) AS y,
                count(*)::UBIGINT AS count
            FROM filtered
            GROUP BY x, y
            HAVING x IS NOT NULL AND y IS NOT NULL
            """,
            [
                *params,
                lon_min, lon_max, lon_min, grid_size_x, grid_size_x,
                lat_min, lat_max, lat_min, grid_size_y, grid_size_y,
            ],
        ).fetchall()
    return {
        "bounds": {
            "lon_min": lon_min,
            "lon_max": lon_max,
            "lat_min": lat_min,
            "lat_max": lat_max,
        },
        "grid_size_x": grid_size_x,
        "grid_size_y": grid_size_y,
        "cells": [{"x": int(x), "y": int(y), "count": int(count)} for x, y, count in rows],
    }


def track_points(mmsi: int, max_points: int = MAX_BROWSER_TRACK_POINTS) -> dict[str, Any]:
    require_dataset()
    max_points = max(100, min(int(max_points), MAX_BROWSER_TRACK_POINTS))
    with connect() as con:
        total = con.execute(
            f"SELECT count(*)::UBIGINT FROM {points_sql()} WHERE mmsi = ?",
            [mmsi],
        ).fetchone()[0]
        if total == 0:
            return {"mmsi": mmsi, "total_points": 0, "sampled_points": 0, "points": []}
        step = max(1, ceil(total / max_points))
        rows = con.execute(
            f"""
            WITH ordered AS (
                SELECT timestamp, lon, lat, speed, course,
                       row_number() OVER (ORDER BY timestamp) AS rn
                FROM {points_sql()}
                WHERE mmsi = ?
            )
            SELECT timestamp, lon, lat, speed, course
            FROM ordered
            WHERE ((rn - 1) % ?) = 0
            ORDER BY timestamp
            """,
            [mmsi, step],
        ).fetchall()
    points = [
        {
            "timestamp": int(ts),
            "lon": float(lon),
            "lat": float(lat),
            "speed": float(speed),
            "course": float(course),
        }
        for ts, lon, lat, speed, course in rows
    ]
    return {"mmsi": mmsi, "total_points": int(total), "sampled_points": len(points), "points": points}
