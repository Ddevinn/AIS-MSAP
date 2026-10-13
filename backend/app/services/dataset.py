from __future__ import annotations

import json
import math
import re
import threading
import time
from dataclasses import dataclass
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
    DATASET_REGISTRY_PATH,
    DATASETS_DIR,
    MANIFEST_PATH,
    PARQUET_PATH,
    RAW_PKL_PATH,
)


ProgressCallback = Callable[[dict[str, Any]], None]
DEFAULT_DATASET_ID = "default"
_REGISTRY_LOCK = threading.RLock()


@dataclass(frozen=True)
class DatasetPaths:
    dataset_id: str
    name: str
    source_path: Path
    source_parquet: Path
    source_manifest: Path
    cleaned_parquet: Path
    cleaning_manifest: Path
    source_timezone: str = "Asia/Shanghai"
    csv_encoding: str = "auto"
    sheet_name: str | None = None


def dataset_ready(dataset_id: str | None = None) -> bool:
    return active_parquet_path(dataset_id).exists()


def source_dataset_ready(dataset_id: str | None = None) -> bool:
    paths = dataset_paths(dataset_id)
    return paths.source_parquet.exists() and paths.source_manifest.exists()


def cleaned_dataset_ready(dataset_id: str | None = None) -> bool:
    paths = dataset_paths(dataset_id)
    return paths.cleaned_parquet.exists() and paths.cleaning_manifest.exists()


def active_parquet_path(dataset_id: str | None = None) -> Path:
    paths = dataset_paths(dataset_id)
    return paths.cleaned_parquet if cleaned_dataset_ready(dataset_id) else paths.source_parquet


def source_parquet_path(dataset_id: str | None = None) -> Path:
    return dataset_paths(dataset_id).source_parquet


def cleaned_parquet_path(dataset_id: str | None = None) -> Path:
    return dataset_paths(dataset_id).cleaned_parquet


def cleaning_manifest_path(dataset_id: str | None = None) -> Path:
    return dataset_paths(dataset_id).cleaning_manifest


def active_dataset_id() -> str:
    return str(_registry()["active_dataset_id"])


def dataset_paths(dataset_id: str | None = None) -> DatasetPaths:
    registry = _registry()
    resolved_id = dataset_id or str(registry["active_dataset_id"])
    dataset = _dataset_record(resolved_id, registry)
    return _paths_from_record(dataset)


def list_datasets() -> list[dict[str, Any]]:
    registry = _registry()
    active_id = str(registry["active_dataset_id"])
    rows: list[dict[str, Any]] = []
    for dataset_id, dataset in registry["datasets"].items():
        paths = _paths_from_record(dataset)
        manifest = read_manifest(dataset_id)
        cleaning_manifest = read_cleaning_manifest(dataset_id)
        active_manifest = cleaning_manifest or manifest
        rows.append(
            {
                "id": dataset_id,
                "name": dataset.get("name", dataset_id),
                "source_path": str(paths.source_path),
                "source_exists": paths.source_path.exists(),
                "source_dataset_ready": paths.source_parquet.exists() and paths.source_manifest.exists(),
                "cleaned_dataset_ready": paths.cleaned_parquet.exists() and paths.cleaning_manifest.exists(),
                "active": dataset_id == active_id,
                "active_role": "cleaned" if cleaning_manifest else "source",
                "row_count": active_manifest.get("row_count") if active_manifest else None,
                "trajectory_count": active_manifest.get("trajectory_count") if active_manifest else None,
                "created_at": dataset.get("created_at"),
                "parquet": str(paths.source_parquet),
                "cleaned_parquet": str(paths.cleaned_parquet),
            }
        )
    return sorted(rows, key=lambda item: (not item["active"], str(item["name"])))


