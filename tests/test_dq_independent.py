from copy import deepcopy
from decimal import Decimal
import importlib.util
from pathlib import Path
import unittest

path=Path(__file__).resolve().parents[1]/'validation/deb/dbt-dq-macro-enforcement/independent.py'
spec=importlib.util.spec_from_file_location('dq_independent',path)
dq=importlib.util.module_from_spec(spec);spec.loader.exec_module(dq)


class DQIndependentTests(unittest.TestCase):
    def test_pinned_double_rounding_and_threshold_are_explicit(self):
        self.assertEqual(dq.sql_net_price(Decimal('267.15'),Decimal('2')),133.57)
        self.assertEqual(dq.sql_net_price(Decimal('-267.15'),Decimal('2')),-133.57)
        tables=self.tables();tables[dq.INPUTS[0]][0].update(quantity_ordered='1',unit_price='113.06',
            discount_amount='8.38',tax_amount='8.37',line_total='113.05')
        row=next(iter(dq.derive(tables).values()))
        self.assertEqual(row['net_unit_price'],Decimal('113.05'))
        self.assertTrue(row['unit_price_net_corrected'])

    def tables(self):
        return {
            dq.INPUTS[0]:[{'order_line_id':9007199254740993,'order_id':'o','variant_id':'v',
                'sku':' OL ','product_name':'Fallback','quantity_ordered':'2','unit_price':'10',
                'discount_amount':'1','tax_amount':'.5','line_total':'19.5'}],
            dq.INPUTS[1]:[{'order_id':'o','order_number':'ORDER'}],
            dq.INPUTS[2]:[{'variant_id':'v','product_id':'p','sku':' P1 ','variant_name':'Blue','cost_price':'3'}],
            dq.INPUTS[3]:[{'product_id':'p','product_name':' Product ','primary_category_id':'c'}],
            dq.INPUTS[4]:[{'category_id':'c','category_path':'Root > Leaf','category_name':'Category'}]}

    def snapshot(self,expected):
        return {'columns':list(next(iter(expected.values()))),'rows':deepcopy(list(expected.values()))}

    def test_hand_computed_prices_normalization_and_large_key(self):
        row=next(iter(dq.derive(self.tables()).values()))
        self.assertEqual(row['order_line_id'],9007199254740993)
        self.assertEqual((row['line_total'],row['line_profit'],row['net_unit_price']),
                         (Decimal('19.5'),Decimal('13.5'),Decimal('9.75')))
        self.assertEqual((row['sku'],row['product_name'],row['product_category_leaf']),('P1','Product','Leaf'))
        self.assertFalse(row['line_total_corrected']);self.assertTrue(row['unit_price_net_corrected'])

    def test_fallback_zero_quantity_and_exact_cent_boundary(self):
        tables=self.tables();line=tables[dq.INPUTS[0]][0];variant=tables[dq.INPUTS[2]][0]
        variant.update(sku=' ',cost_price=None);line.update(quantity_ordered='0',discount_amount='0',tax_amount='0',line_total='0')
        row=next(iter(dq.derive(tables).values()))
        self.assertEqual(row['sku'],'OL');self.assertIsNone(row['net_unit_price'])
        self.assertFalse(row['unit_price_net_corrected']);self.assertTrue(row['dq_format_corrected'])
        line.update(quantity_ordered='1',unit_price='.20',tax_amount='.01',line_total='.21')
        self.assertFalse(next(iter(dq.derive(tables).values()))['unit_price_net_corrected'])
        line['tax_amount']='.02'
        self.assertTrue(next(iter(dq.derive(tables).values()))['unit_price_net_corrected'])

    def test_same_count_duplicate_and_missing_source_key_rejected(self):
        tables=self.tables();second=deepcopy(tables[dq.INPUTS[0]][0]);second['order_line_id']=9007199254740992
        tables[dq.INPUTS[0]].append(second);expected=dq.derive(tables);snapshot=self.snapshot(expected)
        snapshot['rows'][1]=deepcopy(snapshot['rows'][0]);result=dq.compare(expected,snapshot)
        self.assertEqual(result['actual_rows'],result['expected_rows']);self.assertFalse(result['equal'])
        self.assertEqual(result['missing_keys'],[9007199254740992]);self.assertEqual(result['duplicate_keys'][0]['count'],2)

    def test_missing_new_column_and_null_existing_value_rejected(self):
        expected=dq.derive(self.tables());snapshot=self.snapshot(expected)
        snapshot['columns'].remove('net_unit_price')
        self.assertFalse(dq.compare(expected,snapshot)['equal'])
        snapshot=self.snapshot(expected);snapshot['rows'][0]['line_total']=None
        self.assertEqual(dq.compare(expected,snapshot)['mismatches'][0]['column'],'line_total')

    def test_reordering_legal_wrong_value_and_duplicate_inputs_rejected(self):
        tables=self.tables();second=deepcopy(tables[dq.INPUTS[0]][0]);second['order_line_id']='second'
        tables[dq.INPUTS[0]].append(second);expected=dq.derive(tables);snapshot=self.snapshot(expected)
        snapshot['rows'].reverse();self.assertTrue(dq.compare(expected,snapshot)['equal'])
        snapshot['rows'][0]['line_profit']+=Decimal(1);self.assertFalse(dq.compare(expected,snapshot)['equal'])
        tables[dq.INPUTS[0]].append(second)
        with self.assertRaises(ValueError):dq.derive(tables)
