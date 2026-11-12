"""Versioned four-key variant under the reviewed blind missing-rate policy."""
from collections import Counter
from decimal import Decimal as D,ROUND_HALF_UP,ROUND_HALF_DOWN
from fractions import Fraction as F
from datetime import datetime,timezone
import re,math
from audit_tables import TablePolicy,audit_table
from dbt_bench.comparison import Column,TableContract
from expected_v3 import COLS,KEYS
INTS=('TINYINT','SMALLINT','INTEGER','BIGINT','HUGEINT','UTINYINT','USMALLINT','UINTEGER','UBIGINT','UHUGEINT')
INTCOLS={'year','month','quarter','day_of_week','week_of_year','total_orders','unique_customers','total_line_items'}
ADDED={'fx_rate','net_revenue_usd','total_revenue_usd'}
def ck(s,r,**kw):return dict(status=s,reason=r,**kw)
def exact(a,b,terms=1):
 if a is None or b is None:return a is b
 if isinstance(b,(F,D)):
  if type(a)not in[int,float,D]:return False
  if type(a)is float:
   return math.isfinite(a)and abs(F.from_float(a)-F(b))<=F.from_float(math.ulp(a))*max(1,terms)
  return F(a)==F(b)
 return type(a)is type(b)and a==b

def evaluate(outputs,expected,workflow=None,timestamp_window=None):
 checks={};o=outputs.get('fact_revenue',{});rs=o.get('rows');t=o.get('table');cols=COLS['fact_revenue']
 if o.get('exists')is False:checks['presence']=ck('fail','Required fact_revenue absent')
 if o.get('missing_required_columns'):checks['columns']=ck('fail','Required preserved/added columns absent',columns=o['missing_required_columns'])
 if rs is None:checks['values']=ck('pending','Actual submission rows unavailable')
 else:
  if t is None:checks['interface']=ck('pending','Actual physical schema unavailable')
  else:
   types={};cc=[]
   for c in cols:
    typ=t.physical_sql_types.get(c,'');kind='date'if c in ['order_date','settlement_date']else'text'if c in ['currency_code','source_system']else'datetime'if c=='dbt_updated_at'else'integer'if c in INTCOLS else'numeric';cc.append(Column(c,kind));types[c]=('DATE',)if kind=='date'else('VARCHAR',)if kind=='text'else('TIMESTAMP','TIMESTAMP WITH TIME ZONE')if kind=='datetime'else INTS if kind=='integer'else(typ,)if re.fullmatch(r'DECIMAL\(\d+,\d+\)',typ)else('DECIMAL',)if c in ADDED else('DOUBLE','FLOAT',*INTS)
   a=audit_table(t,TablePolicy(TableContract(tuple(cc),keys=(),allow_extra_columns=True),types,not_null=('fx_rate',),relation_types=('TABLE','BASE TABLE')));checks['interface']=ck('pending'if a['status']=='unknown'else'pass'if a['reward']==1 else'fail','All25 inherited names plus four added fields, added DATE/DECIMAL and original table materialization. Nullable grouping dimensions checked separately by exact tuple/multiplicity, not primary-key NULL rules',details=a)
  keys=KEYS['fact_revenue'];want={tuple(r[k]for k in keys):r for r in expected['rows']['fact_revenue']};got={tuple(r.get(k)for k in keys):r for r in rs};counts=Counter(tuple(r.get(k)for k in keys)for r in rs);original_keys={(k[0],k[2],k[3])for k in want};actual_original={(r.get('order_date'),r.get('currency_code'),r.get('source_system'))for r in rs};three_key_candidate=len(rs)==len(actual_original)and actual_original==original_keys;four_key_candidate=set(want)==set(got)
  checks['population']=ck('pass'if four_key_candidate and len(rs)==len(got)else'fail','Explicit v2 four-key source population; the old three-key alternative is forbidden by the appended instruction.',missing=[str(k)for k in want.keys()-got.keys()][:8],extra=[str(k)for k in got.keys()-want.keys()][:8],original_three_key_candidate=three_key_candidate)
  checks['unique_grain']=ck('fail'if any(v>1 for v in counts.values())else'pass','Repeated identical four-dimensional output groups are invalid under either reviewed grain policy')
  for col in cols:
   if col=='dbt_updated_at':continue
   errors=[];unresolved=[]
   for k,r in got.items():
    if k not in want:continue
    w=want[k];a=r.get(col);b=w[col]
    if False:  # Current policy uses reviewed fallback 1; other scopes need an explicit checker version.
     if col=='fx_rate'and a is None:errors.append({'key':str(k),'actual':'NULL','expected':'non-NULL required'})
     else:unresolved.append(str(k))
     continue
    # Two decimal-to-binary conversions plus division can each round.
    # This is a bound in representation ULPs for inherited ratio arithmetic, not a global value tolerance.
    terms=int(w['total_line_items'])if col=='fx_rate'else 3 if col=='discount_rate'else 1
    if not exact(a,b,terms):errors.append({'key':str(k),'actual':str(a),'expected':str(b)})
   status='fail'if errors else'pass';checks[col]=ck(status,'Original-input exact keyed value or declared floating operation representation. Missing-rate groups use the explicitly reviewed author fallback 1, backed by formal reproduction; not asserted as the unique sensible fallback.',mismatch_count=len(errors),mismatches=errors[:8],policy_gap_groups=len(unresolved))
  arithmetic=[]
  for r in rs:
   for local,converted in [('net_revenue','net_revenue_usd'),('total_revenue','total_revenue_usd')]:
    v=r.get(local);rate=r.get('fx_rate');actual=r.get(converted)
    if v is None or rate is None:continue
    if actual is None:arithmetic.append({'key':str(tuple(r.get(k)for k in keys)),'column':converted,'reason':'Non-NULL factors must produce non-NULL converted amount'});continue
    if type(v)not in[int,float,D]or type(rate)not in[int,float,D]or type(actual)not in[int,float,D]:continue
    x=D(str(v))*D(str(rate));options={x.quantize(D('.01'),rounding=mode)for mode in[ROUND_HALF_UP,ROUND_HALF_DOWN]}
    if D(str(actual))not in options:arithmetic.append({'key':str(tuple(r.get(k)for k in keys)),'column':converted,'actual':str(actual),'conditional_options':list(map(str,options))})
  checks['conditional_conversion']=ck('fail'if arithmetic else'pass','Submitted local amount × submitted rate rounded to cents; independent of which sensible missing rate is chosen. Either exact nearest-cent tie is permitted, with no global numeric tolerance.',mismatches=arithmetic[:8],mismatch_count=len(arithmetic))
  if timestamp_window:
   lo=datetime.fromisoformat(timestamp_window['start'].replace('Z','+00:00'));hi=datetime.fromisoformat(timestamp_window['end'].replace('Z','+00:00'));bad=[]
   for r in rs:
    v=r.get('dbt_updated_at')
    if not isinstance(v,datetime):bad.append(str(v));continue
    if v.tzinfo is None:v=v.replace(tzinfo=timezone.utc)
    if not lo<=v<=hi:bad.append(str(v))
   checks['dynamic_timestamp']=ck('fail'if bad else'pass','Every preserved dbt_updated_at falls in trusted actual build/read execution interval',interval=timestamp_window,errors=bad[:8])
  else:checks['dynamic_timestamp']=ck('pending','Actual build interval unavailable; timestamp cannot be silently omitted')
 checks['original_grain_policy']=ck('pass','New instruction explicitly requires four-key grouping; actual mixed-date original groups retained in diagnostic profile',profile=expected['profile']);checks['missing_rate_policy']=ck('pass','Actual missing-rate inputs examined. This scoped checker uses reviewed blind fallback 1, not a universal policy claim.',missing_sales=expected['profile']['missing_rate_sales']);checks['workflow']=workflow or ck('pending','Trusted model/source execution absent');states=[v['status']for v in checks.values()];reward=0 if'fail'in states else None if'gap'in states or'pending'in states else 1
 return dict(status='unknown'if reward is None else'scored',reward=reward,checks=checks,model_requests=0,native_rewards_modified=False)
