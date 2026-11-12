"""Actual original dbt execution facts; instruction-path policy stays explicit."""
import yaml,posixpath,ast,re
def resolve(value,environment):
 if isinstance(value,dict):return {k:resolve(v,environment)for k,v in value.items()}
 if isinstance(value,list):return [resolve(v,environment)for v in value]
 if not isinstance(value,str)or not any(m in value for m in ('{{','{%')):return value
 match=re.fullmatch(r'\s*\{\{\s*env_var\((.*)\)\s*\}\}\s*',value,re.S)
 if not match:raise ValueError('Profile template lacks trusted rendered resolution')
 node=ast.parse('env_var('+match.group(1)+')',mode='eval').body
 if not isinstance(node,ast.Call)or node.keywords or len(node.args)not in(1,2)or any(not isinstance(a,ast.Constant)or not isinstance(a.value,str)for a in node.args):raise ValueError('Profile expression is outside supported captured env_var form')
 name=node.args[0].value
 if name not in environment:raise ValueError('Profile variable not explicitly captured')
 if environment[name]is not None:return environment[name]
 if len(node.args)==2:return node.args[1].value
 raise ValueError('Required profile variable absent')

from datetime import datetime
from audit_v2 import COLS
KIND='coupon'
PROJECT='/app/dbt_models_duckdb'
def ck(s,r,**kw):return dict(status=s,reason=r,**kw)
def evaluate(f):
 checks={};files=f.get('files',{})
 if f.get('trusted_complete_inventory'):
  try:
   cfg=yaml.safe_load(files['dbt_project.yml']);p=yaml.safe_load(files['profiles.yml'])['retail_dw_master'];p=resolve(p['outputs'][resolve(p['target'],f.get('environment',{}))],f.get('environment',{}));ok=cfg['profile']=='retail_dw_master'and p['type']=='duckdb'and posixpath.normpath(posixpath.join(PROJECT,p['path']))=='/app/database/retail.duckdb'and f['environment']['DBT_PROFILES_DIR']==PROJECT;checks['profile']=ck('pass'if ok else'fail','Actual declared local profile, DuckDB backend and database path')
  except(KeyError,TypeError,ValueError,yaml.YAMLError):checks['profile']=ck('fail','Required actual local profile absent or invalid')
 else:checks['profile']=ck('pending','Complete actual inventory unavailable')
 if f.get('trusted_fresh_process'):
  man=f['manifest'];rr={r['unique_id']:r for r in f['run_results']['results']};ok=f.get('fresh_source_invocation_binding')is True
  for name in COLS:
   ns=[(u,n)for u,n in man['nodes'].items()if n.get('resource_type')=='model'and n['name']==name];good=len(ns)==1
   if good:
    u,n=ns[0]
    if KIND=='coupon':path='models/intermediate/'+name+'.sql'if name.startswith('int_')else'models/marts/analytics/'+name+'.sql';material='view'if name.startswith('int_')else'table';schema='main'
    else:path=('models/staging/'if name.startswith('stg_')else'models/intermediate/'if name.startswith('int_')else'models/marts/channel/')+name+'.sql';material='table'if name=='monthly_channel_performance'else'view';schema='main_channel_analytics'if material=='table'else'main'
    good=n['original_file_path']==path and path in files and files[path].strip()==n['raw_code'].strip()and n['schema'].lower()==schema and n['config']['materialized']==material and rr.get(u,{}).get('status')=='success'
   ok=ok and good;checks['model.'+name]=ck('pass'if good else'fail','Actual required relative source path, model materialization, target schema and fresh result')
  checks['build']=ck('pass'if ok and f.get('actual_build_returncode')==0 else'fail','Specific source-bound target build')
 else:checks['build']=ck('pending','Actual fresh process unavailable')
 checks['backend']=ck('pending','Backend chronology unavailable')
 if f.get('backend_before_install'):
  x=f['backend_before_install'];e=x['shell'];ok=e.get('exit_code')==0 and e.get('output','').strip()=='duckdb'and e.get('command')=='echo "$DB_TYPE"'and datetime.fromisoformat(e['finished_at_utc'].replace('Z','+00:00'))<datetime.fromisoformat(x['install_after_utc'].replace('Z','+00:00'));checks['backend']=ck('pass'if ok else'fail','Actual live backend echo precedes source installation')
 checks['idempotence']=f.get('idempotence',ck('pending','Actual repeated build comparison unavailable'));checks['source_lineage']=f.get('source_lineage',ck('pending','Actual compiled sources unexamined'))
 if KIND=='coupon':checks['project_path_policy']=f.get('reviewed_project_path',ck('pending','Explicit current DuckDB path interpretation must be bound to reviewed instruction/decisions and actual setup; no alias assumed.'))
 for k,v in f.get('closed_case_facts',{}).items():checks[k]=v
 states=[v['status']for v in checks.values()];return ck('fail'if'fail'in states else'gap'if'gap'in states else'pending'if'pending'in states else'pass','Actual task workflow facts',details=checks)
