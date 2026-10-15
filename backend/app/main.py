from __future__ import annotations

import asyncio
import os
import threading
import uuid
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime
from contextvars import copy_context
from dataclasses import replace
from pathlib import Path
from fastapi import Depends, FastAPI, File, Form, HTTPException, Query, UploadFile, Request
from fastapi.responses import FileResponse, Response
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from backend.app.config import DATA_DIR, FRONTEND_DIR
from backend.app.auth import install_security, require_admin
from backend.app.context import ScopedJob, dataset_context
from backend.app import storage
from backend.app.services.analysis import (
    behavior_distribution,
    length_distribution,
    nav_state_distribution,
    ship_type_distribution,
    speed_histogram,
    summary,
    time_series,
    track_points,
)
from backend.app.services.cleaning import (
    CleaningConfig,
    default_cleaning_config,
    read_cleaning_manifest,
    reset_cleaning_result,
    run_cleaning,
)
from backend.app.services.dataset import (
    active_dataset_id,
    active_parquet_path,
    build_parquet,
    cleaned_dataset_ready,
    dataset_ready,
    dataset_paths,
    inspect_pickle,
    list_datasets,
    read_active_manifest,
    read_manifest,
    register_dataset,
    set_active_dataset,
    source_dataset_ready,
    unregister_dataset,
)
from backend.app.services.exporter import export_excel
from backend.app.services.directions import direction_histograms, direction_time_series, validate_direction_bin_width
from backend.app.services.density import density_features
from backend.app.services.plots import density_png
from backend.app.services.query import QueryFilters, normalize_behavior, normalize_spatial_filter
from backend.app.services.raster_tracks import (
    cached_track_tile_png,
    clear_track_tile_cache,
    render_track_tile_job,
)


