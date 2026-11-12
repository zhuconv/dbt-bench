"""Current evidence checks must detect file drift without blessing historical data."""
from copy import deepcopy
from pathlib import Path
import sys
import tempfile
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scripts'))
from dbt_bench import io
from dbt_bench.validation import CHECKS
from verify_current_audits import verify_records


class CurrentAuditTests(unittest.TestCase):
    def fixture(self, root):
        evidence = root / 'observed.json'; io.write_json(evidence, {'actual_rows': 3})
        contract = root / 'validation/t/contract.yaml'; io.write_json(contract, {'version': 'original'})
        current = {'backend': 'fixture', 'contract': io.file_hash(contract)}
        row = {'task_key': 't', 'validation_ref': 'validation/t/record.json'}
        native = {'conclusion': 'weak', 'checks': {'negatives': {'status': 'fail'}}}
        record = {'task_key': 't', 'identity': current, 'native': native,
                  'audit': {'checks': {name: {'status': 'gap', 'evidence': [io.evidence_ref(evidence, root)]}
                                       for name in CHECKS}}}
        def identity(_root, _row, path, backend):
            return {'backend': backend, 'contract': io.file_hash(path)}
        def verify(value=None):
            return verify_records(root, {'t': row}, {'t': value or record},
                                  {'t': {'native': deepcopy(native)}}, ['t'], identity)
        return evidence, contract, record, verify

    def test_changed_actual_evidence_is_detected_with_unchanged_identity(self):
        with tempfile.TemporaryDirectory() as d:
            evidence, _, _, verify = self.fixture(Path(d))
            self.assertTrue(verify()['passed'])
            io.write_json(evidence, {'actual_rows': 0})
            result = verify()
            self.assertFalse(result['passed'])
            self.assertTrue(result['tasks'][0]['identity_current'])
            self.assertFalse(result['tasks'][0]['evidence_complete'])
            self.assertEqual(result['tasks'][0]['invalid_evidence'][0]['path'], 'observed.json')

    def test_current_identity_native_promotion_and_unexecuted_check_each_fail(self):
        with tempfile.TemporaryDirectory() as d:
            _, contract, record, verify = self.fixture(Path(d))
            old = contract.read_bytes(); io.write_json(contract, {'version': 'changed'})
            self.assertFalse(verify()['tasks'][0]['identity_current'])
            contract.write_bytes(old)
            promoted = deepcopy(record); promoted['native']['conclusion'] = 'validated'
            self.assertFalse(verify(promoted)['tasks'][0]['native_unchanged'])
            pending = deepcopy(record); pending['audit']['checks']['reference']['status'] = 'pending'
            self.assertFalse(verify(pending)['tasks'][0]['audit14_review_complete'])
            self.assertTrue(verify()['passed'])


if __name__ == '__main__':
    unittest.main()
