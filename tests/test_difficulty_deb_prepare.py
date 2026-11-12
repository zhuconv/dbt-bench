"""Repeat scope and real after-command hook preservation."""
import unittest
from copy import deepcopy
try:
 import yaml
 from difficulty_deb_prepare_v3 import plan_builds,collector_hooks
except ImportError:
 yaml=None

@unittest.skipIf(yaml is None,'Requires PyYAML from the locked Harbor runtime')
class RepeatPlanningTests(unittest.TestCase):
 def test_cltv_has_no_added_obligation(self):
  r={'commands':[['dbt','run']], 'capture_hooks':[]};p=plan_builds(r,'deb__dbt-customer-cltv-forecasting')
  self.assertFalse(p['required']);self.assertEqual(r['commands'],[['dbt','run']])
 def test_order_explicit_requirement_adds_same_project(self):
  r={'commands':[['dbt','run']], 'capture_hooks':[]};p=plan_builds(r,'deb__dbt-customer-order-analytics')
  self.assertTrue(p['required']);self.assertEqual(r['commands'],[['dbt','run'],['dbt','run']])
 def test_distinct_upstream_build_not_repeat(self):
  r={'commands':[['dbt','run','--project-dir','upstream'],['dbt','run']], 'capture_hooks':[]};plan_builds(r,'deb__dbt-customer-order-analytics')
  self.assertEqual(len(r['commands']),3);self.assertEqual(r['commands'][-1],['dbt','run'])
 def test_nonrequired_old_duplicate_hooks_removed(self):
  r={'commands':[['dbt','deps'],['dbt','run'],['dbt','run'],['dbt','test']], 'capture_hooks':[{'phase':'initial'},{'phase':'after_command','command_index':1},{'phase':'after_command','command_index':2},{'phase':'after_command','command_index':3},{'phase':'final'}]}
  p=plan_builds(r,'deb__dbt-customer-lifetime-value');self.assertEqual(p['removed_duplicate_build_indices'],[2])
  self.assertEqual(r['commands'],[['dbt','deps'],['dbt','run'],['dbt','test']]);self.assertEqual([h['command_index']for h in r['capture_hooks']if h['phase']=='after_command'],[1,2])
 def test_existing_required_repeat_not_tripled(self):
  r={'commands':[['dbt','run'],['dbt','run']], 'capture_hooks':[]};plan_builds(r,'deb__dbt-rfm-customer-segmentation');self.assertEqual(len(r['commands']),2)
 def test_frozen_coupon_checker_repeat_preserved(self):
  r={'commands':[['dbt','run','--select','+coupon_summary']], 'capture_hooks':[]};p=plan_builds(r,'deb__dbt-coupon-effectiveness');self.assertTrue(p['required']);self.assertEqual(p['requirement_source'],'frozen_checker')
 def test_different_selectors_preserved_without_repeat(self):
  r={'commands':[['dbt','run','--select','staging'],['dbt','run','--select','mart']], 'capture_hooks':[]};plan_builds(r,'deb__dbt-customer-risk-does-not-exist');self.assertEqual(len(r['commands']),2)

if __name__=='__main__':unittest.main()
