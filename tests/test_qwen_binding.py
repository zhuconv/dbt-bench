from pathlib import Path
import tempfile
import unittest

from dbt_bench.io import file_hash, read_json, write_json
from dbt_bench.prepare import bind_qwen_bundle
from tests.helpers import fixture_root, qwen_bundle_fixture


class QwenBindingTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        fixture_root(self.root)
        self.manifest = qwen_bundle_fixture(self.root)
        self.config = {'agents': [{'name': 'qwen-coder', 'kwargs': {'version': '0.26.5'}}]}

    def test_verified_bytes_are_bound_into_native_kwargs(self):
        receipt = bind_qwen_bundle(self.root, self.config)
        kwargs = self.config['agents'][0]['kwargs']
        self.assertEqual(kwargs['installation_bundle'], str(self.manifest))
        self.assertEqual(kwargs['installation_bundle_sha256'], file_hash(self.manifest))
        self.assertEqual(receipt['files']['archive']['sha256'], file_hash(self.manifest.parent / 'archive'))

    def test_no_fallback_for_missing_or_changed_installation(self):
        path = self.manifest.parent / 'archive'
        path.write_text('changed')
        with self.assertRaisesRegex(ValueError, 'checksum mismatch'):
            bind_qwen_bundle(self.root, self.config)
        self.manifest.unlink()
        with self.assertRaises(FileNotFoundError):
            bind_qwen_bundle(self.root, self.config)
        self.assertNotIn('installation_bundle', self.config['agents'][0]['kwargs'])

    def test_rewriting_manifest_cannot_authorize_changed_archive(self):
        path = self.manifest.parent / 'archive'
        path.write_text('changed')
        manifest = read_json(self.manifest)
        manifest['files']['archive']['sha256'] = file_hash(path)
        write_json(self.manifest, manifest)
        with self.assertRaisesRegex(ValueError, 'checksum mismatch'):
            bind_qwen_bundle(self.root, self.config)

    def test_escaping_path_and_different_agent_version_are_rejected(self):
        self.config['agents'][0]['kwargs']['version'] = 'wrong'
        with self.assertRaisesRegex(ValueError, 'version differs'):
            bind_qwen_bundle(self.root, self.config)
        self.config['agents'][0]['kwargs']['version'] = '0.26.5'
        manifest = read_json(self.manifest)
        manifest['files']['installer']['path'] = '../outside'
        write_json(self.manifest, manifest)
        with self.assertRaisesRegex(ValueError, 'escapes'):
            bind_qwen_bundle(self.root, self.config)
