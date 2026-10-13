from __future__ import annotations

import json
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable

from pydantic import BaseModel, Field

from backend.app.services.dataset import (
    active_dataset_id,
    cleaned_parquet_path,
    cleaning_manifest_path,
    dataset_paths,
    read_cleaning_manifest as read_dataset_cleaning_manifest,
    read_manifest,
    source_dataset_ready,
    source_parquet_path,
)
from backend.app.services.query import connect, points_sql


ProgressCallback = Callable[[dict[str, Any]], None]


class CleaningConfig(BaseModel):
    mmsi_min: int = Field(default=100_000_000, ge=0)
    mmsi_max: int = Field(default=999_999_999, ge=0)
    lon_min: float = Field(default=-180.0, ge=-180.0, le=180.0)
    lon_max: float = Field(default=180.0, ge=-180.0, le=180.0)
    lat_min: float = Field(default=-90.0, ge=-90.0, le=90.0)
    lat_max: float = Field(default=90.0, ge=-90.0, le=90.0)
    speed_min: float = Field(default=0.0, ge=0.0)
    speed_max: float = Field(default=50.0, ge=0.0)
    course_min: float = Field(default=0.0, ge=0.0)
    course_max: float = Field(default=360.0, ge=0.0)
    max_jump_speed_knots: float = Field(default=50.0, ge=1.0)
    max_acceleration_knots_per_min: float = Field(default=8.0, ge=0.1)
    max_time_gap_minutes: float = Field(default=60.0, ge=1.0)
    max_segment_distance_nm: float = Field(default=3.0, ge=0.01)
    stop_speed_knots: float = Field(default=0.5, ge=0.0)
    low_speed_knots: float = Field(default=3.0, ge=0.0)
    remove_exact_duplicates: bool = True


def default_cleaning_config() -> CleaningConfig:
    return CleaningConfig()


def cleaning_ready(dataset_id: str | None = None) -> bool:
    cleaned_path = cleaned_parquet_path(dataset_id)
    manifest_path = cleaning_manifest_path(dataset_id)
    return cleaned_path.exists() and manifest_path.exists()


def read_cleaning_manifest(dataset_id: str | None = None) -> dict[str, Any] | None:
    return read_dataset_cleaning_manifest(dataset_id)


def reset_cleaning_result(dataset_id: str | None = None) -> None:
    for path in (cleaned_parquet_path(dataset_id), cleaning_manifest_path(dataset_id)):
        if path.exists():
            path.unlink()


