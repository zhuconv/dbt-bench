"""Regressions for source delivery, immutable inputs and actual SQL clock joins."""
from copy import deepcopy
from datetime import datetime, timezone
import gzip
import hashlib
import importlib.util
import json
from pathlib import Path
import sys
import tempfile
import unittest

ROOT=Path(__file__).resolve().parents[1]
sys.path[:0]=[str(ROOT/'src'),str(ROOT/'scripts')]
from difficulty_ade_inputs import validate_initial
from difficulty_ade_clock import join_clocks, SQL
from difficulty_ade_registry import ENTRIES, contract_record
from reconcile_difficulty_v2 import audit_for_trial, verify_adapter_dependencies
from dbt_bench import io

HAS_YAML=importlib.util.find_spec('yaml') is not None
if HAS_YAML:
    from prepare_difficulty_audit_v2 import source_plan


class InitialInputTests(unittest.TestCase):
    def capture(self, path, *, mtime=1, rows=None):
        path.mkdir()
        rows=rows if rows is not None else [{'id':9007199254740993,'value':None}]
        raw=json.dumps({'rows':rows}).encode()
        payload=gzip.compress(raw,mtime=mtime)
        (path/'raw.json.gz').write_bytes(payload)
        doc={'unchanged':True,'captured_at_utc':str(mtime),'sql_clock':str(mtime),
             'initial_database_sha256':'same','relations':{'raw':{'rows':len(rows),
             'compact_file':'raw.json.gz','compact_sha256':hashlib.sha256(payload).hexdigest(),
             'physical_schema':[['id','BIGINT'],['value','VARCHAR']]}}}
        (path/'inputs.json').write_text(json.dumps(doc))
        return path/'inputs.json'

    def test_volatile_gzip_header_does_not_change_raw_inputs(self):
        with tempfile.TemporaryDirectory() as tmp:
            p=Path(tmp);left=self.capture(p/'left',mtime=10);right=self.capture(p/'right',mtime=20)
            self.assertNotEqual((left.parent/'raw.json.gz').read_bytes(),(right.parent/'raw.json.gz').read_bytes())
            self.assertTrue(validate_initial(left,right))

    def test_large_integer_raw_change_is_rejected(self):
        with tempfile.TemporaryDirectory() as tmp:
            p=Path(tmp);left=self.capture(p/'left');right=self.capture(p/'right',rows=[{'id':9007199254740992,'value':None}])
            with self.assertRaisesRegex(ValueError,'relations differ'):validate_initial(left,right)

    def test_modified_archive_cannot_be_hidden_by_same_metadata(self):
        with tempfile.TemporaryDirectory() as tmp:
            p=Path(tmp);left=self.capture(p/'left');right=self.capture(p/'right')
            (left.parent/'raw.json.gz').write_bytes(gzip.compress(b'{}'))
            with self.assertRaisesRegex(ValueError,'archive changed'):validate_initial(left,right)


class ClockTests(unittest.TestCase):
    def setUp(self):
        self.probe={'database':'/app/f1.duckdb','sql':SQL,'input_unchanged':True,'error':None,
                    'current_date':'2026-11-08','timezone':'UTC','capture_sha256':'capture',
                    'finished_at_utc':'2026-11-08T04:00:00+00:00'}
        self.native={'started_at':'2026-11-08T04:00:01','finished_at':'2026-11-08T04:01:00'}

    def join(self, original=None, before=None, after=None, zone='UTC', native=None):
        return join_clocks(self.probe if original is None else original,
                self.probe if before is None else before,self.probe if after is None else after,
                self.native if native is None else native,original_capture_sha256='capture',verified_session_timezone=zone)

    def test_same_recorded_day_with_independent_session_evidence(self):
        self.assertEqual(self.join()['status'],'joined')

    def test_new_day_never_becomes_a_wrong_answer_for_old_submission(self):
        later=dict(self.probe,current_date='2026-11-09')
        result=self.join(before=later,after=later)
        self.assertEqual(result['status'],'unknown');self.assertNotIn('reward',result)

    def test_missing_session_evidence_and_missing_original_are_unknown(self):
        self.assertEqual(self.join(zone=None)['status'],'unknown')
        self.assertEqual(join_clocks(None,self.probe,self.probe,self.native,
            original_capture_sha256='capture',verified_session_timezone='UTC')['status'],'unknown')

    def test_wrong_binding_mutated_db_and_midnight_remain_unknown(self):
        for original in [dict(self.probe,capture_sha256='other'),dict(self.probe,input_unchanged=False)]:
            self.assertEqual(self.join(original=original)['status'],'unknown')
        self.assertEqual(self.join(native=dict(self.native,finished_at='2026-11-09T00:00:01'))['status'],'unknown')


