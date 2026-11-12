"""Independent LTV subset bounds and explicitly unresolved eligibility interpretations."""
from collections import defaultdict,Counter
from datetime import datetime
from decimal import Decimal as D,ROUND_HALF_UP
COLS=('customer_id','total_orders','lifetime_value','avg_order_value','first_order_date','last_order_date','value_segment')
def day(x):return x.date()if isinstance(x,datetime)else x
def rnd(x):return D(x).quantize(D('.01'),rounding=ROUND_HALF_UP)
def tier(x):return 'VIP'if x>=1000 else'High Value'if x>=500 else'Medium Value'if x>=100 else'Low Value'if x>0 else'No Value'
def derive(raw):
 ids=[r['customer_id']for r in raw['customers']];assert len(set(ids))==len(ids);by=defaultdict(list)
 for r in raw['orders']:by[r['customer_id']].append(r)
 policies={};bounds={};facts={'raw_customers':len(ids),'raw_orders':len(raw['orders']),'status_payment_counts':dict(Counter(str((r['status'],r['payment_status']))for r in raw['orders']))}
 for policy in ['exclude_cancelled_returned','also_exclude_unpaid','also_exclude_nonproduction']:
  out=[]
  for cid in ids:
   rs=[r for r in by[cid]if r['status']is not None and r['status']not in ['CANCELLED','RETURNED']]
   if policy=='also_exclude_unpaid':rs=[r for r in rs if(r['payment_status']or'').upper()!='UNPAID']
   if policy=='also_exclude_nonproduction':rs=[r for r in rs if not any(r[k]is True for k in ['test_order_flag','sample_order_flag','internal_order_flag'])]
   amounts=[D(r['grand_total'])for r in rs if r['grand_total']is not None];dates=[day(r['ordered_at'])for r in rs if r['ordered_at']is not None];ltv=rnd(sum(amounts,D(0)));out.append(dict(zip(COLS,[cid,len({r['order_id']for r in rs if r['order_id']is not None}),ltv,rnd(sum(amounts,D(0))/len(amounts))if amounts else D(0),min(dates)if dates else None,max(dates)if dates else None,tier(ltv)])))
  policies[policy]=out
 for cid in ids:
  rs=by[cid];amounts=[abs(D(r['grand_total']))for r in rs if r['grand_total']is not None];bounds[cid]={'all_raw_order_count':len({r['order_id']for r in rs if r['order_id']is not None}),'absolute_sum_upper':sum(amounts,D(0)),'absolute_average_upper':max(amounts,default=D(0)),'all_raw_dates':sorted({day(r['ordered_at'])for r in rs if r['ordered_at']is not None}),'no_raw_orders':not rs}
 eligible=[r for r in raw['orders']if r['status']is not None and r['status']not in ['CANCELLED','RETURNED']];facts['candidate_eligible_orders']=len(eligible);facts['included_unpaid_orders']=sum((r['payment_status']or'').upper()=='UNPAID'for r in eligible);facts['included_nonproduction_orders']=sum(any(r[k]is True for k in ['test_order_flag','sample_order_flag','internal_order_flag'])for r in eligible);facts['policy_differences']={k:sum(a!=b for a,b in zip(policies['exclude_cancelled_returned'],v))for k,v in policies.items()};facts['eligibility_unresolved']=True
 return {'policies':policies,'bounds':bounds,'facts':facts}
