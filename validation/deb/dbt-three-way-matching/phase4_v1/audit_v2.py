"""Permit coherent earliest/latest invoice-date representations per PO line."""
from copy import deepcopy
from decimal import Decimal
import audit_v1 as legacy
from dbt_bench.comparison import Column, TableContract, compare_tables


def evaluate(outputs, expected, workflow=None):
    alternate = expected.get('legal_invoice_date_alternatives', {}).get('latest')
    if alternate is None:
        return legacy.evaluate(outputs, expected, workflow)
    fields = expected['columns']
    rows = outputs.get(legacy.NAME, {}).get('rows')
    if rows is None:
        return legacy.evaluate(outputs, expected, workflow)
    first = {r[fields.index('po_line_id')]: r for r in expected['policies']['zero_reference']['rows']}
    last = {r[fields.index('po_line_id')]: r for r in alternate['policies']['zero_reference']['rows']}
    got = {r.get('po_line_id'): r for r in rows}
    cols = tuple(Column(c, 'text' if c in legacy.TEXT else 'integer' if c in legacy.COUNTS else 'numeric',
                        abs_tol=Decimal('1e-8') if c not in legacy.TEXT | legacy.COUNTS else Decimal(0))
                 for c in fields if c != 'match_id')
    policy = TableContract(cols, keys=('po_line_id',), allow_extra_columns=True)
    selected = []; choices = {}
    for key, values in first.items():
        want = dict(zip(fields, values)); actual = got.get(key)
        if actual is not None and compare_tables([want], [actual], policy).equal:
            selected.append(values); choices[key] = 'earliest'
        elif actual is not None and compare_tables([dict(zip(fields, last[key]))], [actual], policy).equal:
            selected.append(last[key]); choices[key] = 'latest'
        else:
            selected.append(values); choices[key] = 'neither_complete_interpretation'
    effective = deepcopy(expected)
    effective['policies']['zero_reference']['rows'] = selected
    result = legacy.evaluate(outputs, effective, workflow)
    result['invoice_date_review'] = {'policy': expected['invoice_date_policy'], 'per_line': choices,
        'scope': 'Expected alternatives are independently derived before reading output; candidate selects only a complete coherent row, never arbitrary per-column combinations.'}
    return result
