"""Frozen eight-customer-task audits on actual pre-native model submissions."""
from pathlib import Path
from dataclasses import replace
from copy import deepcopy
import importlib.util
import hashlib
import os
import subprocess
import sys

from dbt_bench import io
from audit_tables import load_observer_table
from difficulty_deb_binding_v3 import verify
from difficulty_deb_registry_v2 import load as checked_json
from difficulty_deb_workflow_v3 import declared_action_gate

ROOT=Path(__file__).resolve().parents[1]
SHARED=ROOT/'validation/deb/dbt-customer-cohort-retention/phase4_v1'
sys.path.insert(0,str(SHARED))
from review_support_v4 import raw_rows,ck
from seal_candidate_v2 import check_workflow

TASKS={'dbt-customer-cltv-forecasting','dbt-customer-ltv-fix','dbt-customer-order-analytics',
       'dbt-fix-email-attribution','dbt-fix-multi-touch-attribution',
       'dbt-customer-lifetime-value','dbt-loyalty-points-analysis','dbt-rfm-customer-segmentation'}

def module(path,name):
    spec=importlib.util.spec_from_file_location(name,path);m=importlib.util.module_from_spec(spec)
    sys.modules[name]=m;spec.loader.exec_module(m);return m

def actual_outputs(ref,key,root):
    snapshot=checked_json(ref,root)
    if snapshot['task_key']!=key or snapshot['stage']!='agent-submission':raise ValueError('Wrong submission snapshot')
    result={}
    for item in snapshot['outputs']:
        spec=item['specification'];name=spec['relation'].split('.',1)[1]
        if item.get('error'):
            error=item['error'];missing=error.get('type')=='CatalogException' and 'does not exist' in error.get('message','')
            result[name]={'exists':False} if missing else {'capture_error':error}
            continue
        t=load_observer_table(ref,task_key=key,database=spec['database'],relation=spec['relation'],stage='agent-submission',root=root)
        if len({c.lower() for c in t.columns})!=len(t.columns):raise ValueError('Ambiguous actual output columns')
        t=replace(t,columns=tuple(c.lower() for c in t.columns),physical_columns=tuple(c.lower() for c in t.physical_columns),
            sql_types={k.lower():v for k,v in t.sql_types.items()},physical_sql_types={k.lower():v for k,v in t.physical_sql_types.items()},
            rows=[{k.lower():v for k,v in r.items()} for r in t.rows])
        result[name]={'rows':t.rows,'table':t,'relation_type':t.relation_type}
    return result

def parser_facts(context,probe):
    """Current actual source, never a prior author's manifest/SQL hash."""
    d=context['fresh'];task=context['binding']['task_key'].removeprefix('deb__')
    command=next(c for c in reversed(d['commands']) if c['manifest'] is not None and c['run']['command'][1] in ('run','build'))
    digest=io.file_hash(command['manifest_path']);models={}
    for row in probe['parsed']:
        if row['manifest']['sha256']!=digest:continue
        node=command['manifest']['nodes'][row['unique_id']]
        if hashlib.sha256(node['compiled_code'].encode()).hexdigest()!=row['compiled_sha256']:raise ValueError('Actual parsed SQL changed')
        models[node['name']]={'compiled_sha256':row['compiled_sha256'],'ast':row['ast']}
    return {'tasks':{task:{'models':models}}},command

