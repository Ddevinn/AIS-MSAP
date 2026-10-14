# AIS Offline Cleaning and Visualization

This application imports Excel/CSV AIS tables and legacy PKL trajectories into
an analysis-ready Parquet table, then serves a local dashboard.

## Accounts, history and administration

Open `http://127.0.0.1:8000` after starting the server. On first use, the
local-only `/setup` page creates the initial super administrator with a password
you choose (at least 10 characters). There is no built-in password. Existing
registrations in `data/datasets/registry.json` are copied into this administrator's
history without moving data files or rewriting the legacy registry.

- `/login`: sign in to the analysis workbench.
- `/admin/login` and `/admin`: super administrator user management.
- `/account/password`: change your own password; this ends existing sessions.
- Each new login opens with no selected dataset. Refreshing the same session
  preserves its selection. Use **历史数据** to reuse a processed file or
  **新增文件** to upload CSV/Excel. Imports and last-use timestamps persist per
  account. History removal removes the registration, retaining disk files.
- Ordinary users upload files. Importing a server-local path, including legacy
  PKL, requires super administrator permission.
- The administration page supports account creation, roles, enable/disable,
  password resets, login/access records, session revocation and operation logs.
  New accounts use temporary passwords and must change them before analysis.
  Password resets default to the same requirement. Passwords are Argon2 hashes;
  plaintext passwords and hashes are not returned by the administration API.
- Login records start when this feature is enabled. Times display in
  `Asia/Shanghai`. Effective sessions last 12 hours; activity timestamps update
  at most once every 30 seconds and do not prove a user is currently online.

Accounts, sessions, owned registrations, job status and audit records are stored
in `data/private/platform.sqlite3` (override with `AIS_AUTH_DB`). This database,
uploads, AIS data, generated outputs and previews are excluded from Git. Back
up the private database together with the data files to preserve users/history;
the legacy JSON registry is no longer the web application's source of truth.
Dataset selection is scoped to a login session, and jobs/caches/exports to the
account and dataset. Up to two data processing jobs run concurrently; each
account runs one at a time. Interrupted jobs are marked after a server restart.

All functional API endpoints require a session. Mutating authenticated requests
also require the CSRF token returned by `/api/auth/me` in `X-CSRF-Token`; the web
interface handles this automatically. Deployment uses the same origin for
frontend and backend. HTTPS automatically enables Secure cookies.

Install dependencies after updating the checkout:

```powershell
& '.\.venv\Scripts\python.exe' -m pip install -r requirements-lock.txt
# HTTP integration tests additionally need:
& '.\.venv\Scripts\python.exe' -m pip install -r requirements-dev.txt
```

Then start `start_dev.bat` in the server's own PowerShell window. Account HTTP
tests use temporary databases and small synthetic datasets; they do not create
production accounts or change the production registry.

## Excel and CSV import

In **加载数据 → 新增文件**, use **选择 Excel / CSV 文件** to upload a file.
Super administrators may also enter a local file path and use **预览字段映射**,
then **添加这个文件**.
Click **加载数据** to build the normalized dataset; cleaning remains a separate action.

- Supported tables: `.csv`, `.xlsx`, `.xls`. Empty Excel worksheets are ignored;
  nonempty worksheets must have a header row and compatible AIS fields.
- Required fields: MMSI, time, longitude, latitude, speed, and course over ground.
- Optional fields: ship type, length, width, draught, and true heading.
- Matching uses header names and aliases, ignoring case, whitespace, underscores,
  and punctuation. Column order does not matter. Ambiguous aliases or missing
  required columns produce an error rather than guessing.

| Standard field | Customer sample | Other recognized names |
|---|---|---|
| `mmsi` | `mmsi` | `MMSI`, `vessel_mmsi`, `船舶编号` |
| `timestamp` | `updatetime` | `update_time`, `datetime`, `时间`, `更新时间` |
| `lon` / `lat` | `lon` / `lat` | `longitude` / `latitude`, `经度` / `纬度` |
| `speed` | `speed` | `SOG`, `speed_over_ground`, `航速` |
| `course` | `course` | `COG`, `course_over_ground`, `对地航向` |
| `ship_type` | `type` | `shiptype`, `vessel_type`, `船型` |
| `length` / `width` | `length` / `width` | `ship_length` / `beam`, `船长` / `船宽` |
| `draught` | `draught` | `draft`, `吃水` |
| `heading` | `heading` | `true_heading`, `船首向` |

