"""Narrow actual-submission absence failures; never certifies an audit success.

Only explicit frozen delivery relations/columns are checked. A complete capture
and successful read-only catalog inventory are required. Missing source paths,
SQL/process errors and incomplete probes are not inferred to be failures here.
"""
import argparse
from copy import deepcopy
import importlib
import os
from pathlib import Path
import shutil
import subprocess
import sys

ROOT=Path(__file__).resolve().parents[1]
sys.path[:0]=[str(ROOT/'src'),str(ROOT/'scripts')]
from dbt_bench import io
from difficulty_deb_original import verify_original

# These are minimum explicitly required interfaces from the qualified checkers,
# not a generic inventory of every model in the independent author's solution.
CUSTOMER_REQUIREMENTS={
 'dbt-customer-cltv-forecasting':{'analytics.rpt_customer_cltv_forecast':'customer_id as_of_date current_lifetime_revenue current_order_count avg_order_value days_since_last_order forecast_3m_revenue forecast_6m_revenue forecast_12m_revenue'.split()},
 'dbt-customer-ltv-fix':{'main.customer_ltv':'customer_id total_orders lifetime_value avg_order_value first_order_date last_order_date value_segment'.split()},
 'dbt-customer-order-analytics':{'customer_analytics.dim_customer_tiers':['customer_id']},
 'dbt-fix-email-attribution':{'analytics.rpt_email_attribution_fixed':['attribution_date']},
 'dbt-fix-multi-touch-attribution':{'analytics.rpt_multi_touch_attribution_fixed':['attribution_date']},
 'dbt-customer-lifetime-value':{f'main.{t}':[] for t in ('clv_segments','clv_segment_summary','clv_cohort_analysis','clv_monthly_trends')},
 'dbt-loyalty-points-analysis':{'loyalty_analytics.program_loyalty_summary':'program_id program_name avg_points_per_member redemption_rate'.split()},
 'dbt-rfm-customer-segmentation':{'rfm_analytics.rfm_segments':['customer_id']},
}


def requirements(task,root=ROOT):
    if task in CUSTOMER_REQUIREMENTS:return deepcopy(CUSTOMER_REQUIREMENTS[task]),[io.evidence_ref(Path(__file__),root)]
    for name in ('difficulty_deb_phase4_operations','difficulty_deb_phase4_pipeline'):
        try:module=importlib.import_module(name)
        except ModuleNotFoundError as e:
            if e.name==name:continue
            raise
        if task in getattr(module,'FAILURE_REQUIREMENTS',{}):
            return deepcopy(module.FAILURE_REQUIREMENTS[task]),[io.evidence_ref(Path(module.__file__),root)]
    raise ValueError('No explicit qualified absence policy for this task')


def evaluate(capture, inventory, rules):
    """Pure decision with complete factual inputs; unknown is not zero."""
    unknown={'reward':None,'status':'unknown','violations':[],'model_requests':0}
    if (not capture.get('complete') or capture.get('errors') or capture.get('inventory_complete') is not True):
        return unknown|{'reason':'Original capture inventory is incomplete; no absence inferred.'}
    db=next((x for x in capture['databases'] if x['path']=='/app/database/retail.duckdb'),None)
    if db is None:return unknown|{'reason':'Required database was not included in the actual capture.'}
    if db['exists'] is False:
        absence=next((x for x in capture.get('absence_observations',[]) if x.get('role')=='database' and x.get('path')==db['path']),None)
        if not absence or absence.get('stable_absence') is not True:return unknown|{'reason':'No independently repeated ENOENT database absence observation.'}
        return {'reward':0,'status':'scored','model_requests':0,'violations':[{'rule_id':'required_database_exists','database':db['path'],'observed':'stable_absence'}]}
    if (not inventory or inventory.get('inventory_complete') is not True or inventory.get('database_and_wal_unchanged') is not True
            or inventory.get('database')!=db['path'] or inventory.get('hashes_before',{}).get(db['path'])!=db['sha256']):
        return unknown|{'reason':'No intact read-only physical catalog inventory bound to the actual captured database.'}
    got=inventory.get('inventory',{});violations=[]
    for relation,columns in rules.items():
        item=got.get(relation)
        if not item or type(item.get('exists')) is not bool:return unknown|{'reason':'Required relation was not conclusively inventoried: '+relation}
        if item['exists'] is False:
            violations.append({'rule_id':'required_relation_exists:'+relation,'relation':relation,'observed':'absent_in_information_schema'})
        else:
            actual=item.get('columns')
            if not isinstance(actual,list) or len({x.lower() for x in actual})!=len(actual):return unknown|{'reason':'Column inventory incomplete or ambiguous'}
            for column in columns:
                if column.lower() not in {x.lower() for x in actual}:
                    violations.append({'rule_id':'required_column_exists:'+relation+'.'+column,'relation':relation,'column':column,'observed':'absent_in_information_schema'})
    if violations:return {'reward':0,'status':'scored','violations':violations,'model_requests':0}
    return unknown|{'reason':'No narrow interface failure established. Full frozen semantic/workflow audit is still required; this path cannot award1.'}


