"""Independent raw-input geographic derivation. No model/gold/checker imports."""
from collections import defaultdict
from decimal import Decimal, ROUND_HALF_UP, localcontext
class InputUnknown(ValueError): pass
def money(v):
    if v is None or isinstance(v,(float,bool)): raise InputUnknown('Exact non-NULL source DECIMAL required')
    return Decimal(v)
def rounded(v): return v.quantize(Decimal('.01'),rounding=ROUND_HALF_UP)
def derive(raw):
    addresses=[];orders=[]
    for r in raw['addresses']:
        flag=r['is_default_shipping']
        if type(flag) is not bool: raise InputUnknown('Unresolved source shipping flag representation')
        if flag:
            if r['state_province'] is None or r['customer_id'] is None: raise InputUnknown('Default address NULL state/customer policy unspecified')
            addresses.append({k:r[k]for k in ('address_id','customer_id','state_province')})
    for r in raw['orders']:
        status=r['status']
        if status is None: raise InputUnknown('NULL status eligibility policy unspecified')
        if status.upper() in ('COMPLETED','DELIVERED','SHIPPED'):
            orders.append({k:r[k]for k in ('order_id','customer_id')}|{'grand_total':money(r['grand_total'])})
    indexed=defaultdict(list);states=defaultdict(list)
    for r in orders:indexed[r['customer_id']].append(r)
    for r in addresses:states[r['state_province']].append(r)
    result=[]
    with localcontext() as ctx:
        ctx.prec=60
        for state,ads in sorted(states.items()):
            customers={a['customer_id']for a in ads};joined=[o for a in ads for o in indexed.get(a['customer_id'],[])];orderids={o['order_id']for o in joined if o['order_id']is not None};total=sum((r['grand_total']for r in joined),Decimal(0));n=len(customers)
            result.append({'state_province':state,'customer_count':n,'order_count':len(orderids),'total_revenue':rounded(total),'avg_order_value':rounded(total/len(joined))if joined else Decimal(0),'revenue_per_customer':rounded(total/n),'orders_per_customer':Decimal(len(orderids))/n})
    return {'stg_customer_addresses':addresses,'stg_orders':orders,'fct_state_customers':result}, {'source_addresses':len(raw['addresses']),'source_orders':len(raw['orders']),'default_addresses':len(addresses),'eligible_orders':len(orders),'states':len(result),'no_order_states':sum(r['order_count']==0 for r in result),'ratio_policy':'Exact orders/customer rational retained separately; task only explicitly rounds monetary fields. No native SQL/rounding reused.'}
