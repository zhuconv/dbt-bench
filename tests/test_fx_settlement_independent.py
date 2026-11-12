"""Hand-computable FX rules and the native verifier's risky comparison shapes."""
import importlib.util
from pathlib import Path
from decimal import Decimal
import copy
import unittest

PATH = Path(__file__).resolve().parents[1] / 'validation/deb/dbt-exchange-rate-settlement-date/independent.py'
spec = importlib.util.spec_from_file_location('fx_independent', PATH)
fx = importlib.util.module_from_spec(spec)
spec.loader.exec_module(fx)


def fixture():
    def sale(identifier, currency, gross, discount, total):
        return dict(order_id=identifier, customer_id=identifier, order_line_id=identifier,
                    order_date='2020-01-01', currency_code=currency, source_system='fixture',
                    extended_price=gross, discount_amount=discount, line_total=total, tax_amount='1')
    return {'fct_sales': [sale('a', 'EUR', '20', '2', '19'), sale('b', 'EUR', '10', '2', '9'),
                          sale('c', None, '5', '0', '5')],
            'order_payments': [{'order_id': 'a', 'processed_at': '2020-01-02 01:00:00'},
                               {'order_id': 'a', 'processed_at': '2020-01-03 00:00:00'},
                               {'order_id': 'a', 'processed_at': None}],
            'dim_exchange_rates': [{'rate_date': '2020-01-01', 'from_currency': 'EUR', 'to_currency': 'USD', 'rate': '2'},
                                   {'rate_date': '2020-01-03', 'from_currency': 'EUR', 'to_currency': 'USD', 'rate': '1.25'}]}


class FXIndependentTests(unittest.TestCase):
    def test_latest_settlement_and_missing_payment_and_currency(self):
        rows, grain = fx.derive(fixture())
        lookup = {(r['settlement_date'], r['currency_code']): r for r in rows}
        self.assertEqual(lookup['2020-01-03', 'EUR']['net_revenue_usd'], Decimal('22.50'))
        self.assertEqual(lookup['2020-01-03', 'EUR']['total_revenue_usd'], Decimal('23.75'))
        self.assertEqual(lookup['2020-01-01', 'EUR']['total_revenue_usd'], Decimal('18.00'))
        self.assertEqual(lookup['2020-01-01', None]['net_revenue_usd'], Decimal('5.00'))
        self.assertEqual((grain['original_grain_groups'], grain['reference_grain_groups'], grain['split_original_groups']), (2, 3, 1))

    def test_duplicate_and_missing_grain_are_not_sets(self):
        rows, _ = fx.derive(fixture())
        snap = {'columns': list(rows[0]), 'rows': rows + [rows[0]]}
        result = fx.compare(rows, snap, list(rows[0]))
        self.assertFalse(result['equal'])
        self.assertEqual(result['duplicates'][0]['count'], 2)
        self.assertEqual(result['value_mismatches'], [])
        snap['rows'] = rows[1:]
        self.assertEqual(len(fx.compare(rows, snap, list(rows[0]))['missing_keys']), 1)

    def test_null_currency_still_requires_numeric_usd_values(self):
        rows, _ = fx.derive(fixture())
        changed = copy.deepcopy(rows)
        next(r for r in changed if r['currency_code'] is None)['net_revenue_usd'] = None
        result = fx.compare(rows, {'columns': list(rows[0]), 'rows': changed}, list(rows[0]))
        self.assertFalse(result['equal'])
        self.assertEqual(len(result['value_mismatches']), 1)
        self.assertEqual(result['value_mismatches'][0]['expected'], '5.00')

    def test_preserved_column_and_exact_counts_and_row_order(self):
        rows, _ = fx.derive(fixture())
        snap = {'columns': list(rows[0]), 'rows': list(reversed(rows))}
        self.assertTrue(fx.compare(rows, snap, list(rows[0]))['equal'])
        snap['columns'] = [c for c in snap['columns'] if c != 'total_orders']
        self.assertEqual(fx.compare(rows, snap, list(rows[0]))['missing_columns'], ['total_orders'])
        changed = copy.deepcopy(rows)
        changed[0]['total_orders'] += Decimal('0.0000001')
        self.assertFalse(fx.compare(rows, {'columns': list(rows[0]), 'rows': changed}, list(rows[0]))['equal'])

    def test_ambiguous_rate_key_does_not_silently_choose_a_value(self):
        data = fixture()
        data['dim_exchange_rates'].append(dict(data['dim_exchange_rates'][0], rate='7'))
        with self.assertRaisesRegex(ValueError, 'duplicate FX dimension'):
            fx.derive(data)