app = FastAPI(title="AIS Offline Cleaning and Visualization")
TRACK_RENDER_WORKERS = min(4, max(2, (os.cpu_count() or 4) // 2))
_track_render_pool: ThreadPoolExecutor | None = None
install_security(app)

if FRONTEND_DIR.exists():
    app.mount("/static", StaticFiles(directory=FRONTEND_DIR), name="static")


def _render_pool() -> ThreadPoolExecutor:
    global _track_render_pool
    if _track_render_pool is None:
        _track_render_pool = ThreadPoolExecutor(max_workers=TRACK_RENDER_WORKERS, thread_name_prefix="track-render")
    return _track_render_pool


@app.on_event("shutdown")
def shutdown_render_pool() -> None:
    global _track_render_pool
    if _track_render_pool is not None:
        _track_render_pool.shutdown(wait=True, cancel_futures=True)
        _track_render_pool = None

_build_lock = threading.Lock()
_build_job = ScopedJob("build")

_clean_lock = threading.Lock()
_clean_job = ScopedJob("clean")
_work_slots = threading.BoundedSemaphore(2)


def _start_job(function, args, job: ScopedJob) -> None:
    if not _work_slots.acquire(blocking=False):
        job.update(status="idle", message="服务器已有两个数据处理任务，请稍后重试。")
        raise HTTPException(429, "服务器正在处理其他任务，请稍后重试。")
    context = copy_context()
    def run():
        try:
            context.run(function, *args)
        finally:
            _work_slots.release()
    threading.Thread(target=run, daemon=True).start()


class DatasetRegisterRequest(BaseModel):
    name: str = ""
    source_path: str
    make_active: bool = True
    source_timezone: str = "Asia/Shanghai"
    csv_encoding: str = "auto"
    sheet_name: str | None = None


def parse_filters(
    start_ts: int | None = Query(default=None),
    end_ts: int | None = Query(default=None),
    lon_min: float | None = Query(default=None),
    lon_max: float | None = Query(default=None),
    lat_min: float | None = Query(default=None),
    lat_max: float | None = Query(default=None),
    ship_type: int | None = Query(default=None),
    ship_type_min: int | None = Query(default=None),
    ship_type_max: int | None = Query(default=None),
    mmsi: int | None = Query(default=None),
    speed_min: float | None = Query(default=None),
    speed_max: float | None = Query(default=None),
    spatial_mode: str | None = Query(default=None),
    spatial_geometry: str | None = Query(default=None),
    behavior: str | None = Query(default=None),
) -> QueryFilters:
    try:
        spatial_mode, spatial_geometry = normalize_spatial_filter(spatial_mode, spatial_geometry)
        behavior = normalize_behavior(behavior)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    return QueryFilters(
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


@app.get("/")
def index() -> FileResponse:
    return FileResponse(FRONTEND_DIR / "index.html")


@app.get("/api/status")
def status() -> dict[str, object]:
    # Capture job completion before reading the files that the job publishes.
    # Otherwise a response can report "done" with pre-completion file flags.
    build_job = _build_job.snapshot()
    clean_job = _clean_job.snapshot()
    paths = dataset_paths() if active_dataset_id() else None
    return {
        "active_dataset_id": active_dataset_id(),
        "datasets": list_datasets(),
        "dataset_ready": dataset_ready(),
        "source_dataset_ready": source_dataset_ready(),
        "cleaned_dataset_ready": cleaned_dataset_ready(),
        "source_exists": paths.source_path.exists() if paths else False,
        "source_path": str(paths.source_path) if paths else None,
        "manifest": read_manifest(),
        "active_manifest": read_active_manifest(),
        "active_parquet": str(active_parquet_path()) if paths else None,
        "cleaning_manifest": read_cleaning_manifest(),
        "build_job": build_job,
        "clean_job": clean_job,
    }


@app.get("/api/datasets")
def api_datasets() -> dict[str, object]:
    return {
        "active_dataset_id": active_dataset_id(),
        "datasets": list_datasets(),
    }


@app.post("/api/datasets/register")
def api_register_dataset(payload: DatasetRegisterRequest, request: Request) -> dict[str, object]:
    require_admin(request)
    try:
        with _build_lock, _clean_lock:
            if _build_job["status"] == "running" or _clean_job["status"] == "running":
                raise HTTPException(status_code=409, detail="任务运行中，完成后再添加数据源。")
            dataset = register_dataset(
                payload.name, payload.source_path, make_active=payload.make_active,
                source_timezone=payload.source_timezone, csv_encoding=payload.csv_encoding, sheet_name=payload.sheet_name,
            )
        clear_track_tile_cache()
        return {
            "accepted": True,
            "dataset": dataset,
            "active_dataset_id": active_dataset_id(),
            "datasets": list_datasets(),
        }
    except HTTPException:
        raise
    except FileNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc)) from exc


@app.post("/api/datasets/preview")
def api_preview_dataset(payload: DatasetRegisterRequest, request: Request) -> dict[str, object]:
    require_admin(request)
    from backend.app.services.ingestion import inspect_tabular
    source = Path(payload.source_path).expanduser().resolve()
    if not source.is_file():
        raise HTTPException(status_code=404, detail="找不到数据文件。")
    try:
        return inspect_tabular(source, payload.source_timezone, payload.csv_encoding, payload.sheet_name)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except Exception as exc:
        raise HTTPException(status_code=400, detail=f"无法读取文件：{exc}") from exc


@app.post("/api/datasets/upload")
async def api_upload_dataset(
    file: UploadFile = File(...), name: str = Form(""),
    source_timezone: str = Form("Asia/Shanghai"), csv_encoding: str = Form("auto"),
) -> dict[str, object]:
    from backend.app.services.ingestion import TABULAR_SUFFIXES, inspect_tabular, validate_options
    filename = Path(file.filename or "").name
    suffix = Path(filename).suffix.lower()
    if suffix not in TABULAR_SUFFIXES | {".pkl", ".pickle"}:
        raise HTTPException(status_code=400, detail="请选择 CSV、Excel（.xlsx/.xls）或 PKL（.pkl/.pickle）文件。")
    try:
        validate_options(source_timezone, csv_encoding)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    context = dataset_context.get()
    directory = DATA_DIR / "uploads" / f"user_{context.owner_id}" / uuid.uuid4().hex
    source = directory / filename
    try:
        directory.mkdir(parents=True, exist_ok=False)
        with source.open("wb") as handle:
            while chunk := await file.read(1024 * 1024):
                handle.write(chunk)
        if suffix in TABULAR_SUFFIXES:
            preview = inspect_tabular(source, source_timezone, csv_encoding)
        else:
            preview = await asyncio.to_thread(inspect_pickle, source_path=source)
        with _build_lock, _clean_lock:
            if _build_job["status"] == "running" or _clean_job["status"] == "running":
                raise HTTPException(status_code=409, detail="任务运行中，完成后再上传数据源。")
            dataset = register_dataset(name or Path(filename).stem, source, source_timezone=source_timezone, csv_encoding=csv_encoding)
        clear_track_tile_cache()
        return {"accepted": True, "dataset": dataset, "preview": preview, "active_dataset_id": active_dataset_id(), "datasets": list_datasets()}
    except Exception as exc:
        if source.exists():
            source.unlink()
        if directory.exists():
            directory.rmdir()
        if isinstance(exc, HTTPException):
            raise
        raise HTTPException(status_code=400, detail=f"导入失败：{exc}") from exc
    finally:
        await file.close()


@app.post("/api/datasets/select/{dataset_id}")
def api_select_dataset(dataset_id: str) -> dict[str, object]:
    with _build_lock, _clean_lock:
        if _build_job["status"] == "running" or _clean_job["status"] == "running":
            raise HTTPException(status_code=409, detail="构建或清洗任务运行中，完成后再切换数据集。")
        try:
            dataset = set_active_dataset(dataset_id)
        except KeyError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        clear_track_tile_cache()
        return {
            "accepted": True,
            "dataset": dataset,
            "active_dataset_id": active_dataset_id(),
            "active_manifest": read_active_manifest(),
        }


@app.post("/api/datasets/clear")
def api_clear_dataset() -> dict[str, object]:
    context = dataset_context.get()
    with storage.connection() as con:
        con.execute("UPDATE sessions SET active_dataset_id=NULL WHERE token_hash=?", (context.session_hash,))
    dataset_context.set(replace(context, dataset_id=None))
    return {"active_dataset_id": None}


@app.delete("/api/datasets/{dataset_id}")
def api_unregister_dataset(dataset_id: str) -> dict[str, object]:
    with _build_lock, _clean_lock:
        if _build_job["status"] == "running" or _clean_job["status"] == "running":
            raise HTTPException(status_code=409, detail="任务运行中，完成后再移除数据源。")
        try:
            new_active_id = unregister_dataset(dataset_id)
        except KeyError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        clear_track_tile_cache()
        return {
            "accepted": True,
            "active_dataset_id": new_active_id,
            "datasets": list_datasets(),
        }


@app.get("/api/inspect")
def inspect() -> dict[str, object]:
    try:
        return inspect_pickle()
    except RuntimeError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc)) from exc


