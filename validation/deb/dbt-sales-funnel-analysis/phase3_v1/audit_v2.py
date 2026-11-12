"""Funnel phase3 audit: exact interface, independently anchored flags and conditional accounting."""
from collections import Counter
from fractions import Fraction as F
from decimal import Decimal as D
import math,re
from audit_tables import TablePolicy,audit_table
from dbt_bench.comparison import Column,TableContract
from expected_v2 import COLS,trim
INTS=('TINYINT','SMALLINT','INTEGER','BIGINT','HUGEINT','UTINYINT','USMALLINT','UINTEGER','UBIGINT','UHUGEINT')
def ck(s,r,**kw):return dict(status=s,reason=r,**kw)
def num(x):return type(x)in[int,float,D]and D(str(x)).is_finite()
def frac(x):return F.from_float(x)if type(x)is float else F(x)
def near(x,p):
 y=F(x)*10**p;q,r=divmod(y.numerator,y.denominator);ns=[q]if 2*r<y.denominator else[q+1]if 2*r>y.denominator else[q,q+1];return [F(n,10**p)for n in ns]
def representation(x,v,places=4):
 if v is None:return 'gap'
 if not num(x):return 'fail'
 a=frac(x);ulp=F.from_float(math.ulp(x))if type(x)is float else F(0);v=F(v)
 if abs(a-v)<=ulp:return 'pass'
 if any(abs(a-n)<=ulp for p in range(places,19)for n in near(v,p)):return 'pass'
 if any(abs(a-n)<=ulp for p in range(places)for n in near(v,p)):return 'gap'
 return 'fail'