def attribution(context,command,output,root):
    """Bounded actual-source render and frozen tiny fixtures inside locked SIF."""
    task=context['binding']['task_key'].removeprefix('deb__');name='rpt_email_attribution_fixed' if task=='dbt-fix-email-attribution' else 'rpt_multi_touch_attribution_fixed'
    nodes=[n for n in command['manifest']['nodes'].values() if n.get('resource_type')=='model' and n['name']==name]
    if len(nodes)!=1:raise ValueError('Required attribution source model is not unique')
    d=context['fresh'];metadata={}
    for role,relation in [('sessions','stg_ga__sessions'),('events','stg_ga__events'),('joined','int_sessions_events_joined')]:
        item=d['initial']['raw'].get(role)
        if item is not None:metadata[relation]=item['columns']
    payload={'tasks':{task:{'models':{name:nodes[0]},'metadata':metadata,'environment':d['execution']['environment'],
        'trial':str(context['trial']),'source_refs':[context['manifest']['source_binding']]}}}
    output.mkdir()
    inp=output/'request.json';io.write_json(inp,payload)
    image=context['production'][context['binding']['task_key']]['image']
    if not io.evidence_ok(image,root):raise ValueError('Actual image changed')
    base=['/opt/apps/tacc-apptainer/1.4.1/bin/apptainer','exec','--cleanenv','--containall','--no-mount','home,tmp,cwd,hostfs,bind-paths','--pwd','/',
        '--bind',str(root)+':/repo:ro','--bind',str(output)+':/audit:rw',str(root/image['path']),'/usr/local/bin/python3']
    refs=[]
    for script,filename in [('validation/deb/dbt-customer-cohort-retention/phase4_v1/parse_sources_v1.py','parsed.json'),
                            ('scripts/difficulty_deb_attribution_probe.py','fixtures.json')]:
        argv=base+['/repo/'+script,'--input','/audit/request.json','--output','/audit/'+filename]
        p=subprocess.run(argv,capture_output=True,text=True,timeout=120,env=os.environ|{'PYTHONDONTWRITEBYTECODE':'1'})
        (output/(filename+'.log')).write_text(p.stdout+p.stderr)
        io.write_json(output/(filename+'.process.json'),{'argv':argv,'returncode':p.returncode,'model_requests':0,
            'input':io.evidence_ref(inp,root),'script':io.evidence_ref(root/script,root),'image':image})
        if p.returncode:raise ValueError('Actual attribution source fixture process failed')
        refs.append(io.evidence_ref(output/filename,root))
    return checked_json(refs[0],root),checked_json(refs[1],root),refs

def action_gate(task,events,project):
    if task in ('dbt-customer-lifetime-value','dbt-customer-ltv-fix'):
        # These two instructions ask to inspect DB_TYPE, but do not prescribe
        # echo-before-coding or exporting DBT_PROFILES_DIR. Do not invent them.
        ok=bool(events.get('echo_calls'))
        return {'status':'pass' if ok else 'unknown','reward':1 if ok else None,
            'reason':'Actual successful paired backend observation is required; these instructions do not impose echo chronology or a DBT_PROFILES_DIR export.',
            'source_logs':events.get('source_logs',[])}
    return declared_action_gate('deb__'+task,events,project)

def unsupported_profile(verified):
    """Unparsed legal dbt expressions are a limitation, not a wrong profile."""
    import yaml
    from review_support_v4 import env_value
    files=verified['final_files'];env=verified['execution']['environment']
    try:
        cfg=yaml.safe_load(files['dbt_project.yml'].read_text());profiles=yaml.safe_load(files['profiles.yml'].read_text())
        name=cfg['profile']
        if isinstance(name,str) and '{{' in name:return True
        profile=profiles[name];target=env_value(profile['target'],env)
        if target is None:return True
        active=profile['outputs'][target]
        for value in [active['path'],active.get('schema','main')]:
            if env_value(value,env) is None:return True
    except ValueError:return True
    except (KeyError,TypeError,yaml.YAMLError):return False
    return False


