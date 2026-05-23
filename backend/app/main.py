from __future__ import annotations

import threading
import time
from datetime import datetime
from pathlib import Path
from fastapi import Depends, FastAPI, HTTPException, Query
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, Response
from fastapi.staticfiles import StaticFiles

from backend.app.config import FRONTEND_DIR, MANIFEST_PATH, RAW_PKL_PATH
from backend.app.services.analysis import (
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
    active_parquet_path,
    build_parquet,
    cleaned_dataset_ready,
    dataset_ready,
    inspect_pickle,
    read_active_manifest,
    read_manifest,
    source_dataset_ready,
)
from backend.app.services.exporter import export_excel
from backend.app.services.plots import density_png
from backend.app.services.query import QueryFilters
from backend.app.services.tiles import clear_tile_cache, tracks_mvt


app = FastAPI(title="AIS Offline Cleaning and Visualization")
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

if FRONTEND_DIR.exists():
    app.mount("/static", StaticFiles(directory=FRONTEND_DIR), name="static")

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


def parse_filters(
    start_ts: int | None = Query(default=None),
    end_ts: int | None = Query(default=None),
    lon_min: float | None = Query(default=None),
    lon_max: float | None = Query(default=None),
    lat_min: float | None = Query(default=None),
    lat_max: float | None = Query(default=None),
    ship_type: int | None = Query(default=None),
    mmsi: int | None = Query(default=None),
    speed_min: float | None = Query(default=None),
    speed_max: float | None = Query(default=None),
) -> QueryFilters:
    return QueryFilters(
        start_ts=start_ts,
        end_ts=end_ts,
        lon_min=lon_min,
        lon_max=lon_max,
        lat_min=lat_min,
        lat_max=lat_max,
        ship_type=ship_type,
        mmsi=mmsi,
        speed_min=speed_min,
        speed_max=speed_max,
    )


@app.get("/")
def index() -> FileResponse:
    return FileResponse(FRONTEND_DIR / "index.html")


@app.get("/api/status")
def status() -> dict[str, object]:
    return {
        "dataset_ready": dataset_ready(),
        "source_dataset_ready": source_dataset_ready(),
        "cleaned_dataset_ready": cleaned_dataset_ready(),
        "source_exists": RAW_PKL_PATH.exists(),
        "source_path": str(RAW_PKL_PATH),
        "manifest": read_manifest(),
        "active_manifest": read_active_manifest(),
        "active_parquet": str(active_parquet_path()),
        "cleaning_manifest": read_cleaning_manifest(),
        "build_job": _build_job,
        "clean_job": _clean_job,
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
        if source_dataset_ready() and not force:
            return {"accepted": False, "job": _build_job, "manifest": read_manifest()}

        _build_job.update(
            {
                "status": "running",
                "message": "Building Parquet dataset",
                "started_at": datetime.now().isoformat(timespec="seconds"),
                "finished_at": None,
                "progress": {},
            }
        )
        thread = threading.Thread(target=_run_build, args=(force,), daemon=True)
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

        _clean_job.update(
            {
                "status": "running",
                "message": "Running cleaning rules",
                "started_at": datetime.now().isoformat(timespec="seconds"),
                "finished_at": None,
                "progress": {},
            }
        )
        thread = threading.Thread(target=_run_cleaning, args=(config or default_cleaning_config(),), daemon=True)
        thread.start()
        return {"accepted": True, "job": _clean_job}


@app.post("/api/cleaning/reset")
def api_reset_cleaning() -> dict[str, object]:
    with _clean_lock:
        if _clean_job["status"] == "running":
            return {"accepted": False, "message": "Cleaning is running"}
        reset_cleaning_result()
        clear_tile_cache()
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
def api_speed_histogram(bins: int = 40, filters: QueryFilters = Depends(parse_filters)) -> dict[str, object]:
    return _call(speed_histogram, bins=bins, filters=filters)


@app.get("/api/ship-types")
def api_ship_types(limit: int = 20, filters: QueryFilters = Depends(parse_filters)) -> list[dict[str, object]]:
    return _call(ship_type_distribution, limit=limit, filters=filters)


@app.get("/api/nav-states")
def api_nav_states(filters: QueryFilters = Depends(parse_filters)) -> list[dict[str, object]]:
    return _call(nav_state_distribution, filters=filters)


@app.get("/api/time-series")
def api_time_series(grain: str = "day", filters: QueryFilters = Depends(parse_filters)) -> list[dict[str, object]]:
    return _call(time_series, grain=grain, filters=filters)


@app.get("/api/density.png")
def api_density_png(bins: int = 400, filters: QueryFilters = Depends(parse_filters)) -> Response:
    try:
        return Response(content=density_png(bins=bins, filters=filters), media_type="image/png")
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc)) from exc


@app.get("/api/tiles/tracks/{z}/{x}/{y}.mvt")
def api_track_tiles(
    z: int,
    x: int,
    y: int,
    max_points: int = 30000,
    filters: QueryFilters = Depends(parse_filters),
) -> Response:
    try:
        tile = tracks_mvt(z=z, x=x, y=y, filters=filters, max_points=max_points)
        return Response(
            content=tile,
            media_type="application/vnd.mapbox-vector-tile",
            headers={
                "Cache-Control": "public, max-age=300",
                "Content-Encoding": "identity",
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


def _run_build(force: bool) -> None:
    def progress(state: dict[str, object]) -> None:
        _build_job["progress"] = state

    try:
        manifest = build_parquet(force=force, progress=progress)
        reset_cleaning_result()
        clear_tile_cache()
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
        if MANIFEST_PATH.exists():
            time.sleep(0.1)


def _run_cleaning(config: CleaningConfig) -> None:
    def progress(state: dict[str, object]) -> None:
        _clean_job["progress"] = state
        _clean_job["message"] = str(state.get("message", _clean_job.get("message", "")))

    try:
        manifest = run_cleaning(config=config, progress=progress)
        clear_tile_cache()
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
