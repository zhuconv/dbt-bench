"""Source-bound ledger audit; identifiers exact, averages retain rational semantics."""
from decimal import Decimal as D
from fractions import Fraction as F
from collections import Counter
import math,re
from audit_tables import TablePolicy,audit_table
from dbt_bench.comparison import Column,TableContract
from expected_v2 import derive,COLS,KEYS
INTS=('TINYINT','SMALLINT','INTEGER','BIGINT','HUGEINT','UTINYINT','USMALLINT','UINTEGER','UBIGINT','UHUGEINT')
TEXT={'transaction_id','customer_id','transaction_type','balance_status','balance_change_direction','balance_trend','final_status','payment_frequency','balance_volatility','streak_risk','risk_category'}
MONEY={'transaction_amount','previous_balance','running_balance','total_order_amount','total_payment_amount','final_balance','max_balance_reached'}
def ck(s,r,**kw):return dict(status=s,reason=r,**kw)
def exact(a,b):
 if a is None or b is None:return a is b
 if isinstance(b,F):
  if type(a)is float:return math.isfinite(a)and abs(F.from_float(a)-b)<=F.from_float(math.ulp(a))
  if isinstance(a,D):return F(a)==b
  return type(a)is int and F(a)==b
 if isinstance(b,D):return type(a)in[D,int,float]and D(str(a)).is_finite()and D(str(a))==b
 return type(a)is type(b)and a==b

def nearest_cent(value, expected):
 if not isinstance(value,D) or not value.is_finite() or not isinstance(expected,F):return False
 scaled=expected*100;lo=scaled.numerator//scaled.denominator;delta=scaled-lo
 options=[F(lo,100)] if delta<F(1,2) else [F(lo+1,100)] if delta>F(1,2) else [F(lo,100),F(lo+1,100)]
 return F(value) in options

def evaluate(outputs,expected,workflow=None):
 checks={};choices={};source=outputs.get('customer_running_balance',{}).get('rows',[])
 for r in source:
  value=r.get('transaction_amount');tid=r.get('transaction_id');opts=expected['source_amount_options'].get(tid,[])
  if type(value)in[D,float,int]and D(str(value))in opts:choices[tid]=D(str(value))
 # Candidate amounts only select between independently established nearest-cent ties.
 # Every source membership and amount is still separately checked below.
 expected=derive(expected['raw_inputs'],choices)
 for name,cols in COLS.items():
  o=outputs.get(name,{});t=o.get('table');rs=o.get('rows')
  if o.get('exists')is False:checks[name+'.presence']=ck('fail','Required relation absent')
  if o.get('missing_required_columns'):checks[name+'.columns']=ck('fail','Required columns absent',columns=o['missing_required_columns'])
  if rs is None:checks[name+'.values']=ck('pending','Actual output rows unavailable');continue
  if t is None:checks[name+'.interface']=ck('pending','Actual SQL schema unavailable')
  else:
   types={};cs=[]
   for c in cols:
    typ=t.physical_sql_types.get(c,'');kind='text'if c in TEXT else'date'if c=='transaction_date'else'numeric'if c in MONEY or c=='avg_days_between_payments'else'integer';cs.append(Column(c,kind));types[c]=('VARCHAR',)if c in TEXT else('DATE',)if c=='transaction_date'else(typ,)if kind=='numeric'and re.fullmatch(r'DECIMAL\(\d+,\d+\)',typ)else('DOUBLE','FLOAT',*INTS)if kind=='numeric'else INTS
   x=audit_table(t,TablePolicy(TableContract(tuple(cs),keys=tuple(KEYS[name]),allow_extra_columns=True),types,relation_types=('TABLE','BASE TABLE','VIEW')));checks[name+'.interface']=ck('pending'if x['status']=='unknown'else'pass'if x['reward']==1 else'fail','Declared identifier/date/integer flag and count types; no invented physical DECIMAL requirement',details=x)
  keys=KEYS[name];want={tuple(r[k]for k in keys):r for r in expected['rows'][name]};got={tuple(r.get(k)for k in keys):r for r in rs};counts=Counter(tuple(r.get(k)for k in keys)for r in rs);checks[name+'.population']=ck('pass'if set(want)==set(got)and len(rs)==len(got)else'fail','Original eligible source IDs/customer grain with multiplicity',missing=[str(k)for k in want.keys()-got.keys()][:8],extra=[str(k)for k in got.keys()-want.keys()][:8],duplicates=[str(k)for k,n in counts.items()if n>1][:8])
  for col in cols:
   errors=[];unresolved=[]
   for k,r in got.items():
    if k not in want:continue
    a=r.get(col);b=want[k][col]
    if col=='avg_days_between_payments' and nearest_cent(a,b):continue
    if not exact(a,b):
     if col=='avg_days_between_payments'and isinstance(a,D)and b is not None and not re.fullmatch(r'DECIMAL\(\d+,2\)',t.physical_sql_types.get(col,'')if t is not None else''):unresolved.append({'key':str(k),'actual':str(a),'exact_rational':str(b)});continue
     errors.append({'key':str(k),'actual':str(a),'expected':str(b)})
   checks[name+'.'+col]=ck('fail'if errors else'gap'if unresolved else'pass','Input-only row replay; exact money and IDs. Rational day average accepts exact arithmetic/floating ULP or independently nearest-cent decimal representation demonstrated by blind reconstruction. Incorrect values in a captured scale-2 DECIMAL are rejected; other unresolved precisions remain gap',mismatch_count=len(errors),mismatches=errors[:8],precision_gaps=unresolved[:8])
  if name=='customer_running_balance':
   errs=[r.get('transaction_id')for r in rs if type(r.get('transaction_amount'))in[D,float,int]and D(str(r['transaction_amount']))!=D(str(r['transaction_amount'])).quantize(D('.01'))];checks['money_precision']=ck('fail'if errs else'pass','Actual monetary amounts must lie exactly on cents',errors=errs[:8])
 checks['output_expectation']=ck('pass'if len(expected['rows']['customer_running_balance'])==3691 else'fail','Explicit source-derived expected transaction count',actual_source_count=len(expected['rows']['customer_running_balance']));checks['workflow']=workflow or ck('pending','Trusted execution facts unavailable');checks['rounding_scope']=ck('gap'if expected['profile']['trim_differences']else'pass','Source amounts permit either mathematically nearest value at exact half-cent ties; consistent downstream arithmetic independently replayed. No source-key normalization ambiguity on current input.',half_cent_cases=len(expected['profile']['money_half_cent_cases']),trim_differences=expected['profile']['trim_differences']);states=[v['status']for v in checks.values()];reward=0 if'fail'in states else None if'pending'in states or'gap'in states else 1
 return dict(status='unknown'if reward is None else'scored',reward=reward,checks=checks,model_requests=0,native_rewards_modified=False)
