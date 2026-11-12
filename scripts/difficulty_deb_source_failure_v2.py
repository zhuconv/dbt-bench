"""Certify only explicit source obligations from a complete original capture.

Version 2 retains the narrow RFM local profiles.yml rule and emits the standard
``target=audit`` outcome expected by measurement reconciliation. No pre-native
DB or output-table absence is classified as failure for source delivery.
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

def evaluate_source(task, capture, filenames):
    """One explicit local-profile obligation, verified against current source."""
    result={'reward':None,'status':'unknown','violations':[],'model_requests':0}
    if task!='dbt-rfm-customer-segmentation':return result
    if (capture.get('complete') is not True or capture.get('inventory_complete') is not True or capture.get('errors')
            or capture.get('project_root')!='/app/dbt_project'):
        return result|{'reason':'Incomplete or wrong-root source inventory.'}
    if 'profiles.yml' not in filenames:
        return {'reward':0,'status':'scored','model_requests':0,'violations':[{'rule_id':'R-profile-local-file','path':'/app/dbt_project/profiles.yml',
            'observed':'absent_in_complete_source_archive','requirement':'Current RFM instruction explicitly requires profiles.yml in the dbt project directory and profile name dbt_project.'}]}
    return result|{'reason':'Required source file is present; full profile/workflow checks remain separate.'}


def save(path,data,root=ROOT):
    if Path(path).exists():raise ValueError('Evidence already exists')
    io.write_json(path,data);return io.evidence_ref(path,root)


def score(run,trial,output,*,fixture=False,validation=None,root=ROOT):
    root=Path(root);output=Path(output)
    if output.exists() or not output.resolve().is_relative_to('/scratch'):raise ValueError('Select new SCRATCH evidence directory')
    context=verify_original(run,trial,fixture=fixture,root=root)
    key=context['task_key'];rules={};policy_refs=[io.evidence_ref(Path(__file__),root)]
    if key!='deb__dbt-rfm-customer-segmentation':raise ValueError('This narrow source evaluator only covers the explicit RFM profile-file requirement')
    output.mkdir(parents=True)
    request=save(output/'checker-request.json',{'task_key':key,'task_version':context['task_version'],'capture':context['capture_ref'],
        'requirements':rules,'policy_refs':policy_refs,'original_result':context['original_result'],'qualified_review':context['qualified_review'],
        'fixture_only':fixture,'model_requests':0},root)
    observations=[]
    result={'reward':None,'status':'unknown','violations':[],'model_requests':0,
        'reason':'This version only certifies the explicit RFM local profiles.yml source obligation. Pre-native database/table absence cannot fail source delivery.'}
    planned=Path(context['prepared']['identity']['config']['tasks'][0]['path'])
    delivery=save(output/'delivery-contract.json',{'schema_version':1,'task_key':key,'task_version':context['task_version'],
        'qualified_review':context['qualified_review'],'instruction':io.evidence_ref(planned/'instruction.md',root),
        'native_build_path':io.evidence_ref(planned/'tests/test_outputs.py',root),
        'mode':'submitted_source_reconstruction','scoring_basis':'submitted_source_reconstruction',
        'rule_kind':'submitted_source','rule_basis':'RFM explicitly requires profiles.yml in /app/dbt_project. Final output absence before the native build is not a failure rule.',
        'model_requests':0},root)

    if key=='deb__dbt-rfm-customer-segmentation':
        from difficulty_submission import extract_source
        extracted=extract_source(trial,key,output/'captured-source')
        source_result=evaluate_source(key.removeprefix('deb__'),context['capture'],{r['path'] for r in extracted['source_files']})
        source_evidence=save(output/'source-evaluation.json',{'result':source_result,'source_inventory':extracted,
            'instruction':io.evidence_ref(Path(context['prepared']['identity']['config']['tasks'][0]['path'])/'instruction.md',root),
            'model_requests':0,'fixture_only':fixture},root)
        observations.append(source_evidence)
        if source_result['reward']==0:result=source_result

    result.update(target='audit',task_key=key,task_version=context['task_version'],actual_submission_capture=context['capture_ref'],
        scope='Only explicit RFM profiles.yml omission from a complete verified source archive. No pre-native DB/output absence rule is applied.')
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
        'rule_kind':'submitted_source','delivery_contract_ref':delivery,'rule_ids':[v['rule_id'] for v in result['violations']],'rule_evidence':[qualified['record']['audit']['overlay'],qchecker,*policy_refs],
        'observations':[context['capture_ref'],*observations],'complete_capture':True,'model_requests':0},root)
    names=['difficulty_deb_source_failure_v2.py','difficulty_deb_original.py','difficulty_deb_registry_v2.py','difficulty_submission.py','difficulty_submission_v2.py']
    deps=save(output/'adapter-dependencies.json',{'code_refs':[io.evidence_ref(root/'scripts'/n,root) for n in names]+policy_refs,
        'checker_dependencies':qualified['overlay']['code_refs'],'image':context['image']},root)
    receipt={'schema_version':1,'evidence_kind':'direct_contract_failure','adapter_family':'deb','task_key':key,'task_version':context['task_version'],
        'original_result':context['original_result'],'original_native_rewards':(context['original'].get('verifier_result')or{}).get('rewards'),
        'original_capture':context['capture_ref'],'source_binding':source,'rebuild_result':None,'qualified_review':context['qualified_review'],
        'checker_version':'direct-explicit-profile-source-v2','checker':checker,'qualified_checker':qchecker,'checker_request':request,
        'audit_result':result_ref,'audit_reward':0,'audit_status':'scored','fixture_only':fixture,'model_requests':0,'training_prohibited':True,
        'adapter':checker,'adapter_dependencies':deps,'delivery_contract_ref':delivery,'scoring_basis':'submitted_source_reconstruction','rule_kind':'submitted_source','failure_evaluation_ref':proof,'failure_evaluator_validation_ref':validation}
    from difficulty_failure import validate_failure
    from difficulty_failure_qualification import validate as validate_qualification
    validate_failure(receipt,result,root);validate_qualification(receipt,qualified['record'],qualified['overlay'],root)
    save(output/'receipt.json',receipt,root);return receipt


if __name__=='__main__':
    p=argparse.ArgumentParser();p.add_argument('--run',type=Path,required=True);p.add_argument('--trial',type=Path,required=True)
    p.add_argument('--output',type=Path,required=True);p.add_argument('--fixture',action='store_true');p.add_argument('--validation',type=Path)
    a=p.parse_args();r=score(a.run,a.trial,a.output,fixture=a.fixture,validation=a.validation)
    print({k:r[k]for k in ('task_key','audit_reward','audit_status','fixture_only')})