def evaluate(outputs,expected,workflow=None):
 checks={};rows={};profile=expected['profile']
 for name,cols in COLS.items():
  o=outputs.get(name,{});t=o.get('table');rs=o.get('rows')
  if o.get('exists')is False:checks[name+'.presence']=ck('fail','Required declared relation absent')
  if o.get('missing_required_columns'):checks[name+'.required_columns']=ck('fail','Required declared columns absent',columns=o['missing_required_columns'])
  if rs is not None:rows[name]=rs
  if t is None:checks[name+'.interface']=ck('pending','Physical SQL schema unavailable');continue
  types={};columns=[]
  for c in cols:
   typ=t.physical_sql_types.get(c,'');text=c in ['session_id','visitor_id','customer_id','funnel_stage','dropoff_stage'];stamp=c=='session_start';ratio=c.endswith('_rate');types[c]=('VARCHAR',)if text else('VARCHAR','TIMESTAMP','TIMESTAMP_NS','TIMESTAMP_MS','TIMESTAMP_S','TIMESTAMP WITH TIME ZONE')if stamp else('FLOAT','DOUBLE',typ)if ratio and re.fullmatch(r'DECIMAL\(\d+,\d+\)',typ)else('FLOAT','DOUBLE')if ratio else INTS;columns.append(Column(c,'text'if text else'exact'if stamp else'numeric'if ratio else'integer'))
  keys=('session_id',)if name=='int_session_funnel'else('dropoff_stage',)if name=='funnel_dropoff_analysis'else();r=audit_table(t,TablePolicy(TableContract(tuple(columns),keys=keys,allow_extra_columns=True),types,relation_types=('TABLE','BASE TABLE','VIEW')));checks[name+'.interface']=ck('pending'if r['status']=='unknown'else'pass'if r['reward']==1 else'fail','Named columns and integer flags/counts; row order and extra columns not imposed',details=r)
 sessions=rows.get('int_session_funnel')
 if sessions is not None:
  want={r[0]:dict(zip(COLS['int_session_funnel'],r))for r in expected['policies']['reviewed_blind']['int_session_funnel']};got={r.get('session_id'):r for r in sessions};checks['session_population']=ck('pass'if len(got)==len(sessions)and set(got)==set(want)else'fail','All original trimmed sessions exactly once')
  for col in ['visitor_id','customer_id','session_start','has_purchase']:
   errors=[{'session_id':sid,'actual':str(r.get(col)),'expected':str(want[sid][col])}for sid,r in got.items()if sid in want and r.get(col)!=want[sid][col]];checks[col]=ck('fail'if errors else'pass','Independent input identity or complete observed boolean/cart purchase signals',errors=errors[:8])
  flags=[r.get('session_id')for r in sessions if any(type(r.get(c))is not int or r[c]not in[0,1]for c in ['has_product_view','has_add_to_cart','has_purchase'])];checks['integer_flags']=ck('fail'if flags else'pass','Required integer zero/one flags',errors=flags[:8]);stages=[]
  for r in sessions:
   wantstage='PURCHASE'if r.get('has_purchase')==1 else'CART'if r.get('has_add_to_cart')==1 else'VIEW'if r.get('has_product_view')==1 else'NONE'
   if r.get('funnel_stage')!=wantstage:stages.append({'session_id':r.get('session_id'),'actual':r.get('funnel_stage'),'expected_from_submitted_flags':wantstage})
  checks['highest_stage']=ck('fail'if stages else'pass','Highest stage from actual submitted flags, independent of event-alias interpretation',errors=stages[:8]);matches=[]
  for policy,data in expected['policies'].items():
   wm={r[0]:dict(zip(COLS['int_session_funnel'],r))for r in data['int_session_funnel']}
   if all(all(r.get(c)==wm[sid][c]for c in ['has_product_view','has_add_to_cart'])for sid,r in got.items()if sid in wm):matches.append(policy)
  checks['event_mapping']=ck('pass'if matches else'fail','Fixed-input blind interpretation independently replayed; original ambiguous alias counts retained, selected policy explicitly reviewed',matched_policies=matches,ambiguous_rows=profile['ambiguous_event_rows'],reviewed_policy=profile['reviewed_policy'])
  counts={'total_sessions':len(sessions),'sessions_with_view':sum(r.get('has_product_view')==1 for r in sessions),'sessions_with_cart':sum(r.get('has_add_to_cart')==1 for r in sessions),'sessions_with_purchase':sum(r.get('has_purchase')==1 for r in sessions)};n=counts['total_sessions'];v=counts['sessions_with_view'];a=counts['sessions_with_cart'];p=counts['sessions_with_purchase']
  summary=rows.get('funnel_conversion_rates')
  if summary is not None:
   checks['summary_grain']=ck('pass'if len(summary)==1 else'fail','Exactly one summary row')
   if len(summary)==1:
    r=summary[0];errors=[c for c,e in counts.items()if r.get(c)!=e];checks['summary_counts']=ck('fail'if errors else'pass','Counts recomputed from actual per-session flags',errors=errors)
    for c,x,y in [('view_rate',v,n),('view_to_cart_rate',a,v),('cart_to_purchase_rate',p,a),('overall_conversion_rate',p,n)]:checks[c]=ck(representation(r.get(c),F(x,y)if y else None),'Exact conditional count ratio, declared precision absent; coarser correctly-rounded output unresolved',numerator=x,denominator=y,actual=str(r.get(c)))
  drops=rows.get('funnel_dropoff_analysis')
  if drops is not None:
   d={'BEFORE_VIEW':sum(r.get('has_product_view')==0 for r in sessions),'VIEW_TO_CART':sum(r.get('has_product_view')==1 and r.get('has_add_to_cart')==0 for r in sessions),'CART_TO_PURCHASE':sum(r.get('has_add_to_cart')==1 and r.get('has_purchase')==0 for r in sessions)};checks['drop_grain']=ck('pass'if len(drops)==3 and {r.get('dropoff_stage')for r in drops}==set(d)else'fail','Exactly three named drop stages');errors=[];rates=[]
   for r in drops:
    k=r.get('dropoff_stage')
    if k in d and r.get('session_count')!=d[k]:errors.append({'stage':k,'actual':r.get('session_count'),'conditional_expected':d[k]})
    if num(r.get('session_count')):rates.append(representation(r.get('dropoff_rate'),F(r['session_count'],n)if type(r['session_count'])is int and n else None))
    else:rates.append('fail')
   checks['drop_counts']=ck('fail'if errors else'pass','Independent stage predicates on submitted session flags',errors=errors);checks['drop_rates']=ck('fail'if'fail'in rates else'gap'if'gap'in rates else'pass','Actual drop count divided by actual total sessions',states=rates)
 if profile['unknown_boolean_tokens']:checks['unknown_boolean']=ck('gap','Observed boolean tokens lack established meanings',tokens=profile['unknown_boolean_tokens'])
 checks['definition_policy']=ck('pass'if profile['reviewed_policy']=='phase3-funnel-blind-v1'else'gap','User-authorized blind-decision plus formally reproduced native agreement policy scope; unseen inputs/other policies are not silently accepted',ambiguous_rows=profile['ambiguous_event_rows']);checks['workflow']=workflow or ck('pending','Trusted source/build execution absent');states=[v['status']for v in checks.values()];reward=0 if'fail'in states else None if'gap'in states or'pending'in states else 1
 return dict(status='unknown'if reward is None else'scored',reward=reward,checks=checks,model_requests=0,native_rewards_modified=False)
