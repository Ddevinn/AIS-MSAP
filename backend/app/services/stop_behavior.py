
"""Classify complete low-speed stops once per AIS and coastline version."""
from __future__ import annotations

import hashlib
import threading
import uuid
from contextlib import closing
from pathlib import Path

import duckdb
import pyarrow as pa

from backend.app.config import CACHE_DIR
from backend.app.services.coastline import Coastline

STOP_BEHAVIORS = {"berthing", "anchoring"}
BERTH_DISTANCE_M = 100
ANCHOR_DISTANCE_M = 150
STOP_BEHAVIOR_VERSION = "2"
_CACHE_DIR = CACHE_DIR / "stop_behaviors"
_BUILD_LOCK = threading.Lock()


def stop_matches(
    ais_path: Path, points: str, track_column: str, coastline: tuple[Path, str],
) -> Path:
    stat = ais_path.stat()
    key = hashlib.sha256(
        f"{STOP_BEHAVIOR_VERSION}:{ais_path.resolve()}:{stat.st_size}:{stat.st_mtime_ns}:{coastline[1]}".encode()
    ).hexdigest()
    result = _CACHE_DIR / f"{key}.parquet"
    if result.is_file():
        return result
    with _BUILD_LOCK:
        if not result.is_file():
            result.parent.mkdir(parents=True, exist_ok=True)
            stage = result.with_suffix(f".{uuid.uuid4().hex}.tmp")
            try:
                _build_matches(stage, points, track_column, coastline)
                try:
                    stage.replace(result)
                except PermissionError:
                    if not result.is_file():
                        raise
            finally:
                stage.unlink(missing_ok=True)
    return result


def _build_matches(path: Path, points: str, track_column: str, coastline: tuple[Path, str]) -> None:
    # Longitude differences are wrapped before estimating local distances.
    with closing(duckdb.connect(config={"threads": 2})) as db:
        db.execute(f"""
            CREATE TEMP TABLE stop_points AS
            WITH positions AS (
                SELECT mmsi, {track_column} AS track_id, timestamp,
                       lon::DOUBLE AS lon, lat::DOUBLE AS lat, speed
                FROM {points}
                WHERE timestamp IS NOT NULL AND lon BETWEEN -180 AND 180 AND lat BETWEEN -90 AND 90
                QUALIFY row_number() OVER (
                    PARTITION BY mmsi, {track_column}, timestamp, lon, lat
                    ORDER BY speed DESC NULLS FIRST
                ) = 1
            ), ordered AS (
                SELECT *, lag(timestamp) OVER w AS prev_time, lag(speed) OVER w AS prev_speed,
                       lag(lon) OVER w AS prev_lon, lag(lat) OVER w AS prev_lat,
                       count(*) OVER (PARTITION BY mmsi, track_id, timestamp) AS same_time
                FROM positions
                WINDOW w AS (PARTITION BY mmsi, track_id ORDER BY timestamp, lon, lat)
            ), numbered AS (
                SELECT *, sum(CASE WHEN prev_time IS NULL OR timestamp - prev_time NOT BETWEEN 1 AND 900
                    OR speed NOT BETWEEN 0 AND 0.5 OR prev_speed NOT BETWEEN 0 AND 0.5
                    OR speed IS NULL OR prev_speed IS NULL OR same_time > 1
                    OR lag_conflict
                    OR 111319.49 * sqrt(
                        pow((((lon - prev_lon + 540) % 360) - 180) * cos(radians((lat + prev_lat) / 2)), 2)
                        + pow(lat - prev_lat, 2)) > 300
                    THEN 1 ELSE 0 END
                ) OVER (PARTITION BY mmsi, track_id ORDER BY timestamp, lon, lat ROWS UNBOUNDED PRECEDING) AS stop_id
                FROM (
                    SELECT *, lag(same_time, 1, 1) OVER (
                        PARTITION BY mmsi, track_id ORDER BY timestamp, lon, lat
                    ) > 1 AS lag_conflict
                    FROM ordered
                )
            )
            SELECT mmsi, track_id, timestamp, lon, lat, stop_id FROM numbered
            WHERE speed BETWEEN 0 AND 0.5 AND same_time = 1
        """)
        samples = db.execute("""
            WITH qualified AS (
                SELECT mmsi, track_id, stop_id
                FROM stop_points GROUP BY ALL
                HAVING count(*) >= 5 AND max(timestamp) - min(timestamp) >= 900
            ), sampled AS (
                SELECT p.*, first_value(lon) OVER stop_order AS first_lon,
                       first_value(lat) OVER stop_order AS first_lat
                FROM stop_points p JOIN qualified USING (mmsi, track_id, stop_id)
                WINDOW stop_order AS (PARTITION BY mmsi, track_id, stop_id ORDER BY timestamp)
                QUALIFY row_number() OVER (
                    PARTITION BY mmsi, track_id, stop_id, timestamp // 30 ORDER BY timestamp
                ) = 1
            ), local AS (
                SELECT *,
                       (((lon - first_lon + 540) % 360) - 180)
                       * cos(radians(first_lat)) * 111319.49 AS x,
                       (lat - first_lat) * 111319.49 AS y
                FROM sampled
            ), centers AS (
                SELECT mmsi, track_id, stop_id, median(x) AS center_x, median(y) AS center_y,
                       median(((lon - first_lon + 540) % 360) - 180) AS center_lon_offset,
                       first(first_lon) AS first_lon, median(lat) AS center_lat,
                       first(x ORDER BY timestamp DESC) AS last_x, first(y ORDER BY timestamp DESC) AS last_y
                FROM local GROUP BY mmsi, track_id, stop_id
            )
            SELECT c.mmsi, c.track_id, c.stop_id,
                   ((first(c.first_lon + c.center_lon_offset) + 540) % 360) - 180 AS lon,
                   first(c.center_lat),
                   quantile_cont(sqrt(pow(l.x - c.center_x, 2) + pow(l.y - c.center_y, 2)), 0.9) AS radius,
                   sqrt(pow(first(c.last_x), 2) + pow(first(c.last_y), 2)) AS displacement
            FROM centers c JOIN local l USING (mmsi, track_id, stop_id)
            GROUP BY c.mmsi, c.track_id, c.stop_id
        """).fetchall()

        matches = []
        with Coastline(coastline) as shore:
            for mmsi, track_id, stop_id, lon, lat, radius, displacement in samples:
                # Reject slow passage and extended drifting before considering the coast.
                if radius > 300 or displacement > max(40, radius):
                    continue
                distance = shore.distance_m(lon, lat, ANCHOR_DISTANCE_M)
                if distance <= BERTH_DISTANCE_M and radius <= 35 and displacement <= 40:
                    matches.append((mmsi, track_id, stop_id, "berthing"))
                elif distance >= ANCHOR_DISTANCE_M and radius >= 15:
                    matches.append((mmsi, track_id, stop_id, "anchoring"))

        names = ("mmsi", "track_id", "stop_id", "stop_behavior")
        values = zip(*matches) if matches else ((), (), (), ())
        labels = pa.table({
            name: pa.array(column, type=dtype)
            for name, column, dtype in zip(names, values, (pa.int64(), pa.int64(), pa.int64(), pa.string()))
        })
        db.register("stop_labels", labels)
        db.execute("""
            COPY (
                SELECT p.*, l.stop_behavior FROM stop_points p
                JOIN stop_labels l USING (mmsi, track_id, stop_id)
            ) TO ? (FORMAT PARQUET)
        """, [str(path)])
