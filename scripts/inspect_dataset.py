from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from backend.app.services.dataset import inspect_pickle


if __name__ == "__main__":
    print(json.dumps(inspect_pickle(), ensure_ascii=False, indent=2))
