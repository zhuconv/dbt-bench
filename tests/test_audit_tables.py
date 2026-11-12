"""Shared audit must distinguish submission errors from absent/corrupt evidence."""
from dataclasses import replace
from datetime import date
from decimal import Decimal
import hashlib
import json
from pathlib import Path
import sys
import tempfile
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scripts'))
from audit_tables import (CaptureError, CapturedTable, Column, TableContract,
                          TablePolicy, audit_table, decode, load_observer_table)


class TableAuditTests(unittest.TestCase):
    def policy(self, *, tolerance=0, extra=False):
        return TablePolicy(TableContract((Column('id', 'integer'),
            Column('value', 'numeric', abs_tol=Decimal(str(tolerance)))), keys=('id',),
            allow_extra_columns=extra), {'id': ('BIGINT',), 'value': ('DOUBLE',)}, ('id',))

    def table(self, rows, **kwargs):
        values = dict(columns=('id', 'value'), sql_types={'id': 'BIGINT', 'value': 'DOUBLE'},
            rows=rows, physical_columns=('id', 'value'),
            physical_sql_types={'id': 'BIGINT', 'value': 'DOUBLE'}, relation_type='BASE TABLE',
            capture_ref={'path': 'synthetic-fixture', 'sha256': 'synthetic'})
        values.update(kwargs)
        return CapturedTable(**values)

    def test_integer_and_large_integer_ids_are_exact(self):
        for a, b in ((10000, 10001), (9007199254740992, 9007199254740993)):
            result = audit_table(self.table([{'id': b, 'value': 42.0}]), self.policy(tolerance=1),
                                 [{'id': a, 'value': 42.0}])
            self.assertEqual(result['reward'], 0)

    def test_tolerance_aligns_keys_without_rounding_sort(self):
        expected = [{'id': 1, 'value': 0.004}, {'id': 2, 'value': 0.006}]
        candidate = self.table([{'id': 2, 'value': 0.004}, {'id': 1, 'value': 0.006}])
        self.assertEqual(audit_table(candidate, self.policy(tolerance='.01'), expected)['reward'], 1)

    def test_reordered_rows_are_legal_but_shuffled_values_are_not(self):
        expected = [{'id': 1, 'value': 10}, {'id': 2, 'value': 20}]
        self.assertEqual(audit_table(self.table(expected[::-1]), self.policy(), expected)['reward'], 1)
        wrong = [{'id': 1, 'value': 20}, {'id': 2, 'value': 10}]
        self.assertEqual(audit_table(self.table(wrong), self.policy(), expected)['reward'], 0)

    def test_duplicate_and_missing_rows_rejected(self):
        expected = [{'id': 1, 'value': 10}, {'id': 2, 'value': 20}]
        for rows in ([expected[0], expected[0]], expected[:1], expected + expected[:1]):
            self.assertEqual(audit_table(self.table(rows), self.policy(), expected)['reward'], 0)

    def test_empty_table_still_checks_missing_column(self):
        candidate = self.table([], columns=('id',), sql_types={'id': 'BIGINT'},
                               physical_columns=('id',), physical_sql_types={'id': 'BIGINT'})
        self.assertEqual(audit_table(candidate, self.policy(), [])['reward'], 0)

    def test_projection_does_not_hide_physical_extra_column(self):
        candidate = self.table([], physical_columns=('id', 'value', 'secret'),
                               physical_sql_types={'id': 'BIGINT', 'value': 'DOUBLE', 'secret': 'VARCHAR'})
        self.assertEqual(audit_table(candidate, self.policy(), [])['reward'], 0)
        self.assertEqual(audit_table(candidate, self.policy(extra=True), [])['reward'], 1)

    def test_unobserved_required_column_is_unknown(self):
        candidate = self.table([], columns=('id',), sql_types={'id': 'BIGINT'})
        self.assertIsNone(audit_table(candidate, self.policy())['reward'])

    def test_bool_is_not_integer_key(self):
        candidate = self.table([{'id': True, 'value': 42}])
        self.assertEqual(audit_table(candidate, self.policy())['reward'], 0)

    def test_missing_relation_type_is_unknown_not_wrong_table(self):
        policy = replace(self.policy(), relation_types=('BASE TABLE',))
        self.assertIsNone(audit_table(self.table([], relation_type=None), policy)['reward'])
        self.assertEqual(audit_table(self.table([], relation_type='VIEW'), policy)['reward'], 0)

    def test_sql_type_matters_even_for_empty_table(self):
        policy = TablePolicy(TableContract((Column('day', 'date'),)), {'day': ('DATE',)})
        table = CapturedTable(('day',), {'day': 'TIMESTAMP'}, [], ('day',),
                              {'day': 'TIMESTAMP'}, 'BASE TABLE', {})
        self.assertEqual(audit_table(table, policy, [])['reward'], 0)

    def test_invalid_expected_is_checker_unknown(self):
        bad = [{'id': 1, 'value': 10}, {'id': 1, 'value': 20}]
        self.assertIsNone(audit_table(self.table(bad), self.policy(), bad)['reward'])

    def test_typed_decode_is_lossless(self):
        self.assertEqual(decode(9007199254740993), 9007199254740993)
        self.assertIs(type(decode(True)), bool)
        self.assertEqual(decode({'$type': 'decimal', 'value': '0.010000000000001'}), Decimal('0.010000000000001'))
        self.assertEqual(decode({'$type': 'date', 'value': '2024-09-08'}), date(2024, 9, 8))
        with self.assertRaises(CaptureError):
            decode({'$type': 'novel', 'value': 'ignored'})