Date strings without a time zone use the selected import time zone, defaulting to
`Asia/Shanghai` as confirmed for the customer's sample. Offset-bearing date
strings retain their actual instant. Seconds, milliseconds, microseconds and
nanoseconds since the Unix epoch are converted to integer Unix seconds.
`course` and `heading` remain separate, and `draught`/`heading` survive cleaning.
Speeds are assumed to be knots, angles degrees, and length/width/draught metres.

CSV supports UTF-8 with/without BOM and GB18030/GBK. Automatic detection can be
overridden in the import panel. `NULL`, `-`, empty cells and other common missing
markers remain missing; unknown ship types use `-1`. Other unmapped columns are
listed in preview metadata, and the original source file remains intact.

The normalized manifest records source format, time zone, field mappings and
numeric/date conversion errors. Invalid required values can be removed by the
existing cleaning step. This import feature does not perform interpolation.

## Git management and rollback

Repository: https://github.com/Ddevinn/AIS-MSAP

The tag `baseline-before-tabular-import-20261013` preserves the working platform
before Excel/CSV changes. To compare code with that baseline:

```powershell
git diff baseline-before-tabular-import-20261013 HEAD
```

To restore baseline code, save any current uncommitted work first, then create a
branch from the tag:

```powershell
git switch -c rollback-original baseline-before-tabular-import-20261013
```

Runtime datasets, registrations, manifests, uploads, exports, caches, virtual
environments and credentials are kept locally and excluded from new code
commits. The baseline tag retains the original tracked manifest/registry files.

## Validation

```powershell
& '.\.venv\Scripts\python.exe' -m unittest discover -s tests -v
```

To also verify the customer's Excel file and its equivalent CSV locally:

```powershell
$env:AIS_SAMPLE_XLSX = 'D:\AIS系统开发\样例数据.xlsx'
& '.\.venv\Scripts\python.exe' -m unittest discover -s tests -v
```

## Windows quick start

The current project directory is `D:\AIS系统开发\AIS数据清洗及可视化`.
The Python 3.12 environment has been rebuilt for this computer, and the saved
dataset paths have been migrated to this directory.

Double-click `start_website.bat`, or run `start_dev.bat` in an independent
PowerShell window. Open `http://127.0.0.1:8000` after the server starts. Keep
this window open while using the dashboard; Ctrl+C stops the service.

```powershell
Set-Location -LiteralPath 'D:\AIS系统开发\AIS数据清洗及可视化'
.\start_dev.bat
```

The launcher enables Uvicorn auto-reload and watches only `backend` Python
files. Routine backend edits automatically restart the application process
while the reload supervisor keeps the development port. Frontend changes
need a browser refresh (Ctrl+F5 if old assets are cached). Data, exports and
test files are outside the watched directory. New dependencies, environment
variables and launch-option changes still require a manual restart.

The batch entry point uses the project's Python environment directly; no
PowerShell script-policy or system permission changes are needed.

An already-running server started without `--reload` needs a one-time Ctrl+C
in its owner terminal before using this launcher. The launcher reports an
occupied port without stopping another process or changing its permissions.
Codex updates code and runs checks; the independent terminal owns the service.

Reload restarts in-process build/cleaning jobs and clears in-memory job state.
Finish long jobs before editing backend files, or use a stable session:

```powershell
.\start_dev.bat --no-reload
```

To create an environment on another computer with Python 3.12 installed:

```powershell
python -m venv .venv
& '.\.venv\Scripts\python.exe' -m pip install -r requirements-lock.txt
```

`requirements.txt` lists direct dependencies; `requirements-lock.txt` records
the dependency versions verified on this Windows computer. The map libraries,
chart library, and OpenStreetMap base map currently require an internet connection.

