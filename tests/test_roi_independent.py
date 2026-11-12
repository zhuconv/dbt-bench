from copy import deepcopy
from datetime import date, timedelta
from decimal import Decimal
import importlib.util
from pathlib import Path
import unittest


path = Path(__file__).resolve().parents[1] / 'validation/deb/dbt-retail-task-04-market-roi/independent.py'
spec = importlib.util.spec_from_file_location('roi_independent', path)
roi = importlib.util.module_from_spec(spec)
spec.loader.exec_module(roi)


def fixture():
    dates = [date(2021, 1, 1) + timedelta(days=n) for n in (0, 89, 90)]
    keys = [int(d.strftime('%Y%m%d')) for d in dates]
    return {
        'dates': [{'date_key': key, 'full_date': str(d)} for key, d in zip(keys, dates)],
        'customers': [{'customer_key': 10, 'segment_name': None, 'tier_name': 'GOLD'}],
        'channels': [{'channel_key': n, 'channel_name': f'Channel {n}', 'channel_type': 'DIGITAL'} for n in (1, 2)],
        'marketing': [
            {'date_key': keys[0], 'channel_key': 1, 'impressions': 20, 'clicks': 4, 'conversions': 2, 'spend_amount': '60', 'revenue_attributed': '120'},
            {'date_key': keys[0], 'channel_key': 2, 'impressions': 0, 'clicks': 0, 'conversions': 0, 'spend_amount': '0', 'revenue_attributed': '0'},
            {'date_key': keys[0], 'channel_key': 3, 'impressions': 10, 'clicks': 1, 'conversions': 1, 'spend_amount': '10', 'revenue_attributed': '0'}],
        'sales': [
            {'customer_key': 10, 'channel_key': 1, 'date_key': keys[0], 'order_id': 'A', 'total_amount': '20', 'profit_amount': '2'},
            {'customer_key': 10, 'channel_key': 2, 'date_key': keys[0], 'order_id': 'B', 'total_amount': '30', 'profit_amount': '3'},
            {'customer_key': 10, 'channel_key': 1, 'date_key': keys[0], 'order_id': 'A', 'total_amount': '5', 'profit_amount': '0.5'},
            {'customer_key': 10, 'channel_key': 1, 'date_key': keys[1], 'order_id': 'C', 'total_amount': '45', 'profit_amount': '4.5'},
            {'customer_key': 10, 'channel_key': 1, 'date_key': keys[2], 'order_id': 'D', 'total_amount': '1000', 'profit_amount': '100'}]}


class IndependentRoiTests(unittest.TestCase):
    def test_hand_calculated_boundary_tie_and_distinct_order_rules(self):
        rows, stats = roi.recompute(fixture())
        row = next(r for r in rows if r['channel_key'] == 1)
        # First-day channel 1 wins the tie. LTV includes day 89 and excludes
        # day 90: 20+30+5+45=100, profit 2+3+.5+4.5=10.
        self.assertEqual(row['ltv_90d_revenue_per_customer'], Decimal(100))
        self.assertEqual(row['ltv_90d_profit_per_customer'], Decimal(10))
        self.assertEqual(row['sales_revenue'], Decimal(25))
        self.assertEqual(row['order_count'], 1)
        self.assertEqual(row['new_customers'], 1)
        self.assertEqual(row['segment_name'], 'Unknown')
        self.assertEqual(row['ctr'], Decimal('0.2'))
        self.assertEqual(stats['first_date_channel_ties'], 1)
        self.assertEqual(stats['ltv_day_89_joined_rows'], 1)
        self.assertEqual(stats['ltv_day_90_joined_rows'], 1)

    def test_no_new_customer_is_nullable_base_row_not_unknown_segment(self):
        rows, _ = roi.recompute(fixture())
        row = next(r for r in rows if r['channel_key'] == 2)
        for field in ('segment_name', 'tier_name', 'new_customers', 'total_new_customers', 'new_customer_share', 'blended_cac', 'ltv_90d_revenue_per_customer', 'payback_90d_profit_ratio'):
            self.assertIsNone(row[field], field)
        for field in ('ctr', 'conversion_rate', 'roas', 'order_conversion_rate'):
            self.assertIsNone(row[field], field)

    def test_missing_channel_dimension_preserves_marketing_base(self):
        rows, _ = roi.recompute(fixture())
        row = next(r for r in rows if r['channel_key'] == 3)
        self.assertIsNone(row['channel_name'])
        self.assertIsNone(row['channel_type'])
        self.assertIsNone(row['sales_revenue'])
        self.assertEqual(row['spend_amount'], Decimal(10))

    def test_nullable_keys_allow_row_permutation_and_extra_columns(self):
        rows, _ = roi.recompute(fixture())
        equivalent = [{**r, 'additional_metric': 9} for r in reversed(rows)]
        self.assertTrue(roi.compare_output(rows, equivalent)['equal'])

    def test_duplicate_missing_and_wrong_association_are_rejected(self):
        rows, _ = roi.recompute(fixture())
        self.assertFalse(roi.compare_output(rows, [*rows, rows[0]])['equal'])
        self.assertFalse(roi.compare_output(rows, rows[:-1])['equal'])
        wrong = deepcopy(rows)
        wrong[0]['spend_amount'], wrong[2]['spend_amount'] = wrong[2]['spend_amount'], wrong[0]['spend_amount']
        self.assertFalse(roi.compare_output(rows, wrong)['equal'])

    def test_fractional_integer_measure_is_not_silently_truncated(self):
        rows, _ = roi.recompute(fixture())
        wrong = deepcopy(rows)
        wrong[0]['impressions'] = Decimal('20.25')
        self.assertFalse(roi.compare_output(rows, wrong)['equal'])

    def test_dimension_join_multiplicity_is_preserved(self):
        data = fixture()
        data['dates'].append(dict(data['dates'][0]))
        rows, _ = roi.recompute(data)
        row = next(r for r in rows if r['channel_key'] == 1)
        self.assertEqual(row['impressions'], 40)
        self.assertEqual(row['sales_revenue'], Decimal(50))
        self.assertEqual(row['new_customers'], 1)
        self.assertEqual(row['ltv_90d_revenue_per_customer'], Decimal(155))
