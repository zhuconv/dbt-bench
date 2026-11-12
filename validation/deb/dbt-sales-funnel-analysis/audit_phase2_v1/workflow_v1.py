"""Trusted captured invocation/workflow checks, separate from numeric semantics."""
import yaml
from datetime import datetime
KIND='funnel'
MODELS=['int_session_funnel', 'funnel_conversion_rates', 'funnel_dropoff_analysis']
PROFILE='retail_dw_master'
SCHEMA='funnel_analytics'
PROJECT='/app/dbt_project'
def ck(s,r,**kw):return dict(status=s,reason=r,**kw)
def evaluate(f):
 checks={k:ck('pending','Trusted observed workflow data absent')for k in ['profile','models','build','source_lineage']};files=f.get('files',{})
 if f.get('trusted_complete_inventory'):
  try:
   cfg=yaml.safe_load(files['dbt_project.yml']);profiles=yaml.safe_load(files['profiles.yml']);p=profiles[PROFILE];p=p['outputs'][p['target']];ok=cfg['profile']==PROFILE and p['type']=='duckdb'and p['path']=='/app/database/retail.duckdb'and f['environment']['DBT_PROFILES_DIR']==PROJECT;checks['profile']=ck('pass'if ok else'fail','Declared local profile and DuckDB database; actual model schema separately checked')
  except(KeyError,TypeError,ValueError,yaml.YAMLError):checks['profile']=ck('fail','Required profile is absent or invalid')
 if f.get('trusted_fresh_process'):
  man=f['manifest'];rr={r['unique_id']:r for r in f['run_results']['results']};ok=f.get('fresh_source_invocation_binding')is True;seen=[]
  for name in MODELS:
   ns=[(u,n)for u,n in man['nodes'].items()if n.get('resource_type')=='model'and n['name']==name];ok &=len(ns)==1
   if len(ns)!=1:continue
   u,n=ns[0];path=n['original_file_path'];ok &=path in files and n['raw_code'].strip()==files.get(path,'').strip()and n['schema'].lower()==SCHEMA and rr.get(u,{}).get('status')=='success';seen.append(path)
   if KIND=='acquisition':ok &=path=='models/marts/marketing/rpt_customer_acquisition_channel_performance_fixed.sql'and n['config']['materialized']=='table'
  checks['models']=ck('pass'if ok else'fail','Required actual source models and schema bound to the successful specific invocation',paths=seen);checks['build']=ck('pass'if ok and f.get('actual_build_returncode')==0 else'fail','Specific trusted dbt run; later repeat outcome recorded separately')
 if f.get('source_lineage'):checks['source_lineage']=f['source_lineage']
 if KIND=='funnel':
  checks['backend']=ck('pending','Actual pre-write live backend echo unavailable')
  if f.get('backend_before_install'):
   x=f['backend_before_install'];e=x['shell'];ok=e.get('exit_code')==0 and e.get('output','').strip()=='duckdb'and e.get('command')=='echo "$DB_TYPE"'and datetime.fromisoformat(e['finished_at_utc'].replace('Z','+00:00'))<datetime.fromisoformat(x['install_after_utc'].replace('Z','+00:00'));checks['backend']=ck('pass'if ok else'fail','Actual live backend echo before trusted source installation')
  checks['idempotence']=f.get('idempotence',ck('pending','Actual repeated build comparison unavailable'))
 else:
  checks['upstream_build']=f.get('upstream_build',ck('pending','Declared upstream build not bound'))
  checks['adapter_get_relation']=f.get('adapter_get_relation',ck('pending','Actual get_relation use not reviewed'))
 for name,v in f.get('closed_case_facts',{}).items():
  if name in checks:checks[name]=v
 states=[v['status']for v in checks.values()];return ck('fail'if'fail'in states else'gap'if'gap'in states else'pending'if'pending'in states else'pass','Observed task-specific workflow',details=checks)