## Legacy PKL data contract

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
.\start_dev.bat
```

Then open:

```text
http://127.0.0.1:8000
```

Web dataset registrations and session selections are recorded in the private
SQLite database. Legacy command-line tools still use `data/datasets/registry.json`.
An existing dataset's imported points are stored at:

```text
data/datasets/cleaned_trajs_2401/ais_points.parquet
```

After that, API queries read Parquet through DuckDB instead of loading the whole
pickle file. New web datasets use `data/datasets/user_<user_id>/<dataset_id>/`. The
`data/processed/` directory contains results from the earlier single-dataset version.

## PNG raster track tiles

The OpenStreetMap basemap and AIS track tiles have independent loading states.
The page sends an origin-only cross-origin Referer, as required for OSM browser
tile requests. **重新加载底图** reloads visible basemap tiles without changing the
dataset, filters or map markers. Transient basemap failures have at most two
automatic retries; policy/rate-limit rejections retain a visible recovery hint.
Tile URLs and normal browser caching are preserved during retries.

The dashboard includes a MapLibre GL JS map. The base map is loaded from OpenStreetMap. The local backend reads every matching AIS point, keeps the original track order, renders fixed-style transparent PNG raster tiles, and caches them on disk. The map applies a 180-degree hue rotation and increased saturation and opacity to display the tracks in high-contrast orange against the blue sea; these display changes take effect on page refresh without restarting the backend or rebuilding cached tiles. Cache misses are distributed across up to four worker threads, so stopping the backend cannot leave orphan Python worker processes. Track data is not sampled or downsampled, and raw coordinates are not sent to the browser:

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

## Spatial density overlay

The map now opens with an AIS activity-density overlay. Use **空间密度** and
**船舶轨迹** on the map to toggle the layers independently. The **空间密度设置**
tool selects smooth heatmap or count-grid rendering, approximate ground cell
size (100 m–5 km), and opacity. Blue–teal–yellow encodes increasing activity;
empty cells are transparent. The legend shows the current scope and counts.
Click an occupied cell (or press Enter at the keyboard-navigated map center)
to inspect its AIS-point and distinct-vessel counts.

`GET /api/density-grid?cell_size_m=250` returns geographic cell polygons and
metadata. All matching AIS observations are aggregated without point sampling.
Grid origins and the dataset reference latitude stay fixed during filtering
and zooming. Ground dimensions are approximate at that reference latitude.
If more than 20,000 occupied cells would be returned, the grid is coarsened
and its actual size is displayed; no cells or observations are silently dropped.
Only finite coordinates within the Web Mercator basemap extent are counted.

Time, vessel, ship-type, speed, region and behavior filters apply. A section
uses the complete selected crossing tracks, matching the map, and is labeled
accordingly. Behavior density counts recognized points, without adding the
preceding segment endpoint. Grid color uses a logarithmic point-count scale;
the heatmap smooths cell centers using logarithmically compressed count weights
and labels the result as relative heat, not absolute counts. AIS point counts
depend on reporting frequency and do not represent vessel abundance.

```powershell
& '.\.venv\Scripts\python.exe' -m unittest tests.test_density
```

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

## Course and true-heading analysis

The dashboard shows separate distributions for course over ground (`course`,
COG) and true heading (`heading`). Both share the existing vessel, time, speed,
ship-type and water-area filters. The two bin selectors stay synchronized
(15°, 30°, 45° or 90°); statistics include valid points, missing/unavailable
points, invalid points, dominant intervals and a circular mean. Distribution
percentages use valid points as their denominator.

Select a nine-digit MMSI to compare COG (orange) and heading (purple) on one
time plot. This applies the vessel filter to the dashboard. Each line breaks
at unavailable values, trajectory changes, configured time gaps and crossings
of the 0°/360° boundary. The plot displays at most 5,000 points by default;
breaks are calculated before limiting points. Distributions and exports use
all matching points.

Valid angles are finite values in `[0, 360)`. COG 360° and heading 511 are
unavailable sentinels; other out-of-range or non-finite values are invalid.
Missing heading does not remove an otherwise usable AIS row. Legacy datasets
without heading remain supported. Circular means near cancellation are
reported as undefined rather than as a misleading direction. Sentinel
definitions follow the [USCG AIS report specification](https://www.navcen.uscg.gov/ais-class-a-reports).

```text
GET /api/direction-histograms?bin_width=15
GET /api/direction-time-series?mmsi=413523230&max_points=5000
GET /api/export/excel?direction_bin_width=15
```

The API also accepts 5°, 10° and 60° bins. Time-series limits must be between
2 and 20,000 points. Excel retains the original six sheets and adds
`航向统计`, `对地航向分布` and `船首向分布`, using the current filters and
the selected bin width.

## Useful scripts

```powershell
& '.\.venv\Scripts\python.exe' scripts\inspect_dataset.py
& '.\.venv\Scripts\python.exe' scripts\build_dataset.py
& '.\.venv\Scripts\python.exe' scripts\check_spatial_filters.py
& '.\.venv\Scripts\python.exe' scripts\check_track_tiles.py
& '.\.venv\Scripts\python.exe' scripts\check_track_multiprocess.py
```
