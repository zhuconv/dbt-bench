import copy
import importlib.util
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'scripts'))
import submission_declarations as declarations
from dbt_bench import io


class VersionedSubmissionTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.contract = self.root / 'validation/deb/example/contract.yaml'
        io.write_json(self.contract, {'requirements': ['model and database']})
        self.archive = self.root / 'history/contract.json'
        io.write_json(self.archive, io.read_json(self.contract))
        self.evidence = self.root / 'review.json'
        io.write_json(self.evidence, {'source': '/app/project', 'reviewed': True})
        self.path = self.root / 'configs/submissions/v2/example.json'
        self.spec = {
            'schema_version': 1, 'task_key': 'deb__example', 'task_hash': 'task-hash',
            'collection_version': 'v2', 'rationale': 'Reviewed project and database directories',
            'contract': io.evidence_ref(self.archive, self.root),
            'path_evidence': [io.evidence_ref(self.evidence, self.root)],
            'artifacts': [{'source': '/app/project', 'destination': 'submission/project', 'exclude': []}],
            'required_files': ['submission/project/dbt_project.yml'],
        }
        io.write_json(self.path, self.spec)
        self.row = {'task_key': 'deb__example', 'adapted_task_hash': 'task-hash',
                    'validation_ref': 'validation/deb/example/record.json'}
        self.mock = patch.object(declarations, 'load_catalog', return_value=[self.row])
        self.mock.start()
        self.addCleanup(self.mock.stop)

    def bind(self, config=None):
        return declarations.bind_versioned_submission(
            self.root, {} if config is None else config, 'deb__example', self.path)

    def test_current_archived_contract_is_accepted_and_old_declaration_is_untouched(self):
        before = self.path.read_bytes()
        config = {}
        spec, binding = self.bind(config)
        self.assertTrue(io.evidence_ok(binding['spec'], self.root))
        config['artifacts'][0]['exclude'].append('logs')
        self.assertEqual(spec['artifacts'][0]['exclude'], [])
        self.assertEqual(self.path.read_bytes(), before)
        self.assertEqual(self.archive.read_bytes(), self.contract.read_bytes())

    def test_still_valid_old_contract_cannot_bind_after_current_contract_changes(self):
        io.write_json(self.contract, {'requirements': ['new output']})
        self.assertTrue(io.evidence_ok(self.spec['contract'], self.root))
        config = {}
        with self.assertRaisesRegex(ValueError, 'contract/task changed'):
            self.bind(config)
        self.assertEqual(config, {})

    def test_changed_task_or_path_evidence_fails_closed(self):
        self.row['adapted_task_hash'] = 'different-task'
        with self.assertRaisesRegex(ValueError, 'contract/task changed'):
            self.bind()
        self.row['adapted_task_hash'] = 'task-hash'
        io.write_json(self.evidence, {'source': '/elsewhere'})
        with self.assertRaisesRegex(ValueError, 'review evidence missing or changed'):
            self.bind()

    def test_overlapping_or_escaping_paths_cannot_shadow_captured_artifacts(self):
        invalid = [
            [{'source': '/app/project', 'destination': '../outside'}],
            [{'source': '/app/../solution', 'destination': 'submission/project'}],
            [{'source': '/', 'destination': 'submission/project'}],
            [{'source': '/app/project', 'destination': 'manifest.json'}],
            [{'source': '/app/project', 'destination': 'submission/project'},
             {'source': '/app/project/models', 'destination': 'submission/models'}],
            [{'source': '/app/project', 'destination': 'submission/project'},
             {'source': '/data', 'destination': 'submission/project/data'}],
            [{'source': '/app/project', 'destination': 'submission/project', 'service': 'verifier'}],
        ]
        for artifacts in invalid:
            with self.subTest(artifacts=artifacts):
                io.write_json(self.path, {**self.spec, 'artifacts': artifacts})
                config = {}
                with self.assertRaises(ValueError):
                    self.bind(config)
                self.assertEqual(config, {})

    def test_required_files_cannot_be_outside_the_capture_or_duplicate(self):
        for files in ([], ['elsewhere/model.sql'], ['submission/project'],
                      ['submission/project/../escape'], ['submission/project/a'] * 2):
            with self.subTest(files=files):
                io.write_json(self.path, {**self.spec, 'required_files': files})
                with self.assertRaises(ValueError):
                    self.bind()

    def test_existing_artifact_configuration_is_not_replaced(self):
        config = {'artifacts': [{'source': '/keep', 'destination': 'keep'}]}
        before = copy.deepcopy(config)
        with self.assertRaisesRegex(ValueError, 'overwrite'):
            self.bind(config)
        self.assertEqual(config, before)

    def test_typed_file_is_exported_through_native_schema_without_extra_fields(self):
        spec = {**self.spec, 'schema_version': 2,
                'artifact_types': {'/app/project': 'directory', '/data/results': 'file'},
                'artifacts': [*self.spec['artifacts'],
                              {'source': '/data/results', 'destination': 'submission/results'}],
                'required_files': [*self.spec['required_files'], 'submission/results']}
        io.write_json(self.path, spec)
        config = {}
        self.bind(config)
        self.assertEqual(config['artifacts'], spec['artifacts'])
        self.assertNotIn('artifact_types', config)
        for change in ({'artifact_types': {'/app/project': 'directory'}},
                       {'required_files': ['submission/results/child']},
                       {'artifacts': [spec['artifacts'][0],
                                      {**spec['artifacts'][1], 'exclude': ['x']}]}):
            io.write_json(self.path, {**spec, **change})
            with self.assertRaises(ValueError):
                self.bind()

    def test_explicit_stale_smoke_declaration_stops_before_config_or_model_preparation(self):
        loader = importlib.util.spec_from_file_location('versioned_smoke_test', ROOT / 'scripts/run_gh_smoke.py')
        smoke = importlib.util.module_from_spec(loader)
        loader.loader.exec_module(smoke)
        io.write_json(self.contract, {'requirements': ['new output']})
        with patch.object(smoke, 'load_catalog', return_value=[{**self.row, 'source': 'deb'}]), \
                patch.object(smoke, 'emit_config') as emit, patch.object(smoke, 'bind_submission') as legacy:
            with self.assertRaisesRegex(ValueError, 'contract/task changed'):
                smoke.prepare_smoke_task(self.root, {'task_key': 'deb__example'}, None,
                                         '123', self.root, submission_path=self.path)
            emit.assert_not_called()
            legacy.assert_not_called()

    def test_reference_option_without_capture_rejected_before_creating_a_batch(self):
        out = self.root / 'uncreated-batch'
        result = subprocess.run([
            sys.executable, str(ROOT / 'scripts/prepare_reference_batch.py'), '--task', 'deb__example',
            '--submission-spec', str(self.path), '--output-dir', str(out),
        ], cwd=ROOT, env={**os.environ, 'PYTHONDONTWRITEBYTECODE': '1'},
            text=True, capture_output=True, timeout=30)
        self.assertNotEqual(result.returncode, 0)
        self.assertIn('--submission-spec requires --capture-submission', result.stderr)
        self.assertFalse(out.exists())


if __name__ == '__main__':
    unittest.main()
