#!/usr/bin/env python3
"""Update the full evidence ledger; optionally execute isolated Harbor validation."""
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
from dbt_bench.validation import main

if __name__ == "__main__":
    raise SystemExit(main())
