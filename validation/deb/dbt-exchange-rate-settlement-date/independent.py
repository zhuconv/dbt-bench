"""Independent public-input derivation; never imports the native verifier.

The four-column grouping is the native reference interpretation. The prompt's
instruction to retain the original three-column grain is separately unresolved.
This module proves scoped FX values and multiplicity, not full task correctness.
"""
from collections import Counter, defaultdict
from decimal import Decimal, ROUND_HALF_UP
import json

INPUTS = ('fct_sales', 'order_payments', 'dim_exchange_rates')
KEYS = ('order_date', 'settlement_date', 'currency_code', 'source_system')
EXACT_COUNTS = {'total_orders', 'unique_customers', 'total_line_items'}
CORE = ('total_orders', 'unique_customers', 'total_line_items', 'gross_revenue',
        'total_discounts', 'net_revenue', 'total_tax', 'total_revenue',
        'fx_rate', 'net_revenue_usd', 'total_revenue_usd')


def encoded(value):
    return json.dumps(value, sort_keys=True, default=str, separators=(',', ':'))


def canonical_inputs(tables):
    return {name: sorted(tables[name]['rows'], key=encoded) for name in INPUTS}


def number(value):
    return None if value is None else Decimal(str(value))


def sql_sum(values):
    values = [number(v) for v in values if v is not None]
    return sum(values, Decimal(0)) if values else None


def rounded(value, scale):
    return None if value is None else value.quantize(Decimal(scale), rounding=ROUND_HALF_UP)


def derive(inputs):
    payments = {}
    for row in inputs['order_payments']:
        if row['processed_at'] is not None:
            key = row['order_id']
            payments[key] = max(payments.get(key, ''), str(row['processed_at']))
    rates = {}
    for row in inputs['dim_exchange_rates']:
        key = (str(row['rate_date']), row['from_currency'], row['to_currency'])
        if key in rates:
            raise ValueError('Ambiguous duplicate FX dimension key: ' + str(key))
        rates[key] = number(row['rate'])
    groups = defaultdict(list)
    original_grain = defaultdict(set)
    for row in inputs['fct_sales']:
        if row['order_date'] is None:
            continue
        order_date = str(row['order_date'])[:10]
        settlement = payments.get(row['order_id'], order_date)[:10]
        key = (order_date, settlement, row['currency_code'], row['source_system'])
        groups[key].append(row)
        original_grain[(order_date, row['currency_code'], row['source_system'])].add(settlement)
    output = []
    for key, rows in groups.items():
        rate = Decimal(1) if key[2] in (None, 'USD') else rates.get((key[1], key[2], 'USD'))
        rate = Decimal(1) if rate is None else rate
        net = sql_sum(number(r['extended_price']) - number(r['discount_amount'])
                      if r['extended_price'] is not None and r['discount_amount'] is not None else None for r in rows)
        total = sql_sum(r['line_total'] for r in rows)
        out = dict(zip(KEYS, key))
        out.update(total_orders=len({r['order_id'] for r in rows if r['order_id'] is not None}),
                   unique_customers=len({r['customer_id'] for r in rows if r['customer_id'] is not None}),
                   total_line_items=sum(r['order_line_id'] is not None for r in rows),
                   gross_revenue=sql_sum(r['extended_price'] for r in rows),
                   total_discounts=sql_sum(r['discount_amount'] for r in rows),
                   net_revenue=net, total_tax=sql_sum(r['tax_amount'] for r in rows),
                   total_revenue=total, fx_rate=rounded(rate, '0.000001'),
                   net_revenue_usd=rounded(net * rate, '0.01') if net is not None else None,
                   total_revenue_usd=rounded(total * rate, '0.01') if total is not None else None)
        output.append(out)
    stats = {'input_rows': len(inputs['fct_sales']), 'original_grain_groups': len(original_grain),
             'reference_grain_groups': len(groups),
             'split_original_groups': sum(len(v) > 1 for v in original_grain.values()),
             'split_example': next(({'key': list(k), 'settlement_dates': sorted(v)}
                                    for k, v in original_grain.items() if len(v) > 1), None)}
    return sorted(output, key=encoded), stats


def compare(expected, snapshot, preserved_columns):
    columns = snapshot['columns']
    missing = sorted((set(preserved_columns) | set(KEYS) | set(CORE)) - set(columns))
    required = set(KEYS)
    if not required.issubset(columns):
        return {'equal': False, 'missing_columns': missing, 'missing_keys': [],
                'extra_keys': [], 'duplicates': [], 'value_mismatches': [], 'key_columns_missing': True}
    key = lambda row: tuple(row[k] for k in KEYS)
    counts = Counter(key(row) for row in snapshot['rows'])
    wanted = {key(row): row for row in expected}
    actual = {key(row): row for row in snapshot['rows']}
    mismatches = []
    for k in wanted.keys() & actual.keys():
        for column in CORE:
            if column not in columns:
                continue
            left, right = number(wanted[k][column]), number(actual[k][column])
            tolerance = Decimal(0) if column in EXACT_COUNTS else Decimal('0.000001')
            if left is None or right is None:
                same = left is right
            else:
                same = abs(left - right) <= tolerance
            if not same:
                mismatches.append({'key': list(k), 'column': column,
                                   'expected': str(left), 'actual': str(right)})
    result = {'missing_columns': missing,
              'missing_keys': sorted([list(k) for k in wanted.keys() - actual.keys()], key=encoded),
              'extra_keys': sorted([list(k) for k in actual.keys() - wanted.keys()], key=encoded),
              'duplicates': [{'key': list(k), 'count': n} for k, n in counts.items() if n != 1],
              'value_mismatches': sorted(mismatches, key=encoded)}
    result['equal'] = not any(result.values())
    return result
