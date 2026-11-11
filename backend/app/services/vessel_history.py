from __future__ import annotations

from dataclasses import dataclass
from functools import lru_cache
from zipfile import BadZipFile

import pandas as pd
from openpyxl.utils.exceptions import InvalidFileException
from xlrd import XLRDError

from backend.app.services.dataset import DatasetPaths, dataset_paths, read_manifest
from backend.app.services.ingestion import (
    TABULAR_SUFFIXES, VESSEL_IDENTITY_ALIASES, parse_timestamps, resolve_fields,
    source_chunks, vessel_identity_fields, vessel_identity_values,
)
from backend.app.services.query import QueryFilters, analysis_source, connect, points_sql, trajectory_column
from backend.app.services.raster_tracks import TRACK_POINT_ORDER
from backend.app.services.track_inspection import _paired_points, _track_link, _value, require_track_version


MAX_CHART_POINTS = 6_000


@dataclass(frozen=True)
class VesselPeriod:
    mmsi: int
    start_ts: int
    end_ts: int
    data_version: str


def _vessel_points(filters: QueryFilters) -> tuple[str, list]:
    source, params = analysis_source(filters)
    return (
        f"(SELECT * FROM {source} WHERE timestamp > 0 "
        "AND lon BETWEEN -180 AND 180 AND lat BETWEEN -90 AND 90) AS vessel_points",
        params,
    )


def vessel_period_source(period: VesselPeriod) -> tuple[str, list, dict]:
    manifest = require_track_version(period.data_version)
    source, params = _vessel_points(QueryFilters(mmsi=period.mmsi, start_ts=period.start_ts, end_ts=period.end_ts))
    return source, params, manifest


@lru_cache(maxsize=64)
def _legacy_identity(paths: DatasetPaths, modified_ns: int, mmsi: int, end_ts: int) -> dict:
    # Legacy imports kept the original file but did not copy these optional columns.
    latest = {}
    for _, columns, frame, _ in source_chunks(paths.source_path, paths.csv_encoding, paths.sheet_name):
        identity = vessel_identity_fields(columns)
        if not identity:
            continue
        mapping = resolve_fields(columns)
        selected = frame.loc[pd.to_numeric(frame[mapping["mmsi"]], errors="coerce").eq(mmsi)]
        if selected.empty:
            continue
        times = parse_timestamps(selected[mapping["timestamp"]], paths.source_timezone)
        for field, column in identity.items():
            values = vessel_identity_values(selected[column], field)
            valid = times.between(1, end_ts) & values.notna()
            if valid.any():
                stamp = times[valid].max()
                if field not in latest or stamp >= latest[field][0]:
                    latest[field] = stamp, values[valid & times.eq(stamp)].iloc[-1]
    return {field: value for field, (_, value) in latest.items()}


def _identity(con, mmsi: int, end_ts: int) -> dict:
    manifest = read_manifest() or {}
    columns = set(manifest.get("columns", []))
    fields = [name for name in VESSEL_IDENTITY_ALIASES if name in columns]
    paths = dataset_paths()
    if fields and paths.source_parquet.is_file():
        select = ", ".join(
            f"first({name} ORDER BY timestamp DESC, source_point_id DESC) FILTER (WHERE {name} IS NOT NULL)"
            for name in fields
        )
        values = con.execute(
            f"SELECT {select} FROM {points_sql(paths.source_parquet)} WHERE mmsi = ? AND timestamp BETWEEN 1 AND ?",
            [mmsi, end_ts],
        ).fetchone()
        return dict(zip(fields, values))
    if paths.source_path.suffix.lower() not in TABULAR_SUFFIXES:
        return {}
    mappings = manifest.get("field_mappings", {})
    if mappings and not any(vessel_identity_fields(item.get("columns", [])) for item in mappings.values()):
        return {}
    try:
        return _legacy_identity(paths, paths.source_path.stat().st_mtime_ns, mmsi, end_ts)
    except (OSError, ValueError, BadZipFile, InvalidFileException, XLRDError):
        # The original file can be missing or unreadable while its AIS points remain usable.
        return {}


