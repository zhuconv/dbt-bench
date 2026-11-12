"""Final publication must handle an explicitly reported zero pending count."""
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scripts'))
import phase1_publish
from dbt_bench import io
from dbt_bench.validation import CHECKS


class FinalFreezeTests(unittest.TestCase):
    def fixture(self, root):
        out = root / 'pool'
        out.mkdir()
        records = []
        for i in range(135):
            conclusion = 'validated' if i == 0 else 'weak' if i < 131 else 'broken'
            checks = {name: {'status': 'pass'} for name in CHECKS}
            if conclusion == 'weak':
                checks['negatives']['status'] = 'fail'
            elif conclusion == 'broken':
                checks['assets']['status'] = 'fail'
            records.append({'task_key': f'synthetic-{i}',
                            'native': {'conclusion': conclusion, 'checks': checks}})
        io.write_jsonl(out / 'ledger.jsonl', records)
        shared = root / 'shared.txt'
        shared.write_text('Synthetic immutable source for the reporting regression.\n')
        ref = io.evidence_ref(shared, root)
        manifest = {name: ref for name in ('candidate_accounting', 'report',
            'retained_trial_index', 'freeze_script', 'source_lock', 'catalog', 'split')}
        manifest.update(validation_snapshot=io.evidence_ref(out / 'ledger.jsonl', root),
            native_counts={'validated': 1, 'weak': 130, 'broken': 4, 'pending': 0},
            completed_task_keys=[r['task_key'] for r in records[:131]],
            isolated_broken=[r['task_key'] for r in records[131:]],
            review_snapshots=[], phase1_complete=True, phase2_started=False,
            new_model_requests=0)
        self.write_manifest(out, manifest)
        return out, manifest

    @staticmethod
    def write_manifest(out, manifest):
        unsigned = {k: v for k, v in manifest.items() if k != 'hash'}
        io.write_json(out / 'manifest.json', {**unsigned, 'hash': io.digest(unsigned)})

    def test_final_131_reviewed_with_explicit_zero_pending_verifies(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            out, _ = self.fixture(root)
            with patch.object(phase1_publish, 'ROOT', root):
                result = phase1_publish.verify_freeze(out)
            self.assertTrue(result['passed'])
            self.assertEqual(result['completed_native14'], 131)

    def test_rehashed_manifest_with_wrong_dispositions_still_rejected(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            out, manifest = self.fixture(root)
            manifest['native_counts'].update(weak=129, pending=1)
            self.write_manifest(out, manifest)
            with patch.object(phase1_publish, 'ROOT', root), self.assertRaises(AssertionError):
                phase1_publish.verify_freeze(out)


if __name__ == '__main__':
    unittest.main()
