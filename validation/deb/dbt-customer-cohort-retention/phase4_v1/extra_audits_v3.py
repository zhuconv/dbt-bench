"""Adapters for four source-only audits; explicit original-input scope is retained."""
from pathlib import Path
from collections import Counter,defaultdict
from dataclasses import replace
from decimal import Decimal as D,ROUND_FLOOR,ROUND_CEILING,ROUND_HALF_UP,ROUND_HALF_DOWN
from datetime import date,datetime
import importlib.util,json,re
from audit_tables import audit_table,TablePolicy
from dbt_bench.comparison import Column,TableContract
R=Path.cwd();INTS=('TINYINT','SMALLINT','INTEGER','BIGINT','HUGEINT','UTINYINT','USMALLINT','UINTEGER','UBIGINT','UHUGEINT');NUMS=INTS+('FLOAT','REAL','DOUBLE')+tuple(f'DECIMAL({p},{s})' for p in range(1,39) for s in range(p+1))
def old(task,name):
 p=R/'validation/deb'/task/'audit_phase2_v1'/name;s=importlib.util.spec_from_file_location(task+'_'+name,p);m=importlib.util.module_from_spec(s);s.loader.exec_module(m);return m
def record_input(raw,mapping):
 return {'relations':{relation:{'schema':[{'name':c} for c in raw[k][0]],'rows':[[r[c] for c in raw[k][0]] for r in raw[k]]} for k,relation in mapping.items()}}
def ck(status,reason,**kw):return {'status':status,'reason':reason,**kw}
def finish(checks):
 ss=[x['status'] for x in checks.values()];reward=0 if 'fail' in ss else None if any(s in ss for s in ('pending','gap')) else 1
 return {'reward':reward,'status':'unknown' if reward is None else 'scored','checks':checks,'model_requests':0}
def table_check(o,rows,keys,*,types=None,exact_columns=False,materialization=None,not_null=()):
 if o.get('table') is None:return ck('fail' if o.get('exists') is False else 'pending','Required typed relation unavailable')
 t=o['table'];columns=tuple(rows[0]);types=types or {c:('VARCHAR',) if isinstance(rows[0][c],str) else ('DATE',) if type(rows[0][c]) is date else ('TIMESTAMP',) if type(rows[0][c]) is datetime else ('BOOLEAN',) if type(rows[0][c]) is bool else NUMS for c in columns}
 cs=[Column(c,'text' if types[c]==('VARCHAR',) else 'date' if types[c]==('DATE',) else 'datetime' if types[c]==('TIMESTAMP',) else 'boolean' if types[c]==('BOOLEAN',) else 'numeric') for c in columns]
 result=audit_table(t,TablePolicy(TableContract(tuple(cs),keys=tuple(keys),allow_extra_columns=not exact_columns),types,not_null=tuple(not_null),relation_types=tuple(materialization or ())),rows)
 return ck('pass' if result['reward']==1 else 'fail' if result['reward']==0 else 'pending','Full source-derived typed and keyed table comparison',details=result)
class SourceReader:
 def __init__(self,raw):self.raw=raw
 def execute(self,sql):
  if 'SELECT customer_id,first_name,last_name,email' in sql:self.rows=[tuple(r[c] for c in ('customer_id','first_name','last_name','email')) for r in self.raw['customers']]
  elif 'status,grand_total,ordered_at' in sql:self.rows=[tuple(r[c] for c in ('order_id','customer_id','status','grand_total','ordered_at')) for r in self.raw['orders']]
  elif 'ordered_at,grand_total,status' in sql:self.rows=[tuple(r[c] for c in ('order_id','customer_id','ordered_at','grand_total','status')) for r in self.raw['orders']]
  else:raise ValueError('Unexpected source projection: '+sql)
  return self
 def fetchall(self):return self.rows
