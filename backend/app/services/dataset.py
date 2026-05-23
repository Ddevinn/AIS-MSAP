from __future__ import annotations

import json
import math
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable

import numpy as np
import pandas as pd
import pyarrow as pa
import pyarrow.parquet as pq

from backend.app.config import (
    AIS_COLUMNS,
    CLEANED_PARQUET_PATH,
    CLEANING_MANIFEST_PATH,
    MANIFEST_PATH,
    PARQUET_PATH,
    RAW_PKL_PATH,
)


ProgressCallback = Callable[[dict[str, Any]], None]


def dataset_ready() -> bool:
    return active_parquet_path().exists()


def source_dataset_ready() -> bool:
    return PARQUET_PATH.exists() and MANIFEST_PATH.exists()


def cleaned_dataset_ready() -> bool:
    return CLEANED_PARQUET_PATH.exists() and CLEANING_MANIFEST_PATH.exists()


def active_parquet_path() -> Path:
    return CLEANED_PARQUET_PATH if cleaned_dataset_ready() else PARQUET_PATH


def read_manifest() -> dict[str, Any] | None:
    if not MANIFEST_PATH.exists():
        return None
    return json.loads(MANIFEST_PATH.read_text(encoding="utf-8"))


def read_cleaning_manifest() -> dict[str, Any] | None:
    if not CLEANING_MANIFEST_PATH.exists():
        return None
    return json.loads(CLEANING_MANIFEST_PATH.read_text(encoding="utf-8"))


def read_active_manifest() -> dict[str, Any] | None:
    cleaning_manifest = read_cleaning_manifest()
    if cleaning_manifest:
        return cleaning_manifest
    return read_manifest()


def inspect_pickle(source_path: Path = RAW_PKL_PATH) -> dict[str, Any]:
    obj = pd.read_pickle(source_path)
    if not isinstance(obj, list):
        raise TypeError(f"Expected pickle list, got {type(obj)!r}")

    shapes = [getattr(item, "shape", None) for item in obj]
    valid_shapes = [shape for shape in shapes if shape and len(shape) == 2]
    rows = int(sum(shape[0] for shape in valid_shapes))
    cols = sorted({int(shape[1]) for shape in valid_shapes})
    lengths = [int(shape[0]) for shape in valid_shapes]
    return {
        "source": str(source_path),
        "trajectory_count": len(obj),
        "row_count": rows,
        "columns_per_trajectory": cols,
        "min_trajectory_points": min(lengths) if lengths else 0,
        "max_trajectory_points": max(lengths) if lengths else 0,
        "mean_trajectory_points": float(sum(lengths) / len(lengths)) if lengths else 0,
    }