@app.post("/api/build")
def build(force: bool = False) -> dict[str, object]:
    with _build_lock, _clean_lock:
        if _build_job["status"] == "running" or _clean_job["status"] == "running":
            raise HTTPException(409, "当前账号有数据处理任务正在运行。")
        dataset_id = active_dataset_id()
        if not dataset_id:
            raise HTTPException(409, "请先选择历史数据或新增文件。")
        if source_dataset_ready() and not force:
            return {"accepted": False, "job": _build_job.snapshot(), "manifest": read_manifest()}

        _build_job.update(
            {
                "status": "running",
                "message": "Building Parquet dataset",
                "started_at": datetime.now().isoformat(timespec="seconds"),
                "finished_at": None,
                "progress": {},
                "dataset_id": dataset_id,
            }
        )
        _start_job(_run_build, (force, dataset_id), _build_job)
        return {"accepted": True, "job": _build_job.snapshot()}


@app.get("/api/cleaning/defaults")
def api_cleaning_defaults() -> dict[str, object]:
    return default_cleaning_config().model_dump()


@app.get("/api/cleaning/status")
def api_cleaning_status() -> dict[str, object]:
    return {
        "cleaned_dataset_ready": cleaned_dataset_ready(),
        "manifest": read_cleaning_manifest(),
        "job": _clean_job.snapshot(),
    }