def run_cleaning(
    config: CleaningConfig | None = None,
    progress: ProgressCallback | None = None,
    dataset_id: str | None = None,
) -> dict[str, Any]:
    resolved_dataset_id = dataset_id or active_dataset_id()
    paths = dataset_paths(resolved_dataset_id)
    source_path = source_parquet_path(resolved_dataset_id)
    output_path = cleaned_parquet_path(resolved_dataset_id)
    manifest_path = cleaning_manifest_path(resolved_dataset_id)

    if not source_dataset_ready(resolved_dataset_id):
        raise RuntimeError("Source Parquet is not ready. Run POST /api/build first.")

    config = config or default_cleaning_config()
    _validate_config(config)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    manifest_path.parent.mkdir(parents=True, exist_ok=True)
    for stale_path in (output_path,):
        if stale_path.exists():
            stale_path.unlink()

    started = time.perf_counter()
    emit(progress, "preparing", "准备清洗任务", percent=3)

    source = points_sql(source_path)
    with connect() as con:
        source_columns = {row[0] for row in con.execute(f"DESCRIBE SELECT * FROM {source}").fetchall()}
        extra_columns = [name for name in ("draught", "heading") if name in source_columns]
        extra_sql = "".join(f", {name}" for name in extra_columns)
        emit(progress, "validating", "校验字段范围和基础格式", percent=8)
        con.execute(_base_table_sql(source), _base_params(config))
        emit(progress, "deduplicating", "去除精确重复点", percent=18)
        con.execute(_unique_table_sql(config))
        emit(progress, "motion", "识别跳点、漂移点和速度突变点", percent=42)
        con.execute(_flagged_table_sql(config))
        emit(progress, "segmenting", "切分轨迹片段并标注航行状态", percent=68)
        con.execute(_final_table_sql(config, extra_columns))

        emit(progress, "writing", "写入清洗后的 Parquet", percent=86)
        con.execute(
            f"""
            COPY (
                SELECT
                    mmsi,
                    timestamp,
                    lon,
                    lat,
                    course,
                    speed,
                    ship_type,
                    length,
                    width{extra_sql},
                    trajectory_id,
                    clean_segment_id,
                    segment_break_reason,
                    time_delta_seconds,
                    distance_nm,
                    implied_speed_knots,
                    acceleration_knots_per_min,
                    turn_angle_deg,
                    nav_state
                FROM cleaning_final
                ORDER BY mmsi, timestamp, trajectory_id
            )
            TO '{_sql_path(output_path)}'
            (FORMAT PARQUET, COMPRESSION ZSTD)
            """
        )

        emit(progress, "summarizing", "汇总清洗结果", percent=95)
        counts = _counts(con)
        ranges = _ranges(con)

    manifest = {
        "dataset_id": resolved_dataset_id,
        "dataset_name": paths.name,
        "data_role": "cleaned",
        "source_parquet": str(source_path),
        "parquet": str(output_path),
        "created_at": datetime.now(timezone.utc).isoformat(),
        "elapsed_seconds": round(time.perf_counter() - started, 2),
        "config": config.model_dump(),
        "source_manifest": read_manifest(resolved_dataset_id),
        "counts": counts,
        "row_count": counts["output_rows"],
        "trajectory_count": ranges["trajectory_count"],
        "vessel_count": ranges["vessel_count"],
        "bounds": ranges["bounds"],
        "time_range": ranges["time_range"],
        "speed_range": ranges["speed_range"],
        "columns": [
            "mmsi",
            "timestamp",
            "lon",
            "lat",
            "course",
            "speed",
            "ship_type",
            "length",
            "width",
            *extra_columns,
            "trajectory_id",
            "clean_segment_id",
            "segment_break_reason",
            "time_delta_seconds",
            "distance_nm",
            "implied_speed_knots",
            "acceleration_knots_per_min",
            "turn_angle_deg",
            "nav_state",
        ],
    }
    manifest_path.write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")
    emit(progress, "done", "清洗完成", manifest, percent=100)
    return manifest


def emit(
    progress: ProgressCallback | None,
    stage: str,
    message: str,
    manifest: dict[str, Any] | None = None,
    percent: int | float | None = None,
) -> None:
    if progress:
        payload: dict[str, Any] = {"stage": stage, "message": message}
        if percent is not None:
            payload["percent"] = max(0, min(100, round(float(percent), 1)))
        if manifest:
            payload["row_count"] = manifest.get("row_count")
            payload["elapsed_seconds"] = manifest.get("elapsed_seconds")
        progress(payload)


def _validate_config(config: CleaningConfig) -> None:
    if config.mmsi_min > config.mmsi_max:
        raise ValueError("mmsi_min must be <= mmsi_max")
    if config.lon_min > config.lon_max:
        raise ValueError("lon_min must be <= lon_max")
    if config.lat_min > config.lat_max:
        raise ValueError("lat_min must be <= lat_max")
    if config.speed_min > config.speed_max:
        raise ValueError("speed_min must be <= speed_max")
    if config.course_min > config.course_max:
        raise ValueError("course_min must be <= course_max")
    if config.stop_speed_knots > config.low_speed_knots:
        raise ValueError("stop_speed_knots must be <= low_speed_knots")


def _base_table_sql(source: str) -> str:
    return f"""
        CREATE OR REPLACE TEMP TABLE cleaning_base AS
        SELECT
            *,
            (mmsi BETWEEN ? AND ?) AS valid_mmsi,
            (timestamp IS NOT NULL AND timestamp > 0) AS valid_timestamp,
            (lon BETWEEN ? AND ? AND lat BETWEEN ? AND ?) AS valid_position,
            (speed BETWEEN ? AND ?) AS valid_speed,
            (course BETWEEN ? AND ?) AS valid_course,
            (length IS NULL OR length >= 0) AS valid_length,
            (width IS NULL OR width >= 0) AS valid_width
        FROM {source}
    """


def _base_params(config: CleaningConfig) -> list[Any]:
    return [
        config.mmsi_min,
        config.mmsi_max,
        config.lon_min,
        config.lon_max,
        config.lat_min,
        config.lat_max,
        config.speed_min,
        config.speed_max,
        config.course_min,
        config.course_max,
    ]


