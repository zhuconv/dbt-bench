"""Independent failure checks must neither rewrite nor invent frozen rewards."""
from pathlib import Path
from unittest.mock import patch
import sys
import unittest

ROOT=Path(__file__).resolve().parents[1]
sys.path[:0]=[str(ROOT/'scripts'),str(ROOT/'src')]
try:
    import difficulty_spider_failure_v2 as failure
except ModuleNotFoundError as exc:
    if exc.name!='yaml':raise
    raise unittest.SkipTest('Locked runtime PyYAML required')from exc


class SpiderFailureTests(unittest.TestCase):
    def setUp(self):
        self.q={'raw':{'database':'/workspace/x.duckdb'},'overlay':{'contract':{'path':'contract'}},
                'record':{'audit':{'overlay':{'path':'overlay'}}}}
        self.capture={'databases':[{'path':'/workspace/x.duckdb','exists':True,'sha256':'a'},
                                  {'path':'/workspace/x.duckdb.wal','exists':False}]}
        self.probe={'database_and_wal_unchanged':True,'hashes_before':{'/workspace/x.duckdb':'a','/workspace/x.duckdb.wal':None},
                    'hashes_after':{'/workspace/x.duckdb':'a','/workspace/x.duckdb.wal':None},
                    'relation_inventory':[{'database':'/workspace/x.duckdb','relation':'report','exists':True,'query_succeeded':True}]}
        self.patch1=patch.object(failure,'required_output_evidence',return_value={'rule_ids':['required-report'],'rule_evidence':[{'path':'contract'}]})
        self.patch2=patch.object(failure,'load',return_value={'deliverables':[{'table':'report','database':'/workspace/x.duckdb'}]})
        self.patch1.start();self.patch2.start();self.addCleanup(self.patch1.stop);self.addCleanup(self.patch2.stop)

    def test_complete_healthy_relation_cannot_prove_failure(self):
        with self.assertRaisesRegex(ValueError,'No independently observed'):failure.relation_rules(self.capture,self.q,self.probe)

    def test_query_failure_is_not_relation_absence(self):
        self.probe['relation_inventory'][0].update(exists=False,query_succeeded=False)
        with self.assertRaisesRegex(ValueError,'incomplete'):failure.relation_rules(self.capture,self.q,self.probe)

    def test_another_database_probe_cannot_score_this_submission(self):
        self.probe['hashes_before']['/workspace/x.duckdb']='b';self.probe['hashes_after']['/workspace/x.duckdb']='b'
        with self.assertRaisesRegex(ValueError,'original captured'):failure.relation_rules(self.capture,self.q,self.probe)

    def test_unrequested_relation_absence_is_not_a_task_failure(self):
        self.probe['relation_inventory'].append({'database':'/workspace/x.duckdb','relation':'unrequested','exists':False,'query_succeeded':True})
        with self.assertRaisesRegex(ValueError,'No independently observed'):failure.relation_rules(self.capture,self.q,self.probe)

    def test_successful_inventory_of_declared_missing_relation_proves_only_that_rule(self):
        self.probe['relation_inventory'][0]['exists']=False
        result=failure.relation_rules(self.capture,self.q,self.probe)
        self.assertEqual(result['missing_required_relations'],[{'database':'/workspace/x.duckdb','relation':'report'}])
        self.assertEqual(result['rule_ids'],['required-report'])

    def test_old_or_incomplete_absence_capture_cannot_be_promoted(self):
        with self.assertRaisesRegex(ValueError,'complete v2'):failure.absence_rules({'schema_version':1},self.q)
        with self.assertRaisesRegex(ValueError,'complete v2'):failure.absence_rules({'schema_version':2,'inventory_complete':False},self.q)

if __name__=='__main__':unittest.main()
