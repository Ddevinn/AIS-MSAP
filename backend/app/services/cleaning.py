from __future__ import annotations

import json
import time
from datetime import datetime, timezone
from pathlib import Path
from tempfile import TemporaryDirectory
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
    remove_exact_duplicates: bool = True


def default_cleaning_config() -> CleaningConfig:
    return CleaningConfig()


def cleaning_ready(dataset_id: str | None = None) -> bool:
    cleaned_path = cleaned_parquet_path(dataset_id)
    manifest_path = cleaning_manifest_path(dataset_id)
    return cleaned_path.exists() and manifest_path.exists()


def read_cleaning_manifest(dataset_id: str | None = None) -> dict[str, Any] | None:
    return read_dataset_cleaning_manifest(dataset_id)


def rejected_parquet_path(dataset_id: str | None = None) -> Path:
    return cleaned_parquet_path(dataset_id).with_name("ais_points_rejected.parquet")


def reset_cleaning_result(dataset_id: str | None = None) -> None:
    for path in (cleaning_manifest_path(dataset_id), cleaned_parquet_path(dataset_id), rejected_parquet_path(dataset_id)):
        path.unlink(missing_ok=True)


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

    started = time.perf_counter()
    emit(progress, "preparing", "准备清洗任务", percent=3)

    with TemporaryDirectory(prefix="cleaning-", dir=output_path.parent) as directory:
        staged = Path(directory)
        manifest = _stage_cleaning(
            paths, source_path, staged, config, progress, started,
        )
        manifest["parquet"] = str(output_path)
        staged_manifest = staged / "manifest.json"
        staged_manifest.write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")

        # The manifest is the publication marker for both results.
        manifest_path.unlink(missing_ok=True)
        (staged / "cleaned.parquet").replace(output_path)
        (staged / "rejected.parquet").replace(rejected_parquet_path(resolved_dataset_id))
        staged_manifest.replace(manifest_path)
    emit(progress, "done", "清洗完成", manifest, percent=100)
    return manifest


