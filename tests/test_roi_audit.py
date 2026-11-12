from copy import deepcopy
from decimal import Decimal
import importlib.util
from pathlib import Path
import unittest
import tempfile

from tests.test_roi_independent import fixture

path = Path(__file__).resolve().parents[1] / 'validation/deb/dbt-retail-task-04-market-roi/audit.py'
spec = importlib.util.spec_from_file_location('roi_audit', path)
audit = importlib.util.module_from_spec(spec)
spec.loader.exec_module(audit)


def artifacts():
    sql = "select * from {{ ref('stg_fact_marketing_spend') }}"
    document = {'version': 2, 'models': [{'name': audit.MODEL,
        'columns': [{'name': col, 'data_tests': ['not_null']} for col in sorted(audit.NOT_NULL)],
        'data_tests': [{'dbt_utils.unique_combination_of_columns': {'arguments': {'combination_of_columns': list(audit.KEY)}}}]}]}
    manifest = {'nodes': {
        'model.x.'+audit.MODEL: {'name': audit.MODEL, 'resource_type': 'model', 'raw_code': sql,
                               'depends_on': {'nodes': ['model.x.stg_fact_marketing_spend']}},
        'model.x.stg_fact_marketing_spend': {'name': 'stg_fact_marketing_spend', 'resource_type': 'model'}}}
    return sql, document, manifest


class RoiAuditTests(unittest.TestCase):
    def test_legal_numeric_representations_keep_exact_integer_values(self):
        self.assertEqual(audit.exact_counts([{'impressions': Decimal('20.0'), 'channel_key': 9007199254740993}]),
                         [{'impressions': 20, 'channel_key': 9007199254740993}])
        self.assertEqual(audit.exact_counts([{'impressions': '20.000', 'channel_key': 1.0}]),
                         [{'impressions': 20, 'channel_key': 1}])
        for invalid in (Decimal('20.25'), 'NaN', float('inf'), True):
            with self.subTest(invalid=invalid), self.assertRaises(ValueError):
                audit.exact_counts([{'impressions': invalid}])

    def test_data_tests_arguments_form_and_column_order_are_legal(self):
        sql, document, manifest = artifacts()
        self.assertEqual(audit.artifact_checks(sql, document, manifest), [])
        document['models'][0]['data_tests'][0]['dbt_utils.unique_combination_of_columns']['arguments']['combination_of_columns'].reverse()
        self.assertEqual(audit.artifact_checks(sql, document, manifest), [])

    def test_correct_output_does_not_hide_missing_yaml_tests(self):
        rows, _ = audit.recompute(fixture())
        sql, document, manifest = artifacts()
        document['models'][0]['columns'] = []
        result = audit.evaluate(fixture(), rows, sql, document, manifest)
        self.assertTrue(result['comparison']['equal'])
        self.assertFalse(result['success'])
        self.assertEqual(len(result['artifact_issues']), 6)

    def test_stale_manifest_and_disallowed_dependencies_are_rejected(self):
        sql, document, manifest = artifacts()
        node = manifest['nodes']['model.x.'+audit.MODEL]
        node['raw_code'] = 'select 1'
        node['depends_on']['nodes'].append('source.x.secret')
        self.assertEqual(len(audit.artifact_checks(sql, document, manifest)), 2)

    def test_dbt_manifest_outer_whitespace_normalization_is_legal(self):
        sql, document, manifest = artifacts()
        self.assertEqual(audit.artifact_checks(sql + '\n', document, manifest), [])
        self.assertEqual(audit.artifact_checks('\n' + sql + '\n', document, manifest), [])

    def test_fixture_positive_and_business_negative_do_not_invoke_native_grader(self):
        rows, _ = audit.recompute(fixture())
        values = artifacts()
        self.assertTrue(audit.evaluate(fixture(), rows, *values)['success'])
        wrong = deepcopy(rows)
        wrong[0]['impressions'] = Decimal('20.25')
        self.assertFalse(audit.evaluate(fixture(), wrong, *values)['success'])
        self.assertFalse(audit.evaluate(fixture(), rows + rows[:1], *values)['success'])

    def test_documentation_is_not_conflated_with_an_extra_output_column(self):
        sql, document, manifest = artifacts()
        document['models'][0]['description'] = 'Unrequested description'
        self.assertIn('documentation', ' '.join(audit.artifact_checks(sql, document, manifest)))

    def test_snapshot_absence_tampering_and_wrong_collection_phase(self):
        with tempfile.TemporaryDirectory() as tmp:
            folder = Path(tmp)
            source = folder/'submission.sql'
            source.write_text('select 1')
            snapshot = {'phase': 'after_agent_before_native_verifier', 'read_only_database': True,
                        'dbt_invoked': False, 'errors': [], 'files': {
                            'submission.sql': {'sha256': audit.file_hash(source)},
                            'submission.yml': {'absent': True}}}
            required = ['submission.sql', 'submission.yml']
            self.assertEqual(audit.validate_snapshot(folder, snapshot, required), ['submission.yml'])
            source.write_text('select 2')
            with self.assertRaisesRegex(ValueError, 'changed'):
                audit.validate_snapshot(folder, snapshot, required)
            source.write_text('select 1')
            snapshot['phase'] = 'after_verifier'
            with self.assertRaisesRegex(ValueError, 'phase'):
                audit.validate_snapshot(folder, snapshot, required)