@app.post("/api/cleaning/run")
def api_run_cleaning(config: CleaningConfig | None = None) -> dict[str, object]:
    with _build_lock, _clean_lock:
        if _build_job["status"] == "running" or _clean_job["status"] == "running":
            raise HTTPException(409, "当前账号有数据处理任务正在运行。")
        dataset_id = active_dataset_id()
        if not dataset_id or not source_dataset_ready():
            raise HTTPException(409, "请先加载数据。")

        _clean_job.update(
            {
                "status": "running",
                "message": "Running cleaning rules",
                "started_at": datetime.now().isoformat(timespec="seconds"),
                "finished_at": None,
                "progress": {},
                "dataset_id": dataset_id,
            }
        )
        _start_job(_run_cleaning, (config or default_cleaning_config(), dataset_id), _clean_job)
        return {"accepted": True, "job": _clean_job.snapshot()}


@app.post("/api/cleaning/reset")
def api_reset_cleaning() -> dict[str, object]:
    with _build_lock, _clean_lock:
        if _clean_job["status"] == "running" or _build_job["status"] == "running":
            return {"accepted": False, "message": "Cleaning is running"}
        if not active_dataset_id():
            raise HTTPException(409, "请先选择数据。")
        reset_cleaning_result(active_dataset_id())
        clear_track_tile_cache()
        _clean_job.update(
            {
                "status": "idle",
                "message": "Cleaning result removed; active dataset is source Parquet",
                "started_at": None,
                "finished_at": None,
                "progress": {},
            }
        )
        return {"accepted": True, "active_manifest": read_active_manifest()}


@app.get("/api/summary")
def api_summary(filters: QueryFilters = Depends(parse_filters)) -> dict[str, object]:
    return _call(summary, filters)


@app.get("/api/speed-histogram")
def api_speed_histogram(bins: int = 40, bin_width: float | None = Query(default=None), filters: QueryFilters = Depends(parse_filters)) -> dict[str, object]:
    return _call(speed_histogram, bins=bins, bin_width=bin_width, filters=filters)


@app.get("/api/length-histogram")
def api_length_histogram(bins: int = 30, bin_width: float | None = Query(default=None), filters: QueryFilters = Depends(parse_filters)) -> dict[str, object]:
    return _call(length_distribution, bins=bins, bin_width=bin_width, filters=filters)


@app.get("/api/direction-histograms")
def api_direction_histograms(bin_width: int = 15, filters: QueryFilters = Depends(parse_filters)) -> dict[str, object]:
    try:
        validate_direction_bin_width(bin_width)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    return _call(direction_histograms, bin_width=bin_width, filters=filters)


@app.get("/api/direction-time-series")
def api_direction_time_series(
    max_points: int = Query(default=5000, ge=2, le=20000),
    filters: QueryFilters = Depends(parse_filters),
) -> dict[str, object]:
    if filters.mmsi is None:
        raise HTTPException(status_code=422, detail="请先输入船舶编号（MMSI）。")
    return _call(direction_time_series, filters=filters, max_points=max_points)


@app.get("/api/ship-types")
def api_ship_types(limit: int = 20, filters: QueryFilters = Depends(parse_filters)) -> list[dict[str, object]]:
    return _call(ship_type_distribution, limit=limit, filters=filters)


@app.get("/api/nav-states")
def api_nav_states(filters: QueryFilters = Depends(parse_filters)) -> list[dict[str, object]]:
    return _call(nav_state_distribution, filters=filters)


@app.get("/api/behaviors")
def api_behaviors(filters: QueryFilters = Depends(parse_filters)) -> list[dict[str, object]]:
    return _call(behavior_distribution, filters=filters)


@app.get("/api/time-series")
def api_time_series(grain: str = "day", filters: QueryFilters = Depends(parse_filters)) -> list[dict[str, object]]:
    return _call(time_series, grain=grain, filters=filters)


