from __future__ import annotations

from io import BytesIO
import math
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from PIL import Image

from backend.app.services.dataset import read_active_manifest
from backend.app.services.raster_tracks import track_tile_png


def main() -> None:
    bounds = (read_active_manifest() or {}).get("bounds")
    assert bounds
    lon = (bounds["lon_min"] + bounds["lon_max"]) / 2
    lat = (bounds["lat_min"] + bounds["lat_max"]) / 2
    zoom = 12
    scale = 2 ** zoom
    x = int((lon + 180) / 360 * scale)
    y = int((1 - math.asinh(math.tan(math.radians(lat))) / math.pi) / 2 * scale)
    png = track_tile_png(zoom, x, y)
    image = Image.open(BytesIO(png))
    assert image.format == "PNG"
    assert image.size == (256, 256)
    print(f"PNG track tile ok: z={zoom}, x={x}, y={y}, {len(png):,} bytes")


if __name__ == "__main__":
    main()
