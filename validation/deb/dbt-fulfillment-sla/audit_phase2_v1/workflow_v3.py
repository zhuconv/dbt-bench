"""Policies over trusted execution/source captures; candidate logs are insufficient."""
from datetime import datetime
import yaml

MODELS={'fraud':{'stg_fd_orders':'models/staging/fraud/stg_fd_orders.sql','stg_fd_order_lines':'models/staging/fraud/stg_fd_order_lines.sql','stg_fd_addresses':'models/staging/fraud/stg_fd_addresses.sql','fraud_flagged_orders':'models/marts/fraud/fraud_flagged_orders.sql'},'fulfillment':{'int_shipment_times':'models/intermediate/int_shipment_times.sql',**{n:'models/marts/analytics/'+n+'.sql'for n in ['fulfillment_sla','carrier_performance','warehouse_performance','fulfillment_summary']}}}
PROJECT='/app/dbt_models_duckdb'
def chk(s,r,**x):return {'status':s,'reason':r,**x}
def walk(x):
    if isinstance(x,dict):
        yield x
        for v in x.values():yield from walk(v)
    elif isinstance(x,list):
        for v in x:yield from walk(v)

def active_nodes(ast):
    """Exclude unused CTEs from the parser tree before workflow inspection."""
    roots=[s['node'] for s in ast.get('statements',[])];result=[]
    def visit(value, inherited):
        if isinstance(value,list):
            for item in value:visit(item,inherited)
            return
        if not isinstance(value,dict):return
        ctes=dict(inherited)
        for item in value.get('cte_map',{}).get('map',[]):ctes[item['key'].lower()]=item['value']['query']['node']
        result.append(value)
        name=value.get('table_name','').lower()
        if value.get('type')=='BASE_TABLE'and not value.get('schema_name')and name in ctes:
            visit(ctes[name],{k:v for k,v in ctes.items()if k!=name})
        for key,child in value.items():
            if key!='cte_map':visit(child,ctes)
    for node in roots:visit(node,{})
    return result

def function_workflow(kind, ast):
    if not ast or ast.get('error')or len(ast.get('statements',[]))!=1:return chk('pending','Single SELECT parser evidence unavailable')
    nodes=active_nodes(ast)
    if kind=='fraud':
        windows=[n for n in nodes if n.get('class')=='WINDOW']
        if not windows:return chk('fail','Actually parsed active query has no window function; explicit velocity-window requirement violated')
        found=[]
        for n in windows:
            if n.get('function_name','').lower()not in ('count','count_star','sum'):continue
            parts=list(walk(n.get('partitions',[])))
            has_customer=any(x.get('class')=='COLUMN_REF'and x.get('column_names',[])[-1:]==['customer_id']for x in parts)
            has_day=any(x.get('class')=='CAST'and x.get('cast_type',{}).get('id')=='DATE'and any(y.get('column_names',[])[-1:]==['ordered_at']for y in walk(x))for x in parts)
            if has_customer and has_day and n.get('alias','').lower()=='orders_same_day':found.append(n)
        if found:return chk('pass','Active parsed count window partitions customer and calendar day, supplies orders_same_day; all output values independently checked')
        return chk('pending','Window exists but velocity lineage is outside the recognized proof; arbitrary unrelated window does not establish compliance')
    calls=[n for n in nodes if n.get('class')=='FUNCTION'and n.get('function_name','').lower()in ('datediff','date_diff')]
    if not calls:return chk('fail','Actually parsed active query contains no DATEDIFF/date_diff call')
    expected={('ordered_at','shipped_at'),('shipped_at','delivered_at'),('ordered_at','delivered_at')};actual=set()
    for n in calls:
        args=n.get('children',[])
        if len(args)!=3 or args[0].get('value',{}).get('value')!='day':continue
        a,b=args[1:]
        if a.get('class')=='COLUMN_REF'and b.get('class')=='COLUMN_REF':actual.add((a['column_names'][-1],b['column_names'][-1]))
    return chk('pass'if expected<=actual else'pending','Parsed DATEDIFF day calls on all three required source timestamp pairs; aliases/case/formatting not whitelisted',actual_pairs=sorted(actual))

