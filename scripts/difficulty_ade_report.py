"""Reconcile all fifteen actual ADE audit-conformance records, without reruns."""
import argparse
from pathlib import Path
import subprocess
import sys

ROOT=Path(__file__).resolve().parents[1]
sys.path[:0]=[str(ROOT/'src'),str(ROOT/'scripts')]
from dbt_bench import io
from difficulty_ade_registry import ENTRIES


def report(directory):
    directory=Path(directory)
    output=directory/'final-v4'
    output.mkdir(exist_ok=True)
    summary=output/'summary.json'
    if summary.exists():raise ValueError('Preserve previous conformance summary')
    rows=[]
    for key in sorted(ENTRIES):
        if key=='ade__ade-bench-ana-eng005':
            audit=directory.parent/'audit-conformance-ana005-v1'
        elif key=='ade__ade-bench-f1003':
            audit=directory.parent/'audit-conformance-f1003-v1'
        else:
            audit=directory/'conformance-v1/tasks'/key/'rebuild'
        destination=output/key
        receipt=destination/'receipt.json'
        if not receipt.is_file():
            argv=[sys.executable,str(ROOT/'scripts/difficulty_ade_score_v4.py'),
                  '--audit-dir',str(audit),'--output',str(destination)]
            with (output/(key+'.log')).open('x') as log:
                result=subprocess.run(argv,stdout=log,stderr=subprocess.STDOUT,cwd=ROOT,timeout=900)
            if result.returncode:
                raise ValueError('Preserve score failure and investigate '+key)
        value=io.read_json(receipt)
        for field in ('original_result','source_binding','qualified_review','adapter',
                      'checker','audit_result','adapter_dependencies','submission_contract'):
            if not io.evidence_ok(value[field],ROOT):raise ValueError('Stale conformance '+key+' '+field)
        if not value['fixture_only'] or not value['training_prohibited'] or value['model_requests']:
            raise ValueError('Conformance is not explicitly isolated from measurement')
        deps=io.read_json(ROOT/value['adapter_dependencies']['path'])
        for dep in deps['code_refs']+deps['checker_dependencies']+[deps['image']]:
            if not io.evidence_ok(dep,ROOT):raise ValueError('Changed dependency '+dep['path'])
        rows.append({'task_key':key,'native_rewards':value['original_native_rewards'],
                     'audit_reward':value['audit_reward'],'audit_status':value['audit_status'],
                     'receipt':io.evidence_ref(receipt,ROOT),'submission_contract':value['submission_contract']})
        print(key,value['audit_reward'],flush=True)
    source_only=directory/'source-only-ana005-v1/outcome.json'
    control=io.read_json(source_only)
    if not (control['original_database_equals_initial'] and control['agent_dbt_invocations']==0
            and control['native_reward']=={'reward':1.0} and control['audit_reward']==1.0):
        raise ValueError('Source-only legal control did not establish expected behavior')
    result={'schema_version':1,'tasks':rows,'task_count':len(rows),'fixture_only':True,
            'model_requests':0,'training_prohibited':True,'difficulty_attempts':0,
            'counts':{str(n):sum(r['audit_reward']==n for r in rows)for n in (0.0,1.0,None)},
            'source_only_legal_control':io.evidence_ref(source_only,ROOT),
            'adapter':io.evidence_ref(ROOT/'scripts/difficulty_ade_score_v4.py',ROOT),
            'limitations':['F1009 retains unknown because the original capture lacks a verified original SQL-clock/dbt-session relationship. Fresh rebuild clocks do not establish an earlier submission date.',
                           'Oracle failures of a qualified audit are preserved actual controls, not reasons to change the qualified audit or model difficulty success.'],
            'scope':'Actual CPU-only source-delivery conformance. Original native records/checkers remain immutable; none of these executions is a model attempt.'}
    io.write_json(summary,result)
    return result

if __name__=='__main__':
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('--directory',type=Path,required=True)
    print(report(p.parse_args().directory)['counts'])