def register_dataset(
    name: str, source_path: str | Path, make_active: bool = True,
    source_timezone: str = "Asia/Shanghai", csv_encoding: str = "auto", sheet_name: str | None = None,
) -> dict[str, Any]:
    from backend.app.services.ingestion import TABULAR_SUFFIXES, inspect_tabular, validate_options

    source = Path(source_path).expanduser().resolve()
    if not source.is_file():
        raise FileNotFoundError(f"Source file does not exist: {source}")
    if source.suffix.lower() not in TABULAR_SUFFIXES | {".pkl", ".pickle"}:
        raise ValueError("当前支持 CSV、Excel (.xlsx/.xls) 和原有 PKL/PICKLE 文件。")
    validate_options(source_timezone, csv_encoding)
    if source.suffix.lower() in TABULAR_SUFFIXES:
        inspect_tabular(source, source_timezone, csv_encoding, sheet_name)

    registry = _registry()
    dataset_id = _unique_dataset_id(name or source.stem, registry["datasets"])
    dataset_dir = DATASETS_DIR / dataset_id
    dataset = {
        "id": dataset_id,
        "name": name.strip() or source.stem,
        "source_path": str(source),
        "source_parquet": str(dataset_dir / "ais_points.parquet"),
        "source_manifest": str(dataset_dir / "manifest.json"),
        "cleaned_parquet": str(dataset_dir / "ais_points_cleaned.parquet"),
        "cleaning_manifest": str(dataset_dir / "cleaning_manifest.json"),
        "source_timezone": source_timezone,
        "csv_encoding": csv_encoding,
        "sheet_name": sheet_name,
        "created_at": datetime.now(timezone.utc).isoformat(),
    }
    registry["datasets"][dataset_id] = dataset
    if make_active:
        registry["active_dataset_id"] = dataset_id
    _write_registry(registry)
    return _dataset_summary(dataset_id)


def set_active_dataset(dataset_id: str) -> dict[str, Any]:
    registry = _registry()
    _dataset_record(dataset_id, registry)
    registry["active_dataset_id"] = dataset_id
    _write_registry(registry)
    return _dataset_summary(dataset_id)


def unregister_dataset(dataset_id: str) -> str:
    """Remove a dataset registration without deleting any source or generated files."""
    registry = _registry()
    _dataset_record(dataset_id, registry)
    if len(registry["datasets"]) == 1:
        raise ValueError("至少保留一个数据源。")
    del registry["datasets"][dataset_id]
    if registry["active_dataset_id"] == dataset_id:
        registry["active_dataset_id"] = next(iter(registry["datasets"]))
    _write_registry(registry)
    return str(registry["active_dataset_id"])


def read_manifest(dataset_id: str | None = None) -> dict[str, Any] | None:
    paths = dataset_paths(dataset_id)
    path = paths.source_manifest
    if not path.exists():
        return None
    return _with_dataset_meta(json.loads(path.read_text(encoding="utf-8")), paths, "source")


def read_cleaning_manifest(dataset_id: str | None = None) -> dict[str, Any] | None:
    paths = dataset_paths(dataset_id)
    path = paths.cleaning_manifest
    if not path.exists():
        return None
    return _with_dataset_meta(json.loads(path.read_text(encoding="utf-8")), paths, "cleaned")


def read_active_manifest(dataset_id: str | None = None) -> dict[str, Any] | None:
    cleaning_manifest = read_cleaning_manifest(dataset_id)
    if cleaning_manifest:
        return cleaning_manifest
    return read_manifest(dataset_id)


