#!/usr/bin/env python3
"""Reconcile native Harbor results and export conservative training candidates."""
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
from dbt_bench.report_v6 import main

if __name__ == "__main__":
    raise SystemExit(main())
