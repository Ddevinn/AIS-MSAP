from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from backend.app.services.dataset import build_parquet


def report(state: dict[str, object]) -> None:
    print(json.dumps(state, ensure_ascii=False))


if __name__ == "__main__":
    force = "--force" in sys.argv
    manifest = build_parquet(force=force, progress=report)
    print(json.dumps(manifest, ensure_ascii=False, indent=2))
