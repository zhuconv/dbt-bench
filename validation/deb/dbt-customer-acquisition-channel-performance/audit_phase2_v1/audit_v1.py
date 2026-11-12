"""Acquisition audit with strict explicit types and policy-independent accounting checks."""
from decimal import Decimal as D
from fractions import Fraction as F
import math,re
from audit_tables import TablePolicy,audit_table
from dbt_bench.comparison import Column,TableContract
from expected_v1 import COLS,tier
NAME='rpt_customer_acquisition_channel_performance_fixed';INTS=('TINYINT','SMALLINT','INTEGER','BIGINT','HUGEINT','UTINYINT','USMALLINT','UINTEGER','UBIGINT','UHUGEINT')
def ck(s,r,**kw):return dict(status=s,reason=r,**kw)
def num(x):return type(x)in[int,float,D]and D(str(x)).is_finite()
def frac(x):return F.from_float(x)if type(x)is float else F(x)
def nearest(x,p):
 y=F(x)*10**p;q,r=divmod(y.numerator,y.denominator);ns=[q]if 2*r<y.denominator else[q+1]if 2*r>y.denominator else[q,q+1];return [F(n,10**p)for n in ns]
def representation(a,v,places=2,explicit=True):
 if not num(a)or v is None:return 'fail'
 af=frac(a);ulp=F.from_float(math.ulp(a))if type(a)is float else F(0);v=F(v)
 if explicit:return'pass'if any(abs(af-n)<=ulp for n in nearest(v,places))else'fail'
 if abs(af-v)<=ulp or any(abs(af-n)<=ulp for p in range(places,19)for n in nearest(v,p)):return'pass'
 if any(abs(af-n)<=ulp for p in range(places)for n in nearest(v,p)):return'gap'
 return'fail'
