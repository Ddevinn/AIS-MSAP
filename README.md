# AIS Offline Cleaning and Visualization

This application turns `cleaned_trajs_2401.pkl` into an analysis-ready Parquet table, then serves a local dashboard.

## Windows quick start

The current project directory is `D:\AIS系统开发\AIS数据清洗及可视化`.
The Python 3.12 environment has been rebuilt for this computer, and the saved
dataset paths have been migrated to this directory.

Double-click `start_website.bat`, or run the command below from the project
directory. Open `http://127.0.0.1:8000` after the server starts. Keep the server
window open while using the dashboard.

To create an environment on another computer with Python 3.12 installed:

```powershell
python -m venv .venv
& '.\.venv\Scripts\python.exe' -m pip install -r requirements-lock.txt
```

`requirements.txt` lists direct dependencies; `requirements-lock.txt` records
the dependency versions verified on this Windows computer. The map libraries,
chart library, and OpenStreetMap base map currently require an internet connection.

## Current data contract

The pickle file is expected to be a list of NumPy arrays. Each array has 9 columns:

1. `mmsi`
2. `timestamp`
3. `lon`
4. `lat`
5. `course`
6. `speed`
7. `ship_type`
8. `length`
9. `width`

The converter adds `trajectory_id` during Parquet generation.

## Run

Use the project Python environment:

```powershell
& '.\.venv\Scripts\python.exe' -m uvicorn backend.app.main:app --host 127.0.0.1 --port 8000
```

Then open:

```text
http://127.0.0.1:8000
```

The active dataset and its paths are recorded in `data/datasets/registry.json`.
The current dataset's imported points are stored at:

```text
data/datasets/cleaned_trajs_2401/ais_points.parquet
```

After that, API queries read Parquet through DuckDB instead of loading the whole
pickle file. New registered datasets use `data/datasets/<dataset_id>/`. The
`data/processed/` directory contains results from the earlier single-dataset version.

## PNG raster track tiles

The dashboard includes a MapLibre GL JS map. The base map is loaded from OpenStreetMap. The local backend reads every matching AIS point, keeps the original track order, renders dark-blue fixed-style transparent PNG raster tiles, and caches them on disk. Cache misses are distributed across up to four worker threads, so stopping the backend cannot leave orphan Python worker processes. Track data is not sampled or downsampled, and raw coordinates are not sent to the browser:

```text
/api/tiles/tracks/{z}/{x}/{y}.png
```

Supported query filters are the same as the statistics API:

```text
start_ts, end_ts, lon_min, lon_max, lat_min, lat_max, ship_type, mmsi, speed_min, speed_max
```

The map workbench supports point, section, route, and region markers. A region can be an arbitrary polygon, rectangle, or circle. Section and region markers can drive every statistics endpoint and the rendered track tiles through:

```text
spatial_mode=section|region
spatial_geometry=[[lon,lat],...]
```

Section statistics count real intersections between consecutive trajectory segments and the marked section, including forward and reverse crossing totals. Region statistics use point-in-polygon filtering rather than a bounding-box approximation.

Example:

```text
http://127.0.0.1:8000/api/tiles/tracks/12/3302/1813.png
```

## Cleaning module

The dashboard has a configurable cleaning panel. Running cleaning keeps the original imported Parquet and writes a separate active dataset:

```text
data/datasets/cleaned_trajs_2401/ais_points_cleaned.parquet
data/datasets/cleaned_trajs_2401/cleaning_manifest.json
```

Current cleaning rules include:

- MMSI, timestamp, longitude, latitude, speed, course, length, and width validation
- exact duplicate removal
- non-increasing timestamp removal within each MMSI sequence
- jump/drift detection from implied point-to-point speed
- acceleration anomaly removal
- time-gap based segment splitting
- derived motion fields: distance, implied speed, acceleration, turn angle, and navigation state

## Vessel behavior recognition

`GET /api/behaviors` recognizes berthing, anchoring, turning, straight sailing,
and speed changes from consecutive AIS points. It supports the same time,
vessel, speed, ship-type, and spatial filters as the other statistics APIs.
The default rules are: speed at or below 0.5 kn with implied movement at or
below 0.2 kn for berthing; the same reported speed with greater implied
movement for anchoring; course changes of at least 15 degrees for turning;
course changes up to 5 degrees with acceleration below 0.5 kn/min for straight
sailing; and acceleration of at least 0.5 kn/min for speed changes. Motion
rules ignore gaps longer than 10 minutes.

Pass `behavior=berthing|anchoring|turning|straight|speed_change` to the track
tile endpoint to draw only the trajectory portions where the selected behavior
occurs. The map behavior tool applies and clears this filter interactively.

Cleaning API:

```text
GET  /api/cleaning/defaults
GET  /api/cleaning/status
POST /api/cleaning/run
POST /api/cleaning/reset
```

When a cleaned dataset exists, statistics, density plots, single-vessel tracks,
Excel export, and PNG track tiles automatically read the cleaned Parquet.
`POST /api/cleaning/reset` and the "恢复清洗前数据" button remove the current
cleaned Parquet and cleaning manifest, then use the source Parquet.

## Useful scripts

```powershell
& '.\.venv\Scripts\python.exe' scripts\inspect_dataset.py
& '.\.venv\Scripts\python.exe' scripts\build_dataset.py
& '.\.venv\Scripts\python.exe' scripts\check_spatial_filters.py
& '.\.venv\Scripts\python.exe' scripts\check_track_tiles.py
& '.\.venv\Scripts\python.exe' scripts\check_track_multiprocess.py
```