def _unique_table_sql(config: CleaningConfig) -> str:
    duplicate_rank = (
        "row_number() OVER (PARTITION BY mmsi, timestamp, lon, lat ORDER BY trajectory_id)"
        if config.remove_exact_duplicates
        else "1"
    )
    return f"""
        CREATE OR REPLACE TEMP TABLE cleaning_unique AS
        SELECT *
        FROM (
            SELECT
                *,
                {duplicate_rank} AS duplicate_rank
            FROM cleaning_base
            WHERE valid_mmsi
              AND valid_timestamp
              AND valid_position
              AND valid_speed
              AND valid_course
              AND valid_length
              AND valid_width
        )
        WHERE duplicate_rank = 1
    """


def _flagged_table_sql(config: CleaningConfig) -> str:
    return f"""
        CREATE OR REPLACE TEMP TABLE cleaning_flagged AS
        WITH ordered AS (
            SELECT
                *,
                lag(timestamp) OVER (PARTITION BY mmsi ORDER BY timestamp, trajectory_id, lon, lat) AS prev_timestamp,
                lag(lon) OVER (PARTITION BY mmsi ORDER BY timestamp, trajectory_id, lon, lat) AS prev_lon,
                lag(lat) OVER (PARTITION BY mmsi ORDER BY timestamp, trajectory_id, lon, lat) AS prev_lat,
                lag(speed) OVER (PARTITION BY mmsi ORDER BY timestamp, trajectory_id, lon, lat) AS prev_speed,
                lag(course) OVER (PARTITION BY mmsi ORDER BY timestamp, trajectory_id, lon, lat) AS prev_course
            FROM cleaning_unique
        ),
        motion AS (
            SELECT
                *,
                timestamp - prev_timestamp AS time_delta_seconds,
                CASE
                    WHEN prev_timestamp IS NULL THEN NULL
                    ELSE 2 * 3440.065 * asin(
                        sqrt(
                            least(
                                1.0,
                                pow(sin(radians(lat - prev_lat) / 2), 2)
                                + cos(radians(prev_lat))
                                * cos(radians(lat))
                                * pow(sin(radians(lon - prev_lon) / 2), 2)
                            )
                        )
                    )
                END AS distance_nm
            FROM ordered
        ),
        features AS (
            SELECT
                *,
                CASE
                    WHEN time_delta_seconds IS NULL OR time_delta_seconds <= 0 THEN NULL
                    ELSE distance_nm / (time_delta_seconds / 3600.0)
                END AS implied_speed_knots,
                CASE
                    WHEN time_delta_seconds IS NULL OR time_delta_seconds <= 0 THEN NULL
                    ELSE abs(speed - prev_speed) / (time_delta_seconds / 60.0)
                END AS acceleration_knots_per_min,
                CASE
                    WHEN prev_course IS NULL THEN NULL
                    ELSE abs(((course - prev_course + 540) % 360) - 180)
                END AS turn_angle_deg
            FROM motion
        )
        SELECT
            *,
            CASE
                WHEN speed <= {config.stop_speed_knots} THEN 'stopped'
                WHEN speed <= {config.low_speed_knots} THEN 'low_speed'
                ELSE 'underway'
            END AS nav_state,
            (
                time_delta_seconds IS NOT NULL
                AND time_delta_seconds <= 0
            ) AS invalid_time_order,
            (
                implied_speed_knots IS NOT NULL
                AND implied_speed_knots > {config.max_jump_speed_knots}
            ) AS jump_point,
            (
                acceleration_knots_per_min IS NOT NULL
                AND acceleration_knots_per_min > {config.max_acceleration_knots_per_min}
            ) AS acceleration_point,
            (
                time_delta_seconds IS NULL
                OR time_delta_seconds > {config.max_time_gap_minutes * 60.0}
                OR (
                    distance_nm IS NOT NULL
                    AND distance_nm > {config.max_segment_distance_nm}
                )
            ) AS gap_break,
            CASE
                WHEN time_delta_seconds IS NULL THEN 'start'
                WHEN time_delta_seconds > {config.max_time_gap_minutes * 60.0} THEN 'time_gap'
                WHEN distance_nm IS NOT NULL
                     AND distance_nm > {config.max_segment_distance_nm} THEN 'distance_gap'
                ELSE 'continuous'
            END AS segment_break_reason
        FROM features
    """


