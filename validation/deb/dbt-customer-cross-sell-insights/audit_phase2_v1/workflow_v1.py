"""Require actual source-bound model builds, profiles and original input lineage."""
import yaml,posixpath
from datetime import datetime
KIND='cross'
PROJECT='/app/dbt_models_duckdb'if KIND=='clv'else'/app/dbt_project'
NAMES=['int_clv_customer_orders','int_clv_metrics','clv_predictions','clv_segments','clv_segment_summary','clv_cohort_analysis','clv_monthly_trends']if KIND=='clv'else['rpt_cross_sell_insights','rpt_cross_sell_trends','rpt_cross_sell_by_segment','rpt_cross_sell_category_hierarchy']
def ck(s,r,**kw):return dict(status=s,reason=r,**kw)
def evaluate(f):
 checks={};files=f.get('files',{})
 if f.get('trusted_complete_inventory'):
  try:
   cfg=yaml.safe_load(files['dbt_project.yml']);p=yaml.safe_load(files['profiles.yml'])['retail_dw_master'];p=p['outputs'][p['target']];ok=cfg['profile']=='retail_dw_master'and p['type']=='duckdb'and posixpath.normpath(posixpath.join(PROJECT,p['path']))=='/app/database/retail.duckdb'and f['environment']['DBT_PROFILES_DIR']==PROJECT and(KIND!='cross'or p.get('schema')=='analytics');checks['profile']=ck('pass'if ok else'fail','Actual task-local retail_dw_master profile/database, backend and declared analytics schema where required')
  except(KeyError,TypeError,ValueError,yaml.YAMLError):checks['profile']=ck('fail','Required actual local profile absent or invalid')
 else:checks['profile']=ck('pending','Complete actual project inventory unavailable')
 if f.get('trusted_fresh_process'):
  m=f['manifest'];rr={r['unique_id']:r for r in f['run_results']['results']};ok=f.get('fresh_source_invocation_binding')is True
  for name in NAMES:
   ns=[(u,n)for u,n in m['nodes'].items()if n.get('resource_type')=='model'and n['name']==name];good=len(ns)==1
   if good:
    u,n=ns[0]
    if KIND=='clv':path=('models/intermediate/'if name.startswith('int_')else'models/marts/customer/')+name+'.sql';material='view'if name.startswith('int_')or name=='clv_predictions'else'table';schema='main'
    else:path=('models/marts/customer/'if name=='rpt_cross_sell_by_segment'else'models/marts/products/')+name+'.sql';material='table';schema='analytics'
    good=n['original_file_path']==path and path in files and files[path].strip()==n['raw_code'].strip()and n['schema'].lower()==schema and n['config']['materialized']==material and rr.get(u,{}).get('status')=='success'
   ok=ok and good;checks['model.'+name]=ck('pass'if good else'fail','Explicit actual source path/model/materialization/schema and bound fresh build result')
  checks['build']=ck('pass'if ok and f.get('actual_build_returncode')==0 else'fail','Specific source-bound target build, not native rebuild inferred from reward')
 else:checks['build']=ck('pending','Actual fresh build unavailable')
 checks['backend']=ck('pending','Backend action chronology unavailable')
 if f.get('backend_before_install'):
  x=f['backend_before_install'];e=x['shell'];ok=e.get('exit_code')==0 and e.get('output','').strip()=='duckdb'and e.get('command')=='echo "$DB_TYPE"'and datetime.fromisoformat(e['finished_at_utc'].replace('Z','+00:00'))<datetime.fromisoformat(x['install_after_utc'].replace('Z','+00:00'));checks['backend']=ck('pass'if ok else'fail','Actual live echo before any candidate source installation')
 checks['idempotence']=f.get('idempotence',ck('pending','Actual repeated dbt build/read comparison unavailable'));checks['source_lineage']=f.get('source_lineage',ck('pending','Actual compiled sources not yet reviewed'))
 for k,v in f.get('closed_case_facts',{}).items():checks[k]=v
 ss=[c['status']for c in checks.values()];return ck('fail'if'fail'in ss else'gap'if'gap'in ss else'pending'if'pending'in ss else'pass','Actual dbt workflow evidence',details=checks)