class CaptureTests(unittest.TestCase):
    def fixture(self, root):
        data = b'[9007199254740993,{"$type":"float","value":"42.0"}]\n'
        (root / 'output.jsonl').write_bytes(data)
        schema = [{'name': 'id', 'type': 'BIGINT'}, {'name': 'value', 'type': 'DOUBLE'}]
        value = {'task_key': 'synthetic', 'stage': 'before', 'outputs': [{
            'specification': {'database': '/db', 'relation': 'main.output'},
            'query': 'SELECT * FROM "main"."output"', 'schema': schema, 'sql_schema': schema,
            'relation_sql_schema': schema, 'relation_metadata': [{'table_type': 'BASE TABLE'}],
            'file': 'output.jsonl', 'rows': 1, 'sha256': hashlib.sha256(data).hexdigest()}]}
        return value

    def load(self, root, value):
        path = root / 'snapshot.json'
        path.write_text(json.dumps(value))
        return load_observer_table({'path': str(path), 'sha256': hashlib.sha256(path.read_bytes()).hexdigest()},
            task_key='synthetic', stage='before', database='/db', relation='main.output')

    def test_hash_and_row_count_are_verified(self):
        with tempfile.TemporaryDirectory() as name:
            root = Path(name); value = self.fixture(root)
            self.assertEqual(self.load(root, value).rows[0]['id'], 9007199254740993)
            value['outputs'][0]['rows'] = 2
            with self.assertRaises(CaptureError): self.load(root, value)
            value['outputs'][0]['rows'] = 1
            (root / 'output.jsonl').write_text('[1,42]\n')
            with self.assertRaises(CaptureError): self.load(root, value)

    def test_old_full_scan_inventory_accepted_but_projection_unknown(self):
        with tempfile.TemporaryDirectory() as name:
            root = Path(name); value = self.fixture(root)
            del value['outputs'][0]['relation_sql_schema']
            self.assertEqual(self.load(root, value).physical_columns, ('id', 'value'))
            value['outputs'][0]['specification']['columns'] = ['id', 'value']
            with self.assertRaises(CaptureError): self.load(root, value)

    def test_duplicate_schema_and_wrong_stage_rejected(self):
        with tempfile.TemporaryDirectory() as name:
            root = Path(name); value = self.fixture(root)
            value['stage'] = 'after'
            with self.assertRaises(CaptureError): self.load(root, value)
            value['stage'] = 'before'
            value['outputs'][0]['sql_schema'].append({'name': 'id', 'type': 'BIGINT'})
            with self.assertRaises(CaptureError): self.load(root, value)

    def test_failed_capture_not_treated_as_empty_output(self):
        with tempfile.TemporaryDirectory() as name:
            root = Path(name); value = self.fixture(root)
            value['outputs'][0]['error'] = {'message': 'database unavailable'}
            with self.assertRaises(CaptureError): self.load(root, value)


if __name__ == '__main__':
    unittest.main()
