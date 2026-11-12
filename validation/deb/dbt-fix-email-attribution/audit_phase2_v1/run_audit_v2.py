"""Read trusted hash-pinned snapshots and independently recompute current inputs."""
import argparse,hashlib,json
from pathlib import Path
import yaml
from audit_tables import decode,CaptureError,load_observer_table
from expected_v1 import derive
from audit_v1 import evaluate,check
from workflow_v1 import evaluate as evaluate_workflow
KIND='email'
TASK='deb__dbt-fix-email-attribution'
PROJECT='/app/dbt_project'
MODEL='rpt_email_attribution_fixed'
def checked(ref,root):
 p=root/ref['path'];b=p.read_bytes()
 if hashlib.sha256(b).hexdigest()!=ref['sha256']:raise CaptureError('Changed evidence '+str(p))
 return b

def snapshot(spec,root):
 ref=spec['candidate_snapshot'];p=root/ref['path'];s=json.loads(checked(ref,root));assert s['task_key']==TASK and s['stage']==spec['candidate_stage'];out=[x for x in s['outputs']if x['specification']['relation']=='analytics.'+MODEL];assert len(out)==1;x=out[0];rows=None;table=None;limits=[]
 if not x.get('error'):
  name=x['file'];assert Path(name).name==name;data=checked({'path':str(p.parent/name),'sha256':x['sha256']},root);cols=[k['name']for k in x['schema']];assert len(cols)==len({k.lower()for k in cols});lines=[json.loads(r)for r in data.splitlines()];assert len(lines)==x['rows']and all(len(r)==len(cols)for r in lines);rows=[dict(zip(cols,map(decode,r)))for r in lines]
  try:table=load_observer_table(ref,task_key=TASK,database='/app/database/retail.duckdb',relation='analytics.'+MODEL,stage=spec['candidate_stage'],root=root)
  except CaptureError as e:limits.append(str(e))
 files={}
 for f in s.get('files',[]):
  if f.get('exists')and'text'in f:
   assert hashlib.sha256(f['text'].encode()).hexdigest()==f['sha256'];files[f['path']]=f['text']
  elif f.get('exists')is False:files[f['path']]=None
 failures=[]
 for required in [PROJECT+'/models/marts/marketing/'+MODEL+'.sql']+([PROJECT+'/macros/utils/generate_schema_name.sql']if KIND=='paid'else[]):
  if required in files and files[required]is None:failures.append('Explicitly captured required file absent: '+required)
 if PROJECT+'/profiles.yml'in files:
  text=files[PROJECT+'/profiles.yml'];profile='dbt_project'if KIND=='email'else'retail_dw_master'
  if text is None:failures.append('Required project profile file absent')
  elif profile not in(yaml.safe_load(text)or{}):failures.append('Required profile name absent: '+profile)
 if KIND=='email':
  for path,text in files.items():
   if text is None and path.endswith('/models/staging/ga/stg_ga__events.sql'):failures.append('Actual supplied required upstream staging file absent: '+path)
 workflow=check('fail','; '.join(failures))if failures else None
 relation_type=x.get('relation_metadata',[{}])[0].get('table_type')if len(x.get('relation_metadata',[]))==1 else None
 projection={v['name'].lower():v['type']for v in x['sql_schema']}if x.get('sql_schema')else None
 error=x.get('error')or{};missing=error.get('type')=='CatalogException'and('Table with name '+MODEL+' does not exist'in error.get('message','')or'Schema with name analytics does not exist'in error.get('message',''))
 return rows,table,workflow,relation_type,projection,limits,False if missing else None

def run(spec,root):
 try:
  assert spec['task_key']==TASK;raw=json.loads(checked(spec['original_input'],root));expected,stats=derive(KIND,raw['rows'],raw.get('joined_revenue_available',False));rows,table,wf,typ,projection,limits,exists=snapshot(spec,root)
  if spec.get('trusted_workflow'):
   trusted=evaluate_workflow(KIND,json.loads(checked(spec['trusted_workflow'],root)))
   if wf is None or wf['status']!='fail':wf=trusted
  result=evaluate(KIND,rows,expected,structured=table,relation_exists=exists,relation_type=typ,workflow=wf,projection_types=projection);result.update(task_key=TASK,statistics=stats,capture_limitations=limits,input_refs={k:v for k,v in spec.items()if isinstance(v,dict)and'sha256'in v});return result
 except (OSError,ValueError,KeyError,TypeError,AssertionError,ArithmeticError)as exc:return {'task_key':TASK,'status':'unknown','reward':None,'reason':str(exc),'error_type':type(exc).__name__,'model_requests':0,'native_rewards_modified':False}
if __name__=='__main__':
 p=argparse.ArgumentParser();p.add_argument('--spec',required=True);p.add_argument('--output',required=True);p.add_argument('--root',default='.');a=p.parse_args();r=run(json.loads(Path(a.spec).read_text()),Path(a.root));Path(a.output).write_text(json.dumps(r,indent=2,default=str)+'\n');print(json.dumps({'task_key':TASK,'audit_reward':r['reward']}))
