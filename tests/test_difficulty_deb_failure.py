"""Actual-evidence boundary tests for the narrow DEB failure evaluator."""
import unittest
from copy import deepcopy
from difficulty_deb_failure_v2 import evaluate

class FailureBoundaryTests(unittest.TestCase):
 def setUp(self):
  self.db='/app/database/retail.duckdb';self.rules={'loyalty_analytics.program_loyalty_summary':['program_id']}
  self.capture={'complete':True,'inventory_complete':True,'errors':[], 'databases':[{'path':self.db,'exists':True,'sha256':'original'},{'path':self.db+'.wal','exists':False}]}
  self.inventory={'inventory_complete':True,'database_and_wal_unchanged':True,'database':self.db,'hashes_before':{self.db:'original',self.db+'.wal':None},'hashes_after':{self.db:'original',self.db+'.wal':None},'inventory':{'loyalty_analytics.program_loyalty_summary':{'exists':True,'columns':['PROGRAM_ID']}}}
 def call(self):return evaluate(self.capture,self.inventory,self.rules)
 def test_healthy_never_earns_success_or_failure(self):self.assertIsNone(self.call()['reward'])
 def test_absent_required_relation_is_zero(self):
  self.inventory['inventory'][next(iter(self.rules))]={'exists':False,'columns':[]};self.assertEqual(self.call()['reward'],0)
 def test_absent_required_column_is_zero(self):
  self.inventory['inventory'][next(iter(self.rules))]['columns']=['wrong_name'];self.assertEqual(self.call()['reward'],0)
 def test_incomplete_capture_cannot_earn_zero(self):
  self.capture['inventory_complete']=False;self.inventory['inventory']={};self.assertIsNone(self.call()['reward'])
 def test_capture_error_cannot_earn_zero(self):self.capture['errors']=['EACCES'];self.assertIsNone(self.call()['reward'])
 def test_missing_catalog_probe_is_unknown(self):self.assertIsNone(evaluate(self.capture,None,self.rules)['reward'])
 def test_foreign_database_hash_cannot_earn_zero(self):
  self.inventory['hashes_before'][self.db]='other';self.assertIsNone(self.call()['reward'])
 def test_foreign_wal_cannot_earn_zero(self):
  self.inventory['hashes_before'][self.db+'.wal']='other';self.assertIsNone(self.call()['reward'])
 def test_after_probe_database_change_is_unknown(self):
  self.inventory['hashes_after'][self.db]='other';self.assertIsNone(self.call()['reward'])
 def test_missing_relation_entry_not_assumed_absent(self):
  self.inventory['inventory']={};self.assertIsNone(self.call()['reward'])
 def test_ambiguous_column_case_is_unknown(self):
  self.inventory['inventory'][next(iter(self.rules))]['columns']=['program_id','PROGRAM_ID'];self.assertIsNone(self.call()['reward'])
 def test_missing_project_not_automatically_wrong(self):
  self.capture['project_observation']={'exists':False};self.assertIsNone(self.call()['reward'])
 def test_absent_database_requires_two_observations(self):
  self.capture['databases'][0]={'path':self.db,'exists':False};self.assertIsNone(self.call()['reward'])
  self.capture['absence_observations']=[{'role':'database','path':self.db,'stable_absence':True,'before':{'exists':False,'errno':2},'after':{'exists':False,'errno':2,'path':self.db}}]
  self.assertEqual(self.call()['reward'],0)
  self.capture['absence_observations'][0]['after']['errno']=13;self.assertIsNone(self.call()['reward'])

if __name__=='__main__':unittest.main()
