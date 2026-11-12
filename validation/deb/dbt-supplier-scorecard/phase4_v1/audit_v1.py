"""Supplier scorecard audit with exact rational values and disclosed display boundaries."""
from decimal import Decimal as D
from fractions import Fraction as F
from collections import defaultdict
import math,re
from audit_tables import TablePolicy,audit_table
from dbt_bench.comparison import Column,TableContract
from expected_v1 import METRICS,SCORES
M='fct_supplier_metrics';S='fct_supplier_scorecard';TEXT={'supplier_id','supplier_code','supplier_name','supplier_type','performance_tier'}
def ck(s,r,**kw):return dict(status=s,reason=r,**kw)
def finite(x):return type(x)in(int,float,D)and D(str(x)).is_finite()
def frac(x):return F(D(str(x)))
def nearest(x,p):
 y=F(x)*10**p;q,r=divmod(y.numerator,y.denominator);return {F(n,10**p)for n in ([q]if 2*r<y.denominator else[q+1]if 2*r>y.denominator else[q,q+1])}
def represent(a,values,places):
 if a is None:return 'pass'if None in values else'fail'
 if not finite(a):return 'fail'
 af=F.from_float(a)if type(a)is float else frac(a);ulp=F.from_float(math.ulp(a))*4 if type(a)is float else F(0)
 values=[F(v)for v in values if v is not None]
 if any(abs(af-v)<=ulp for v in values):return 'pass'
 for p in range(places,19):
  if any(abs(af-n)<=ulp for v in values for n in nearest(v,p)):return 'pass'
 if any(abs(af-n)<=ulp for p in range(0,places)for v in values for n in nearest(v,p)):return 'gap'
 return 'fail'
