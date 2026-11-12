import ast
from pathlib import Path
import unittest
from datetime import date, datetime
from decimal import Decimal
import math

from dbt_bench.risk_cases import calendar_counterexample


class RiskTests(unittest.TestCase):
    def test_calendar_vs_rows_independently_wrong_but_aggregate_close(self):
        result = calendar_counterexample()
        self.assertEqual(result["calendar"][-1], result["independent_expected_last"])
        self.assertNotEqual(result["calendar"][-1], result["rows_28"][-1])
        self.assertEqual(result["different_rows"], [27])
        self.assertTrue(result["aggregate_one_percent_accepts"])
        self.assertFalse(result["native_verifier_executed"])

    def test_legacy_comparator_defects_from_read_only_source(self):
        path = Path(__file__).resolve().parents[2] / "training/code/harbor-cmp/strict/strict_check.py"
        if not path.exists():
            self.skipTest("Legacy checkout absent; new comparator regression suite remains standalone")
        tree = ast.parse(path.read_text())
        selected = [node for node in tree.body if isinstance(node, ast.FunctionDef) and node.name in {"norm", "key", "same_value", "same_rows"}]
        namespace = {"date": date, "datetime": datetime, "Decimal": Decimal, "math": math, "ABS_TOL": 0.01, "REL_TOL": 1e-4}
        exec(compile(ast.Module(body=selected, type_ignores=[]), str(path), "exec"), namespace)
        compare = namespace["same_rows"]
        self.assertTrue(compare([(10000, 42.0)], [(10001, 42.0)])[0])
        self.assertTrue(compare([(9007199254740992, "A")], [(9007199254740993, "A")])[0])
        self.assertFalse(compare([(0.004, "A"), (0.006, "B")], [(0.006, "A"), (0.004, "B")])[0])
