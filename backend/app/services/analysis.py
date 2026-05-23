from __future__ import annotations

from math import ceil
from typing import Any

from backend.app.config import MAX_BROWSER_TRACK_POINTS
from backend.app.services.dataset import dataset_ready, read_active_manifest
from backend.app.services.query import QueryFilters, connect, points_sql, trajectory_column, where_clause


def require_dataset() -> None:
    if not dataset_ready():
        raise RuntimeError("Dataset is not built yet. Run POST /api/build first.")


def summary(filters: QueryFilters | None = None) -> dict[str, Any]:
    require_dataset()
    where, params = where_clause(filters)
    track_col = trajectory_column()
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
        FROM {points_sql()}
        {where}
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
    ]
    result = dict(zip(keys, row))
    result["manifest"] = read_active_manifest()
    return result


def speed_histogram(bins: int = 40, filters: QueryFilters | None = None) -> dict[str, Any]:
    require_dataset()
    bins = max(5, min(int(bins), 200))
    where, params = where_clause(filters)
    source = points_sql()
    with connect() as con:
        bounds = con.execute(
            f"""
            SELECT min(speed)::DOUBLE, max(speed)::DOUBLE
            FROM {source}
            {where}
            """,
            params,
        ).fetchone()
        min_speed, max_speed = bounds
        if min_speed is None or max_speed is None:
            return {"bins": [], "counts": []}
        if min_speed == max_speed:
            return {"bins": [float(min_speed), float(max_speed)], "counts": [0, 0]}
        rows = con.execute(
            f"""
            WITH filtered AS (
                SELECT speed
                FROM {source}
                {where}
            )
            SELECT
                least(CAST(floor((speed - ?) / NULLIF(? - ?, 0) * ?) AS INTEGER), ? - 1) AS bin,
                count(*)::UBIGINT AS count
            FROM filtered
            GROUP BY bin
            ORDER BY bin
            """,
            [*params, min_speed, max_speed, min_speed, bins, bins],
        ).fetchall()

    counts = [0] * bins
    for bin_idx, count in rows:
        if bin_idx is not None and 0 <= int(bin_idx) < bins:
            counts[int(bin_idx)] = int(count)
    edges = [min_speed + (max_speed - min_speed) * i / bins for i in range(bins + 1)]
    return {"bins": edges, "counts": counts}


def ship_type_distribution(limit: int = 20, filters: QueryFilters | None = None) -> list[dict[str, Any]]:
    require_dataset()
    limit = max(1, min(int(limit), 100))
    where, params = where_clause(filters)
    with connect() as con:
        rows = con.execute(
            f"""
            SELECT ship_type::INTEGER AS ship_type, count(*)::UBIGINT AS point_count,
                   count(DISTINCT mmsi)::UBIGINT AS vessel_count
            FROM {points_sql()}
            {where}
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
    where, params = where_clause(filters)
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
                FROM {points_sql()}
                {where}
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


def time_series(grain: str = "day", filters: QueryFilters | None = None) -> list[dict[str, Any]]:
    require_dataset()
    formats = {
        "hour": "%Y-%m-%d %H:00",
        "day": "%Y-%m-%d",
        "month": "%Y-%m",
    }
    fmt = formats.get(grain, formats["day"])
    where, params = where_clause(filters)
    with connect() as con:
        rows = con.execute(
            f"""
            SELECT
                strftime(to_timestamp(timestamp), '{fmt}') AS bucket,
                count(*)::UBIGINT AS point_count,
                count(DISTINCT mmsi)::UBIGINT AS vessel_count,
                avg(speed)::DOUBLE AS avg_speed
            FROM {points_sql()}
            {where}
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


def density_grid(bins: int = 400, filters: QueryFilters | None = None) -> dict[str, Any]:
    require_dataset()
    bins = max(50, min(int(bins), 1000))
    where, params = where_clause(filters)
    source = points_sql()
    with connect() as con:
        bounds = con.execute(
            f"""
            SELECT min(lon)::DOUBLE, max(lon)::DOUBLE, min(lat)::DOUBLE, max(lat)::DOUBLE
            FROM {source}
            {where}
            """,
            params,
        ).fetchone()
        lon_min, lon_max, lat_min, lat_max = bounds
        if None in bounds or lon_min == lon_max or lat_min == lat_max:
            return {"bounds": bounds, "bins": bins, "cells": []}

        rows = con.execute(
            f"""
            WITH filtered AS (
                SELECT lon, lat
                FROM {source}
                {where}
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
                lon_min,
                lon_max,
                lon_min,
                bins,
                bins,
                lat_min,
                lat_max,
                lat_min,
                bins,
                bins,
            ],
        ).fetchall()
    return {
        "bounds": {
            "lon_min": lon_min,
            "lon_max": lon_max,
            "lat_min": lat_min,
            "lat_max": lat_max,
        },
        "bins": bins,
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
