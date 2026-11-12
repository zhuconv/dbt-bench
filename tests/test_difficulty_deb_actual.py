"""Task-aware actual-source uncertainty and exact repeated-output controls."""
import json,tempfile,unittest
from pathlib import Path
from types import SimpleNamespace
from difficulty_deb_failure_v3 import evaluate_source
from difficulty_deb_repeat import compare
try:
 import yaml
 from difficulty_deb_phase4_customer_v3 import unsupported_profile
except ImportError:yaml=None

class ExplicitSourceTests(unittest.TestCase):
 def setUp(self):self.capture={'complete':True,'inventory_complete':True,'errors':[],'project_root':'/app/dbt_project'}
 def test_rfm_explicit_profile_absence_zero(self):self.assertEqual(evaluate_source('dbt-rfm-customer-segmentation',self.capture,{'dbt_project.yml'})['reward'],0)
 def test_present_profile_no_success_claim(self):self.assertIsNone(evaluate_source('dbt-rfm-customer-segmentation',self.capture,{'profiles.yml'})['reward'])
 def test_loyalty_ambiguous_path_not_failure(self):self.assertIsNone(evaluate_source('dbt-loyalty-points-analysis',self.capture,set())['reward'])
 def test_incomplete_source_not_failure(self):
  self.capture['inventory_complete']=False;self.assertIsNone(evaluate_source('dbt-rfm-customer-segmentation',self.capture,set())['reward'])

class ActualRepeatTests(unittest.TestCase):
 def evaluate(self,a,b):
  with tempfile.TemporaryDirectory()as temp:
   root=Path(temp);commands=[]
   for i,rows in enumerate([a,b]):
    p=root/str(i);p.write_text(''.join(json.dumps(r)+'\n'for r in rows))
    commands.append({'run':{'command':['dbt','run']},'hooks':[{'physical_outputs':{'m':{'path':str(p),'columns':['id','value'],'sql_schema':[['id','BIGINT'],['value','VARCHAR']]}}}]})
   capture=SimpleNamespace(root=root,runtime=lambda r:Path(r['path']))
   return compare({'commands':commands,'capture':capture},{'m':['id','value']})
 def test_row_order_irrelevant(self):self.assertEqual(self.evaluate([[1,'A'],[2,'B']],[[2,'B'],[1,'A']])['reward'],1)
 def test_key_value_permutation_rejected(self):self.assertEqual(self.evaluate([[1,'A'],[2,'B']],[[2,'A'],[1,'B']])['reward'],0)
 def test_duplicate_multiplicity_preserved(self):self.assertEqual(self.evaluate([[1,'A'],[1,'A']],[[1,'A']])['reward'],0)
 def test_bigint_exact(self):self.assertEqual(self.evaluate([[9007199254740992,'A']],[[9007199254740993,'A']])['reward'],0)

@unittest.skipIf(yaml is None,'Requires PyYAML from the locked Harbor runtime')
class ProfileLimits(unittest.TestCase):
 def check(self,profile):
  with tempfile.TemporaryDirectory()as temp:
   root=Path(temp);(root/'dbt_project.yml').write_text('profile: project\n');(root/'profiles.yml').write_text(profile)
   return unsupported_profile({'final_files':{p.name:p for p in root.iterdir()},'execution':{'environment':{'DB_PATH':'/app/database/retail.duckdb'}}})
 def test_plain_env_var_supported(self):self.assertFalse(self.check('project:\n  target: dev\n  outputs:\n    dev:\n      path: "{{ env_var(\'DB_PATH\') }}"\n      schema: main\n'))
 def test_legal_unhandled_expression_is_uncertain(self):self.assertTrue(self.check('project:\n  target: dev\n  outputs:\n    dev:\n      path: "{{ env_var(\'DB_PATH\') | trim }}"\n      schema: main\n'))
 def test_missing_profile_not_parser_uncertainty(self):self.assertFalse(self.check('wrong_name: {}\n'))

if __name__=='__main__':unittest.main()
