"""Regression evidence for exact current custom-task semantics."""
import importlib.util
import math
from decimal import Decimal
from pathlib import Path
import sys
import unittest
ROOT=Path(__file__).resolve().parents[1]

def load(path,name):
 spec=importlib.util.spec_from_file_location(name,path);mod=importlib.util.module_from_spec(spec);spec.loader.exec_module(mod);return mod

class MidpointPolicyTests(unittest.TestCase):
 @classmethod
 def setUpClass(cls):
  cls.e=load(ROOT/'validation/deb/dbt-fulfillment-sla/phase4_v1/expected_v5.py','phase4_test_fulfillment_expected')
 def test_speed_only_nearest_exact_midpoint(self):
  e=self.e;e.SPEED_OVERRIDES={};self.assertEqual(e.rounded_speed(Decimal('19.15')),Decimal('19.2'))
  e.SPEED_OVERRIDES={('19.15',1):True};self.assertEqual(e.rounded_speed(Decimal('19.15')),Decimal('19.1'))
  self.assertEqual(e.rounded_speed(Decimal('19.151')),Decimal('19.2'));e.SPEED_OVERRIDES={}
 def test_midpoint_downstream_score_not_arbitrary_tolerance(self):
  e=self.e;e.SPEED_OVERRIDES={('30.55',1):True};v=e.rounded_speed(Decimal('30.55'));self.assertEqual(v,Decimal('30.5'));self.assertNotEqual(v,Decimal('30.4'));e.SPEED_OVERRIDES={}
 def test_nonmidpoint_cannot_be_changed(self):
  e=self.e;e.SPEED_OVERRIDES={('19.151',1):True};self.assertEqual(e.rounded_speed(Decimal('19.151')),Decimal('19.2'));e.SPEED_OVERRIDES={}

class RatioPrecisionTests(unittest.TestCase):
 @classmethod
 def setUpClass(cls):
  sys.path.insert(0,str(ROOT/'scripts'));sys.path.insert(0,str(ROOT/'validation/deb/dbt-abc-classification/phase4_v1'))
  original=sys.modules.pop('expected_v1',None)
  try:cls.a=load(ROOT/'validation/deb/dbt-abc-classification/phase4_v1/audit_v2.py','phase4_test_abc_audit')
  finally:
   sys.modules.pop('expected_v1',None)
   if original is not None:sys.modules['expected_v1']=original
   sys.path.pop(0);sys.path.pop(0)
 def test_ratio_binary_representation(self):self.assertTrue(self.a.eq(81.12666666666667,Decimal(24338)/300,True))
 def test_ratio_meaningful_error_rejected(self):self.assertFalse(self.a.eq(81.13666666666667,Decimal(24338)/300,True))
 def test_large_integer_identity_exact(self):self.assertFalse(self.a.eq(9007199254740993,9007199254740992))
 def test_decimal_money_no_ulp_policy(self):self.assertFalse(self.a.eq(Decimal('42.000000000001'),Decimal('42')))

if __name__=='__main__':unittest.main()
