#!/usr/bin/env python3
"""Prepare catalog, pinned tasks, and resolved Harbor job configuration."""
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
from dbt_bench.prepare import main

if __name__ == "__main__":
    raise SystemExit(main())
