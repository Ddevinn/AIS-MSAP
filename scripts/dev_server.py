"""Start the user-owned development server without changing Windows policies."""
from __future__ import annotations

import argparse
import os
import socket
import sys
from pathlib import Path

import uvicorn

ROOT = Path(__file__).resolve().parents[1]


def valid_port(value: str) -> int:
    port = int(value)
    if not 1 <= port <= 65535:
        raise argparse.ArgumentTypeError("port must be between 1 and 65535")
    return port


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--port", type=valid_port, default=8000)
    parser.add_argument("--no-reload", action="store_true", help="Stable mode for uninterrupted build/cleaning jobs")
    args = parser.parse_args()
    # Report an occupied port instead of terminating its owner or selecting a
    # different address. Uvicorn also checks binding in case a race occurs.
    try:
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as probe:
            probe.bind(("127.0.0.1", args.port))
    except OSError as error:
        print(f"Cannot use port {args.port}: {error}", file=sys.stderr)
        print("Reuse an existing reload-enabled server. If it lacks reload, press Ctrl+C in its owner terminal, then start this launcher once.", file=sys.stderr)
        return 1

    os.chdir(ROOT)
    sys.path.insert(0, str(ROOT))
    print(f"AIS dashboard: http://127.0.0.1:{args.port}", flush=True)
    if args.no_reload:
        print("Auto-reload is OFF. Use this mode for uninterrupted cleaning/build jobs.", flush=True)
    else:
        print("Auto-reload is ON. Backend Python changes are loaded automatically.", flush=True)
        print("Refresh the browser after HTML, CSS or JavaScript changes.", flush=True)
    print("Keep this PowerShell window open. Press Ctrl+C here to stop the service.", flush=True)
    uvicorn.run(
        "backend.app.main:app", host="127.0.0.1", port=args.port,
        reload=not args.no_reload,
        reload_dirs=[str(ROOT / "backend")] if not args.no_reload else None,
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
