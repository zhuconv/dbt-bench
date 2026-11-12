"""Original-input capacity audit: exact types/keys, lawful NTILE ties and bounded DOUBLE arithmetic."""
from pathlib import Path
from dataclasses import replace
from datetime import datetime,time
from fractions import Fraction
from collections import Counter,defaultdict
import argparse,hashlib,importlib.util,json,sys
HERE=Path(__file__).parent;TASK='deb__dbt-harbor-warehouse-capacity';MODELS={'orders':'main_staging.stg_capacity__orders','shipments':'main_staging.stg_capacity__shipments','hourly':'main_intermediate.int_capacity__hourly_volume','daily':'main_intermediate.int_capacity__daily_utilization','peak':'main_intermediate.int_capacity__peak_periods','fact':'main_marts.fct_warehouse_capacity','report':'main_marts.rpt_capacity_bottlenecks'}
DOUBLE={'grand_total','order_value','daily_order_value','theoretical_daily_capacity','utilization_rate','total_order_value','avg_hourly_orders','peak_load_factor','capacity_headroom_pct','rolling_7d_avg_orders'}
INTEGER={'order_hour','day_of_week','shipment_hour','order_count','shipment_count','daily_orders','daily_shipments','location_count','volume_percentile','is_peak_hour','total_orders','total_shipments','peak_hour_count','max_hourly_orders'}
DATES={'order_date','shipment_date','volume_date','utilization_date','capacity_date'}
def module(name):s=importlib.util.spec_from_file_location('capacity_'+name,HERE/(name+'.py'));m=importlib.util.module_from_spec(s);s.loader.exec_module(m);return m
def normalize(t):
 rows=[]
 for original in t.rows:
  r={n.lower():v for n,v in original.items()}
  for n in ['volume_date','utilization_date','capacity_date']:
   if type(r.get(n))is datetime and r[n].tzinfo is None and r[n].time()==time(0):r[n]=r[n].date()
  rows.append(r)
 return replace(t,columns=tuple(n.lower()for n in t.columns),physical_columns=tuple(n.lower()for n in t.physical_columns),sql_types={n.lower():v for n,v in t.sql_types.items()},physical_sql_types={n.lower():v for n,v in t.physical_sql_types.items()},rows=rows)
