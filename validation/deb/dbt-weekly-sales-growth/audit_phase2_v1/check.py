"""Original-input exact Weekly audit; trusted workflow is a separate component."""
from pathlib import Path
from dataclasses import replace
from collections import Counter
import argparse,hashlib,importlib.util,json,sys
HERE=Path(__file__).parent;TASK='deb__dbt-weekly-sales-growth';SCHEMA='sales_analytics';MODELS={'stage':'stg_orders__weekly','intermediate':'int_weekly_sales','final':'weekly_sales_growth'}
PATHS={'stage':'models/staging/stg_orders__weekly.sql','intermediate':'models/intermediate/int_weekly_sales.sql','final':'models/marts/weekly_sales_growth.sql'}
def module(name):
 s=importlib.util.spec_from_file_location('weekly_'+name,HERE/(name+'.py'));m=importlib.util.module_from_spec(s);s.loader.exec_module(m);return m

def normalize(t):return replace(t,columns=tuple(n.lower()for n in t.columns),physical_columns=tuple(n.lower()for n in t.physical_columns),sql_types={n.lower():v for n,v in t.sql_types.items()},physical_sql_types={n.lower():v for n,v in t.physical_sql_types.items()},rows=[{n.lower():v for n,v in r.items()}for r in t.rows])
def bag(t):return Counter(tuple((n,type(v).__name__,v)for n,v in sorted(r.items()))for r in t.rows)
def run(request,root):
 sys.path[:0]=[str(root/'scripts'),str(root/'src')]
 from audit_tables import load_observer_table,audit_table,TablePolicy,decode
 from dbt_bench.comparison import Column,TableContract
 workflow=module('workflow');policy=json.loads((HERE/'policy.json').read_text());inputs=json.loads(workflow.read_ref(policy['independent_inputs'],root));assert request['task_key']==TASK and request['initial_database_sha256']==inputs['initial_database_sha256'];expected=module('derive_expected').derive(inputs,decode);parts={}
 for role,model in MODELS.items():
  try:
   t=normalize(load_observer_table(request['snapshot'],task_key=TASK,database='/app/database/retail.duckdb',relation=SCHEMA+'.'+model,stage=request['stage'],root=root));gold=expected[model];columns=[];types={}
   for n,v in gold[0].items():
    kind='text'if n in('order_id','customer_id','growth_status','is_best_week')else'datetime'if n=='ordered_at'else'date'if n=='week_start'else'integer'if n in('week_number','order_count','unique_customers')else'numeric';columns.append(Column(n,kind))
    types[n]=('VARCHAR',)if kind=='text'else('TIMESTAMP',)if kind=='datetime'else('DATE',)if kind=='date'else('INTEGER',)if kind=='integer'else('DECIMAL(8,2)',)if n=='revenue_growth_pct'else(t.physical_sql_types.get(n,'missing'),)if n=='grand_total'else('DECIMAL(12,2)',)
   key='order_id'if role=='stage'else'week_start';contract=TableContract(tuple(columns),keys=(key,),ordered=role=='final',allow_extra_columns=role!='final');result=audit_table(t,TablePolicy(contract,types,relation_types=('BASE TABLE',)if role=='final'else('BASE TABLE','VIEW')),gold)
   if role=='final':
    result['checks']['physical_column_order']=t.physical_columns==tuple(gold[0]);result['checks']['physical_row_order']=list(t.rows)==sorted(t.rows,key=lambda r:r.get('week_start'))
    if not result['checks']['physical_column_order']or not result['checks']['physical_row_order']:result.update(status='scored',reward=0.0)
   parts[role]=result
  except Exception as e:parts[role]={'status':'unknown','reward':None,'reason':str(e)}
  try:parts['model:'+role]=workflow.workflow_check(request['workflow'],request['snapshot'],TASK,root,model,SCHEMA,PATHS[role],role=='final')
  except Exception as e:parts['model:'+role]={'status':'unknown','reward':None,'reason':str(e)}
 try:parts['project_workflow']=module('project_workflow').evaluate(request,root,MODELS)
 except Exception as e:parts['project_workflow']={'status':'unknown','reward':None,'reason':str(e)}
 try:
  iterations=request['iterations'];assert len(iterations)==2;checks={}
  for role,model in MODELS.items():
   pair=[normalize(load_observer_table(q,task_key=TASK,database='/app/database/retail.duckdb',relation=SCHEMA+'.'+model,stage=json.loads(workflow.read_ref(q,root))['stage'],root=root))for q in iterations];a,b=pair;checks[model]=bag(a)==bag(b)and a.physical_columns==b.physical_columns and a.physical_sql_types==b.physical_sql_types and a.relation_type==b.relation_type and(role!='final'or a.rows==b.rows)
  parts['idempotency']={'status':'scored','reward':float(all(checks.values())),'checks':checks,'scope':'Two actual trusted successive builds, complete typed rows and declared final row/column order.'}
 except Exception as e:parts['idempotency']={'status':'unknown','reward':None,'reason':str(e)}
 rewards=[p['reward']for p in parts.values()];reward=0.0 if 0.0 in rewards else None if None in rewards else 1.0;paths=[HERE/n for n in('check.py','workflow.py','project_workflow.py','derive_expected.py','raw_derivation_v1.py','policy.json')]+[root/'scripts/audit_tables.py',root/'src/dbt_bench/comparison.py'];return {'schema_version':1,'target':'audit','task_key':TASK,'status':'unknown'if reward is None else'scored','reward':reward,'components':parts,'evidence':request,'code_refs':[{'path':str(p),'sha256':hashlib.sha256(p.read_bytes()).hexdigest()}for p in paths],'scope':'Fixed original raw orders independently recomputed; explicit schema/type/grain/ordered mart plus trusted actual workflow/idempotency. Missing evidence stays unknown.'}
def main():
 p=argparse.ArgumentParser();p.add_argument('--request',type=Path,required=True);p.add_argument('--output',type=Path,required=True);p.add_argument('--repo-root',type=Path,required=True);a=p.parse_args()
 try:answer=run(json.loads(a.request.read_text()),a.repo_root)
 except Exception as e:answer={'target':'audit','status':'unknown','reward':None,'reason':str(e)}
 a.output.write_text(json.dumps(answer,indent=2)+'\n')
if __name__=='__main__':main()
