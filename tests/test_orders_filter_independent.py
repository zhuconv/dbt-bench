import importlib.util
from pathlib import Path
from decimal import Decimal
import unittest

SPEC=importlib.util.spec_from_file_location('orders_filter_independent',Path(__file__).resolve().parents[1]/'validation/deb/dbt-test-orders-filter/independent.py')
mod=importlib.util.module_from_spec(SPEC);SPEC.loader.exec_module(mod)


class OrdersFilterIndependentTests(unittest.TestCase):
    def row(self, **overrides):
        return {'order_id':'9007199254740993','ordered_at':'2024-02-29 23:59:59',
            'grand_total':'12.3450','tax_total':'0.0050','notes':'retain this',
            'test_order_flag':False,'sample_order_flag':False,'internal_order_flag':False,**overrides}

    def test_each_purpose_flag_and_nulls(self):
        for flag in mod.FLAGS:
            self.assertFalse(mod.production(self.row(**{flag:True})))
            self.assertTrue(mod.production(self.row(**{flag:None})))
        with self.assertRaises(ValueError):mod.production(self.row(test_order_flag='FALSE'))

    def test_all_columns_dates_and_exact_half_up_money(self):
        output=mod.derive([self.row()])[0]
        self.assertEqual(output['order_id'],'9007199254740993')
        self.assertEqual(output['order_date'],'2024-02-29')
        self.assertEqual(output['grand_total'],Decimal('12.35'))
        self.assertEqual(output['tax_total'],Decimal('0.01'))
        self.assertEqual(output['notes'],'retain this')

    def test_row_association_duplicates_and_missing_columns(self):
        expected=mod.derive([self.row(order_id='A'),self.row(order_id='B',grand_total='22.105')])
        columns=list(expected[0]);types={c:'VARCHAR' for c in columns}
        types.update(grand_total='DECIMAL(12,4)',tax_total='DECIMAL(12,4)',ordered_at='TIMESTAMP',order_date='DATE')
        types.update({c:'BOOLEAN' for c in mod.FLAGS})
        obs={'columns':columns,'rows':[dict(r) for r in reversed(expected)]}
        self.assertTrue(mod.compare(expected,obs,columns,types)['equal'])
        obs['rows'][0]['grand_total'],obs['rows'][1]['grand_total']=obs['rows'][1]['grand_total'],obs['rows'][0]['grand_total']
        self.assertEqual(len(mod.compare(expected,obs,columns,types)['value_mismatches']),2)
        obs={'columns':columns,'rows':expected+[expected[0]]}
        self.assertFalse(mod.compare(expected,obs,columns,types)['equal'])
        obs={'columns':[c for c in columns if c!='notes'],'rows':expected}
        self.assertEqual(mod.compare(expected,obs,columns,types)['missing_columns'],['notes'])

    def test_missing_metadata_fails_closed_and_casefolded_types_preserve_numeric_equivalence(self):
        expected=[{'order_id':'A','grand_total':Decimal('12.35')}]
        observed={'columns':['order_id','grand_total'],'rows':[{'order_id':'A','grand_total':Decimal('12.350')}]}
        with self.assertRaisesRegex(ValueError,'Incomplete input schema'):
            mod.compare(expected,observed,observed['columns'],{})
        self.assertTrue(mod.compare(expected,observed,observed['columns'],{'ORDER_ID':'VARCHAR','GRAND_TOTAL':'DECIMAL(18,4)'})['equal'])
        observed['rows'][0]['grand_total']=Decimal('12.351')
        self.assertFalse(mod.compare(expected,observed,observed['columns'],{'ORDER_ID':'VARCHAR','GRAND_TOTAL':'DECIMAL(18,4)'})['equal'])