def save(path,data,root=ROOT):
    if Path(path).exists():raise ValueError('Evidence already exists')
    io.write_json(path,data);return io.evidence_ref(path,root)


def observe(context,output,rules,root=ROOT):
    output=Path(output);output.mkdir();dbpath='/app/database/retail.duckdb';capture=context['capture'];mounts=[];copies=[]
    database=next(x for x in capture['databases'] if x['path']==dbpath)
    if database['exists'] is False:return None,[]
    for item in capture['databases']:
        if item['path'] not in (dbpath,dbpath+'.wal') or not item['exists']:continue
        src=context['trial']/'artifacts/difficulty-submission'/item['file'];dst=output/Path(item['file']).name
        shutil.copyfile(src,dst)
        if io.file_hash(dst)!=item['sha256']:raise ValueError('Actual captured DB copy changed')
        mounts+=['--bind',str(dst)+':'+item['path']+':ro'];copies.append(io.evidence_ref(dst,root))
    request=save(output/'request.json',{'database':dbpath,'requirements':rules,'model_requests':0},root)
    argv=['/opt/apps/tacc-apptainer/1.4.1/bin/apptainer','exec','--cleanenv','--containall','--no-mount','home,tmp,cwd,hostfs,bind-paths','--pwd','/',
        '--bind',str(root)+':/repo:ro','--bind',str(output)+':/audit:rw',*mounts,str(root/context['image']['path']),'/usr/local/bin/python3',
        '/repo/scripts/difficulty_deb_schema_probe.py','--request','/audit/request.json','--output','/audit/inventory.json']
    proc=subprocess.run(argv,capture_output=True,text=True,timeout=120,env=os.environ|{'PYTHONDONTWRITEBYTECODE':'1'})
    (output/'probe.log').write_text(proc.stdout+proc.stderr)
    process=save(output/'process.json',{'argv':argv,'returncode':proc.returncode,'request':request,'image':context['image'],
        'log':io.evidence_ref(output/'probe.log',root),'copies':copies,'script':io.evidence_ref(root/'scripts/difficulty_deb_schema_probe.py',root),'model_requests':0},root)
    if proc.returncode:return None,[process]
    inventory=io.read_json(output/'inventory.json')
    return inventory,[process,io.evidence_ref(output/'inventory.json',root)]


