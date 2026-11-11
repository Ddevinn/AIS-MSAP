from __future__ import annotations

from dataclasses import dataclass, replace

from backend.app.services.dataset import dataset_paths, read_manifest
from backend.app.services.ingestion import VESSEL_IDENTITY_ALIASES
from backend.app.services.query import QueryFilters, connect, points_sql, trajectory_column, trajectory_source
from backend.app.services.raster_tracks import TRACK_POINT_ORDER
from backend.app.services.track_inspection import _paired_points, _track_link, _value, require_track_version
from backend.app.services.vessel_identity import legacy_vessel_identity


MAX_CHART_POINTS = 6_000


@dataclass(frozen=True)
class VesselPeriod:
    mmsi: int
    start_ts: int
    end_ts: int
    data_version: str


def _vessel_points(mmsi: int, filters: QueryFilters) -> tuple[str, list]:
    if filters.mmsi is not None and filters.mmsi != mmsi:
        return f"(SELECT * FROM {points_sql()} WHERE FALSE) AS vessel_points", []
    source, params = trajectory_source(replace(filters, mmsi=mmsi))
    return (
        f"(SELECT * FROM {source} WHERE timestamp > 0 "
        "AND lon BETWEEN -180 AND 180 AND lat BETWEEN -90 AND 90) AS vessel_points",
        params,
    )


def vessel_period_source(period: VesselPeriod, filters: QueryFilters) -> tuple[str, list, dict]:
    manifest = require_track_version(period.data_version)
    source, params = _vessel_points(period.mmsi, filters)
    # Determine crossings and behavior pairs in the applied scope before narrowing the displayed period.
    return (
        f"(SELECT * FROM {source} WHERE timestamp BETWEEN ? AND ?) AS vessel_period",
        [*params, period.start_ts, period.end_ts],
        manifest,
    )


def _identity(con, mmsi: int, end_ts: int) -> tuple[dict, bool]:
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
        return dict(zip(fields, values)), False
    return legacy_vessel_identity(paths, manifest, mmsi, end_ts)


def vessel_profile(mmsi: int, data_version: str, filters: QueryFilters) -> dict | None:
    manifest = require_track_version(data_version)
    columns = set(manifest.get("columns", []))
    if filters.mmsi is not None and filters.mmsi != mmsi:
        return None
    source, params = _vessel_points(mmsi, filters)
    heading = "heading::DOUBLE" if "heading" in columns else "NULL::DOUBLE"
    draught = "draught::DOUBLE" if "draught" in columns else "NULL::DOUBLE"
    with connect() as con:
        row = con.execute(f"""
            SELECT min(timestamp),
                   first(struct_pack(ts := timestamp, lon := lon::DOUBLE, lat := lat::DOUBLE,
                                     speed := speed::DOUBLE, course := course::DOUBLE,
                                     heading := {heading}, draught := {draught})
                         ORDER BY timestamp DESC, lon, lat),
                   first(length::DOUBLE ORDER BY timestamp DESC, lon, lat) FILTER (WHERE isfinite(length) AND length > 0),
                   first(width::DOUBLE ORDER BY timestamp DESC, lon, lat) FILTER (WHERE isfinite(width) AND width > 0)
            FROM {source}
        """, params).fetchone()
        start_ts, latest, length, width = row
        if latest is None:
            return None
        end_ts = latest["ts"]
        identity, identity_pending = _identity(con, mmsi, end_ts)
    return {
        "mmsi": mmsi, **dict.fromkeys(VESSEL_IDENTITY_ALIASES), **identity, "identity_pending": identity_pending,
        "start_ts": start_ts, "end_ts": end_ts, "lon": latest["lon"], "lat": latest["lat"],
        "length": length, "width": width,
        "speed": _value(latest["speed"], minimum=0, upper=102.3),
        "course": _value(latest["course"], minimum=0, upper=360),
        "heading": _value(latest["heading"], minimum=0, upper=360),
        "draught": _value(latest["draught"], minimum=0),
    }


def vessel_history(period: VesselPeriod, filters: QueryFilters) -> dict:
    source, params, manifest = vessel_period_source(period, filters)
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