def derive_for(task,raw):
 m=old(task,'derive_expected.py')
 if task=='dbt-fix-multi-touch-attribution':
  data=record_input(raw,{'sessions':'main.stg_ga__sessions','events':'main.stg_ga__events'});data['metadata']={'optional_joined':{'exists':bool(raw.get('joined')),'has_t2_event_value':bool(raw.get('joined')) and 't2_event_value' in raw['joined'][0]}};rows,proof=m.derive(data,lambda x:x);return {'rows':rows,'proof':proof,'facts':proof['conditions']}
 if task=='dbt-loyalty-points-analysis':
  data=record_input(raw,{'transactions':'main.LOYALTY_POINTS_TRANSACTIONS','programs':'main.LOYALTY_PROGRAMS','customers':'main.CUSTOMERS'});value=m.derive(data,lambda x:x);value['facts']=value['profile'];return value
 if task=='dbt-customer-order-analytics':
  rows,refs,facts=m.derive(SourceReader(raw));return {'rows':[dict(zip(m.COLS,r)) for r in rows],'raw':raw,'facts':facts}
 if task=='dbt-order-interval-metrics':
  values,facts=m.derive_intervals(SourceReader(raw));return {'outputs':{k.split('.',1)[1]:v for k,v in values.items()},'raw':raw,'facts':facts}
 raise ValueError(task)
def staged(o,raw,key,upper_status=False):
 t=o.get('table')
 if t is not None and t.physical_sql_types.get(key)!='VARCHAR':return ck('fail','Source identifier requires text SQL type',key=key,actual=t.physical_sql_types.get(key))
 if t is None:return ck('fail' if o.get('exists') is False else 'pending','Staging relation not captured')
 cleaned=[{k:(v.strip() if isinstance(v,str) else v) for k,v in r.items()} for r in raw]
 if upper_status:
  for r in cleaned:r['status']=r['status'].upper() if r.get('status') is not None else None
 want={r[key]:r for r in cleaned};got={r.get(key):r for r in t.rows};errors=[]
 if len(want)!=len(cleaned):return ck('gap','Actual source primary key duplicates require multimap review',raw_rows=len(cleaned),unique=len(want))
 for ident,row in got.items():
  if ident not in want:errors.append([ident,'unexpected key']);continue
  for c,v in row.items():
   if c in want[ident] and v!=want[ident][c]:errors.append([ident,c])
 return ck('pass' if not errors and len(got)==len(t.rows) and set(got)==set(want) else 'fail','All source keys and exposed raw fields retain associations after declared string cleaning',errors=errors[:10],actual=len(t.rows),expected=len(want))
