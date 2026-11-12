from copy import deepcopy
from pathlib import Path
import unittest
from unittest.mock import patch
from prepare_difficulty_gh_batches_v2 import balance, changed_config, remaining_tasks, native_schema_equivalent


class DifficultyGhBatchTest(unittest.TestCase):
    def test_budget_packing_preserves_all_tasks_with_six_hours_margin(self):
        rows = [{'task_key': f'{source}{n:02d}', 'native_agent_verifier_sec': 4 * budget}
                for source, count, budget in [('ade', 13, 900), ('spider', 16, 2400), ('deb', 35, 7000)]
                for n in range(count)]
        plan = balance(rows)
        self.assertEqual(len(plan), 8)
        self.assertEqual(sorted(r['task_key'] for b in plan for r in b['members']), sorted(r['task_key'] for r in rows))
        self.assertTrue(all(b['overhead_margin_sec'] >= 21600 for b in plan))
        self.assertEqual(sum(b['native_agent_verifier_sec'] for b in plan), 1180400)
        self.assertEqual(balance(list(reversed(rows))), plan)
        with self.assertRaises(ValueError):
            balance(rows, count=6)

    def test_finished_failures_are_preserved_and_partial_execution_blocks_migration(self):
        rows = [{'task_key': key, 'task_version': 'v2', 'eligible_for_measurement': True} for key in ('zero', 'pending')]
        measured = [{'task_key': 'zero', 'task_version': 'v2', 'complete': True,
                     'valid_attempt_count': 4, 'attempts': [{'attempt': n, 'joint_success': False} for n in (1, 2, 3, 4)]},
                    {'task_key': 'pending', 'task_version': 'v2', 'complete': False, 'attempts': []}]
        done, todo = remaining_tasks({'tasks': rows}, {'tasks': measured})
        self.assertEqual([r['task_key'] for r in done], ['zero'])
        self.assertEqual([r['task_key'] for r in todo], ['pending'])
        measured[1]['attempts'] = [{'attempt': 1}]
        with self.assertRaises(ValueError):
            remaining_tasks({'tasks': rows}, {'tasks': measured})

    def test_migration_changes_only_output_locations_and_attempt_grouping(self):
        original = {'job_name': 'old', 'jobs_dir': '/old', 'n_attempts': 1,
                    'n_concurrent_trials': 1, 'retry': {'max_retries': 0},
                    'tasks': [{'path': '/immutable/task'}], 'agent_timeout_multiplier': None,
                    'agents': [{'kwargs': {'config': {'samplingParams': {'temperature': .6}}}}]}
        before = deepcopy(original); updated = changed_config(original, Path('/scratch/new'))
        self.assertEqual(original, before)
        self.assertEqual({k for k in original if original[k] != updated[k]}, {'job_name', 'jobs_dir', 'n_attempts'})
        self.assertEqual(updated['n_attempts'], 4)
        original['retry']['max_retries'] = 1
        with self.assertRaises(ValueError):
            changed_config(original, Path('/scratch/new'))

    def test_duplicate_keys_and_over_capacity_are_rejected(self):
        with self.assertRaises(ValueError):
            balance([{'task_key': 'x', 'native_agent_verifier_sec': 1}] * 8)
        with self.assertRaises(ValueError):
            balance([{'task_key': str(n), 'native_agent_verifier_sec': 172800} for n in range(8)])

    def test_native_exception_set_order_is_not_a_retry_policy_change(self):
        config = {'tasks': [{'path': '/task'}], 'retry': {'max_retries': 0,
                  'exclude_exceptions': ['AgentTimeoutError', 'VerifierTimeoutError']}}
        normalized = deepcopy(config)
        normalized['retry']['exclude_exceptions'].reverse()
        with patch('prepare_difficulty_gh_batches_v2.check_harbor', return_value=normalized):
            self.assertTrue(native_schema_equivalent(config))
        normalized['retry']['exclude_exceptions'] = ['AgentTimeoutError']
        with patch('prepare_difficulty_gh_batches_v2.check_harbor', return_value=normalized):
            with self.assertRaises(ValueError):
                native_schema_equivalent(config)
