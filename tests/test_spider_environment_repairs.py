import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scripts'))
from build_spider_v4 import merge_manifest
from reconcile_spider_repairs import classify


class FrozenPackageRepairTests(unittest.TestCase):
    def test_keeps_original_packages_and_adds_only_missing_files(self):
        original = {'models/a.sql': {'sha256': 'model'},
                    'dbt_packages/dbt_utils/a.sql': {'sha256': 'original'}}
        additions = {'dbt_packages/dbt_duckdb_utils/b.sql': {'sha256': 'frozen'}}
        result = merge_manifest(original, additions)
        self.assertEqual(result['dbt_packages/dbt_utils/a.sql']['sha256'], 'original')
        self.assertEqual(result['models/a.sql']['sha256'], 'model')
        self.assertEqual(len(result), 3)
        self.assertEqual(len(original), 2)

    def test_refuses_replacing_existing_package_even_with_same_bytes(self):
        existing = {'dbt_packages/asana_source/a.sql': {'sha256': 'v080'}}
        for digest in ('v080', 'v082'):
            with self.assertRaisesRegex(ValueError, 'Refuse to replace'):
                merge_manifest(existing, {'dbt_packages/asana_source/a.sql': {'sha256': digest}})

    def test_rejects_non_package_and_traversal_additions(self):
        for name in ('models/a.sql', 'dbt_packages/../models/a.sql', '/dbt_packages/a.sql'):
            with self.assertRaises(ValueError):
                merge_manifest({}, {name: {'sha256': 'bad'}})


class RepairedProbeTests(unittest.TestCase):
    def test_actual_success_only_certifies_environment(self):
        value = classify({'exit_code': 0}, None)
        self.assertEqual(value['status'], 'pass')
        self.assertIn('not a solved-task claim', value['reason'])

    def test_failure_cannot_become_template_gap(self):
        for code in (1, 2, None):
            with self.assertRaises(ValueError):
                classify({'exit_code': code}, None)
            with self.assertRaises(ValueError):
                classify({'exit_code': code}, {'status': 'pass', 'reason': 'ignore'})

    def test_reviewed_failure_preserves_observed_reason(self):
        review = {'status': 'fail', 'reason': 'Actual native SQL error retained'}
        self.assertEqual(classify({'exit_code': 1}, review), review)
        with self.assertRaises(ValueError):
            classify({'exit_code': 137}, review)
