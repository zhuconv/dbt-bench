"""Trusted backend chronology, profile resolution and source/ref graph."""
from datetime import datetime
import hashlib,json
import yaml

def read(ref,root):
    value=(root/ref['path']).read_bytes()
    if hashlib.sha256(value).hexdigest()!=ref['sha256']:raise ValueError('Changed artifact')
    return value

def evaluate(request,root,models):
    facts=json.loads(read(request['workflow'],root))
    if facts.get('producer')not in('locked_qwen_clean_rebuild','task_image_selftest_clean_build')or facts['snapshot']!=request['snapshot']:
        raise ValueError('Untrusted/mismatched reconstruction')
    if facts.get('initial_required_models_absent'):
        return {'status':'scored','reward':0.0,'checks':{'models_present':False},'reason':'Actual initial inventory lacks task model sources'}
    cfg=yaml.safe_load(read(facts['project_source'],root));profiles=yaml.safe_load(read(facts['profile_source'],root));profile=profiles['retail_dw_master'];target=profile['outputs'][profile['target']]
    checks={'profile':cfg['profile']=='retail_dw_master'and target['type']=='duckdb'and target['path']==facts['actual_database'],
            'profile_environment':facts['environment'].get('DBT_PROFILES_DIR')==facts['project_root']}
    echo=json.loads(read(facts['backend_shell'],root));checks['live_backend_echo']=echo.get('command')=='echo "$DB_TYPE"'and echo.get('exit_code')==0 and echo.get('output','').strip()=='duckdb';checks['echo_before_source_install']=datetime.fromisoformat(echo['finished_at_utc'].replace('Z','+00:00'))<datetime.fromisoformat(facts['source_install_after_utc'].replace('Z','+00:00'))
    manifest=json.loads(read(facts['manifest'],root));nodes=manifest['nodes'];source_nodes=manifest['sources'];selected={}
    for role,name in models.items():
        matches=[(uid,node)for uid,node in nodes.items()if node.get('resource_type')=='model'and node.get('name')==name]
        if len(matches)!=1:raise ValueError('Required model node absent/ambiguous')
        selected[role]=matches[0]
    def ancestors(uid,seen=None):
        seen=set()if seen is None else seen
        for child in nodes[uid]['depends_on']['nodes']if uid in nodes else []:
            if child not in seen:seen.add(child);ancestors(child,seen)
        return seen
    source_ids=ancestors(selected['staging'][0])&source_nodes.keys()
    resolved={(source_nodes[x]['source_name'].casefold(),source_nodes[x]['name'].casefold(),source_nodes[x]['schema'].casefold(),source_nodes[x]['identifier'].casefold())for x in source_ids}
    for name in('orders','order_lines'):
        checks['enterprise_source:'+name]=any(s=='enterprise_db'and n==name and schema in('main','orders')and table==name for s,n,schema,table in resolved)
    checks['intermediate_lineage']=selected['staging'][0]in ancestors(selected['intermediate'][0]);checks['mart_lineage']=selected['intermediate'][0]in ancestors(selected['mart'][0])
    return {'status':'scored','reward':float(all(checks.values())),'checks':checks,'reason':'Actual fresh manifest source/ref graph, project-local profile and pre-install backend shell; no source SQL hash whitelist'}
