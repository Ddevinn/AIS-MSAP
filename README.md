# AIS Offline Cleaning and Visualization

This scaffold turns `cleaned_trajs_2401.pkl` into an analysis-ready Parquet table, then serves a lightweight local dashboard.

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
& 'D:\Miniforge3\envs\ais_agent_env\python.exe' -m uvicorn backend.app.main:app --host 127.0.0.1 --port 8000
```

Then open:

```text
http://127.0.0.1:8000
```

The first build converts the pickle file to:

```text
data/processed/ais_points.parquet
```

After that, API queries read Parquet through DuckDB instead of loading the whole pickle file.

## Dynamic map tiles

The dashboard includes a MapLibre GL JS map. The base map is loaded from OpenStreetMap raster tiles, while AIS tracks are served by the local backend as Mapbox Vector Tiles:

```text
/api/tiles/tracks/{z}/{x}/{y}.mvt
```

Supported query filters are the same as the statistics API:

```text
start_ts, end_ts, lon_min, lon_max, lat_min, lat_max, ship_type, mmsi, speed_min, speed_max
```

Example:

```text
http://127.0.0.1:8000/api/tiles/tracks/12/3302/1813.mvt?max_points=20000
```

Do not open the `{z}/{x}/{y}` template URL directly in the browser. MapLibre replaces those placeholders with real tile coordinates while the map is running.

## Cleaning module

The dashboard has a configurable cleaning panel. Running cleaning keeps the original imported Parquet and writes a separate active dataset:

```text
data/processed/ais_points_cleaned.parquet
data/processed/cleaning_manifest.json
```

Current cleaning rules include:

- MMSI, timestamp, longitude, latitude, speed, course, length, and width validation
- exact duplicate removal
- non-increasing timestamp removal within each MMSI sequence
- jump/drift detection from implied point-to-point speed
- acceleration anomaly removal
- time-gap based segment splitting
- derived motion fields: distance, implied speed, acceleration, turn angle, and navigation state

Cleaning API:

```text
GET  /api/cleaning/defaults
GET  /api/cleaning/status
POST /api/cleaning/run
POST /api/cleaning/reset
```

When a cleaned dataset exists, statistics, density plots, single-vessel tracks, Excel export, and MVT map tiles automatically read the cleaned Parquet. Use `POST /api/cleaning/reset` or the "使用原始数据" button to switch back to the source Parquet.

## Useful scripts

```powershell
& 'D:\Miniforge3\envs\ais_agent_env\python.exe' scripts\inspect_dataset.py
& 'D:\Miniforge3\envs\ais_agent_env\python.exe' scripts\build_dataset.py
```
