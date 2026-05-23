from __future__ import annotations

from pathlib import Path


ROOT_DIR = Path(__file__).resolve().parents[2]
RAW_PKL_PATH = ROOT_DIR / "cleaned_trajs_2401.pkl"
DATA_DIR = ROOT_DIR / "data"
PROCESSED_DIR = DATA_DIR / "processed"
EXPORT_DIR = DATA_DIR / "exports"
CACHE_DIR = DATA_DIR / "cache"
FRONTEND_DIR = ROOT_DIR / "frontend"

PARQUET_PATH = PROCESSED_DIR / "ais_points.parquet"
MANIFEST_PATH = PROCESSED_DIR / "manifest.json"
CLEANED_PARQUET_PATH = PROCESSED_DIR / "ais_points_cleaned.parquet"
CLEANING_MANIFEST_PATH = PROCESSED_DIR / "cleaning_manifest.json"

AIS_COLUMNS = [
    "mmsi",
    "timestamp",
    "lon",
    "lat",
    "course",
    "speed",
    "ship_type",
    "length",
    "width",
]

MAX_BROWSER_TRACK_POINTS = 50_000
