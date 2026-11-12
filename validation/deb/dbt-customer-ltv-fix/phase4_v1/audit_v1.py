"""Patched status-only LTV semantics, recomputed directly from original source rows."""
from collections import defaultdict
from datetime import datetime,date
from decimal import Decimal as D
from fractions import Fraction
from audit_tables import audit_table,TablePolicy
from dbt_bench.comparison import TableContract,Column
COLS=('customer_id','total_orders','lifetime_value','avg_order_value','first_order_date','last_order_date','value_segment')
INTS=('TINYINT','SMALLINT','INTEGER','BIGINT','HUGEINT','UTINYINT','USMALLINT','UINTEGER','UBIGINT','UHUGEINT')
NUMERIC=INTS+('FLOAT','DOUBLE','REAL')+tuple(f'DECIMAL({p},{s})' for p in range(1,39) for s in range(p+1))
def cents(x):
 f=Fraction(x)*100;lo=f.numerator//f.denominator;rem=f-lo
 return [D(lo)/100] if rem<Fraction(1,2) else [D(lo+1)/100] if rem>Fraction(1,2) else [D(lo)/100,D(lo+1)/100]
def day(v):return v.date() if isinstance(v,datetime) else v
def tier(v):return 'VIP' if v>=1000 else 'High Value' if v>=500 else 'Medium Value' if v>=100 else 'Low Value' if v>0 else 'No Value'
def derive(raw):
 groups=defaultdict(list)
 for r in raw['orders']:
  if r['status'] is not None and r['status'] not in ('CANCELLED','RETURNED'):groups[r['customer_id']].append(r)
 out=[]
 for c in raw['customers']:
  cid=c['customer_id'];rows=groups[cid];amounts=[D(r['grand_total']) for r in rows if r['grand_total'] is not None];dates=[day(r['ordered_at']) for r in rows if r['ordered_at'] is not None];money=cents(sum(amounts,D(0)));count=len({r['order_id'] for r in rows if r['order_id'] is not None})
  out.append({'customer_id':cid,'total_orders':count,'lifetime_value':money,'avg_order_value':cents(Fraction(sum(amounts,D(0)))/len(amounts)) if amounts else [D(0)],'first_order_date':min(dates) if dates else None,'last_order_date':max(dates) if dates else None,'value_segment':sorted({tier(m) for m in money})})
 return {'rows':out,'facts':{'raw_customers':len(raw['customers']),'raw_orders':len(raw['orders']),'eligible_orders':sum(map(len,groups.values())),'policy':'Patched explicit status-only rule excludes exactly CANCELLED/RETURNED; payment/fulfillment/cancellation timestamps are not additional filters. SQL NULL status is explicitly retained as an investigated absence if present.','null_status_orders':sum(r['status'] is None for r in raw['orders'])}}
def exact(got,wanted):
 if isinstance(wanted,list):return any(exact(got,x) for x in wanted)
 if type(got) in (int,float,D) and type(wanted) in (int,D):return D(str(got))==D(wanted)
 return type(got)==type(wanted) and got==wanted

def evaluate(o,expected,workflow=None):
 checks={};table=o.get('table');rows=o.get('rows')
 types={c:('VARCHAR',) if c in ('customer_id','value_segment') else ('DATE',) if c.endswith('_date') else NUMERIC for c in COLS}
 contract=TableContract(tuple(Column(c,'text' if c in ('customer_id','value_segment') else 'date' if c.endswith('_date') else 'numeric') for c in COLS),keys=('customer_id',),allow_extra_columns=True)
 if table is None:checks['interface']={'status':'fail' if o.get('exists') is False else 'pending','reason':'Actual required table/schema unavailable'}
 else:
  interface=audit_table(table,TablePolicy(contract,types,not_null=tuple(c for c in COLS if not c.endswith('_date')),relation_types=('BASE TABLE',)));checks['interface']={'status':'pass' if interface['reward']==1 else 'fail' if interface['reward']==0 else 'pending','details':interface}
 if rows is None:checks['values']={'status':'pending','reason':'Rows not captured'}
 else:
  got={r.get('customer_id'):r for r in rows};want={r['customer_id']:r for r in expected['rows']};errors=[]
  checks['population']={'status':'pass' if len(got)==len(rows) and set(got)==set(want) else 'fail','expected':len(want),'actual_rows':len(rows),'actual_distinct':len(got)}
  for cid in set(got)&set(want):
   for col in COLS:
    if not exact(got[cid].get(col),want[cid][col]):errors.append({'customer_id':cid,'column':col,'actual':str(got[cid].get(col)),'expected':str(want[cid][col])})
  checks['values']={'status':'fail' if errors else 'pass','error_count':len(errors),'examples':errors[:20],'reason':'Full seven-field original-source exact arithmetic and date/key correspondence; nearest cents only, both exact half-cent ties allowed'}
 if expected['facts']['null_status_orders']:checks['null_status']={'status':'gap','reason':'Actual NULL status rows require explicit selection interpretation under exclude-only wording'}
 checks['workflow']=workflow or {'status':'pending','reason':'Actual workflow not supplied'}
 states=[v['status'] for v in checks.values()];reward=0 if 'fail' in states else None if any(s in states for s in ('gap','pending')) else 1
 return {'reward':reward,'status':'unknown' if reward is None else 'scored','checks':checks,'model_requests':0}
