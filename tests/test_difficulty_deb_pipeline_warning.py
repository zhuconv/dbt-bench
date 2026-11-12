"""Warning source delivery retains its upstream action without adding a final run."""
from pathlib import Path
import importlib.util
import sys
import unittest

ROOT=Path(__file__).resolve().parents[1]
sys.path[:0]=[str(ROOT/'src'),str(ROOT/'scripts')]
HAS_YAML=importlib.util.find_spec('yaml') is not None
if HAS_YAML:import difficulty_deb_phase4_pipeline_v3 as adapter


@unittest.skipUnless(HAS_YAML,'Locked runtime PyYAML required')
class WarningDeliveryTests(unittest.TestCase):
    def events(self,text='OK created sql view model main.int_sales__orders_enriched [OK]'):
        return {'pairs':[{'name':'run_shell_command','call_id':'reference-only',
            'args':{'command':'dbt run --project-dir /app/dbt_models_duckdb --select +int_sales__orders_enriched'},
            'response':{'output':{'exitCode':0,'stdout':text}}}], 'profile_build_calls':[]}
    def test_reference_success_does_not_require_a_final_run(self):
        self.assertEqual(adapter.warning_reference_gate(self.events())['reward'],1)
    def test_no_selected_models_is_not_upstream_success(self):
        self.assertIsNone(adapter.warning_reference_gate(self.events('Nothing to do'))['reward'])
    def test_all_three_are_source_delivery(self):
        self.assertEqual(set(adapter.DELIVERY_CONTRACTS.values()),{'submitted_source_reconstruction'})
        self.assertEqual(adapter.FAILURE_REQUIREMENTS,{})

if __name__=='__main__':unittest.main()
