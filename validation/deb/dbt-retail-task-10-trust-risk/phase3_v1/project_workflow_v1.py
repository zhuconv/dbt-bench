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

MODELS=['int_reviews__enriched','fct_review_moderation_risk','rpt_review_moderation_kpis']
ALLOWED={'stg_product__product_reviews','stg_orders__orders','stg_orders__order_lines','stg_orders__order_fraud_scores','stg_orders__returns','stg_orders__return_lines','stg_coupon_usage','stg_customer__customers','stg_product_ratings_summary','stg_events'}
FLAGS={'rating_outlier_flag','coupon_used_flag','return_flag','fraud_high_flag','unverified_purchase_flag','customer_velocity_flag','product_velocity_flag','low_content_flag'}
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
def canonical(x):
 if isinstance(x,list):return [canonical(v)for v in x]
 if not isinstance(x,dict):return x
 return {k:canonical(v)for k,v in x.items()if k not in('query_location','alias')}

def evaluate(request,root):
 from posixpath import normpath
 from jinja2 import Environment,nodes
 f=json.loads(read(request['workflow'],root));assert f['producer']in('locked_qwen_clean_rebuild','task_image_selftest_clean_build')and f['snapshot']==request['snapshot'];checks={}
 if f.get('initial_required_models_absent'):return {'status':'scored','reward':0.0,'reason':'Trusted initial required model absent'}
 for name in('dbt_project.yml','profiles.yml','models/intermediate/reviews/schema.yml','models/marts/reviews/schema.yml'):
  if name not in f['observed_files']:raise ValueError('Missing captured file inventory: '+name)
  checks[name]=f['observed_files'][name]['exists']
 if not all(checks.values()):return {'status':'scored','reward':0.0,'checks':checks}
 cfg=yaml.safe_load(read(f['project_source'],root));profiles=yaml.safe_load(read(f['profile_source'],root));name=cfg.get('profile');checks['profile_name']=name=='retail_dw_master'
 if name not in profiles:return {'status':'scored','reward':0.0,'checks':checks,'reason':'Selected profile absent'}
 target_name=resolve(profiles[name]['target'],f.get('profile_environment',{}));target=resolve(profiles[name]['outputs'][target_name],f.get('profile_environment',{}));checks['target']=target['type']=='duckdb'and normpath(target['path'])==f['actual_database'];checks['project_location']=normpath(f['project_root'])=='/app/dbt_models_duckdb';checks['profiles_environment']=isinstance(f['environment'].get('DBT_PROFILES_DIR'),str)and normpath(f['environment']['DBT_PROFILES_DIR'])==normpath(f['project_root']);echo=json.loads(read(f['backend_shell'],root));checks['backend']=echo['command']=='echo "$DB_TYPE"'and echo['exit_code']==0 and echo['output'].strip()=='duckdb';checks['echo_before_code']=datetime.fromisoformat(echo['finished_at_utc'].replace('Z','+00:00'))<datetime.fromisoformat(f['source_install_after_utc'].replace('Z','+00:00'))
 manifest=json.loads(read(f['manifest'],root));captured=json.loads(read(request['parsed_snapshot'],root));assert captured['manifest_sha256']==f['manifest']['sha256'];asts={}
 for model in MODELS:
  ns=[v for v in manifest['nodes'].values()if v.get('resource_type')=='model'and v['name']==model]
  if len(ns)!=1:raise ValueError('Required model not uniquely captured')
  n=ns[0];folder='models/intermediate/reviews'if model==MODELS[0]else'models/marts/reviews';checks[model+':folder']=PurePosixPath(n['original_file_path']).parent==PurePosixPath(folder);checks[model+':materialization']=n['config']['materialized']=='view';checks[model+':tags']=({'intermediate'if model==MODELS[0]else'marts','reviews','trust-risk'})<=set(n['config']['tags']);calls=list(Environment().parse(n['raw_code']).find_all(nodes.Call));checks[model+':no_source']=not n.get('sources')and not any(isinstance(v.node,nodes.Name)and v.node.name=='source'for v in calls)
  declared={v['name']if isinstance(v,dict)else v[-1]for v in n['refs']};checks[model+':refs']=declared==ALLOWED if model==MODELS[0]else declared<=(ALLOWED|set(MODELS))
  parsed=captured['parsed_compiled_sql'][model];assert parsed['compiled_sql_sha256']==hashlib.sha256(n['compiled_code'].encode()).hexdigest();active=active_nodes(parsed['ast']);asts[model]=active;leaves=[v for v in active if v.get('type')=='BASE_TABLE'and v.get('schema_name')];allowed_relations={(manifest['nodes'][uid]['schema'].lower(),manifest['nodes'][uid]['alias'].lower())for uid in n['depends_on']['nodes']if uid in manifest['nodes']};checks[model+':all_relations_via_ref']=all((v['schema_name'].lower(),v['table_name'].lower())in allowed_relations and v.get('catalog_name','').lower()in('',n['database'].lower())for v in leaves)
 # The schema is an actual submission requirement. Successful dbt run is
 # checked separately; no unrequested dbt test execution is invented.
 docs=[]
 for p in('models/intermediate/reviews/schema.yml','models/marts/reviews/schema.yml'):
  doc=yaml.safe_load(read(f['observed_files'][p]['file'],root));checks[p+':version']=isinstance(doc,dict)and doc.get('version')==2;docs.extend(doc.get('models',[])if isinstance(doc,dict)else[])
 for model in MODELS:
  entries=[v for v in docs if v.get('name')==model];checks[model+':yaml_entry']=len(entries)==1
  if len(entries)!=1:continue
  e=entries[0];checks[model+':description']=isinstance(e.get('description'),str)and bool(e['description'].strip());cols={v['name']:v for v in e.get('columns',[])}
  if model==MODELS[-1]:checks[model+':descriptive_columns']=bool(cols)and all(isinstance(v.get('description'),str)and bool(v['description'].strip())for v in cols.values())
  else:
   c=cols.get('review_id',{});tests=c.get('tests',[])+c.get('data_tests',[]);names={v if isinstance(v,str)else next(iter(v),'')for v in tests};checks[model+':review_id_tests']=({'not_null','unique'}if model==MODELS[1]else{'not_null'})<=names
 enrich=asts[MODELS[0]];fact=asts[MODELS[1]];kpi=asts[MODELS[2]]
 def select_stage(stage):return [v for v in enrich if v.get('type')=='SELECT_NODE'and v.get('from_table',{}).get('table_name','').lower()==stage]
 def has_alias(stage,source,dest):return any(any(e.get('alias','').lower()==dest and bare(e).get('class')=='COLUMN_REF'and bare(e)['column_names'][-1].lower()==source for e in n.get('select_list',[]))for n in select_stage(stage))
 checks['reviews_cte_mapping']=has_alias('stg_product__product_reviews','status','review_status')and any({'review_ts','review_date'}<={e.get('alias')for e in n.get('select_list',[])}for n in select_stage('stg_product__product_reviews'))
 checks['orders_cte_mapping']=has_alias('stg_orders__orders','customer_id','order_customer_id')and has_alias('stg_orders__orders','status','order_status')
 checks['lines_cte_mapping']=has_alias('stg_orders__order_lines','status','line_status')and all('quantity'not in refs(n.get('select_list',[]))for n in select_stage('stg_orders__order_lines'))
 checks['needed_field_selection']=all(select_stage(stage)and not any(e.get('class')=='STAR'for n in select_stage(stage)for e in n.get('select_list',[]))for stage in('stg_product__product_reviews','stg_orders__orders','stg_orders__order_lines'))
 checks['fraud_aliases']=all(has_alias('stg_orders__order_fraud_scores',src,dst)for src,dst in {'score':'fraud_score_value','risk_level':'fraud_risk_level','provider':'fraud_provider','reviewed_by':'fraud_reviewed_by','reviewed_at':'fraud_reviewed_at'}.items())
 checks['return_status_alias']=has_alias('stg_orders__returns','status','return_status')
 windows=[v for v in enrich if v.get('class')=='WINDOW'and v.get('function_name')=='row_number'and refs(v.get('partitions'))=={'review_id'}];checks['matching_ranking']=any(len(v['orders'])>=3 and v['orders'][0]['expression'].get('class')=='CASE'and 'order_id'in refs(v['orders'][0])and v['orders'][1]['type']=='DESCENDING'and v['orders'][1]['null_order']=='NULLS_LAST'and refs(v['orders'][1])=={'ordered_at'}and v['orders'][2]['type']in('ASCENDING','ORDER_DEFAULT')and refs(v['orders'][2])=={'line_number'}for v in windows)
 joins=[v for v in enrich if v.get('type')=='JOIN'and v.get('join_type')=='INNER'];checks['matching_date_in_join']=any(any(z.get('type')=='CONJUNCTION_OR'and {'ordered_at','review_ts'}<=refs(z)and any(w.get('type')=='COMPARE_LESSTHANOREQUALTO'for w in walk(z))and any(w.get('type')=='OPERATOR_IS_NULL'and refs(w)=={'review_ts'}for w in walk(z))for z in walk(v.get('condition')))for v in joins)
 checks['events_timestamp_fallback']=any(any(v.get('class')=='CAST'and v.get('cast_type',{}).get('id')=='TIMESTAMP'and refs(v.get('child'))=={'created_at'}for v in walk(n))for n in select_stage('stg_events'))
 flag_exprs={flag:[v for v in fact if v.get('alias')==flag and bare(v).get('class')=='CASE']for flag in FLAGS};checks['all_flags_case_else_false']=all(any(constant(bare(v)['else_expr'])is False and all(constant(c['then_expr'])in(True,False)for c in bare(v)['case_checks'])for v in vs)for vs in flag_exprs.values())
 checks['datediff_spelling']=not any(v.get('function_name')=='date_diff'for v in fact)and any(v.get('function_name')=='datediff'for v in fact)
 score_nodes=[v for v in fact if v.get('type')=='SELECT_NODE'and any(e.get('alias')=='score_deductions'and bare(e).get('function_name')=='+'for e in v.get('select_list',[]))];scorepairs=[]
 for n in score_nodes:
  ds=[e for e in n['select_list']if e.get('alias')=='score_deductions'];ss=[e for e in n['select_list']if e.get('alias')=='trust_score'and bare(e).get('function_name')=='greatest']
  if ds and ss:scorepairs.append((ds[0],ss[0]))
 checks['same_cte_score_and_deductions']=bool(scorepairs);checks['repeat_full_penalty_expression']=any('score_deductions'not in refs(s)and sorted(json.dumps(canonical(e),sort_keys=True)for e in walk(d)if e.get('class')=='CASE')==sorted(json.dumps(canonical(e),sort_keys=True)for e in walk(s)if e.get('class')=='CASE')for d,s in scorepairs)
 risk=[v for v in fact if v.get('alias')=='risk_bucket'and bare(v).get('class')=='CASE'];checks['risk_uses_score_column']=any(refs(v)=={'trust_score'}for v in risk)
 share=[v for v in kpi if v.get('alias')=='verified_purchase_share'and bare(v).get('function_name')=='round'];checks['kpi_round_nullif']=any(any(e.get('function_name')=='nullif'for e in walk(v))and constant(bare(v)['children'][-1])==2 and not any(e.get('class')=='CASE'and any(q.get('function_name')in('count','count_star')for c in e['case_checks']for q in walk(c['when_expr']))for e in walk(v))for v in share)
 return {'status':'scored','reward':float(all(checks.values())),'checks':checks,'scope':'Actual captured source/config/YAML/build plus prescribed field mapping, matching rank/join, explicit flags, same-CTE repeated penalty and KPI SQL forms; AST identities never whitelist a SQL hash. Complete original input values independently checked separately, inactive data branches disclosed.'}
