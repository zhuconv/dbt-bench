"""Consolidate workflow policy over verified driver artifacts, never candidate assertions."""
from datetime import datetime
import yaml
from expected_v1 import TABLES,STAGING_COLUMNS
PROJECT='/app/dbt_consolidate'
MODELS={n:('models/int/'if k=='unified'else'models/staging/')+n+'.sql'for k,n in TABLES.items()}
def chk(status,reason,**more):return dict(status=status,reason=reason,**more)
def walk(value):
    if isinstance(value,dict):
        yield value
        for v in value.values():yield from walk(v)
    elif isinstance(value,list):
        for v in value:yield from walk(v)
def active_nodes(ast):
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
    for statement in ast.get('statements',[]):visit(statement['node'],{})
    return result
def sql_workflow(ast,unified=False):
    if not ast or ast.get('error')or len(ast.get('statements',[]))!=1:return chk('pending','Actual single-SELECT parser capture missing')
    nodes=active_nodes(ast)
    if unified:
        unions=[n for n in nodes if n.get('type')=='SET_OPERATION_NODE'and n.get('setop_type')=='UNION']
        if not unions or any(not n.get('setop_all')for n in unions):return chk('fail','Active parsed intermediate query omits required UNION ALL or uses duplicate-eliminating UNION')
        tables={n.get('table_name')for n in nodes if n.get('type')=='BASE_TABLE'}
        required=set(TABLES.values())-{TABLES['unified']}
        return chk('pass'if required<=tables else'pending','Active UNION ALL with all three required staging sources; unfamiliar alternate lineage remains unresolved',sources=sorted(tables))
    qualifiers=[n['qualify']for n in nodes if n.get('qualify')is not None]
    if not qualifiers:return chk('fail','Active parsed staging query has no QUALIFY clause')
    windows=[n for n in nodes if n.get('type')=='WINDOW_ROW_NUMBER']
    if not windows:return chk('fail','Active parsed staging query has no ROW_NUMBER window')
    candidates=[]
    for window in windows:
        parts=window.get('partitions',[])
        if all(n.get('class')=='COLUMN_REF'for n in parts)and {n['column_names'][-1].lower()for n in parts}==set(STAGING_COLUMNS):candidates.append(window)
    for q in qualifiers:
        for n in walk(q):
            if n.get('type')!='COMPARE_EQUAL':continue
            for a,b in [(n.get('left',{}),n.get('right',{})),(n.get('right',{}),n.get('left',{}))]:
                if b.get('class')!='CONSTANT'or b.get('value',{}).get('value')!=1:continue
                if a in candidates or(a.get('class')=='COLUMN_REF'and any(w.get('alias')==a.get('column_names',[])[-1]for w in candidates)):
                    return chk('pass','Active QUALIFY retains ROW_NUMBER=1 partitioned by all five normalized content fields')
    return chk('pending','QUALIFY and ROW_NUMBER exist but exact dedupe lineage is outside recognized proof; no SQL source whitelist')
