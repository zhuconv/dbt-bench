"""Trusted build/profile/lineage facts; output equality never certifies workflow."""
from datetime import datetime
from pathlib import PurePosixPath
import yaml
MODELS={'dq':{'fct_order_line_detail':'main'},'reconciliation':{'stg_orders':'staging','stg_order_lines':'staging','stg_order_cancellations':'staging','int_orders_enriched':'intermediate','mart_order_totals':'mart','mart_revenue_by_source':'mart','mart_variance_details':'mart'}}
PROJECTS={'dq':'/app/dbt_models_duckdb','reconciliation':'/app/dbt_project'}
def chk(s,r,**x):return dict(status=s,reason=r,**x)
def time(x):return datetime.fromisoformat(x.replace('Z','+00:00'))
def evaluate(kind,f):
    f=f or {};parts={n:chk('pending','Trusted actual capture unavailable')for n in ['model_delivery','profile','trusted_build','backend']};files=f.get('files',{});manifest=f.get('manifest',{});proc=f.get('process',{});project=PROJECTS[kind]
    nodes={n:[(uid,v)for uid,v in manifest.get('nodes',{}).items()if v.get('resource_type')=='model'and v.get('name')==n]for n in MODELS[kind]}
    if f.get('trusted_fresh_artifact_binding'):
        valid=True
        for name,pairs in nodes.items():
            valid &=len(pairs)==1
            if len(pairs)!=1:continue
            uid,n=pairs[0];valid &=n.get('original_file_path')in files and n.get('schema','').lower()==MODELS[kind][name]
            if kind=='dq':valid &=n.get('original_file_path')=='models/marts/sales/fct_order_line_detail.sql'and n.get('config',{}).get('materialized')=='view'
        parts['model_delivery']=chk('pass'if valid else'fail','Task-named models and schemas, explicit DQ path/original materialization; no source hash whitelist')
    absent=f.get('trusted_controlled_model_absence')
    if absent and absent.get('single_mutation_verified')is True and absent.get('model_name')in MODELS[kind] and absent.get('source_exists_after')is False:
        parts['model_delivery']=chk('fail','Actual controlled candidate removed a named model after its verified seven-model construction; no substitute was introduced',model_name=absent['model_name'],scope='Bound retained controlled construction and actual file-absence proof, not inferred from output rows')
    inventory=f.get('trusted_model_file_inventory')
    if inventory is not None:
        names={PurePosixPath(p).stem for p in inventory if PurePosixPath(p).suffix in ('.sql','.py')};missing=sorted(set(MODELS[kind])-names)
        if missing:parts['model_delivery']=chk('fail','Complete trusted file inventory lacks named dbt model',missing=missing)
    if f.get('trusted_process_complete'):
        try:
            cfg=yaml.safe_load(files['dbt_project.yml']);profiles=yaml.safe_load(files['profiles.yml']);pname=cfg['profile'];pr=profiles[pname];target=pr['outputs'][pr['target']];ok=target['type']=='duckdb'and target['path']=='/app/database/retail.duckdb'and proc['environment']['DBT_PROFILES_DIR']==project
            if kind=='dq':ok &=pname=='retail_dw_master'
            parts['profile']=chk('pass'if ok else'fail','Actual local matching profile, declared database path and profiles environment')
        except(KeyError,TypeError,ValueError,yaml.YAMLError):parts['profile']=chk('pending'if any('{{'in files.get(p,'')for p in ('profiles.yml','dbt_project.yml'))else'fail','Required captured profile absent, invalid or unresolved')
        rr={r['unique_id']:r for r in f.get('run_results',{}).get('results',[])};valid=proc.get('returncode')==0 and proc.get('cwd')==project and proc.get('command',[])[:2]==['dbt','run']and f.get('trusted_fresh_artifact_binding')is True
        for pairs in nodes.values():valid &=len(pairs)==1 and rr.get(pairs[0][0],{}).get('status')=='success'
        parts['trusted_build']=chk('pass'if valid else'fail','Trusted successful dbt process and matching fresh invocation artifacts, every named model succeeded')
    b=f.get('backend_before_install')
    if b:
        s=b['shell'];ok=s.get('exit_code')==0 and s.get('command')=='echo "$DB_TYPE"'and s.get('output')in ('duckdb','duckdb\n','duckdb\\n')and time(s['finished_at_utc'])<time(b['install_after_utc'])and b.get('sequential_trusted_driver_verified')is True
        parts['backend']=chk('pass'if ok else'fail','Actual backend shell echo finished before code installation')
    if f.get('trusted_candidate_command_history'):
        h=f['trusted_candidate_command_history']
        if h.get('artifact_hashes_verified'):
            first=min(time(x['at'])for x in h['actions']if x['kind']=='candidate_model_written');s=h['literal_echo'];ok=s['command']==['bash','-c','echo "$DB_TYPE"']and s['exit_code']==0 and s['stdout']=='duckdb\n'and time(s['finished_at_utc'])<first
            parts['backend']=chk('pass'if ok else'fail','Actual controlled write/echo chronology; earlier harness probe acknowledged',prior_harness_probe_acknowledged=h['prior_harness_probe_acknowledged'])
    if kind=='dq':
        parts['deps_before_run']=chk('pending','Actual complete candidate command history missing')
        runs=f.get('trusted_command_sequence')
        if runs is not None:
            builds=[i for i,r in enumerate(runs)if r.get('command',[])[:2]==['dbt','run']];deps=[i for i,r in enumerate(runs)if r.get('command',[])[:2]==['dbt','deps']and r.get('returncode')==0]
            ok=bool(builds)and bool(deps)and min(deps)<min(builds)
            parts['deps_before_run']=chk('pass'if ok else'fail','Complete trusted candidate execution sequence explicitly includes successful deps before run')
        before=f.get('upstream_before');after=f.get('upstream_after');parts['upstream_unchanged']=chk('pending','Initial and final upstream source inventory unavailable')
        if before is not None and after is not None:
            parts['upstream_unchanged']=chk('pass'if before==after else'fail','Actual upstream source bytes before/after; task explicitly prohibits upstream staging modification')
        parts['macro_behavior']=f.get('trusted_macro_probes')or chk('pending','Rendered candidate macro dispatch/empty-field behavior has not been independently tested')
    states=[v['status']for v in parts.values()]
    return chk('fail'if'fail'in states else'pending'if'pending'in states else'gap'if'gap'in states else'pass','Independent workflow requirements over trusted execution/source captures',details=parts)
