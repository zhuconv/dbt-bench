"""Registration must add only the newly qualified task and preserve history."""
from copy import deepcopy
from pathlib import Path
import sys
import unittest

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT / 'src'), str(ROOT / 'scripts')]
from prepare_repeat_purchase_measurement_v1 import extend_population, KEY, VERSION


class RepeatRegistrationTests(unittest.TestCase):
    def setUp(self):
        rows = []
        for source, count in [('ade', 15), ('deb', 35), ('spider2', 16)]:
            rows += [{'task_key': f'{source}-{i}', 'source': source, 'eligible_for_measurement': True,
                      'historical_split': 'test' if i % 4 == 0 else 'train', 'task_family': f'family-{i}'}
                     for i in range(count)]
        rows += [{'task_key': f'excluded-{i}', 'source': 'deb', 'eligible_for_measurement': False} for i in range(68)]
        rows.append({'task_key': KEY, 'task_version': VERSION, 'source': 'deb',
                     'eligible_for_measurement': False, 'qualified_review': None,
                     'historical_split': 'test', 'task_family': 'deb-customer-value-retention',
                     'frozen_pool_row': {'audit': 'pending', 'eligible_for_sampling': False}})
        self.previous = {'version': 'difficulty-population-v1', 'eligible_count': 66, 'tasks': rows,
                         'training_prohibited': True, 'phase4_review_complete': True,
                         'split_rules': {'path': 'unchanged', 'sha256': 'historical'}}
        self.record = {'task_key': KEY, 'task_version': VERSION, 'eligible_for_measurement': True,
                       'eligible_for_sampling': True, 'training_use_allowed': False,
                       'audit': {'conclusion': 'validated', 'check_counts': {'pass': 14}},
                       'native': {'conclusion': 'weak', 'review_complete': True},
                       'reproduction': {'native1_both_clean_builds': True},
                       'formal_candidate_evaluation': {'passed': True, 'scores': [1] * 6}}

    def register(self):
        return extend_population(self.previous, self.record, {'path': 'old', 'sha256': 'old'}, {'path': 'review', 'sha256': 'review'})

    def test_one_addition_preserves_all_history_and_other_rows(self):
        before = deepcopy(self.previous)
        result = self.register()
        self.assertEqual(self.previous, before)
        self.assertEqual(result['tasks'][:-1], before['tasks'][:-1])
        for name in ('frozen_pool_row', 'historical_split', 'task_family', 'task_version'):
            self.assertEqual(result['tasks'][-1][name], before['tasks'][-1][name])
        self.assertEqual(result['split_rules'], before['split_rules'])
        self.assertEqual(result['eligible_count'], 67)
        self.assertEqual(result['eligible_by_source'], {'ade': 15, 'deb': 36, 'spider2': 16})
        self.assertFalse(result['registration']['frozen'])

    def test_candidate_score_cannot_be_replaced_by_qualification(self):
        self.record['formal_candidate_evaluation']['scores'][-1] = 0
        with self.assertRaises(ValueError): self.register()

    def test_partial_audit_or_failed_native_rejected(self):
        original = deepcopy(self.record)
        for mutation in ('pending', 'partial', 'native_failed'):
            self.record = deepcopy(original)
            if mutation == 'pending': self.record['audit']['conclusion'] = 'pending'
            if mutation == 'partial': self.record['audit']['check_counts'] = {'pass': 13, 'gap': 1}
            if mutation == 'native_failed': self.record['reproduction']['native1_both_clean_builds'] = False
            with self.subTest(mutation=mutation), self.assertRaises(ValueError): self.register()

    def test_wrong_version_or_foreign_task_rejected(self):
        original = deepcopy(self.record)
        for field in ('task_key', 'task_version'):
            self.record = deepcopy(original); self.record[field] = 'different'
            with self.subTest(field=field), self.assertRaises(ValueError): self.register()

    def test_duplicate_population_id_rejected(self):
        self.previous['tasks'][0]['task_key'] = self.previous['tasks'][1]['task_key']
        with self.assertRaises(ValueError): self.register()


if __name__ == '__main__':
    unittest.main()
