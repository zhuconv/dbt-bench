"""Channel source/profile/actual command policy, separate from output equality."""
from datetime import datetime
import hashlib
import yaml,ast,re
from jinja2 import Environment,nodes,TemplateSyntaxError
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
PROJECT='/app/dbt_models_duckdb'
MODELS={'stg_orders__channels':'models/staging/stg_orders__channels.sql','int_customer_channel_first_touch':'models/intermediate/int_customer_channel_first_touch.sql','channel_performance':'models/marts/channel_performance.sql'}
def chk(status,reason,**more):return dict(status=status,reason=reason,**more)
def source_calls(text):
    try:ast=Environment().parse(text)
    except TemplateSyntaxError:return chk('pending','Jinja syntax is outside the standalone parser; actual dbt lineage needs review')
    calls=list(ast.find_all(nodes.Call));literal=[];unrecognized=False
    for call in calls:
        if not isinstance(call.node,nodes.Name):unrecognized=True;continue
        if call.node.name not in {'config','ref','source'}:unrecognized=True
        if call.node.name=='source'and len(call.args)==2 and all(isinstance(a,nodes.Const)and isinstance(a.value,str)for a in call.args):literal.append(tuple(a.value for a in call.args))
    required={('enterprise_db','ORDERS'),('enterprise_db','CHANNELS')}
    if required<={(s,t.upper())for s,t in literal}:return chk('pass','Actual Jinja calls explicitly use enterprise_db ORDERS and CHANNELS; fresh manifest linkage is separately verified')
    names={n.name for n in ast.find_all(nodes.Name)}
    if not literal and not unrecognized and 'source'not in names:return chk('fail','Actual staging source uses no source() or alternate macro calls; explicit source-macro workflow is absent')
    return chk('pending','Source call indirection/partial lineage requires additional proof; no raw SQL source hash whitelist')
def evaluate(f):
    f=f or {};files=f.get('files',{});parts={n:chk('pending','Trusted actual capture unavailable')for n in ['model_files','profile','source_macros','build','dependencies','backend','idempotency']}
    if f.get('trusted_source_inventory_complete'):
        parts['model_files']=chk('pass'if set(MODELS.values())<=set(files)else'fail','Actual complete source inventory at all three explicit paths')
        try:
            config=yaml.safe_load(files['dbt_project.yml']);profiles=yaml.safe_load(files['profiles.yml']);p=profiles['retail_dw_master'];selected=resolve(p['target'],f.get('environment',{}));target=resolve(p['outputs'][selected],f.get('environment',{}));ok=config.get('profile')=='retail_dw_master'and target.get('type')=='duckdb'and target.get('path')=='/app/database/retail.duckdb'
            if f.get('environment'):ok &=f['environment'].get('DBT_PROFILES_DIR')==PROJECT
            parts['profile']=chk('pass'if ok and f.get('environment')else'fail'if not ok else'pending','Required actual local profile, selected database and environment')
        except(KeyError,ValueError,TypeError,yaml.YAMLError):parts['profile']=chk('pending'if any('{{'in files.get(n,'')for n in ['dbt_project.yml','profiles.yml'])else'fail','Required profile missing, invalid or unresolved')
        if MODELS['stg_orders__channels']in files:
            text=files[MODELS['stg_orders__channels']];probe=f.get('source_macro_probe')
            if probe and probe.get('source_sha256')==hashlib.sha256(text.encode()).hexdigest():parts['source_macros']=probe
            else:
                partial=source_calls(text)
                parts['source_macros']=partial if partial['status']=='fail'else chk('pending','Actual active source-marker trace not captured; literal call text alone does not certify active SQL lineage')
    if f.get('trusted_process_complete'):
        manifest=f.get('manifest',{});rr={r['unique_id']:r for r in f.get('run_results',{}).get('results',[])};ok=bool(f.get('trusted_fresh_artifact_binding'))
        for name,path in MODELS.items():
            matches=[(u,n)for u,n in manifest.get('nodes',{}).items()if n.get('resource_type')=='model'and n.get('name')==name];ok &=len(matches)==1
            if len(matches)!=1:continue
            uid,n=matches[0];ok &=n.get('original_file_path')==path and n.get('schema')=='main_channel_analytics'and rr.get(uid,{}).get('status')=='success'
            if name=='channel_performance':ok &=n.get('config',{}).get('materialized')=='table'
            if name=='stg_orders__channels':
                deps=n.get('depends_on',{}).get('nodes',[]);sources=[manifest.get('sources',{}).get(x,{})for x in deps];bound={x.get('name','').upper()for x in sources if x.get('source_name')=='enterprise_db'}
                if parts['source_macros']['status']=='pass':parts['source_macros']=chk('pass'if {'ORDERS','CHANNELS'}<=bound else'pending','Literal source calls bound to the actual fresh manifest source dependencies',bound=sorted(bound))
        commands=f.get('trusted_command_sequence',[]);runs=[i for i,r in enumerate(commands)if r.get('command',[])[:2]==['dbt','run']and r.get('returncode')==0 and r.get('cwd')==PROJECT];ok &=bool(runs)
        parts['build']=chk('pass'if ok else'fail','Trusted run and fresh source/manifest/result invocation binding for all three models')
        deps=bool(runs)and any(i<min(runs)and r.get('command',[])[:2]==['dbt','deps']and r.get('returncode')==0 and r.get('cwd')==PROJECT for i,r in enumerate(commands))
        parts['dependencies']=chk('pass'if deps else'fail','Actual successful dbt deps precedes first candidate dbt run; installed packages alone do not satisfy it')
    if f.get('trusted_command_history_complete'):
        seq=f.get('trusted_command_sequence',[]);runs=[i for i,r in enumerate(seq)if r.get('command',[])[:2]==['dbt','run']and r.get('returncode')==0]
        if runs and not any(i<min(runs)and r.get('command',[])[:2]==['dbt','deps']and r.get('returncode')==0 for i,r in enumerate(seq)):parts['dependencies']=chk('fail','Complete recorded candidate history omits dbt deps before first successful build')
    b=f.get('backend_before_install')
    if b:
        s=b['shell'];end=datetime.fromisoformat(s['finished_at_utc'].replace('Z','+00:00'));boundary=datetime.fromisoformat(b['install_after_utc'].replace('Z','+00:00'));ok=s.get('exit_code')==0 and s.get('command')=='echo "$DB_TYPE"'and s.get('output')in ['duckdb','duckdb\n','duckdb\\n']and end<boundary and b.get('sequential_trusted_driver_verified')is True
        parts['backend']=chk('pass'if ok else'fail','Actual literal candidate echo before source installation; prior harness environment probes are a different event')
    if f.get('trusted_repeat_outputs'):
        r=f['trusted_repeat_outputs'];parts['idempotency']=chk('pass'if r['equal']else'fail','Actual consecutive trusted builds and output captures compared; raw input unchanged',scope=r['scope'],evidence=r.get('evidence',[]))
    states=[p['status']for p in parts.values()]
    return chk('fail'if'fail'in states else'pending'if'pending'in states else'pass','Channel actual workflow review',details=parts)
