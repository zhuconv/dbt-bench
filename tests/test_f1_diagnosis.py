"""Hand-computable cutoff counterexamples for the diagnostic witness."""
import importlib.util
from pathlib import Path
import unittest

SOURCE = Path(__file__).resolve().parents[1] / 'validation/ade/ade-bench-f1003/derive.py'
spec = importlib.util.spec_from_file_location('f1_diagnostic_derivation', SOURCE)
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)


class CutoffDiagnosisTests(unittest.TestCase):
    def test_partial_tie_changes_membership(self):
        result = module.boundary({'A': 10, 'B': 9, 'C': 9}, 2)
        self.assertTrue(result['membership_ambiguous'])
        self.assertEqual(result['available_cutoff_slots'], 1)
        self.assertEqual(set(result['cutoff_keys']), {'B', 'C'})

    def test_tie_fully_inside_limit_is_not_missing_rows(self):
        # ORDER BY does not specify B/C order, but either order contains both.
        result = module.boundary({'A': 10, 'B': 9, 'C': 9, 'D': 8}, 3)
        self.assertFalse(result['membership_ambiguous'])

    def test_same_metric_different_entities_are_retained(self):
        # Using distinct metric values would incorrectly erase this ambiguity.
        result = module.boundary({9007199254740992: 4, 9007199254740993: 4}, 1)
        self.assertTrue(result['membership_ambiguous'])
        self.assertEqual(len(result['cutoff_keys']), 2)

    def test_source_order_does_not_decide_diagnosis(self):
        left = module.boundary({'A': 10, 'B': 9, 'C': 9}, 2)
        right = module.boundary({'C': 9, 'B': 9, 'A': 10}, 2)
        self.assertEqual(left['membership_ambiguous'], right['membership_ambiguous'])
        self.assertEqual(set(left['cutoff_keys']), set(right['cutoff_keys']))
