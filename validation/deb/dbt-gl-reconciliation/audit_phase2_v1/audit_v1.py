"""GL raw-derived rules plus explicit unresolved trial sides/orphan membership."""
from decimal import Decimal as D
from datetime import date
import math
from audit_tables import TablePolicy,audit_table
from dbt_bench.comparison import Column,TableContract
from expected_v1 import COLS,KEYS
TEXT={'account_id','account_number','account_name','account_type','account_subtype','transaction_number','expected_direction','actual_direction','period_id','period_name','period_status'}
DATES={'entry_date','start_date','end_date'};BOOLS={'is_balanced','is_unusual'};COUNTS={'line_count','fiscal_year','fiscal_quarter','fiscal_month','transaction_count'}
INTS=('TINYINT','SMALLINT','INTEGER','BIGINT','HUGEINT','UTINYINT','USMALLINT','UINTEGER','UBIGINT','UINTEGER','UBIGINT','UHUGEINT');NUMERIC=INTS+('FLOAT','DOUBLE','REAL')+tuple(f'DECIMAL({p},{s})'for p in range(1,39)for s in range(p+1))
def check(status,reason,**kw):return dict(status=status,reason=reason,**kw)
def finite(v):return type(v)in(int,float,D)and D(str(v)).is_finite()
def same(e,a,c):
 if e is None:return a is None
 if c in TEXT|DATES|BOOLS:return type(a)is type(e)and a==e
 if not finite(a):return False
 if D(str(a))==D(e):return True
 return c not in COUNTS and type(a)is float and abs(D.from_float(a)-D(e))<=D.from_float(math.ulp(float(e)))
def evaluate(outputs,expected,workflow=None):
 checks={}
 for name,cols in COLS.items():
  o=outputs.get(name,{});rows=o.get('rows');kind=lambda c:'text'if c in TEXT else'date'if c in DATES else'boolean'if c in BOOLS else'numeric';types={c:('VARCHAR',)if c in TEXT else('DATE',)if c in DATES else('BOOLEAN',)if c in BOOLS else NUMERIC for c in cols};tc=TableContract(tuple(Column(c,kind(c))for c in cols),keys=(KEYS[name],)if KEYS[name]else(),allow_extra_columns=True);pol=TablePolicy(tc,types,not_null=tuple(c for c in cols if c not in ['account_subtype','prior_period_net_activity','activity_change']))
  if o.get('exists')is False:checks[name+'.presence']=check('fail','Trusted observation proves required relation absent')
  if o.get('table')is not None:
   v=audit_table(o['table'],pol);checks[name+'.interface']=check('pending'if v['status']=='unknown'else'pass'if v['reward']==1 else'fail','Required logical types/columns/grain; no unspecified physical materialization constraint',details=v)
  else:checks[name+'.interface']=check('pending','Exact physical schema capture unavailable')
  if rows is None:checks[name+'.values']=check('pending','Required rows unavailable');continue
  if name=='trial_balance':
   checks['trial_single_row']=check('pass'if len(rows)==1 else'fail','Exactly one trial summary required')
   if len(rows)==1:
    r=rows[0];d,c,diff=[r.get(x)for x in ['total_debit_balances','total_credit_balances','difference']];ok=all(finite(v)for v in [d,c,diff]);checks['trial_difference']=check('pass'if ok and same(D(str(d))-D(str(c)),diff,'difference')else'fail','Difference must equal actual debit total minus credit total without choosing side convention');checks['trial_balance_flag']=check('pass'if finite(diff)and type(r.get('is_balanced'))is bool and r['is_balanced']==(abs(D(str(diff)))<=D('.01'))else'fail','Balance flag must follow reported difference within inclusive0.01');checks['trial_side_semantics']=check('gap','Actual raw-sign and natural-net-sign conventions produce different totals; task does not select a convention',independent_alternatives=expected['trial_alternatives'])
   continue
  want=expected['outputs'][name];key=KEYS[name];got={};errors=[]
  for r in rows:
   k=r.get(key)
   if type(k)is not str or k in got:errors.append([str(k),'invalid or duplicate grain'])
   got[k]=r
  required={r[key]for r in want};extras=set(got)-required
  # Known chart accounts must agree; unmatched original transaction accounts lack type/dimension definition.
  if not required<=set(got)or(extras and name!='account_balances'):errors.append(['membership','missing/unexpected rows'])
  for r in want:
   k=r[key]
   if k not in got:continue
   for c,e in r.items():
    a=got[k].get(c)
    if name=='period_summary'and r==want[0]and c in ['prior_period_net_activity','activity_change']and a is None:continue
    if not same(e,a,c):errors.append([k,c,str(e),str(a)])
  checks[name+'.values']=check('fail'if errors else'pass','Independent original-input keyed values in the explicitly defined population',error_count=len(errors),examples=errors[:10])
 if expected['facts']['transactions_without_account']:checks['orphan_account_membership']=check('gap','Actual15 transactions have account IDs absent from chart; all-transactions wording leaves required type/name/net-direction unspecified for those accounts. Known20 accounts are checked, not claimed as complete raw coverage.',unmatched_transaction_count=expected['facts']['transactions_without_account'])
 checks['workflow']=workflow or check('pending','Trusted project/profile/backend/build capture unavailable');states=[c['status']for c in checks.values()];reward=0 if'fail'in states else None if any(s in states for s in ['pending','gap'])else 1
 return dict(status='unknown'if reward is None else'scored',reward=reward,checks=checks,model_requests=0,native_rewards_modified=False)
