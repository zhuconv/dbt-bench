import importlib.util
from pathlib import Path
import tempfile
import unittest

from dbt_bench.io import evidence_ref

ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location('deb_layer_check', ROOT / 'scripts/check_deb_server_layer.py')
module = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(module)


class PublicInputVisibilityTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.key = 'deb__dbt-consolidate'
        task = self.root / 'tasks' / self.key
        self.source = task / 'environment/data/public.csv'
        self.hidden = task / 'tests/private_data/hidden.csv'
        for p in (self.source, self.hidden):
            p.parent.mkdir(parents=True, exist_ok=True)
            p.write_text('key,value\n1,10\n')
        self.dockerfile = task / 'environment/Dockerfile'
        self.dockerfile.write_text('FROM ghcr.io/snowflake-labs/data-eng-bench-base:1.0.0\nCOPY data /app/data\n')
        ref = evidence_ref(self.source, self.root)
        self.review = {'reviewed_matches': [{'native_public_input': ref, 'path': '/app/data/public.csv',
            'sha256': ref['sha256'], 'sources': [str(self.hidden.relative_to(self.root))]}]}

    def test_native_public_input_duplicate_is_disclosed(self):
        rows = module.reviewed_public_copies(self.root, self.review, [self.key])
        self.assertEqual(rows[0]['path'], '/app/data/public.csv')
        self.assertEqual(rows[0]['hidden_source'], self.key + '/tests/private_data/hidden.csv')

    def test_different_private_answer_cannot_use_public_data_exception(self):
        self.hidden.write_text('key,value\n1,999\n')
        with self.assertRaisesRegex(ValueError, 'hidden duplicate differs'):
            module.reviewed_public_copies(self.root, self.review, [self.key])

    def test_exception_requires_actual_native_copy_and_exact_destination(self):
        self.review['reviewed_matches'][0]['path'] = '/app/answer.csv'
        with self.assertRaisesRegex(ValueError, 'destination/hash mismatch'):
            module.reviewed_public_copies(self.root, self.review, [self.key])
        self.review['reviewed_matches'][0]['path'] = '/app/data/public.csv'
        self.dockerfile.write_text('FROM ghcr.io/snowflake-labs/data-eng-bench-base:1.0.0\n')
        with self.assertRaisesRegex(ValueError, 'COPY declaration changed'):
            module.reviewed_public_copies(self.root, self.review, [self.key])
