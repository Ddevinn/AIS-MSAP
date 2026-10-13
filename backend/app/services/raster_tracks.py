from __future__ import annotations

import hashlib
import io
import json
import math
import os
import threading
from functools import lru_cache
from pathlib import Path

from PIL import Image, ImageDraw

from backend.app.config import CACHE_DIR
from backend.app.services.analysis import require_dataset
from backend.app.services.dataset import active_parquet_path, read_active_manifest
from backend.app.services.query import QueryFilters, connect, trajectory_column, trajectory_source, where_clause
from backend.app.services.tiles import _validate_tile, tile_bbox_mercator, tile_bbox_wgs84

TILE_SIZE = 256
RENDER_SCALE = 2
TRACK_COLOR = "0f4c81"
TRACK_WIDTH = 1.8
TRACK_OPACITY = 0.86
CACHE_VERSION = "actual-tracks-v6-behavior-segments"
LEGACY_COLOR = "0ea5e9"
LEGACY_OPACITY = 0.72
LEGACY_CACHE_VERSION = "actual-tracks-v3"
CACHE_DIR_PATH = CACHE_DIR / "actual_track_tiles"


def cached_track_tile_png(z: int, x: int, y: int, filters: QueryFilters | None = None) -> bytes | None:
    require_dataset()
    _validate_tile(z, x, y)
    filters = filters or QueryFilters()
    signature = _data_signature()
    key = _tile_key(z, x, y, filters, TRACK_COLOR, TRACK_WIDTH, TRACK_OPACITY, signature)
    disk_path = _disk_path(key)
    if disk_path.exists():
        return disk_path.read_bytes()
    legacy_key = _tile_key(z, x, y, filters, LEGACY_COLOR, TRACK_WIDTH, LEGACY_OPACITY, signature)
    legacy_path = _disk_path(legacy_key, LEGACY_CACHE_VERSION)
    if legacy_path.exists():
        png = _recolor_legacy_tile(legacy_path)
        _write_cache(disk_path, png)
        return png
    return None


def track_tile_png(z: int, x: int, y: int, filters: QueryFilters | None = None) -> bytes:
    filters = filters or QueryFilters()
    cached = cached_track_tile_png(z, x, y, filters)
    if cached is not None:
        return cached
    key = _tile_key(z, x, y, filters, TRACK_COLOR, TRACK_WIDTH, TRACK_OPACITY, _data_signature())
    png = _track_tile_cached(*key)
    _write_cache(_disk_path(key), png)
    return png


def render_track_tile_job(z: int, x: int, y: int, filters: QueryFilters) -> tuple[bytes, int]:
    return track_tile_png(z, x, y, filters), threading.get_ident()


def clear_track_tile_cache() -> None:
    _track_tile_cached.cache_clear()


@lru_cache(maxsize=256)
def _track_tile_cached(
    z: int,
    x: int,
    y: int,
    start_ts: int | None,
    end_ts: int | None,
    lon_min: float | None,
    lon_max: float | None,
    lat_min: float | None,
    lat_max: float | None,
    ship_type: int | None,
    ship_type_min: int | None,
    ship_type_max: int | None,
    mmsi: int | None,
    speed_min: float | None,
    speed_max: float | None,
    spatial_mode: str | None,
    spatial_geometry: str | None,
    behavior: str | None,
    color: str,
    line_width: float,
    opacity: float,
    _signature: str,
) -> bytes:
    filters = QueryFilters(
        start_ts=start_ts,
        end_ts=end_ts,
        lon_min=lon_min,
        lon_max=lon_max,
        lat_min=lat_min,
        lat_max=lat_max,
        ship_type=ship_type,
        ship_type_min=ship_type_min,
        ship_type_max=ship_type_max,
        mmsi=mmsi,
        speed_min=speed_min,
        speed_max=speed_max,
        spatial_mode=spatial_mode,
        spatial_geometry=spatial_geometry,
        behavior=behavior,
    )
    source, source_params = trajectory_source(filters)
    west, south, east, north = tile_bbox_wgs84(z, x, y)
    manifest = read_active_manifest() or {}
    cleaning_config = manifest.get("config", {})
    max_segment_nm = max(0.05, float(cleaning_config.get("max_segment_distance_nm", 3.0)))
    max_gap_seconds = max(1, round(float(cleaning_config.get("max_time_gap_minutes", 60.0)) * 60))
    middle_latitude = math.radians((south + north) / 2)
    lat_pad = max((north - south) * 0.08, max_segment_nm / 60)
    lon_pad = max((east - west) * 0.08, max_segment_nm / (60 * max(math.cos(middle_latitude), 0.1)))
    tile_where, tile_params = where_clause(
        QueryFilters(
            lon_min=west - lon_pad,
            lon_max=east + lon_pad,
            lat_min=south - lat_pad,
            lat_max=north + lat_pad,
        ),
        include_spatial=False,
    )
    track_col = trajectory_column()
    columns = set(manifest.get("columns", []))
    time_delta_select = "time_delta_seconds" if "time_delta_seconds" in columns else "NULL::BIGINT AS time_delta_seconds"
    query = f"""
        SELECT {track_col} AS segment_id, mmsi, timestamp, lon::DOUBLE, lat::DOUBLE,
               {time_delta_select}
        FROM {source}
        {tile_where}
        ORDER BY segment_id, mmsi, timestamp
    """

    size = TILE_SIZE * RENDER_SCALE
    image = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    draw = ImageDraw.Draw(image, "RGBA")
    rgb = tuple(int(color[index:index + 2], 16) for index in (0, 2, 4))
    fill = (*rgb, round(opacity * 255))
    width = max(1, round(line_width * RENDER_SCALE))
    min_x, min_y, max_x, max_y = tile_bbox_mercator(z, x, y)
    current_key: tuple[int, int] | None = None
    path: list[tuple[float, float]] = []
    previous: tuple[int, float, float] | None = None

    def flush() -> None:
        nonlocal path
        if len(path) >= 2:
            draw.line(path, fill=fill, width=width, joint="curve")
        path = []

    with connect() as con:
        con.execute("PRAGMA threads=1")
        cursor = con.execute(query, [*source_params, *tile_params])
        while True:
            rows = cursor.fetchmany(100_000)
            if not rows:
                break
            for segment_id, vessel_id, timestamp, lon, lat, source_time_delta in rows:
                key = (int(segment_id), int(vessel_id))
                if key != current_key:
                    flush()
                    current_key = key
                    previous = None
                if previous is not None:
                    prev_timestamp, prev_lon, prev_lat = previous
                    actual_time_delta = int(timestamp) - prev_timestamp
                    missing_source_points = source_time_delta is not None and actual_time_delta != int(source_time_delta)
                    if missing_source_points or actual_time_delta > max_gap_seconds or _distance_nm(prev_lon, prev_lat, lon, lat) > max_segment_nm:
                        flush()
                path.append(_pixel(float(lon), float(lat), min_x, min_y, max_x, max_y, size))
                previous = (int(timestamp), float(lon), float(lat))
        flush()

    if image.getbbox() is None:
        return _empty_png()
    image = image.resize((TILE_SIZE, TILE_SIZE), Image.Resampling.LANCZOS)
    buffer = io.BytesIO()
    image.save(buffer, format="PNG", optimize=True)
    return buffer.getvalue()


