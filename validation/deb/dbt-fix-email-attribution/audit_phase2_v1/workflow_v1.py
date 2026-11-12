"""Task policy over trusted process/file captures, never candidate attestations."""
from datetime import datetime
import yaml

def chk(s,r,**x):return {'status':s,'reason':r,**x}
def evaluate(kind,f):
 f=f or{};parts={n:chk('pending','Trusted actual capture unavailable')for n in ['model_delivery','profile','trusted_build','backend']};project='/app/dbt_project'if kind=='email'else'/app/dbt_models_duckdb';name='rpt_email_attribution_fixed'if kind=='email'else'rpt_paid_search_attribution_fixed';profile='dbt_project'if kind=='email'else'retail_dw_master';files=f.get('files',{});manifest=f.get('manifest',{});proc=f.get('process',{});path='models/marts/marketing/'+name+'.sql';nodes=[(k,n)for k,n in manifest.get('nodes',{}).items()if n.get('resource_type')=='model'and n.get('name')==name]
 if f.get('trusted_fresh_artifact_binding'):
  ok=path in files and len(nodes)==1 and nodes[0][1].get('original_file_path')==path and nodes[0][1].get('schema','').lower()=='analytics'and nodes[0][1].get('config',{}).get('materialized')=='table';parts['model_delivery']=chk('pass'if ok else'fail','Actual submitted model path, fresh node schema and table materialization; source SQL spelling is unrestricted')
 if f.get('trusted_process_complete'):
  try:
   config=yaml.safe_load(files['dbt_project.yml']);profiles=yaml.safe_load(files['profiles.yml']);pp=profiles[profile];target=pp['outputs'][pp['target']];ok=config['profile']==profile and target['type']=='duckdb'and target['path']=='/app/database/retail.duckdb'and proc['environment']['DBT_PROFILES_DIR']==project;parts['profile']=chk('pass'if ok else'fail','Declared profile name, DuckDB path and actual project-local profile environment')
  except(KeyError,ValueError,TypeError,yaml.YAMLError):parts['profile']=chk('pending'if any('{{'in files.get(n,'')for n in ['dbt_project.yml','profiles.yml'])else'fail','Captured configuration unresolved or required profile missing')
  results={r['unique_id']:r for r in f.get('run_results',{}).get('results',[])};ok=len(nodes)==1 and proc.get('returncode')==0 and proc.get('cwd')==project and proc.get('command',[])[:2]==['dbt','run']and f.get('trusted_fresh_artifact_binding')is True and results.get(nodes[0][0],{}).get('status')=='success';parts['trusted_build']=chk('pass'if ok else'fail','Actual successful declared dbt subprocess, fresh matching invocation/model artifacts and captured final compiled SELECT')
  if kind=='paid':parts['backend']=chk('pass'if proc['environment'].get('DB_TYPE')=='duckdb'else'fail','Actual live selected backend matches pinned DuckDB environment; no undeclared echo-before-code requirement added')
 if kind=='email':
  parts['backend']=chk('pending','Actual live echo-before-install proof unavailable');b=f.get('backend_before_install')
  if b:
   shell=b['shell'];finished=datetime.fromisoformat(shell['finished_at_utc'].replace('Z','+00:00'));bound=datetime.fromisoformat(b['install_after_utc'].replace('Z','+00:00'));ok=shell.get('exit_code')==0 and shell.get('command')=='echo "$DB_TYPE"'and shell.get('output')in['duckdb','duckdb\n','duckdb\\n']and finished<bound and b.get('sequential_trusted_driver_verified')is True;parts['backend']=chk('pass'if ok else'fail','Actual Qwen-shell live echo completed before trusted submission install bound')
  u=f.get('upstream');parts['upstream_preparation']=chk('pending','Actual original upstream source/process captures unavailable')
  if u and u.get('trusted_process_verified'):
   rr={x['unique_id']:x for x in u['run_results'].get('results',[])};nodes=u['manifest'].get('nodes',{});wanted=['stg_ga__sessions','stg_ga__events','int_sessions_events_joined'];ok=u['process']['returncode']==0 and u['finished_before_candidate_build']and u['original_required_source_files_unchanged']and u['manifest']['metadata']['invocation_id']==u['run_results']['metadata']['invocation_id']and u['invocation_changed_from_initial']
   for n in wanted:
    ids=[uid for uid,v in nodes.items()if v.get('resource_type')=='model'and v.get('name')==n];ok &=len(ids)==1 and rr.get(ids[0],{}).get('status')=='success'
   parts['upstream_preparation']=chk('pass'if ok else'fail','Actual provided upstream models ran before candidate build with original source bytes preserved. Environment-bound project path and missing documented legacy path are recorded separately.',path_discrepancy=u.get('path_discrepancy'))
 else:
  macros=[m for m in manifest.get('macros',{}).values()if m.get('name')=='generate_schema_name'and m.get('original_file_path')=='macros/utils/generate_schema_name.sql'];parts['schema_macro']=chk('pending'if not f.get('trusted_fresh_artifact_binding')else'pass'if 'macros/utils/generate_schema_name.sql'in files and macros else'fail','Required schema macro file and definition in fresh manifest; no source hash whitelist')
 states=[x['status']for x in parts.values()];return chk('fail'if'fail'in states else'pending'if'pending'in states else'pass','Trusted task workflow checks',details=parts)
