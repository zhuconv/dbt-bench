"""Recognize the two exact source-derived DOUBLE division evaluation orders."""
import argparse
import json
from pathlib import Path
import audit_v2 as base
from common_io_v1 import encode

previous_inspect = base.inspect


def inspect(candidate, relation, raw, name, companions=None):
    result = previous_inspect(candidate, relation, raw, name, companions)
    if not relation.endswith('customer_payment_velocity'):
        return result
    orders = {r['order_id']: r for r in ({k.lower(): v for k, v in x.items()} for x in raw['orders'])}
    groups = {}
    for payment in raw['payments']:
        row = {k.lower(): v for k, v in payment.items()}
        groups.setdefault(orders[row['order_id']]['customer_id'], []).append(row)
    retained = []; accepted = []
    for error in result.get('field_errors', []):
        if error.get('rule') == 'R-customer_payment_velocity-amount_per_day':
            row = candidate.rows[error['row']]; payments = groups[row['customer_id']]
            total = sum(p['amount'] for p in payments)
            days = max((max(p['processed_at'] for p in payments).date() - min(p['processed_at'] for p in payments).date()).days, 1)
            options = [float(total) / days, float(total / days)]
            if type(error['actual']) is float and error['actual'] in options:
                accepted.append({'original_observation': error, 'exact_source_evaluation_options': options})
                continue
        retained.append(error)
    result['field_errors'] = retained
    result['floating_evaluation_review'] = {'accepted': accepted,
        'scope': 'Only amount_per_day: exact Decimal source sum converted before or after division. Exact equality to either computed IEEE value; no scalar tolerance or candidate-derived expected value.'}
    result['reward'] = 0.0 if retained else None
    result['status'] = 'scored' if retained else 'unknown'
    return result


def run(spec, root):
    # Avoid recursively replacing the function used inside the v2 adapter.
    base.base.inspect = inspect
    result = base.base.run(spec, root)
    result['checker_version'] = 'phase4-payment-source-representation-v3'
    return result


if __name__ == '__main__':
    p = argparse.ArgumentParser(); p.add_argument('--spec', required=True)
    p.add_argument('--output', required=True); p.add_argument('--root', default='.')
    a = p.parse_args(); result = run(json.loads(Path(a.spec).read_text()), Path(a.root))
    Path(a.output).write_text(json.dumps(result, default=encode, indent=2) + '\n')
    print(json.dumps({'reward': result['reward']}))
