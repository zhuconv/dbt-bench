from copy import deepcopy
import importlib.util
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

ROOT=Path(__file__).resolve().parents[1]
sys.path[:0]=[str(ROOT/'src'),str(ROOT/'scripts')]
from difficulty_ade_session import source_session_guard
from difficulty_ade_session_collect import selected_credentials


class SessionSourceTests(unittest.TestCase):
    def setUp(self):
        self.manifest={'metadata':{'project_name':'f1'},'macros':{},'nodes':{
            'model.f1.age':{'resource_type':'model','language':'sql','original_file_path':'models/age.sql',
                'config':{'materialized':'table','pre-hook':[],'post-hook':[]},
                'compiled_code':'select current_date as day'}}}

    def test_arbitrary_normal_sql_is_not_an_author_hash_allowlist(self):
        for sql in ['select current_date as day','with dates as(select current_date as x)select x as day from dates',
                    "{{ config(materialized='table') }} select current_date as day"]:
            self.assertTrue(source_session_guard(self.manifest,{'models/age.sql':sql})['supported'])

    def test_executable_jinja_and_session_hooks_do_not_gain_false_certification(self):
        for sql in ["{% do run_query('SET TimeZone=UTC') %}select current_date",'select query(dynamic_sql)']:
            with self.assertRaises(ValueError):source_session_guard(self.manifest,{'models/age.sql':sql})
        altered=deepcopy(self.manifest);altered['nodes']['model.f1.age']['config']['pre-hook']=['set TimeZone=UTC']
        with self.assertRaisesRegex(ValueError,'hook/header'):
            source_session_guard(altered,{'models/age.sql':'select current_date'})

    def test_custom_materialization_and_project_macros_are_explicit_unknown_boundaries(self):
        for macro in [{'name':'anything','package_name':'f1'},
                      {'name':'materialization_table_custom','package_name':'other'}]:
            altered=deepcopy(self.manifest);altered['macros']={'macro.test':macro}
            with self.assertRaises(ValueError):source_session_guard(altered,{'models/age.sql':'select current_date'})


@unittest.skipUnless(importlib.util.find_spec('yaml'),'Locked runtime PyYAML required')
class ProfileTests(unittest.TestCase):
    def test_only_selected_profile_is_read_and_never_rewritten(self):
        with tempfile.TemporaryDirectory()as tmp:
            p=Path(tmp);database=p/'f1.duckdb'
            (p/'dbt_project.yml').write_text("name: f1\nprofile: f1\nseeds:\n  post-hook: '{% harmless seed template %}'\n")
            profile="f1:\n  target: dev\n  outputs:\n    dev:\n      type: duckdb\n      path: ./f1.duckdb\n      schema: main\n      settings:\n        TimeZone: UTC\n"
            (p/'profiles.yml').write_text(profile)
            with patch.dict('os.environ',{},clear=True):
                result=selected_credentials(p,database)
            self.assertEqual(result['selected']['settings'],{'TimeZone':'UTC'})
            self.assertEqual((p/'profiles.yml').read_text(),profile)

    def test_custom_connection_initializer_does_not_execute(self):
        with tempfile.TemporaryDirectory()as tmp:
            p=Path(tmp);(p/'dbt_project.yml').write_text('name: f1\nprofile: f1\n')
            (p/'profiles.yml').write_text('f1:\n  target: dev\n  outputs:\n    dev:\n      type: duckdb\n      path: ./f1.duckdb\n      plugins: [candidate_code]\n')
            with patch.dict('os.environ',{},clear=True),self.assertRaisesRegex(ValueError,'initializer'):
                selected_credentials(p,p/'f1.duckdb')

if __name__=='__main__':unittest.main()
