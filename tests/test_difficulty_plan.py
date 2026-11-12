import unittest
from prepare_difficulty_plan import plan_tasks


class DifficultyPlanTest(unittest.TestCase):
    def tasks(self):
        return [{'task_key':s,'source':s,'task_version':'v1','eligible_for_measurement':True}
                for s in ('ade','spider2','deb')]

    def test_exactly_four_slots_with_reuse_and_native_budget_chunks(self):
        plan=plan_tasks(self.tasks(),[{'task_key':'ade','task_version':'v1','attempt_ids':[1,2,3,4]},
                                      {'task_key':'deb','task_version':'v1','attempt_ids':[1]}])
        self.assertEqual((plan['planned_attempts'],plan['existing_attempts'],plan['new_attempts']),(12,5,7))
        self.assertEqual([r['attempt_ids'] for r in plan['remaining'] if r['source']=='deb'],[[2],[3],[4]])
        self.assertEqual([r['attempt_ids'] for r in plan['remaining'] if r['source']=='spider2'],[[1,2],[3,4]])

    def test_duplicate_slots_and_unapproved_versions_fail_before_preparation(self):
        old={'task_key':'ade','task_version':'v1','attempt_ids':[1]}
        with self.assertRaises(ValueError):plan_tasks(self.tasks(),[old,old])
        with self.assertRaises(ValueError):plan_tasks(self.tasks(),[{**old,'task_version':'old'}])

    def test_ineligible_tasks_do_not_gain_planned_attempts(self):
        tasks=self.tasks();tasks[0]['eligible_for_measurement']=False
        self.assertEqual(plan_tasks(tasks,[])['planned_attempts'],8)
