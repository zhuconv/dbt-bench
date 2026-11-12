"""Source delivery may be correct before the native verifier builds it."""
from pathlib import Path
import importlib.util
import sys
import unittest

ROOT=Path(__file__).resolve().parents[1]
sys.path[:0]=[str(ROOT/'src'),str(ROOT/'scripts')]
HAS_YAML=importlib.util.find_spec('yaml') is not None
if HAS_YAML:
    import difficulty_deb_phase4_pipeline_v2 as adapter


@unittest.skipUnless(HAS_YAML,'Locked runtime PyYAML required')
class DeliveryTests(unittest.TestCase):
    def test_original_missing_output_does_not_reject_source_contract(self):
        for task in adapter.SOURCE_TASKS:
            self.assertNotIn(task,adapter.FAILURE_REQUIREMENTS)
            self.assertEqual(adapter.DELIVERY_CONTRACTS[task],'submitted_source_reconstruction')
        self.assertIn('dbt-customer-churn-early-warning',adapter.FAILURE_REQUIREMENTS)

    def test_explicit_export_does_not_impose_model_build(self):
        self.assertTrue(adapter.explicit_profile_export('export DBT_PROFILES_DIR=/app/dbt_models_duckdb','/app','/app/dbt_models_duckdb'))
        self.assertTrue(adapter.explicit_profile_export('cd /app/dbt_models_duckdb && export DBT_PROFILES_DIR=.','/app','/app/dbt_models_duckdb'))
        self.assertFalse(adapter.explicit_profile_export('echo "export DBT_PROFILES_DIR=/app/dbt_models_duckdb"','/app','/app/dbt_models_duckdb'))

    def test_source_action_success_needs_actual_pair_and_prior_echo(self):
        events={'echo_before_first_write':True,'profile_build_calls':[], 'source_logs':[],
            'pairs':[{'call_id':'export','name':'run_shell_command','args':{'command':'export DBT_PROFILES_DIR=/app/dbt_models_duckdb'},
                'response':{'output':{'exitCode':0,'stdout':''}}}]}
        key='deb__dbt-customer-churn-cohorts'
        self.assertEqual(adapter.source_action_gate(key,events,'/app/dbt_models_duckdb')['reward'],1)
        events['pairs'][0]['response']['output']['exitCode']=1
        self.assertIsNone(adapter.source_action_gate(key,events,'/app/dbt_models_duckdb')['reward'])
        events['echo_before_first_write']=False
        self.assertEqual(adapter.source_action_gate(key,events,'/app/dbt_models_duckdb')['reward'],0)


if __name__=='__main__':unittest.main()
