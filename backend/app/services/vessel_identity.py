"""Build an identity lookup for tabular files imported before these fields were retained."""

from __future__ import annotations

import json
import logging
import os
import sqlite3
from concurrent.futures import Future, ThreadPoolExecutor
from pathlib import Path
from tempfile import NamedTemporaryFile
from threading import Event, Lock

import pandas as pd

from backend.app.services.dataset import DatasetPaths
from backend.app.services.ingestion import (
    TABULAR_SUFFIXES, VESSEL_IDENTITY_ALIASES, parse_timestamps, resolve_fields,
    source_chunks, vessel_identity_fields, vessel_identity_values,
)

INDEX_SCHEMA_VERSION = 2

logger = logging.getLogger(__name__)
_lock = Lock()
_stop = Event()
_builds: dict[Path, tuple[str, Future[bool]]] = {}
_executor: ThreadPoolExecutor | None = None


def legacy_vessel_identity(paths: DatasetPaths, manifest: dict, mmsi: int, end_ts: int) -> tuple[dict, bool]:
    global _executor
    if paths.source_path.suffix.lower() not in TABULAR_SUFFIXES:
        return {}, False
    mappings = manifest.get("field_mappings", {})
    if mappings and not any(vessel_identity_fields(item.get("columns", [])) for item in mappings.values()):
        return {}, False
    try:
        source_stat = paths.source_path.stat()
    except OSError:
        return {}, False

    index = paths.source_parquet.with_name("vessel_identity.sqlite3")
    signature = json.dumps([
        INDEX_SCHEMA_VERSION, str(paths.source_path), source_stat.st_size, source_stat.st_mtime_ns,
        manifest.get("created_at"), paths.source_timezone, paths.csv_encoding, paths.sheet_name,
    ])
    with _lock:
        values = _read_identity(index, signature, mmsi, end_ts)
        if values is not None:
            _builds.pop(index, None)
            return values, False
        if _stop.is_set():
            return {}, False
        existing = _builds.get(index)
        if existing:
            old_signature, job = existing
            if not job.done():
                return {}, True
            if old_signature == signature and not job.result():
                return {}, False

        if _executor is None:
            _executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix="vessel-identity")
        _builds[index] = signature, _executor.submit(_build_index, paths, index, signature)
        return {}, True


def _read_identity(index: Path, signature: str, mmsi: int, end_ts: int) -> dict | None:
    if not index.is_file():
        return None
    try:
        con = sqlite3.connect(index.resolve().as_uri() + "?mode=ro", timeout=0.2, uri=True)
        try:
            if con.execute("SELECT signature FROM source").fetchone() != (signature,):
                return None
            result = {}
            for field in VESSEL_IDENTITY_ALIASES:
                row = con.execute("""
                    SELECT value FROM identity
                    WHERE mmsi = ? AND field = ? AND timestamp <= ?
                    ORDER BY timestamp DESC LIMIT 1
                """, (mmsi, field, end_ts)).fetchone()
                if row:
                    result[field] = row[0]
            return result
        finally:
            con.close()
    except sqlite3.Error:
        return None


def _build_index(paths: DatasetPaths, index: Path, signature: str) -> bool:
    temporary = None
    try:
        with NamedTemporaryFile(dir=index.parent, prefix=".vessel-identity-", suffix=".tmp", delete=False) as file:
            temporary = Path(file.name)
        con = sqlite3.connect(temporary)
        try:
            con.executescript("""
                CREATE TABLE source (signature TEXT NOT NULL);
                CREATE TABLE identity (
                    mmsi INTEGER, field TEXT, timestamp INTEGER, value TEXT NOT NULL,
                    PRIMARY KEY (mmsi, field, timestamp)
                ) WITHOUT ROWID;
            """)
            for _, columns, frame, _ in source_chunks(paths.source_path, paths.csv_encoding, paths.sheet_name):
                if _stop.is_set():
                    return False
                fields = vessel_identity_fields(columns)
                if not fields:
                    continue
                mapping = resolve_fields(columns)
                mmsi = pd.to_numeric(frame[mapping["mmsi"]], errors="coerce")
                times = parse_timestamps(frame[mapping["timestamp"]], paths.source_timezone)
                valid_points = mmsi.between(100_000_000, 999_999_999) & mmsi.mod(1).eq(0) & times.gt(0)
                for field, column in fields.items():
                    values = vessel_identity_values(frame[column], field)
                    valid = valid_points & values.notna()
                    con.executemany("""
                        INSERT OR REPLACE INTO identity (mmsi, field, timestamp, value) VALUES (?, ?, ?, ?)
                    """, ((int(vessel), field, int(stamp), value)
                          for vessel, stamp, value in zip(mmsi[valid], times[valid], values[valid])))
            if _stop.is_set():
                return False
            # Keep change times so historical lookups retain the correct value without duplicating static reports.
            con.execute("""
                DELETE FROM identity WHERE (mmsi, field, timestamp) IN (
                    SELECT mmsi, field, timestamp FROM (
                        SELECT mmsi, field, timestamp, value,
                               lag(value) OVER (PARTITION BY mmsi, field ORDER BY timestamp) AS previous_value
                        FROM identity
                    )
                    WHERE value = previous_value
                )
            """)
            con.execute("INSERT INTO source VALUES (?)", (signature,))
            con.commit()
            con.execute("VACUUM")
        finally:
            con.close()
        stat = paths.source_path.stat()
        if _stop.is_set() or json.loads(signature)[2:4] != [stat.st_size, stat.st_mtime_ns]:
            return False
        # A reader of the previous version releases the file before the worker publishes.
        with _lock:
            os.replace(temporary, index)
        return True
    except Exception:
        logger.exception("Could not build vessel identity index for dataset %s", paths.dataset_id)
        return False
    finally:
        if temporary is not None:
            try:
                temporary.unlink(missing_ok=True)
            except OSError:
                logger.warning("Could not remove a temporary vessel identity index for %s", paths.dataset_id)


def shutdown_vessel_identity() -> None:
    global _executor
    with _lock:
        _stop.set()
        executor, _executor = _executor, None
    if executor is not None:
        executor.shutdown(wait=True, cancel_futures=True)
    with _lock:
        _builds.clear()
        _stop.clear()