def model_path(relation):schema,model=relation.split('.');layer=schema.removeprefix('main_');return'models/'+layer+'/capacity/'+model+'.sql'
def run(request,root):
 sys.path[:0]=[str(root/'scripts'),str(root/'src')]
 from audit_tables import load_observer_table,audit_table,TablePolicy,decode
 from dbt_bench.comparison import Column,TableContract
 workflow=module('workflow');inputs=json.loads(workflow.read_ref(json.loads((HERE/'policy.json').read_text())['independent_inputs'],root));assert request['task_key']==TASK and request['initial_database_sha256']==inputs['initial_database_sha256'];expected,keys,details=module('derive_expected').derive(inputs,decode);floats=module('float_bounds');bounds=floats.derive_bounds(expected,details);tables={};parts={};diagnostics={}
 for role,relation in MODELS.items():
  try:tables[relation]=normalize(load_observer_table(request['snapshot'],task_key=TASK,database='/app/database/retail.duckdb',relation=relation,stage=request['stage'],root=root))
  except Exception as e:parts[role]={'status':'unknown','reward':None,'reason':str(e)}
 expected[MODELS['peak']]=[{**{k:v for k,v in r.items()if k!='order_value'},'volume_percentile':1,'is_peak_hour':0}for r in details['hourly']];keys[MODELS['peak']]=['warehouse_id','volume_date','volume_hour'];peak_errors=[];observed_buckets=defaultdict(Counter)
 if MODELS['peak']in tables:
  for r in tables[MODELS['peak']].rows:
   if {'warehouse_id','order_count','volume_percentile','is_peak_hour'}<=r.keys():
    if type(r['volume_percentile'])is int:observed_buckets[r['warehouse_id'],r['order_count']][r['volume_percentile']]+=1
    else:peak_errors.append('non-integer percentile')
    if type(r['volume_percentile'])is not int or r['is_peak_hour']!=int(r['volume_percentile']>=90):peak_errors.append('wrong peak threshold')
  if dict(observed_buckets)!={k:Counter(v)for k,v in details['allowed_bucket_multisets'].items()}:peak_errors.append('wrong exact NTILE multiset in warehouse/order-count tie groups')
  parts['ntile_ties']={'status':'scored','reward':float(not peak_errors),'errors':peak_errors,'scope':'Exact bucket multiset per equal-order-count tie group; any internal tie permutation is lawful. Flags exactly bucket>=90.'}
 if MODELS['fact']in tables:
  allocation=defaultdict(list);errors=[]
  for r in tables[MODELS['fact']].rows:
   wid=r.get('warehouse_id');day=r.get('capacity_date');count=r.get('peak_hour_count');policy=details['bounds'].get(wid,{}).get('daily',{}).get(day)
   if policy is None or type(count)is not int or not policy[0]<=count<=sum(policy):errors.append('infeasible daily peak count')
   else:allocation[wid].append(count)
  if any(sum(allocation.get(wid,[]))!=b['total_required_peak_slots']for wid,b in details['bounds'].items()):errors.append('incorrect total peak slots within warehouse')
  parts['daily_peak_allocation']={'status':'scored','reward':float(not errors),'errors':errors,'scope':'Daily peak counts can reflect any feasible unspecified tie assignment of the view during materialization. Per-day mandatory/boundary intervals and each warehouse total slots are exact; not compared to a separately evaluated peak view assignment.'}
 for role,relation in MODELS.items():
  if relation in tables:
   try:
    t=tables[relation];keynames=keys[relation];key=lambda r:tuple(r.get(n)for n in keynames);actual={key(r):r for r in t.rows};gold=[dict(r)for r in expected[relation]];accepted_numeric=0;outside=[]
    for row in gold:
     k=key(row);a=actual.get(k,{})
     if role=='peak':row['volume_percentile']=a.get('volume_percentile',1);row['is_peak_hour']=a.get('is_peak_hour',0)
     if role=='fact':row['peak_hour_count']=a.get('peak_hour_count',-1)
     for n,value in list(row.items()):
      if n in DOUBLE:
       interval=bounds.get((relation,k),{}).get(n,floats.exact(Fraction(value)));observed=a.get(n)
       if floats.admit(observed,interval):row[n]=observed;accepted_numeric+=1
       else:row[n]=float(value);outside.append({'key':[str(v)for v in k],'column':n,'actual':str(observed),'lower':str(interval[0]),'upper':str(interval[1])})
    cols=[];sql={}
    for n,value in gold[0].items():
     kind='numeric'if n in DOUBLE else'date'if n in DATES else'datetime'if n in('ordered_at','shipped_at')else'integer'if n in INTEGER or n=='volume_hour'else'text';cols.append(Column(n,kind));sql[n]=('DOUBLE',)if n in DOUBLE else('INTEGER',)if n in INTEGER and not(role=='peak'and n in ('order_count','shipment_count'))else('TINYINT','SMALLINT','INTEGER','BIGINT','HUGEINT','UTINYINT','USMALLINT','UINTEGER','UBIGINT','UHUGEINT')if n=='volume_hour'or(role=='peak'and n in ('order_count','shipment_count'))else('DATE',)if n in('order_date','shipment_date')else('DATE','TIMESTAMP')if n in DATES else('TIMESTAMP',)if kind=='datetime'else('VARCHAR',)
    policy=TablePolicy(TableContract(tuple(cols),keys=tuple(keynames),allow_extra_columns=True),sql);parts[role]=audit_table(t,policy,gold);diagnostics[role]={'numeric_cells_within_derived_bound':accepted_numeric,'outside_bound_count':len(outside),'outside_bound_examples':outside[:8]}
    if role=='report':
     order=all((a['utilization_rate'],a['capacity_date'])>=(b['utilization_rate'],b['capacity_date'])for a,b in zip(t.rows,t.rows[1:]));parts['report_order']={'status':'scored','reward':float(order and bool(t.rows)),'ordered_descending':order,'nonempty':bool(t.rows),'scope':'Unsorted actual SELECT preserves observable return order. No tertiary tie sort imposed.'}
   except Exception as e:parts[role]={'status':'unknown','reward':None,'reason':str(e)}
  schema,model=relation.split('.')
  try:parts['workflow:'+role]=workflow.workflow_check(request['workflow'],request['snapshot'],TASK,root,model,schema,model_path(relation))
  except Exception as e:parts['workflow:'+role]={'status':'unknown','reward':None,'reason':str(e)}
 try:parts['project_workflow']=module('project_workflow_v4').evaluate(request,root,MODELS)
 except Exception as e:parts['project_workflow']={'status':'unknown','reward':None,'reason':str(e)}
 rewards=[v['reward']for v in parts.values()];reward=0.0 if 0.0 in rewards else None if None in rewards else 1.0;paths=[HERE/n for n in ['check_v6.py','derive_expected.py','float_bounds.py','workflow.py','project_workflow_v4.py','policy.json']]+[root/'scripts/audit_tables.py',root/'src/dbt_bench/comparison.py'];return {'schema_version':1,'target':'audit','task_key':TASK,'status':'unknown'if reward is None else'scored','reward':reward,'components':parts,'numeric_diagnostics':diagnostics,'evidence':request,'code_refs':[{'path':str(p),'sha256':hashlib.sha256(p.read_bytes()).hexdigest()}for p in paths],'scope':'All original-input deterministic fields, exact declared types/key multiplicity, mathematically lawful unspecified NTILE ties and actual trusted workflow. DOUBLE comparison uses per-cell forward-error envelopes from finite source conversion/formula operations, not fitted global tolerances.'}
def main():
 p=argparse.ArgumentParser();p.add_argument('--request',type=Path,required=True);p.add_argument('--output',type=Path,required=True);p.add_argument('--repo-root',type=Path,required=True);a=p.parse_args()
 try:answer=run(json.loads(a.request.read_text()),a.repo_root)
 except Exception as e:answer={'target':'audit','status':'unknown','reward':None,'reason':str(e)}
 a.output.write_text(json.dumps(answer,indent=2)+'\n')
if __name__=='__main__':main()
