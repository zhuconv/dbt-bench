"""Declared Session table, exact channel grain and independent decimal attribution."""
from decimal import Decimal,InvalidOperation
from collections import Counter
import re
from audit_tables import TablePolicy,audit_table
from dbt_bench.comparison import Column,TableContract,compare_tables
from expected_v1 import recompute
COLS={'attribution_report':['channel','total_conversions','total_revenue']}
KEYS={'attribution_report':['channel']}

def ck(s,r,**kw):return dict(status=s,reason=r,**kw)
def evaluate(outputs,expected,workflow=None):
 checks={};o=outputs.get('attribution_report',{});rows=o.get('rows');t=o.get('table');want=expected['expected'];unknown=expected['unknown']
 if o.get('exists')is False:checks['presence']=ck('fail','Required main.attribution_report absent')
 if o.get('missing_required_columns'):checks['columns']=ck('fail','Required named columns absent')
 if rows is None:checks['values']=ck('pending','Actual captured rows unavailable')
 else:
  if t is None:checks['interface']=ck('pending','Actual SQL interface capture unavailable')
  else:
   allowed={}
   for n in COLS['attribution_report']:
    typ=t.physical_sql_types.get(n,'');allowed[n]=('VARCHAR',)if n=='channel'else(typ,)if re.fullmatch(r'(DECIMAL\(\d+,\d+\)|DOUBLE|FLOAT|REAL|TINYINT|SMALLINT|INTEGER|BIGINT|HUGEINT)',typ)else('DECIMAL','DOUBLE')
   z=audit_table(t,TablePolicy(TableContract((Column('channel','text'),Column('total_conversions','numeric'),Column('total_revenue','numeric')),keys=('channel',),allow_extra_columns=True),allowed,not_null=tuple(COLS['attribution_report']),relation_types=('TABLE','BASE TABLE')));checks['interface']=ck('fail'if any(v is False for v in z.get('checks',{}).values())else'pass'if z['reward']==1 else'pending'if z['status']=='unknown'else'fail','Required named numeric values and text key; no arbitrary DECIMAL width required; additional nullable fields permitted',details=z)
  got=Counter(r.get('channel')for r in rows);desired=Counter(r['channel']for r in want);checks['population']=ck('fail'if got!=desired else'pass','Exact channel/value association and row multiplicity',actual=dict(got),expected=dict(desired))
  for n in ['total_conversions','total_revenue']:
   gold=[{'channel':r['channel'],n:Decimal(r[n])}for r in want];ct=TableContract((Column('channel','text'),Column(n,'numeric',abs_tol=Decimal('0.000000001'))),keys=('channel',),allow_extra_columns=True);z=compare_tables(gold,rows,ct);checks[n]=ck('gap'if unknown else'pass'if z.equal else'fail','Independent80-digit Decimal hybrid weights from original input, not public solution or native output',comparison=z.__dict__,unknown_input_branches=unknown)
  bad=[]
  for i,r in enumerate(rows):
   for n in ['total_conversions','total_revenue']:
    try:
     v=Decimal(str(r[n]));valid=v.is_finite()and v==v.quantize(Decimal('.01'))
    except(InvalidOperation,KeyError,ValueError,TypeError):valid=False
    if not valid:bad.append({'row':i,'column':n,'value':str(r.get(n))})
  checks['precision']=ck('fail'if bad else'pass','Explicit2decimal output precision and finite values; storage tolerance does not permit extra business precision',errors=bad)
  labels=[r.get('channel')for r in rows];ok=all(isinstance(c,str)for c in labels)and labels==sorted(labels);checks['ordering']=ck('pass'if ok else'fail','Actual output query order alphabetical by channel')
 checks['policy_scope']=ck('gap'if unknown else'pass','Actual input nullable-quality/tied-touch/repeated-conversion/half-cent limitations evaluated',unknown=unknown);checks['workflow']=workflow or ck('pending','Trusted actual workflow observations unavailable');ss=[v['status']for v in checks.values()];reward=0 if'fail'in ss else None if any(s in ['gap','pending']for s in ss)else 1
 return {'status':'unknown'if reward is None else'scored','reward':reward,'checks':checks,'model_requests':0,'native_rewards_modified':False,'scope':'Current finite input profile only; complete task legality also needs path/workflow judgment.'}