def build_parquet(
    source_path: Path = RAW_PKL_PATH,
    output_path: Path = PARQUET_PATH,
    force: bool = False,
    progress: ProgressCallback | None = None,
    chunk_rows: int = 750_000,
) -> dict[str, Any]:
    if output_path.exists() and MANIFEST_PATH.exists() and not force:
        manifest = read_manifest()
        if manifest:
            return manifest

    output_path.parent.mkdir(parents=True, exist_ok=True)
    if output_path.exists():
        output_path.unlink()

    started = time.perf_counter()
    obj = pd.read_pickle(source_path)
    if not isinstance(obj, list):
        raise TypeError(f"Expected pickle list, got {type(obj)!r}")

    total_trajectories = len(obj)
    total_rows = 0
    writer: pq.ParquetWriter | None = None
    parts: list[np.ndarray] = []
    trajectory_ids: list[np.ndarray] = []
    pending_rows = 0
    stats = _empty_stats()

    def emit(state: dict[str, Any]) -> None:
        if progress:
            progress(state)

    def flush() -> None:
        nonlocal writer, parts, trajectory_ids, pending_rows, total_rows, stats
        if not parts:
            return
        stacked = np.vstack(parts)
        traj_col = np.concatenate(trajectory_ids).astype(np.int32, copy=False)
        table = _arrow_table(stacked, traj_col)
        if writer is None:
            writer = pq.ParquetWriter(
                output_path,
                table.schema,
                compression="zstd",
                use_dictionary=["mmsi", "ship_type"],
            )
        writer.write_table(table)
        total_rows += stacked.shape[0]
        _update_stats(stats, stacked)
        parts = []
        trajectory_ids = []
        pending_rows = 0

    for trajectory_id, arr in enumerate(obj):
        if not isinstance(arr, np.ndarray):
            arr = np.asarray(arr)
        if arr.ndim != 2 or arr.shape[1] != len(AIS_COLUMNS):
            raise ValueError(
                f"Trajectory {trajectory_id} has shape {arr.shape}; expected Nx{len(AIS_COLUMNS)}"
            )
        parts.append(arr)
        trajectory_ids.append(np.full(arr.shape[0], trajectory_id, dtype=np.int32))
        pending_rows += arr.shape[0]
        if pending_rows >= chunk_rows:
            flush()
            emit(
                {
                    "processed_trajectories": trajectory_id + 1,
                    "total_trajectories": total_trajectories,
                    "processed_rows": total_rows,
                    "elapsed_seconds": round(time.perf_counter() - started, 2),
                }
            )

    flush()
    if writer is not None:
        writer.close()

    manifest = {
        "source": str(source_path),
        "parquet": str(output_path),
        "columns": AIS_COLUMNS + ["trajectory_id"],
        "row_count": int(total_rows),
        "trajectory_count": int(total_trajectories),
        "created_at": datetime.now(timezone.utc).isoformat(),
        "elapsed_seconds": round(time.perf_counter() - started, 2),
        "bounds": {
            "lon_min": _finite_or_none(stats["lon_min"]),
            "lon_max": _finite_or_none(stats["lon_max"]),
            "lat_min": _finite_or_none(stats["lat_min"]),
            "lat_max": _finite_or_none(stats["lat_max"]),
        },
        "time_range": {
            "timestamp_min": _finite_or_none(stats["timestamp_min"]),
            "timestamp_max": _finite_or_none(stats["timestamp_max"]),
        },
        "speed_range": {
            "speed_min": _finite_or_none(stats["speed_min"]),
            "speed_max": _finite_or_none(stats["speed_max"]),
        },
    }
    MANIFEST_PATH.write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")
    return manifest


def _arrow_table(stacked: np.ndarray, trajectory_ids: np.ndarray) -> pa.Table:
    return pa.table(
        {
            "mmsi": _safe_int(stacked[:, 0], np.int64, missing=0),
            "timestamp": _safe_int(stacked[:, 1], np.int64, missing=0),
            "lon": stacked[:, 2].astype(np.float64, copy=False),
            "lat": stacked[:, 3].astype(np.float64, copy=False),
            "course": stacked[:, 4].astype(np.float32, copy=False),
            "speed": stacked[:, 5].astype(np.float32, copy=False),
            "ship_type": _safe_int(stacked[:, 6], np.int16, missing=-1),
            "length": stacked[:, 7].astype(np.float32, copy=False),
            "width": stacked[:, 8].astype(np.float32, copy=False),
            "trajectory_id": trajectory_ids,
        }
    )


def _safe_int(values: np.ndarray, dtype: np.dtype, missing: int) -> np.ndarray:
    return np.nan_to_num(values, nan=missing, posinf=missing, neginf=missing).astype(dtype, copy=False)


def _empty_stats() -> dict[str, float]:
    return {
        "lon_min": math.inf,
        "lon_max": -math.inf,
        "lat_min": math.inf,
        "lat_max": -math.inf,
        "timestamp_min": math.inf,
        "timestamp_max": -math.inf,
        "speed_min": math.inf,
        "speed_max": -math.inf,
    }


def _update_stats(stats: dict[str, float], stacked: np.ndarray) -> None:
    columns = {
        "timestamp": stacked[:, 1],
        "lon": stacked[:, 2],
        "lat": stacked[:, 3],
        "speed": stacked[:, 5],
    }
    for name, values in columns.items():
        finite = values[np.isfinite(values)]
        if finite.size == 0:
            continue
        stats[f"{name}_min"] = min(stats[f"{name}_min"], float(np.min(finite)))
        stats[f"{name}_max"] = max(stats[f"{name}_max"], float(np.max(finite)))


def _finite_or_none(value: float) -> float | None:
    return value if math.isfinite(value) else None
