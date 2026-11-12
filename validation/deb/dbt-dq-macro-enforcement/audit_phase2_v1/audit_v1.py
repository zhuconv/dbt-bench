"""DQ full-current-input values, preserved fields, relation and flag placement."""
from decimal import Decimal
from audit_tables import CapturedTable,TablePolicy,audit_table
from dbt_bench.comparison import Column,TableContract,compare_tables
from expected_v1 import PRESERVED,NUMERIC
INTS=('TINYINT','SMALLINT','INTEGER','BIGINT','HUGEINT','UTINYINT','USMALLINT','UINTEGER','UBIGINT','UHUGEINT')
NUMERIC_TYPES=INTS+('FLOAT','DOUBLE','REAL')+tuple(f'DECIMAL({p},{s})'for p in range(1,39)for s in range(p+1))
BOOLS=set('line_total_corrected unit_price_net_corrected dq_is_valid dq_missing_required dq_format_corrected dq_duplicate_suspected dq_late_arriving dq_shipped_vs_ordered_valid dq_return_vs_shipped_valid dq_status_qty_valid dq_order_total_match dq_line_total_nonnegative dq_line_dates_valid dq_category_missing'.split())
def check(status,reason,**x):return dict(status=status,reason=reason,**x)
def normalize(t):return CapturedTable(tuple(c.lower()for c in t.columns),{c.lower():v for c,v in t.sql_types.items()},[{c.lower():v for c,v in r.items()}for r in t.rows],tuple(c.lower()for c in t.physical_columns),{c.lower():v for c,v in t.physical_sql_types.items()},t.relation_type,t.capture_ref)
def contract(expected):return TableContract(tuple(Column(c,'boolean'if c in BOOLS else'numeric'if c in NUMERIC else'text')for c in expected[0]),keys=('order_line_id',),allow_extra_columns=True)
def policy(expected):
    tc=contract(expected)
    return TablePolicy(tc,{c.name:('BOOLEAN',)if c.name in BOOLS else NUMERIC_TYPES if c.name in NUMERIC else('VARCHAR',)for c in tc.columns},not_null=('order_line_id',),relation_types=('VIEW',))
def evaluate(outputs,expected,workflow=None,initial_rows=None):
    name='fct_order_line_detail';o=outputs.get(name,{});checks={};rows=list(expected.values())
    if o.get('exists')is False:checks['presence']=check('fail','Trusted capture proves required relation absent')
    if o.get('rows')is None:checks['values']=check('pending','Lossless output rows unavailable')
    else:
        actual=[{c.lower():v for c,v in r.items()}for r in o['rows']];comp=compare_tables(rows,actual,contract(rows));checks['values']=check('pass'if comp.equal else'fail','Independent Python all 33 fields and 3576 original source keys',comparison=comp.reason)
        errs=[(r.get('order_line_id'),c)for r in actual for c in NUMERIC if r.get(c)is not None and(type(r[c])not in(int,float,Decimal)or not Decimal(str(r[c])).is_finite())]
        checks['numeric_domains']=check('fail'if errs else'pass','Numeric measures finite; identifiers never coerced',examples=errs[:10])
        if initial_rows is None:checks['preserved_nonnull']=check('pending','Actual initial existing columns not captured')
        else:
            old={r['order_line_id']:r for r in initial_rows};new={r.get('order_line_id'):r for r in actual};errors=[(k,c)for k,r in old.items()for c in PRESERVED if r[c]is not None and(k not in new or new[k].get(c)is None)]
            checks['preserved_nonnull']=check('fail'if errors else'pass','No new NULL in any of ten actual original fields',error_count=len(errors),examples=errors[:10])
    t=o.get('table')
    if t is None:checks['structure']=check('pending','Actual native physical SQL schema unavailable')
    else:
        t=normalize(t);a=audit_table(t,policy(rows));checks['structure']=check('pending'if a['status']=='unknown'else'pass'if a['reward']==1 else'fail','Required columns, original VIEW materialization, typed booleans/text/numeric, exact key',details=a)
        cols=t.physical_columns;dq=[i for i,c in enumerate(cols)if c.startswith('dq_')];nondq=[i for i,c in enumerate(cols)if not c.startswith('dq_')]
        checks['dq_columns_at_end']=check('pass'if dq and nondq and min(dq)>max(nondq)else'fail','Physical metadata confirms all DQ columns occur after non-DQ columns')
    checks['workflow']=workflow or check('pending','Trusted deps/run, upstream preservation, backend/profile and macro behavior not verified')
    states=[v['status']for v in checks.values()];reward=0 if'fail'in states else None if any(s in states for s in ['pending','gap'])else 1
    return dict(status='unknown'if reward is None else'scored',reward=reward,checks=checks,model_requests=0,native_rewards_modified=False)
