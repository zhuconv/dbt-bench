import importlib.util
from pathlib import Path
import unittest
SPEC=importlib.util.spec_from_file_location('customer_cases',Path(__file__).resolve().parents[1]/'validation/ade/ade-bench-ana-eng003/case_engine.py')
mod=importlib.util.module_from_spec(SPEC);SPEC.loader.exec_module(mod)


class CustomerDimensionTests(unittest.TestCase):
    def test_exact_duplicate_rows_collapse_but_conflicts_do_not(self):
        r={'id':9007199254740993,'name':'A'}
        self.assertEqual(mod.derive([r,r]),[{'customer_id':9007199254740993,'name':'A'}])
        with self.assertRaises(ValueError):mod.derive([r,{**r,'name':'B'}])
        with self.assertRaises(ValueError):mod.derive([{'id':True,'name':'A'}])

    def test_multiset_rejects_duplicate_that_set_comparison_misses(self):
        wanted=[{'customer_id':1,'name':'A'},{'customer_id':2,'name':'B'}]
        actual={'columns':['customer_id','name'],'rows':wanted+[wanted[0]]}
        self.assertEqual(mod.compare(wanted,actual,actual['columns'])['extra_rows'],1)

    def test_key_association_and_order(self):
        wanted=[{'customer_id':1,'name':'A'},{'customer_id':2,'name':'B'}]
        columns=['customer_id','name']
        self.assertTrue(mod.compare(wanted,{'columns':columns,'rows':list(reversed(wanted))},columns)['equal'])
        bad=[{'customer_id':1,'name':'B'},{'customer_id':2,'name':'A'}]
        self.assertFalse(mod.compare(wanted,{'columns':columns,'rows':bad},columns)['equal'])
