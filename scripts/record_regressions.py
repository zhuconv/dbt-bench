#!/usr/bin/env python3
"""Persist independently reproducible comparator/calendar evidence, not benchmark results."""
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

import argparse
import ast
from datetime import date, datetime
from decimal import Decimal
import math

from dbt_bench.comparison import Column, TableContract, compare_tables
from dbt_bench.io import file_hash, write_json
from dbt_bench.risk_cases import calendar_counterexample


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--legacy", type=Path, default=ROOT.parent / "training")
    args = parser.parse_args()
    source = args.legacy / "code/harbor-cmp/strict/strict_check.py"
    result = {"scope": "synthetic_fixtures_and_legacy_code", "native_benchmark_executed": False,
              "calendar_window": calendar_counterexample(), "legacy_comparison": []}
    if source.exists():
        namespace = {"date": date, "datetime": datetime, "Decimal": Decimal, "math": math, "ABS_TOL": 0.01, "REL_TOL": 1e-4}
        tree = ast.parse(source.read_text())
        functions = [n for n in tree.body if isinstance(n, ast.FunctionDef) and n.name in {"norm", "key", "same_value", "same_rows"}]
        exec(compile(ast.Module(body=functions, type_ignores=[]), str(source), "exec"), namespace)
        result["legacy_source"] = {"path": "../training/code/harbor-cmp/strict/strict_check.py", "sha256": file_hash(source)}
        cases = [
            ("integer_id", [(10000, 42.0)], [(10001, 42.0)], False,
             TableContract((Column("a", "integer"), Column("b", "numeric", "0.01")), keys=("a",))),
            ("large_integer_id", [(9007199254740992, "A")], [(9007199254740993, "A")], False,
             TableContract((Column("a", "integer"), Column("b", "text")), keys=("a",))),
            ("key_aligned_tolerance", [(0.004, "A"), (0.006, "B")], [(0.006, "A"), (0.004, "B")], True,
             TableContract((Column("a", "numeric", "0.01"), Column("b", "text")), keys=("b",))),
            ("unordered_positive_control", [(1, "A"), (2, "B")], [(2, "B"), (1, "A")], True,
             TableContract((Column("a", "integer"), Column("b", "text")))),
        ]
        for name, gold, pred, expected, contract in cases:
            old, reason = namespace["same_rows"](gold, pred)
            new = compare_tables([dict(zip(("a", "b"), r)) for r in gold],
                                 [dict(zip(("a", "b"), r)) for r in pred], contract)
            result["legacy_comparison"].append({"case": name, "gold": gold, "prediction": pred,
                  "independent_expected": expected, "legacy_accepted": old, "legacy_reason": reason,
                  "new_accepted": new.equal, "new_reason": new.reason})
    else:
        result["legacy_source"] = {"status": "unavailable"}
    write_json(ROOT / "validation/evidence/regressions.json", result)
    print("Recorded validation/evidence/regressions.json; native benchmark trials: 0")


if __name__ == "__main__":
    main()
