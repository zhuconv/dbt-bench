"""Declared original-input Basket audit, including actual workflow and idempotency."""
from pathlib import Path
from dataclasses import replace
from datetime import date,datetime
from decimal import Decimal
import argparse,hashlib,importlib.util,json,re,sys
HERE=Path(__file__).parent;TASK='deb__dbt-basket-composition-analysis';DB='/app/database/retail.duckdb'
KEYS={'stg_order_baskets':('order_id',),'int_customer_basket_patterns':('customer_id',),'int_basket_transitions':('from_category','to_category'),'int_customer_cohorts':('cohort_category',),'basket_size_analysis':('basket_size_category',),'transition_summary':('category',)}
def module(path):
 path=Path(path);s=importlib.util.spec_from_file_location('basket_'+path.stem,path);m=importlib.util.module_from_spec(s);s.loader.exec_module(m);return m
def contract(name,schema,policy):
 from dbt_bench.comparison import Column,TableContract
 def kind(typ):return 'integer'if typ in('INTEGER','BIGINT','HUGEINT')else'boolean'if typ=='BOOLEAN'else'date'if typ=='DATE'else'datetime'if typ.startswith('TIMESTAMP')else'numeric'if typ.startswith('DECIMAL')or typ in('DOUBLE','FLOAT')else'text'
 return TableContract(tuple(Column(n,kind(t))for n,t in schema),KEYS[name],allow_extra_columns=policy['models'][name]['allow_extra_columns'])
def legacy_rows(table,ct):
 rows=[]
 for original in table['rows']:
  row={k.lower():v for k,v in original.items()}
  for col in ct.columns:
   value=row.get(col.name)
   if value is None:continue
   if col.kind=='numeric'and type(value)in(str,int,float,Decimal):row[col.name]=Decimal(str(value))
   elif col.kind=='integer'and type(value)in(str,int,float,Decimal):
    n=Decimal(str(value))
    if n.is_finite()and n==n.to_integral_value():row[col.name]=int(n)
   elif col.kind=='date'and isinstance(value,str):row[col.name]=date.fromisoformat(value)
   elif col.kind=='datetime'and isinstance(value,str):row[col.name]=datetime.fromisoformat(value)
  rows.append(row)
 return rows
