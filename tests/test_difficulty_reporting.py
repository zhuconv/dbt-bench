from copy import deepcopy
from pathlib import Path
import tempfile
import unittest

from dbt_bench import io
from reconcile_difficulty import binary_reward, joint_outcome, measured_agent, reconcile


class DifficultyReportingTest(unittest.TestCase):
    def test_native_one_does_not_certify_missing_audit(self):
        self.assertIsNone(joint_outcome(1, None, execution_valid=True))
        self.assertIsNone(joint_outcome(0, None, execution_valid=True))
        self.assertIsNone(joint_outcome(1, 1, execution_valid=False))
        self.assertIs(joint_outcome(1, 0, execution_valid=True), False)
        self.assertIs(joint_outcome(0, 1, execution_valid=True), False)
        self.assertIs(joint_outcome(1, 1, execution_valid=True), True)

    def test_native_reward_is_binary_not_any_positive_number(self):
        self.assertIsNone(binary_reward(0.5))
        self.assertIsNone(binary_reward(True))
        self.assertIsNone(binary_reward('1'))
        self.assertEqual(binary_reward(1.0), 1)

    def test_reference_and_changed_endpoint_cannot_be_measured_attempts(self):
        expected = {'name': 'qwen-coder', 'model_name': 'openai/qwen',
                    'base_url': 'http://localhost/v1', 'kwargs': {'version': '0.26.5'}}
        raw = {'config': {'agent': deepcopy(expected)}}
        measured_agent(raw, expected)
        raw['config']['agent']['name'] = 'oracle'
        with self.assertRaises(ValueError):
            measured_agent(raw, expected)
        raw['config']['agent'] = deepcopy(expected)
        raw['config']['agent']['base_url'] = 'http://different/v1'
        with self.assertRaises(ValueError):
            measured_agent(raw, expected)

    def fixture(self, directory):
        rules = directory / 'rules.json'
        io.write_json(rules, {'training_prohibited': True})
        run = directory / 'run'; run.mkdir()
        config = {'job_name': 'run', 'retry': {'max_retries': 0},
                  'n_concurrent_trials': 1, 'n_attempts': 4}
        io.write_json(run / 'config.yaml', config)
        identity = {'config': config, 'purpose': 'difficulty_only_no_training',
            'measurement_rules': io.evidence_ref(rules, directory),
            'task_key': 'task', 'task_version': 'v1', 'attempt_ids': [1, 2, 3, 4],
            'phase3_production': {'task': {'pool_row': {'phase3_record': {'path': 'unused', 'sha256': 'unused'}}}}}
        # Absolute evidence reference is valid outside the repository.
        identity['measurement_rules']['path'] = str(rules)
        io.write_json(run / 'manifest.json', {'identity': identity, 'experiment_id': io.digest(identity),
            'config_hash': io.file_hash(run / 'config.yaml'), 'training_prohibited': True})
        return run

    def test_unstarted_run_has_four_missing_slots_not_four_failures(self):
        with tempfile.TemporaryDirectory() as folder:
            run = self.fixture(Path(folder))
            report = reconcile([run], [])
            self.assertFalse(report['complete'])
            task = report['tasks'][0]
            self.assertEqual(task['missing_attempts'], [1, 2, 3, 4])
            self.assertEqual(task['valid_attempt_count'], 0)
            self.assertIsNone(task['joint_success_count'])

    def test_duplicate_planned_slots_are_not_silently_retried(self):
        with tempfile.TemporaryDirectory() as folder:
            run = self.fixture(Path(folder))
            with self.assertRaisesRegex(ValueError, 'Duplicate planned'):
                reconcile([run, run], [])

    def test_changed_config_invalidates_reconciliation(self):
        with tempfile.TemporaryDirectory() as folder:
            run = self.fixture(Path(folder))
            config = io.read_json(run / 'config.yaml'); config['n_attempts'] = 5
            io.write_json(run / 'config.yaml', config)
            with self.assertRaisesRegex(ValueError, 'identity changed'):
                reconcile([run], [])
