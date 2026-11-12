"""Per-cell IEEE binary64 forward-error envelopes for the declared formulas.

Exact source conversion and integer divisions have one rounded binary64 value.
Money sums allow either source-decimal aggregation or at most two aggregation
levels of converted binary64 amounts. The usual gamma_n bound follows n finite
round-to-nearest additions (no under/overflow). Other formulas propagate these
intervals through their stated small arithmetic expression; exact-zero branches
remain exact. This is not a fitted epsilon or a native/gold tolerance.
"""
from fractions import Fraction as F
from collections import defaultdict
import math
U=F(1,2**53)
def exact(x):v=F(float(x));return(v,v)
def span(*intervals):return min(v[0]for v in intervals),max(v[1]for v in intervals)
def round_interval(interval):return F(float(interval[0])),F(float(interval[1]))
def mul(a,b):values=[x*y for x in a for y in b];return round_interval((min(values),max(values)))
def sub(a,b):return round_interval((a[0]-b[1],a[1]-b[0]))
def div(a,b):
 assert b[0]>0
 values=[x/y for x in a for y in b];return round_interval((min(values),max(values)))
def add_sum(values):
 if not values or all(v==0 for v in values):return(F(0),F(0))
 n=max(0,2*len(values)-1);gamma=n*U/(1-n*U);binary=[F(float(v))for v in values];center=sum(binary);bound=gamma*sum(abs(v)for v in binary);return span((center-bound,center+bound),exact(sum(values)))
def derive_bounds(expected,details):
 hourly=defaultdict(list);daily=defaultdict(list)
 for row in details['raw']['orders']:
  if row['warehouse_id']is None or row['ordered_at']is None:continue
  value=F(row['grand_total']);key=row['warehouse_id'],row['ordered_at'].date();daily[key].append(value);hourly[key+(row['ordered_at'].hour,)].append(value)
 bounds={};dates={}
 for row in expected['main_intermediate.int_capacity__daily_utilization']:
  key=row['warehouse_id'],row['utilization_date'];cap=span(exact(row['theoretical_daily_capacity']),mul(exact(row['location_count']),exact(F(1,20))));util=span(exact(row['utilization_rate']),div(exact(row['daily_orders']),cap));dates[key]={'daily_order_value':add_sum(daily[key]),'theoretical_daily_capacity':cap,'utilization_rate':util}
 for relation,rows in expected.items():
  for row in rows:
   key=(row['order_id'],)if relation.endswith('__orders')else(row['shipment_id'],)if relation.endswith('__shipments')else(row['warehouse_id'],row['volume_date'],row['volume_hour'])if relation.endswith('hourly_volume')else(row['warehouse_id'],row['utilization_date'])if relation.endswith('daily_utilization')else(row['warehouse_id'],row['capacity_date'])
   item={n:exact(value)for n,value in row.items()if isinstance(value,F)}
   if relation.endswith('hourly_volume'):item['order_value']=add_sum(hourly[key])
   elif relation.endswith('daily_utilization'):item.update(dates[key])
   elif relation.endswith('fct_warehouse_capacity'):
    item['total_order_value']=dates[key]['daily_order_value'];item['utilization_rate']=dates[key]['utilization_rate'];avg=exact(row['avg_hourly_orders']);item['peak_load_factor']=(F(1),F(1))if row['avg_hourly_orders']==0 else span(exact(row['peak_load_factor']),div(exact(row['max_hourly_orders']),avg));util=dates[key]['utilization_rate'];item['capacity_headroom_pct']=span(exact(row['capacity_headroom_pct']),mul(sub(exact(1),util),exact(100)),sub(exact(100),mul(util,exact(100))))
   elif relation.endswith('rpt_capacity_bottlenecks'):
    fact=bounds['main_marts.fct_warehouse_capacity',key];item['utilization_rate']=fact['utilization_rate'];item['peak_load_factor']=fact['peak_load_factor']
   bounds[relation,key]=item
 return bounds

def admit(value,interval):return type(value)is float and math.isfinite(value)and interval[0]<=F(value)<=interval[1]
