"""Interpret trusted process/file captures, not candidate-provided attestations."""
from datetime import datetime
from pathlib import PurePosixPath
import yaml

def check(s,r,**x):return {'status':s,'reason':r,**x}
def evaluate(kind,facts=None):
 f=facts or {};names=['backend_before_code','fresh_project','profile','model_files','source_definitions','trusted_build'];out={n:check('pending','Required trusted capture unavailable')for n in names};files=f.get('files',{});probed=set(f.get('probed_paths',[]));profile='retail_dw_master'if kind=='geographic'else'dbt_project';schema='geographic_analytics'if kind=='geographic'else'risk_analytics';models=['stg_customer_addresses','stg_orders','fct_state_customers']if kind=='geographic'else['stg_orders_risk','stg_customers_risk','customer_risk_scores'];manifest=f.get('manifest',{});process=f.get('process',{})
 if f.get('trusted_initial_capture_complete'):
  exists=f.get('initial_project_exists');out['fresh_project']=check('pass'if exists is False else'fail'if f.get('initial_project_contains_dbt_files')else'pending','Actual fresh-project initial capture; existing empty directory alone is not forbidden')
 if any(p in probed and p not in files for p in ['dbt_project.yml','profiles.yml']):out['profile']=check('fail','Actually probed project/config profile file absent')
 elif all(p in files for p in ['dbt_project.yml','profiles.yml']):
  try:
   cfg=yaml.safe_load(files['dbt_project.yml']);profiles=yaml.safe_load(files['profiles.yml']);p=profiles[profile];target=p['outputs'][p['target']];ok=cfg['profile']==profile and target['type']=='duckdb'and target['path']==f['database']and target['schema']==schema
   env=process.get('environment',{});command=process.get('command',[]);profile_env=env.get('DBT_PROFILES_DIR')=='/app/dbt_project';profile_arg=any(x=='--profiles-dir'and i+1<len(command)and command[i+1]=='/app/dbt_project'for i,x in enumerate(command))
   if f.get('trusted_process_complete'):ok &= profile_env if kind=='geographic'else(profile_env or profile_arg)
   out['profile']=check('pass'if ok and f.get('trusted_process_complete')else'pending'if '{{'in files['profiles.yml']or not f.get('trusted_process_complete')else'fail','Parsed task-declared profile/schema/database and actual process environment/arguments; unresolved templates stay pending')
  except (ValueError,TypeError,KeyError,AttributeError,yaml.YAMLError)as exc:out['profile']=check('pending'if '{{'in files['profiles.yml']else'fail','Captured project/profile could not resolve: '+str(exc))
 nodes={name:[(uid,n)for uid,n in manifest.get('nodes',{}).items()if n.get('resource_type')=='model'and n.get('name')==name]for name in models}
 if f.get('trusted_manifest_complete'):
  good=True
  for name,items in nodes.items():
   if len(items)!=1:good=False;continue
   path=items[0][1].get('original_file_path','');wanted='models/staging/'if name.startswith('stg_')else'models/marts/';good &= path.startswith(wanted)and path in files and path in probed and '..'not in PurePosixPath(path).parts
  out['model_files']=check('pass'if good else'fail','Fresh trusted manifest model names and actual local source-file captures in staging/marts layers')
 # Directly probed absent known named model is a definite error even if a later manifest is incomplete.
 for p in f.get('required_model_paths',[]):
  if p in probed and p not in files:out['model_files']=check('fail','Actually probed required model file absent: '+p)
 if f.get('trusted_manifest_complete'):
  sources=manifest.get('sources',{});pairs={(str(s.get('schema')).lower(),str(s.get('identifier',s.get('name'))).lower())for s in sources.values()};defs=[]
  for p,text in files.items():
   if p.endswith(('.yml','.yaml'))and p.startswith('models/'):
    try:
     data=yaml.safe_load(text)
     if isinstance(data,dict)and data.get('sources'):defs.append(p)
    except yaml.YAMLError:pass
  required=[{('customer','customer_addresses')},{('orders','orders')}]if kind=='geographic'else[{('main','customers'),('customer','customers')},{('main','orders'),('orders','orders')}]
  out['source_definitions']=check('pass'if defs and all(pairs&allowed for allowed in required)else'fail','Actual source YAML plus freshly resolved manifest source schema/identifiers; source SQL spelling is not whitelisted')
 if f.get('trusted_process_complete'):
  run=f.get('run_results',{});results={r['unique_id']:r for r in run.get('results',[])};ok=process.get('exit_code')==0 and process.get('cwd')=='/app/dbt_project'and process.get('dbt_command_executed')is True and process.get('artifacts_bound_to_process')is True
  for name,items in nodes.items():
   if len(items)!=1:ok=False;continue
   uid,n=items[0];ephemeral=n.get('config',{}).get('materialized')=='ephemeral';ok &= ephemeral or(results.get(uid,{}).get('status')=='success'and n.get('schema')==schema)
  out['trusted_build']=check('pass'if ok else'fail','Actual successful dbt process and fresh invocation-bound models/results; ephemeral staging is allowed')
 if f.get('trusted_backend_before_install'):
  b=f['trusted_backend_before_install'];e=b['shell'];a=datetime.fromisoformat(e['finished_at_utc'].replace('Z','+00:00'));z=datetime.fromisoformat(b['source_install_after_utc'].replace('Z','+00:00'));ok=e.get('exit_code')==0 and e.get('command')=='echo "$DB_TYPE"'and e.get('output')in ['duckdb','duckdb\n','duckdb\\n']and a<z and b.get('controlled_sequential_runner_verified')is True and b.get('initial_project_exists')is False;out['backend_before_code']=check('pass'if ok else'fail','Actual echo finishes before trusted source-install lower bound; new reconstruction scope')
 if f.get('trusted_candidate_events'):
  events=f['trusted_candidate_events'];writes=[e for e in events if e.get('event')=='task_model_written'];echoes=[e for e in events if e.get('event')=='live_echo_finished'and e.get('stdout')=='duckdb\n'and e.get('returncode')==0]
  if writes:out['backend_before_code']=check('pass'if echoes and min(e['monotonic_ns']for e in echoes)<min(e['monotonic_ns']for e in writes)else'fail','Actual candidate event order. Earlier context probe may have read DB_TYPE; only requested echo order is assessed')
 if kind=='risk':
  stages=f.get('compiled_staging_rows')
  if stages is None:out['trimmed_staging']=check('pending','Actual compiled staging string values not supplied')
  else:out['trimmed_staging']=check('pass'if all(not isinstance(v,str)or v==v.strip()for rows in stages.values()for r in rows for v in r.values())else'fail','Actual compiled staging string fields are trimmed; no requirement for reference SQL spelling')
 return {'checks':out}
