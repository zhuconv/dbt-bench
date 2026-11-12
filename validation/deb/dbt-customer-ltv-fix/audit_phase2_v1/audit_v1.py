"""LTV invariants independent of unresolved order eligibility; never promote matching policy to success."""
from decimal import Decimal as D,ROUND_HALF_UP
from datetime import date
import math
from audit_tables import TablePolicy,audit_table
from dbt_bench.comparison import Column,TableContract
from expected_v1 import COLS,tier
INTS=('TINYINT','SMALLINT','INTEGER','BIGINT','HUGEINT','UTINYINT','USMALLINT','UINTEGER','UBIGINT','UHUGEINT')
NUMERIC=INTS+('FLOAT','DOUBLE','REAL')+tuple(f'DECIMAL({p},{s})'for p in range(1,39)for s in range(p+1))
def check(status,reason,**kw):return dict(status=status,reason=reason,**kw)
def number(v):return type(v)in(int,float,D)and D(str(v)).is_finite()
def decimal(v):return D(str(v))
def cent(v):
 if not number(v):return False
 d=decimal(v);q=d.quantize(D('.01'),rounding=ROUND_HALF_UP)
 return d==q or type(v)is float and abs(D.from_float(v)-q)<=D.from_float(math.ulp(float(q)))
def evaluate(o,expected,workflow=None):
 checks={};rows=o.get('rows');cols=tuple(Column(c,'text'if c in ['customer_id','value_segment']else'date'if c.endswith('_date')else'numeric')for c in COLS);types={c:('VARCHAR',)if c in ['customer_id','value_segment']else('DATE',)if c.endswith('_date')else NUMERIC for c in COLS};policy=TablePolicy(TableContract(cols,keys=('customer_id',),allow_extra_columns=True),types,not_null=('customer_id','total_orders','lifetime_value','avg_order_value','value_segment'),relation_types=('BASE TABLE',))
 if o.get('exists')is False:checks['presence']=check('fail','Actual required main.customer_ltv absent')
 if o.get('table')is not None:
  v=audit_table(o['table'],policy);checks['interface']=check('pending'if v['status']=='unknown'else'pass'if v['reward']==1 else'fail','Required fields, logical type families, unique customer grain and physical TABLE',details=v)
 else:
  checks['interface']=check('pending','Exact physical schema capture unavailable')
  if o.get('relation_type')is not None:checks['table_materialization']=check('pass'if o['relation_type']=='BASE TABLE'else'fail','Trusted actual relation kind')
 if rows is None:checks['values']=check('pending','Actual output rows not captured')
 else:
  bounds=expected['bounds'];got={};errors={k:[]for k in ['customer_coverage','aggregate_bounds','dates','segments','zero_defaults','numeric_precision']}
  for r in rows:
   cid=r.get('customer_id')
   if type(cid)is not str or cid in got:errors['customer_coverage'].append([str(cid),'invalid or duplicate customer'])
   got[cid]=r
   if cid not in bounds:continue
   b=bounds[cid];count=r.get('total_orders');v=r.get('lifetime_value');avg=r.get('avg_order_value')
   if not(number(count)and decimal(count)==decimal(count).to_integral_value()and 0<=decimal(count)<=b['all_raw_order_count']):errors['aggregate_bounds'].append([cid,'order count outside all-raw-order bounds'])
   for field,value,upper in [('lifetime_value',v,b['absolute_sum_upper']),('avg_order_value',avg,b['absolute_average_upper'])]:
    if not number(value)or abs(decimal(value))>upper+D('.005'):errors['aggregate_bounds'].append([cid,field,'exceeds every possible raw-order eligibility subset'])
    if not cent(value):errors['numeric_precision'].append([cid,field,'not finite nearest-cent numeric representation'])
   if number(v)and decimal(v)>=0 and r.get('value_segment')!=tier(decimal(v)):errors['segments'].append([cid,'tier contradicts actual reported lifetime_value'])
   for field in ['first_order_date','last_order_date']:
    if r.get(field)is not None and(type(r[field])is not date or r[field]not in b['all_raw_dates']):errors['dates'].append([cid,field,'date absent from every raw order for this customer'])
   if all(type(r.get(c))is date for c in ['first_order_date','last_order_date'])and r['first_order_date']>r['last_order_date']:errors['dates'].append([cid,'first after last'])
   if b['no_raw_orders']and(count!=0 or v!=0 or avg!=0 or r.get('value_segment')!='No Value'):errors['zero_defaults'].append([cid,'raw customer has no orders of any status'])
  if set(got)!=set(bounds):errors['customer_coverage'].append(['membership','missing or fabricated customer IDs'])
  for k,e in errors.items():checks[k]=check('fail'if e else'pass','Eligibility-independent '+k.replace('_',' ')+' check',error_count=len(e),examples=e[:8])
  diagnostics={}
  for policy,rs in expected['policies'].items():
   diagnostics[policy]=sum(any(r.get(c)!=got.get(r['customer_id'],{}).get(c)for c in COLS)for r in rs)
  checks['eligibility_semantics']=check('gap','Actual revenue eligibility remains unspecified for129 included UNPAID and75 nonproduction-flag orders; matching one candidate policy does not establish full correctness',facts=expected['facts'],policy_mismatch_customer_counts=diagnostics)
 checks['workflow']=workflow or check('pending','Trusted source/profile/build/staging/backend history unavailable');states=[c['status']for c in checks.values()];reward=0 if'fail'in states else None if any(s in states for s in ['pending','gap'])else 1
 return dict(status='unknown'if reward is None else'scored',reward=reward,checks=checks,model_requests=0,native_rewards_modified=False)