@app.get("/api/density-grid")
def api_density_grid(
    cell_size_m: int = Query(default=250, ge=50, le=20000),
    filters: QueryFilters = Depends(parse_filters),
) -> dict[str, object]:
    return _call(density_features, cell_size_m=cell_size_m, filters=filters)


@app.get("/api/density.png")
def api_density_png(grid_res: float = 0.001, filters: QueryFilters = Depends(parse_filters)) -> Response:
    try:
        return Response(content=density_png(grid_res=grid_res, filters=filters), media_type="image/png")
    except RuntimeError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc)) from exc


@app.get("/api/tiles/tracks/{z}/{x}/{y}.png")
async def api_rendered_track_tiles(
    z: int,
    x: int,
    y: int,
    filters: QueryFilters = Depends(parse_filters),
) -> Response:
    try:
        tile = cached_track_tile_png(z, x, y, filters)
        worker = "cache"
        if tile is None:
            tile, worker_id = await asyncio.get_running_loop().run_in_executor(
                _render_pool(), copy_context().run, render_track_tile_job, z, x, y, filters
            )
            worker = str(worker_id)
        return Response(
            content=tile,
            media_type="image/png",
            headers={
                "Cache-Control": "no-store",
                "X-Track-Render": "cache" if worker == "cache" else "threaded",
                "X-Track-Worker": worker,
                "X-Track-Thread": worker,
                "X-Track-Workers": str(TRACK_RENDER_WORKERS),
            },
        )
    except RuntimeError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc)) from exc


@app.get("/api/track/{mmsi}")
def api_track(mmsi: int, max_points: int = 50000) -> dict[str, object]:
    return _call(track_points, mmsi=mmsi, max_points=max_points)


@app.get("/api/export/excel")
def api_export_excel(direction_bin_width: int = 15, filters: QueryFilters = Depends(parse_filters)) -> FileResponse:
    try:
        validate_direction_bin_width(direction_bin_width)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    try:
        path = export_excel(filters=filters, direction_bin_width=direction_bin_width)
        return FileResponse(path, filename=Path(path).name)
    except RuntimeError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc)) from exc


def _run_build(force: bool, dataset_id: str) -> None:
    def progress(state: dict[str, object]) -> None:
        _build_job["progress"] = state

    try:
        manifest = build_parquet(dataset_id=dataset_id, force=force, progress=progress)
        reset_cleaning_result(dataset_id)
        clear_track_tile_cache()
        _build_job.update(
            {
                "status": "done",
                "message": "Dataset is ready",
                "finished_at": datetime.now().isoformat(timespec="seconds"),
                "progress": {"row_count": manifest.get("row_count"), "elapsed_seconds": manifest.get("elapsed_seconds")},
            }
        )
    except Exception as exc:
        _build_job.update(
            {
                "status": "error",
                "message": str(exc),
                "finished_at": datetime.now().isoformat(timespec="seconds"),
            }
        )


def _run_cleaning(config: CleaningConfig, dataset_id: str) -> None:
    def progress(state: dict[str, object]) -> None:
        _clean_job["progress"] = state
        _clean_job["message"] = str(state.get("message", _clean_job.get("message", "")))

    try:
        manifest = run_cleaning(config=config, progress=progress, dataset_id=dataset_id)
        clear_track_tile_cache()
        _clean_job.update(
            {
                "status": "done",
                "message": "Cleaning finished",
                "finished_at": datetime.now().isoformat(timespec="seconds"),
                "progress": {
                    "stage": "done",
                    "message": "清洗完成",
                    "percent": 100,
                    "row_count": manifest.get("row_count"),
                    "removed_rows": manifest.get("counts", {}).get("removed_rows"),
                    "elapsed_seconds": manifest.get("elapsed_seconds"),
                },
            }
        )
    except Exception as exc:
        _clean_job.update(
            {
                "status": "error",
                "message": str(exc),
                "finished_at": datetime.now().isoformat(timespec="seconds"),
                "progress": {
                    "stage": "error",
                    "message": str(exc),
                    "percent": 0,
                },
            }
        )


def _call(func, *args, **kwargs):
    try:
        return func(*args, **kwargs)
    except RuntimeError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc)) from exc