def run(request,root):
 sys.path[:0]=[str(root/'scripts'),str(root/'src')]
 from audit_tables import load_observer_table,audit_table,TablePolicy
 from dbt_bench.comparison import compare_tables
 wf=module(HERE/'workflow.py');policy=json.loads((HERE/'policy.json').read_text());schemas=json.loads((HERE/'schemas.json').read_text())['relations'];inputs=json.loads(wf.read_ref(policy['independent_inputs'],root));assert request['task_key']==TASK and request['initial_database_sha256']==inputs['initial_database_sha256'];wf.read_ref(policy['replay_engine'],root);replay=module(root/policy['replay_engine']['path']);expected,raw=module(HERE/'derive_expected.py').derive_basket(replay.Replay(inputs));parts={};legacy=None
 if request.get('legacy_case_proof'):
  legacy=json.loads(wf.read_ref(request['legacy_case_proof'],root));assert legacy['task_key']==TASK and legacy['raw_observations']['sources']==json.loads(json.dumps(raw,default=str))['sources']
 for relation,rows in expected.items():
  schema,name=relation.split('.');ct=contract(name,schemas[relation],policy)
  try:
   if legacy is not None:
    prediction=legacy_rows(legacy['after'][relation],ct);comp=compare_tables(rows,prediction,ct);parts[name]={'status':'scored','reward':float(comp.equal),'reason':comp.reason,'scope':'Existing actual hash-bound original-input value capture; old source/type workflow is not certified.'};continue
   t=load_observer_table(request['snapshot'],task_key=TASK,database=DB,relation=relation,stage=request['stage'],root=root);t=replace(t,columns=tuple(n.lower()for n in t.columns),physical_columns=tuple(n.lower()for n in t.physical_columns),sql_types={n.lower():v for n,v in t.sql_types.items()},physical_sql_types={n.lower():v for n,v in t.physical_sql_types.items()},rows=[{n.lower():v for n,v in row.items()}for row in t.rows]);types={n:((t.physical_sql_types[n],)if n=='total_quantity'and re.fullmatch(r'DECIMAL\(\d+,\d+\)',t.physical_sql_types.get(n,''))else(typ,))for n,typ in schemas[relation]};parts[name]=audit_table(t,TablePolicy(ct,types,not_null=ct.keys,relation_types=tuple(policy['models'][name]['physical_kinds'])),expected_rows=rows)
   order=True
   if policy['models'][name]['physical_column_order']:order=t.physical_columns==tuple(n for n,typ in schemas[relation])
   roworder=policy['specified_row_order'].get(name)
   if roworder:
    observed=[r[roworder['column']]for r in t.rows];order=order and observed==sorted(observed,reverse=roworder['descending'])
   parts[name]['prescribed_order']=order
   if not order:parts[name].update(status='scored',reward=0.0,reason='Required physical column order or explicit result ordering violated')
  except Exception as e:parts[name]={'status':'unknown','reward':None,'reason':str(e)}
 if legacy is None:
  for relation in expected:
   schema,name=relation.split('.')
   try:parts['build:'+name]=wf.workflow_check(request['workflow'],request['snapshot'],TASK,root,name,schema)
   except Exception as e:parts['build:'+name]={'status':'unknown','reward':None,'reason':str(e)}
  try:parts['project_workflow']=module(HERE/'project_workflow.py').evaluate(request,root,policy['models'])
  except Exception as e:parts['project_workflow']={'status':'unknown','reward':None,'reason':str(e)}
 else:parts['workflow']={'status':'unknown','reward':None,'reason':'Historical value capture does not certify current declared source/workflow or SQL type interface.'}
 try:
  repeated=request.get('repeat_execution');details={}
  if legacy is not None:
   assert legacy.get('first_nonidempotent_output') is not None
   for relation in expected:
    name=relation.split('.')[-1];ct=contract(name,schemas[relation],policy);a=legacy_rows(legacy['first_nonidempotent_output'][relation],ct);b=legacy_rows(legacy['after'][relation],ct);c=compare_tables(a,b,ct);details[name]={'equal':c.equal,'reason':c.reason}
  else:
   snapshots=[json.loads(wf.read_ref(repeated[k],root))for k in('first_snapshot','second_snapshot')]
   for relation in expected:
    name=relation.split('.')[-1];tables=[load_observer_table(repeated[k],task_key=TASK,database=DB,relation=relation,stage=s['stage'],root=root)for k,s in zip(('first_snapshot','second_snapshot'),snapshots)];ct=contract(name,schemas[relation],policy);a,b=[[{n.lower():v for n,v in r.items()}for r in t.rows]for t in tables];c=compare_tables(a,b,ct);details[name]={'equal':c.equal,'reason':c.reason}
  parts['idempotency']={'status':'scored','reward':float(all(v['equal']for v in details.values())),'relations':details,'scope':'Complete97 declared values compared after two actually observed dbt runs with unchanged raw inputs; exact Decimal/keys and multiplicity retained.'}
 except Exception as e:parts['idempotency']={'status':'unknown','reward':None,'reason':str(e)or'No captured repeat for this historic case'}
 scores=[p['reward']for p in parts.values()];reward=0.0 if 0.0 in scores else None if None in scores else 1.0;paths=[HERE/n for n in('check.py','derive_expected.py','schemas.json','policy.json','workflow.py','project_workflow.py')]+[root/policy['replay_engine']['path'],root/'scripts/audit_tables.py',root/'src/dbt_bench/comparison.py']
 return {'schema_version':1,'task_key':TASK,'target':'audit','status':'unknown'if reward is None else'scored','reward':reward,'components':parts,'raw_observations':json.loads(json.dumps(raw,default=str)),'evidence':request,'code_refs':[{'path':str(p),'sha256':hashlib.sha256(p.read_bytes()).hexdigest()}for p in paths],'scope':'All six original-input models and97 declared fields, explicit types and42/8 mart column order, required result order, actual deps/build/source/profile/backend chronology and two-run idempotency. No gold or native rewards used as expected.'}
def main():
 p=argparse.ArgumentParser();p.add_argument('--request',type=Path,required=True);p.add_argument('--output',type=Path,required=True);p.add_argument('--repo-root',type=Path,required=True);a=p.parse_args()
 try:r=run(json.loads(a.request.read_text()),a.repo_root)
 except Exception as e:r={'target':'audit','status':'unknown','reward':None,'reason':str(e)}
 a.output.write_text(json.dumps(r,indent=2)+'\n')
if __name__=='__main__':main()
