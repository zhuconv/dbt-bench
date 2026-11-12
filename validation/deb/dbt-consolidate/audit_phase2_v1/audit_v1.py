"""Independent public-CSV result policy; row order and integral scale are immaterial."""
from decimal import Decimal
from audit_tables import TablePolicy,audit_table
from dbt_bench.comparison import Column,TableContract
from expected_v1 import STAGING_COLUMNS,INT_COLUMNS,TABLES,compare

INTS=('TINYINT','SMALLINT','INTEGER','BIGINT','HUGEINT','UTINYINT','USMALLINT','UINTEGER','UBIGINT','UHUGEINT')
NUMERIC=INTS+('FLOAT','DOUBLE','REAL')+tuple(f'DECIMAL({p},{s})'for p in range(1,39)for s in range(p+1))
def check(status,reason,**more):return dict(status=status,reason=reason,**more)
def policy(name):
    cols=INT_COLUMNS if name=='unified'else STAGING_COLUMNS
    contract=TableContract(tuple(Column(c,'text'if c=='source'else'exact'if c=='ad_date'else'numeric')for c in cols),keys=('source','ad_date')if name=='unified'else(),allow_extra_columns=False)
    return TablePolicy(contract,{c:('VARCHAR',)if c=='source'else('DATE','VARCHAR')if c=='ad_date'else NUMERIC for c in cols},not_null=cols,relation_types=('BASE TABLE',)if name=='unified'else('VIEW',))
def evaluate(outputs,expected,workflow=None):
    checks={}
    for name,table in TABLES.items():
        o=outputs.get(name,{})
        if o.get('exists')is False:checks[name+'.presence']=check('fail','Trusted capture proves required relation absent')
        if o.get('physical_columns')is not None:checks[name+'.required_columns']=check('pass'if set(INT_COLUMNS if name=='unified'else STAGING_COLUMNS)<=set(o['physical_columns'])else'fail','Actual physical schema is available even if a projected SELECT failed')
        if o.get('relation_type')is not None:checks[name+'.materialization']=check('pass'if o['relation_type']==('BASE TABLE'if name=='unified'else'VIEW')else'fail','Actual relation kind captured independently of missing SQL column types')
        if o.get('rows')is None:checks[name+'.values']=check('pending','Required output rows not captured');continue
        try:
            rows=o['rows'];cols=INT_COLUMNS if name=='unified'else STAGING_COLUMNS
            # The independent canonicalizer accepts input CSV strings; delivered counts must be numeric.
            typed=all(type(r.get(c))in(int,float,Decimal)for r in rows for c in STAGING_COLUMNS[1:])
            comparison=compare(expected[name],rows,name=='unified')
            checks[name+'.values']=check('pass'if typed and comparison['equal']else'fail','Exact full-row multiset from public CSVs; finite integral numeric counts, no floating tolerance or row-order condition',numeric_values=typed,comparison=comparison)
        except(KeyError,ValueError,TypeError,ArithmeticError)as e:checks[name+'.values']=check('fail','Invalid required source-derived value: '+str(e))
        if o.get('table')is None:checks[name+'.structure']=check('pending','Physical SQL types/materialization not captured')
        else:
            result=audit_table(o['table'],policy(name));checks[name+'.structure']=check('pending'if result['status']=='unknown'else'pass'if result['reward']==1 else'fail','Declared columns, numeric representations and physical view/table role',details=result)
    checks['workflow']=workflow or check('pending','Trusted dbt source/seed/deps/run/test/backend evidence not supplied')
    states=[v['status']for v in checks.values()];reward=0 if'fail'in states else None if any(s in states for s in ['pending','gap'])else 1
    return dict(status='unknown'if reward is None else'scored',reward=reward,checks=checks,model_requests=0,native_rewards_modified=False)
