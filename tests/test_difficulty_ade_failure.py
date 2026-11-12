from copy import deepcopy
import hashlib
import importlib.util
from pathlib import Path
import sys
import tempfile
import unittest

ROOT=Path(__file__).resolve().parents[1]
sys.path[:0]=[str(ROOT/'src'),str(ROOT/'scripts')]
from difficulty_ade_prepare_clock_v2 import assert_unlaunched,SEALED_MARKERS

HAS_YAML=importlib.util.find_spec('yaml')is not None
if HAS_YAML:
    from difficulty_ade_failure_score_v2 import independent_failure


class PreparationGuardTests(unittest.TestCase):
    def test_each_seal_submit_or_launch_marker_blocks_augmentation(self):
        for marker in SEALED_MARKERS:
            with self.subTest(marker=marker),tempfile.TemporaryDirectory()as tmp:
                p=Path(tmp);assert_unlaunched(p)
                (p/marker).mkdir()if marker=='jobs'else(p/marker).write_text('{}')
                with self.assertRaisesRegex(ValueError,'sealed/submitted/started'):assert_unlaunched(p)


@unittest.skipUnless(HAS_YAML,'Locked PyYAML runtime required for audit execution helpers')
class DirectFailureTests(unittest.TestCase):
    def setUp(self):
        self.policy={'models':['fact_inventory']}
        self.names=['dbt_project.yml','models/fact_inventory.sql']
        digest=hashlib.sha256(b'SELECT FROM').hexdigest()
        self.manifest={'metadata':{'invocation_id':'actual'},'nodes':{
            'model.task.fact_inventory':{'resource_type':'model','name':'fact_inventory',
                'checksum':{'checksum':digest},'compiled_code':'SELECT FROM','depends_on':{'nodes':[]}}}}
        self.runs={'metadata':{'invocation_id':'actual'},'results':[{'unique_id':'model.task.fact_inventory','status':'error'}]}
        self.probe={'invocation_id':'actual','input_unchanged':True,'proofs':[{
            'node_id':'model.task.fact_inventory','proved_failure':True,
            'typed_exception':'ParserException','manifest_source_sha256':digest,
            'compiled_sql_sha256':digest}]}

    def evaluate(self,probe=None,manifest=None,runs=None):
        return independent_failure(self.policy,self.names,manifest or self.manifest,runs or self.runs,probe or self.probe)

    def test_source_only_correct_model_is_not_absent_because_database_is_initial(self):
        self.assertIsNone(independent_failure(self.policy,self.names))

    def test_complete_source_absence_is_contract_failure(self):
        result=independent_failure(self.policy,['dbt_project.yml','models/unrelated.sql'])
        self.assertEqual(result['missing_models'],['fact_inventory'])
        # Potential package or Python implementations prevent false absence.
        self.assertIsNone(independent_failure(self.policy,['dbt_packages/pkg/models/fact_inventory.sql']))
        self.assertIsNone(independent_failure(self.policy,['models/fact_inventory.py']))

    def test_typed_parser_proof_is_distinct_from_error_text(self):
        self.assertEqual(self.evaluate()['rule_ids'],['ADE.required_dbt_model.compiles_on_fixed_inputs'])
        for changes in [{'proved_failure':False,'detail':'Parser Error'},
                        {'typed_exception':'IOException','detail':'Parser Error'},
                        {'typed_exception':'OutOfMemoryException'}]:
            altered=deepcopy(self.probe);altered['proofs'][0].update(changes)
            self.assertIsNone(self.evaluate(altered))

    def test_external_or_changed_evidence_is_not_a_zero(self):
        self.assertIsNone(self.evaluate(dict(self.probe,input_unchanged=False)))
        with self.assertRaisesRegex(ValueError,'another invocation'):
            self.evaluate(dict(self.probe,invocation_id='stale'))
        altered=deepcopy(self.probe);altered['proofs'][0]['compiled_sql_sha256']='other'
        with self.assertRaisesRegex(ValueError,'source differs'):self.evaluate(altered)

    def test_unrelated_failing_model_does_not_prove_required_model_failure(self):
        altered=deepcopy(self.probe);altered['proofs'][0]['node_id']='model.task.unrelated'
        self.assertIsNone(self.evaluate(altered))

if __name__=='__main__':unittest.main()
