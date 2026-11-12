#!/usr/bin/env python3
"""Run the stdlib test suite and retain machine-readable, hashed test evidence."""
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT), str(ROOT / "src")]

import argparse
from datetime import datetime, timezone
import io
import json
import unittest

from dbt_bench.io import evidence_ref, file_hash, write_json, write_text


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--output", type=Path, default=ROOT / "validation/evidence/implementation-tests")
    args = p.parse_args()
    suite = unittest.defaultTestLoader.discover(str(ROOT / "tests"), top_level_dir=str(ROOT))
    log = io.StringIO()
    result = unittest.TextTestRunner(stream=log, verbosity=2).run(suite)
    write_text(args.output.with_suffix(".log"), log.getvalue())
    evidence = {"kind": "implementation_and_synthetic_fixture_tests", "benchmark_trials": 0,
                "timestamp_utc": datetime.now(timezone.utc).isoformat(),
                "python": sys.version, "tests_run": result.testsRun, "failures": len(result.failures),
                "errors": len(result.errors), "skipped": [{"test": str(t), "reason": why} for t, why in result.skipped],
                "successful": result.wasSuccessful(), "log": evidence_ref(args.output.with_suffix(".log"), ROOT),
                "inputs": {str(p.relative_to(ROOT)): file_hash(p) for folder in ("src", "tests", "scripts", "validation", "runtime/qwen-code", "patches")
                           for p in sorted((ROOT / folder).rglob("*"))
                           if p.is_file() and (p.suffix == ".py" or folder != "validation" and p.suffix == ".json")}}
    write_json(args.output.with_suffix(".json"), evidence)
    print(json.dumps({k: v for k, v in evidence.items() if k not in {"inputs", "python"}}, indent=2))
    return 0 if result.wasSuccessful() else 1


if __name__ == "__main__":
    raise SystemExit(main())