def _stage_cleaning(paths, source_path, staged, config, progress, started):
    source = points_sql(source_path)
    with connect() as con:
        source_columns = {row[0] for row in con.execute(f"DESCRIBE SELECT * FROM {source}").fetchall()}
        if "source_point_id" not in source_columns:
            source = f"""(
                SELECT *, row_number() OVER () AS source_point_id, NULL::VARCHAR AS source_sheet,
                       NULL::BIGINT AS source_record, NULL::VARCHAR AS source_invalid_values
                FROM {source}
            ) AS legacy_points"""
        extra_columns = [name for name in ("draught", "heading") if name in source_columns]
        extra_sql = "".join(f", {name}" for name in extra_columns)
        emit(progress, "validating", "校验字段范围和基础格式", percent=8)
        con.execute(_base_table_sql(source), _base_params(config))
        emit(progress, "deduplicating", "去除精确重复点", percent=18)
        con.execute(_ranked_table_sql(config))
        con.execute("CREATE TEMP TABLE cleaning_unique AS SELECT * FROM cleaning_ranked WHERE duplicate_rank = 1")
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
            TO '{_sql_path(staged / 'cleaned.parquet')}'
            (FORMAT PARQUET, COMPRESSION ZSTD)
            """
        )

        emit(progress, "audit", "保存逐条剔除记录", percent=91)
        con.execute(f"""
            COPY ({_rejected_sql(paths.source_path.name, extra_columns, config)})
            TO '{_sql_path(staged / 'rejected.parquet')}' (FORMAT PARQUET, COMPRESSION ZSTD)
        """)
        emit(progress, "summarizing", "汇总清洗结果", percent=95)
        counts = _counts(con)
        rejected_count = con.execute(
            f"SELECT count(*) FROM {points_sql(staged / 'rejected.parquet')}"
        ).fetchone()[0]
        if rejected_count != counts["removed_rows"]:
            raise RuntimeError("剔除记录数与清洗汇总不一致。")
        ranges = _ranges(con)

    return {
        "dataset_id": paths.dataset_id,
        "dataset_name": paths.name,
        "data_role": "cleaned",
        "source_parquet": str(source_path),
        "rejected_records_available": True,
        "created_at": datetime.now(timezone.utc).isoformat(),
        "elapsed_seconds": round(time.perf_counter() - started, 2),
        "config": config.model_dump(),
        "source_manifest": read_manifest(paths.dataset_id),
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


VALIDATION_REASONS = {
    "valid_mmsi": "invalid_mmsi",
    "valid_timestamp": "invalid_timestamp",
    "valid_position": "invalid_position",
    "valid_speed": "invalid_speed",
    "valid_course": "invalid_course",
    "valid_length": "invalid_length",
    "valid_width": "invalid_width",
}


REASON_LABELS = {
    "invalid_mmsi": "MMSI 无效",
    "invalid_timestamp": "时间无效",
    "invalid_position": "经纬度缺失或超范围",
    "invalid_speed": "航速缺失或超范围",
    "invalid_course": "对地航向缺失或超范围",
    "invalid_length": "船长为负值",
    "invalid_width": "船宽为负值",
    "duplicate_point": "重复轨迹点",
    "invalid_time_order": "相邻点时间未递增",
    "jump_point": "推算航速超过跳点阈值",
    "acceleration_point": "航速变化率超过阈值",
}


def _ranked_table_sql(config: CleaningConfig) -> str:
    window = "PARTITION BY mmsi, timestamp, lon, lat ORDER BY trajectory_id, source_point_id"
    duplicate_rank = f"row_number() OVER ({window})" if config.remove_exact_duplicates else "1"
    retained_columns = ", ".join(
        f"first_value(source_{column}) OVER ({window}) AS duplicate_retained_{column}"
        if config.remove_exact_duplicates else f"source_{column} AS duplicate_retained_{column}"
        for column in ("point_id", "sheet", "record")
    )
    valid = " AND ".join(VALIDATION_REASONS)
    return f"""
        CREATE OR REPLACE TEMP TABLE cleaning_ranked AS
        SELECT *, {duplicate_rank} AS duplicate_rank, {retained_columns}
        FROM cleaning_base
        WHERE {valid}
    """


def _rejected_sql(filename: str, extra_columns: list[str], config: CleaningConfig) -> str:
    validation_reasons = ", ".join(
        f"CASE WHEN {check} IS NOT TRUE THEN '{reason}' END"
        for check, reason in VALIDATION_REASONS.items()
    )
    any_invalid = " OR ".join(f"{check} IS NOT TRUE" for check in VALIDATION_REASONS)
    extra_sql = "".join(f", {name}" for name in extra_columns)
    filename_sql = filename.replace("'", "''")
    labels = " ".join(f"WHEN '{code}' THEN '{label}'" for code, label in REASON_LABELS.items())
    return f"""
        WITH rejected AS (
            SELECT *, 'validation' AS rejected_stage,
                   concat_ws('|', {validation_reasons}) AS reason_codes
            FROM cleaning_base WHERE {any_invalid}
            UNION ALL BY NAME
            SELECT *, 'duplicate' AS rejected_stage, 'duplicate_point' AS reason_codes
            FROM cleaning_ranked WHERE duplicate_rank > 1
            UNION ALL BY NAME
            SELECT *, 'motion' AS rejected_stage,
                   concat_ws('|',
                       CASE WHEN invalid_time_order THEN 'invalid_time_order' END,
                       CASE WHEN jump_point THEN 'jump_point' END,
                       CASE WHEN acceleration_point THEN 'acceleration_point' END
                   ) AS reason_codes
            FROM cleaning_flagged
            WHERE invalid_time_order OR jump_point OR acceleration_point
        )
        SELECT '{filename_sql}' AS source_file, source_sheet, source_record, source_point_id,
               mmsi, timestamp, lon, lat, speed, course, ship_type, length, width{extra_sql}, trajectory_id,
               rejected_stage, reason_codes,
               array_to_string(list_transform(string_split(reason_codes, '|'),
                   code -> CASE code {labels} END), '；') AS reason_labels,
               source_invalid_values,
               CASE WHEN rejected_stage = 'duplicate' THEN duplicate_retained_point_id END AS duplicate_retained_id,
               CASE WHEN rejected_stage = 'duplicate' THEN duplicate_retained_sheet END AS duplicate_retained_sheet,
               CASE WHEN rejected_stage = 'duplicate' THEN duplicate_retained_record END AS duplicate_retained_record,
               prev_source_point_id, prev_source_sheet, prev_source_record, prev_timestamp, prev_lon, prev_lat, prev_speed,
               time_delta_seconds, distance_nm, implied_speed_knots, acceleration_knots_per_min,
               {config.speed_min} AS speed_min_knots, {config.speed_max} AS speed_max_knots,
               {config.max_jump_speed_knots} AS max_jump_speed_knots,
               {config.max_acceleration_knots_per_min} AS max_acceleration_knots_per_min
        FROM rejected ORDER BY source_point_id
    """


def _flagged_table_sql(config: CleaningConfig) -> str:
    return f"""
        CREATE OR REPLACE TEMP TABLE cleaning_flagged AS
        WITH ordered AS (
            SELECT
                *,
                lag(source_point_id) OVER trajectory_order AS prev_source_point_id,
                lag(source_sheet) OVER trajectory_order AS prev_source_sheet,
                lag(source_record) OVER trajectory_order AS prev_source_record,
                lag(timestamp) OVER (PARTITION BY mmsi ORDER BY timestamp, trajectory_id, lon, lat) AS prev_timestamp,
                lag(lon) OVER (PARTITION BY mmsi ORDER BY timestamp, trajectory_id, lon, lat) AS prev_lon,
                lag(lat) OVER (PARTITION BY mmsi ORDER BY timestamp, trajectory_id, lon, lat) AS prev_lat,
                lag(speed) OVER (PARTITION BY mmsi ORDER BY timestamp, trajectory_id, lon, lat) AS prev_speed,
                lag(course) OVER (PARTITION BY mmsi ORDER BY timestamp, trajectory_id, lon, lat) AS prev_course
            FROM cleaning_unique
            WINDOW trajectory_order AS (PARTITION BY mmsi ORDER BY timestamp, trajectory_id, lon, lat)
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
            count(*) FILTER (WHERE valid_mmsi IS NOT TRUE)::UBIGINT AS invalid_mmsi_rows,
            count(*) FILTER (WHERE valid_timestamp IS NOT TRUE)::UBIGINT AS invalid_timestamp_rows,
            count(*) FILTER (WHERE valid_position IS NOT TRUE)::UBIGINT AS invalid_position_rows,
            count(*) FILTER (WHERE valid_speed IS NOT TRUE)::UBIGINT AS invalid_speed_rows,
            count(*) FILTER (WHERE valid_course IS NOT TRUE)::UBIGINT AS invalid_course_rows,
            count(*) FILTER (WHERE valid_length IS NOT TRUE OR valid_width IS NOT TRUE)::UBIGINT AS invalid_size_rows
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
    duplicate_count = con.execute("SELECT count(*) FROM cleaning_ranked WHERE duplicate_rank > 1").fetchone()[0]
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
