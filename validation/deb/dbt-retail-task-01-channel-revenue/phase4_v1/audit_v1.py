"""Finite declared channel join and exact monetary arithmetic; unmatched-source policy is unresolved."""
from collections import Counter
from dataclasses import replace
from decimal import Decimal
from datetime import date,datetime,timezone,time
import re
from audit_tables import audit_table,TablePolicy
from dbt_bench.comparison import Column,TableContract,compare_tables
from expected_v1 import KEYS,MEASURES,COLUMNS,MONEY,RATIOS
NAME='ts_sales__channel_revenue_margin_monthly';COLS={NAME:COLUMNS}
def ck(s,r,**kw):return dict(status=s,reason=r,**kw)
def day(v):return date.fromisoformat(v)if isinstance(v,str)else v.date()if isinstance(v,datetime)and v.time()==time()else v

def evaluate(outputs,expected,workflow=None,timestamp_window=None):
 rows_expected,stats=expected;o=outputs.get(NAME,{});rows=o.get('rows');t=o.get('table');checks={};want=[]
 for row in rows_expected:
  r=dict(row);r['month_start']=day(r['month_start'])
  for col in MONEY|RATIOS:r[col]=Decimal(r[col])if r[col]is not None else None
  want.append(r)
 if o.get('exists')is False:checks['presence']=ck('fail','Declared target absent')
 if o.get('missing_required_columns'):checks['columns']=ck('fail','Declared fields absent',missing=o['missing_required_columns'])
 if t is None:checks['interface']=ck('pending','Complete physical interface unavailable')
 else:
  types={};cols=[]
  for c in COLUMNS:
   typ=t.physical_sql_types.get(c,'');kind='date'if c=='month_start'else'datetime'if c=='dbt_updated_at'else'integer'if c in ['order_count','sales_year','sales_month']else'numeric'if c in MONEY|RATIOS else'exact';cols.append(Column(c,kind));types[c]=('DATE','TIMESTAMP')if c=='month_start'else('TIMESTAMP','TIMESTAMP WITH TIME ZONE')if c=='dbt_updated_at'else('TINYINT','SMALLINT','INTEGER','BIGINT','HUGEINT')if kind=='integer'else(typ,)if kind!='numeric'or re.fullmatch(r'DECIMAL\(\d+,\d+\)|DOUBLE|FLOAT|REAL|INTEGER|BIGINT|HUGEINT',typ)else('DECIMAL','DOUBLE')
  normal=[r|{'month_start':day(r['month_start'])}for r in t.rows];z=audit_table(replace(t,rows=normal),TablePolicy(TableContract(tuple(cols),allow_extra_columns=True),types,relation_types=('VIEW',)));checks['interface']=ck('fail'if any(v is False for v in z.get('checks',{}).values())else'pass'if z['reward']==1 else'pending','Instruction-required16columns/view; unspecified monetary SQLwidth not imposed',details=z)
 if rows is None:checks['values']=ck('pending','Complete actual output unavailable')
 else:
  rs=[r|{'month_start':day(r.get('month_start'))}for r in rows];key=lambda r:tuple((type(r.get(c)).__name__,r.get(c))for c in KEYS);keys=Counter(key(r)for r in rs);desired=Counter(key(r)for r in want);checks['population']=ck('pass'if keys==desired else'fail','Literal declared source-join monthly/channel/attribute grain and multiplicity; nullable unmatched attributes retained',actual_rows=len(rs),expected_rows=len(want),duplicate_groups=sum(n>1 for n in keys.values()))
  for col in MEASURES:
   cs=[Column(c,'exact')for c in KEYS]+[Column(col,'integer'if col=='order_count'else'numeric',abs_tol=Decimal('1e-9')if col in RATIOS else Decimal(0),rel_tol=Decimal('1e-12')if col in RATIOS else Decimal(0))];z=compare_tables(want,rs,TableContract(tuple(cs),allow_extra_columns=True));checks[col]=ck('pass'if z.equal else'fail','Independent raw join: exact counts/money and finite Decimal-derived ratio storage tolerance; no arbitrary semantic rounding',comparison=z.__dict__)
  if timestamp_window:
   lo=datetime.fromisoformat(timestamp_window['start'].replace('Z','+00:00'));hi=datetime.fromisoformat(timestamp_window['end'].replace('Z','+00:00'));bad=[]
   for i,r in enumerate(rs):
    v=r.get('dbt_updated_at');v=datetime.fromisoformat(v)if isinstance(v,str)else v
    if not isinstance(v,datetime):bad.append({'row':i,'value':str(v)});continue
    if v.tzinfo is None:v=v.replace(tzinfo=timezone.utc)
    if not lo<=v<=hi:bad.append({'row':i,'value':str(v)})
   checks['current_timestamp']=ck('fail'if bad else'pass','Every actual dbt_updated_at must lie in its capture SELECT interval; dynamic value never deleted without check',window=timestamp_window,error_count=len(bad),examples=bad[:4])
  else:checks['current_timestamp']=ck('pending','Actual SELECT timestamp interval unavailable')
 checks['policy_scope']=ck('pass','Reviewed blind fixed-input policy: group fact channel key, LEFT JOIN required channel dimension with NULL attributes, INNER JOIN dates. Independently profiled missing channels/dates remain explicit; no invented lookup or source repair.',actual_stats=stats);checks['workflow']=workflow or ck('pending','Actual workflow not yet bound');ss=[v['status']for v in checks.values()];reward=0 if'fail'in ss else None if any(s in ['pending','gap']for s in ss)else 1
 return dict(status='unknown'if reward is None else'scored',reward=reward,checks=checks,model_requests=0,native_rewards_modified=False,scope='Finite literal source-join audit; unresolved raw dimension/domain policy prevents complete valid certification.')
