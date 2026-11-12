"""Trusted project/config and parsed window-form checks, independent of output rows."""
from pathlib import PurePosixPath
from datetime import datetime
import ast,hashlib,json,re,yaml

def read(ref,root):
 p=root/ref['path'];data=p.read_bytes()
 if hashlib.sha256(data).hexdigest()!=ref['sha256']:raise ValueError('Changed workflow capture')
 return data

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
def walk(x):
 if isinstance(x,dict):
  yield x
  for v in x.values():yield from walk(v)
 elif isinstance(x,list):
  for v in x:yield from walk(v)
def active_nodes(parsed):
 result=[]
 def visit(value,inherited):
  if isinstance(value,list):
   for item in value:visit(item,inherited)
   return
  if not isinstance(value,dict):return
  ctes=dict(inherited)
  for item in value.get('cte_map',{}).get('map',[]):ctes[item['key'].lower()]=item['value']['query']['node']
  result.append(value);name=value.get('table_name','').lower()
  if value.get('type')=='BASE_TABLE'and not value.get('schema_name')and name in ctes:visit(ctes[name],{k:v for k,v in ctes.items()if k!=name})
  for key,child in value.items():
   if key!='cte_map':visit(child,ctes)
 for statement in parsed.get('statements',[]):visit(statement['node'],{})
 return result

def evaluate(request,root,models):
 f=json.loads(read(request['workflow'],root));assert f['producer']in('locked_qwen_clean_rebuild','task_image_selftest_clean_build')and f['snapshot']==request['snapshot'];checks={}
 if f.get('initial_required_models_absent'):return {'status':'scored','reward':0.0,'reason':'Trusted initial inventory lacks required models'}
 for name in('dbt_project.yml','profiles.yml'):
  if name not in f['observed_files']:raise ValueError('Configuration not captured')
  checks['file:'+name]=f['observed_files'][name]['exists']
 if not all(checks.values()):return {'status':'scored','reward':0.0,'checks':checks}
 cfg=yaml.safe_load(read(f['project_source'],root));profiles=resolve(yaml.safe_load(read(f['profile_source'],root)),f.get('profile_environment',{}));name=cfg.get('profile')
 if name not in profiles:return {'status':'scored','reward':0.0,'reason':'Configured profile absent'}
 target=profiles[name]['outputs'][profiles[name]['target']];checks['profile_name']=name=='dbt_project';checks['target']=target['type']=='duckdb'and target['path']==f['actual_database']and target['schema']=='sales_analytics';checks['project_location']=f['project_root']=='/app/dbt_project';checks['profiles_environment']=isinstance(f['environment'].get('DBT_PROFILES_DIR'),str)and PurePosixPath(f['environment']['DBT_PROFILES_DIR'])==PurePosixPath(f['project_root'])
 def schema(value):
  if isinstance(value,dict):return any(k in('schema','+schema')or schema(v)for k,v in value.items())
  if isinstance(value,list):return any(schema(v)for v in value)
  return False
 checks['no_project_schema']=not any(k in cfg for k in('schema','+schema'))and not any(schema(cfg.get(k,{}))for k in('models','seeds','snapshots'));echo=json.loads(read(f['backend_shell'],root));checks['backend']=echo['command']=='echo "$DB_TYPE"'and echo['exit_code']==0 and echo['output'].strip()=='duckdb';checks['echo_before_code']=datetime.fromisoformat(echo['finished_at_utc'].replace('Z','+00:00'))<datetime.fromisoformat(f['source_install_after_utc'].replace('Z','+00:00'))
 if not all(checks.values()):return {'status':'scored','reward':0.0,'checks':checks}
 captured=json.loads(read(request['parsed_snapshot'],root));manifest=json.loads(read(f['manifest'],root));assert captured['manifest_sha256']==f['manifest']['sha256']and captured['manifest_invocation_id']==manifest['metadata']['invocation_id'];nodes=[]
 for model in models.values():
  p=captured['parsed_compiled_sql'][model];matches=[n for n in manifest['nodes'].values()if n.get('resource_type')=='model'and n['name']==model];assert len(matches)==1 and hashlib.sha256(matches[0]['compiled_code'].encode()).hexdigest()==p['compiled_sql_sha256'];parsed=p['ast']
  if parsed.get('error')or len(parsed.get('statements',[]))!=1:raise ValueError('Unparsed required model SQL')
  nodes+=active_nodes(parsed)
 windows=[n for n in nodes if n.get('class')=='WINDOW'];checks['window_functions_present']=bool(windows);checks['explicit_window_order']=all(bool(n.get('orders'))for n in windows)
 if not all(checks.values()):return {'status':'scored','reward':0.0,'checks':checks}
 from window_lineage_v1 import output_windows
 needed={'prev_week_revenue':{'lag','first_value','last_value'},'rolling_4wk_avg_revenue':{'avg','sum'},'cumulative_revenue':{'sum'},'is_best_week':{'max'}}
 lineage=output_windows(captured['parsed_compiled_sql']['weekly_sales_growth']['ast']);forms={name:bool(set(lineage.get(name,[]))&functions)for name,functions in needed.items()}
 if not all(forms.values()):return {'status':'unknown','reward':None,'checks':checks,'recognized_window_lineage':forms,'reason':'Window roles not established by current parsed alias flow; unrelated windows are insufficient. No SQL hash whitelist.'}
 return {'status':'scored','reward':1.0,'checks':checks,'recognized_window_lineage':forms,'scope':'Actual file/config/environment and fresh compiled SQL. Active named window expressions are structurally recognized; full current values/types/order independently compared. Unrecognized legal SQL remains unknown, not rejected.'}
