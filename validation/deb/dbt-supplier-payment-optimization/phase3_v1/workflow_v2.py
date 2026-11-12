"""Audit trusted source/execution capture; candidate output values do not establish workflow."""
import yaml
from profile_env import resolve
from datetime import datetime
TASK_KIND='payment'
PROJECT='/app/dbt_models_duckdb'
PROFILE='retail_dw_master'
SCHEMA='main'
MODELS=['supplier_payment_optimization']
def ck(status,reason,**kw):return dict(status=status,reason=reason,**kw)
def evaluate(f):
 f=f or{};parts={k:ck('pending','Trusted actual capture unavailable')for k in ['profile','models','build','backend']};files=f.get('files',{})
 if f.get('trusted_complete_inventory'):
  try:
   config=yaml.safe_load(files['dbt_project.yml']);p=resolve(yaml.safe_load(files['profiles.yml']),f['environment']);v=p[PROFILE];v=v['outputs'][v['target']];ok=config['profile']==PROFILE and v['type']=='duckdb'and v['path']=='/app/database/retail.duckdb'and str(v.get('schema','main')).lower()==SCHEMA and f.get('environment',{}).get('DBT_PROFILES_DIR')==PROJECT
   parts['profile']=ck('pass'if ok else'fail','Actual required project-local profile/database/schema/environment')
  except(KeyError,TypeError,ValueError,yaml.YAMLError):parts['profile']=ck('fail','Actual required profile invalid or absent')
 if f.get('trusted_fresh_process'):
  man=f['manifest'];rr={r['unique_id']:r for r in f['run_results']['results']};ok=bool(f.get('fresh_source_invocation_binding'));paths=[]
  for name in MODELS:
   found=[(u,n)for u,n in man['nodes'].items()if n.get('resource_type')=='model'and n['name']==name];ok &=len(found)==1
   if len(found)!=1:continue
   uid,node=found[0];path=node.get('original_file_path');ok &=path in files and node['raw_code'].strip()==files.get(path,'').strip() and (rr.get(uid,{}).get('status')=='success'or(node.get('config',{}).get('materialized')=='ephemeral'and node.get('compiled_code')))and node.get('schema','').lower()==SCHEMA;paths.append(path)
   if TASK_KIND=='payment':ok &=path=='models/marts/supplier_payment_optimization.sql'
  parts['models']=ck('pass'if ok else'fail','Required actual model names and paths bound to successful fresh invocation',model_paths=paths)
  good=any(r.get('returncode')==0 and r.get('command',[])[:2]in[['dbt','run'],['dbt','build']]for r in f.get('commands',[]));parts['build']=ck('pass'if ok and good else'fail','Trusted executed dbt process with installed source and fresh artifacts')
 if f.get('backend_before_install'):
  b=f['backend_before_install'];s=b['shell'];ok=s.get('exit_code')==0 and s.get('output','').strip()=='duckdb'and s.get('command')=='echo "$DB_TYPE"'and b.get('sequential_trusted_driver_verified')is True;ok &=datetime.fromisoformat(s['finished_at_utc'].replace('Z','+00:00'))<datetime.fromisoformat(b['install_after_utc'].replace('Z','+00:00'));parts['backend']=ck('pass'if ok else'fail','Actual shell backend check preceding code installation')
 for name in ['source_lineage']+(['single_model','staging_refs','weekofyear']if TASK_KIND=='payment'else['date_difference']):parts[name]=f.get(name,ck('pending','Actual compiled/source clause inspection unavailable'))
 for name,proof in f.get('closed_case_facts',{}).items():
  if name in parts:parts[name]=proof
 states=[v['status']for v in parts.values()];return ck('fail'if'fail'in states else'gap'if'gap'in states else'pending'if'pending'in states else'pass','Trusted actual task workflow',details=parts)
