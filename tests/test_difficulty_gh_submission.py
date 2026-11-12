from dbt_bench.local_config import expand as _release_expand
from pathlib import Path
from unittest.mock import patch
from types import SimpleNamespace
import unittest
from submit_difficulty_gh_batches import sbatch_args
from submit_difficulty_gh_batches_v2 import parse_job_id
from submit_difficulty_gh_batches_v3 import confirm_job

class GhSubmissionTest(unittest.TestCase):

    def test_eight_independent_jobs_have_no_serial_dependency_or_array_limit(self):
        commands = [sbatch_args(Path(f'/scratch/plan/batch-{i:02d}'), {'experiment_id': str(i) * 64}) for i in range(1, 9)]
        for command in commands:
            for option in (_release_expand('--account=${ALLOCATION}'), '--partition=gh', '--nodes=1', '--ntasks=1', '--time=48:00:00'):
                self.assertIn(option, command)
            self.assertFalse(any((x.startswith(('--dependency', '--array', '--requeue')) for x in command)))
            self.assertTrue(next((x for x in command if x.startswith('--output='))).startswith('--output=/scratch/'))
        self.assertEqual(len({c[-1] for c in commands}), 8)

    def test_no_submission_check_uses_the_exact_same_resource_request(self):
        manifest = {'experiment_id': 'x' * 64}
        directory = Path('/scratch/plan/batch-01')
        probe = sbatch_args(directory, manifest, test_only=True)
        self.assertIn('--test-only', probe)
        probe.remove('--test-only')
        self.assertEqual(probe, sbatch_args(directory, manifest))

    def test_tacc_banner_keeps_the_exact_job_id(self):
        self.assertEqual(parse_job_id(_release_expand('Welcome to Vista\nChecking ${ALLOCATION}...OK\n1130679\n')), '1130679')
        self.assertEqual(parse_job_id('1130679;vista\n'), '1130679')
        for text in ('Submitted job1130679', '1130679\n1130680', 'Error:1130679'):
            with self.assertRaises(ValueError):
                parse_job_id(text)

    def test_pending_node_range_still_means_exactly_one_node(self):
        prefix = _release_expand('JobId=123 Account=${ALLOCATION} Partition=gh Comment=dbt-difficulty-abc JobName=dbt-diff-gh-01 TimeLimit=2-00:00:00 NumTasks=1 NumNodes=')
        for nodes in ('1', '1-1'):
            with patch('submit_difficulty_gh_batches_v3.subprocess.run', return_value=SimpleNamespace(stdout=prefix + nodes)):
                self.assertEqual(confirm_job('123', Path('/scratch/batch-01'), {'experiment_id': 'abc'}), prefix + nodes)
        with patch('submit_difficulty_gh_batches_v3.subprocess.run', return_value=SimpleNamespace(stdout=prefix + '1-2')):
            with self.assertRaises(ValueError):
                confirm_job('123', Path('/scratch/batch-01'), {'experiment_id': 'abc'})
