"""Declared numeric/key policy; workflow remains an independent requirement."""
from decimal import Decimal,ROUND_HALF_UP,ROUND_HALF_DOWN,localcontext
import math
from audit_tables import CapturedTable,TablePolicy,audit_table
from dbt_bench.comparison import Column,TableContract,compare_tables

INTS=('TINYINT','SMALLINT','INTEGER','BIGINT','HUGEINT','UTINYINT','USMALLINT','UINTEGER','UBIGINT','UHUGEINT')
NUMERIC=INTS+('FLOAT','DOUBLE','REAL')+tuple(f'DECIMAL({p},{s})'for p in range(1,39)for s in range(p+1))
TEXT={'order_id','order_source'}
FLAGS={'is_cancelled','has_lines','has_variance','has_tax_variance'}
COUNTS={'line_count','order_count'}
def check(status,reason,**x):return dict(status=status,reason=reason,**x)
def normalize(t):return CapturedTable(tuple(c.lower()for c in t.columns),{c.lower():v for c,v in t.sql_types.items()},[{c.lower():v for c,v in r.items()}for r in t.rows],tuple(c.lower()for c in t.physical_columns),{c.lower():v for c,v in t.physical_sql_types.items()},t.relation_type,t.capture_ref)
def contract(name,rows):return TableContract(tuple(Column(c,'text'if c in TEXT else'numeric')for c in rows[0]),keys=('order_source',)if name=='mart_revenue_by_source'else('order_id',),allow_extra_columns=True)
def policy(name,rows):
    t=contract(name,rows)
    return TablePolicy(t,{c.name:('VARCHAR',)if c.name in TEXT else NUMERIC for c in t.columns},not_null=t.keys)
def numeric_equal(expected,actual,column):
    if type(actual)not in (int,float,Decimal):return False
    a=Decimal(str(actual));e=Decimal(expected)
    if not a.is_finite():return False
    if a==e:return True
    if column not in {'avg_order_value','subtotal_variance_pct','tax_variance_pct'}:return False
    # Current expressions have one division, or division followed by *100.
    # Four ulps bounds conversion and these binary64 operations; not a business tolerance.
    bound=Decimal.from_float(math.ulp(float(e)))*4
    if abs(a-e)<=bound:return True
    if column.endswith('_pct'):
        # Explicit nearest decimal rounding; both nearest choices at exact half ties.
        # Zero through twelve decimal places are allowed because no precision is specified.
        with localcontext()as ctx:
            ctx.prec=100
            for digits in range(13):
                q=Decimal(1).scaleb(-digits)
                if a in {e.quantize(q,rounding=ROUND_HALF_UP),e.quantize(q,rounding=ROUND_HALF_DOWN)}:return True
    return False
def values(name,expected,rows):
    tc=contract(name,expected);key=tc.keys[0];em={r[key]:r for r in expected};got={};errors=[]
    for r in rows:
        k=r.get(key)
        if type(k)is not str or k in got:errors.append((k,'invalid/duplicate key'))
        got[k]=r
    if set(got)!=set(em):errors.append(('key_set','missing or unexpected source-derived keys'))
    for k,e in em.items():
        if k not in got:continue
        r=got[k]
        for c,v in e.items():
            a=r.get(c)
            if c not in r or not(type(a)is type(v)and a==v if c in TEXT else numeric_equal(v,a,c)):errors.append((k,c,str(v),str(a)))
    return check('fail'if errors else'pass','Independent source-derived full values and keyed multiplicity; exact sums/counts, bounded division and explicit nearest percentage rounding only',error_count=len(errors),examples=errors[:10])
def evaluate(outputs,expected,workflow=None,staging=None):
    checks={}
    for name,rows in expected.items():
        o=outputs.get(name,{})
        if o.get('exists')is False:checks[name+'.presence']=check('fail','Trusted capture proves required relation absent')
        if o.get('rows')is None:checks[name+'.values']=check('pending','Output rows not captured');continue
        actual=[{c.lower():v for c,v in r.items()}for r in o['rows']];checks[name+'.values']=values(name,rows,actual)
        t=o.get('table')
        if t is None:checks[name+'.structure']=check('pending','Native physical SQL schema unavailable')
        else:
            a=audit_table(normalize(t),policy(name,rows));checks[name+'.structure']=check('pending'if a['status']=='unknown'else'pass'if a['reward']==1 else'fail','Declared schema and grain; no invented column order or materialization',details=a)
    checks['staging']=staging or check('pending','Actual staging source lineage and values not verified')
    checks['workflow']=workflow or check('pending','Trusted source/build/profile/backend capture not verified')
    states=[x['status']for x in checks.values()];reward=0 if'fail'in states else None if any(s in states for s in ['pending','gap'])else 1
    return {'status':'unknown'if reward is None else'scored','reward':reward,'checks':checks,'model_requests':0,'native_rewards_modified':False}
