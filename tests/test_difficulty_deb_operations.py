"""Actual-action and current-source evidence boundaries for operations audits."""
from pathlib import Path
import tempfile
import unittest
import sys
import hashlib

ROOT=Path(__file__).resolve().parents[1]
sys.path[:0]=[str(ROOT/'src'),str(ROOT/'scripts')]
try:
    from difficulty_deb_phase4_operations import action_gate, parsed, FAILURE_REQUIREMENTS
    from difficulty_deb_operations_collect import RAW,MODELS,PROJECTS
except ModuleNotFoundError as exc:
    if exc.name!='yaml':raise
    raise unittest.SkipTest('Use locked runtime with PyYAML')from exc


class OperationsDifficultyTests(unittest.TestCase):
    def events(self,echo=True,profiles=()):
        return {'echo_before_first_write':echo,'profile_build_calls':[{'profile_directories':list(profiles)}], 'source_logs':[]}

    def test_risk_profiles_dir_argument_is_not_rejected_for_missing_export(self):
        self.assertEqual(action_gate('dbt-customer-risk-scoring',self.events(),'/app/dbt_project')['reward'],1)

    def test_category_no_unrequested_export_obligation(self):
        self.assertEqual(action_gate('dbt-product-category-analytics',self.events(),'/app/dbt_project')['reward'],1)

    def test_supplier_no_unrequested_export_obligation(self):
        self.assertEqual(action_gate('dbt-supplier-scorecard',self.events(),'/app/dbt_project')['reward'],1)

    def test_actual_echo_after_code_is_failure(self):
        for task in PROJECTS:
            with self.subTest(task=task):self.assertEqual(action_gate(task,self.events(False),PROJECTS[task])['reward'],0)

    def test_audit_environment_cannot_replace_missing_original_actions(self):
        for task in PROJECTS:
            with self.subTest(task=task):self.assertIsNone(action_gate(task,self.events(None,[PROJECTS[task]]),PROJECTS[task])['reward'])

    def test_daily_explicit_profile_environment_requires_original_evidence(self):
        self.assertIsNone(action_gate('dbt-daily-order-summary',self.events(),'/app/dbt_project')['reward'])
        self.assertEqual(action_gate('dbt-daily-order-summary',self.events(profiles=['/app/dbt_project']),'/app/dbt_project')['reward'],1)

    def test_different_project_environment_does_not_satisfy_action(self):
        self.assertIsNone(action_gate('dbt-fraud-detection-model',self.events(profiles=['/tmp/project']),'/app/dbt_models_duckdb')['reward'])

    def test_optional_staging_alias_absence_is_not_declared_final_failure(self):
        self.assertEqual(set(FAILURE_REQUIREMENTS['dbt-product-category-analytics']),{'sales_analytics.int_product_sales','sales_analytics.fct_category_performance'})
        self.assertNotIn('inventory_analytics.stg_inventory_levels',FAILURE_REQUIREMENTS['dbt-inventory-analysis'])

    def test_parsed_sql_cannot_be_borrowed_from_another_manifest(self):
        with tempfile.TemporaryDirectory()as directory:
            path=Path(directory)/'manifest.json';path.write_text('{}')
            command={'manifest':{'nodes':{'model.p.x':{'compiled_code':'select 1','name':'x'}}},'manifest_path':path,'run':{'command':['dbt','run']}}
            context={'fresh':{'commands':[command]}}
            actual={'manifest':{'sha256':'0'*64},'unique_id':'model.p.x','compiled_sha256':hashlib.sha256(b'select 1').hexdigest(),'ast':{}}
            self.assertEqual(parsed(context,{'parsed':[actual]})[0],[])

    def test_same_manifest_different_sql_is_rejected(self):
        with tempfile.TemporaryDirectory()as directory:
            path=Path(directory)/'manifest.json';path.write_text('{}')
            command={'manifest':{'nodes':{'model.p.x':{'compiled_code':'select 1','name':'x'}}},'manifest_path':path,'run':{'command':['dbt','run']}}
            context={'fresh':{'commands':[command]}}
            actual={'manifest':{'sha256':hashlib.sha256(path.read_bytes()).hexdigest()},'unique_id':'model.p.x','compiled_sha256':hashlib.sha256(b'select 2').hexdigest(),'ast':{}}
            with self.assertRaisesRegex(ValueError,'Parsed source changed'):parsed(context,{'parsed':[actual]})

    def test_fixed_raw_queries_do_not_read_gold_or_output_relations(self):
        for task,queries in RAW.items():
            for query in queries.values():
                with self.subTest(task=task,query=query):
                    self.assertNotIn('gold',query.lower())
                    for target in FAILURE_REQUIREMENTS[task]:self.assertNotIn('from '+target.lower(),query.lower())

if __name__=='__main__':unittest.main()
