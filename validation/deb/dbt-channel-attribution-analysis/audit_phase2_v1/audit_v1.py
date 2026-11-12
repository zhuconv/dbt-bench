"""Typed channel results and source-derived values; compiled SELECT order separately."""
from datetime import date
from decimal import Decimal
import math
from audit_tables import TablePolicy,audit_table
from dbt_bench.comparison import Column,TableContract
from expected_v1 import COLS,KEYS
TEXT={'order_id','customer_id','channel_id','channel_name','channel_type','first_channel_id','first_channel_name','is_cross_channel','channel_efficiency','acquisition_strength','engagement_tier'}
DATES={'order_date','order_month','first_order_date'}
COUNTS={'total_orders','distinct_channels_used','customer_lifespan_days','unique_customers','new_customers_acquired','revenue_rank','cross_channel_customers','channel_engagement_score'}
INTS=('TINYINT','SMALLINT','INTEGER','BIGINT','HUGEINT','UTINYINT','USMALLINT','UINTEGER','UBIGINT','UHUGEINT')
NUMERIC=INTS+('FLOAT','DOUBLE','REAL')+tuple(f'DECIMAL({p},{s})'for p in range(1,39)for s in range(p+1))
DECIMALS={'total_spend':'DECIMAL(12,2)','total_revenue':'DECIMAL(12,2)','avg_order_value':'DECIMAL(10,2)','orders_per_customer':'DECIMAL(8,2)','revenue_per_customer':'DECIMAL(10,2)','revenue_share_pct':'DECIMAL(5,2)','order_share_pct':'DECIMAL(5,2)','customer_share_pct':'DECIMAL(5,2)','channel_index':'DECIMAL(5,2)','cross_channel_rate':'DECIMAL(5,2)','avg_customer_lifespan':'DECIMAL(8,2)'}
def check(status,reason,**more):return dict(status=status,reason=reason,**more)
def policy(name):
    cols=COLS[name];tc=TableContract(tuple(Column(c,'text'if c in TEXT else'date'if c in DATES else'integer'if c in COUNTS else'numeric')for c in cols),keys=(KEYS[name],),allow_extra_columns=name!='channel_performance')
    types={c:('VARCHAR',)if c in TEXT else('DATE',)if c in DATES else('INTEGER',)if c in COUNTS else(DECIMALS[c],)if c in DECIMALS else NUMERIC for c in cols}
    return TablePolicy(tc,types,not_null=cols,relation_types=('BASE TABLE',)if name=='channel_performance'else())
def same(e,a,c):
    if c in TEXT or c in DATES or c in COUNTS:return type(a)is type(e)and a==e
    if type(a)not in (int,float,Decimal):return False
    actual=Decimal(str(a));wanted=Decimal(e)
    if not actual.is_finite():return False
    if actual==wanted:return True
    # Unrounded detail values may be represented in binary64; one conversion ulp only.
    if c in {'grand_total','first_order_value'}and type(a)is float and wanted!=0:return abs(Decimal.from_float(a)-wanted)<=Decimal.from_float(math.ulp(float(wanted)))
    return False
def values(name,rows,expected):
    key=KEYS[name];got={};errors=[];want={r[key]:r for r in expected}
    for r in rows:
        k=r.get(key)
        if type(k)is not str or k in got:errors.append((k,'invalid or duplicate key'))
        got[k]=r
    if set(got)!=set(want):errors.append(('key_set','missing/unexpected raw-derived keys'))
    for k,e in want.items():
        if k not in got:continue
        for c,v in e.items():
            if c not in got[k]or not same(v,got[k][c],c):errors.append((k,c,str(v),str(got[k].get(c))))
    return check('fail'if errors else'pass','All declared fields independently derived from original orders/channels, preserving keyed membership',error_count=len(errors),examples=errors[:10])
def evaluate(outputs,expected,workflow=None,compiled_rows=None):
    checks={}
    for name,rows in expected.items():
        o=outputs.get(name,{})
        if o.get('exists')is False:checks[name+'.presence']=check('fail','Trusted capture proves required relation absent')
        if o.get('physical_columns')is not None:checks[name+'.required_columns']=check('pass'if set(COLS[name])<=set(o['physical_columns'])else'fail','Actual physical schema proves required columns even when projected SELECT fails')
        if o.get('rows')is None:checks[name+'.values']=check('pending','Required output rows unavailable');continue
        checks[name+'.values']=values(name,o['rows'],rows);t=o.get('table')
        if t is None:checks[name+'.structure']=check('pending','Physical schema/types unavailable')
        else:
            r=audit_table(t,policy(name));checks[name+'.structure']=check('pending'if r['status']=='unknown'else'pass'if r['reward']==1 else'fail','Explicit SQL types and grain; mart physical TABLE required',details=r)
            checks[name+'.lowercase']=check('pass'if all(c==c.lower()for c in t.physical_columns)else'fail','Every actual output column name must be lowercase')
            if name=='channel_performance':checks['mart_column_order']=check('pass'if t.physical_columns==COLS[name]else'fail','Actual physical mart column order must match the prescribed 22 columns')
    if compiled_rows is None:checks['revenue_ordering']=check('pending','Actual compiled SELECT row sequence not captured; physical table scans cannot establish query ordering')
    else:
        v=values('channel_performance',compiled_rows,expected['channel_performance']);ok=v['status']=='pass'and all(a['total_revenue']>=b['total_revenue']for a,b in zip(compiled_rows,compiled_rows[1:]));checks['revenue_ordering']=check('pass'if ok else'fail','Actual compiled mart SELECT returns independently correct rows in descending total_revenue sequence',value_check=v)
    checks['workflow']=workflow or check('pending','Trusted source/deps/run/backend/idempotency evidence unavailable')
    states=[v['status']for v in checks.values()];reward=0 if'fail'in states else None if any(s in states for s in ['pending','gap'])else 1
    return dict(status='unknown'if reward is None else'scored',reward=reward,checks=checks,model_requests=0,native_rewards_modified=False)
