import importlib.util
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from tests import test_customer_audit as fixtures

ROOT=Path(__file__).resolve().parents[1]
SPEC=importlib.util.spec_from_file_location('customer_v2',ROOT/'validation/ade/ade-bench-ana-eng003/audit_v2/audit.py')
audit=importlib.util.module_from_spec(SPEC);SPEC.loader.exec_module(audit)


class CustomerAuditV2Tests(unittest.TestCase):
    def setUp(self):
        self.fixture=fixtures.CustomerAuditTests();self.fixture.setUp()
        self.rows=self.fixture.expected

    def output(self,rows):
        return {'columns':list(rows[0]),'rows':rows}

    def test_original_values_and_order_equivalence(self):
        rows=[dict(reversed(list(r.items()))) for r in reversed(self.rows)]
        self.assertTrue(audit.output_interface(self.output(rows))['pass'])
        self.assertTrue(audit.base.compare_output(self.fixture.source,self.output(rows),{})['equal'])

    def test_v1_acceptance_does_not_override_new_output_policy(self):
        rows=[{**r,'extra_information':'derived-column'} for r in self.rows]
        output=self.output(rows)
        self.assertTrue(audit.base.compare_output(self.fixture.source,output,{})['equal'])
        result=audit.output_interface(output)
        self.assertFalse(result['pass'])
        self.assertEqual(result['extra_columns'],['extra_information'])

    def test_required_nullable_attribute_cannot_be_dropped(self):
        rows=[{k:v for k,v in r.items() if k!='notes'} for r in self.rows]
        result=audit.output_interface(self.output(rows))
        self.assertFalse(result['pass']);self.assertEqual(result['missing_columns'],['notes'])

    def test_duplicate_column_inventory_and_malformed_rows(self):
        output=self.output(self.rows);output['columns'].append('customer_id')
        self.assertFalse(audit.output_interface(output)['pass'])
        output=self.output(self.rows);output['columns'].append('unrecorded')
        with self.assertRaises(ValueError):audit.output_interface(output)

    def test_same_schema_still_requires_valid_values_and_multiplicity(self):
        rows=self.rows+[self.rows[0]]
        self.assertTrue(audit.output_interface(self.output(rows))['pass'])
        self.assertFalse(audit.base.compare_output(self.fixture.source,self.output(rows),{})['equal'])

    def test_full_snapshot_evaluation_and_tamper_null(self):
        with tempfile.TemporaryDirectory() as d:
            directory=Path(d)
            self.fixture.make_export(directory/'before','before')
            self.fixture.make_export(directory/'after-native-build','after-native-build')
            with patch.object(audit.base,'INPUT_SHA',audit.base.file_hash(directory/'before/input.json')):
                result=audit.evaluate(directory)
                self.assertEqual((result['status'],result['reward']),('scored',1.0))
                path=directory/'after-native-build/output.json'
                output=json.loads(path.read_text());output['rows'][0]['notes']='tampered'
                path.write_text(json.dumps(output))
                result=audit.evaluate(directory)
                self.assertEqual(result['status'],'error');self.assertIsNone(result['reward'])


if __name__=='__main__':unittest.main()
