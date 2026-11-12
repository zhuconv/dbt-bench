"""Synthetic provenance regressions; these never certify benchmark attempts."""
from copy import deepcopy
from pathlib import Path
import tempfile
import unittest

from dbt_bench import io
from difficulty_collect_v2 import collect
from difficulty_receipt_binding import source_binding, prepared_reference, original_actions, delivery_basis


class ReceiptBindingTest(unittest.TestCase):
    def setup_case(self, root):
        key='deb__synthetic'; task=root/'task'; task.mkdir()
        (task/'instruction.md').write_text('Deliver a model source.\n')
        (task/'tests').mkdir(); (task/'tests/test_outputs.py').write_text('# synthetic native build\n')
        project=root/'project'; project.mkdir(); (project/'model.sql').write_text('select 1\n')
        run=root/'run'; trial=run/'jobs/run/trial'; destination=trial/'artifacts/difficulty-submission'
        collect({'task_key':key,'project_root':str(project),'database_paths':[]},destination)
        io.write_json(trial/'artifacts/manifest.json',[{'source':'/tmp/dbt-bench-difficulty-submission',
            'destination':'artifacts/difficulty-submission','status':'ok','type':'directory'}])
        io.write_json(root/'review.json',{'fixture_only':True,'synthetic_contract':True})
        qualified=io.evidence_ref(root/'review.json',root)
        config={'tasks':[{'path':str(task)}],'job_name':'run','jobs_dir':str(run/'jobs')}
        io.write_json(run/'config.yaml',config)
        identity={'purpose':'difficulty_only_no_training','task_key':key,'task_version':'v1','config':config,
            'actual_task_hash':io.tree_hash(task),'reviewed_production':{key:{'qualified_review':qualified}}}
        io.write_json(run/'manifest.json',{'identity':identity,'experiment_id':io.digest(identity),
            'config_hash':io.file_hash(run/'config.yaml')})
        io.write_json(trial/'result.json',{'config':{'task':{'path':str(task)},'agent':{'name':'qwen-coder'}},
            'agent_execution':{'finished_at':'2020-01-01T00:00:00+00:00'},
            'verifier':{'started_at':'2030-01-01T00:00:00+00:00'}})
        receipt={'task_key':key,'task_version':'v1','fixture_only':False,'evidence_kind':'direct_contract_failure',
            'qualified_review':qualified,'original_result':io.evidence_ref(trial/'result.json',root)}
        binding={k:receipt[k] for k in ('task_key','task_version','fixture_only','qualified_review','original_result')}
        binding.update(prepared_model_run=io.evidence_ref(run/'manifest.json',root),
            capture=io.evidence_ref(destination/'capture.json',root),actual_model_trial=True)
        self.save_binding(root,receipt,binding)
        return receipt,binding,trial

    def save_binding(self,root,receipt,binding):
        io.write_json(root/'binding.json',binding)
        receipt['source_binding']=io.evidence_ref(root/'binding.json',root)

    def test_coherent_capture_and_published_prepared_alias(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);r,b,_=self.setup_case(root)
            source_binding(r,root)
            b['prepared_run']=b.pop('prepared_model_run');self.save_binding(root,r,b)
            source_binding(r,root)

    def test_fixture_relabel_and_original_result_swap_are_rejected(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);r,b,_=self.setup_case(root)
            b['fixture_only']=True;self.save_binding(root,r,b)
            with self.assertRaisesRegex(ValueError,'Fixture label'):
                source_binding(r,root)
            b['fixture_only']=False;self.save_binding(root,r,b)
            io.write_json(root/'other-result.json',{'not_executed':True})
            r['original_result']=io.evidence_ref(root/'other-result.json',root)
            with self.assertRaisesRegex(ValueError,'original_result'):
                source_binding(r,root)

    def test_inner_actual_model_and_native_adapter_cannot_be_relabelled(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);r,b,trial=self.setup_case(root)
            b['actual_model_trial']=False;self.save_binding(root,r,b)
            with self.assertRaisesRegex(ValueError,'not an actual model'):
                source_binding(r,root)
            b['actual_model_trial']=True
            raw=io.read_json(trial/'result.json');raw['config']['agent']['name']='oracle'
            io.write_json(trial/'result.json',raw)
            b['original_result']=r['original_result']=io.evidence_ref(trial/'result.json',root)
            self.save_binding(root,r,b)
            with self.assertRaisesRegex(ValueError,'native model adapter'):
                source_binding(r,root)

    def test_conflicting_prepared_aliases_rejected(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);r,b,_=self.setup_case(root)
            b['prepared_run']=r['qualified_review']
            with self.assertRaisesRegex(ValueError,'aliases'):
                prepared_reference(b,root)

    def test_changed_production_source_invalidates_binding(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);r,_,_=self.setup_case(root)
            (root/'task/instruction.md').write_text('Changed instruction\n')
            with self.assertRaisesRegex(ValueError,'task bytes changed'):
                source_binding(r,root)

    def test_action_logs_must_be_from_original_trial(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);r,_,trial=self.setup_case(root);c=source_binding(r,root)
            log=trial/'agent/session.json';io.write_json(log,{'synthetic':True})
            actions=root/'actions.json';io.write_json(actions,{'source_logs':[io.evidence_ref(log,root)]})
            original_actions(io.evidence_ref(actions,root),c,root)
            wrong=root/'audit-rebuild/agent/session.json';io.write_json(wrong,{'synthetic':True})
            io.write_json(actions,{'source_logs':[io.evidence_ref(wrong,root)]})
            with self.assertRaisesRegex(ValueError,'another trial'):
                original_actions(io.evidence_ref(actions,root),c,root)

    def test_delivery_basis_requires_matching_task_and_qualification(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);r,_,_=self.setup_case(root);c=source_binding(r,root)
            contract={k:r[k] for k in ('task_key','task_version','qualified_review')}
            contract.update(scoring_basis='submitted_source_reconstruction',
                instruction=io.evidence_ref(root/'task/instruction.md',root),
                native_build_path=io.evidence_ref(root/'task/tests/test_outputs.py',root))
            io.write_json(root/'score.json',{});r['audit_result']=io.evidence_ref(root/'score.json',root)
            r['scoring_basis']='submitted_source_reconstruction'
            for version,accepted in [('v1',True),('another-version',False)]:
                changed=deepcopy(contract);changed['task_version']=version
                io.write_json(root/'delivery.json',changed);r['delivery_contract_ref']=io.evidence_ref(root/'delivery.json',root)
                if accepted:self.assertEqual(delivery_basis(r,c,root),'submitted_source_reconstruction')
                else:
                    with self.assertRaisesRegex(ValueError,'task_version'):delivery_basis(r,c,root)