def score(run,trial,output,*,fixture=False,validation=None,root=ROOT):
    root=Path(root);output=Path(output)
    if output.exists() or not output.resolve().is_relative_to('/scratch'):raise ValueError('Select new SCRATCH evidence directory')
    context=verify_original(run,trial,fixture=fixture,root=root)
    key=context['task_key'];rules,policy_refs=requirements(key.removeprefix('deb__'),root)
    output.mkdir(parents=True)
    request=save(output/'checker-request.json',{'task_key':key,'task_version':context['task_version'],'capture':context['capture_ref'],
        'requirements':rules,'policy_refs':policy_refs,'original_result':context['original_result'],'qualified_review':context['qualified_review'],
        'fixture_only':fixture,'model_requests':0},root)
    inventory,observations=observe(context,output/'probe',rules,root)
    result=evaluate(context['capture'],inventory,rules)
    result.update(task_key=key,task_version=context['task_version'],actual_submission_capture=context['capture_ref'],
        scope='Narrow frozen-contract interface absence on the original pre-native DB. Source path absence, helper-model names, and build errors are not classified by this evaluator.')
    result_ref=save(output/'audit-result.json',result,root)
    if result['reward']!=0:
        return {'task_key':key,'audit_reward':None,'audit_status':'unknown','diagnostic':result_ref,'fixture_only':fixture,'model_requests':0}
    checker=io.evidence_ref(Path(__file__),root);qualified=context['qualified'];qchecker=io.evidence_ref(qualified['checker'],root)
    if validation is None:
        return {'task_key':key,'audit_reward':0,'audit_status':'unqualified_control_only','diagnostic':result_ref,'fixture_only':fixture,'model_requests':0}
    validation=io.evidence_ref(Path(validation),root)
    control=io.read_json(root/validation['path'])
    if control.get('evaluator')!=checker:raise ValueError('Failure qualification belongs to another evaluator version')
    source=save(output/'source-binding.json',{'task_key':key,'task_version':context['task_version'],'original_result':context['original_result'],
        'capture':context['capture_ref'],'qualified_review':context['qualified_review'],'prepared_run':io.evidence_ref(Path(run)/'manifest.json',root),
        'reconstruction_performed':False,'model_requests':0,'fixture_only':fixture},root)
    proof=save(output/'failure-evaluation.json',{'schema_version':1,'evaluation_kind':'direct_contract_failure','task_key':key,
        'task_version':context['task_version'],'original_result':context['original_result'],'qualified_review':context['qualified_review'],
        'capture':context['capture_ref'],'checker':checker,'checker_result':result_ref,'reward':0,'status':'scored',
        'rule_ids':[v['rule_id'] for v in result['violations']],'rule_evidence':[qualified['record']['audit']['overlay'],qchecker,*policy_refs],
        'observations':[context['capture_ref'],*observations],'complete_capture':True,'model_requests':0},root)
    names=['difficulty_deb_failure.py','difficulty_deb_original.py','difficulty_deb_schema_probe.py','difficulty_deb_registry_v2.py','difficulty_submission.py','difficulty_submission_v2.py']
    deps=save(output/'adapter-dependencies.json',{'code_refs':[io.evidence_ref(root/'scripts'/n,root) for n in names]+policy_refs,
        'checker_dependencies':qualified['overlay']['code_refs'],'image':context['image']},root)
    receipt={'schema_version':1,'evidence_kind':'direct_contract_failure','adapter_family':'deb','task_key':key,'task_version':context['task_version'],
        'original_result':context['original_result'],'original_native_rewards':(context['original'].get('verifier_result')or{}).get('rewards'),
        'original_capture':context['capture_ref'],'source_binding':source,'rebuild_result':None,'qualified_review':context['qualified_review'],
        'checker_version':'direct-required-interface-v1','checker':checker,'qualified_checker':qchecker,'checker_request':request,
        'audit_result':result_ref,'audit_reward':0,'audit_status':'scored','fixture_only':fixture,'model_requests':0,'training_prohibited':True,
        'adapter':checker,'adapter_dependencies':deps,'failure_evaluation_ref':proof,'failure_evaluator_validation_ref':validation}
    from difficulty_failure import validate_failure
    from difficulty_failure_qualification import validate as validate_qualification
    validate_failure(receipt,result,root);validate_qualification(receipt,qualified['record'],qualified['overlay'],root)
    save(output/'receipt.json',receipt,root);return receipt


if __name__=='__main__':
    p=argparse.ArgumentParser();p.add_argument('--run',type=Path,required=True);p.add_argument('--trial',type=Path,required=True)
    p.add_argument('--output',type=Path,required=True);p.add_argument('--fixture',action='store_true');p.add_argument('--validation',type=Path)
    a=p.parse_args();r=score(a.run,a.trial,a.output,fixture=a.fixture,validation=a.validation)
    print({k:r[k]for k in ('task_key','audit_reward','audit_status','fixture_only')})