def run(request,root=ROOT):
    root=Path(root);context=verify(request['audit_dir'],root);key=request['task_key'];task=key.removeprefix('deb__')
    if task not in TASKS or context['binding']['task_key']!=key:raise ValueError('Wrong customer adapter/task')
    output=Path(request['output_dir']);d=context['fresh'];raw=raw_rows(d)
    observed=actual_outputs(request['snapshot'],key,root)
    probe=checked_json(request['probe'],root);events=checked_json(request['original_events'],root)
    parser,command=parser_facts(context,probe);fixtures={'tasks':{}};extra=[]
    if task in ('dbt-fix-email-attribution','dbt-fix-multi-touch-attribution'):
        parser,fixtures,extra=attribution(context,command,output/'attribution-current-source',root)
    if task in ('dbt-loyalty-points-analysis','dbt-rfm-customer-segmentation','dbt-customer-lifetime-value'):
        revision=root/'validation/deb/dbt-loyalty-points-analysis/phase4_v2'
        sys.path.insert(0,str(revision))
        name='run_revision_v3.py' if task=='dbt-customer-lifetime-value' else 'run_revision_v2.py'
        driver=module(revision/name,'difficulty_current_revision')
        e,a=driver.load(task);expected=driver.expected_for(task,e,raw)
        wf=driver.revision_workflow(task,d,parser,fixtures)
    else:
        a=module(context['qualified']['checker'],'difficulty_actual_frozen_checker')
        if task in ('dbt-customer-ltv-fix','dbt-customer-cltv-forecasting'):expected=a.derive(raw)
        elif task in ('dbt-customer-order-analytics','dbt-fix-multi-touch-attribution'):expected=a.derive_for(task,raw)
        else:
            e=module(root/'validation/deb'/task/'audit_phase2_v1/expected_v1.py','difficulty_email_expected')
            rows,facts=e.derive('email',raw,joined_available=bool(raw.get('joined')) and 't2_event_value' in raw['joined'][0]);expected={'rows':rows,'facts':facts}
        wf=check_workflow(task,d,parser,fixtures)
    if wf['checks'].get('profile',{}).get('status')=='fail' and unsupported_profile(d):
        wf['checks']['profile']=ck('gap','Actual source built successfully, but the limited static profile resolver cannot establish the legal expression. No incorrect-profile conclusion is inferred.',previous_frozen_component=wf['checks']['profile'])
    if task in ('dbt-customer-order-analytics','dbt-rfm-customer-segmentation'):
        from difficulty_deb_repeat import compare as compare_repeat
        if task=='dbt-customer-order-analytics':
            columns={'dim_customer_tiers':list(expected['rows'][0]),'int_customer_order_metrics':'customer_id order_count total_spend avg_order_value first_order_date last_order_date days_since_last_order'.split(),'stg_customers':['customer_id'],'stg_orders':['order_id']}
        else:
            columns={name:list(cols)for name,cols in e.COLS.items()}
            columns.update(stg_orders__orders=list(raw['orders'][0]),stg_customer__customers=list(raw['customers'][0]))
        wf['checks']['idempotence']=compare_repeat(d,columns)
    gate=action_gate(task,events,d['request']['project_root'])
    wf['checks']['backend']=ck('pass' if gate['reward']==1 else 'fail' if gate['reward']==0 else 'gap',
        'Prescribed original Qwen actions are evaluated from paired raw model tool events. Fresh reconstruction echo/profile environment does not substitute for them.',details=gate)
    states=[x['status'] for x in wf['checks'].values()]
    wf['reward']=0 if 'fail' in states else None if any(x in states for x in ('gap','pending')) else 1
    wf['status']='unknown' if wf['reward'] is None else 'scored'
    w=ck('pass' if wf['reward']==1 else 'fail' if wf['reward']==0 else 'gap','Actual delivered source reconstruction and separately verified original model actions',details=wf)
    if task=='dbt-customer-ltv-fix':result=a.evaluate(observed.get('customer_ltv',{}),expected,w)
    elif task=='dbt-customer-cltv-forecasting':result=a.evaluate(observed.get('rpt_customer_cltv_forecast',{}),expected,w)
    elif task in ('dbt-customer-order-analytics','dbt-fix-multi-touch-attribution'):result=a.evaluate_extra(task,observed,expected,w)
    elif task=='dbt-fix-email-attribution':
        item=observed.get('rpt_email_attribution_fixed',{})
        result=a.evaluate('email',item.get('rows'),expected['rows'],structured=item.get('table'),relation_exists=item.get('exists'),relation_type=item.get('relation_type'),workflow=w)
    else:result=a.evaluate(observed,expected,w)
    result.update(original_action_gate=gate,actual_workflow=wf,actual_snapshot=request['snapshot'],
        original_events=request['original_events'],source_binding=context['manifest']['source_binding'],
        auxiliary_source_fixtures=extra,
        adapter_code_refs=[io.evidence_ref(Path(__file__),root),io.evidence_ref(root/'scripts/difficulty_deb_repeat.py',root),io.evidence_ref(root/'scripts/difficulty_deb_attribution_probe.py',root),io.evidence_ref(SHARED/'seal_candidate_v2.py',root),io.evidence_ref(SHARED/'review_support_v4.py',root)],
        scope='Qualified frozen semantics applied to the original pre-native DB. Fresh source artifacts are bound to actual delivered bytes; original required actions come only from Qwen logs. Attribution fixtures use current actual SQL, not an author whitelist.')
    return result
