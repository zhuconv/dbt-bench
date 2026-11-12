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

def inspect_template(sql,metadata):
 """Bounded Jinja control-flow analysis for the actually captured template.

No source hash whitelist. Other template forms remain unknown. This is source
analysis tied to a trusted real build, not fabricated adapter call telemetry.
"""
 from jinja2 import Environment,nodes
 astnode=Environment().parse(sql);calls=[];env={'execute':True,'target':{'type':'duckdb'}};columns=metadata['optional_joined']['sql_schema']
 def expr(n):
  if isinstance(n,nodes.Const):return n.value
  if isinstance(n,nodes.Name):return env[n.name]
  if isinstance(n,nodes.Getattr):return expr(n.node)[n.attr]
  if isinstance(n,nodes.Filter)and n.name=='lower':return str(expr(n.node)).lower()
  if isinstance(n,nodes.And):return expr(n.left)and expr(n.right)
  if isinstance(n,nodes.Or):return expr(n.left)or expr(n.right)
  if isinstance(n,nodes.Not):return not expr(n.node)
  if isinstance(n,nodes.Compare):
   left=expr(n.expr);answer=True
   for op in n.ops:
    right=expr(op.expr)
    if op.op!='eq':raise ValueError('Unsupported template comparison')
    answer=answer and left==right;left=right
   return answer
  if isinstance(n,nodes.Call):
   if isinstance(n.node,nodes.Name)and n.node.name=='namespace':return {v.key:expr(v.value)for v in n.kwargs}
   if isinstance(n.node,nodes.Getattr)and isinstance(n.node.node,nodes.Name)and n.node.node.name=='adapter':
    name=n.node.attr
    if name=='get_relation':
     args={v.key:expr(v.value)if not(isinstance(v.value,nodes.Getattr)and v.value.attr=='database')else'fixed_database'for v in n.kwargs};assert args.get('schema')=='main';identifier=args['identifier'];assert identifier in ('stg_ga__sessions','stg_ga__events','int_sessions_events_joined');calls.append({'method':name,'identifier':identifier});return {'relation':identifier}if identifier!='int_sessions_events_joined'or metadata['optional_joined']['exists']else None
    if name=='get_columns_in_relation':
     relation=expr(n.args[0]);assert relation['relation']=='int_sessions_events_joined';calls.append({'method':name,'identifier':relation['relation']});return [{'name':v[0]}for v in columns]
  raise ValueError('Unsupported template expression '+type(n).__name__)
 def visit(body):
  for n in body:
   if isinstance(n,nodes.Assign):
    value=expr(n.node)
    if isinstance(n.target,nodes.Name):assert n.target.name not in ('adapter','execute','target');env[n.target.name]=value
    elif isinstance(n.target,nodes.NSRef):env[n.target.name][n.target.attr]=value
    else:raise ValueError('Unknown assignment')
   elif isinstance(n,nodes.If):
    if n.elif_:raise ValueError('Elif branch not in this limited template evaluator')
    visit(n.body if expr(n.test)else n.else_)
   elif isinstance(n,nodes.For):
    assert isinstance(n.target,nodes.Name)and not n.recursive and n.test is None
    for value in expr(n.iter):env[n.target.name]=value;visit(n.body)
   elif isinstance(n,nodes.Output):continue
   else:raise ValueError('Unknown template statement '+type(n).__name__)
 # Revenue conditional contains elif and SQL output only. Adapter inspection is
 # performed in the leading assignments/if loop, before the SQL Output chunk.
 prefix=[]
 for n in astnode.body:
  if isinstance(n,nodes.Output):
   if any(isinstance(v,nodes.TemplateData)and 'WITH sessions_base'in v.data for v in n.nodes):break
   continue
  prefix.append(n)
 all_calls=list(astnode.find_all(nodes.Call));named_unknown=[n for n in all_calls if isinstance(n.node,nodes.Name)and n.node.name not in ('config','namespace')]
 methods=[n.node.attr for n in all_calls if isinstance(n.node,nodes.Getattr)and isinstance(n.node.node,nodes.Name)and n.node.node.name=='adapter']
 if not {'get_relation','get_columns_in_relation'}<=set(methods):
  if named_unknown:raise ValueError('Unresolved helper-macro introspection path')
  return {'status':'scored','reward':0.0,'reason':'No required adapter introspection calls in the complete captured template; no helper macro path is present.'}
 visit(prefix);used={c['method']for c in calls if c['identifier']=='int_sessions_events_joined'}
 if used!={'get_relation','get_columns_in_relation'}:raise ValueError('Actual original-input leading branch did not establish both optional-source adapter operations')
 return {'status':'scored','reward':1.0,'source_control_flow':calls,'scope':'Bounded source-control-flow evaluation from actual initial metadata plus fresh successful dbt render. This is not claimed as instrumented runtime method telemetry; unknown helper/template forms remain unknown.'}
