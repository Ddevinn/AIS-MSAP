
"""Offline coastline lookup shared by the map and stop classification."""
from __future__ import annotations

import hashlib
import math
import os
import sqlite3
import struct
import threading
import uuid
from contextlib import closing
from pathlib import Path

import numpy as np
from shapely.geometry import LineString

from backend.app.config import CACHE_DIR, DATA_DIR

REFERENCE_DIR = DATA_DIR / "reference" / "coastline"
INDEX_DIR = CACHE_DIR / "coastline"
INDEX_VERSION = "2"
_INDEX_LOCK = threading.Lock()
_SHAPE_HEADER = struct.Struct("<i4d2i")
_ATTRIBUTION = "© OpenStreetMap contributors"
_METRES_PER_DEGREE = 111_319.49


def coastline_source() -> tuple[Path, str] | None:
    configured = os.getenv("AIS_COASTLINE_PATH")
    if not configured:
        pointer = REFERENCE_DIR / "source.txt"
        if pointer.is_file():
            configured = pointer.read_text(encoding="utf-8-sig").strip()
    path = Path(configured).expanduser() if configured else REFERENCE_DIR / "lines.shp"
    if not path.is_absolute():
        path = REFERENCE_DIR / path
    return source_for_path(path)


def source_for_path(path: Path) -> tuple[Path, str] | None:
    if path.is_dir():
        path = path / "lines.shp"
    if not path.is_file():
        return None
    path = path.resolve()
    stat = path.stat()
    version = hashlib.sha256(f"{INDEX_VERSION}:{path}:{stat.st_size}:{stat.st_mtime_ns}".encode()).hexdigest()[:24]
    return path, version


def coastline_status() -> dict[str, str | bool | None]:
    source = coastline_source()
    return {"available": source is not None, "version": source[1] if source else None, "attribution": _ATTRIBUTION}


def _record_index(path: Path):
    size = path.stat().st_size
    with path.open("rb", buffering=1024 * 1024) as source:
        header = source.read(100)
        if (len(header) != 100 or struct.unpack_from(">i", header)[0] != 9994
                or struct.unpack_from("<i", header, 32)[0] != 3
                or struct.unpack_from(">i", header, 24)[0] * 2 != size):
            raise ValueError("岸线文件须为有效的 WGS84 PolyLine Shapefile。")
        prj = path.with_suffix(".prj").read_text(encoding="utf-8-sig").upper().replace(" ", "")
        if "PROJCS[" in prj or "GEOGCS[" not in prj or not any(name in prj for name in ("WGS_1984", "WGS84", "WGS1984")):
            raise ValueError("岸线坐标系须为 WGS84（EPSG:4326）。")
        offset = 100
        while offset < size:
            record = source.read(8)
            if len(record) != 8:
                raise ValueError("岸线文件记录不完整。")
            length = struct.unpack_from(">i", record, 4)[0] * 2
            end = offset + 8 + length
            if length < 4 or end > size:
                raise ValueError("岸线文件记录长度无效。")
            shape_bytes = source.read(4)
            shape = struct.unpack("<i", shape_bytes)[0]
            if shape:
                if shape != 3 or length < _SHAPE_HEADER.size:
                    raise ValueError("岸线文件含无效线记录。")
                _, west, south, east, north, parts, vertices = _SHAPE_HEADER.unpack(
                    shape_bytes + source.read(_SHAPE_HEADER.size - 4)
                )
                if (not -180 <= west <= east <= 180 or not -90 <= south <= north <= 90
                        or parts < 1 or vertices < 2 * parts
                        or _SHAPE_HEADER.size + 4 * parts + 16 * vertices != length):
                    raise ValueError("岸线文件的边界或顶点数量无效。")
                yield offset + 8, west, east, south, north
            offset = source.seek(end)


