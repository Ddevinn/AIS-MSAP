from __future__ import annotations

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
    mmsi: int | None = None
    speed_min: float | None = None
    speed_max: float | None = None


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


def where_clause(filters: QueryFilters | None) -> tuple[str, list[Any]]:
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
    if filters.mmsi is not None:
        clauses.append("mmsi = ?")
        params.append(filters.mmsi)
    if filters.speed_min is not None:
        clauses.append("speed >= ?")
        params.append(filters.speed_min)
    if filters.speed_max is not None:
        clauses.append("speed <= ?")
        params.append(filters.speed_max)

    if not clauses:
        return "", params
    return "WHERE " + " AND ".join(clauses), params
