from pathlib import Path
import tempfile
import unittest

from dbt_bench import io
from index_phase1_trials import case_dispositions


class CaseDispositionTests(unittest.TestCase):
    def setup_case(self, root):
        batch = root / 'batch'
        manifest = {'tasks': {'task--refund': {'task_key': 'task'}}}
        io.write_json(batch / 'manifest.json', manifest)
        proof = root / 'review.json'
        io.write_json(proof, {'observed_refunds': [None, None], 'zero_is_not_established': True})
        value = {'manifest': io.evidence_ref(batch / 'manifest.json', root),
            'raw_rewards_modified': False, 'model_requests': 0,
            'excluded_cases': [{'task_key': 'task', 'case_label': 'task--refund',
                'coverage_eligible': False, 'independent_validity': 'not_established',
                'adjudication': io.evidence_ref(proof, root)}]}
        io.write_json(batch / 'case-validity-dispositions-v1.json', value)
        return batch, manifest, value, proof

    def test_review_excludes_only_named_case_without_modifying_source(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            batch, manifest, _, proof = self.setup_case(root)
            original = (batch / 'manifest.json').read_bytes(), proof.read_bytes()
            ref, found = case_dispositions(batch, manifest, root)
            self.assertEqual(set(found), {'task--refund'})
            self.assertNotIn('task--other', found)
            self.assertFalse(found['task--refund']['coverage_eligible'])
            self.assertTrue(io.evidence_ok(ref, root))
            self.assertEqual(original, ((batch / 'manifest.json').read_bytes(), proof.read_bytes()))

    def test_changed_review_cannot_exclude_a_case(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            batch, manifest, _, proof = self.setup_case(root)
            proof.write_text('changed conclusion')
            with self.assertRaises(AssertionError):
                case_dispositions(batch, manifest, root)

    def test_wrong_case_or_manifest_is_rejected(self):
        for changed in ('case', 'manifest'):
            with self.subTest(changed=changed), tempfile.TemporaryDirectory() as temp:
                root = Path(temp)
                batch, manifest, value, _ = self.setup_case(root)
                if changed == 'case':
                    value['excluded_cases'][0]['case_label'] = 'unrelated--refund'
                else:
                    value['manifest']['sha256'] = 'different-run'
                io.write_json(batch / 'case-validity-dispositions-v1.json', value)
                with self.assertRaises(AssertionError):
                    case_dispositions(batch, manifest, root)


if __name__ == '__main__':
    unittest.main()
