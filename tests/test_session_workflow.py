import copy
import importlib.util
from pathlib import Path
import sys
import unittest
from unittest.mock import patch

BASE = Path(__file__).resolve().parents[1] / 'validation/deb/dbt-session-attribution'


def load(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


semantics = load('session_workflow_independent', BASE / 'semantics/independent.py')
with patch.dict(sys.modules, {'independent': semantics}):
    workflow = load('session_workflow_common', BASE / 'workflow/common.py')


class SessionWorkflowTests(unittest.TestCase):
    def setUp(self):
        self.expected = [{'channel': 'A', 'total_conversions': '1.00', 'total_revenue': '10.00'},
                         {'channel': 'B', 'total_conversions': '2.00', 'total_revenue': '20.00'}]
        self.before = {'model_present': True, 'model_sha256': 'original',
                       'staging_sources': {'models/staging/existing.sql': 'existing'},
                       'query_channels': ['A', 'B'], 'extra_relation_type': None,
                       'wrong_schema_output': None,
                       'required_output': {'relation_type': 'BASE TABLE',
                                           'columns': list(self.expected[0]), 'rows': copy.deepcopy(self.expected)}}

    def test_table_scan_order_does_not_reject_a_correct_model_query(self):
        after = copy.deepcopy(self.before)
        after['required_output']['rows'].reverse()
        self.assertTrue(workflow.assess('idempotent-control', self.before, [after, after], self.expected)['established'])
        # A descending scan alone is not proof of an incorrectly ordered SELECT.
        with self.assertRaises(AssertionError):
            workflow.assess('descending-query', self.before, [after], self.expected)
        after['query_channels'].reverse()
        self.assertTrue(workflow.assess('descending-query', self.before, [after], self.expected)['established'])

    def test_changed_row_association_is_not_proof_of_append_behavior(self):
        first = copy.deepcopy(self.before)
        after = copy.deepcopy(first)
        after['required_output']['rows'] *= 2
        self.assertTrue(workflow.assess('append-on-repeat', self.before, [first, after], self.expected)['established'])
        after['required_output']['rows'] = [dict(r, total_revenue='999') for r in after['required_output']['rows']]
        with self.assertRaises(AssertionError):
            workflow.assess('append-on-repeat', self.before, [first, after], self.expected)

    def test_extra_staging_case_must_preserve_existing_staging(self):
        after = copy.deepcopy(self.before)
        after['staging_sources']['models/staging/stg_validation_extra.sql'] = 'new'
        after['extra_relation_type'] = 'VIEW'
        self.assertTrue(workflow.assess('new-staging', self.before, [after], self.expected)['established'])
        after['staging_sources']['models/staging/existing.sql'] = 'changed'
        with self.assertRaises(AssertionError):
            workflow.assess('new-staging', self.before, [after], self.expected)

    def test_wrong_schema_requires_successfully_built_correct_values(self):
        after = copy.deepcopy(self.before)
        after['wrong_schema_output'] = after['required_output']
        after['required_output'] = None
        self.assertTrue(workflow.assess('wrong-schema', self.before, [after], self.expected)['established'])
        after['wrong_schema_output']['rows'].pop()
        with self.assertRaises(AssertionError):
            workflow.assess('wrong-schema', self.before, [after], self.expected)
