from copy import deepcopy
import unittest
from propose_difficulty_split_v2 import summarize_task


def attempt(n, native, audit, **changes):
    return {'attempt': n, 'trial_id': 'trial'+str(n), 'finished_at': '2026-11-08T00:00:00Z',
            'capture': {'path': 'captured'}, 'native_validity': {'reward_valid': True},
            'native_success': native, 'audit_reward': audit, 'audit_receipt': {'path': 'audit'+str(n)},
            'execution_valid': audit is not None, **changes}


class SplitAccountingV2Test(unittest.TestCase):
    def test_both_tests_must_pass_and_native_count_does_not_define_stratum(self):
        rows=[attempt(1,True,1),attempt(2,True,0),attempt(3,False,1),attempt(4,False,0)]
        s=summarize_task(rows)
        self.assertEqual((s['native_success_count'],s['k'],s['nominal_stratum']),(2,1,'1-3'))

    def test_executed_unknown_is_neither_pending_nor_a_confirmed_success(self):
        rows=[attempt(i,True,None)for i in range(1,5)];before=deepcopy(rows)
        s=summarize_task(rows)
        self.assertTrue(s['ready_for_draft']);self.assertEqual(s['valid_attempt_count'],4)
        self.assertEqual((s['native_success_count'],s['k'],s['audit_unknown_count']),(4,0,4))
        self.assertEqual(s['possible_strata'],['0','1-3','4']);self.assertTrue(s['stratum_sensitive_audit_unknown'])
        self.assertEqual(rows,before)

    def test_unknown_can_leave_stratum_fixed_or_cross_boundary(self):
        rows=[attempt(1,True,1),attempt(2,True,None),attempt(3,False,0),attempt(4,False,None)]
        s=summarize_task(rows)
        self.assertEqual(s['audit_unknown_count'],2)
        self.assertEqual(s['possible_k_range'],[1,2]);self.assertFalse(s['stratum_sensitive_audit_unknown'])
        rows=[attempt(i,True,1)for i in range(1,4)]+[attempt(4,True,None)]
        s=summarize_task(rows)
        self.assertEqual(s['possible_strata'],['1-3','4']);self.assertTrue(s['stratum_sensitive_audit_unknown'])

    def test_native_failure_unknown_cannot_change_zero_stratum(self):
        s=summarize_task([attempt(i,False,None)for i in range(1,5)])
        self.assertEqual((s['k'],s['audit_unknown_count'],s['native_pass_audit_unknown_count']),(0,4,0))
        self.assertEqual(s['possible_strata'],['0']);self.assertFalse(s['stratum_sensitive_audit_unknown'])

    def test_infrastructure_invalid_excluded_but_model_native_budget_counted(self):
        rows=[attempt(i,False,0)for i in range(1,5)]
        rows[0].update(native_validity={'reward_valid':False},exception_info={'exception_type':'EnvironmentStartTimeoutError'})
        s=summarize_task(rows)
        self.assertEqual(s['valid_attempt_count'],3);self.assertFalse(s['ready_for_draft'])
        rows[0].update(native_validity={'reward_valid':True},exception_info={'exception_type':'AgentTimeoutError'})
        s=summarize_task(rows)
        self.assertEqual(s['valid_attempt_count'],4);self.assertEqual(s['k'],0);self.assertTrue(s['ready_for_draft'])

    def test_audit_not_run_and_missing_fourth_attempt_block_draw(self):
        rows=[attempt(i,True,1)for i in range(1,5)]
        rows[0].update(audit_receipt=None,audit_reward=None)
        s=summarize_task(rows)
        self.assertEqual((s['audit_unknown_count'],s['audit_pending_count']),(0,1));self.assertFalse(s['ready_for_draft'])
        self.assertFalse(summarize_task(rows[1:])['ready_for_draft'])
        with self.assertRaises(ValueError):summarize_task(rows+[rows[-1]])
