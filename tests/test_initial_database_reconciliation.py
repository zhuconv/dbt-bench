from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from dbt_bench import io
import reconcile_initial_database_batch as reconciler


class InitialDatabaseReconciliationTests(unittest.TestCase):
    def fixture(self, root, *, changed=False, missing_probe=False, duplicate=False):
        batch = root / 'batch'
        task = batch / 'tasks/one'
        task.mkdir(parents=True)
        (task / 'instruction.md').write_text('Use the declared raw database.')
        binding = root / 'binding.json'
        io.write_json(binding, {'image': 'immutable-test-image'})
        job = batch / 'jobs/probe'
        trial = job / 'one-attempt'
        raw = {'id': 'trial-one', 'finished_at': '2026-11-07T00:00:00',
               'config': {'task': {'path': str(task)}},
               'exception_info': None, 'verifier_result': None}
        io.write_json(trial / 'result.json', raw)
        if duplicate:
            io.write_json(job / 'duplicate/result.json', raw)
        io.write_json(job / 'result.json', {'finished_at': raw['finished_at']})
        io.write_json(trial / 'agent/initial-databases-shell.json', {'exit_code': 0})
        io.write_json(trial / 'agent/no-model.json',
                      {'model_requests': 0, 'hidden_material_uploaded': False})
        if not missing_probe:
            io.write_json(trial / 'agent/initial-databases.json', {
                'model_requests': 0, 'hidden_material_uploaded': False,
                'databases': [{'database': '/workspace/raw.duckdb', 'exists': True,
                    'before_sha256': 'initial', 'after_sha256': 'changed' if changed else 'initial',
                    'unchanged': not changed,
                    'relations': [{'name': 'expected', 'columns': []}], 'views': []}]})
        config = {'verifier': {'disable': True}, 'n_attempts': 1,
                  'jobs_dir': str(job.parent), 'job_name': job.name,
                  'tasks': [{'path': str(task)}]}
        io.write_json(batch / 'config.json', config)
        io.write_json(batch / 'manifest.json', {
            'config': io.evidence_ref(batch / 'config.json', root),
            'planned_trials': 1, 'candidate_count': 1,
            'tasks': {'one': {'task_hash': io.tree_hash(task),
                'image': 'immutable-test-image', 'task_bindings': [
                    {'task_key': 'one', 'binding': io.evidence_ref(binding, root)}]}}})
        return batch

    def reconcile(self, root, batch):
        output = root / 'review'
        with patch.object(reconciler, 'ROOT', root), patch('sys.argv', [
                'reconcile', '--batch', str(batch), '--output', str(output)]):
            reconciler.main()
        return io.read_json(output / 'summary.json'), io.read_json(output / 'one/initial-database.json')

    def test_metadata_names_do_not_certify_visibility_or_reward(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            summary, record = self.reconcile(root, self.fixture(root))
            self.assertTrue(summary['all_probes_executed'])
            self.assertTrue(summary['all_databases_unchanged'])
            self.assertEqual(record['hidden_material_assessment'], 'pending_task_review')
            self.assertIsNone(record['native_reward'])

    def test_database_change_is_reported_instead_of_hidden(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            summary, record = self.reconcile(root, self.fixture(root, changed=True))
            self.assertFalse(summary['all_databases_unchanged'])
            self.assertFalse(record['database_facts'][0]['unchanged'])

    def test_missing_probe_is_not_execution_evidence(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            summary, record = self.reconcile(root, self.fixture(root, missing_probe=True))
            self.assertFalse(summary['all_probes_executed'])
            self.assertFalse(record['probe_executed'])
            self.assertIsNone(record['metadata'])

    def test_duplicate_trial_fails_reconciliation(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            batch = self.fixture(root, duplicate=True)
            with self.assertRaises(AssertionError):
                self.reconcile(root, batch)


if __name__ == '__main__':
    unittest.main()
