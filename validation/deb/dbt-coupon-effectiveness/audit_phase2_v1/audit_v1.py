"""Conditional source audit; unresolved population/rounding/percentile policies stay gaps."""
from collections import Counter
from decimal import Decimal as D,ROUND_HALF_UP,ROUND_HALF_DOWN
from datetime import date,datetime
import math,re
from audit_tables import TablePolicy,audit_table
from dbt_bench.comparison import Column,TableContract
from expected_v2 import derive,EFFECT,SUMMARY,TRENDS,INT
KIND='coupon'
COLS={'int_coupon_redemptions':INT,'coupon_effectiveness':EFFECT,'coupon_summary':SUMMARY,'coupon_trends':TRENDS}
KEYS={'int_coupon_redemptions':['redemption_id','coupon_id','order_id'],'coupon_effectiveness':['promotion_id'],'coupon_summary':['promotion_type','discount_type'],'coupon_trends':['promotion_id','redemption_month']}
TEXT={'promotion_id','promotion_name','promotion_type','discount_type','activity_status','performance_tier','efficiency_rating','customer_focus','concentration_risk','decay_classification','redemption_month','redemption_id','coupon_id','order_id','customer_id','coupon_code','redeemed_at'}
COUNTS={'total_coupons','total_redemptions','unique_customers','unique_orders','repeat_redeemer_count','total_usage_limit','first_time_redemptions','repeat_redemptions','days_active','revenue_rank','top_customer_redemptions','single_use_customer_count','early_redemption_count','late_redemption_count','promotion_count','high_performer_count','excellent_efficiency_count','monthly_redemptions','monthly_unique_customers','monthly_first_time_count','cumulative_redemptions','month_rank','weekday_redemptions','weekend_redemptions','usage_limit','day_of_week'}
INTS=('TINYINT','SMALLINT','INTEGER','BIGINT','HUGEINT','UTINYINT','USMALLINT','UINTEGER','UBIGINT','UHUGEINT')
def ck(s,r,**kw):return dict(status=s,reason=r,**kw)
def eq(a,b):
 if isinstance(b,D):return type(a)in[D,int,float]and D(str(a)).is_finite()and D(str(a))==b
 return type(a)is type(b)and a==b

