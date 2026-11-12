from copy import deepcopy
from pathlib import Path
import sys
import tempfile
import unittest

ROOT=Path(__file__).resolve().parents[1]
sys.path[:0]=[str(ROOT/'src'),str(ROOT/'scripts')]
from dbt_bench import io
from difficulty_ade_session_v2 import review
from difficulty_ade_clock import SQL


class CapturedProfileBindingTests(unittest.TestCase):
    def build(self,directory):
        p=Path(directory)
        for name,text in [('profiles.yml','profile bytes'),('dbt_project.yml','project bytes'),('age.sql','select current_date')]:
            (p/name).write_text(text)
        capture={'project_root':'/app','project_files':[{'path':n,'sha256':io.file_hash(p/n)}for n in ('profiles.yml','dbt_project.yml')]}
        io.write_json(p/'capture.json',capture);reference=io.evidence_ref(p/'capture.json',p)
        manifest={'metadata':{'project_name':'f1'},'macros':{},'nodes':{'model.f1.age':{
            'name':'age','resource_type':'model','language':'sql','original_file_path':'models/age.sql',
            'config':{'materialized':'table','pre-hook':[],'post-hook':[]},'compiled_code':'select current_date'}}}
        io.write_json(p/'manifest.json',manifest)
        workflow={'manifest':io.evidence_ref(p/'manifest.json',p),'sources':[{'original_file_path':'models/age.sql','file':io.evidence_ref(p/'age.sql',p)}]}
        probe={'producer':'actual_dbt_connection_manager_readonly','error':None,
            'observer_only_override':{'read_only':True,'session_settings_modified':False},
            'connection_open_calls':[{'actual_read_only':True}],
            'adapter_sources':[{'path':'adapter.py','sha256':'locked'}],
            'dbt_core_version':'locked','dbt_duckdb_version':'locked','duckdb_version':'locked',
            'session':{'profile_name':'f1','target':'dev','selected':{},'resolved_database':'/app/f1.duckdb',
                'source_files':[{'path':'/app/'+n,'sha256':io.file_hash(p/n)}for n in ('profiles.yml','dbt_project.yml')]},
            'database':'/app/f1.duckdb','sql':SQL,'input_unchanged':True,'capture_sha256':reference['sha256'],
            'current_date':'2026-11-08','timezone':'Etc/UTC','finished_at_utc':'2026-11-08T04:00:00+00:00'}
        native={'started_at':'2026-11-08T04:00:01','finished_at':'2026-11-08T04:01:00'}
        return probe,workflow,native,reference

    def test_matching_real_session_source_context_can_join(self):
        with tempfile.TemporaryDirectory()as tmp:
            probe,workflow,native,reference=self.build(tmp)
            result=review(probe,deepcopy(probe),deepcopy(probe),workflow,native,reference,tmp)
            self.assertEqual(result['status'],'joined')

    def test_profile_observed_after_capture_must_match_preserved_submission(self):
        with tempfile.TemporaryDirectory()as tmp:
            probe,workflow,native,reference=self.build(tmp);before=deepcopy(probe);after=deepcopy(probe)
            probe['session']['source_files'][0]['sha256']='different'
            result=review(probe,before,after,workflow,native,reference,tmp)
            self.assertEqual(result['status'],'unknown')
            self.assertIn('actual captured source',result['reason'])

if __name__=='__main__':unittest.main()