def coastline_index(source: tuple[Path, str]) -> Path:
    path, version = source
    index = INDEX_DIR / f"{version}.sqlite3"
    with _INDEX_LOCK:
        if index.is_file():
            return index
        INDEX_DIR.mkdir(parents=True, exist_ok=True)
        stage = index.with_suffix(f".{uuid.uuid4().hex}.tmp")
        try:
            with closing(sqlite3.connect(stage)) as db, db:
                db.execute("CREATE VIRTUAL TABLE coastline USING rtree(offset, west, east, south, north)")
                db.executemany("INSERT INTO coastline VALUES (?, ?, ?, ?, ?)", _record_index(path))
                if not db.execute("SELECT 1 FROM coastline LIMIT 1").fetchone():
                    raise ValueError("岸线文件没有有效线段。")
            try:
                stage.replace(index)
            except PermissionError:
                if not index.is_file():
                    raise
        finally:
            stage.unlink(missing_ok=True)
    return index


class Coastline:
    def __init__(self, source: tuple[Path, str]) -> None:
        self.path = source[0]
        self.index = coastline_index(source)

    def __enter__(self) -> Coastline:
        self._db = sqlite3.connect(self.index.as_uri() + "?mode=ro", uri=True)
        try:
            self._file = self.path.open("rb")
        except OSError:
            self._db.close()
            raise
        return self

    def __exit__(self, *_exc) -> None:
        self._file.close()
        self._db.close()

    def _offsets(self, west: float, south: float, east: float, north: float):
        return self._db.execute(
            "SELECT offset FROM coastline WHERE east >= ? AND north >= ? AND west <= ? AND south <= ?",
            (west, south, east, north),
        )

    def _lines(self, offset: int):
        self._file.seek(offset)
        _, _, _, _, _, parts, vertices = _SHAPE_HEADER.unpack(self._file.read(_SHAPE_HEADER.size))
        starts = struct.unpack(f"<{parts}i", self._file.read(4 * parts))
        points = np.frombuffer(self._file.read(16 * vertices), dtype="<f8").reshape(-1, 2)
        for start, end in zip(starts, (*starts[1:], vertices)):
            if end - start >= 2:
                yield points[start:end]

    def lines(self, west: float, south: float, east: float, north: float):
        for (offset,) in self._offsets(west, south, east, north):
            for points in self._lines(offset):
                yield LineString(points)

    def distance_m(self, lon: float, lat: float, limit_m: float) -> float:
        """Distance to the nearest line within the local search radius, else infinity."""
        latitude = math.radians(lat)
        sin_lat = math.sin(latitude)
        ellipsoid = 1 - 0.00669438 * sin_lat**2
        x_scale = _METRES_PER_DEGREE * math.cos(latitude) / math.sqrt(ellipsoid)
        y_scale = _METRES_PER_DEGREE * (1 - 0.00669438) / ellipsoid**1.5
        dx = min(180.0, limit_m / max(x_scale, 1e-6) * 1.01)
        dy = limit_m / y_scale * 1.01
        windows = [(max(-180, lon - dx), min(180, lon + dx))]
        if lon - dx < -180:
            windows.append((lon - dx + 360, 180))
        if lon + dx > 180:
            windows.append((-180, lon + dx - 360))
        offsets = {
            offset for west, east in windows
            for (offset,) in self._offsets(west, lat - dy, east, lat + dy)
        }
        closest = math.inf
        for offset in sorted(offsets):
            for line in self._lines(offset):
                # Unwrap each short line around its first vertex before projecting near the query.
                # This also keeps segments on the opposite meridian from crossing the query point.
                first = (line[0, 0] - lon + 180) % 360 - 180
                delta = np.degrees(np.unwrap(np.radians(line[:, 0] - line[0, 0])))
                xy = np.column_stack(((first + delta) * x_scale, (line[:, 1] - lat) * y_scale))
                start = xy[:-1]
                edge = xy[1:] - start
                squared = np.einsum("ij,ij->i", edge, edge)
                position = np.divide(-np.einsum("ij,ij->i", start, edge), squared,
                                     out=np.zeros_like(squared), where=squared > 0).clip(0, 1)
                closest = min(closest, float(np.linalg.norm(start + position[:, None] * edge, axis=1).min()))
        return closest if closest <= limit_m else math.inf