def _final_table_sql(config: CleaningConfig, extra_columns: list[str] | None = None) -> str:
    extra_sql = "".join(f", {name}" for name in (extra_columns or []))
    return f"""
        CREATE OR REPLACE TEMP TABLE cleaning_final AS
        WITH kept AS (
            SELECT
                mmsi,
                timestamp,
                lon,
                lat,
                course,
                speed,
                ship_type,
                length,
                width{extra_sql},
                trajectory_id,
                nav_state
            FROM cleaning_flagged
            WHERE NOT invalid_time_order
              AND NOT jump_point
              AND NOT acceleration_point
        ),
        ordered AS (
            SELECT
                *,
                lag(timestamp) OVER (PARTITION BY mmsi ORDER BY timestamp, trajectory_id, lon, lat) AS prev_timestamp,
                lag(lon) OVER (PARTITION BY mmsi ORDER BY timestamp, trajectory_id, lon, lat) AS prev_lon,
                lag(lat) OVER (PARTITION BY mmsi ORDER BY timestamp, trajectory_id, lon, lat) AS prev_lat,
                lag(speed) OVER (PARTITION BY mmsi ORDER BY timestamp, trajectory_id, lon, lat) AS prev_speed,
                lag(course) OVER (PARTITION BY mmsi ORDER BY timestamp, trajectory_id, lon, lat) AS prev_course
            FROM kept
        ),
        motion AS (
            SELECT
                mmsi,
                timestamp,
                lon,
                lat,
                course,
                speed,
                ship_type,
                length,
                width{extra_sql},
                trajectory_id,
                nav_state,
                timestamp - prev_timestamp AS time_delta_seconds,
                CASE
                    WHEN prev_timestamp IS NULL THEN NULL
                    ELSE 2 * 3440.065 * asin(
                        sqrt(
                            least(
                                1.0,
                                pow(sin(radians(lat - prev_lat) / 2), 2)
                                + cos(radians(prev_lat))
                                * cos(radians(lat))
                                * pow(sin(radians(lon - prev_lon) / 2), 2)
                            )
                        )
                    )
                END AS distance_nm,
                CASE
                    WHEN prev_timestamp IS NULL OR timestamp <= prev_timestamp THEN NULL
                    ELSE (
                        2 * 3440.065 * asin(
                            sqrt(
                                least(
                                    1.0,
                                    pow(sin(radians(lat - prev_lat) / 2), 2)
                                    + cos(radians(prev_lat))
                                    * cos(radians(lat))
                                    * pow(sin(radians(lon - prev_lon) / 2), 2)
                                )
                            )
                        )
                    ) / ((timestamp - prev_timestamp) / 3600.0)
                END AS implied_speed_knots,
                CASE
                    WHEN prev_timestamp IS NULL OR timestamp <= prev_timestamp THEN NULL
                    ELSE abs(speed - prev_speed) / ((timestamp - prev_timestamp) / 60.0)
                END AS acceleration_knots_per_min,
                CASE
                    WHEN prev_course IS NULL THEN NULL
                    ELSE abs(((course - prev_course + 540) % 360) - 180)
                END AS turn_angle_deg
            FROM ordered
        ),
        marked AS (
            SELECT
                *,
                (
                    time_delta_seconds IS NULL
                    OR time_delta_seconds > {config.max_time_gap_minutes * 60.0}
                    OR (
                        distance_nm IS NOT NULL
                        AND distance_nm > {config.max_segment_distance_nm}
                    )
                ) AS gap_break,
                CASE
                    WHEN time_delta_seconds IS NULL THEN 'start'
                    WHEN time_delta_seconds > {config.max_time_gap_minutes * 60.0} THEN 'time_gap'
                    WHEN distance_nm IS NOT NULL
                         AND distance_nm > {config.max_segment_distance_nm} THEN 'distance_gap'
                    ELSE 'continuous'
                END AS segment_break_reason
            FROM motion
        ),
        segmented AS (
            SELECT
                *,
                sum(CASE WHEN gap_break THEN 1 ELSE 0 END)
                    OVER (
                        PARTITION BY mmsi
                        ORDER BY timestamp, trajectory_id, lon, lat
                        ROWS BETWEEN UNBOUNDED PRECEDING AND CURRENT ROW
                    ) AS clean_segment_index
            FROM marked
        )
        SELECT
            *,
            dense_rank() OVER (ORDER BY mmsi, clean_segment_index)::BIGINT AS clean_segment_id
        FROM segmented
    """


