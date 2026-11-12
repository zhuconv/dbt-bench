"""Audit immutable declared captures; missing/corrupt evidence is unknown."""
from pathlib import Path
from dataclasses import replace
import json,hashlib,re,argparse
from audit_tables import decode,load_observer_table,CaptureError
from expected_v2 import derive,COLS
from audit_v2 import evaluate
from workflow_v2 import evaluate as workflow_evaluate
KIND='funnel'
TASK='deb__dbt-sales-funnel-analysis'
SCHEMA='funnel_analytics'
NAMES=['int_session_funnel', 'funnel_conversion_rates', 'funnel_dropoff_analysis']
def checked(r,root):
 p=root/r['path'];b=p.read_bytes()
 if hashlib.sha256(b).hexdigest()!=r['sha256']:raise CaptureError('Changed capture '+str(p))
 return b
def dec(x):return decode(x)if isinstance(x,dict)and'$type'in x else{k:dec(v)for k,v in x.items()}if isinstance(x,dict)else[dec(v)for v in x]if isinstance(x,list)else x
def run(spec,root):
 try:
  assert spec['task_key']==TASK;raw=json.loads(checked(spec['original_input'],root))
  for r in raw['source_refs']:checked(r,root)
  expected=derive(dec(raw['rows']));sr=spec['candidate_snapshot'];snap=json.loads(checked(sr,root));assert snap['task_key']==TASK and snap['stage']==spec['candidate_stage'];outputs={};limits=[]
  for name in NAMES:
   rel=SCHEMA+'.'+name;items=[i for i in snap['outputs']if i['specification']['database']=='/app/database/retail.duckdb'and i['specification']['relation'].lower()==rel];assert len(items)==1;i=items[0];o={}
   if i.get('error'):
    e=i['error'];required=COLS[name]if KIND=='funnel'else COLS;missing=re.findall(r'Referenced column "([^"]+)" not found',e.get('message',''))if e.get('type')=='BinderException'else[]
    if missing and set(c.lower()for c in missing)<=set(required):o['missing_required_columns']=missing
    if e.get('type')=='CatalogException'and ('Table with name '+name+' does not exist'in e['message']or'Schema with name '+SCHEMA+' does not exist'in e['message']):o['exists']=False
   else:
    cols=[c['name'].lower()for c in i['schema']];assert len(cols)==len(set(cols));fn=i['file'];assert Path(fn).name==fn;values=[json.loads(l)for l in checked({'path':str((root/sr['path']).parent/fn),'sha256':i['sha256']},root).splitlines()];assert len(values)==i['rows']and all(len(v)==len(cols)for v in values);o['rows']=[dict(zip(cols,map(decode,v)))for v in values]
    try:
     t=load_observer_table(sr,task_key=TASK,database='/app/database/retail.duckdb',relation=i['specification']['relation'],stage=spec['candidate_stage'],root=root);assert len({c.lower()for c in t.physical_columns})==len(t.physical_columns);o['table']=replace(t,columns=tuple(c.lower()for c in t.columns),sql_types={k.lower():v for k,v in t.sql_types.items()},rows=[{k.lower():v for k,v in row.items()}for row in t.rows],physical_columns=tuple(c.lower()for c in t.physical_columns),physical_sql_types={k.lower():v for k,v in t.physical_sql_types.items()})
    except CaptureError as e:limits.append({'relation':rel,'reason':str(e)})
   outputs[name]=o
  wf=None
  if spec.get('trusted_workflow'):
   facts=json.loads(checked(spec['trusted_workflow'],root))
   for r in facts.get('evidence',[]):checked(r,root)
   wf=workflow_evaluate(facts)
  a=evaluate(outputs,expected,wf);a.update(task_key=TASK,input_refs={k:v for k,v in spec.items()if isinstance(v,dict)and'sha256'in v},capture_limitations=limits,facts=expected['profile']);return a
 except(OSError,KeyError,TypeError,ValueError,ArithmeticError,AssertionError,AttributeError)as e:return dict(task_key=TASK,status='unknown',reward=None,error_type=type(e).__name__,reason=str(e),model_requests=0,native_rewards_modified=False)
if __name__=='__main__':
 p=argparse.ArgumentParser();p.add_argument('--spec',required=True);p.add_argument('--output',required=True);p.add_argument('--root',default='.');a=p.parse_args();r=run(json.loads(Path(a.spec).read_text()),Path(a.root));Path(a.output).write_text(json.dumps(r,indent=2,default=str)+'\n');print(json.dumps({'task_key':TASK,'status':r['status'],'reward':r['reward']}))