def inspect_pickle(source_path: Path | None = None, dataset_id: str | None = None) -> dict[str, Any]:
    if source_path is None:
        source_path = dataset_paths(dataset_id).source_path
    if source_path.suffix.lower() in {".csv", ".xls", ".xlsx"}:
        from backend.app.services.ingestion import inspect_tabular
        paths = dataset_paths(dataset_id)
        return inspect_tabular(source_path, paths.source_timezone, paths.csv_encoding, paths.sheet_name)
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
    source_path: Path | None = None,
    output_path: Path | None = None,
    manifest_path: Path | None = None,
    dataset_id: str | None = None,
    force: bool = False,
    progress: ProgressCallback | None = None,
    chunk_rows: int = 750_000,
) -> dict[str, Any]:
    paths = dataset_paths(dataset_id)
    source_path = source_path or paths.source_path
    output_path = output_path or paths.source_parquet
    manifest_path = manifest_path or paths.source_manifest

    if output_path.exists() and manifest_path.exists() and not force:
        manifest = read_manifest(dataset_id)
        if manifest:
            return manifest

    if source_path.suffix.lower() in {".csv", ".xls", ".xlsx"}:
        from backend.app.services.ingestion import build_tabular_parquet
        return build_tabular_parquet(
            source_path, output_path, manifest_path, paths.dataset_id, paths.name,
            paths.source_timezone, paths.csv_encoding, paths.sheet_name, progress,
        )

    output_path.parent.mkdir(parents=True, exist_ok=True)
    manifest_path.parent.mkdir(parents=True, exist_ok=True)
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

    try:
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
    finally:
        if writer is not None:
            writer.close()

    manifest = {
        "dataset_id": paths.dataset_id,
        "dataset_name": paths.name,
        "data_role": "source",
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
    manifest_path.write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")
    return manifest


def _registry() -> dict[str, Any]:
    with _REGISTRY_LOCK:
        _ensure_registry_unlocked()
        return json.loads(DATASET_REGISTRY_PATH.read_text(encoding="utf-8"))


def _ensure_registry() -> None:
    with _REGISTRY_LOCK:
        _ensure_registry_unlocked()


def _ensure_registry_unlocked() -> None:
    DATASETS_DIR.mkdir(parents=True, exist_ok=True)
    changed = not DATASET_REGISTRY_PATH.exists()
    if DATASET_REGISTRY_PATH.exists():
        registry = json.loads(DATASET_REGISTRY_PATH.read_text(encoding="utf-8"))
    else:
        registry = {"active_dataset_id": DEFAULT_DATASET_ID, "datasets": {}}

    datasets = registry.setdefault("datasets", {})
    if not datasets:
        changed = True
        datasets[DEFAULT_DATASET_ID] = {
            "id": DEFAULT_DATASET_ID,
            "name": RAW_PKL_PATH.stem,
            "source_path": str(RAW_PKL_PATH),
            "source_parquet": str(PARQUET_PATH),
            "source_manifest": str(MANIFEST_PATH),
            "cleaned_parquet": str(CLEANED_PARQUET_PATH),
            "cleaning_manifest": str(CLEANING_MANIFEST_PATH),
            "created_at": datetime.now(timezone.utc).isoformat(),
        }
    if registry.get("active_dataset_id") not in datasets:
        changed = True
        registry["active_dataset_id"] = DEFAULT_DATASET_ID
    if changed:
        _write_registry(registry)


def _write_registry(registry: dict[str, Any]) -> None:
    # 注意：调用方已持有 _REGISTRY_LOCK（RLock），此处不重复加锁
    DATASET_REGISTRY_PATH.parent.mkdir(parents=True, exist_ok=True)
    # 用 parent / name+".tmp" 而非 with_suffix，避免多后缀在 Windows 的路径解析歧义
    temp_path = DATASET_REGISTRY_PATH.parent / (DATASET_REGISTRY_PATH.name + ".tmp")
    temp_path.write_text(json.dumps(registry, ensure_ascii=False, indent=2), encoding="utf-8")
    temp_path.replace(DATASET_REGISTRY_PATH)


def _dataset_record(dataset_id: str, registry: dict[str, Any] | None = None) -> dict[str, Any]:
    registry = registry or _registry()
    dataset = registry.get("datasets", {}).get(dataset_id)
    if not dataset:
        raise KeyError(f"Unknown dataset_id: {dataset_id}")
    return dataset


def _paths_from_record(dataset: dict[str, Any]) -> DatasetPaths:
    return DatasetPaths(
        dataset_id=str(dataset["id"]),
        name=str(dataset.get("name") or dataset["id"]),
        source_path=Path(dataset["source_path"]),
        source_parquet=Path(dataset["source_parquet"]),
        source_manifest=Path(dataset["source_manifest"]),
        cleaned_parquet=Path(dataset["cleaned_parquet"]),
        cleaning_manifest=Path(dataset["cleaning_manifest"]),
        source_timezone=str(dataset.get("source_timezone", "Asia/Shanghai")),
        csv_encoding=str(dataset.get("csv_encoding", "auto")),
        sheet_name=dataset.get("sheet_name"),
    )


def _dataset_summary(dataset_id: str) -> dict[str, Any]:
    return next(item for item in list_datasets() if item["id"] == dataset_id)


def _with_dataset_meta(manifest: dict[str, Any], paths: DatasetPaths, data_role: str) -> dict[str, Any]:
    manifest = dict(manifest)
    manifest.setdefault("dataset_id", paths.dataset_id)
    manifest.setdefault("dataset_name", paths.name)
    manifest.setdefault("data_role", data_role)
    return manifest


def _unique_dataset_id(name: str, existing: dict[str, Any]) -> str:
    stem = re.sub(r"[^a-zA-Z0-9\u4e00-\u9fff]+", "_", name.strip()).strip("_").lower()
    if not stem:
        stem = "dataset"
    dataset_id = stem
    index = 2
    while dataset_id in existing:
        dataset_id = f"{stem}_{index}"
        index += 1
    return dataset_id


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
