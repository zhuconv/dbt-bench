"""Phase4 authorization, new-version identity and executed-review boundaries."""
from copy import deepcopy
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scripts'))
from dbt_bench import io
from phase4_inventory import initial_candidates, selected_directory
from phase4_prepare import plan_entry
from phase4_publish import merge_candidates, record_exclusion, stage, freeze
from phase4_capture import target_artifact_prefix
from phase4_report import completion_status
from phase4_nop import nop_entry


class Phase4Tests(unittest.TestCase):
    def fixture(self, root):
        key = 'deb__dbt-example'
        for name, text in [('pilot/dbt-example/instruction.md', 'original'),
                           ('pilot/dbt-example_v2/instruction.md', 'clarified'),
                           ('tasks/example/instruction.md', 'original'), ('record.json', '{}')]:
            p = root / name; p.parent.mkdir(parents=True, exist_ok=True); p.write_text(text)
        row = {'task_key': key, 'source': 'deb', 'split': 'test', 'upstream_task_id': 'dbt-example',
               'run_path': 'tasks/example', 'validation_ref': 'record.json'}
        results = {'dbt-example': {'outcome': 'patched_pass'}}
        return row, results

    def test_only_deb_test_can_enter_formal_plan(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp); row, results = self.fixture(root)
            call = lambda: plan_entry({'task_key': row['task_key']}, {row['task_key']: row}, results, root/'pilot', root)
            identity = call()
            self.assertEqual(identity['split'], 'test')
            self.assertTrue(identity['task_version'].startswith('phase4-instruction-'))
            row['split'] = 'train'
            with self.assertRaises(ValueError): call()
            row['split'] = 'test'; row['source'] = 'spider2'
            with self.assertRaises(ValueError): call()

    def test_excluded_result_cannot_run_as_a_formal_candidate(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp); row, results = self.fixture(root)
            for excluded in ('gold_error', 'degenerate_empty', 'native_only'):
                results['dbt-example']['outcome'] = excluded
                with self.subTest(excluded=excluded), self.assertRaises(ValueError):
                    plan_entry({'task_key': row['task_key']}, {row['task_key']: row}, results, root/'pilot', root)

    def test_variant_changes_identity_without_changing_original(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp); row, results = self.fixture(root)
            call = lambda: plan_entry({'task_key': row['task_key']}, {row['task_key']: row}, results, root/'pilot', root)
            before = call()
            (root/'pilot/dbt-example_v2/instruction.md').write_text('different clarification')
            self.assertNotEqual(before['task_version'], call()['task_version'])
            self.assertEqual((root/'tasks/example/instruction.md').read_text(), 'original')
            (root/'pilot/dbt-example/instruction.md').write_text('unrelated original')
            with self.assertRaisesRegex(ValueError, 'different original'): call()

    def test_case_spelling_is_explicit_and_ambiguous_versions_fail(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp); row, results = self.fixture(root)
            self.assertEqual(selected_directory(root/'pilot','dbt-example','patched_pass').name,'dbt-example_v2')
            upper=root/'pilot/dbt-example_V2'; upper.mkdir(); (upper/'instruction.md').write_text('ambiguous')
            with self.assertRaisesRegex(ValueError,'exactly one'): selected_directory(root/'pilot','dbt-example','patched_pass')

    def test_phase4_pending_or_excluded_rows_never_change_train_history(self):
        rows=[{'task_key':'a','source':'ade','split':'train','eligible_for_sampling':True},
              {'task_key':'b','source':'deb','split':'test','eligible_for_sampling':True}]
        old=deepcopy(rows); new=initial_candidates(rows,{'b'})
        self.assertEqual(new[0],old[0]);self.assertEqual(rows,old);self.assertFalse(new[1]['eligible_for_sampling'])
        with self.assertRaises(ValueError): initial_candidates(rows,{'a'})
        with self.assertRaises(ValueError): merge_candidates(rows,{'a':{'split':'train'}})

    def test_native_overlay_is_mandatory_even_for_unchanged_instruction(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp)
            with patch('phase4_publish.verify_protected',return_value=0), patch('phase4_publish.precheck',return_value={}):
                with self.assertRaisesRegex(ValueError,'current native14'):
                    stage(root,None,root/'audit.json',root/'reconcile.json',root,candidate_review=root/'candidate.json')

    def test_exclusion_needs_checked_evidence_and_cannot_enter_pool(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp); proof=root/'proof.json';io.write_json(proof,{'actual_investigation':'retained evidence'})
            previous=io.evidence_ref(proof,root)
            inventory={'tasks':[{'task_key':'deb__x','category':'gold_error','formal_execution_required':False,'previous_record':previous}]}
            io.write_json(root/'inventory.json',inventory);io.write_json(root/'protected-files.json',[])
            io.write_json(root/'state.json',{'inventory':io.evidence_ref(root/'inventory.json',root)})
            value={'task_key':'deb__x','category':'gold_error','model_requests':0,'review_status':'reviewed',
                   'eligible_for_sampling':False,'previous_record':previous,'reason':'checked current evidence','evidence':[]}
            p=root/'review.json';io.write_json(p,value)
            with self.assertRaisesRegex(ValueError,'evidence'):record_exclusion(root,p,root)
            value['evidence']=[previous];io.write_json(p,value)
            self.assertTrue(record_exclusion(root,p,root)['excluded'])
            with self.assertRaisesRegex(ValueError,'already recorded'):record_exclusion(root,p,root)

    def test_final_freeze_requires_three_exclusion_investigations(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);io.write_json(root/'protected-files.json',[])
            io.write_json(root/'inventory.json',{'formal_tasks':1,'tasks':[{'task_key':'excluded','formal_execution_required':False}]})
            io.write_json(root/'state.json',{'inventory':io.evidence_ref(root/'inventory.json',root),'completed':{},'ready':{'formal':{} }})
            with self.assertRaisesRegex(ValueError,'three explicit exclusion'):freeze(root,final=True,root=root)

    def test_absolute_multproject_target_is_bound_to_actual_capture_root(self):
        command=['dbt','run','--project-dir','/app/reference','--target-path','/app/project/phase4_upstream_target_v1']
        self.assertEqual(target_artifact_prefix(command,'/app/project'),'phase4_upstream_target_v1')
        self.assertEqual(target_artifact_prefix(['dbt','run','--target-path','target'],'/app/project'),'target')
        for command in (['dbt','run','--target-path','../outside'],['dbt','run','--project-dir','/app/reference']):
            with self.assertRaisesRegex(ValueError,'outside'):target_artifact_prefix(command,'/app/project')

    def test_completed_checks_do_not_invent_audit_certification(self):
        rows=[{'task_key':'formal','formal_execution_required':True,'native_current':'weak','audit_current':'pending'},
              {'task_key':'excluded','formal_execution_required':False,'exclusion_review':{'path':'proof'}}]
        result=completion_status(rows,1,expected=1)
        self.assertTrue(result['phase4_review_complete']);self.assertFalse(result['phase4_validation_complete'])
        rows[1]['exclusion_review']=None
        self.assertFalse(completion_status(rows,1,expected=1)['phase4_review_complete'])

    def test_nop_control_cannot_install_author_source_or_run_commands(self):
        request={'task_key':'deb__x','label':'blind','rebuild':{'commands':[['dbt','run']],
            'capture_hooks':[{'source':'collector'}], 'submission':{'files':[{'source':'author.sql'}],
            'reset_paths':['models'],'delete_paths':['old.sql']},'fresh_artifact_paths':['target']}}
        before=deepcopy(request); nop=nop_entry(request)
        self.assertEqual(request,before); self.assertEqual(nop['rebuild']['mode'],'nop')
        self.assertEqual(nop['rebuild']['commands'],[]);self.assertEqual(nop['rebuild']['capture_hooks'],[])
        self.assertEqual(nop['rebuild']['submission'],{'files':[],'reset_paths':[],'delete_paths':[]})


if __name__=='__main__':unittest.main()
