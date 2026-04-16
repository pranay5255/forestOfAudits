#!/usr/bin/env python3
"""Entrypoint for mini-swe-agent Modal runners."""

from __future__ import annotations

import importlib
import sys
from pathlib import Path
from types import ModuleType

RUNNERS = {
    "baseline": "modal_baseline",
    "forest": "modal_forest",
    "smoke": "modal_smoke",
}


def _load_runner(name: str) -> ModuleType:
    current_dir = Path(__file__).resolve().parent
    if str(current_dir) not in sys.path:
        sys.path.insert(0, str(current_dir))
    return importlib.import_module(RUNNERS[name])


def main(argv: list[str] | None = None) -> int:
    args = list(sys.argv[1:] if argv is None else argv)
    if args and args[0] in RUNNERS:
        runner_name = args.pop(0)
    else:
        runner_name = "forest"
    return int(_load_runner(runner_name).main(args))


if __name__ == "__main__":
    raise SystemExit(main())
