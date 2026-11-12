"""Hash-bound formal-capture loading and minimal actual dbt workflow checks."""
from pathlib import Path
from dataclasses import replace
from datetime import datetime
import json,re,yaml
from dbt_bench import io
from audit_tables import load_observer_table,decode,CaptureError
from phase4_capture import verify_trial
ROOT=Path.cwd()
STAGES={'agent-submission':'agent/audit-submission','verifier-before':'verifier/mechanical-before','verifier-after':'verifier/mechanical-after'}
def ck(status,reason,**facts):return {'status':status,'reason':reason,**facts}
def raw_rows(verified,phase='initial'):
 cap=verified['capture'];out={}
 for name,row in verified[phase]['raw'].items():
  cols=[c.lower() for c in row['columns']]
  if len(cols)!=len(set(cols)):raise ValueError('Ambiguous case-folded columns')
  out[name]=[dict(zip(cols,map(decode,json.loads(line)))) for line in cap.runtime(row).read_text().splitlines()]
  if len(out[name])!=row['rows']:raise ValueError('Raw row count changed')
 return out

def outputs(verified,stage):
 key=verified['request']['task_key'];trial=verified['capture'].trial;snapshot=io.evidence_ref(trial/STAGES[stage]/'snapshot.json',ROOT);value=io.read_json(ROOT/snapshot['path']);result={}
 if value['task_key']!=key or value['stage']!=stage:raise ValueError('Wrong candidate task/stage')
 for item in value['outputs']:
  spec=item['specification'];name=spec['relation'].split('.',1)[1]
  if item.get('error'):
   error=item['error'];missing=error['type']=='CatalogException' and 'does not exist' in error['message']
   result[name]={'exists':False} if missing else {'capture_error':error}
   continue
  table=load_observer_table(snapshot,task_key=key,database=spec['database'],relation=spec['relation'],stage=stage,root=ROOT)
  if len(set(c.lower() for c in table.columns))!=len(table.columns):raise ValueError('Ambiguous case-folded outputs')
  table=replace(table,columns=tuple(c.lower() for c in table.columns),physical_columns=tuple(c.lower() for c in table.physical_columns),sql_types={k.lower():v for k,v in table.sql_types.items()},physical_sql_types={k.lower():v for k,v in table.physical_sql_types.items()},rows=[{k.lower():v for k,v in row.items()} for row in table.rows])
  result[name]={'rows':table.rows,'table':table,'relation_type':table.relation_type}
 return snapshot,result

def env_value(value,environment):
 if not isinstance(value,str):return value
 if '{{' not in value:return value
 # Resolve the ordinary documented env_var form only; never execute candidate Jinja.
 m=re.fullmatch(r'''\s*\{\{\s*env_var\(\s*['\"]([^'\"]+)['\"]\s*(?:,\s*['\"]([^'\"]*)['\"]\s*)?\)\s*\}\}\s*''',value)
 if not m:raise ValueError('Unsupported profile expression requires separate review')
 return environment.get(m[1]) if environment.get(m[1]) is not None else m[2]

def workflow(verified,policy):
 req=verified['request'];execution=verified['execution'];project=req['project_root'];files={k:p.read_text() for k,p in verified['final_files'].items() if p.suffix in ('.sql','.yml','.yaml')};checks={}
 checks['project']=ck('pass' if project==policy['project_root'] else 'fail','Actual declared project root; no substitute project accepted',actual=project)
 try:
  cfg=yaml.safe_load(files['dbt_project.yml']);profiles=yaml.safe_load(files['profiles.yml']);name=cfg['profile'];pf=profiles[name];active=pf['outputs'][pf['target']];env=execution['environment'];db=env_value(active['path'],env);schema=env_value(active.get('schema','main'),env)
  ok=active['type']=='duckdb' and db==policy['database'] and env.get('DBT_PROFILES_DIR')==project
  if policy.get('profile'):ok &= name==policy['profile']
  if policy.get('profile_schema'):ok &= schema==policy['profile_schema']
  checks['profile']=ck('pass' if ok else 'fail','Project-local profile resolved against recorded actual environment',profile=name,database=db,schema=schema)
 except (KeyError,TypeError,ValueError,yaml.YAMLError) as e:checks['profile']=ck('fail','Required local profile missing or invalid',error=str(e))
 if policy.get('forbid_project_schema'):
  checks['project_schema']=ck('fail' if '+schema' in files.get('dbt_project.yml','') else 'pass','Instruction allows schema only in profile')
 build=[c for c in verified['commands'] if c['run']['command'][1] in ('run','build') and '--project-dir' not in c['run']['command']]
 if not build:checks['build']=ck('fail','No actual completed final-project model build')
 else:
  command=build[-1];m=command['manifest'];rr={r['unique_id']:r for r in command['run_results']['results']};bad=[];details=[]
  for model in policy['models']:
   nodes=[(u,n) for u,n in m['nodes'].items() if n.get('resource_type')=='model' and n.get('name')==model]
   if len(nodes)!=1:bad.append([model,'not one manifest node']);continue
   uid,node=nodes[0];path=node['original_file_path'];installed=files.get(path)
   ok=installed is not None and node.get('raw_code','').strip()==installed.strip() and rr.get(uid,{}).get('status')=='success'
   if policy.get('required_paths',{}).get(model):ok &= path==policy['required_paths'][model]
   if not ok:bad.append([model,'source/manifest/success/path mismatch'])
   details.append({'model':model,'path':path,'schema':node.get('schema'),'materialized':node.get('config',{}).get('materialized'),'depends_on':node.get('depends_on',{})})
  checks['build']=ck('fail' if bad else 'pass','Required actual SQL sources are bound to fresh successful model invocations',errors=bad,models=details)
 echo=verified['backend_shell'];checks['backend']=ck('pass' if echo.get('exit_code')==0 and echo.get('output','').strip()=='duckdb' and echo.get('command')=='echo "$DB_TYPE"' else 'fail','Actual locked Qwen shell backend read preceded source installation')
 commands=[r['command'] for r in execution['runs']]
 if policy.get('deps_required'):
  first=next((i for i,x in enumerate(commands) if x[1] in ('run','build')),len(commands));has=any(x[1]=='deps' for x in commands[:first]);checks['dependencies']=ck('pass' if has else 'fail','Explicit task deps-before-run requirement',actual_commands=commands)
 if policy.get('tests_required'):checks['dbt_test']=ck('pass' if any(c[1]=='test' for c in commands) else 'fail','Actual required dbt test execution')
 if policy.get('upstream_models'):
  before=[]
  for command in verified['commands']:
   if '--project-dir' not in command['run']['command']:continue
   m=command['manifest'];rr={r['unique_id']:r for r in command['run_results']['results']};before.extend(n['name'] for u,n in m['nodes'].items() if n.get('resource_type')=='model' and rr.get(u,{}).get('status')=='success')
  checks['upstream']=ck('pass' if set(policy['upstream_models'])<=set(before) else 'fail','Required original reference models actually built before final project',built=before)
 reward=0 if any(x['status']=='fail' for x in checks.values()) else 1
 return {'status':'scored','reward':reward,'checks':checks,'model_requests':0,'scope':'Actual source/process/profile/backend provenance only; business values and specific SQL-form rules evaluated separately.'}
