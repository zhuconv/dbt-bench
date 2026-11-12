"""Declared monthly tables plus bounded independent policy alternatives."""
from collections import Counter
from decimal import Decimal as D,ROUND_HALF_UP,ROUND_HALF_DOWN
from datetime import date,datetime
import re
from audit_tables import TablePolicy,audit_table
from dbt_bench.comparison import Column,TableContract
from expected_v3 import derive,STAGING,METRICS,BENCH,MART
COLS={'stg_orders__channel':STAGING,'int_monthly_channel_metrics':METRICS,'int_monthly_market_benchmarks':BENCH,'monthly_channel_performance':MART}
KEYS={'stg_orders__channel':['order_id'],'int_monthly_channel_metrics':['month_start','channel'],'int_monthly_market_benchmarks':['month_start'],'monthly_channel_performance':['month_start','channel']}
TEXT={'order_id','customer_id','order_source','channel','quarter','growth_category','performance_tier','is_top_channel','strategic_recommendation'}
COUNTS={'order_count','unique_customers','total_market_orders','total_market_customers','channel_count','channel_tenure','channel_rank','prev_month_rank','rank_change','consecutive_growth_months','ytd_order_count','prev_month_customers','aov_rank','growth_consistency_score','channel_momentum_score','channel_efficiency_score'}
INTS=('TINYINT','SMALLINT','INTEGER','BIGINT','HUGEINT','UTINYINT','USMALLINT','UINTEGER','UBIGINT','UHUGEINT')
def ck(s,r,**kw):return dict(status=s,reason=r,**kw)
def eq(a,b):
 if isinstance(b,D):return type(a)in[D,int,float]and D(str(a)).is_finite()and D(str(a))==b
 return type(a)is type(b)and a==b

def policies(raw):return[derive(raw,f,r,h)for f in['emerging','null']for r in[ROUND_HALF_UP,ROUND_HALF_DOWN]for h in['observed','calendar']]
def evaluate(outputs,expected,workflow=None):
 checks={};variants=expected['policies'];maps={}
 for name in COLS:
  relation=('main_channel_analytics.'if name=='monthly_channel_performance'else'main.')+name;tables=[]
  for p in variants:
   rs=[dict(r)for r in p['outputs'][relation]]
   for r in rs:
    if isinstance(r.get('month_start'),str):r['month_start']=date.fromisoformat(r['month_start'])
   tables.append(rs)
  maps[name]=tables
 for name,cols in COLS.items():
  o=outputs.get(name,{});rows=o.get('rows');t=o.get('table');mart=name=='monthly_channel_performance'
  if o.get('exists')is False:checks[name+'.presence']=ck('fail','Declared output relation absent')
  if o.get('missing_required_columns'):checks[name+'.columns']=ck('fail','Explicitly listed required columns absent',columns=o['missing_required_columns'])
  if rows is None:checks[name+'.values']=ck('pending','Actual output rows unavailable');continue
  if t is None:checks[name+'.interface']=ck('pending','Physical SQL schema unavailable')
  else:
   types={};cs=[]
   for c in cols:
    typ=t.physical_sql_types.get(c,'');kind='text'if c in TEXT else'integer'if c in COUNTS else'date'if c=='month_start'else'datetime'if c=='ordered_at'else'numeric';cs.append(Column(c,kind));types[c]=('VARCHAR',)if kind=='text'else INTS if kind=='integer'else('DATE',)if kind=='date'else('TIMESTAMP','TIMESTAMP WITH TIME ZONE')if kind=='datetime'else(typ,)if re.fullmatch(r'DECIMAL\(\d+,\d+\)',typ)else('DECIMAL',)
   a=audit_table(t,TablePolicy(TableContract(tuple(cs),keys=tuple(KEYS[name]),allow_extra_columns=not mart),types,relation_types=('TABLE','BASE TABLE')if mart else('VIEW',)));checks[name+'.interface']=ck('pending'if a['status']=='unknown'else'pass'if a['reward']==1 else'fail','Explicit columns and decimal/integer/date families; table mart and view staging/intermediates',details=a)
   if mart:checks['physical_column_order']=ck('pass'if tuple(t.physical_columns)==tuple(MART)else'fail','Exactly40physical mart columns in declared order',actual=list(t.physical_columns),required=MART)
  keys=KEYS[name];want=[{tuple(r[k]for k in keys):r for r in tab}for tab in maps[name]];got={tuple(r.get(k)for k in keys):r for r in rows};counts=Counter(tuple(r.get(k)for k in keys)for r in rows);checks[name+'.population']=ck('pass'if set(want[0])==set(got)and len(got)==len(rows)else'fail','Original eligible source/month-channel membership with duplicate multiplicity',missing=[str(k)for k in want[0].keys()-got.keys()][:8],extra=[str(k)for k in got.keys()-want[0].keys()][:8],duplicates=[str(k)for k,v in counts.items()if v>1][:8])
  for col in cols:
   errors=[];variable=[]
   for k,r in got.items():
    if k not in want[0]:continue
    options=[m[k][col]for m in want];v=r.get(col)
    if not any(eq(v,b)for b in options):errors.append({'key':str(k),'actual':str(v),'independent_options':list(dict.fromkeys(map(str,options)))})
    elif any(not eq(options[0],x)for x in options[1:]):variable.append(str(k))
   checks[name+'.'+col]=ck('fail'if errors else'gap'if variable else'pass','Input-only arrays and arithmetic; rounding/first-month/missing-calendar alternatives remain unadjudicated',mismatch_count=len(errors),mismatches=errors[:8],policy_dependent_cells=len(variable))
  if name!='stg_orders__channel':
   bad=[]
   for r in rows:
    for col,v in r.items():
     if col not in cols or col in TEXT or col in COUNTS or col=='month_start'or v is None:continue
     places=4 if col=='revenue_hhi'else 2
     if type(v)in[D,int,float]and D(str(v))!=D(str(v)).quantize(D(1).scaleb(-places)):bad.append({'column':col,'actual':str(v),'places':places})
   checks[name+'.precision']=ck('fail'if bad else'pass','Explicit2decimal monetary/percentage values and4decimal HHI; no global tolerance',errors=bad[:8],error_count=len(bad))
  if mart:
   seq=[(r.get('month_start'),r.get('channel_rank'))for r in rows]
   try:ordered=all(a<=b for a,b in zip(seq,seq[1:]));checks['ordering']=ck('pass'if ordered else'fail','Unsorted physical read respects month_start ASC then channel_rank ASC; ties unrestricted')
   except TypeError:checks['ordering']=ck('fail','Required date/rank ordering values cannot be compared')
 checks['policy_scope']=ck('gap','Actually executed8policy contrasts: first-month Emerging/NULL clauses, exact halfway rounding, observed versus calendar month windows. Per-cell permissible alternatives do not certify a globally lawful solution.',policies=[p['policy']for p in variants]);checks['workflow']=workflow or ck('pending','Actual source/build evidence unavailable');states=[c['status']for c in checks.values()];reward=0 if'fail'in states else None if'pending'in states or'gap'in states else 1
 return dict(status='unknown'if reward is None else'scored',reward=reward,checks=checks,model_requests=0,native_rewards_modified=False)