def evaluate(outputs,expected,workflow=None):
 o=outputs.get(NAME,{});rows=o.get('rows');table=o.get('table');checks={};profile=expected['profile']
 if o.get('exists')is False:checks['presence']=ck('fail','Required analytics TABLE absent')
 if o.get('missing_required_columns'):checks['required_columns']=ck('fail','Required columns absent',columns=o['missing_required_columns'])
 if table is not None:
  types={};cols=[]
  for c in COLS:
   typ=table.physical_sql_types.get(c,'');text=c in['acquisition_channel','channel_tier'];integer=c in['customer_count','total_orders'];types[c]=('VARCHAR',)if text else INTS if integer else(typ,)if re.fullmatch(r'DECIMAL\(\d+,\d+\)',typ)else('DECIMAL(18,2)',);cols.append(Column(c,'text'if text else'integer'if integer else'numeric'))
  result=audit_table(table,TablePolicy(TableContract(tuple(cols),keys=('acquisition_channel',),allow_extra_columns=True),types,not_null=('acquisition_channel',),relation_types=('TABLE','BASE TABLE')));checks['interface']=ck('pending'if result['status']=='unknown'else'pass'if result['reward']==1 else'fail','Explicit VARCHAR, integer count, DECIMAL measure columns and TABLE materialization; row order and extra columns not imposed',details=result)
 else:checks['interface']=ck('pending','Physical SQL schema unavailable')
 if rows is not None:
  names=[r.get('acquisition_channel')for r in rows];checks['channel_grain']=ck('pass'if len(set(names))==len(names)and all(isinstance(v,str)for v in names)else'fail','Exactly one row per non-null acquisition channel');nonnegative=[];rounding=[];tiers=[];ratios={c:[]for c in ['average_order_value','customer_lifetime_value','average_orders_per_customer']};outside=[]
  for r in rows:
   ch=r.get('acquisition_channel');cc=r.get('customer_count');oc=r.get('total_orders');rev=r.get('total_revenue');rate=r.get('repeat_purchase_rate')
   if any(not num(r.get(c))or frac(r[c])<0 for c in COLS[1:8])or type(cc)is not int or type(oc)is not int or cc<=0 or oc<=0:nonnegative.append(ch)
   if num(rate)and not 0<=frac(rate)<=100:outside.append(ch)
   for c in ['total_revenue','average_order_value','customer_lifetime_value','repeat_purchase_rate']:
    if not num(r.get(c))or representation(r.get(c),frac(r[c]),2,True)!='pass':rounding.append({'channel':ch,'column':c,'actual':str(r.get(c))})
   for c,n,d,explicit in [('average_order_value',rev,oc,True),('customer_lifetime_value',rev,cc,True),('average_orders_per_customer',oc,cc,False)]:
    state=representation(r.get(c),frac(n)/frac(d),2,explicit)if num(n)and num(d)and d else'fail';ratios[c].append({'channel':ch,'state':state,'actual':str(r.get(c)),'conditional_fraction':str(frac(n)/frac(d))if num(n)and num(d)and d else None})
   if num(r.get('customer_lifetime_value'))and num(rate)and type(cc)is int:
    want=tier(frac(r['customer_lifetime_value']),frac(rate),cc)
    if r.get('channel_tier')!=want:tiers.append({'channel':ch,'actual':r.get('channel_tier'),'expected_from_actual_declared_metrics':want})
   else:tiers.append({'channel':ch,'reason':'Invalid metrics'})
  checks['nonnegative_integral_counts']=ck('fail'if nonnegative else'pass','All numeric measures finite/nonnegative and positive integer denominators for represented eligible channels',errors=nonnegative);checks['repeat_percent_bounds']=ck('fail'if outside else'pass','Repeat percentage within0..100',errors=outside);checks['explicit_rounding']=ck('fail'if rounding else'pass','Money and percentage explicitly round to2places; binary float ULP only for value serialization, physical DECIMAL checked separately',errors=rounding[:8]);checks['conditional_tier']=ck('fail'if tiers else'pass','Ordered tier logic from actual submitted CLTV/repeat/count, without choosing NULL-date policy',errors=tiers[:8])
  for c,ds in ratios.items():checks['conditional_'+c]=ck('fail'if any(v['state']=='fail'for v in ds)else'gap'if any(v['state']=='gap'for v in ds)else'pass','Formula from actual declared numerator/denominator independent of attribution policy',details=ds)
  matched={};diffs={}
  for policy,data in expected['policies'].items():
   wm={r[0]:dict(zip(COLS,r))for r in data['rows']};got={r['acquisition_channel']:r for r in rows};errors=[]
   for k,r in got.items():
    if k not in wm:errors.append({'channel':k,'missing_from_policy':True});continue
    for c in COLS[1:]:
     e=wm[k][c];ok=r.get(c)==e if c in['customer_count','total_orders','channel_tier']else representation(r.get(c),F(e),2,c!='average_orders_per_customer')=='pass'
     if not ok:errors.append({'channel':k,'column':c,'actual':str(r.get(c)),'expected_under_disclosed_policy':str(e)})
   matched[policy]=not errors and set(got)==set(wm)and len(got)==len(rows);diffs[policy]=errors[:8]
  checks['disclosed_policy_comparison']=ck('gap','Exact source recomputation under two declared NULL-date readings actually compared; failure to match this nonexhaustive set is not alone proof of business error',matched_policies=[p for p,v in matched.items()if v],differences=diffs)
 checks['definition_policy']=ck('gap'if profile['undated_only_customer_ids']or profile['known_dated_customers_with_undated_orders']or profile['earliest_different_channel_ties']else'pass','Actual NULL-date first-touch and eligible-order inclusion alternatives are not uniquely specified; do not infer policy from reference SQL',undated_only_customer_count=len(profile['undated_only_customer_ids']),known_customer_undated_order_count=len(profile['known_dated_customers_with_undated_orders']),earliest_channel_ties=profile['earliest_different_channel_ties']);checks['workflow']=workflow or ck('pending','Trusted actual source/upstream workflow evidence unavailable');states=[v['status']for v in checks.values()];reward=0 if'fail'in states else None if'gap'in states or'pending'in states else 1
 return dict(status='unknown'if reward is None else'scored',reward=reward,checks=checks,model_requests=0,native_rewards_modified=False)