class ContractTests(unittest.TestCase):
    def test_fifteen_explicit_contracts_and_only_one_clock_sensitive(self):
        self.assertEqual(len(ENTRIES),15)
        self.assertEqual({k for k,v in ENTRIES.items() if v.get('clock_sensitive')},{'ade__ade-bench-f1009'})
        self.assertEqual({k for k,v in ENTRIES.items() if v['submission_kind']=='diagnostic_dbt_model_source'},
            {'ade__ade-bench-f1003','ade__ade-bench-f1003-hard','ade__ade-bench-f1011'})

    def test_native_source_materialization_must_be_actual_reviewed_path(self):
        with tempfile.TemporaryDirectory() as tmp:
            batch=Path(tmp);task=batch/'tasks/control';(task/'tests').mkdir(parents=True)
            (task/'instruction.md').write_text('Fix the dbt model.')
            (task/'tests/test-setup.sh').write_text('dbt run --profiles-dir /app\n')
            (task/'tests/mechanical-native-test.sh').write_text('bash /tests/test-setup.sh\n')
            q={'task_key':'ade__ade-bench-ana-eng005','phase3_identity':{'batch_manifest':{'path':str(batch/'manifest.json')},'label':'control'}}
            row=contract_record(q)
            self.assertFalse(row['actual_model_dbt_execution_required'])
            self.assertFalse(row['actual_pre_native_database_required_for_score'])
            self.assertTrue(row['preserve_original_database'])
            (task/'tests/test-setup.sh').write_text('echo no source materialization\n')
            with self.assertRaisesRegex(ValueError,'materialization path'):contract_record(q)


class ReceiptTests(unittest.TestCase):
    def test_native_one_oracle_control_cannot_become_a_difficulty_attempt(self):
        with tempfile.TemporaryDirectory() as tmp:
            p=Path(tmp)/'receipt.json'
            value={'fixture_only':True,'training_prohibited':True,
                'task_key':'ade__ade-bench-ana-eng005','task_version':'fixture',
                'original_result':{'path':'original','sha256':'same'},
                'qualified_review':{'path':'qualification','sha256':'same'},
                'original_native_rewards':{'reward':1.0},'audit_reward':1.0}
            p.write_text(json.dumps(value))
            with self.assertRaisesRegex(ValueError,'fixture'):
                audit_for_trial(p,value['original_result'],value['task_key'],'fixture',value['qualified_review'])

    def test_changed_adapter_dependency_invalidates_a_receipt(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);(root/'code.py').write_text('source=1\n')
            (root/'checker.py').write_text('expected=1\n');(root/'image.sif').write_bytes(b'image')
            manifest={'code_refs':[io.evidence_ref(root/'code.py',root)],
                      'checker_dependencies':[io.evidence_ref(root/'checker.py',root)],
                      'image':io.evidence_ref(root/'image.sif',root)}
            io.write_json(root/'dependencies.json',manifest)
            receipt={'task_key':'ade__ade-bench-ana-eng005',
                     'adapter_dependencies':io.evidence_ref(root/'dependencies.json',root)}
            verify_adapter_dependencies(receipt,root)
            (root/'checker.py').write_text('expected=2\n')
            with self.assertRaisesRegex(ValueError,'dependency changed'):
                verify_adapter_dependencies(receipt,root)


@unittest.skipUnless(HAS_YAML,'Locked runtime with PyYAML required for source-plan integration')
class ActualSourceTests(unittest.TestCase):
    def test_alternate_model_path_and_profile_are_preserved_without_author_allowlist(self):
        with tempfile.TemporaryDirectory() as tmp:
            p=Path(tmp)
            values={'dbt_project.yml':'name: arbitrary\nmodel-paths: [custom_models]\nprofile: delivered\n',
                    'profiles.yml':'delivered:\n  target: new_target\n',
                    'custom_models/new.sql':'select 9007199254740993 as customer_id\n',
                    'target/manifest.json':'untrusted generated metadata'}
            files=[]
            for name,text in values.items():
                f=p/name;f.parent.mkdir(parents=True,exist_ok=True);f.write_text(text)
                files.append({'path':name,'sha256':hashlib.sha256(text.encode()).hexdigest(),
                              'captured_source':{'path':str(f),'sha256':hashlib.sha256(text.encode()).hexdigest()}})
            old={'initial_trees':[{'relative_path':'models','files':[{'path':'old.sql','sha256':'old'}]}],
                 'initial_files':[]}
            payload,names,plan=source_plan({'source_dir':str(p),'source_files':files},old,{'fresh_artifact_paths':['phase3_target']})
            self.assertIn('custom_models/new.sql',names)
            self.assertIn('profiles.yml',names)
            self.assertNotIn('target/manifest.json',names)
            self.assertEqual(payload['delete_paths'],['models/old.sql'])
            self.assertEqual((p/'profiles.yml').read_text(),values['profiles.yml'])

if __name__=='__main__':unittest.main()