def vessel_profile(mmsi: int, data_version: str) -> dict | None:
    manifest = require_track_version(data_version)
    columns = set(manifest.get("columns", []))
    source, params = _vessel_points(QueryFilters(mmsi=mmsi))
    heading = "heading::DOUBLE" if "heading" in columns else "NULL::DOUBLE"
    draught = "draught::DOUBLE" if "draught" in columns else "NULL::DOUBLE"
    with connect() as con:
        row = con.execute(f"""
            SELECT timestamp, lon::DOUBLE, lat::DOUBLE, speed::DOUBLE, course::DOUBLE,
                   {heading}, {draught}, min(timestamp) OVER (),
                   first_value(CASE WHEN isfinite(length) AND length > 0 THEN length::DOUBLE END IGNORE NULLS)
                       OVER recent,
                   first_value(CASE WHEN isfinite(width) AND width > 0 THEN width::DOUBLE END IGNORE NULLS)
                       OVER recent
            FROM {source}
            WINDOW recent AS (ORDER BY timestamp DESC, lon, lat ROWS BETWEEN UNBOUNDED PRECEDING AND UNBOUNDED FOLLOWING)
            ORDER BY timestamp DESC, lon, lat
            LIMIT 1
        """, params).fetchone()
        if row is None:
            return None
        end_ts, lon, lat, speed, course, heading_value, draught_value, start_ts, length, width = row
        identity = _identity(con, mmsi, end_ts)
    return {
        "mmsi": mmsi, **dict.fromkeys(VESSEL_IDENTITY_ALIASES), **identity,
        "start_ts": start_ts, "end_ts": end_ts, "lon": lon, "lat": lat,
        "length": length, "width": width,
        "speed": _value(speed, minimum=0, upper=102.3),
        "course": _value(course, minimum=0, upper=360),
        "heading": _value(heading_value, minimum=0, upper=360),
        "draught": _value(draught_value, minimum=0),
    }


def vessel_history(period: VesselPeriod) -> dict:
    source, params, manifest = vessel_period_source(period)
    # Match the map's gap rules, including discontinuities in cleaned trajectories.
    columns = set(manifest.get("columns", []))
    track_col = trajectory_column()
    pairs = _paired_points(source, track_col, columns, "TRUE")
    heading = "heading::DOUBLE" if "heading" in columns else "NULL::DOUBLE"
    segments = []
    samples = []
    count = speed_count = 0
    speed_sum = 0.0
    with connect() as con:
        result = con.execute(f"""
            WITH linked AS (
                SELECT *, ({_track_link(manifest)}) AS linked FROM ({pairs}) AS vessel_pairs
            ), numbered AS (
                SELECT *, sum(CASE WHEN linked THEN 0 ELSE 1 END) OVER (
                    PARTITION BY {track_col} ORDER BY {TRACK_POINT_ORDER} ROWS UNBOUNDED PRECEDING
                ) AS run,
                row_number() OVER (ORDER BY timestamp, {track_col}, lon, lat) AS chart_index,
                count(*) OVER () AS total
                FROM linked
            )
            SELECT timestamp, {track_col}, run, lon::DOUBLE, lat::DOUBLE,
                   speed::DOUBLE, course::DOUBLE, {heading}, chart_index, total
            FROM numbered
            ORDER BY {track_col}, {TRACK_POINT_ORDER}
        """, params)
        previous = None
        while rows := result.fetchmany(10_000):
            for timestamp, track, run, lon, lat, speed, course, heading_value, index, count in rows:
                key = track, run
                if key != previous:
                    segments.append([])
                    previous = key
                segments[-1].append([lon, lat])
                speed = _value(speed, minimum=0, upper=102.3)
                if speed is not None:
                    speed_sum += speed
                    speed_count += 1
                chart_slots = MAX_CHART_POINTS - 1
                if count <= MAX_CHART_POINTS or (
                    (index - 1) * chart_slots // (count - 1) != (index - 2) * chart_slots // (count - 1)
                ):
                    samples.append((index, {
                        "timestamp": timestamp, "lon": lon, "lat": lat, "run": len(segments) - 1,
                        "speed": speed,
                        "course": _value(course, minimum=0, upper=360),
                        "heading": _value(heading_value, minimum=0, upper=360),
                    }))
    return {
        "segments": segments, "point_count": count, "speed_avg": speed_sum / speed_count if speed_count else None,
        "points": [point for _, point in sorted(samples, key=lambda sample: sample[0])],
    }
