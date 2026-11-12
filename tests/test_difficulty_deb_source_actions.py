"""Source delivery accepts observed exports without inventing a final dbt run."""
import unittest
try:
 import yaml
 from difficulty_deb_actions import source_action_gate,explicit_profile_export,reference_build_events
except ImportError:yaml=None

@unittest.skipIf(yaml is None,'Requires the locked Harbor runtime PyYAML dependency')
class SourceActionTests(unittest.TestCase):
 def events(self,command,exitcode=0):
  return {'echo_before_first_write':True,'profile_build_calls':[],'source_logs':[],
   'pairs':[{'call_id':'actual-1','name':'run_shell_command','args':{'command':command,'directory':'/app/dbt_project'},'response':{'output':{'exitCode':exitcode,'stdout':''}}}]}
 def gate(self,events):return source_action_gate('deb__dbt-coupon-effectiveness',events,'/app/dbt_project')['reward']
 def test_export_without_build_satisfies_profile_action(self):self.assertEqual(self.gate(self.events('export DBT_PROFILES_DIR=/app/dbt_project')),1)
 def test_echo_after_write_still_fails(self):
  e=self.events('export DBT_PROFILES_DIR=/app/dbt_project');e['echo_before_first_write']=False;self.assertEqual(self.gate(e),0)
 def test_missing_echo_remains_unknown(self):
  e=self.events('export DBT_PROFILES_DIR=/app/dbt_project');e['echo_before_first_write']=None;self.assertIsNone(self.gate(e))
 def test_failed_export_not_accepted(self):self.assertIsNone(self.gate(self.events('export DBT_PROFILES_DIR=/app/dbt_project',1)))
 def test_printed_export_not_execution(self):self.assertIsNone(self.gate(self.events('echo "export DBT_PROFILES_DIR=/app/dbt_project"')))
 def test_wrong_profile_directory_unknown(self):self.assertIsNone(self.gate(self.events('export DBT_PROFILES_DIR=/wrong')))
 def test_relative_export_with_actual_cwd(self):self.assertEqual(self.gate(self.events('export DBT_PROFILES_DIR=.')),1)
 def test_opaque_script_not_invented_export(self):self.assertIsNone(self.gate(self.events('bash setup.sh')))
 def test_successful_reference_named_build(self):
  e=self.events('cd /app/dbt_models_duckdb && dbt run --select a b c');self.assertEqual(reference_build_events(e,{'/app/dbt_models_duckdb'},{'a','b','c'}),['actual-1'])
 def test_reference_wrong_project_is_not_proof(self):
  e=self.events('dbt run --select a b c');self.assertEqual(reference_build_events(e,{'/app/dbt_models_duckdb'},{'a','b','c'}),[])
 def test_reference_printed_command_is_not_proof(self):
  e=self.events('echo dbt run --select a b c');self.assertEqual(reference_build_events(e,{'/app/dbt_project'},{'a','b','c'}),[])
 def test_missing_one_required_reference_model(self):
  e=self.events('cd /app/dbt_models_duckdb && dbt run --select a b');self.assertEqual(reference_build_events(e,{'/app/dbt_models_duckdb'},{'a','b','c'}),[])

if __name__=='__main__':unittest.main()