def evaluate(f):
    parts={n:chk('pending','Trusted actual evidence unavailable')for n in ['paths','project','profile','seed','packages','yaml','unique_test','build','dbt_test','backend']}
    f=f or {};files=f.get('files',{});manifest=f.get('manifest',{});nodes={name:[(uid,n)for uid,n in manifest.get('nodes',{}).items()if n.get('resource_type')=='model'and n.get('name')==name]for name in MODELS}
    if f.get('trusted_fresh_artifact_binding'):
        ok=all(len(pairs)==1 and pairs[0][1].get('original_file_path')==MODELS[name]and MODELS[name]in files and pairs[0][1].get('schema')=='analytics'for name,pairs in nodes.items())
        parts['paths']=chk('pass'if ok else'fail','Actual model nodes map to four required source paths and analytics relations')
    if f.get('trusted_source_inventory_complete') and f.get('environment'):
        try:
            config=yaml.safe_load(files['dbt_project.yml']);profiles=yaml.safe_load(files['profiles.yml']);profile=profiles['retail_dw_master'];target=profile['outputs'][profile['target']]
            parts['project']=chk('pass'if config.get('name')=='dbt_consolidate'else'fail','Explicit project name')
            ok=config.get('profile')=='retail_dw_master'and target.get('type')=='duckdb'and target.get('path')=='/app/consolidate.duckdb'and target.get('schema')=='analytics'and f['environment'].get('DBT_PROFILES_DIR')==PROJECT
            parts['profile']=chk('pass'if ok else'fail','Actual local profile/database/schema/environment')
        except(KeyError,ValueError,TypeError,yaml.YAMLError):parts['profile']=chk('pending'if any('{{'in files.get(n,'')for n in ['dbt_project.yml','profiles.yml'])else'fail','Required captured local profile is missing, invalid or unresolved')
    if f.get('trusted_process_complete'):
        commands=f.get('trusted_command_sequence',[]);good=[(i,r)for i,r in enumerate(commands)if r.get('cwd')==PROJECT and r.get('returncode')==0];runs=[i for i,r in good if r.get('command',[])[:2]==['dbt','run']];first_run=min(runs)if runs else -1
        rr={r['unique_id']:r for r in f.get('run_results',{}).get('results',[])}
        ok=bool(runs)and f.get('trusted_fresh_artifact_binding')and all(len(v)==1 and rr.get(v[0][0],{}).get('status')=='success'for v in nodes.values())
        parts['build']=chk('pass'if ok else'fail','Trusted run and fresh successful model invocation artifacts')
        deps=any(i<first_run and r['command'][:2]==['dbt','deps']for i,r in good)
        package_nodes=any(n.get('package_name')=='dbt_utils'for n in manifest.get('macros',{}).values())
        parts['packages']=chk('pass'if deps and package_nodes else'fail','Actual successful dbt deps before run and installed dbt_utils macro definitions')
        seed=any(i<first_run and r['command'][:2]==['dbt','seed']for i,r in good)
        seeds=f.get('seed_run_results',{}).get('results',[]);seed_names={r['unique_id'].rsplit('.',1)[-1]for r in seeds if r.get('status')=='success'}
        exact=f.get('submitted_seeds_match_original_csv')
        parts['seed']=chk('pending'if exact is None else'pass'if seed and exact and {'googleads','metaads','tiktokads'}<=seed_names else'fail','Actual dbt seed of all three unchanged public CSV files before model run')
        tests=f.get('test_run_results',{}).get('results',[]);test_nodes=f.get('test_manifest',{}).get('nodes',{});ids=[]
        for uid,n in test_nodes.items():
            tm=n.get('test_metadata',{});kwargs=tm.get('kwargs',{})
            if n.get('resource_type')=='test'and tm.get('namespace')=='dbt_utils'and tm.get('name')=='unique_combination_of_columns'and set(kwargs.get('combination_of_columns',[]))=={'source','ad_date'}and any(dep.rsplit('.',1)[-1]=='int__ads_unified'for dep in n.get('depends_on',{}).get('nodes',[])):ids.append(uid)
        invoked=any(i>first_run and r['command'][:2]==['dbt','test']for i,r in good)
        passed={r['unique_id']for r in tests if r.get('status')=='pass'}
        parts['dbt_test']=chk('pass'if invoked and ids and set(ids)<=passed else'fail','Actual dbt test command and passing required dbt_utils uniqueness test; data equality does not certify execution')
    if f.get('trusted_source_inventory_complete'):
        if not f.get('trusted_fresh_artifact_binding'):
            parts['paths']=chk('pass'if set(MODELS.values())<=set(files)else'fail','Complete actual source inventory proves presence or absence of required model paths; manifest linkage assessed separately')
        try:
            staging=yaml.safe_load(files['models/staging/staging.yml']);inter=yaml.safe_load(files['models/int/int.yml']);sm={n['name']for n in staging.get('models',[])};im=[n for n in inter.get('models',[])if n.get('name')=='int__ads_unified'];ok=set(TABLES.values())-{TABLES['unified']}<=sm and len(im)==1
            parts['yaml']=chk('pass'if ok else'fail','Required staging/int YAML model declarations')
            configs=im[0].get('tests',im[0].get('data_tests',[]))if len(im)==1 else[];ok=False
            for test in configs:
                if not isinstance(test,dict):continue
                v=test.get('dbt_utils.unique_combination_of_columns',{});v=v.get('arguments',v)
                ok |=set(v.get('combination_of_columns',[]))=={'ad_date','source'}
            parts['unique_test']=chk('pass'if ok else'fail','Required uniqueness test references the ad_date/source pair, independent of list order')
        except(KeyError,ValueError,TypeError,yaml.YAMLError):parts['yaml']=chk('fail','Required model YAML missing or invalid')
    if f.get('trusted_command_history_complete'):
        sequence=f.get('trusted_command_sequence',[]);run_indices=[i for i,r in enumerate(sequence)if r.get('command',[])[:2]==['dbt','run']and r.get('returncode')==0]
        if run_indices:
            first=min(run_indices)
            for part,command in [('packages','deps'),('seed','seed'),('dbt_test','test')]:
                executed=any(r.get('command',[])[:2]==['dbt',command]and r.get('returncode')==0 and(i<first if command!='test'else i>first)for i,r in enumerate(sequence))
                if not executed:parts[part]=chk('fail','Complete trusted candidate command history proves missing successful dbt '+command+' in the required sequence; a later grader command cannot cure it')
    b=f.get('backend_before_install')
    if b:
        shell=b['shell'];finished=datetime.fromisoformat(shell['finished_at_utc'].replace('Z','+00:00'));boundary=datetime.fromisoformat(b['install_after_utc'].replace('Z','+00:00'));ok=shell.get('exit_code')==0 and shell.get('command')=='echo "$DB_TYPE"'and shell.get('output')in ['duckdb','duckdb\n','duckdb\\n']and finished<boundary and b.get('sequential_trusted_driver_verified')is True
        parts['backend']=chk('pass'if ok else'fail','Trusted live echo before submitted code installation')
    for name in MODELS:parts[name+'.sql_workflow']=sql_workflow(f.get('parsed_compiled_sql',{}).get(name),name==TABLES['unified'])
    states=[p['status']for p in parts.values()]
    return chk('fail'if'fail'in states else'pending'if'pending'in states else'gap'if'gap'in states else'pass','Consolidate trusted execution and declared SQL workflow',details=parts)
