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

MODELS=['int_cart_recovery__cart_base','int_cart_recovery__session_signals','int_cart_recovery__inventory_risk','int_cart_recovery__customer_context','fct_cart_recovery_priority']
TAGS={n:({'marts','digital','cart_recovery'}if n==MODELS[-1]else{'intermediate','cart_recovery','inventory'if n.endswith('inventory_risk')else'customer'if n.endswith('customer_context')else'digital'})for n in MODELS}
def evaluate(request,root):
 from posixpath import normpath
 from jinja2 import Environment,nodes
 f=json.loads(read(request['workflow'],root));assert f['producer']in('locked_qwen_clean_rebuild','task_image_selftest_clean_build')and f['snapshot']==request['snapshot'];checks={}
 if f.get('initial_required_models_absent'):return {'status':'scored','reward':0.0,'reason':'Trusted initial required model absent'}
 for name in ('dbt_project.yml','profiles.yml'):
  if name not in f['observed_files']:raise ValueError('Missing captured configuration inventory')
  checks[name]=f['observed_files'][name]['exists']
 if not all(checks.values()):return {'status':'scored','reward':0.0,'checks':checks}
 cfg=yaml.safe_load(read(f['project_source'],root));profiles=resolve(yaml.safe_load(read(f['profile_source'],root)),f.get('profile_environment',{}));name=cfg.get('profile');checks['profile_name']=name=='retail_dw_master'
 if name not in profiles:return {'status':'scored','reward':0.0,'checks':checks,'reason':'Selected profile absent'}
 target=profiles[name]['outputs'][profiles[name]['target']];checks['target']=target['type']=='duckdb'and normpath(target['path'])==f['actual_database'];checks['project_location']=normpath(f['project_root'])=='/app/dbt_models_duckdb';checks['profiles_environment']=isinstance(f['environment'].get('DBT_PROFILES_DIR'),str)and normpath(f['environment']['DBT_PROFILES_DIR'])==normpath(f['project_root']);echo=json.loads(read(f['backend_shell'],root));checks['backend']=echo['command']=='echo "$DB_TYPE"'and echo['exit_code']==0 and echo['output'].strip()=='duckdb';checks['echo_before_code']=datetime.fromisoformat(echo['finished_at_utc'].replace('Z','+00:00'))<datetime.fromisoformat(f['source_install_after_utc'].replace('Z','+00:00'))
 if not all(checks.values()):return {'status':'scored','reward':0.0,'checks':checks}
 manifest=json.loads(read(f['manifest'],root));captured=json.loads(read(request['parsed_snapshot'],root));assert captured['manifest_sha256']==f['manifest']['sha256'];initial=set(f['initial_model_inventory']);added={v['original_file_path']for v in f['sources']if v['original_file_path']not in initial};checks['only_five_new_models']=all(PurePosixPath(p).stem in MODELS for p in added)
 upstream=set();asts={}
 for model in MODELS:
  ns=[v for v in manifest['nodes'].values()if v.get('resource_type')=='model'and v['name']==model]
  if len(ns)!=1:raise ValueError('Required model manifest ambiguous or missing')
  n=ns[0];path=PurePosixPath(n['original_file_path']);checks[model+':folder']=path.parent==PurePosixPath('models/marts/digital')if model==MODELS[-1]else path.is_relative_to(PurePosixPath('models/intermediate'));checks[model+':tags']=TAGS[model]<=set(n['config']['tags']);checks[model+':materialization']=n['config']['materialized']==('table'if model==MODELS[-1]else'view')
  tree=Environment().parse(n['raw_code']);calls=list(tree.find_all(nodes.Call));checks[model+':no_source']=not any(isinstance(v.node,nodes.Name)and v.node.name=='source'for v in calls)and not n.get('sources');refs={v['name']if isinstance(v,dict)else v[-1]for v in n['refs']};upstream|=refs-set(MODELS);checks[model+':staging_or_own_refs']=all(v.startswith('stg_')or v in MODELS for v in refs);checks[model+':logic_in_models']=not n['depends_on']['macros']
  parsed=captured['parsed_compiled_sql'][model];assert parsed['compiled_sql_sha256']==hashlib.sha256(n['compiled_code'].encode()).hexdigest();active=active_nodes(parsed['ast']);asts[model]=active
  leaves=[v for v in active if v.get('type')=='BASE_TABLE'and v.get('schema_name')];expected_relations={(manifest['nodes'][uid]['schema'].lower(),manifest['nodes'][uid]['alias'].lower())for uid in n['depends_on']['nodes']if uid in manifest['nodes']};checks[model+':all_relations_via_ref']=all((v['schema_name'].lower(),v['table_name'].lower())in expected_relations and v.get('catalog_name','').lower()in('',n['database'].lower())for v in leaves)
  checks[model+':no_dynamic_clock']=not any(v.get('function_name','').lower()in('current_timestamp','get_current_timestamp','now','current_date','today')for v in active)
 checks['upstream_limit']=len(upstream)<=10
 # Structural checks concern the explicitly prescribed forms, not one SQL text.
 session=asts[MODELS[1]];required={'checkout_event_count','payment_error_count','add_to_cart_count','remove_from_cart_count','product_view_count'}
 def stripcast(x):
  while x.get('class')=='CAST':x=x['child']
  return x
 aliases={v.get('alias'):stripcast(v)for v in session if v.get('alias')in required and stripcast(v).get('function_name')=='sum'}
 checks['conditional_sum_case']=all(n in aliases and aliases[n]['children']and stripcast(aliases[n]['children'][0]).get('class')=='CASE'for n in required)
 checks['no_filtered_counts']=not any(v.get('function_name')in('count','count_star')and v.get('filter')is not None for v in session)
 for staging in ('stg_web_sessions','stg_web_events'):
  checks[staging+':select_star']=any(v.get('type')=='SELECT_NODE'and v.get('from_table',{}).get('table_name','').lower()==staging and any(x.get('class')=='STAR'for x in v.get('select_list',[]))for v in session)
 consent=asts[MODELS[3]];flags={n+'_consent'for n in ('email','sms','push','personalization')};checks['consent_max_case']=all(any(v.get('alias')==flag and v.get('function_name')=='max'and any(x.get('class')=='CASE'for x in walk(v))for v in consent)for flag in flags)
 fact=asts[MODELS[-1]];checks['recovery_status_join_form']=any(v.get('type')=='JOIN'and any(x.get('function_name')=='upper'and any(y.get('class')=='COLUMN_REF'and y['column_names'][-1].lower()=='status'for y in walk(x))and any(y.get('type')=='OPERATOR_COALESCE'for y in walk(x))for x in walk(v.get('condition')))for v in fact)
 return {'status':'scored','reward':float(all(checks.values())),'checks':checks,'scope':'Trusted actual source/config/build and complete declared outputs; structural forms parsed from compiled SQL and Jinja, never SQL-hash whitelisted. Original-input values independently derived separately; no absent-branch correctness claimed.'}
