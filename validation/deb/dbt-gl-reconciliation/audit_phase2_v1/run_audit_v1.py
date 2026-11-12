"""Strict retained observation replay; lowercase identifier normalization is case-insensitive SQL naming only."""
from pathlib import Path
from dataclasses import replace
import hashlib,json,argparse
from audit_tables import decode,CaptureError,load_observer_table
from expected_v1 import derive
from audit_v1 import evaluate
from workflow_v1 import evaluate as workflow_evaluate
KIND='gl';TASK='deb__dbt-customer-ltv-fix'if KIND=='ltv'else'deb__dbt-gl-reconciliation';NAMES=['customer_ltv']if KIND=='ltv'else['account_balances','trial_balance','out_of_balance_entries','unusual_balance_accounts','period_summary'];SCHEMA='main'if KIND=='ltv'else'gl_analytics'
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
   relation=SCHEMA+'.'+name;xs=[x for x in snap['outputs']if x['specification']['database']=='/app/database/retail.duckdb'and x['specification']['relation']==relation];assert len(xs)==1;item=xs[0];o={}
   if item.get('error'):
    error=item['error']
    if error.get('type')=='CatalogException'and ('Table with name '+name+' does not exist'in error['message']or'Schema with name '+SCHEMA+' does not exist'in error['message']):o['exists']=False
   else:
    cols=[x['name'].lower()for x in item['schema']];assert len(cols)==len(set(cols));fn=item['file'];assert Path(fn).name==fn;values=[json.loads(l)for l in checked({'path':str((root/sr['path']).parent/fn),'sha256':item['sha256']},root).splitlines()];assert len(values)==item['rows']and all(len(v)==len(cols)for v in values);o['rows']=[dict(zip(cols,map(decode,v)))for v in values]
    try:
     t=load_observer_table(sr,task_key=TASK,database='/app/database/retail.duckdb',relation=relation,stage=spec['candidate_stage'],root=root);assert len({c.lower()for c in t.physical_columns})==len(t.physical_columns);o['table']=replace(t,columns=tuple(c.lower()for c in t.columns),sql_types={k.lower():v for k,v in t.sql_types.items()},rows=[{k.lower():v for k,v in r.items()}for r in t.rows],physical_columns=tuple(c.lower()for c in t.physical_columns),physical_sql_types={k.lower():v for k,v in t.physical_sql_types.items()})
    except CaptureError as e:limits.append({'relation':relation,'reason':str(e)})
   outputs[name]=o
  w=None
  if spec.get('trusted_workflow'):
   wf=json.loads(checked(spec['trusted_workflow'],root))
   for r in wf.get('evidence',[]):checked(r,root)
   w=workflow_evaluate(wf)
  if spec.get('trusted_relation_metadata'):
   md=json.loads(checked(spec['trusted_relation_metadata'],root))
   for r in md.get('evidence',[]):checked(r,root)
   for name,v in md['relations'].items():outputs[name]['relation_type']=v
  answer=evaluate(outputs['customer_ltv']if KIND=='ltv'else outputs,expected,w);answer.update(task_key=TASK,input_refs={k:v for k,v in spec.items()if isinstance(v,dict)and'sha256'in v},facts=expected['facts'],capture_limitations=limits);return answer
 except(OSError,KeyError,TypeError,ValueError,ArithmeticError,AssertionError,AttributeError)as e:return dict(task_key=TASK,status='unknown',reward=None,error_type=type(e).__name__,reason=str(e),model_requests=0,native_rewards_modified=False)
if __name__=='__main__':
 p=argparse.ArgumentParser();p.add_argument('--spec',required=True);p.add_argument('--output',required=True);p.add_argument('--root',default='.');a=p.parse_args();answer=run(json.loads(Path(a.spec).read_text()),Path(a.root));Path(a.output).write_text(json.dumps(answer,indent=2,default=str)+'\n');print(json.dumps({'task_key':TASK,'reward':answer['reward']}))
