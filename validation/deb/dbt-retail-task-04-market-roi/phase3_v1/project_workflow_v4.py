"""Trusted project/config and parsed window-form checks, independent of output rows."""
from pathlib import PurePosixPath
from datetime import datetime
import ast,hashlib,json,re,yaml
from portable_dateadd import portable_dateadd

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

MODEL='rpt_channel_roi_payback_monthly'
ALLOWED={'stg_fact_marketing_spend','stg_analytics__dim_date','stg_analytics__fact_sales','stg_analytics__dim_customer','stg_analytics__dim_channel'}
REQUIRED_NULL={'month_start','channel_key','impressions','clicks','conversions','spend_amount'}
KEY={'month_start','channel_key','segment_name','tier_name'}
def evaluate(request,root):
 from posixpath import normpath
 f=json.loads(read(request['workflow'],root));assert f['producer']in('locked_qwen_clean_rebuild','task_image_selftest_clean_build')and f['snapshot']==request['snapshot'];checks={}
 if f.get('initial_required_models_absent'):return {'status':'scored','reward':0.0,'reason':'Trusted initial required model absent'}
 yp='models/marts/marketing/'+MODEL+'.yml'
 for name in ('dbt_project.yml','profiles.yml',yp):
  if name not in f['observed_files']:raise ValueError('Missing captured configuration/file inventory')
  checks[name]=f['observed_files'][name]['exists']
 if not all(checks.values()):return {'status':'scored','reward':0.0,'checks':checks}
 cfg=yaml.safe_load(read(f['project_source'],root));profiles=resolve(yaml.safe_load(read(f['profile_source'],root)),f.get('profile_environment',{}));name=cfg.get('profile');checks['profile_name']=name=='retail_dw_master'
 if name not in profiles:return {'status':'scored','reward':0.0,'checks':checks,'reason':'Selected profile absent'}
 target=profiles[name]['outputs'][profiles[name]['target']];checks['target']=target['type']=='duckdb'and normpath(target['path'])==f['actual_database'];checks['project_location']=normpath(f['project_root'])=='/app/dbt_models_duckdb';checks['profiles_environment']=isinstance(f['environment'].get('DBT_PROFILES_DIR'),str)and normpath(f['environment']['DBT_PROFILES_DIR'])==normpath(f['project_root']);echo=json.loads(read(f['backend_shell'],root));checks['backend']=echo['command']=='echo "$DB_TYPE"'and echo['exit_code']==0 and echo['output'].strip()=='duckdb';checks['echo_before_code']=datetime.fromisoformat(echo['finished_at_utc'].replace('Z','+00:00'))<datetime.fromisoformat(f['source_install_after_utc'].replace('Z','+00:00'))
 if not all(checks.values()):return {'status':'scored','reward':0.0,'checks':checks}
 doc=yaml.safe_load(read(f['observed_files'][yp]['file'],root));checks['yaml_version']=isinstance(doc,dict)and doc.get('version')==2
 if not checks['yaml_version']:return {'status':'scored','reward':0.0,'checks':checks}
 entries=[v for v in doc.get('models',[])if v.get('name')==MODEL];checks['yaml_model']=len(entries)==1
 if not checks['yaml_model']:return {'status':'scored','reward':0.0,'checks':checks}
 entry=entries[0]
 def tests(v):return v.get('tests',[])+v.get('data_tests',[])
 def testname(v):return v if isinstance(v,str)else next(iter(v),'')
 columns={v['name']:v for v in entry.get('columns',[])};checks['yaml_not_null']=all(n in columns and'not_null'in{testname(t)for t in tests(columns[n])}for n in REQUIRED_NULL)
 unique=[]
 for t in tests(entry):
  if isinstance(t,dict)and'dbt_utils.unique_combination_of_columns'in t:
   args=t['dbt_utils.unique_combination_of_columns']or{};args=args.get('arguments',args);unique.append(args.get('combination_of_columns',[]))
 checks['yaml_unique']=any(len(v)==4 and set(v)==KEY for v in unique);checks['no_documentation_text']=not any(bool(v.get('description'))for v in walk(doc))
 manifest=json.loads(read(f['manifest'],root));models=[n for n in manifest['nodes'].values()if n.get('resource_type')=='model'and n['name']==MODEL];assert len(models)==1;node=models[0];checks['no_documentation_blocks']=not re.search(r'{%\s*docs\b|{{\s*doc\s*\(',node['raw_code'])
 if not all(checks.values()):return {'status':'scored','reward':0.0,'checks':checks}
 captured=json.loads(read(request['parsed_snapshot'],root));assert captured['manifest_sha256']==f['manifest']['sha256'];p=captured['parsed_compiled_sql'][MODEL];assert p['compiled_sql_sha256']==hashlib.sha256(node['compiled_code'].encode()).hexdigest();active=active_nodes(p['ast']);functions=[n for n in active if n.get('class')=='FUNCTION'];divisions=[v for v in functions if v.get('function_name')=='/'];unresolved=[]
 for v in divisions:
  denominator=v['children'][1]
  while denominator.get('class')=='CAST':denominator=denominator['child']
  if denominator.get('function_name')!='nullif':
   if denominator.get('class')=='COLUMN_REF':unresolved.append('division uses an alias; NULLIF lineage not captured by bounded validator')
   else:checks['all_divisions_nullif']=False
 portable=portable_dateadd(node['raw_code'],manifest);checks.setdefault('all_divisions_nullif',True);checks['dateadd_business_form']=any(v.get('function_name')=='dateadd'for v in functions)or portable;checks['no_business_interval']=portable or not any(v.get('class')=='CAST'and v.get('cast_type',{}).get('id')=='INTERVAL'for v in active)
 # Walk only reachable compiled references. A direct staging relation is an
 # allowed boundary; uncaptured helper-view lineage is unknown, not forbidden.
 leafrefs=[v for v in active if v.get('type')=='BASE_TABLE'and v.get('schema_name')]
 if any(v.get('schema_name','').lower()!='main'or v.get('catalog_name','').lower()not in('',node['database'].lower())for v in leafrefs):raise ValueError('Unresolved source namespace: bounded original-input audit certifies the captured main staging relations in the active database only')
 refs={v.get('table_name','').lower()for v in leafrefs};external=refs-ALLOWED
 if external:raise ValueError('Unresolved active helper/relation lineage: '+str(sorted(external)))
 checks['required_staging_inputs']=ALLOWED<=refs
 if not all(checks.values()):return {'status':'scored','reward':0.0,'checks':checks}
 if unresolved:raise ValueError('; '.join(unresolved))
 test=f.get('required_test_execution')
 if test is None:raise ValueError('Required trusted test invocation not captured')
 if test['returncode']!=0:return {'status':'scored','reward':0.0,'checks':checks,'reason':'Actual required dbt tests failed'}
 assert 'test'in test['argv'];tm=json.loads(read(test['manifest'],root));tr=json.loads(read(test['run_results'],root));assert tm['metadata']['invocation_id']==tr['metadata']['invocation_id']
 for artifact in(tm,tr):assert test['started_unix']-1<=datetime.fromisoformat(artifact['metadata']['generated_at'].replace('Z','+00:00')).timestamp()<=test['finished_unix']+1
 tnode=tm['nodes'][node['unique_id']];assert tnode['raw_code']==node['raw_code'];results={v['unique_id']:v for v in tr['results']};found=[];nonnull=set();unique_ok=False
 for uid,n in tm['nodes'].items():
  if n.get('resource_type')!='test'or node['unique_id']not in n.get('depends_on',{}).get('nodes',[]):continue
  m=n.get('test_metadata',{});args=m.get('kwargs',{});isrequired=m.get('name')=='not_null'and args.get('column_name')in REQUIRED_NULL or m.get('namespace')=='dbt_utils'and m.get('name')=='unique_combination_of_columns'and set(args.get('combination_of_columns',[]))==KEY
  if not isrequired:continue
  found.append(uid);checks['test_pass:'+uid]=uid in results and results[uid]['status']=='pass'and not results[uid].get('failures')
  if m.get('name')=='not_null':nonnull.add(args['column_name'])
  else:unique_ok=True
 checks['required_tests_executed']=nonnull==REQUIRED_NULL and unique_ok
 return {'status':'scored','reward':float(all(checks.values())),'checks':checks,'test_invocation_id':tm['metadata']['invocation_id'],'tests':found,'scope':'Fresh trusted run and test artifacts plus original required YAML/source/profile/backend chronology. Active SQL DATEADD and NULLIF forms checked independently of output values; day-only compatibility macro DDL is not a forbidden business interval expression.'}
