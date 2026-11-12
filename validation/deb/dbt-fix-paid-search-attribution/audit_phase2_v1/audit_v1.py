"""Explicit typed-table audit; correct data alone never establishes dbt workflow."""
from decimal import Decimal
from audit_tables import TablePolicy,audit_table,CapturedTable
from dbt_bench.comparison import Column,TableContract,compare_tables
NAMES=('attribution_date','channel','medium','campaign','sessions','conversions','attributed_revenue','conversion_rate');KEYS=NAMES[:4]
INTEGERS=('TINYINT','SMALLINT','INTEGER','BIGINT','HUGEINT','UTINYINT','USMALLINT','UINTEGER','UBIGINT','UHUGEINT')
NUMERIC=INTEGERS+('FLOAT','DOUBLE','REAL')+tuple(f'DECIMAL({p},{s})'for p in range(1,39)for s in range(p+1))
def contract(kind):return TableContract(tuple(Column(n,'date'if n=='attribution_date'else'text'if n in KEYS else'numeric')for n in NAMES),keys=KEYS,allow_extra_columns=kind=='paid')
def policy(kind):
 types={n:('DATE',)if n=='attribution_date'else('VARCHAR',)if n in KEYS else('BIGINT',)if kind=='email'and n in ('sessions','conversions')else('DECIMAL(12,2)',)if kind=='email'and n=='attributed_revenue'else('DECIMAL(10,4)',)if kind=='email'and n=='conversion_rate'else INTEGERS if n in ('sessions','conversions')else NUMERIC for n in NAMES}
 return TablePolicy(contract(kind),types,not_null=NAMES,relation_types=('TABLE','BASE TABLE'))
def check(s,r,**x):return {'status':s,'reason':r,**x}
def precision(v,places):
 d=Decimal(str(v));parts=d.as_tuple();return d.is_finite()and(parts.exponent>=-places or not any(parts.digits[-min(len(parts.digits),-places-parts.exponent):]))
def evaluate(kind,rows,expected,*,structured=None,relation_exists=None,relation_type=None,workflow=None,repeated=None,projection_types=None):
 checks={}
 if relation_exists is False:checks['required_relation']=check('fail','Trusted relation inventory explicitly proves required analytics relation absent')
 if rows is None:checks['values']=check('pending','Required candidate rows not captured')
 else:
  rows=[{k.lower():v for k,v in r.items()}for r in rows];cmp=compare_tables(expected,rows,contract(kind));checks['values']=check('pass'if cmp.equal else'fail',cmp.reason)
  valid=True
  for r in rows:
   if any(r.get(n)is None for n in NAMES):valid=False;continue
   for n in NAMES[4:]:
    v=r[n]
    if type(v)not in(int,float,Decimal):valid=False;continue
    d=Decimal(str(v))
    if not d.is_finite():valid=False;continue
    if n in ('sessions','conversions'):valid &= d>=0 and d==d.to_integral_value()
    if n=='conversion_rate':valid &= 0<=d<=1 and precision(v,4)
    if n=='attributed_revenue':valid &= precision(v,2)and(kind!='email'or d>=0)
   if type(r['sessions'])in(int,float,Decimal)and type(r['conversions'])in(int,float,Decimal):
    left,right=Decimal(str(r['conversions'])),Decimal(str(r['sessions']));valid &= left.is_finite()and right.is_finite()and left<=right
  checks['domains']=check('pass'if valid else'fail','Non-NULL required columns; finite integral counts; conversion bound; correct decimal precision; email revenue nonnegative')
 if structured is None:checks['structure']=check('pending','Precise SQL and physical column inventory missing; DBAPI NUMBER/STRING cannot replace it')
 else:
  t=structured;t=CapturedTable(tuple(x.lower()for x in t.columns),{k.lower():v for k,v in t.sql_types.items()},[{k.lower():v for k,v in r.items()}for r in t.rows],tuple(x.lower()for x in t.physical_columns),{k.lower():v for k,v in t.physical_sql_types.items()},t.relation_type,t.capture_ref);out=audit_table(t,policy(kind));checks['structure']=check('pending'if out['status']=='unknown'else'pass'if out['reward']==1 else'fail',out['reason'],details=out)
 if projection_types is not None:
  wrong={n:t for n,t in projection_types.items()if n in policy(kind).sql_types and t not in policy(kind).sql_types[n]}
  if wrong:checks['observed_wrong_column_types']=check('fail','Captured native SQL types violate declared field types',wrong=wrong)
 if relation_type is not None:checks['materialization']=check('pass'if relation_type in ('TABLE','BASE TABLE')else'fail','Independently captured final relation type must be TABLE',actual_type=relation_type)
 if repeated is not None and rows is not None:
  cmp=compare_tables(rows,[{k.lower():v for k,v in r.items()}for r in repeated],contract(kind));checks['repeat']=check('pass'if cmp.equal else'fail',cmp.reason)
 checks['workflow']=check('pending','Trusted actual project/profile/model/build evidence not supplied')if workflow is None else workflow
 statuses=[v['status']for v in checks.values()];reward=0 if'fail'in statuses else None if'pending'in statuses else 1
 return {'status':'unknown'if reward is None else'scored','reward':reward,'checks':checks,'model_requests':0,'native_rewards_modified':False}
