from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
import math
import time

import requests

from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from backend.app.services.dataset import read_active_manifest


def main() -> None:
    bounds = (read_active_manifest() or {}).get("bounds")
    assert bounds
    lon = (bounds["lon_min"] + bounds["lon_max"]) / 2
    lat = (bounds["lat_min"] + bounds["lat_max"]) / 2
    zoom = 17
    scale = 2 ** zoom
    x = int((lon + 180) / 360 * scale)
    y = int((1 - math.asinh(math.tan(math.radians(lat))) / math.pi) / 2 * scale)
    vessel = 100_000_000 + time.time_ns() % 800_000_000
    urls = [
        f"http://127.0.0.1:8000/api/tiles/tracks/{zoom}/{x + dx}/{y + dy}.png?mmsi={vessel}"
        for dx, dy in ((0, 0), (1, 0), (0, 1), (1, 1))
    ]

    def fetch(url: str) -> tuple[str | None, str | None]:
        response = requests.get(url, timeout=120)
        response.raise_for_status()
        return response.headers.get("X-Track-Render"), response.headers.get("X-Track-Worker")

    with ThreadPoolExecutor(max_workers=4) as executor:
        results = list(executor.map(fetch, urls))
    assert all(mode in {"threaded", "cache"} and worker for mode, worker in results), results
    print(f"threaded track rendering ok: {len(results)} concurrent requests")


if __name__ == "__main__":
    main()
