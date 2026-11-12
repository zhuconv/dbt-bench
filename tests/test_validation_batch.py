import importlib.util
from pathlib import Path
import tempfile
import unittest

from dbt_bench.io import evidence_ref, read_json, tree_hash, write_json


class ValidationBatchReconciliationTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        spec = importlib.util.spec_from_file_location('validation_batch_script',
            Path(__file__).resolve().parents[1] / 'scripts/reconcile_validation_batch.py')
        self.module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(self.module)
        self.module.ROOT = self.root
        self.task = self.root / 'tasks/deb__example'
        self.task.mkdir(parents=True)
        (self.task / 'instruction.md').write_text('Build the declared table')
        self.job = self.root / 'jobs/reference'
        self.job.mkdir(parents=True)
        config = {'jobs_dir': str(self.root / 'jobs'), 'job_name': 'reference',
                  'tasks': [{'path': str(self.task)}], 'agents': [{'name': 'oracle'}],
                  'n_attempts': 2, 'retry': {'max_retries': 0}}
        write_json(self.root / 'config.json', config)
        self.manifest = self.root / 'manifest.json'
        write_json(self.manifest, {'config': evidence_ref(self.root / 'config.json', self.root),
            'planned': [{'task_key': 'deb__example', 'agent': 'oracle', 'attempt': n} for n in (1, 2)],
            'tasks': {'deb__example': {'validation_copy_hash': tree_hash(self.task)}}})
        for n in (1, 2):
            write_json(self.job / f'trial{n}/result.json', {
                'id': f'uuid-{n}', 'trial_name': f'trial{n}',
                'config': {'task': {'path': str(self.task)}, 'agent': {'name': 'oracle'}},
                'verifier_result': {'rewards': {'reward': 1.0}}, 'exception_info': None,
                'started_at': f'2026-01-01T00:0{n}:00Z', 'finished_at': f'2026-01-01T00:0{n}:01Z'})
        write_json(self.job / 'result.json', {'stats': {'n_completed_trials': 2}, 'finished_at': '2026-01-01T00:03:00Z'})
        self.output = self.root / 'reconciliation.json'

    def run_report(self):
        code = self.module.reconcile(self.manifest, self.output)
        return code, read_json(self.output)

    def test_complete_references_do_not_certify_task_or_invent_attempt_ids(self):
        code, report = self.run_report()
        self.assertEqual(code, 0)
        self.assertFalse(report['automatic_certification'])
        self.assertTrue(all(row['attempt'] is None for row in report['trials']))

    def test_scratch_artifact_location_does_not_require_repository_ancestry(self):
        outside = Path('/scratch/example/jobs/reference/trial')
        self.assertEqual(self.module.artifact_directory(outside), str(outside))
        self.assertEqual(self.module.artifact_directory(self.job / 'trial1'), 'jobs/reference/trial1')

    def test_reused_uuid_cannot_count_as_independent_initializations(self):
        p = self.job / 'trial2/result.json'
        row = read_json(p)
        row['id'] = 'uuid-1'
        write_json(p, row)
        code, report = self.run_report()
        self.assertEqual(code, 1)
        self.assertIn('duplicate_trial_id', [e['kind'] for e in report['errors']])
        self.assertFalse(report['coverage_complete'])

    def test_missing_repeat_cannot_complete(self):
        (self.job / 'trial2/result.json').unlink()
        write_json(self.job / 'result.json', {'stats': {'n_completed_trials': 1}, 'finished_at': '2026-01-01T00:03:00Z'})
        code, report = self.run_report()
        self.assertEqual(code, 1)
        self.assertEqual(report['coverage'][0]['missing'], 1)

    def test_task_mutation_blocks_complete_even_with_full_rewards(self):
        (self.task / 'instruction.md').write_text('Different business requirement')
        code, report = self.run_report()
        self.assertEqual(code, 1)
        self.assertIn('task_content_changed', [e['kind'] for e in report['errors']])
