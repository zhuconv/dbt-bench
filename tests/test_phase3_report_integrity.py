from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scripts'))
from dbt_bench import io
import phase3_report


class FrozenReportIntegrityTests(unittest.TestCase):
    def test_executed_gap_is_reviewed_but_not_certified(self):
        rows = [{'task_key': 'a', 'formal_execution_required': True,
                 'native_current': 'weak', 'audit_current': 'pending'}]
        result = phase3_report.completion_status(rows, 1, expected=1)
        self.assertTrue(result['phase3_review_complete'])
        self.assertFalse(result['phase3_validation_complete'])
        self.assertEqual(result['unresolved_validation_tasks'], ['a'])

    def test_known_weak_broken_conclusions_are_resolved_investigations(self):
        rows = [{'task_key': 'a', 'formal_execution_required': True,
                 'native_current': 'weak', 'audit_current': 'broken'}]
        self.assertTrue(phase3_report.completion_status(rows, 1, expected=1)['phase3_validation_complete'])
        self.assertFalse(phase3_report.completion_status(rows, 0, expected=1)['phase3_review_complete'])

    def test_changed_candidate_file_cannot_inflate_reported_pool(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            io.write_json(root / 'protected-files.json', [])
            io.write_json(root / 'inventory.json', {})
            io.write_jsonl(root / 'candidates.jsonl', [{'task_key': 'excluded', 'eligible_for_sampling': False}])
            io.write_json(root / 'freeze.json', {'candidates': io.evidence_ref(root / 'candidates.jsonl', root)})
            io.write_json(root / 'state.json', {'inventory': io.evidence_ref(root / 'inventory.json', root),
                                               'latest_freeze': io.evidence_ref(root / 'freeze.json', root)})
            io.write_jsonl(root / 'candidates.jsonl', [{'task_key': 'excluded', 'eligible_for_sampling': True}])
            with patch.object(phase3_report, 'ROOT', root):
                with self.assertRaisesRegex(ValueError, 'frozen candidates'):
                    phase3_report.report(root, root / 'report')
            self.assertFalse((root / 'report').exists())


if __name__ == '__main__':
    unittest.main()