def exact(a,b):return a==b if isinstance(b,str)or b is None else finite(a)and frac(a)==F(b)
def tier(v):return 'PLATINUM'if v>=90 else'GOLD'if v>=75 else'SILVER'if v>=60 else'BRONZE'if v>=40 else'AT_RISK'
def evaluate(outputs,expected,workflow=None):
 checks={};maps={};raw={n:{r[0]:dict(zip(v['columns'],r))for r in v['rows']}for n,v in expected['relations'].items()};fq={sid:{c:F(v)if v is not None else None for c,v in vals.items()}for sid,vals in expected['exact_fractions'].items()}
 for name,cols in [(M,METRICS),(S,SCORES)]:
  o=outputs.get(name,{});rows=o.get('rows');t=o.get('table')
  if o.get('missing_required_columns'):checks[name+'.required_columns']=ck('fail','Actual trusted declared-column read reports required columns absent',columns=o['missing_required_columns'])
  if o.get('exists')is False:checks[name+'.presence']=ck('fail','Required actual mart absent')
  if t is not None:
   types={}
   for c in cols:
    actual=t.physical_sql_types.get(c,'');types[c]=('VARCHAR',)if c in TEXT else('TINYINT','SMALLINT','INTEGER','BIGINT','HUGEINT','UTINYINT','USMALLINT','UINTEGER','UBIGINT','UHUGEINT','DOUBLE','FLOAT')+((actual,)if re.fullmatch(r'DECIMAL\(\d+,\d+\)',actual)else())
   policy=TablePolicy(TableContract(tuple(Column(c,'text'if c in TEXT else'numeric')for c in cols),keys=('supplier_id',),allow_extra_columns=True),types,not_null=('supplier_id',),relation_types=('TABLE','BASE TABLE','VIEW'));r=audit_table(t,policy);checks[name+'.interface']=ck('pending'if r['status']=='unknown'else'pass'if r['reward']==1 else'fail','Declared mart names and supplier grain; numeric SQL representations remain explicit, not universally coerced tofloat',details=r)
  else:checks[name+'.interface']=ck('pending','Exact mart SQL capture unavailable')
  if rows is None:checks[name+'.values']=ck('pending','Mart rows unavailable');continue
  got={r.get('supplier_id'):r for r in rows};maps[name]=got;want=raw[name];checks[name+'.population']=ck('pass'if len(got)==len(rows)and set(got)==set(want)else'fail','All eligible ACTIVE suppliers once',missing=sorted(map(str,set(want)-set(got))),extra=sorted(map(str,set(got)-set(want))));errs=defaultdict(list);gaps=defaultdict(list)
  def test(c,a,values,places=4):
   state=represent(a,values,places)
   if state=='fail':errs[c].append({'supplier_id':sid,'actual':str(a),'expected_exact':[str(v)for v in values]})
   elif state=='gap':gaps[c].append({'supplier_id':sid,'actual':str(a),'exact':[str(v)for v in values],'reason':'Actually nearest-rounded but coarser than retained precision; task has no explicit precision boundary.'})
  for sid in got.keys()&want.keys():
   row=got[sid];exp=want[sid]
   for c in cols:
    if c in TEXT and c!='performance_tier'and row.get(c)!=exp[c]:errs[c].append({'supplier_id':sid,'actual':str(row.get(c)),'expected':str(exp[c])})
   if name==M:
    for c in ['total_pos','on_time_pos','total_quantity_received','total_quantity_accepted','total_quantity_rejected']:
     if not exact(row.get(c),exp[c]):errs[c].append({'supplier_id':sid,'actual':str(row.get(c)),'expected':str(exp[c])})
    for c,v in fq[sid].items():test(c,row.get(c),[v]if v is not None else[None,F(0)],2 if c=='avg_days_late'or c.endswith('_value')or c=='overall_cost_variance'else 4)
   else:
    dr=fq[sid]['on_time_delivery_rate'];ar=fq[sid]['overall_acceptance_rate'];vr=fq[sid]['overall_cost_variance_pct'];pairs=[('on_time_delivery_rate',dr),('acceptance_rate',ar),('cost_variance_pct',vr)]
    for c,v in pairs:test(c,row.get(c),[v]if v is not None else[None,F(0)])
    for col,rate,rawrate in [('on_time_score','on_time_delivery_rate',dr),('quality_score','acceptance_rate',ar)]:
     vals=[rawrate*100]if rawrate is not None else[None,F(0)]
     if finite(row.get(rate)):vals.append(frac(row[rate])*100)
     test(col,row.get(col),vals,2)
    clamp=lambda r:max(F(0),min(F(100),(F(1,10)-r)/F(15,100)*100));costvals=[clamp(vr)]if vr is not None else[None,clamp(F(0))]
    if finite(row.get('cost_variance_pct')):costvals.append(clamp(frac(row['cost_variance_pct'])))
    test('cost_score',row.get('cost_score'),costvals,2)
    components=[row.get(c)for c in ['on_time_score','quality_score','cost_score']]
    if all(finite(v)for v in components):test('composite_score',row.get('composite_score'),[frac(components[0])*F(40,100)+frac(components[1])*F(35,100)+frac(components[2])*F(25,100)],2)
    elif any(v is None for v in components):
     if row.get('composite_score')is not None:gaps['composite_score'].append({'supplier_id':sid,'reason':'Explicit no-cost NULL component fallback not uniquely defined; actual candidate needs policy review.'})
    else:errs['composite_score'].append({'supplier_id':sid,'reason':'Nonfinite component'})
    if finite(row.get('composite_score')):
     if row.get('performance_tier')!=tier(frac(row['composite_score'])):errs['performance_tier'].append({'supplier_id':sid,'actual':row.get('performance_tier'),'expected':tier(frac(row['composite_score']))})
    else:gaps['performance_tier'].append({'supplier_id':sid,'reason':'Tier for actual NULL composite not specified.'})
  for c in cols:checks[name+'.'+c]=ck('fail'if errs[c]else'gap'if gaps[c]else'pass','Independent raw/grouped formula for '+c,errors=errs[c][:6],unresolved=gaps[c][:6])
 # Explicit per-PO eligibility in the named delivery/quality/cost metric models, distinct from unrequested staging-column shape.
 for name in ['int_po_delivery_performance','int_po_quality_performance','int_po_cost_performance']:
  rows=outputs.get(name,{}).get('rows')
  if rows is None or any('po_id'not in r for r in rows):checks[name+'.eligibility']=ck('pending','PO membership capture unavailable; alternative internal representation needs lineage review');continue
  keys=[r['po_id']for r in rows];want=set(raw[name]);checks[name+'.eligibility']=ck('pass'if len(keys)==len(set(keys))and set(keys)==want else'fail','Named per-PO metric population obeys explicit receipt/positive-quantity/positive-amount/non-disputed invoice criteria',missing=sorted(map(str,want-set(keys))),extra=sorted(map(str,set(keys)-want)))
 if expected['profile']['active_without_cost']:
  checks['definition_policy']=ck('pass','Current instruction variant explicitly sets no-eligible-PO cost variance to zero and requires all three weights. Exact raw fractions substitute zero only for that stated case; every candidate cost/component/composite is checked above.',affected_suppliers=expected['profile']['active_without_cost'])
 checks['workflow']=workflow or ck('pending','Trusted actual source/model workflow unavailable');states=[v['status']for v in checks.values()];reward=0 if'fail'in states else None if any(v in states for v in ['gap','pending'])else 1
 return dict(status='unknown'if reward is None else'scored',reward=reward,checks=checks,model_requests=0,native_rewards_modified=False)
