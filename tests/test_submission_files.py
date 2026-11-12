import asyncio
import logging
from pathlib import Path
import shutil
import tempfile
import unittest

from dbt_bench import io
from dbt_bench.report import inspect_submission, verify_submission


class FileSubmissionTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.trial = self.root / 'trial'
        self.artifacts = self.trial / 'artifacts'
        (self.artifacts / 'submission/models').mkdir(parents=True)
        (self.artifacts / 'submission/models/output.sql').write_text('select 42')
        (self.artifacts / 'submission/results').write_bytes(b'')
        self.spec = {'schema_version': 2, 'task_key': 'fixture',
                     'artifacts': [{'source': '/app/models', 'destination': 'submission/models', 'exclude': []},
                                   {'source': '/data/results', 'destination': 'submission/results', 'exclude': []}],
                     'artifact_types': {'/app/models': 'directory', '/data/results': 'file'},
                     'required_files': ['submission/models/output.sql', 'submission/results']}
        self.entries = [{**a, 'destination': 'artifacts/' + a['destination'],
                         'type': self.spec['artifact_types'][a['source']], 'status': 'ok', 'service': None}
                        for a in self.spec['artifacts']]
        io.write_json(self.artifacts / 'manifest.json', self.entries)

    def test_individual_empty_file_and_directory_are_captured_and_content_change_is_detected(self):
        first = inspect_submission(self.trial, self.spec)
        self.assertTrue(first['complete'], first)
        self.assertEqual(len(first['files']), 2)
        (self.artifacts / 'submission/results').write_text('changed output')
        self.assertNotEqual(first['snapshot_hash'], inspect_submission(self.trial, self.spec)['snapshot_hash'])

    def test_failed_skipped_misclassified_duplicate_and_wrong_destination_are_incomplete(self):
        for change in ({'status': 'failed'}, {'status': 'skipped'}, {'type': 'directory'},
                       {'destination': 'artifacts/elsewhere'}, {'exclude': ['*.sql']}):
            io.write_json(self.artifacts / 'manifest.json', [self.entries[0], {**self.entries[1], **change}])
            self.assertFalse(inspect_submission(self.trial, self.spec)['complete'], change)
        io.write_json(self.artifacts / 'manifest.json', self.entries + [self.entries[1]])
        self.assertFalse(inspect_submission(self.trial, self.spec)['complete'])

    def test_missing_files_unclaimed_files_and_escaping_links_are_not_complete(self):
        path = self.artifacts / 'submission/results'
        path.unlink()
        self.assertFalse(inspect_submission(self.trial, self.spec)['complete'])
        path.symlink_to(self.artifacts / 'submission/models/output.sql')
        self.assertFalse(inspect_submission(self.trial, self.spec)['complete'])
        path.unlink()
        path.write_text('output')
        (self.artifacts / 'unclaimed').write_text('exists outside declared capture')
        for required in (['unclaimed'], ['../outside'], []):
            self.assertFalse(inspect_submission(self.trial, {**self.spec, 'required_files': required})['complete'])
        (self.artifacts / 'submission/models/escape').symlink_to('/etc/passwd')
        self.assertFalse(inspect_submission(self.trial, self.spec)['complete'])

    def test_saved_receipt_cannot_hide_changed_file(self):
        io.write_json(self.root / 'spec.json', self.spec)
        io.write_json(self.root / 'receipt.json', inspect_submission(self.trial, self.spec))
        binding = {'task_key': 'fixture', 'submission': io.evidence_ref(self.root / 'receipt.json', self.root)}
        identity = {'submission_capture': {'spec': io.evidence_ref(self.root / 'spec.json', self.root)}}
        config = {'artifacts': self.spec['artifacts']}
        self.assertTrue(verify_submission(self.root, self.trial, config, binding, identity, True)[0])
        (self.artifacts / 'submission/results').write_text('verifier changed bytes')
        self.assertFalse(verify_submission(self.root, self.trial, config, binding, identity, True)[0])

    def test_native_harbor_handler_collects_extensionless_file_and_model_directory(self):
        try:
            from harbor.models.trial.config import ArtifactConfig
            from harbor.trial.artifact_handler import ArtifactHandler
        except ImportError:
            self.skipTest('Pinned Harbor runtime not installed')
        # Real native collector and real file transfer; only the environment
        # transport is a local fixture. This is not a benchmark/container run.
        source = self.root / 'fixture-environment'
        (source / 'app/models').mkdir(parents=True)
        (source / 'app/models/output.sql').write_text('select 9007199254740993')
        (source / 'data').mkdir()
        (source / 'data/results').write_text('retained result')

        class LocalTransport:
            def is_path_mounted(self, *args): return False
            async def service_is_dir(self, path, **kwargs):
                return (source / path.lstrip('/')).is_dir()
            async def service_download_dir(self, source_dir, target_dir, **kwargs):
                shutil.copytree(source / source_dir.lstrip('/'), target_dir, dirs_exist_ok=True)
            async def service_download_file(self, source_path, target_path, **kwargs):
                shutil.copyfile(source / source_path.lstrip('/'), target_path)

        out = self.root / 'native-trial'
        handler = ArtifactHandler(artifacts=[ArtifactConfig(**a) for a in self.spec['artifacts']],
                                  logger=logging.getLogger('file-capture-test'))
        asyncio.run(handler.download_artifacts(LocalTransport(), out / 'artifacts',
                                               source_artifacts_dir='/logs/artifacts'))
        result = inspect_submission(out, self.spec)
        self.assertTrue(result['complete'], result)
        self.assertEqual((out / 'artifacts/submission/results').read_text(), 'retained result')
        self.assertEqual(len(result['files']), 2)


if __name__ == '__main__':
    unittest.main()
