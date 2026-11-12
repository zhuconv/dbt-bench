"""Interrupted jobs retain terminal failures and merge only missing slots."""
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scripts'))
import review_tools
from index_phase1_trials import planned_slots, trial_kind
from dbt_bench import io


class RecoveryTests(unittest.TestCase):
    def test_controlled_input_trials_never_become_original_input_scores(self):
        spec = {'controlled_fixture': {'main.source': 'SELECT 1 AS x'}}
        self.assertEqual(trial_kind({}, {'case_scope': 'controlled_input_fixture'}, spec), 'additional_fixture')
        self.assertEqual(trial_kind({}, {'case_scope': 'original_input'}, spec), 'native')
        self.assertEqual(trial_kind({}, {'status': 'construction_failed'}, spec), 'input_scope_unknown')
        self.assertEqual(trial_kind({}, {'data_scope': 'additional_input_fixture',
                                         'status': 'construction_failed'}), 'additional_fixture')
        self.assertEqual(trial_kind({'verifier': {'disable': True}}, {}, spec), 'environment_probe')
        self.assertEqual(trial_kind({}, {'data_scope': 'original_pinned_inputs'}), 'native')

    def test_oracle_and_nop_keep_separate_attempt_denominators(self):
        config = {'agents': [{'name': 'oracle'}, {'name': 'nop'}],
                  'tasks': [{'path': '/task'}], 'n_attempts': 2}
        expected = planned_slots(config)
        self.assertEqual(sum(expected.values()), 4)
        self.assertEqual(expected[('/task', ('name', 'oracle'))], 2)
        self.assertEqual(expected[('/task', ('name', 'nop'))], 2)
        with self.assertRaises(ValueError):
            planned_slots({**config, 'agents': [{'name': 'qwen-code', 'model_name': 'model'}]})

    def test_terminal_failure_and_recovery_result_are_both_retained(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            batch, continuation = root / 'original', root / 'completion'
            agent = {'name': None, 'import_path': 'native_mechanical_agent:NativeMechanicalAgent'}
            cfg = {'agents': [agent], 'tasks': [{'path': '/a'}, {'path': '/b'}],
                   'n_attempts': 1, 'job_name': 'original', 'jobs_dir': str(batch / 'jobs')}
            io.write_json(batch / 'config.yaml', cfg)
            io.write_json(batch / 'manifest.json', {'planned_trials': 2})
            first = batch / 'jobs/original/first/result.json'
            io.write_json(first, {'id': 'first', 'trial_name': 'first', 'finished_at': 'done',
                'config': {'agent': agent, 'task': {'path': '/a'}},
                'verifier_result': None, 'exception_info': {'exception_type': 'RewardFileNotFoundError'}})
            resumed = {**cfg, 'tasks': [{'path': '/b'}], 'job_name': 'completion-part1',
                       'jobs_dir': str(continuation / 'jobs')}
            io.write_json(continuation / 'config-1.yaml', resumed)
            second = continuation / 'jobs/completion-part1/second/result.json'
            row = {'id': 'second', 'trial_name': 'second', 'finished_at': 'done',
                   'config': {'agent': agent, 'task': {'path': '/b'}},
                   'verifier_result': {'rewards': {'reward': 0}}}
            io.write_json(second, row)
            io.write_json(second.parent.parent / 'result.json', {'finished_at': 'done'})
            io.write_text(root / 'stopped.txt', 'Original allocation was cancelled; independently verified.')
            io.write_json(continuation / 'completion.json', {
                'original_config': io.evidence_ref(batch / 'config.yaml', root),
                'original_manifest': io.evidence_ref(batch / 'manifest.json', root),
                'retained_results': [io.evidence_ref(first, root)],
                'stopped_evidence': io.evidence_ref(root / 'stopped.txt', root),
                'plans': [{'config': io.evidence_ref(continuation / 'config-1.yaml', root), 'planned_trials': 1}]})
            with patch.object(review_tools, 'ROOT', root):
                paths = review_tools.terminal_result_paths(batch, [continuation])
                self.assertEqual(set(paths), {first, second})
                self.assertIsNone(io.read_json(first)['verifier_result'])
                io.write_json(second, {**row, 'id': 'first'})
                with self.assertRaises(AssertionError):
                    review_tools.terminal_result_paths(batch, [continuation])


if __name__ == '__main__':
    unittest.main()