def policies(raw):return[derive(raw,r,s,t)for r in[ROUND_HALF_UP,ROUND_HALF_DOWN]for s in['raw','displayed']for t in['ntile','ceil','floor']]
def evaluate(outputs,expected,workflow=None):
 checks={};variants=expected['policies'];base=variants[0];profile=base['profile']
 for name,cols in COLS.items():
  o=outputs.get(name,{});rows=o.get('rows');t=o.get('table');inter=name=='int_coupon_redemptions';wanted=[]
  for p in variants:w=p['relations'][name];wanted.append([dict(zip(w['columns'],r))for r in w['rows']])
  if o.get('exists')is False:checks[name+'.presence']=ck('fail','Required named relation absent')
  if o.get('missing_required_columns'):checks[name+'.columns']=ck('fail'if not inter else'gap','Required final output missing, or undeclared intermediate projection unresolved',columns=o['missing_required_columns'])
  if rows is None:checks[name+'.values']=ck('pending','Actual output unavailable');continue
  if t is None:checks[name+'.interface']=ck('pending','Physical SQL schema unavailable')
  elif inter:checks[name+'.interface']=ck('pass'if t.relation_type=='VIEW'else'fail','Intermediate must be view; no17-column fixed internal schema is invented')
  else:
   types={};cs=[]
   for c in cols:
    typ=t.physical_sql_types.get(c,'');kind='text'if c in TEXT else'integer'if c in COUNTS else'date'if c in['first_redemption_date','last_redemption_date']else'numeric';cs.append(Column(c,kind));types[c]=('VARCHAR',)if kind=='text'else INTS if kind=='integer'else('DATE',)if kind=='date'else(typ,)if re.fullmatch(r'DECIMAL\(\d+,\d+\)',typ)else('DECIMAL',)
   a=audit_table(t,TablePolicy(TableContract(tuple(cs),keys=(),allow_extra_columns=True),types,relation_types=('TABLE','BASE TABLE')));checks[name+'.interface']=ck('pending'if a['status']=='unknown'else'pass'if a['reward']==1 else'fail','Declared final column names, decimal/integer/date/text families, and table materialization; nullable group dimensions validated separately',details=a)
  keys=KEYS[name];maps=[{tuple(r[k]for k in keys):r for r in w}for w in wanted];got={tuple(r.get(k)for k in keys):r for r in rows};counts=Counter(tuple(r.get(k)for k in keys)for r in rows);checks[name+'.population']=ck('pass'if set(got)==set(maps[0])and len(rows)==len(got)else'fail','Current active population membership and multiplicities; zero-redemption whole population remains separate policy gap',missing=[str(k)for k in maps[0].keys()-got.keys()][:8],extra=[str(k)for k in got.keys()-maps[0].keys()][:8],duplicates=[str(k)for k,v in counts.items()if v>1][:8])
  for col in cols:
   if inter and col=='day_of_week':continue # Internal encoding is not a declared output convention.
   if inter and any(col not in r for r in rows):checks[name+'.'+col]=ck('gap','Actual intermediate representation lacks undeclared auxiliary field; final semantics checked independently');continue
   errors=[];variable=[]
   for key,row in got.items():
    if key not in maps[0]:continue
    options=[m[key][col]for m in maps];a=row.get(col)
    if not any(eq(a,b)for b in options):errors.append({'key':str(key),'actual':str(a),'independent_options':list(dict.fromkeys(map(str,options)))})
    elif any(not eq(options[0],x)for x in options[1:]):variable.append(str(key))
   checks[name+'.'+col]=ck('fail'if errors else'gap'if variable else'pass','Original input and declared arithmetic; policy-dependent cells remain unadjudicated, never a single forced default',mismatch_count=len(errors),mismatches=errors[:8],policy_dependent_cells=len(variable))
  if inter:
   bad=[r.get('redemption_id')for r in rows if type(r.get('discount_amount'))in[D,int,float]and D(str(r['discount_amount']))<=0];checks['positive_discount']=ck('fail'if bad else'pass','Actual unified redemption rows require strictly positive original discount',bad_ids=bad[:8])
  else:
   orderkeys=['revenue_rank']if name=='coupon_effectiveness'else['total_order_value']if name=='coupon_summary'else['promotion_id','redemption_month'];seq=[tuple(r.get(c)for c in orderkeys)for r in rows]
   try:ordered=all(a>=b for a,b in zip(seq,seq[1:]))if name=='coupon_summary'else all(a<=b for a,b in zip(seq,seq[1:]));checks[name+'.ordering']=ck('pass'if ordered else'fail','Unsorted physical fetch sequence meets only stated ORDER BY; tie order is unrestricted')
   except TypeError:checks[name+'.ordering']=ck('gap','Actual NULL/mixed ordering needs policy decision')
   bad=[]
   for r in rows:
    for col in cols:
     v=r.get(col)
     if col in TEXT or col in COUNTS or col in['first_redemption_date','last_redemption_date']or v is None:continue
     places=1 if col.endswith('_pct')or col in['redemption_rate','discount_to_revenue_ratio']else 2
     if type(v)in[D,int,float]and D(str(v))!=D(str(v)).quantize(D(1).scaleb(-places)):bad.append({'column':col,'actual':str(v),'places':places})
   checks[name+'.precision']=ck('fail'if bad else'pass','Declared monetary2,percentage1,ratio/average2; no global numeric tolerance',errors=bad[:8],error_count=len(bad))
 checks['zero_redemption_policy']=ck('gap'if profile['zero_redemption_valid_promotions']else'pass','Actual master promotions without valid redemptions contrast active output population; undefined0-denominator metrics not invented',promotions=profile['zero_redemption_valid_promotions'])
 checks['policy_scope']=ck('gap','Twelve actual rounding/subtotal/20-percent boundary policies replayed; finite allowed-cell comparison is not whole legal certification',policies=[p['policy']for p in variants]);checks['workflow']=workflow or ck('pending','Trusted actual source/build facts unavailable');states=[c['status']for c in checks.values()];reward=0 if'fail'in states else None if'pending'in states or'gap'in states else 1
 return dict(status='unknown'if reward is None else'scored',reward=reward,checks=checks,model_requests=0,native_rewards_modified=False)
