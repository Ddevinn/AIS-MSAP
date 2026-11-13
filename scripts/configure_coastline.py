
"""Configure a local OSM WGS84 coastline file and build its spatial index."""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from backend.app.services.coastline import REFERENCE_DIR, coastline_index, coastline_source, source_for_path


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("path", nargs="?", type=Path, help="Path to lines.shp or its directory")
    args = parser.parse_args()
    try:
        source = source_for_path(args.path.resolve()) if args.path else coastline_source()
        if source is None:
            parser.error("Provide the WGS84 coastline Shapefile path.")
        path, _ = source
        index = coastline_index(source)
        if args.path:
            REFERENCE_DIR.mkdir(parents=True, exist_ok=True)
            pointer = REFERENCE_DIR / "source.txt"
            stage = pointer.with_suffix(".tmp")
            stage.write_text(str(path) + "\n", encoding="utf-8")
            stage.replace(pointer)
        print(f"Coastline: {path}\nIndex: {index}")
        return 0
    except (OSError, ValueError) as exc:
        parser.error(str(exc))


if __name__ == "__main__":
    raise SystemExit(main())