def evaluate(request,root,inputs):
 from posixpath import normpath
 f=json.loads(read(request['workflow'],root));assert f['producer']in('locked_qwen_clean_rebuild','task_image_selftest_clean_build')and f['snapshot']==request['snapshot'];checks={}
 if f.get('initial_required_models_absent'):return {'status':'scored','reward':0.0,'reason':'Trusted initial required model absent'}
 for name in ('dbt_project.yml','profiles.yml'):
  if name not in f['observed_files']:raise ValueError('Missing captured configuration inventory')
  checks[name]=f['observed_files'][name]['exists']
 if not all(checks.values()):return {'status':'scored','reward':0.0,'checks':checks}
 cfg=yaml.safe_load(read(f['project_source'],root));profiles=resolve(yaml.safe_load(read(f['profile_source'],root)),f.get('profile_environment',{}));name=cfg.get('profile');checks['profile_name']=name=='retail_dw_master'
 if name not in profiles:return {'status':'scored','reward':0.0,'checks':checks,'reason':'Selected profile absent'}
 target=profiles[name]['outputs'][profiles[name]['target']];checks['target']=target['type']=='duckdb'and normpath(target['path'])==f['actual_database'];checks['project_location']=normpath(f['project_root'])=='/app/dbt_project';checks['profiles_environment']=isinstance(f['environment'].get('DBT_PROFILES_DIR'),str)and normpath(f['environment']['DBT_PROFILES_DIR'])==normpath(f['project_root']);echo=json.loads(read(f['backend_shell'],root));checks['backend']=echo['command']=='echo "$DB_TYPE"'and echo['exit_code']==0 and echo['output'].strip()=='duckdb';checks['echo_before_code']=datetime.fromisoformat(echo['finished_at_utc'].replace('Z','+00:00'))<datetime.fromisoformat(f['source_install_after_utc'].replace('Z','+00:00'))
 if not all(checks.values()):return {'status':'scored','reward':0.0,'checks':checks}
 model='rpt_multi_touch_attribution_fixed';manifest=json.loads(read(f['manifest'],root));nodes=[n for n in manifest['nodes'].values()if n.get('resource_type')=='model'and n['name']==model];assert len(nodes)==1;node=nodes[0];template=inspect_template(node['raw_code'],inputs['metadata'])
 if template['reward']!=1:return template
 captured=json.loads(read(request['parsed_snapshot'],root));assert captured['manifest_sha256']==f['manifest']['sha256'];p=captured['parsed_compiled_sql'][model];assert p['compiled_sql_sha256']==hashlib.sha256(node['compiled_code'].encode()).hexdigest();active=active_nodes(p['ast']);functions=[n for n in active if n.get('class')=='FUNCTION'];checks['fixed_clock']=not any(n.get('function_name')in ('current_date','current_timestamp','today','now')for n in functions);checks['no_interval']=not any(n.get('class')=='CAST'and n.get('cast_type',{}).get('id')=='INTERVAL'for n in active)
 def date_operand(n):return n.get('class')=='CAST'and n.get('cast_type',{}).get('id')in ('DATE','TIMESTAMP')or n.get('class')=='COLUMN_REF'and n.get('column_names',[])[-1:]in (['attribution_date'],['session_start'])
 checks['no_date_subtraction']=not any(n.get('function_name')=='-'and len(n.get('children',[]))==2 and all(date_operand(v)for v in n['children'])for n in functions)
 return {'status':'scored','reward':float(all(checks.values())),'checks':checks,'introspection':template,'scope':'Actual required project/profile/backend chronology and bounded optional-source template control flow tied to fresh build. Active compiled date arithmetic checked; table equality alone does not certify these workflow clauses.'}
