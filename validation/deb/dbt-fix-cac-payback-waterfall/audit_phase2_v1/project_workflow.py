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
def evaluate(request,root):
 f=json.loads(read(request['workflow'],root));assert f['producer']in('locked_qwen_clean_rebuild','task_image_selftest_clean_build')and f['snapshot']==request['snapshot'];checks={}
 if f.get('initial_required_models_absent'):return {'status':'scored','reward':0.0,'reason':'Trusted initial required model inventory absent'}
 for name in('dbt_project.yml','profiles.yml'):
  if name not in f['observed_files']:raise ValueError('Required config file not captured')
  checks['file:'+name]=f['observed_files'][name]['exists']
 if not all(checks.values()):return {'status':'scored','reward':0.0,'checks':checks}
 cfg=yaml.safe_load(read(f['project_source'],root));profiles=resolve(yaml.safe_load(read(f['profile_source'],root)),f.get('profile_environment',{}));name=cfg.get('profile')
 if name not in profiles:return {'status':'scored','reward':0.0,'reason':'Configured profile absent'}
 target=profiles[name]['outputs'][profiles[name]['target']];checks['profile_name']=name=='retail_dw_master';checks['target']=target['type']=='duckdb'and PurePosixPath(target['path'])==PurePosixPath(f['actual_database'])and str(target['schema']).casefold()=='analytics';checks['project_location']=PurePosixPath(f['project_root'])==PurePosixPath('/app/dbt_project');echo=json.loads(read(f['backend_shell'],root));checks['actual_backend_checked']=echo['exit_code']==0 and echo['output'].strip()=='duckdb'
 # This task does not mandate DBT_PROFILES_DIR or echo-before-code chronology.
 # Fresh trusted manifest/profile target binding is established independently.
 return {'status':'scored','reward':float(all(checks.values())),'checks':checks,'scope':'Actual configured profile, backend and project location. No extra environment-variable, SQL hash, model-schema-config prohibition or chronology requirement.'}
