"""Regression controls for actual-output and original-action separation."""
from pathlib import Path
from tempfile import TemporaryDirectory
import importlib.util
import json
import sys
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT/'src'), str(ROOT/'scripts')]
HAS_YAML = importlib.util.find_spec('yaml') is not None
if HAS_YAML:
    import difficulty_deb_phase4_pipeline as adapter


@unittest.skipUnless(HAS_YAML, 'Locked runtime PyYAML required')
class PipelineAdapterTests(unittest.TestCase):
    def pair(self, command=None, output=None, result_index=2):
        return {'call_id':'upstream', 'name':'run_shell_command', 'call_index':1,
            'result_index':result_index,
            'args':{'command':command or 'dbt run --project-dir /app/dbt_models_duckdb --select +int_sales__orders_enriched', 'directory':'/app/dbt_project'},
            'response':{'output':{'exitCode':0, 'stdout':output if output is not None else '1 of 1 OK created sql table model main.int_sales__orders_enriched [OK]'}}}

    def events(self, pair):
        return {'pairs':[pair], 'profile_build_calls':[{'call_index':3, 'result_index':4,
            'profile_directories':['/app/dbt_project']}], 'source_logs':[]}

    def test_original_success_and_order_required(self):
        self.assertEqual(adapter.warning_upstream_gate(self.events(self.pair()))['reward'], 1)
        self.assertIsNone(adapter.warning_upstream_gate(self.events(self.pair(result_index=4)))['reward'])

    def test_zero_exit_without_required_model_is_unknown(self):
        self.assertIsNone(adapter.warning_upstream_gate(self.events(self.pair(output='Nothing to do')))['reward'])

    def test_script_contents_do_not_prove_original_action(self):
        row=self.pair(command="python -c 'print(\"dbt run\")'")
        self.assertIsNone(adapter.warning_upstream_gate(self.events(row))['reward'])

    def test_reconstruction_cannot_fill_missing_original_actions(self):
        self.assertIsNone(adapter.warning_upstream_gate({'pairs':[], 'profile_build_calls':[]})['reward'])

    def test_explicit_reference_project_and_selection(self):
        self.assertTrue(adapter.upstream_commands('cd /app/dbt_models_duckdb && dbt run --select +int_sales__orders_enriched', '/app'))
        self.assertFalse(adapter.upstream_commands('dbt run --select +int_sales__orders_enriched', '/app/dbt_project'))
        self.assertFalse(adapter.upstream_commands('dbt run --select different_model', '/app/dbt_models_duckdb'))
        self.assertIsNone(adapter.upstream_commands('dbt run || true', '/app/dbt_models_duckdb'))

    def test_rfm2_does_not_gain_rerun_obligation(self):
        result=adapter.idempotence('dbt-rfm-customer-segmentation-2', {'commands':[]})
        self.assertEqual(result['status'],'pass'); self.assertFalse(result['required'])

    def test_two_independent_trials_are_not_same_workspace_proof(self):
        result=adapter.idempotence('dbt-customer-churn-cohorts', {'commands':[]})
        self.assertEqual(result['status'],'gap')

    def test_projection_preserves_large_ids_and_duplicate_multiplicity(self):
        with TemporaryDirectory() as directory:
            p=Path(directory)/'rows.jsonl'
            cap=type('Cap',(),{'runtime':lambda self,row:p})()
            item={'columns':['id','value'],'sql_schema':[['id','BIGINT'],['value','VARCHAR']],'rows':2}
            p.write_text('[9007199254740992,"A"]\n[9007199254740992,"A"]\n')
            a=adapter.typed_projection(cap,item,['id','value'])
            p.write_text('[9007199254740992,"A"]\n[9007199254740993,"A"]\n')
            b=adapter.typed_projection(cap,item,['id','value'])
            self.assertNotEqual(a,b)
            p.write_text('[9007199254740993,"A"]\n[9007199254740992,"A"]\n')
            self.assertEqual(b,adapter.typed_projection(cap,item,['id','value']))

    def test_io_error_is_unresolved_not_missing_relation_zero(self):
        key='deb__dbt-customer-churn-early-warning'
        relation=next(iter(adapter.FAILURE_REQUIREMENTS[key.removeprefix('deb__')]))
        snap={'task_key':key,'stage':'agent-submission','outputs':[{'specification':{
            'relation':relation,'database':'/app/database/retail.duckdb'},'error':{'type':'IOException','message':'permission denied'}}]}
        with patch.object(adapter,'checked_json',return_value=snap):
            observed,unresolved=adapter.actual_outputs({},key,ROOT)
        self.assertEqual(observed,{})
        self.assertEqual(unresolved[relation.split('.',1)[1]]['type'],'IOException')

    def test_absence_rules_only_cover_final_contract_deliverables(self):
        rules=adapter.FAILURE_REQUIREMENTS['dbt-customer-churn-cohorts']
        self.assertEqual(set(rules), {'main.customer_cohorts','main.cohort_retention','main.customer_clv'})
        self.assertNotIn('main.int_analytics__fact_sales',rules)

    def test_valid_but_unsupported_profile_expression_is_not_failure(self):
        with TemporaryDirectory() as directory:
            project=Path(directory)/'dbt_project.yml';profile=Path(directory)/'profiles.yml'
            project.write_text('profile: retail_dw_master\n')
            profile.write_text('retail_dw_master:\n  target: dev\n  outputs:\n    dev:\n      type: duckdb\n      path: "{{ env_var(\'DB_ROOT\') ~ \'/retail.duckdb\' }}"\n')
            d={'final_files':{'dbt_project.yml':project,'profiles.yml':profile},'execution':{'environment':{'DB_ROOT':'/app/database'}}}
            self.assertIsNotNone(adapter.profile_resolution_gap(d,{}))
            profile.write_text('retail_dw_master:\n  target: dev\n  outputs:\n    dev:\n      type: duckdb\n')
            self.assertIsNone(adapter.profile_resolution_gap(d,{}))


if __name__=='__main__':
    unittest.main()