def _counts(con) -> dict[str, int]:
    row = con.execute(
        """
        SELECT
            count(*)::UBIGINT AS raw_rows,
            count(*) FILTER (WHERE NOT valid_mmsi)::UBIGINT AS invalid_mmsi_rows,
            count(*) FILTER (WHERE NOT valid_timestamp)::UBIGINT AS invalid_timestamp_rows,
            count(*) FILTER (WHERE NOT valid_position)::UBIGINT AS invalid_position_rows,
            count(*) FILTER (WHERE NOT valid_speed)::UBIGINT AS invalid_speed_rows,
            count(*) FILTER (WHERE NOT valid_course)::UBIGINT AS invalid_course_rows,
            count(*) FILTER (WHERE NOT valid_length OR NOT valid_width)::UBIGINT AS invalid_size_rows
        FROM cleaning_base
        """
    ).fetchone()
    keys = [
        "raw_rows",
        "invalid_mmsi_rows",
        "invalid_timestamp_rows",
        "invalid_position_rows",
        "invalid_speed_rows",
        "invalid_course_rows",
        "invalid_size_rows",
    ]
    counts = dict(zip(keys, [int(value) for value in row]))
    duplicate_count = con.execute(
        """
        SELECT
            (
                SELECT count(*)::UBIGINT
                FROM cleaning_base
                WHERE valid_mmsi
                  AND valid_timestamp
                  AND valid_position
                  AND valid_speed
                  AND valid_course
                  AND valid_length
                  AND valid_width
            )
            - (SELECT count(*)::UBIGINT FROM cleaning_unique)
        """
    ).fetchone()[0]
    flags = con.execute(
        """
        SELECT
            count(*) FILTER (WHERE invalid_time_order)::UBIGINT AS invalid_time_order_rows,
            count(*) FILTER (WHERE jump_point)::UBIGINT AS jump_point_rows,
            count(*) FILTER (WHERE acceleration_point)::UBIGINT AS acceleration_point_rows
        FROM cleaning_flagged
        """
    ).fetchone()
    output_rows = con.execute("SELECT count(*)::UBIGINT FROM cleaning_final").fetchone()[0]
    segment_counts = con.execute(
        """
        SELECT
            count(*) FILTER (WHERE gap_break)::UBIGINT AS segment_break_rows,
            count(*) FILTER (WHERE gap_break AND segment_break_reason = 'time_gap')::UBIGINT AS time_segment_break_rows,
            count(*) FILTER (WHERE gap_break AND segment_break_reason = 'distance_gap')::UBIGINT AS distance_segment_break_rows
        FROM cleaning_final
        """
    ).fetchone()
    counts.update(
        {
            "duplicate_rows": int(duplicate_count),
            "invalid_time_order_rows": int(flags[0]),
            "jump_point_rows": int(flags[1]),
            "acceleration_point_rows": int(flags[2]),
            "segment_break_rows": int(segment_counts[0]),
            "time_segment_break_rows": int(segment_counts[1]),
            "distance_segment_break_rows": int(segment_counts[2]),
            "output_rows": int(output_rows),
            "removed_rows": int(counts["raw_rows"] - output_rows),
        }
    )
    return counts


def _ranges(con) -> dict[str, Any]:
    row = con.execute(
        """
        SELECT
            count(DISTINCT mmsi)::UBIGINT AS vessel_count,
            count(DISTINCT clean_segment_id)::UBIGINT AS trajectory_count,
            min(timestamp)::BIGINT AS timestamp_min,
            max(timestamp)::BIGINT AS timestamp_max,
            min(lon)::DOUBLE AS lon_min,
            max(lon)::DOUBLE AS lon_max,
            min(lat)::DOUBLE AS lat_min,
            max(lat)::DOUBLE AS lat_max,
            min(speed)::DOUBLE AS speed_min,
            max(speed)::DOUBLE AS speed_max
        FROM cleaning_final
        """
    ).fetchone()
    return {
        "vessel_count": int(row[0] or 0),
        "trajectory_count": int(row[1] or 0),
        "time_range": {"timestamp_min": row[2], "timestamp_max": row[3]},
        "bounds": {"lon_min": row[4], "lon_max": row[5], "lat_min": row[6], "lat_max": row[7]},
        "speed_range": {"speed_min": row[8], "speed_max": row[9]},
    }


def _sql_path(path: Path) -> str:
    return str(path).replace("\\", "/").replace("'", "''")
