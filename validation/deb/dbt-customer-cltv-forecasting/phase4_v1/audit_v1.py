"""Patched CLTV eligibility and exact rational forecast from raw enriched orders."""
from collections import defaultdict
from datetime import datetime
from decimal import Decimal as D
from fractions import Fraction
from audit_tables import TablePolicy, audit_table
from dbt_bench.comparison import Column, TableContract
COLS=('customer_id','as_of_date','current_lifetime_revenue','current_order_count','avg_order_value','days_since_last_order','forecast_3m_revenue','forecast_6m_revenue','forecast_12m_revenue')
COUNTS={'current_order_count','days_since_last_order'}
def day(x): return x.date() if isinstance(x,datetime) else x
def month(d): return d.year*12+d.month-1
def cents(x):
 f=Fraction(x)*100;lo=f.numerator//f.denominator;rem=f-lo
 return [D(lo)/100] if rem<Fraction(1,2) else [D(lo+1)/100] if rem>Fraction(1,2) else [D(lo)/100,D(lo+1)/100]
def derive(raw):
 source=raw['enriched'];asof=max(day(r['ordered_at']) for r in source if r['ordered_at'] is not None);groups=defaultdict(list)
 for r in source:
  if r['customer_id'] is not None and r['ordered_at'] is not None and r['is_cancelled'] is not True: groups[r['customer_id']].append(r)
 rows=[]
 for cid,rs in sorted(groups.items()):
  total=sum((D(r['grand_total']) for r in rs if r['grand_total'] is not None),D(0));count=len({r['order_id'] for r in rs if r['order_id'] is not None});monthly=defaultdict(lambda:D(0))
  for r in rs:
   m=month(day(r['ordered_at']))
   if month(asof)-6<=m<month(asof):monthly[m]+=D(r['grand_total']) if r['grand_total'] is not None else D(0)
  avg=Fraction(sum(monthly.values(),D(0)))/len(monthly) if monthly else Fraction(0)
  row={'customer_id':cid,'as_of_date':asof,'current_lifetime_revenue':cents(total),'current_order_count':count,'avg_order_value':cents(Fraction(total)/max(count,1)),'days_since_last_order':(asof-max(day(r['ordered_at']) for r in rs)).days}
  row.update({f'forecast_{n}m_revenue':cents(Fraction(total)+n*avg) for n in (3,6,12)});rows.append(row)
 return {'rows':rows,'facts':{'raw_rows':len(source),'eligible_rows':sum(map(len,groups.values())),'customers':len(rows),'as_of_date':asof,'null_cancellation_rows':sum(r['is_cancelled'] is None for rs in groups.values() for r in rs),'reconciliation_total':sum((D(r['grand_total']) for rs in groups.values() for r in rs if r['grand_total'] is not None),D(0)),'scope':'All nine columns independently recomputed; patched exclusions apply before every metric. Rational average over active months in the six full calendar months; only exact final half-cent ties allow both nearest cents.'}}
def same(a,e):
 if isinstance(e,list):return any(same(a,v) for v in e)
 if type(e) is D:return type(a) in (int,float,D) and D(str(a))==e
 return type(a) is type(e) and a==e
def evaluate(o,expected,workflow=None):
 checks={};types={c:('VARCHAR',) if c=='customer_id' else ('DATE',) if c=='as_of_date' else ('INTEGER',) if c in COUNTS else ('DECIMAL(18,2)',) for c in COLS};contract=TableContract(tuple(Column(c,'text' if c=='customer_id' else 'date' if c=='as_of_date' else 'integer' if c in COUNTS else 'numeric') for c in COLS),keys=('customer_id',),allow_extra_columns=True)
 if o.get('table') is None: checks['interface']={'status':'fail' if o.get('exists') is False else 'pending','reason':'Actual required relation absent or not captured'}
 else:
  result=audit_table(o['table'],TablePolicy(contract,types,not_null=COLS,relation_types=('TABLE','BASE TABLE')));checks['interface']={'status':'pass' if result['reward']==1 else 'fail' if result['reward']==0 else 'pending','details':result}
 rows=o.get('rows')
 if rows is None:checks['values']={'status':'pending','reason':'Output not captured'}
 else:
  got={r.get('customer_id'):r for r in rows};want={r['customer_id']:r for r in expected['rows']};errors=[]
  checks['population']={'status':'pass' if len(got)==len(rows) and set(got)==set(want) else 'fail','actual':len(rows),'expected':len(want)}
  for cid in set(got)&set(want):
   for c in COLS:
    if not same(got[cid].get(c),want[cid][c]):errors.append({'customer':cid,'column':c,'actual':str(got[cid].get(c)),'expected':str(want[cid][c])})
  checks['values']={'status':'fail' if errors else 'pass','reason':'Full keyed nine-field independent arithmetic','error_count':len(errors),'examples':errors[:20]}
  actual=sum((D(str(r['current_lifetime_revenue'])) for r in rows if type(r.get('current_lifetime_revenue')) in (int,float,D)),D(0));checks['reconciliation']={'status':'pass' if abs(actual-expected['facts']['reconciliation_total'])<=D('.01') else 'fail','actual':str(actual),'expected':str(expected['facts']['reconciliation_total'])}
 checks['workflow']=workflow or {'status':'pending','reason':'Workflow not supplied'};states=[x['status'] for x in checks.values()];reward=0 if 'fail' in states else None if any(s in states for s in ('pending','gap')) else 1
 return {'reward':reward,'status':'unknown' if reward is None else 'scored','checks':checks,'model_requests':0}
