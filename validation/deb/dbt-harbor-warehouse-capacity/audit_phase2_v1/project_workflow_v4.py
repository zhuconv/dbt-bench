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
 target=profiles[name]['outputs'][profiles[name]['target']];checks['profile_name']=name=='retail_dw_master';checks['target']=target['type']=='duckdb'and target['path']==f['actual_database'];checks['project_location']=f['project_root']=='/app/dbt_project';checks['profiles_environment']=isinstance(f['environment'].get('DBT_PROFILES_DIR'),str)and PurePosixPath(f['environment']['DBT_PROFILES_DIR'])==PurePosixPath(f['project_root'])
 echo=json.loads(read(f['backend_shell'],root));checks['backend']=echo['command']=='echo "$DB_TYPE"'and echo['exit_code']==0 and echo['output'].strip()=='duckdb';checks['echo_before_code']=datetime.fromisoformat(echo['finished_at_utc'].replace('Z','+00:00'))<datetime.fromisoformat(f['source_install_after_utc'].replace('Z','+00:00'))
 if not all(checks.values()):return {'status':'scored','reward':0.0,'checks':checks}
 captured=json.loads(read(request['parsed_snapshot'],root));manifest=json.loads(read(f['manifest'],root));assert captured['manifest_sha256']==f['manifest']['sha256']and captured['manifest_invocation_id']==manifest['metadata']['invocation_id'];nodes=[];by_model={}
 for relation in models.values():
  model=relation.split('.')[-1]
  p=captured['parsed_compiled_sql'][model];matches=[n for n in manifest['nodes'].values()if n.get('resource_type')=='model'and n['name']==model];assert len(matches)==1 and hashlib.sha256(matches[0]['compiled_code'].encode()).hexdigest()==p['compiled_sql_sha256'];parsed=p['ast']
  if parsed.get('error')or len(parsed.get('statements',[]))!=1:raise ValueError('Unparsed required model SQL')
  by_model[model]=active_nodes(parsed);nodes+=by_model[model]
 peak_windows=[n for n in by_model['int_capacity__peak_periods']if n.get('class')=='WINDOW'];fact_windows=[n for n in by_model['fct_warehouse_capacity']if n.get('class')=='WINDOW']
 def refs(values):return [v.get('expression',v).get('column_names',[])[-1:]for v in values]
 ntile=any(n.get('function_name')=='ntile'and n.get('children',[{}])[0].get('value',{}).get('value')==100 and refs(n.get('partitions',[]))==[['warehouse_id']]and refs(n.get('orders',[]))[:1]==[['order_count']]and n['orders'][0]['type']in('ORDER_DEFAULT','ASCENDING')for n in peak_windows)
 rolling=any(n.get('function_name')=='avg'and refs(n.get('partitions',[]))==[['warehouse_id']]and refs(n.get('orders',[]))[:1]==[['capacity_date']]and n.get('start')=='EXPR_PRECEDING_ROWS'and n.get('start_expr',{}).get('value',{}).get('value')==6 and n.get('end')=='CURRENT_ROW_ROWS'for n in fact_windows)
 if not all(checks.values()):return {'status':'scored','reward':0.0,'checks':checks}
 # The report explicitly filters the delivered fact. Trust only fresh manifest
 # lineage or an active qualified relation, never the mere equality of rows.
 report_nodes=[(key,node)for key,node in manifest['nodes'].items()if node.get('resource_type')=='model'and node.get('name')=='rpt_capacity_bottlenecks'];assert len(report_nodes)==1
 def has_fact(key,seen):
  if key in seen:return False
  node=manifest['nodes'].get(key,{})
  if node.get('name')=='fct_warehouse_capacity'and node.get('schema','').lower()=='main_marts':return True
  return any(has_fact(dep,seen|{key})for dep in node.get('depends_on',{}).get('nodes',[]))
 report_active=active_nodes(captured['parsed_compiled_sql']['rpt_capacity_bottlenecks']['ast']);bases=[n for n in report_active if n.get('type')=='BASE_TABLE'];source=has_fact(report_nodes[0][0],set())or any(n.get('table_name','').lower()=='fct_warehouse_capacity'and n.get('schema_name','').lower()=='main_marts'for n in bases)
 checks['report_fact_source']=source
 if not source:
  if not bases and not report_nodes[0][1].get('depends_on',{}).get('nodes'):return {'status':'scored','reward':0.0,'checks':checks,'reason':'Fresh parsed report is a constant relation with no fact/table/model source, violating its explicit Source clause despite equal output values.'}
  return {'status':'unknown','reward':None,'checks':checks,'reason':'Report fact-source lineage is not established; external view or unrecognized source path was not captured. No SQL hash whitelist.'}
 if not(ntile and rolling):return {'status':'unknown','reward':None,'checks':checks,'recognized_ntile100':ntile,'recognized_rolling7':rolling,'reason':'Current limited parsed lineage does not establish mandated window form; no SQL hash whitelist.'}
 return {'status':'scored','reward':1.0,'checks':checks,'recognized_ntile100':ntile,'recognized_rolling7':rolling,'scope':'Actual required backend/profile/project/source inventory and fresh parsed active window expressions; all seven target schemas independently bound by trusted manifest. Unspecified NTILE tie columns not forced.'}