def evaluate_extra(task,o,e,w):
 checks={}
 if task=='dbt-fix-multi-touch-attribution':required={'rpt_multi_touch_attribution_fixed':set(e['rows'][0])}
 elif task=='dbt-loyalty-points-analysis':required={'stg_lp_transactions':{'transaction_id'},'stg_lp_programs':{'program_id'},'stg_lp_customers':{'customer_id'},'int_loyalty_metrics':set(e['intermediate'][0]),'program_loyalty_summary':set(e['final'][0])}
 elif task=='dbt-customer-order-analytics':required={'stg_customers':{'customer_id'},'stg_orders':{'order_id'},'int_customer_order_metrics':set('customer_id order_count total_spend avg_order_value first_order_date last_order_date days_since_last_order'.split()),'dim_customer_tiers':set(e['rows'][0])}
 else:required={name:set(rows[0]) for name,rows in e['outputs'].items()}
 for name,columns in required.items():
  item=o.get(name,{})
  if item.get('table') is None:return finish({'required_relation':ck('fail' if item.get('exists') is False else 'pending','Required relation or trusted typed capture unavailable',model=name)})
  if not columns<=set(item['table'].columns) or any(not columns<=set(row) for row in item.get('rows',[])):return finish({'required_columns':ck('fail','Required contract column absent from actual relation',model=name,missing=sorted(columns-set(item['table'].columns)))})
 if task=='dbt-fix-multi-touch-attribution':
  name='rpt_multi_touch_attribution_fixed';item=o[name];target=[dict(r) for r in e['rows']];actual={tuple(r[k] for k in ('attribution_date','channel','medium','campaign','attribution_model')):r for r in item.get('rows',[])}
  for row in target:
   key=tuple(row[k] for k in ('attribution_date','channel','medium','campaign','attribution_model'));value=actual.get(key,{}).get('conversion_rate')
   if value in e['proof']['ratio_options'][key]:row['conversion_rate']=value
  types={c:('DATE',) if c=='attribution_date' else ('INTEGER',) if c in ('sessions','conversions','assist_sessions') else ('DECIMAL(12,2)',) if c=='attributed_revenue' else NUMS if c=='conversion_rate' else ('VARCHAR',) for c in target[0]}
  checks['full_original_input']=table_check(item,target,('attribution_date','channel','medium','campaign','attribution_model'),types=types,materialization=('TABLE','BASE TABLE'))
  checks['conditional_scope']=ck('pass','Actual original-input proof establishes one session per effective user and zero purchase rows: all five attribution models reduce independently to the same single-session indicator. Multi-session cases require separate fixtures.',facts=e['facts'])
 elif task=='dbt-loyalty-points-analysis':
  for role,name,key in [('transactions','stg_lp_transactions','transaction_id'),('programs','stg_lp_programs','program_id'),('customers','stg_lp_customers','customer_id')]:checks[role]=staged(o[name],e['stages'][role],key)
  checks['intermediate']=table_check(o['int_loyalty_metrics'],e['intermediate'],('program_id',))
  item=o['program_loyalty_summary'];t=item.get('table');target=[dict(r) for r in e['final']];actual={r['program_id']:r for r in item.get('rows',[])};presentation=[]
  for row in target:
   pid=row['program_id'];a=actual.get(pid,{});ex=e['exact'][pid];avg=a.get('avg_points_per_member');rate=a.get('redemption_rate');row['avg_points_per_member']=ex['integer_floor']
   if type(avg) is int and avg in (ex['integer_floor'],ex['integer_ceil']):row['avg_points_per_member']=avg
   if type(rate) in (int,float,D) and ex['rate'] is not None:
    value=D(str(rate));scale=max(0,-value.as_tuple().exponent)
    if scale<=18 and (value==ex['rate'] or type(rate) is float and rate==float(ex['rate']) or value in (ex['rate'].quantize(D(1).scaleb(-scale),rounding=ROUND_FLOOR),ex['rate'].quantize(D(1).scaleb(-scale),rounding=ROUND_CEILING))):row['redemption_rate']=rate
   presentation.append({'program_id':pid,'exact_average':str(ex['average']),'integer_bracket':[ex['integer_floor'],ex['integer_ceil']],'actual_average':avg,'exact_rate':str(ex['rate']),'actual_rate':str(rate)})
  types={c:('VARCHAR',) if c in ('program_id','program_name') else INTS if c=='avg_points_per_member' else NUMS for c in target[0]};checks['final']=table_check(item,target,('program_id',),types=types,materialization=('TABLE','BASE TABLE'),not_null=tuple(target[0]))
  checks['number_presentation']=ck('gap','Actual noninteger averages and nonterminating percentages were recalculated. Task requires integer average but gives no rounding mode; finite bracketing is tested without claiming a complete permitted presentation policy.',facts=presentation)
 elif task=='dbt-customer-order-analytics':
  raw=e['raw'];checks['customer_stage']=staged(o['stg_customers'],raw['customers'],'customer_id');checks['orders_stage']=staged(o['stg_orders'],raw['orders'],'order_id',True)
  target=[dict(r) for r in e['rows']];actual={r['customer_id']:r for r in o['dim_customer_tiers'].get('rows',[])}
  for row in target:
   cid=row['customer_id'];exact=e['facts']['exact_money_values'][cid]
   for c,index in [('total_spend','4'),('avg_order_value','5')]:
    value=exact[index];options=[value.quantize(D('.01'),rounding=r) for r in (ROUND_HALF_UP,ROUND_HALF_DOWN)] if value is not None else [None]
    if actual.get(cid,{}).get(c) in options:row[c]=actual[cid][c]
  checks['final']=table_check(o['dim_customer_tiers'],target,('customer_id',),materialization=('TABLE','BASE TABLE'),not_null=tuple(target[0]))
  groups=defaultdict(list)
  for r in raw['orders']:
   if r['status'] is not None and r['status'].strip().upper() not in ('CANCELLED','RETURNED','FAILED'):groups[r['customer_id'].strip() if r['customer_id'] is not None else None].append(r)
  inter=[];observed={r['customer_id']:r for r in o['int_customer_order_metrics'].get('rows',[])}
  for cid,rs in groups.items():
   amounts=[D(r['grand_total']) for r in rs if r['grand_total'] is not None];dates=[r['ordered_at'].date() for r in rs if r['ordered_at'] is not None];total=sum(amounts,D(0));avg=total/len(amounts) if amounts else None
   row={'customer_id':cid,'order_count':len(rs),'total_spend':total.quantize(D('.01'),rounding=ROUND_HALF_UP),'avg_order_value':avg.quantize(D('.01'),rounding=ROUND_HALF_UP) if avg is not None else None,'first_order_date':min(dates) if dates else None,'last_order_date':max(dates) if dates else None,'days_since_last_order':(date(2024,12,1)-max(dates)).days if dates else None}
   for c,value in [('total_spend',total),('avg_order_value',avg)]:
    options=[value.quantize(D('.01'),rounding=x) for x in (ROUND_HALF_UP,ROUND_HALF_DOWN)] if value is not None else [None]
    if observed.get(cid,{}).get(c) in options:row[c]=observed[cid][c]
   inter.append(row)
  checks['intermediate']=table_check(o['int_customer_order_metrics'],inter,('customer_id',))
  same=lambda rows:sorted(json.dumps(r,default=str,sort_keys=True) for r in rows)
  checks['source_alias']=ck('pass' if same(raw['customers'])==same(raw['main_customers']) and same(raw['orders'])==same(raw['main_orders']) else 'fail','Actual public main aliases and named schemas have identical full rows; original ORDERS.CUSTOMERS prose names an absent table. Alias correction is recorded, not silently guessed.')
 elif task=='dbt-order-interval-metrics':
  p=json.loads((R/'validation/deb'/task/'audit_phase2_v1/policy.json').read_text());wanted=e['outputs'];raw=e['raw'];money=defaultdict(lambda:D(0));counts=Counter()
  for r in raw['orders']:
   if r['ordered_at'] is not None and date(2024,1,1)<=r['ordered_at'].date()<=date(2024,12,31) and r['status'] is not None and r['status'].strip() not in ('CANCELLED','RETURNED','FAILED'):money[r['customer_id'].strip()]+=D(r['grand_total']);counts[r['customer_id'].strip()]+=1
  for name,rows in wanted.items():
   actual={tuple(r[k] for k in p['models'][name]['keys']):r for r in o[name].get('rows',[])};target=[dict(r) for r in rows];holes=[]
   for row in target:
    key=tuple(row[k] for k in p['models'][name]['keys']);got=actual.get(key,{})
    if name=='customer_order_intervals':
     cid=row['customer_id']
     for c in ('engagement_momentum','lifecycle_stage'):
      if row[c] is None:holes.append([cid,c]);row[c]=got.get(c)
     for col,exact in [('total_revenue',money[cid]),('avg_order_value',money[cid]/counts[cid])]:
      opts=[exact.quantize(D('.01'),rounding=rounding) for rounding in (ROUND_HALF_UP,ROUND_HALF_DOWN)]
      if got.get(col) in opts:row[col]=got[col]
   policy=p['models'][name];types={c:('VARCHAR',) if spec['kind']=='text' else ('DATE',) if spec['kind']=='date' else INTS if spec['kind']=='integer' else NUMS for c,spec in policy['columns'].items()};checks[name]=table_check(o[name],target,policy['keys'],types=types,exact_columns=policy['exact_columns'],materialization=('TABLE','BASE TABLE') if policy['require_table'] else None)
   if holes:checks['fallback_policy']=ck('gap','Actual source replay identifies unspecified classification fallback cells; values retained only for comparison of other columns, never treated as validated.',cells=holes)
 checks['workflow']=w;return finish(checks)
