"""Explicit typed projections for revised audits; no automatic table discovery."""
from collections import Counter
from dataclasses import replace
from datetime import date, datetime
from decimal import Decimal
from audit_tables import TablePolicy, audit_table
from dbt_bench.comparison import Column, TableContract

INTS = ('TINYINT','SMALLINT','INTEGER','BIGINT','HUGEINT','UTINYINT','USMALLINT','UINTEGER','UBIGINT','UHUGEINT')
NUMS = INTS + ('FLOAT','DOUBLE','REAL') + tuple(f'DECIMAL({p},{s})' for p in range(1,39) for s in range(p+1))

def ck(status, reason, **kw):
    return dict(status=status, reason=reason, **kw)

def finish(checks):
    states = [v['status'] for v in checks.values()]
    reward = 0 if 'fail' in states else None if any(s in states for s in ('pending','gap')) else 1
    return dict(reward=reward, status='unknown' if reward is None else 'scored', checks=checks,
                model_requests=0, native_rewards_modified=False)

def table_check(item, expected, keys, *, required=None, materializations=(), not_null=(), types=None, tolerance=None):
    """Compare named projections with exact keys, rows and multiplicity."""
    if item.get('exists') is False:
        return ck('fail','Required named relation is absent')
    t = item.get('table')
    if t is None:
        return ck('pending','Typed physical capture unavailable')
    required = list(required or expected[0])
    if not set(required) <= set(t.columns):
        return ck('fail','Required declared projection is missing', missing=sorted(set(required)-set(t.columns)))
    policies = {}; columns = []
    for name in required:
        values = [r[name] for r in expected if r[name] is not None]
        value = values[0] if values else None
        kind = ('text' if isinstance(value,str) else 'boolean' if type(value) is bool else
                'datetime' if isinstance(value,datetime) else 'date' if type(value) is date else
                'integer' if type(value) is int else 'numeric')
        policy = (('VARCHAR',) if kind=='text' else ('BOOLEAN',) if kind=='boolean' else
                  ('TIMESTAMP','TIMESTAMP WITH TIME ZONE') if kind=='datetime' else
                  ('DATE',) if kind=='date' else INTS if kind=='integer' else NUMS)
        policies[name] = (types or {}).get(name,policy)
        columns.append(Column(name,kind,abs_tol=(tolerance or {}).get(name,0)))
    result = audit_table(t, TablePolicy(TableContract(tuple(columns),keys=tuple(keys),allow_extra_columns=True),
        policies,not_null=tuple(not_null),relation_types=tuple(materializations)),
        [{k:r[k] for k in required} for r in expected])
    return ck('pass' if result['reward']==1 else 'fail' if result['reward']==0 else 'pending',
              'Independent original-input projection; exact typed keys and duplicate-preserving population',details=result)

def cleaned(rows):
    return [{k:v.strip() if isinstance(v,str) else v for k,v in r.items()} for r in rows]
