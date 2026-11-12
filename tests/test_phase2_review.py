"""Audit review gates: no inherited certification, stale evidence or fake gaps."""
from copy import deepcopy
from pathlib import Path
import sys
import tempfile
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scripts'))
from dbt_bench import io
from dbt_bench.validation import CHECKS
from phase2_review import check_overlay


class AuditReviewTests(unittest.TestCase):
    def fixture(self, root):
        refs = []
        for name in ('scripts/audit_tables.py', 'src/dbt_bench/comparison.py', 'task/audit.py'):
            p = root / name
            p.parent.mkdir(parents=True, exist_ok=True)
            p.write_text('# Synthetic checker identity fixture\n')
            refs.append(io.evidence_ref(p, root))
        log = root / 'synthetic-execution.json'
        io.write_json(log, {'synthetic_fixture': True, 'executed': True})
        evidence = io.evidence_ref(log, root)
        identity = {'task': 'synthetic-task', 'environment_data': 'synthetic-environment'}
        base = {'task_key': 'synthetic', 'identity': identity,
                'native': {'conclusion': 'weak', 'checks': {'synthetic': 'unchanged'}}}
        contract = {'task_key': 'synthetic', 'requirements': [{'id': 'R1', 'critical': True,
                    'coverage': {'native': {'status': 'missed_wrong'}}}]}
        overlay = {'schema_version': 1, 'task_key': 'synthetic', 'target': 'audit',
            'base_identity': identity, 'scope': 'Synthetic protocol test', 'model_requests': 0,
            'checker_version': 'test-v1', 'code_refs': refs, 'review_status': 'reviewed',
            'reference_kind': 'independent_recompute', 'witness_kind': 'built_from_inputs',
            'checks': {name: {'status': 'pass', 'reason': 'Synthetic executed fixture',
                             'evidence': [evidence], 'execution_evidence': [evidence]} for name in CHECKS},
            'requirement_coverage': {'R1': {'status': 'covered', 'reason': 'Synthetic rule check',
                                          'evidence': [evidence]}}}
        return base, contract, identity, overlay

    def run_check(self, root, fixture):
        base, contract, current, overlay = fixture
        return check_overlay(overlay, base, contract, current, root)

    def test_validated_audit_does_not_change_native(self):
        with tempfile.TemporaryDirectory() as name:
            root = Path(name); fixture = self.fixture(root); original = deepcopy(fixture[0])
            result = self.run_check(root, fixture)
            self.assertEqual(result['conclusion'], 'validated')
            self.assertEqual(fixture[0], original)
            self.assertEqual(result['native_conclusion_unchanged'], 'weak')

    def test_copy_gold_never_certifies_build(self):
        with tempfile.TemporaryDirectory() as name:
            root = Path(name); fixture = self.fixture(root)
            fixture[3]['witness_kind'] = 'copy_gold'
            self.assertEqual(self.run_check(root, fixture)['conclusion'], 'pending')

    def test_pending_is_not_gap_or_completion(self):
        with tempfile.TemporaryDirectory() as name:
            root = Path(name); fixture = self.fixture(root)
            fixture[3]['checks']['negatives'] = {'status': 'pending', 'reason': 'Not run', 'evidence': []}
            result = self.run_check(root, fixture)
            self.assertFalse(result['audit14_review_complete'])
            self.assertEqual(result['conclusion'], 'pending')

    def test_gap_without_execution_evidence_is_rejected(self):
        with tempfile.TemporaryDirectory() as name:
            root = Path(name); fixture = self.fixture(root)
            check = fixture[3]['checks']['cross_context']
            check.update(status='gap', execution_evidence=[])
            with self.assertRaises(ValueError): self.run_check(root, fixture)

    def test_executed_gap_is_complete_review_but_not_validated(self):
        with tempfile.TemporaryDirectory() as name:
            root = Path(name); fixture = self.fixture(root)
            fixture[3]['checks']['cross_context']['status'] = 'gap'
            result = self.run_check(root, fixture)
            self.assertTrue(result['audit14_review_complete'])
            self.assertEqual(result['conclusion'], 'pending')

    def test_code_change_invalidates_audit_evidence(self):
        with tempfile.TemporaryDirectory() as name:
            root = Path(name); fixture = self.fixture(root)
            (root / 'task/audit.py').write_text('# Changed checker\n')
            with self.assertRaises(ValueError): self.run_check(root, fixture)

    def test_environment_change_invalidates_review(self):
        with tempfile.TemporaryDirectory() as name:
            root = Path(name); base, contract, identity, overlay = self.fixture(root)
            current = {**identity, 'environment_data': 'new-environment'}
            with self.assertRaises(ValueError): check_overlay(overlay, base, contract, current, root)

    def test_execution_evidence_tamper_rejected(self):
        with tempfile.TemporaryDirectory() as name:
            root = Path(name); fixture = self.fixture(root)
            (root / 'synthetic-execution.json').write_text('{}')
            with self.assertRaises(ValueError): self.run_check(root, fixture)

    def test_missing_rule_and_isolated_task_rejected(self):
        with tempfile.TemporaryDirectory() as name:
            root = Path(name); fixture = self.fixture(root)
            fixture[3]['requirement_coverage'] = {}
            with self.assertRaises(ValueError): self.run_check(root, fixture)
            fixture = self.fixture(root); fixture[0]['native']['conclusion'] = 'broken'
            with self.assertRaises(ValueError): self.run_check(root, fixture)

    def test_independently_proved_audit_miss_stays_weak(self):
        with tempfile.TemporaryDirectory() as name:
            root = Path(name); fixture = self.fixture(root)
            entry = fixture[3]['requirement_coverage']['R1']; ref = entry['evidence'][0]
            entry.update(status='missed_wrong', independent_proof=ref, checker_evidence=ref,
                         case_validity='business_error')
            self.assertEqual(self.run_check(root, fixture)['conclusion'], 'weak')


if __name__ == '__main__':
    unittest.main()
