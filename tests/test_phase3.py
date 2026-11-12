"""Phase 3 regression tests: train isolation, task versions, and raw-score gates."""
from copy import deepcopy
from pathlib import Path
import sys
import tempfile
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scripts'))
from dbt_bench import io
from phase3_inventory import initial_candidates
from phase3_prepare import plan_entry
from phase3_publish import merge_candidates, reproduction, verify_protected
from phase3_review import check_overlay
from tests import test_phase2_review as phase2_tests


class Phase3Tests(unittest.TestCase):
    def test_exclusions_preserve_test_and_original_rows(self):
        rows = [{'task_key': 'a', 'split': 'train', 'eligible_for_sampling': True},
                {'task_key': 'b', 'split': 'test', 'eligible_for_sampling': True}]
        before = deepcopy(rows)
        new = initial_candidates(rows, {'a': 'replaced version pending'})
        self.assertFalse(new[0]['eligible_for_sampling'])
        self.assertEqual(new[1], before[1])
        self.assertEqual(rows, before)
        with self.assertRaises(ValueError): initial_candidates(rows, {'b': 'forbidden'})

    def test_publish_rejects_test_review(self):
        rows = [{'task_key': 'a', 'split': 'test'}]
        with self.assertRaises(ValueError): merge_candidates(rows, {'a': {'split': 'train'}})

    def task_fixture(self, root):
        for name, text in [('pilot/asset001/instruction.md', 'old task'),
                           ('pilot/asset001_v2/instruction.md', 'new task'),
                           ('tasks/spider2__asset001/instruction.md', 'old task'),
                           ('record.json', '{}')]:
            p = root / name; p.parent.mkdir(parents=True, exist_ok=True); p.write_text(text)
        row = {'task_key': 'spider2__asset001', 'split': 'train', 'upstream_task_id': 'asset001',
               'run_path': 'tasks/spider2__asset001', 'validation_ref': 'record.json'}
        return row, {'asset001': {'outcome': 'patched_pass'}}

    def test_variant_hash_changes_and_original_is_not_replaced(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder); row, results = self.task_fixture(root)
            run = lambda: plan_entry({'task_key': row['task_key']}, {row['task_key']: row}, results, root / 'pilot', root)
            first = run()
            self.assertTrue(first['task_variant'])
            (root / 'pilot/asset001_v2/instruction.md').write_text('new task corrected')
            self.assertNotEqual(run()['task_version'], first['task_version'])
            self.assertEqual((root / row['run_path'] / 'instruction.md').read_text(), 'old task')
            row['split'] = 'test'
            with self.assertRaises(ValueError): run()

    def test_original_prompt_drift_rejected(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder); row, results = self.task_fixture(root)
            (root / 'pilot/asset001/instruction.md').write_text('different task')
            with self.assertRaises(ValueError):
                plan_entry({'task_key': row['task_key']}, {row['task_key']: row}, results, root / 'pilot', root)

    def test_misplaced_nop_mode_cannot_be_silently_ignored(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder); row, results = self.task_fixture(root)
            entry = {'task_key': row['task_key'], 'mode': 'nop', 'rebuild': {'commands': []}}
            with self.assertRaisesRegex(ValueError, 'mode belongs inside rebuild'):
                plan_entry(entry, {row['task_key']: row}, results, root / 'pilot', root)
            entry['rebuild']['mode'] = 'nop'
            self.assertTrue(plan_entry(entry, {row['task_key']: row}, results, root / 'pilot', root)['task_variant'])

    def test_old_evidence_change_detected(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder); p = root / 'old.json'; p.write_text('{}')
            io.write_json(root / 'protected-files.json', [io.evidence_ref(p, root)])
            self.assertEqual(verify_protected(root, root), 1)
            p.write_text('{"reward":1}')
            with self.assertRaises(ValueError): verify_protected(root, root)

    def test_companion_does_not_inherit_audit_certification(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            base, contract, current, overlay = phase2_tests.AuditReviewTests().fixture(root)
            io.write_json(root / 'contract.json', contract)
            expected = {'task_key': 'synthetic', 'task_version': 'v2', 'category': 'agree', 'base_identity': current}
            overlay.update(phase3_identity=expected, contract=io.evidence_ref(root / 'contract.json', root))
            self.assertEqual(check_overlay(overlay, contract, expected, root)['conclusion'], 'validated')
            overlay['witness_kind'] = 'copy_gold'
            self.assertEqual(check_overlay(overlay, contract, expected, root)['conclusion'], 'pending')
            overlay['checks']['negatives'].update(status='gap', execution_evidence=[])
            with self.assertRaises(ValueError): check_overlay(overlay, contract, expected, root)

    def test_reconciliation_cannot_invent_reward_or_a_second_trial(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder); trials = []
            expected = {'label': 'a', 'batch_manifest': {'path': 'batch', 'sha256': 'synthetic'}}
            for n in range(2):
                raw, exe = root / f'{n}-raw.json', root / f'{n}-execution.json'
                io.write_json(raw, {'verifier_result': {'rewards': {'reward': 1.0}}, 'exception_info': None})
                io.write_json(exe, {'build_success': True})
                trials.append({'trial_id': str(n), 'label': 'a', 'result': io.evidence_ref(raw, root),
                    'execution': io.evidence_ref(exe, root), 'build_success': True,
                    'native_rewards': {'reward': 1.0}, 'exception_info': None})
            rec = {'complete': True, 'model_requests': 0, 'errors': [], 'manifest': expected['batch_manifest'], 'trials': trials}
            path = root / 'reconciliation.json'; io.write_json(path, rec)
            self.assertTrue(reproduction(path, expected, root)['native1_both_clean_builds'])
            trials[0]['native_rewards'] = {'reward': 0.0}; io.write_json(path, rec)
            with self.assertRaises(ValueError): reproduction(path, expected, root)
            trials[0]['native_rewards'] = {'reward': 1.0}; trials[1]['trial_id'] = '0'; io.write_json(path, rec)
            with self.assertRaises(ValueError): reproduction(path, expected, root)


if __name__ == '__main__':
    unittest.main()
