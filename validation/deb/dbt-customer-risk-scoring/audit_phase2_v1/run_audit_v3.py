"""CLI over root-owned hash-pinned evidence; candidate claims cannot supply trust."""
import argparse,hashlib,json
from pathlib import Path
from audit_tables import CaptureError,decode,load_observer_table
from expected import derive
from audit_v5 import evaluate
from workflow_v2 import evaluate as workflow_evaluate
KIND = 'risk'
TASK = 'deb__dbt-customer-risk-scoring'
SCHEMA = 'risk_analytics'
def checked(v,root):
 p=root/v['path'];b=p.read_bytes()
 if hashlib.sha256(b).hexdigest()!=v['sha256']:raise CaptureError('Hash mismatch: '+str(p))
 return b

def load_snapshot(reference,stage,root):
 p=root/reference['path'];s=json.loads(checked(reference,root))
 if s['task_key']!=TASK or s['stage']!=stage:raise CaptureError('Wrong capture task/stage')
 rows={};structured={};errors={}
 for v in s['outputs']:
  relation=v['specification']['relation']
  if not relation.startswith(SCHEMA+'.'):continue
  name=relation.split('.')[-1]
  if name in rows:raise CaptureError('Duplicate captured relation')
  if v.get('error'):errors[name]=v['error'];continue
  if Path(v['file']).name!=v['file']:raise CaptureError('Unsafe rows path')
  raw=checked({'path':str(p.parent/v['file']),'sha256':v['sha256']},root);cols=[r['name']for r in v['schema']];lines=[json.loads(x)for x in raw.splitlines()]
  if len(cols)!=len(set(c.lower()for c in cols))or len(lines)!=v['rows']or any(not isinstance(r,list)or len(r)!=len(cols)for r in lines):raise CaptureError('Bad row inventory')
  rows[name]=[dict(zip(cols,map(decode,r)))for r in lines]
  try:structured[name]=load_observer_table(reference,task_key=TASK,database=v['specification']['database'],relation=relation,stage=stage,root=root)
  except CaptureError as exc:errors[name]=str(exc)
 return rows,structured,errors

def run(spec,root):
 try:
  if spec['task_key']!=TASK:raise CaptureError('Wrong audit task')
  raw=json.loads(checked(spec['original_input'],root));expected,statistics=derive(raw['rows']);rows,structured,errors=load_snapshot(spec['candidate_snapshot'],spec['candidate_stage'],root);workflow=None
  if spec.get('trusted_workflow'):workflow=json.loads(checked(spec['trusted_workflow'],root))
  if spec.get('trusted_compiled_outputs'):
   compiled=json.loads(checked(spec['trusted_compiled_outputs'],root))
   if not compiled.get('bound_to_trusted_build'):raise CaptureError('Compiled outputs lack trusted fresh-build binding')
   logical={name:[dict(zip(c['columns'],map(decode,r)))for r in c['rows']]for name,c in compiled['models'].items()}
   # Stage semantics can be checked even for legitimate ephemeral staging.
   for name,value in logical.items():
    if name.startswith('stg_'):rows[name]=value
   if workflow is not None:workflow['compiled_staging_rows']={n:r for n,r in logical.items()if n.startswith('stg_')}
  wf=workflow_evaluate(KIND,workflow)
  repeated=None
  if spec.get('repeat_snapshot'):repeated=load_snapshot(spec['repeat_snapshot'],spec['repeat_stage'],root)[0]
  result=evaluate(KIND,rows,expected,structured=structured,workflow=wf,repeated=repeated,relation_types=spec.get('trusted_relation_types'));result.update(task_key=TASK,statistics=statistics,capture_limitations=errors,input_refs={k:v for k,v in spec.items()if isinstance(v,dict)and 'sha256'in v});return result
 except (OSError,ValueError,KeyError,TypeError,AttributeError,ArithmeticError)as exc:return {'task_key':TASK,'status':'unknown','reward':None,'reason':str(exc),'error_type':type(exc).__name__,'model_requests':0,'native_rewards_modified':False}
if __name__=='__main__':
 p=argparse.ArgumentParser();p.add_argument('--spec',required=True);p.add_argument('--output',required=True);p.add_argument('--root',default='.');a=p.parse_args();out=run(json.loads(Path(a.spec).read_text()),Path(a.root));Path(a.output).write_text(json.dumps(out,indent=2,default=str)+'\n');print(json.dumps({'task_key':TASK,'status':out['status'],'audit_reward':out['reward']}))
