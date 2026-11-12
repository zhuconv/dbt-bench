"""Independent Python arithmetic for the instruction's row/unit contradiction.

Reads declared inputs after the native reference project's two input models
have been rebuilt. Does not import the task verifier or reference solution.
"""
from collections import defaultdict
from datetime import datetime
from decimal import Decimal
import hashlib
import json
from pathlib import Path
import duckdb


out = Path('/evidence')
c = duckdb.connect('/app/database/retail.duckdb', read_only=True)
queries = {
    'orders': 'SELECT order_id, ordered_at, status FROM main.int_sales__orders_enriched ORDER BY order_id',
    'lines': 'SELECT order_line_id, order_id, product_id, sku, quantity_ordered FROM main.int_sales__order_lines ORDER BY order_line_id',
    'returns': 'SELECT return_line_id, order_line_id, quantity_returned, created_at FROM ORDERS.RETURN_LINES ORDER BY return_line_id',
}
inputs = {}
refs = []
for name, sql in queries.items():
    q = c.execute(sql)
    columns = [x[0].lower() for x in q.description]
    rows = [dict(zip(columns, row)) for row in q.fetchall()]
    inputs[name] = rows
    data = json.dumps(rows, default=str, sort_keys=True).encode()
    (out / f'{name}.json').write_bytes(data)
    refs.append({'file': f'{name}.json', 'sha256': hashlib.sha256(data).hexdigest(), 'rows': len(rows), 'query': sql})
c.close()
orders = defaultdict(list)
lines = defaultdict(list)
for row in inputs['orders']:
    orders[row['order_id']].append(row)
for row in inputs['lines']:
    lines[row['order_line_id']].append(row)


def dt(value):
    return value if isinstance(value, datetime) else datetime.fromisoformat(value)


def eligible(line, order):
    return line['product_id'] is not None and order['status'] is not None and order['status'] not in ('CANCELLED', 'FAILED')


def key(line, order):
    month = str(dt(order['ordered_at']).date().replace(day=1)) if order['ordered_at'] is not None else None
    return (month, line['product_id'], line['sku'] if line['sku'] is not None else line['product_id'])


groups = defaultdict(lambda: {'units_ordered': Decimal(0), 'units_returned': Decimal(0),
                              'fast_returns': 0, 'medium_returns': 0, 'slow_returns': 0, 'return_lines': []})
for line in inputs['lines']:
    for order in orders[line['order_id']]:
        if eligible(line, order) and line['quantity_ordered'] is not None:
            groups[key(line, order)]['units_ordered'] += Decimal(str(line['quantity_ordered']))
filtered = defaultdict(int)
for ret in inputs['returns']:
    if ret['quantity_returned'] is None or Decimal(str(ret['quantity_returned'])) <= 0:
        filtered['nonpositive_return'] += 1
        continue
    quantity = Decimal(str(ret['quantity_returned']))
    for line in lines[ret['order_line_id']]:
        for order in orders[line['order_id']]:
            if not eligible(line, order):
                filtered['excluded_joined_line'] += 1
                continue
            if ret['created_at'] is None or order['ordered_at'] is None:
                filtered['missing_date'] += 1
                continue
            elapsed = dt(ret['created_at']) - dt(order['ordered_at'])
            if elapsed.total_seconds() < 0:
                filtered['return_before_order'] += 1
                continue
            group = groups[key(line, order)]
            group['units_returned'] += quantity
            days = elapsed.total_seconds() / 86400
            group['fast_returns' if days <= 7 else 'medium_returns' if days <= 30 else 'slow_returns'] += 1
            group['return_lines'].append({'return_line_id': ret['return_line_id'], 'quantity_returned': str(quantity), 'days_to_return': days})

contradictions = []
over_returned = []
for group_key, g in sorted(groups.items(), key=lambda item: str(item[0])):
    if not g['units_returned']:
        continue
    n = g['fast_returns'] + g['medium_returns'] + g['slow_returns']
    ratio = Decimal(n) / g['units_returned']
    row = {'month_start': group_key[0], 'product_id': group_key[1], 'sku': group_key[2], **g,
           'velocity_rate_sum_required_by_formulas': str(ratio), 'sum_invariant_required': '1.0'}
    if abs(ratio - 1) > Decimal('0.0001'):
        contradictions.append(row)
    if g['units_returned'] > g['units_ordered']:
        over_returned.append(row)
assert contradictions, 'No original-input row/unit contradiction observed; do not infer it from native failures'
result = {'scope': 'Original pinned full inputs after rebuilding the two declared native input models; independent Python joins/counts/Decimal arithmetic',
          'inputs': refs, 'filtered': dict(filtered), 'groups_with_returns': sum(bool(g['units_returned']) for g in groups.values()),
          'input_duplicate_keys': {'order_id': sum(len(rows) > 1 for rows in orders.values()), 'order_line_id': sum(len(rows) > 1 for rows in lines.values())},
          'join_semantics': 'Preserve multiplicity of every matching input order/order-line row; do not silently deduplicate the source models. Exclude missing/negative return dates according to the declared nonnegative days condition.',
          'velocity_contradiction_groups': len(contradictions), 'units_returned_exceed_ordered_groups': len(over_returned),
          'derivation': 'Each eligible return line belongs to exactly one fast/medium/slow segment. The specified counts sum to line count. Dividing by SUM(quantity_returned) gives line_count / returned_units, which differs from the separately required sum=1 on these inputs.',
          'contradictions': contradictions, 'over_returned': over_returned,
          'checker_invoked': False, 'native_reward': None, 'task_certified': False}
(out / 'diagnosis.json').write_text(json.dumps(result, default=str, indent=2) + '\n')
print(json.dumps({k: v for k, v in result.items() if k not in ('inputs', 'contradictions', 'over_returned')}, indent=2))
