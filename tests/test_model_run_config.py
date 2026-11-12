from copy import deepcopy
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'scripts'))
import prepare_model_run as runs
from dbt_bench import io


class RunIdentityTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.output = self.root / 'one-run'
        self.output.mkdir()
        self.config = {'job_name': 'template', 'jobs_dir': 'jobs', 'artifacts': [],
                       'n_attempts': 2, 'n_concurrent_trials': 1}
        self.identity = {'config': self.config, 'tasks': {'deb__test': 'task-hash'},
                         'deployment': 'fixed-checkpoint', 'backend': 'docker'}
        previous_id = io.digest(self.identity)
        # Reproduce the actual old generator mutation after computing the ID.
        self.config['job_name'] = 'deb-' + previous_id[:16]
        self.base = self.output / 'base-config.yaml'
        io.write_json(self.base, self.config)
        io.write_json(self.base.with_suffix('.manifest.json'), {
            'experiment_id': previous_id, 'identity': self.identity,
            'config_hash': io.file_hash(self.base),
            'planned': [{'task_key': 'deb__test', 'attempt': i} for i in (1, 2)],
        })
        self.schema = patch.object(runs, 'check_harbor', side_effect=deepcopy)
        self.schema.start()
        self.addCleanup(self.schema.stop)

    def test_final_identity_matches_serialized_config_and_preserves_base_bytes(self):
        before = (self.base.read_bytes(), self.base.with_suffix('.manifest.json').read_bytes())
        m = runs.finalize_run(self.root, self.output, self.base)
        persisted = io.read_json(self.output / 'manifest.json')
        self.assertEqual(io.digest(persisted['identity']), persisted['experiment_id'])
        self.assertEqual(persisted['identity']['config'], io.read_json(self.output / 'config.yaml'))
        self.assertFalse(m['preparation']['base_identity_self_consistent'])
        self.assertEqual(m['identity']['config']['job_name'], 'one-run')
        self.assertEqual(m['planned'], [{'task_key': 'deb__test', 'attempt': i} for i in (1, 2)])
        self.assertEqual(before, (self.base.read_bytes(), self.base.with_suffix('.manifest.json').read_bytes()))

    def test_capture_is_in_final_identity_and_has_an_immutable_local_spec(self):
        spec = {'task_key': 'deb__test', 'artifacts': [{'source': '/app/project', 'destination': 'submission'}]}
        m = runs.finalize_run(self.root, self.output, self.base, capture=(spec, {'contract': {'path': 'contract', 'sha256': 'hash'}}))
        ref = m['identity']['submission_capture']['spec']
        self.assertTrue(io.evidence_ok(ref, self.root))
        self.assertEqual(io.read_json(self.root / ref['path']), spec)
        self.assertEqual(m['identity']['config']['artifacts'], spec['artifacts'])
        self.assertEqual(io.digest(m['identity']), m['experiment_id'])

    def test_changed_base_and_wrong_task_capture_are_rejected(self):
        changed = {**self.config, 'n_attempts': 99}
        io.write_json(self.base, changed)
        with self.assertRaisesRegex(ValueError, 'disagree'):
            runs.finalize_run(self.root, self.output, self.base)
        io.write_json(self.base, self.config)
        with self.assertRaisesRegex(ValueError, 'reviewed task'):
            runs.finalize_run(self.root, self.output, self.base,
                              capture=({'task_key': 'deb__other', 'artifacts': []}, {}))
        self.assertFalse((self.output / 'config.yaml').exists())

    def test_rerun_never_overwrites_a_prepared_run(self):
        runs.finalize_run(self.root, self.output, self.base)
        with self.assertRaisesRegex(ValueError, 'replace'):
            runs.finalize_run(self.root, self.output, self.base)


if __name__ == '__main__':
    unittest.main()
