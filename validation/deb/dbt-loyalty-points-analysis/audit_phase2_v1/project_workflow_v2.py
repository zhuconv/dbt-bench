"""Task-specific profile/source/config checks over trusted clean reconstruction."""
from pathlib import PurePosixPath
from datetime import datetime
import hashlib,json,yaml

def read(ref,root):
 p=root/ref['path'];data=p.read_bytes()
 if hashlib.sha256(data).hexdigest()!=ref['sha256']:raise ValueError('Changed capture')
 return data

def evaluate(request,root,models,kind):
 facts=json.loads(read(request['workflow'],root));checks={}
 if facts.get('producer')not in('locked_qwen_clean_rebuild','task_image_selftest_clean_build')or facts['snapshot']!=request['snapshot']:raise ValueError('Not a trusted matching rebuild')
 if facts.get('initial_required_models_absent'):return {'status':'scored','reward':0.0,'reason':'Complete initial model inventory lacks required delivery'}
 observed=facts['observed_files']
 for name in('dbt_project.yml','profiles.yml'):
  if name not in observed:raise ValueError('Configuration file was not captured')
  checks['file:'+name]=observed[name]['exists']
 if not all(checks.values()):return {'status':'scored','reward':0.0,'checks':checks,'reason':'Required configuration file observed absent after trusted build'}
 cfg=yaml.safe_load(read(facts['project_source'],root));profiles=yaml.safe_load(read(facts['profile_source'],root));name=cfg.get('profile');
 if '{{' in json.dumps(profiles) or '{%' in json.dumps(profiles):raise ValueError('Template profile requires trusted rendered resolution; do not reject unrendered literal text')
 if name not in profiles:return {'status':'scored','reward':0.0,'checks':{'named_profile_present':False},'reason':'Delivered configuration lacks its requested profile'}
 target=profiles[name]['outputs'][profiles[name]['target']];schema='inventory_analytics'if kind=='inventory'else'loyalty_analytics'
 checks['profile_name']=name=='dbt_project'if kind=='inventory'else isinstance(name,str)and bool(name)
 checks['profile_target']=target['type']=='duckdb'and target['path']==facts['actual_database']and target['schema']==schema
 checks['required_project_location']=facts.get('logical_project_root',facts['project_root'])=='/app/dbt_project'
 checks['required_profiles_environment']=isinstance(facts['environment'].get('DBT_PROFILES_DIR'),str) and PurePosixPath(facts['environment']['DBT_PROFILES_DIR'])==PurePosixPath(facts['project_root'])
 def schema_configs(value):
  if isinstance(value,dict):return any(k in('schema','+schema')or schema_configs(v)for k,v in value.items())
  if isinstance(value,list):return any(schema_configs(v)for v in value)
  return False
 checks['no_project_schema']=not any(k in cfg for k in('schema','+schema'))and not any(schema_configs(cfg.get(k,{}))for k in('models','seeds','snapshots'))
 echo=json.loads(read(facts['backend_shell'],root));checks['actual_backend']=echo.get('command')=='echo "$DB_TYPE"'and echo.get('exit_code')==0 and echo.get('output','').strip()=='duckdb';checks['echo_before_code']=datetime.fromisoformat(echo['finished_at_utc'].replace('Z','+00:00'))<datetime.fromisoformat(facts['source_install_after_utc'].replace('Z','+00:00'))
 manifest=json.loads(read(facts['manifest'],root));nodes=manifest['nodes'];sources=manifest['sources'];selected={}
 for role,model in models.items():
  found=[(uid,node)for uid,node in nodes.items()if node.get('resource_type')=='model'and node.get('name')==model]
  if len(found)!=1:raise ValueError('Required model role missing or ambiguous')
  selected[role]=found[0]
 def ancestors(uid,seen=None):
  seen=set()if seen is None else seen
  for child in nodes[uid]['depends_on']['nodes']if uid in nodes else[]:
   if child not in seen:seen.add(child);ancestors(child,seen)
  return seen
 required={'levels':('inventory','inventory_levels'),'warehouses':('inventory','warehouses')}if kind=='inventory'else{'transactions':('main','loyalty_points_transactions'),'programs':('main','loyalty_programs'),'customers':('main','customers')}
 for role,physical in required.items():
  uid,node=selected[role];ids=ancestors(uid)&sources.keys();matched=[sources[i]for i in ids if (sources[i]['schema'].casefold(),sources[i]['identifier'].casefold())==physical];checks['source:'+role]=bool(matched)and node['original_file_path'].startswith('models/staging/')
  for source in matched:
   path=source['original_file_path']
   if path not in observed:raise ValueError('Source YAML path was not captured: '+path)
   checks['source_file:'+role]=observed[path]['exists']
   if observed[path]['exists']:
    raw_yaml=read(observed[path]['file'],root).decode();current=yaml.safe_load(raw_yaml) or {};definitions=current.get('sources',[])
    checks['source_definition:'+role]=any(item.get('name')==source['source_name'] and any(t.get('name')==source['name'] for t in item.get('tables',[]))for item in definitions)
    if not checks['source_definition:'+role] and ('{{'in raw_yaml or '{%'in raw_yaml):raise ValueError('Templated YAML source declaration needs trusted rendered binding')
   if kind=='inventory':checks['sources_yml:'+role]=PurePosixPath(path).name=='sources.yml'
 if kind=='inventory':checks['required_lineage']=all(selected[r][0]in ancestors(selected['final'][0])for r in required)
 else:
  checks['intermediate_lineage']=selected['transactions'][0]in ancestors(selected['intermediate'][0]);checks['final_lineage']=all(selected[r][0]in ancestors(selected['final'][0])for r in('intermediate','programs'))
 return {'status':'scored','reward':float(all(checks.values())),'checks':checks,'scope':'Actual config files, explicitly required environment, fresh source/ref graph and chronology. No reference SQL hash or unspecified staging column layout is required.'}
