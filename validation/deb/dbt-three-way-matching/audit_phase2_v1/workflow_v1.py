"""Actual model, build and profile facts; policy paths do not become success."""
import yaml,posixpath
from datetime import datetime
KIND='threeway'
PROJECT='/app/dbt_models_duckdb'
NAMES=['three_way_match']
def ck(s,r,**kw):return dict(status=s,reason=r,**kw)
def evaluate(f):
 checks={};files=f.get('files',{})
 if f.get('trusted_complete_inventory'):
  try:
   cfg=yaml.safe_load(files['dbt_project.yml']);p=yaml.safe_load(files['profiles.yml'])['retail_dw_master'];p=p['outputs'][p['target']];ok=cfg['profile']=='retail_dw_master'and p['type']=='duckdb'and posixpath.normpath(posixpath.join(PROJECT,p['path']))=='/app/database/retail.duckdb'and f['environment']['DBT_PROFILES_DIR']==PROJECT;checks['profile']=ck('pass'if ok else'fail','Actual task-local named profile/database and environment binding')
  except(KeyError,TypeError,ValueError,yaml.YAMLError):checks['profile']=ck('fail','Required profile absent/invalid')
 else:checks['profile']=ck('pending','Actual complete inventory unavailable')
 if f.get('trusted_fresh_process'):
  m=f['manifest'];rr={r['unique_id']:r for r in f['run_results']['results']};ok=f.get('fresh_source_invocation_binding')is True
  for name in NAMES:
   ns=[(u,n)for u,n in m['nodes'].items()if n.get('resource_type')=='model'and n['name']==name];good=len(ns)==1
   if good:
    u,n=ns[0];path='models/marts/three_way_match.sql';good=n['original_file_path']==path and path in files and files[path].strip()==n['raw_code'].strip()and n['schema'].lower()=='main'and rr.get(u,{}).get('status')=='success'
    if KIND=='channel':good=good and n['config']['materialized']=='view'
   checks['model.'+name]=ck('pass'if good else'fail','Exact required source path, manifest, schema/materialization and actual matched build result');ok=ok and good
  checks['build']=ck('pass'if ok and f.get('actual_build_returncode')==0 else'fail','Actual source-bound build sequences, not reward inference')
 else:checks['build']=ck('pending','Actual source-bound build unavailable')
 checks['backend']=ck('pending','Action chronology missing')
 if f.get('backend_before_install'):
  x=f['backend_before_install'];e=x['shell'];ok=e.get('exit_code')==0 and e.get('output','').strip()=='duckdb'and e.get('command')=='echo "$DB_TYPE"'and datetime.fromisoformat(e['finished_at_utc'].replace('Z','+00:00'))<datetime.fromisoformat(x['install_after_utc'].replace('Z','+00:00'));checks['backend']=ck('pass'if ok else'fail','Actual live backend echo precedes source installation')
 for n in ['idempotence','source_lineage'] :checks[n]=f.get(n,ck('pending','Actual '+n+' facts unavailable'))
 if KIND=='threeway':
  checks['staging_unchanged']=f.get('staging_unchanged',ck('pending','Initial/final upstream filehashes unavailable'))
  checks['materialization_scope']=f.get('materialization_scope',ck('pending','Initial target materialization baseline not yet inspected'))
 if KIND=='channel':
  checks['tags']=f.get('tags',ck('pending','Actual required tag configuration unavailable'))
  checks['channel_cast']=f.get('channel_cast',ck('pending','Actual VARCHAR channel-key cast inspection unavailable'))
  checks['timestamp_expression']=f.get('timestamp_expression',ck('pending','Actual compiled current_timestamp expression inspection unavailable'))
 for k,v in f.get('closed_case_facts',{}).items():checks[k]=v
 ss=[v['status']for v in checks.values()];return ck('fail'if'fail'in ss else'gap'if'gap'in ss else'pending'if'pending'in ss else'pass','Actual declared dbt workflow',details=checks)
