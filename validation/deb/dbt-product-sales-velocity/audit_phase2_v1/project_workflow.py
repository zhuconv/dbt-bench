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

def bare(x):
 while isinstance(x,dict)and x.get('class')=='CAST':x=x['child']
 return x

def refs(x):return {v['column_names'][-1].lower()for v in walk(x)if v.get('class')=='COLUMN_REF'}

def constant(x):
 if isinstance(x,dict)and x.get('class')=='CAST'and x.get('cast_type',{}).get('id')=='BOOLEAN':
  value=bare(x).get('value',{}).get('value')
  if value in('t','true','TRUE'):return True
  if value in('f','false','FALSE'):return False
 return bare(x).get('value',{}).get('value')

def evaluate(request,root,models):
 from posixpath import normpath
 f=json.loads(read(request['workflow'],root));assert f['producer']in('locked_qwen_clean_rebuild','task_image_selftest_clean_build')and f['snapshot']==request['snapshot'];checks={}
 if f.get('initial_required_models_absent'):return {'status':'scored','reward':0.0,'reason':'Trusted initial required source model absent'}
 for name in('dbt_project.yml','profiles.yml'):
  if name not in f['observed_files']:raise ValueError('Required file inventory not captured')
  checks[name]=f['observed_files'][name]['exists']
 if not all(checks.values()):return {'status':'scored','reward':0.0,'checks':checks}
 cfg=yaml.safe_load(read(f['project_source'],root));profiles=resolve(yaml.safe_load(read(f['profile_source'],root)),f.get('profile_environment',{}));checks['profile_name']=cfg.get('profile')=='retail_dw_master'
 if cfg.get('profile')not in profiles:return {'status':'scored','reward':0.0,'checks':checks,'reason':'Selected profile absent'}
 profile=profiles[cfg['profile']];target=profile['outputs'][profile['target']];checks['database']=target['type']=='duckdb'and normpath(target['path'])==f['actual_database'];checks['project_root']=normpath(f['project_root'])=='/app/dbt_models_duckdb';checks['profiles_environment']=isinstance(f['environment'].get('DBT_PROFILES_DIR'),str)and normpath(f['environment']['DBT_PROFILES_DIR'])==normpath(f['project_root']);echo=json.loads(read(f['backend_shell'],root));checks['backend']=echo['command']=='echo "$DB_TYPE"'and echo['exit_code']==0 and echo['output'].strip()=='duckdb';checks['echo_before_code']=datetime.fromisoformat(echo['finished_at_utc'].replace('Z','+00:00'))<datetime.fromisoformat(f['source_install_after_utc'].replace('Z','+00:00'))
 sequence=json.loads(read(f['build_sequence'],root));assert sequence['task_key']==request['task_key'];commands=sequence['commands'];first=next((i for i,v in enumerate(commands)if v['argv'][1]in('run','build')),len(commands));checks['deps_before_run']=any(v['argv'][:2]==['dbt','deps']and v['returncode']==0 for v in commands[:first]);checks['build_observed']=first<len(commands)and all(v['returncode']==0 for v in commands)
 manifest=json.loads(read(f['manifest'],root));parsed=json.loads(read(request['parsed_snapshot'],root));assert parsed['manifest_sha256']==f['manifest']['sha256'];asts={}
 for name,expected in models.items():
  ns=[v for v in manifest['nodes'].values()if v.get('resource_type')=='model'and v['name']==name]
  if len(ns)!=1:raise ValueError('Required model not uniquely captured')
  n=ns[0];checks[name+':path']=n['original_file_path']==expected['path'];checks[name+':schema']=n['schema'].lower()==expected['schema'];checks[name+':materialized']=n['config']['materialized']in expected['materializations'];p=parsed['parsed_compiled_sql'][name];assert p['compiled_sql_sha256']==hashlib.sha256(n['compiled_code'].encode()).hexdigest();asts[name]=active_nodes(p['ast']);allowed=set()
  for uid in n['depends_on']['nodes']:
   dep=manifest['nodes'].get(uid,manifest['sources'].get(uid))
   if not dep:raise ValueError('Unresolved dependency node')
   schema=dep['schema'].lower();table=dep.get('alias',dep.get('identifier',dep['name'])).lower();allowed.add((schema,table))
  leaves=[v for v in asts[name]if v.get('type')=='BASE_TABLE'and v.get('schema_name')];checks[name+':source_or_ref']=all((v['schema_name'].lower(),v['table_name'].lower())in allowed and v.get('catalog_name','').lower()in('',n['database'].lower())for v in leaves)
 return {'status':'scored','reward':float(all(checks.values())),'checks':checks,'scope':'Actual trusted source/config/backend chronology, successful deps-before-selected-build and declared model locations/materializations/schema/ref-source relation provenance; output business values independently checked.'}
