"""Trusted dbt workflow facts; values alone never establish source execution."""
import yaml
from datetime import datetime
TASK_KIND='ltv'
PROJECT='/app/dbt_models_duckdb'if TASK_KIND=='ltv'else'/app/dbt_project'
PROFILE='retail_dw_master'if TASK_KIND=='ltv'else'dbt_project'
MODELS=['customer_ltv']if TASK_KIND=='ltv'else['account_balances','trial_balance','out_of_balance_entries','unusual_balance_accounts','period_summary']
def ck(status,reason,**kw):return dict(status=status,reason=reason,**kw)
def evaluate(f):
 f=f or{};parts={k:ck('pending','Trusted actual evidence unavailable')for k in ['profile','models','build','backend']};files=f.get('files',{})
 if f.get('trusted_complete_inventory'):
  try:
   config=yaml.safe_load(files['dbt_project.yml']);pr=yaml.safe_load(files['profiles.yml']);v=pr[PROFILE];v=v['outputs'][v['target']];ok=config['profile']==PROFILE and v['type']=='duckdb'and v['path']=='/app/database/retail.duckdb';ok &= f.get('environment',{}).get('DBT_PROFILES_DIR')==PROJECT;parts['profile']=ck('pass'if ok else'fail','Actual project-local required profile/database/environment')
  except(KeyError,TypeError,ValueError,yaml.YAMLError):parts['profile']=ck('fail','Required actual local profile missing/invalid')
 if f.get('trusted_profile_absent'):parts['profile']=ck('fail','Actual required project-local profiles.yml absent')
 if f.get('trusted_model_absent'):parts['models']=ck('fail','Actual required named dbt model is absent after closed recorded deletion/build',missing=f['trusted_model_absent'])
 if f.get('trusted_fresh_process'):
  manifest=f['manifest'];rr={r['unique_id']:r for r in f['run_results']['results']};ok=bool(f.get('fresh_source_invocation_binding'));nodes=[]
  for name in MODELS:
   found=[(u,n)for u,n in manifest['nodes'].items()if n.get('resource_type')=='model'and n['name']==name]
   ok &=len(found)==1
   if len(found)!=1:continue
   uid,n=found[0];ok &= n.get('schema')==('main'if TASK_KIND=='ltv'else'gl_analytics')and rr.get(uid,{}).get('status')=='success'and n.get('original_file_path')in files;nodes.append(n.get('original_file_path'))
   if TASK_KIND=='ltv':ok &=n.get('config',{}).get('materialized')=='table'
  parts['models']=ck('pass'if ok else'fail','Trusted installed model files bound to actual manifest/run results',model_paths=nodes);runs=f.get('commands',[]);good=any(r.get('command',[])[:2]in[['dbt','run'],['dbt','build']]and r.get('returncode')==0 for r in runs);parts['build']=ck('pass'if ok and good else'fail','Actual trusted dbt process plus fresh artifacts, not candidate self-report')
 if f.get('backend_before_install'):
  b=f['backend_before_install'];s=b['shell'];ok=s.get('exit_code')==0 and s.get('output','').strip()=='duckdb'and s.get('command')=='echo "$DB_TYPE"';ok &=datetime.fromisoformat(s['finished_at_utc'].replace('Z','+00:00'))<datetime.fromisoformat(b['install_after_utc'].replace('Z','+00:00'));ok &=b.get('sequential_trusted_driver_verified')is True;parts['backend']=ck('pass'if ok else'fail','Actual backend shell observation preceding source installation')
 if TASK_KIND=='ltv':
  parts['upstream_staging']=ck('pending','Initial/final complete upstream staging inventory not captured')
  if 'staging_unchanged'in f:parts['upstream_staging']=ck('pass'if f['staging_unchanged']else'gap','Actual before/after staging source hashes; changed comments alone are not assumed business errors. A nonidentical source requires semantic review.')
 states=[x['status']for x in parts.values()];return ck('fail'if'fail'in states else'gap'if'gap'in states else'pending'if'pending'in states else'pass','Actual dbt workflow',details=parts)