def _pixel(lon: float, lat: float, min_x: float, min_y: float, max_x: float, max_y: float, size: int) -> tuple[float, float]:
    radius = 6_378_137.0
    lat = max(min(lat, 85.05112878), -85.05112878)
    mercator_x = radius * math.radians(lon)
    mercator_y = radius * math.log(math.tan(math.pi / 4 + math.radians(lat) / 2))
    return (
        (mercator_x - min_x) / (max_x - min_x) * size,
        (max_y - mercator_y) / (max_y - min_y) * size,
    )


def _distance_nm(lon1: float, lat1: float, lon2: float, lat2: float) -> float:
    mean_lat = math.radians((lat1 + lat2) / 2)
    return 60.0 * math.hypot((lon2 - lon1) * math.cos(mean_lat), lat2 - lat1)


def _filter_key(filters: QueryFilters) -> tuple[object, ...]:
    return (
        filters.start_ts,
        filters.end_ts,
        filters.lon_min,
        filters.lon_max,
        filters.lat_min,
        filters.lat_max,
        filters.ship_type,
        filters.ship_type_min,
        filters.ship_type_max,
        filters.mmsi,
        filters.speed_min,
        filters.speed_max,
        filters.spatial_mode,
        filters.spatial_geometry,
        filters.behavior,
    )


def _tile_key(
    z: int,
    x: int,
    y: int,
    filters: QueryFilters,
    color: str,
    line_width: float,
    opacity: float,
    signature: str,
) -> tuple[object, ...]:
    return (z, x, y, *_filter_key(filters), color, line_width, opacity, signature)


def _data_signature() -> str:
    path = active_parquet_path()
    stat = path.stat()
    manifest = read_active_manifest() or {}
    return f"{manifest.get('dataset_id')}:{manifest.get('data_role')}:{stat.st_size}:{stat.st_mtime_ns}"


def _disk_path(key: tuple[object, ...], version: str = CACHE_VERSION) -> Path:
    raw = json.dumps((version, key), ensure_ascii=True, separators=(",", ":"), default=str)
    digest = hashlib.sha256(raw.encode("utf-8")).hexdigest()
    return CACHE_DIR_PATH / digest[:2] / f"{digest}.png"


def _recolor_legacy_tile(path: Path) -> bytes:
    with Image.open(path) as source:
        alpha = source.convert("RGBA").getchannel("A")
    alpha = alpha.point(lambda value: min(255, round(value * TRACK_OPACITY / LEGACY_OPACITY)))
    rgb = tuple(int(TRACK_COLOR[index:index + 2], 16) for index in (0, 2, 4))
    image = Image.new("RGBA", (TILE_SIZE, TILE_SIZE), (*rgb, 0))
    image.putalpha(alpha)
    buffer = io.BytesIO()
    image.save(buffer, format="PNG", optimize=True)
    return buffer.getvalue()


def _write_cache(path: Path, png: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(f".{os.getpid()}.tmp")
    temporary.write_bytes(png)
    temporary.replace(path)


def _empty_png() -> bytes:
    buffer = io.BytesIO()
    Image.new("RGBA", (TILE_SIZE, TILE_SIZE), (0, 0, 0, 0)).save(buffer, format="PNG")
    return buffer.getvalue()
