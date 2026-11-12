"""Python derivation from original orders, quantities, and address projections."""
from collections import Counter, defaultdict
from datetime import datetime
from decimal import Decimal, ROUND_HALF_UP

QUERIES = {
    'orders': 'SELECT order_id,customer_id,ordered_at,grand_total,billing_address_id,shipping_address_id,chargeback_flag,is_first_order FROM main.orders',
    'lines': 'SELECT order_line_id,order_id,quantity_ordered FROM main.order_lines',
    'addresses': 'SELECT address_id,state_province FROM main.addresses',
}
COLUMNS = ('order_id','customer_id','ordered_at','grand_total','total_items','orders_same_day','billing_state','shipping_state','is_high_value','is_bulk_order','is_velocity_fraud','is_address_mismatch','is_first_order_high_value','has_chargeback_history','fraud_flags_count','fraud_risk_level')

def trim(x):
    return None if x is None else str(x).strip()

def truth(x):
    return str(x).upper() in ('1','TRUE','T','Y','YES')

def build(raw):
    orders = [{k.lower(): v for k, v in r.items()} for r in raw['orders']]
    lines = [{k.lower(): v for k, v in r.items()} for r in raw['lines']]
    addresses = [{k.lower(): v for k, v in r.items()} for r in raw['addresses']]
    assert len({trim(r['order_id']) for r in orders}) == len(orders), 'Original order grain is not unique'
    assert len({trim(r['address_id']) for r in addresses}) == len(addresses), 'Original address grain is not unique'
    address = {trim(r['address_id']): trim(r['state_province']) for r in addresses}
    items = defaultdict(int)
    for r in lines:
        quantity = r['quantity_ordered'] or 0
        assert Decimal(str(quantity)) == int(quantity), 'Nonintegral source quantity needs separate interpretation'
        items[trim(r['order_id'])] += int(quantity)
    daily = Counter()
    history = defaultdict(bool)
    for r in orders:
        r['ordered_at'] = datetime.fromisoformat(r['ordered_at']) if isinstance(r['ordered_at'], str) else r['ordered_at']
        assert r['ordered_at'] is not None and r['grand_total'] is not None, 'NULL amount/date policy not specified'
        daily[trim(r['customer_id']), r['ordered_at'].date()] += 1
        history[trim(r['customer_id'])] |= truth(r['chargeback_flag'])
    result = []
    for r in orders:
        oid, cid = trim(r['order_id']), trim(r['customer_id'])
        amount = Decimal(str(r['grand_total'])).quantize(Decimal('.01'), rounding=ROUND_HALF_UP)
        b, s = address.get(trim(r['billing_address_id'])), address.get(trim(r['shipping_address_id']))
        n = daily[cid, r['ordered_at'].date()]
        flags = [amount > 1500, items[oid] > 5, n >= 2, b is not None and s is not None and b != s, truth(r['is_first_order']) and amount > 500, history[cid]]
        count = sum(flags)
        level = 'CRITICAL' if count >= 4 else {3:'HIGH',2:'MEDIUM',1:'LOW',0:'NONE'}[count]
        result.append(dict(zip(COLUMNS, [oid,cid,r['ordered_at'],amount,items[oid],n,b,s,*flags,count,level])))
    result.sort(key=lambda r: r['order_id'])
    return {'fraud_flagged_orders': result}, {'raw_counts': {k: len(v) for k,v in raw.items()}, 'all_orders': len(result), 'missing_address_states': sum(r['billing_state'] is None or r['shipping_state'] is None for r in result), 'unflagged': sum(r['fraud_flags_count'] == 0 for r in result), 'scope': 'Complete current input; source flags denote first-ever order and any customer chargeback including current order. No reference/native checker called.'}
