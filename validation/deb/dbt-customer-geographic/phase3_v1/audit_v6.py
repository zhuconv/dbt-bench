"""Supplementary audit over independently derived input and trusted capture facts.
SQL source hashes only bind provenance; they never define legal implementation.
"""
from decimal import Decimal,localcontext,ROUND_HALF_UP,ROUND_HALF_DOWN
import math
from collections import Counter
from audit_tables import TablePolicy,audit_table,CapturedTable,CaptureError
from dbt_bench.comparison import Column,TableContract,compare_tables
NUMERIC=('TINYINT','SMALLINT','INTEGER','BIGINT','HUGEINT','UTINYINT','USMALLINT','UINTEGER','UBIGINT','UHUGEINT','FLOAT','DOUBLE','REAL')+tuple(f'DECIMAL({p},{s})'for p in range(1,39)for s in range(p+1))
def check(status,reason,**kwargs):return {'status':status,'reason':reason,**kwargs}
def configuration(kind):
 if kind=='geographic':
  names={'stg_customer_addresses':(('address_id','text'),('customer_id','text'),('state_province','text')),'stg_orders':(('order_id','text'),('customer_id','text'),('grand_total','numeric')),'fct_state_customers':(('state_province','text'),('customer_count','numeric'),('order_count','numeric'),('total_revenue','numeric'),('avg_order_value','numeric'),('revenue_per_customer','numeric'),('orders_per_customer','numeric'))};keys={'stg_customer_addresses':'address_id','stg_orders':'order_id','fct_state_customers':'state_province'};return names,keys,'fct_state_customers'
 return {'customer_risk_scores':(('customer_id','text'),('customer_name','text'),('late_payment_count','numeric'),('return_count','numeric'),('chargeback_count','numeric'),('risk_score','numeric'),('risk_tier','text'))},{'customer_risk_scores':'customer_id'},'customer_risk_scores'
def contract(kind,name):
 names,keys,_=configuration(kind)
 return TableContract(tuple(Column(n,t,abs_tol=Decimal(0))for n,t in names[name]),keys=(keys[name],),allow_extra_columns=True)
def ratio_precision(rows,expected,table=None):
 """Accept correctly rounded decimal scales >=2 or native binary representation.
 No arbitrary +/-0.005 neighborhood is accepted. The0.005 bound follows only
 from nearest-cent rounding; float representation adds at most half one ULP.
 """
 wanted={r['state_province']:r['orders_per_customer']for r in expected};unresolved=[];failures=[]
 for row in rows:
  state=row.get('state_province');v=row.get('orders_per_customer')
  if state not in wanted or type(v)not in(int,float,Decimal):failures.append(str(state));continue
  number=Decimal(str(v));exact=wanted[state]
  if not number.is_finite():failures.append(str(state));continue
  with localcontext() as ctx:
   ctx.prec=80;targets=[exact]+[exact.quantize(Decimal(1).scaleb(-scale),rounding=mode)for scale in range(2,39)for mode in (ROUND_HALF_UP,ROUND_HALF_DOWN)]
  if number in targets:continue
  sqltype=table.sql_types.get('orders_per_customer')if table else None
  if type(v)is float:
   # The captured SQL type chooses representation error, never a global float cast.
   if sqltype in ('DOUBLE','FLOAT','REAL'):
    if sqltype in ('FLOAT','REAL'):
     ulp=2.0**max(-149,math.frexp(abs(v))[1]-24)if v else 2.0**-149
    else:ulp=math.ulp(v)
    half=Decimal.from_float(ulp)/2;actual=Decimal.from_float(v)
    if any(abs(actual-t)<=half for t in targets):continue
   elif sqltype is None and abs(number-exact)<=Decimal('.005'):
    unresolved.append(str(state));continue
  failures.append(str(state))
 return check('fail'if failures else'pending'if unresolved else'pass','Ratio equals exact quotient or correctly nearest-rounded decimal at scale>=2 (either nearest neighbor at exact ties), allowing only half-ULP representation error for explicitly captured SQL FLOAT/DOUBLE. No arbitrary half-cent error is ignored.',failed_states=failures,missing_type_states=unresolved,nearest_cent_error_bound='0.005; more decimal places have bound0.5*10^-scale')