def evaluate(kind, f):
    f=f or {};parts={n:chk('pending','Trusted actual capture unavailable')for n in ['model_delivery','profile','trusted_build','backend','required_sql_workflow']};files=f.get('files',{});manifest=f.get('manifest',{});proc=f.get('process',{});nodes={}
    for name in MODELS[kind]:nodes[name]=[(uid,n)for uid,n in manifest.get('nodes',{}).items()if n.get('resource_type')=='model'and n.get('name')==name]
    if f.get('trusted_fresh_artifact_binding'):
        ok=True
        for name,pairs in nodes.items():
            ok &=len(pairs)==1
            if len(pairs)!=1:continue
            uid,n=pairs[0];path=n.get('original_file_path');ok &=path in files
            if kind=='fulfillment':ok &=path==MODELS[kind][name] and n.get('config',{}).get('materialized')==('view'if name=='int_shipment_times'else'table')
            # Fraud specifies names, not exact source directories or staging materializations.
            if name=='fraud_flagged_orders':ok &=n.get('schema','').lower()=='fraud_analytics'
        parts['model_delivery']=chk('pass'if ok else'fail','Required model names, prescribed paths/materializations where explicit, and fresh manifest; no original SQL hash whitelist')
    inventory=f.get('trusted_model_file_inventory')
    if inventory is not None:
        from pathlib import PurePosixPath
        names={PurePosixPath(p).stem for p in inventory if PurePosixPath(p).suffix in ('.sql','.py')}
        missing=sorted(set(MODELS[kind])-names)
        if missing:parts['model_delivery']=chk('fail','Complete trusted model-file inventory lacks explicitly named dbt models',missing_models=missing)
    if f.get('trusted_process_complete'):
        try:
            config=yaml.safe_load(files['dbt_project.yml']);profiles=yaml.safe_load(files['profiles.yml']);profile=profiles['retail_dw_master'];target=profile['outputs'][profile['target']];ok=config['profile']=='retail_dw_master'and target['type']=='duckdb'and target['path']=='/app/database/retail.duckdb'and proc['environment']['DBT_PROFILES_DIR']==PROJECT
            parts['profile']=chk('pass'if ok else'fail','Actual local retail_dw_master profile, database path and environment')
        except(KeyError,ValueError,TypeError,yaml.YAMLError):parts['profile']=chk('pending'if any('{{'in files.get(n,'')for n in ['dbt_project.yml','profiles.yml'])else'fail','Required captured profile missing, invalid or unresolved')
        rr={r['unique_id']:r for r in f.get('run_results',{}).get('results',[])};ok=proc.get('returncode')==0 and proc.get('cwd')==PROJECT and proc.get('command',[])[:2]==['dbt','run']and f.get('trusted_fresh_artifact_binding')is True
        for pairs in nodes.values():ok &=len(pairs)==1 and rr.get(pairs[0][0],{}).get('status')=='success'
        parts['trusted_build']=chk('pass'if ok else'fail','Trusted successful dbt process with all required models and fresh matching invocation artifacts')
    b=f.get('backend_before_install')
    if b:
        shell=b['shell'];finished=datetime.fromisoformat(shell['finished_at_utc'].replace('Z','+00:00'));bound=datetime.fromisoformat(b['install_after_utc'].replace('Z','+00:00'));ok=shell.get('exit_code')==0 and shell.get('command')=='echo "$DB_TYPE"'and shell.get('output')in ['duckdb','duckdb\n','duckdb\\n']and finished<bound and b.get('sequential_trusted_driver_verified')is True
        parts['backend']=chk('pass'if ok else'fail','Actual live backend echo completed before first submitted code installation')
    history=f.get('trusted_candidate_command_history')
    if history and history.get('artifact_hashes_verified')is True:
        writes=history['actions'];echo=history['literal_echo'];first=min(datetime.fromisoformat(x['at'])for x in writes if x['kind']=='candidate_model_written');end=datetime.fromisoformat(echo['finished_at_utc']);valid=echo['command']==['bash','-c','echo "$DB_TYPE"']and echo['exit_code']==0 and echo['stdout']=='duckdb\n'and end<first
        parts['backend']=chk('pass'if valid else'fail','Actual controlled candidate write/echo chronology; prior harness backend probe is explicitly acknowledged, not called absent',prior_harness_backend_probe_acknowledged=history['prior_harness_probe_acknowledged'])
    if f.get('parsed_compiled_sql'):
        name='fraud_flagged_orders'if kind=='fraud'else'int_shipment_times';parts['required_sql_workflow']=function_workflow(kind,f['parsed_compiled_sql'].get(name))
    if kind=='fulfillment':
        asts=f.get('parsed_compiled_sql',{})
        parts['division_guideline']=chk('pending','Parsed current compiled divisions not captured')
        if all(name in asts for name in MODELS[kind]):
            divisions=[n for ast in asts.values() for n in active_nodes(ast) if n.get('class')=='FUNCTION'and n.get('function_name')=='/']
            casted=[n for n in divisions if any(x.get('class')=='CAST'and x.get('cast_type',{}).get('id')=='DOUBLE'for x in walk(n.get('children',[])))]
            if divisions and len(casted)==len(divisions):parts['division_guideline']=chk('pass','Every active parsed division contains an explicit DOUBLE operand conversion',divisions=len(divisions))
            else:parts['division_guideline']=chk('gap','Current source has implicit divisions. Actually executed explicit-CAST omission preserves floating values/types; literal form versus prevention purpose remains ambiguous.',divisions=len(divisions),explicit_casts=len(casted),evidence=f.get('division_guideline_gap_evidence',[]))
    states=[v['status']for v in parts.values()]
    return chk('fail'if'fail'in states else'pending'if'pending'in states else'gap'if'gap'in states else'pass','Trusted task workflow review',details=parts)
