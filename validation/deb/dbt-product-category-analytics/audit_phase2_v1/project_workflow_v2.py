"""Project/profile/backend/source-graph checks over trusted reconstruction captures."""
from datetime import datetime
import ast,hashlib,json
from pathlib import Path
import yaml

def read(ref,root):
    data=(root/ref['path']).read_bytes()
    if hashlib.sha256(data).hexdigest()!=ref['sha256']:raise ValueError('Changed trusted artifact')
    return data

def evaluate(request,root,models):
    facts=json.loads(read(request['workflow'],root))
    if facts.get('producer')not in('locked_qwen_clean_rebuild','task_image_selftest_clean_build'):
        raise ValueError('Not a trusted reconstruction producer')
    if facts['snapshot']!=request['snapshot']:raise ValueError('Wrong capture binding')
    if facts.get('initial_required_models_absent'):
        return {'status':'scored','reward':0.0,'reason':'Trusted initial source inventory lacks required task models','checks':{'models_present':False}}
    cfg=yaml.safe_load(read(facts['project_source'],root));profiles=yaml.safe_load(read(facts['profile_source'],root))
    profile=profiles['dbt_project'];target=profile['outputs'][profile['target']]
    checks={'profile':cfg['profile']=='dbt_project'and target['type']=='duckdb'and target['path']==facts['actual_database']and target['schema']=='sales_analytics'}
    invocation=facts['invocation'];argv=invocation['argv'];env=facts['environment']
    explicit=env.get('DBT_PROFILES_DIR')==facts['project_root'] or any(v=='--profiles-dir'and i+1<len(argv)and argv[i+1]==facts['project_root']for i,v in enumerate(argv))
    resolution='explicit_project_profile' if explicit else None
    # dbt's current-image actual debug output records its resolved profiles_dir.
    # It is part of the trusted invocation log, not a candidate-written manifest.
    if not explicit:
        for line in read(invocation['log'],root).decode().splitlines():
            marker='running dbt with arguments '
            if marker not in line:continue
            args=ast.literal_eval(line.split(marker,1)[1])
            if args.get('profiles_dir')==facts['project_root']:resolution='actual_dbt_resolved_project_profile'
    checks['profile_resolution']=True if resolution else None
    echo=json.loads(read(facts['backend_shell'],root))
    checks['live_backend_echo']=echo.get('command')=='echo "$DB_TYPE"'and echo.get('exit_code')==0 and echo.get('output','').strip()=='duckdb'
    checks['echo_before_source_install']=datetime.fromisoformat(echo['finished_at_utc'].replace('Z','+00:00'))<datetime.fromisoformat(facts['source_install_after_utc'].replace('Z','+00:00'))
    manifest=json.loads(read(facts['manifest'],root));nodes=manifest['nodes'];source_nodes=manifest['sources'];selected={}
    for role,name in models.items():
        matches=[(uid,node)for uid,node in nodes.items()if node.get('resource_type')=='model'and node.get('name')==name]
        if len(matches)!=1:raise ValueError('Model role is ambiguous or absent')
        selected[role]=matches[0]
    def sources(uid,seen=None):
        seen=set()if seen is None else seen
        if uid in seen:return set()
        seen.add(uid)
        if uid in source_nodes:return {uid}
        return set().union(*(sources(v,seen)for v in nodes[uid]['depends_on']['nodes']))if uid in nodes else set()
    expected={'lines':('orders','order_lines'),'products':('product','products'),
              'categories':('product','product_categories'),'orders':('main','orders')}
    for role,pair in expected.items():
        uid,node=selected[role];physical={(str(source_nodes[v]['schema']).casefold(),str(source_nodes[v]['identifier']).casefold())for v in sources(uid)}
        checks['source:'+role]=pair in physical and node['original_file_path'].startswith('models/staging/')
    def ancestors(uid,seen=None):
        seen=set()if seen is None else seen
        for child in nodes[uid]['depends_on']['nodes']if uid in nodes else []:
            if child not in seen:seen.add(child);ancestors(child,seen)
        return seen
    checks['intermediate_lineage']=all(selected[r][0]in ancestors(selected['intermediate'][0])for r in expected)
    checks['mart_lineage']=selected['intermediate'][0]in ancestors(selected['final'][0])
    reward=0.0 if any(v is False for v in checks.values())else None if any(v is None for v in checks.values())else 1.0
    return {'status':'unknown'if reward is None else'scored','reward':reward,'checks':checks,'profile_resolution_evidence':resolution,
            'reason':'Explicit or actually logged default profile resolution, pre-install backend echo and fresh dbt dependency graph; staging role names may vary and no SQL implementation is whitelisted'}