def evaluate(kind,captured,expected,*,structured=None,workflow=None,repeated=None,relation_types=None):
 names,keys,final=configuration(kind);checks={};structured=structured or {}
 # DuckDB identifiers are case-insensitive; preserve values/types, normalize only unique column labels.
 def labels(row):
  out={str(k).lower():v for k,v in row.items()}
  if len(out)!=len(row):raise CaptureError('Ambiguous case-insensitive column labels')
  return out
 captured={n:[labels(r)for r in rows]for n,rows in captured.items()}
 if repeated is not None:repeated={n:[labels(r)for r in rows]for n,rows in repeated.items()}
 structured={n:CapturedTable(tuple(c.lower()for c in t.columns),labels(t.sql_types),[labels(r)for r in t.rows],tuple(c.lower()for c in t.physical_columns),labels(t.physical_sql_types),t.relation_type,t.capture_ref)for n,t in structured.items()}
 for name in names:
  if name not in captured:
   checks[name+'_values']=check('pending','Required logical output capture unavailable');continue
  value_contract=contract(kind,name)
  if kind=='geographic'and name==final:value_contract=TableContract(tuple(c for c in value_contract.columns if c.name!='orders_per_customer'),keys=value_contract.keys,allow_extra_columns=True)
  cmp=compare_tables(expected[name],captured[name],value_contract);checks[name+'_values']=check('pass'if cmp.equal else'fail',cmp.reason)
  if name in structured:
   ntypes={n:('VARCHAR',)if t=='text'else NUMERIC for n,t in names[name]};notnull=tuple(ntypes)if name==final else();types=('BASE TABLE','TABLE')if kind=='geographic'and name==final else();policy=TablePolicy(contract(kind,name),ntypes,not_null=notnull,relation_types=types);s=audit_table(structured[name],policy);checks[name+'_structure']=check('pending'if s['status']=='unknown'else'pass'if s['reward']==1 else'fail',s['reason'],details=s)
  else:checks[name+'_structure']=check('pending','Exact SQL metadata was not captured; DB-API STRING/NUMBER is not substituted')
 if kind=='geographic':
  typ=(relation_types or {}).get(final) or (structured[final].relation_type if final in structured else None)
  checks['final_materialization']=check('pending'if typ is None else'pass'if typ in ('TABLE','BASE TABLE')else'fail','Actual relation-type capture requires final TABLE; missing column types are not invented',observed_type=typ)
 if final in captured:
  counts=('customer_count','order_count')if kind=='geographic'else('late_payment_count','return_count','chargeback_count','risk_score');monetary=('total_revenue','avg_order_value','revenue_per_customer')if kind=='geographic'else();ok=True
  if kind=='geographic':checks['orders_per_customer_precision']=ratio_precision(captured[final],expected[final],structured.get(final))
  for row in captured[final]:
   # The task explicitly forbids NULL in any final output column, including extras.
   if any(v is None for v in row.values()):ok=False
   for n,t in names[final]:
    v=row.get(n)
    if v is None:ok=False;continue
    if t=='numeric':
     if type(v)not in(int,float,Decimal):ok=False;continue
     v=Decimal(str(v));ok &= v.is_finite()and v>=0
     if not v.is_finite():continue
     if n in counts:ok &= v==v.to_integral_value()
     if n in monetary:
      parts=v.as_tuple();ok &= parts.exponent>=-2 or not any(parts.digits[-min(len(parts.digits),-2-parts.exponent):])
  checks['required_domains']=check('pass'if ok else'fail','Final required fields non-NULL, finite/nonnegative numeric, integral counts/score; monetary values have cent precision')
 else:checks['required_domains']=check('pending','Final logical output not captured')
 if repeated is not None and final in captured and final in repeated:
  cmp=compare_tables(captured[final],repeated[final],TableContract(tuple(Column(c.name,c.kind)for c in contract(kind,final).columns),keys=contract(kind,final).keys,allow_extra_columns=True));checks['repeat']=check('pass'if cmp.equal else'fail','Actual repeated-build retained capture comparison: '+cmp.reason)
 else:checks['repeat']=check('pending','Actual repeated-build capture not supplied')
 checks['workflow']=check('pending','Trusted fresh execution/project/profile/models/source capture not supplied')
 if workflow is not None:
  # These are trusted runner-computed facts, never candidate self-reported flags.
  facts=workflow.get('checks',{});required={'backend_before_code','fresh_project','profile','model_files','source_definitions','trusted_build'};required |= {'trimmed_staging'}if kind=='risk'else set();states=[facts.get(k,{'status':'pending'})['status']for k in required];status='fail'if 'fail'in states else'pending'if not states or'pending'in states else'pass';checks['workflow']=check(status,'Trusted workflow fact checks supplied separately',details=facts)
 states=[v['status']for v in checks.values()];reward=0.0 if 'fail'in states else None if 'pending'in states else 1.0
 return {'status':'scored'if reward is not None else'unknown','reward':reward,'checks':checks,'native_rewards_modified':False,'model_requests':0,'scope':'Independent original-input expectations and trusted capture facts; missing metadata/workflow remains pending even when table values match.'}
