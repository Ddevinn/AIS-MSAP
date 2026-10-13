from __future__ import annotations

import asyncio
import os
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime
from pathlib import Path
from fastapi import Depends, FastAPI, HTTPException, Query
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, Response
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from backend.app.config import FRONTEND_DIR
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
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

if FRONTEND_DIR.exists():
    app.mount("/static", StaticFiles(directory=FRONTEND_DIR), name="static")


def _render_pool() -> ThreadPoolExecutor:
    global _track_render_pool
    if _track_render_pool is None:
        _track_render_pool = ThreadPoolExecutor(max_workers=TRACK_RENDER_WORKERS, thread_name_prefix="track-render")
    return _track_render_pool


@app.on_event("shutdown")
def shutdown_render_pool() -> None:
    if _track_render_pool is not None:
        _track_render_pool.shutdown(wait=True, cancel_futures=True)

_build_lock = threading.Lock()
_build_job: dict[str, object] = {
    "status": "idle",
    "message": "",
    "started_at": None,
    "finished_at": None,
    "progress": {},
}

_clean_lock = threading.Lock()
_clean_job: dict[str, object] = {
    "status": "idle",
    "message": "",
    "started_at": None,
    "finished_at": None,
    "progress": {},
}


class DatasetRegisterRequest(BaseModel):
    name: str = ""
    source_path: str
    make_active: bool = True


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
    paths = dataset_paths()
    return {
        "active_dataset_id": active_dataset_id(),
        "datasets": list_datasets(),
        "dataset_ready": dataset_ready(),
        "source_dataset_ready": source_dataset_ready(),
        "cleaned_dataset_ready": cleaned_dataset_ready(),
        "source_exists": paths.source_path.exists(),
        "source_path": str(paths.source_path),
        "manifest": read_manifest(),
        "active_manifest": read_active_manifest(),
        "active_parquet": str(active_parquet_path()),
        "cleaning_manifest": read_cleaning_manifest(),
        "build_job": _build_job,
        "clean_job": _clean_job,
    }


@app.get("/api/datasets")
def api_datasets() -> dict[str, object]:
    return {
        "active_dataset_id": active_dataset_id(),
        "datasets": list_datasets(),
    }


@app.post("/api/datasets/register")
def api_register_dataset(payload: DatasetRegisterRequest) -> dict[str, object]:
    try:
        dataset = register_dataset(payload.name, payload.source_path, make_active=payload.make_active)
        clear_track_tile_cache()
        return {
            "accepted": True,
            "dataset": dataset,
            "active_dataset_id": active_dataset_id(),
            "datasets": list_datasets(),
        }
    except FileNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc)) from exc


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
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc)) from exc


@app.post("/api/build")
def build(force: bool = False) -> dict[str, object]:
    with _build_lock:
        if _build_job["status"] == "running":
            return {"accepted": False, "job": _build_job}
        dataset_id = active_dataset_id()
        if source_dataset_ready() and not force:
            return {"accepted": False, "job": _build_job, "manifest": read_manifest()}

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
        thread = threading.Thread(target=_run_build, args=(force, dataset_id), daemon=True)
        thread.start()
        return {"accepted": True, "job": _build_job}


@app.get("/api/cleaning/defaults")
def api_cleaning_defaults() -> dict[str, object]:
    return default_cleaning_config().model_dump()


@app.get("/api/cleaning/status")
def api_cleaning_status() -> dict[str, object]:
    return {
        "cleaned_dataset_ready": cleaned_dataset_ready(),
        "manifest": read_cleaning_manifest(),
        "job": _clean_job,
    }


@app.post("/api/cleaning/run")
def api_run_cleaning(config: CleaningConfig | None = None) -> dict[str, object]:
    with _clean_lock:
        if _clean_job["status"] == "running":
            return {"accepted": False, "job": _clean_job}
        dataset_id = active_dataset_id()

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
        thread = threading.Thread(target=_run_cleaning, args=(config or default_cleaning_config(), dataset_id), daemon=True)
        thread.start()
        return {"accepted": True, "job": _clean_job}


@app.post("/api/cleaning/reset")
def api_reset_cleaning() -> dict[str, object]:
    with _clean_lock:
        if _clean_job["status"] == "running":
            return {"accepted": False, "message": "Cleaning is running"}
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


@app.get("/api/density.png")
def api_density_png(grid_res: float = 0.001, filters: QueryFilters = Depends(parse_filters)) -> Response:
    try:
        return Response(content=density_png(grid_res=grid_res, filters=filters), media_type="image/png")
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
                _render_pool(), render_track_tile_job, z, x, y, filters
            )
            worker = str(worker_id)
        return Response(
            content=tile,
            media_type="image/png",
            headers={
                "Cache-Control": "public, max-age=86400, immutable",
                "X-Track-Render": "cache" if worker == "cache" else "threaded",
                "X-Track-Worker": worker,
                "X-Track-Thread": worker,
                "X-Track-Workers": str(TRACK_RENDER_WORKERS),
            },
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc)) from exc


@app.get("/api/track/{mmsi}")
def api_track(mmsi: int, max_points: int = 50000) -> dict[str, object]:
    return _call(track_points, mmsi=mmsi, max_points=max_points)


@app.get("/api/export/excel")
def api_export_excel(filters: QueryFilters = Depends(parse_filters)) -> FileResponse:
    try:
        path = export_excel(filters=filters)
        return FileResponse(path, filename=Path(path).name)
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
    finally:
        if source_dataset_ready(dataset_id):
            time.sleep(0.1)


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
