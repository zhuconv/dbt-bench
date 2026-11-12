"""Allow source-faithful date or timestamp endpoints without changing artifacts."""
from dataclasses import replace
from datetime import date, datetime
import argparse
import json
from pathlib import Path
import audit_v1 as base
from common_io_v1 import encode

original_inspect = base.inspect


def inspect(candidate, relation, raw, name, companions=None):
    if not relation.endswith('customer_payment_velocity'):
        return original_inspect(candidate, relation, raw, name, companions)
    orders = {r['order_id']: r for r in ({k.lower(): v for k, v in x.items()} for x in raw['orders'])}
    groups = {}
    for payment in raw['payments']:
        p = {k.lower(): v for k, v in payment.items()}
        customer = orders[p['order_id']]['customer_id']
        groups.setdefault(customer, []).append(p['processed_at'])
    rows = []; errors = []
    for row in candidate.rows:
        row = dict(row); times = groups.get(row['customer_id'])
        for column, op in [('first_payment_date', min), ('last_payment_date', max)]:
            value = row[column]
            if times:
                wanted = op(times)
                if type(value) is datetime:
                    if value != wanted:
                        errors.append({'field': column, 'customer_id': row['customer_id'], 'actual': value, 'expected_timestamp': wanted})
                    row[column] = value.date()
                elif type(value) is not date:
                    errors.append({'field': column, 'customer_id': row['customer_id'], 'reason': 'Not a date or timestamp'})
        rows.append(row)
    # This is an explicit semantic comparison projection, never a replacement
    # snapshot or a claim that the submitted physical type was DATE.
    sql_types = dict(candidate.sql_types); physical = dict(candidate.physical_sql_types)
    for column in ('first_payment_date', 'last_payment_date'):
        if physical.get(column) in ('TIMESTAMP', 'TIMESTAMP_S', 'TIMESTAMP_MS', 'TIMESTAMP_NS'):
            sql_types[column] = physical[column] = 'DATE'
    projected = replace(candidate, rows=rows, sql_types=sql_types, physical_sql_types=physical)
    result = original_inspect(projected, relation, raw, name, companions)
    result['representation_review'] = {'actual_physical_types': candidate.physical_sql_types,
        'scope': 'Instruction does not prescribe DATE versus TIMESTAMP. Timestamps must equal the actual earliest/latest PROCESSED_AT; their calendar dates are then compared using the retained arithmetic.',
        'timestamp_errors': errors}
    if errors:
        result['reward'] = 0.0; result['status'] = 'scored'
        result.setdefault('field_errors', []).extend(errors)
    return result


def run(spec, root):
    base.inspect = inspect
    result = base.run(spec, root)
    result['checker_version'] = 'phase4-payment-scored-fraction-date-representation-v2'
    return result


if __name__ == '__main__':
    p = argparse.ArgumentParser(); p.add_argument('--spec', required=True)
    p.add_argument('--output', required=True); p.add_argument('--root', default='.')
    a = p.parse_args(); result = run(json.loads(Path(a.spec).read_text()), Path(a.root))
    Path(a.output).write_text(json.dumps(result, default=encode, indent=2) + '\n')
    print(json.dumps({'reward': result['reward']}))
