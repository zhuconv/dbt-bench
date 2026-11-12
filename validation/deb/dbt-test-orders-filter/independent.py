"""Independent interpretation of declared filtering, preservation and rounding.

Only the three named purpose flags are used. The separate
exclude_from_reporting flag has no stated policy in this task and is retained.
"""
from collections import Counter
from datetime import datetime
from decimal import Decimal, ROUND_HALF_UP

FLAGS = ('test_order_flag','sample_order_flag','internal_order_flag')
MONEY = ('subtotal','discount_total','shipping_total','tax_total','grand_total',
         'cost_of_goods','gross_margin','commission_amount','affiliate_fee',
         'payment_processing_fee','net_revenue','tax_exempt_amount',
         'gift_card_amount','store_credit_amount','customer_ltv_at_order')


def production(row):
    for column in FLAGS:
        value = row[column]
        if value is not None and not isinstance(value,bool):
            raise ValueError('The pinned input flags must be Boolean or NULL')
    return not any(row[c] is True for c in FLAGS)


def money(value):
    if value is None:return None
    if isinstance(value,bool):raise ValueError('Boolean amount')
    value = Decimal(str(value))
    if not value.is_finite():raise ValueError('Nonfinite amount')
    return value.quantize(Decimal('0.01'),rounding=ROUND_HALF_UP)


def derive(rows):
    expected=[]
    for row in rows:
        if not production(row):continue
        item=dict(row)
        for column in MONEY:
            if column in item:item[column]=money(item[column])
        item['order_date']=datetime.fromisoformat(str(row['ordered_at'])).date().isoformat()
        expected.append(item)
    return expected


def scalar(value, column, types):
    if value is None:return None
    kind=types.get(column,'DATE' if column=='order_date' else None)
    if kind is None:raise ValueError('Missing declared type for '+column)
    if kind.upper().startswith(('DECIMAL','NUMERIC','DOUBLE','FLOAT','REAL','INTEGER','BIGINT','SMALLINT','TINYINT','HUGEINT')):
        if isinstance(value,bool):raise ValueError('Boolean in numeric column')
        return Decimal(str(value))
    if kind.upper()=='BOOLEAN':
        if not isinstance(value,bool):raise ValueError('Non-boolean value')
        return value
    return str(value)


def compare(expected, observed, columns, types):
    types={name.lower():kind for name,kind in types.items()}
    untyped=set(columns)-set(types)-{'order_date'}
    if untyped:raise ValueError('Incomplete input schema: '+str(sorted(untyped)))
    missing_columns=sorted(set(columns)-set(observed['columns']))
    keys=[r.get('order_id') for r in observed['rows']]
    duplicates={str(k):n for k,n in Counter(keys).items() if n>1}
    actual={r.get('order_id'):r for r in observed['rows']}
    wanted={r['order_id']:r for r in expected}
    if len(wanted)!=len(expected):raise ValueError('Expected order key is not unique')
    missing=sorted(set(wanted)-set(actual));extra=sorted(set(actual)-set(wanted),key=str)
    mismatches=[]
    for key in sorted(set(wanted)&set(actual)):
        for column in columns:
            if column in missing_columns:continue
            left=scalar(wanted[key][column],column,types)
            right=scalar(actual[key][column],column,types)
            if left!=right:
                mismatches.append({'order_id':key,'column':column,'expected':str(left) if left is not None else None,'actual':str(right) if right is not None else None})
    return {'equal':not(missing_columns or duplicates or missing or extra or mismatches),
        'expected_rows':len(expected),'actual_rows':len(observed['rows']),
        'missing_columns':missing_columns,'duplicate_keys':duplicates,
        'missing_keys':missing,'extra_keys':extra,'value_mismatches':mismatches}
