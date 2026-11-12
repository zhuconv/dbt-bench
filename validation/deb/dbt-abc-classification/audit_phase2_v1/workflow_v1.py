"""Actual trusted process/source facts; no whitelist of solution hashes."""
import yaml,re
from datetime import datetime
from expected_v1 import COLS
KIND='abc'
PROJECT='/app/dbt_models_duckdb'
def ck(s,r,**kw):return dict(status=s,reason=r,**kw)
def evaluate(f):
 checks={};files=f.get('files',{})
 if f.get('trusted_complete_inventory'):
  try:
   cfg=yaml.safe_load(files['dbt_project.yml']);p=yaml.safe_load(files['profiles.yml'])['retail_dw_master'];p=p['outputs'][p['target']];ok=cfg['profile']=='retail_dw_master'and p['type']=='duckdb'and p['path']=='/app/database/retail.duckdb'and f['environment']['DBT_PROFILES_DIR']==PROJECT;checks['profile']=ck('pass'if ok else'fail','Required local retail_dw_master profile and actual DuckDB path')
  except(KeyError,TypeError,ValueError,yaml.YAMLError):checks['profile']=ck('fail','Required local profile absent or invalid')
 else:checks['profile']=ck('pending','Complete actual file inventory unavailable')
 if f.get('trusted_fresh_process'):
  man=f['manifest'];rr={r['unique_id']:r for r in f['run_results']['results']};checks['models']=ck('pass','Required actual paths and materialization examined');ok=f.get('fresh_source_invocation_binding')is True
  for name in COLS:
   ns=[(u,n)for u,n in man['nodes'].items()if n.get('resource_type')=='model'and n['name']==name];good=len(ns)==1
   if good:
    u,n=ns[0];path=n['original_file_path'];prefix='models/intermediate/'if name.startswith('int_')else'models/marts/analytics/'if KIND=='abc'else'models/marts/';good=path.startswith(prefix)and path==prefix+name+'.sql'and path in files and files[path].strip()==n['raw_code'].strip()and n['schema'].lower()=='main'and rr.get(u,{}).get('status')=='success'
    if KIND=='abc':good=good and n['config']['materialized']==('view'if name.startswith('int_')else'table')
   ok=ok and good;checks['model.'+name]=ck('pass'if good else'fail','Declared real SQL location and successful invocation',matches=len(ns))
  checks['build']=ck('pass'if ok and f.get('actual_build_returncode')==0 else'fail','Actual specific build invocation and source binding')
 else:checks['build']=ck('pending','Fresh process provenance unavailable')
 checks['backend']=ck('pending','Initial backend echo provenance unavailable')
 if f.get('backend_before_install'):
  x=f['backend_before_install'];e=x['shell'];ok=e.get('exit_code')==0 and e.get('output','').strip()=='duckdb'and e.get('command')=='echo "$DB_TYPE"'and datetime.fromisoformat(e['finished_at_utc'].replace('Z','+00:00'))<datetime.fromisoformat(x['install_after_utc'].replace('Z','+00:00'));checks['backend']=ck('pass'if ok else'fail','Actual live backend echo before code installation')
 checks['idempotence']=f.get('idempotence',ck('pending','Two actual build outputs unavailable'))
 checks['source_lineage']=f.get('source_lineage',ck('pending','Compiled source inspection unavailable'))
 if KIND=='lifo':
  checks['reference_readonly']=f.get('reference_readonly',ck('pending','Initial/final reference source comparison unavailable'))
  checks['discovery']=f.get('discovery',ck('pending','Actual staging discovery unavailable'))
  checks['portable_cast']=f.get('portable_cast',ck('pending','Actual SQL tokens unavailable'))
 else:checks['date_format']=f.get('date_format',ck('pending','Actual compiled date functions unavailable'))
 for k,v in f.get('closed_case_facts',{}).items():checks[k]=v
 states=[v['status']for v in checks.values()];return ck('fail'if'fail'in states else'gap'if'gap'in states else'pending'if'pending'in states else'pass','Observed actual task workflow',details=checks)
