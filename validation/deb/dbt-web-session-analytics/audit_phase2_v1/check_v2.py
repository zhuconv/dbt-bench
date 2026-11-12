"""Original-input Web audit. Undeclared fact velocity precision remains explicit unknown."""
from pathlib import Path
from dataclasses import replace
from decimal import Decimal
from collections import Counter
import argparse,hashlib,importlib.util,json,re,sys
HERE=Path(__file__).parent;TASK='deb__dbt-web-session-analytics';SCHEMA='web_analytics';MODELS={'fact':'fct_session_quality','device':'rpt_session_summary','segment':'rpt_visitor_segments'};KEYS={'fact':'session_id','device':'device_type','segment':'visitor_segment'}
def module(name):s=importlib.util.spec_from_file_location('web_'+name,HERE/(name+'.py'));m=importlib.util.module_from_spec(s);s.loader.exec_module(m);return m
def normalize(t):
 rows=[]
 for old in t.rows:
  r={n.lower():v for n,v in old.items()}
  for n in ['is_bounce','is_converted','is_returning_visitor']:
   if n in r and(type(r[n])is bool or type(r[n])in(int,float,Decimal)and Decimal(str(r[n])).is_finite()and r[n]in(0,1)):r[n]=int(r[n])
  rows.append(r)
 return replace(t,columns=tuple(n.lower()for n in t.columns),physical_columns=tuple(n.lower()for n in t.physical_columns),sql_types={n.lower():v for n,v in t.sql_types.items()},physical_sql_types={n.lower():v for n,v in t.physical_sql_types.items()},rows=rows)
def bag(t):return Counter(tuple((n,type(v).__name__,v)for n,v in sorted(r.items()))for r in t.rows)
def run(request,root):
 sys.path[:0]=[str(root/'scripts'),str(root/'src')]
 from audit_tables import load_observer_table,audit_table,TablePolicy,decode
 from dbt_bench.comparison import Column,TableContract
 workflow=module('workflow');policy=json.loads((HERE/'policy.json').read_text());inputs=json.loads(workflow.read_ref(policy['independent_inputs'],root));assert request['task_key']==TASK and request['initial_database_sha256']==inputs['initial_database_sha256'];expected,details=module('derive_expected').derive(inputs,decode);raw_index={r['session_id']:r for r in details['raw']};parts={};tables={}
 for role,model in MODELS.items():
  try:
   t=normalize(load_observer_table(request['snapshot'],task_key=TASK,database='/app/database/retail.duckdb',relation=SCHEMA+'.'+model,stage=request['stage'],root=root));tables[model]=t;gold=[dict(r)for r in expected[model]];key=KEYS[role];actual={r[key]:r for r in t.rows if key in r}
   for r in gold:
    for n,allowed in details['rounding_options'].get((model,r[key]),{}).items():
     value=actual.get(r[key],{}).get(n)
     if type(value)in(int,float,Decimal)and Decimal(str(value)).is_finite()and Decimal(str(value))in allowed:r[n]=Decimal(str(value))
   columns=[];types={}
   for n,v in gold[0].items():
    kind='text'if n in('session_id','visitor_id','device_type','quality_tier','velocity_category','visitor_segment')else'datetime'if n=='session_start'else'numeric';columns.append(Column(n,kind));actual_type=t.physical_sql_types.get(n,'missing')
    types[n]=('VARCHAR',)if kind=='text'else('TIMESTAMP',)if kind=='datetime'else(actual_type,)if re.fullmatch(r'(?:U?TINYINT|U?SMALLINT|U?INTEGER|U?BIGINT|U?HUGEINT|DOUBLE|FLOAT|DECIMAL\(\d+,\d+\))',actual_type)or n.startswith('is_')and actual_type=='BOOLEAN'else('NUMERIC_SQL_REQUIRED',)
   contract=TableContract(tuple(columns),keys=(key,),allow_extra_columns=True);parts[role]=audit_table(t,TablePolicy(contract,types),gold)
   if role=='fact':
    missing='engagement_velocity'not in t.columns;wrong=[]
    for r in t.rows:
     original=raw_index.get(r.get('session_id'))
     if original is None:continue
     value=r.get('engagement_velocity');finite=type(value)in(int,float,Decimal)and Decimal(str(value)).is_finite()
     if not finite or original['duration_seconds']<60 and value!=0:wrong.append({'session_id':r['session_id'],'actual':str(value),'duration':original['duration_seconds'],'rule':'finite numeric; exact0 for short sessions'})
    parts['fact_velocity']={'status':'scored'if missing or wrong else'unknown','reward':0.0 if missing or wrong else None,'known_errors':wrong[:10],'known_error_count':len(wrong),'reason':'Explicit short-duration zero and finite numeric interface checked. Fact velocity precision is not declared; executed four-decimal and unrounded probes are investigated separately. No arbitrary tolerance or full-fact correctness claim.'}
  except Exception as e:parts[role]={'status':'unknown','reward':None,'reason':str(e)}
  try:parts['model:'+role]=workflow.workflow_check(request['workflow'],request['snapshot'],TASK,root,model,SCHEMA)
  except Exception as e:parts['model:'+role]={'status':'unknown','reward':None,'reason':str(e)}
 try:parts['project_workflow']=module('project_workflow').evaluate(request,root,MODELS)
 except Exception as e:parts['project_workflow']={'status':'unknown','reward':None,'reason':str(e)}
 try:
  assert len(request['iterations'])==2;checks={}
  for model in MODELS.values():
   a,b=[normalize(load_observer_table(s,task_key=TASK,database='/app/database/retail.duckdb',relation=SCHEMA+'.'+model,stage=json.loads(workflow.read_ref(s,root))['stage'],root=root))for s in request['iterations']];checks[model]=bag(a)==bag(b)and a.physical_sql_types==b.physical_sql_types and a.relation_type==b.relation_type and set(a.physical_columns)==set(b.physical_columns)
  parts['idempotency']={'status':'scored','reward':float(all(checks.values())),'checks':checks,'scope':'Two actual successive builds; full typed rows, schema and relation kind; no undeclared row/column order obligation.'}
 except Exception as e:parts['idempotency']={'status':'unknown','reward':None,'reason':str(e)}
 try:parts['projected_values']=module('projection_scope').compare(request,root,expected,details)
 except Exception as e:parts['projected_values']={'status':'unknown','reward':None,'reason':str(e)}
 rewards=[p['reward']for p in parts.values()];reward=0.0 if 0.0 in rewards else None if None in rewards else 1.0;paths=[HERE/n for n in ['check_v2.py','projection_scope.py','workflow.py','project_workflow.py','derive_expected.py','policy.json']]+[root/'scripts/audit_tables.py',root/'src/dbt_bench/comparison.py'];return {'schema_version':1,'target':'audit','task_key':TASK,'status':'unknown'if reward is None else'scored','reward':reward,'components':parts,'evidence':request,'code_refs':[{'path':str(p),'sha256':hashlib.sha256(p.read_bytes()).hexdigest()}for p in paths],'scope':'Original-session independent finite recomputation, keyed tables and actual workflow; full fact velocity precision unresolved, never upgraded by native score.'}
def main():
 p=argparse.ArgumentParser();p.add_argument('--request',type=Path,required=True);p.add_argument('--output',type=Path,required=True);p.add_argument('--repo-root',type=Path,required=True);a=p.parse_args()
 try:answer=run(json.loads(a.request.read_text()),a.repo_root)
 except Exception as e:answer={'target':'audit','status':'unknown','reward':None,'reason':str(e)}
 a.output.write_text(json.dumps(answer,indent=2)+'\n')
if __name__=='__main__':main()
