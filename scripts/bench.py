#!/usr/bin/env python3
"""Prepare a portable prescribed batch, print a Slurm command, inspect raw results.

Never submits, retries, draws a split or trains. Execution remains native Harbor.
"""
import argparse
import json
from pathlib import Path
import shlex
import sys
ROOT=Path(__file__).resolve().parents[1]
sys.path[:0]=[str(ROOT/'src'),str(ROOT/'scripts')]
from dbt_bench import io
from dbt_bench.local_config import required


def command(batch,test_only=False):
    from release_tools import validate_batch
    m=validate_batch(batch);s=required();hours=m['identity']['walltime_sec']//3600
    argv=['sbatch','--parsable','--account='+s['allocation'],'--partition='+s['partition'],
        '--nodes=1','--ntasks=1',f'--time={hours:02}:00:00','--signal=B:TERM@180',
        '--comment=dbt-difficulty-'+m['experiment_id'],'--job-name=dbt-release-'+m['identity']['batch_id'],
        '--output='+str(batch/'slurm-%j.out'),'--error='+str(batch/'slurm-%j.err')]
    if test_only:argv.append('--test-only')
    argv += [str(ROOT/'scripts/slurm/release-batch.sbatch'),str(batch),s['runtime_python'],str(Path(__import__('os').environ.get('DBT_BENCH_LOCAL_CONFIG',ROOT/'configs/local.toml')).resolve())]
    return shlex.join(argv)


def inspect(batch):
    manifest=io.read_json(batch/'manifest.json');records=[];seen=set();missing=[]
    for member in manifest['identity']['members']:
        run=Path(member['run']);m=io.read_json(run/'manifest.json');paths=list(run.glob('jobs/*/*/result.json'))
        paths.sort(key=lambda p:io.read_json(p).get('started_at') or '')
        if len(paths)>len(m['planned']):raise ValueError('Unexpected duplicate/excess trials')
        for slot,p in zip(m['planned'],paths):
            r=io.read_json(p)
            if r.get('id') in seen:raise ValueError('Duplicate trial ID')
            seen.add(r.get('id'))
            records.append({**slot,'trial_id':r.get('id'),'native_reward':(r.get('verifier_result') or {}).get('rewards'),
                'exception_info':r.get('exception_info'),'error_policy':r.get('error_policy'),'finished_at':r.get('finished_at'),
                'native_result':io.evidence_ref(p,ROOT),'audit_reward':None,'joint_success':None,
                'status':'pending_submission_audit_and_validity_review'})
        missing += m['planned'][len(paths):]
    return {'experiment_id':manifest['experiment_id'],'raw_trials':records,'missing_slots':missing,
        'audit_status':'unscored; use qualified checker on the captured same submission; never infer audit from native reward',
        'training_prohibited':True,'cross_account_merge_supported':False}


if __name__=='__main__':
    p=argparse.ArgumentParser(description=__doc__);sub=p.add_subparsers(dest='action',required=True)
    x=sub.add_parser('prepare');x.add_argument('--batch-id',required=True);x.add_argument('--output',type=Path,required=True)
    for name in ('check','sbatch-command','inspect'):
        x=sub.add_parser(name);x.add_argument('--batch',type=Path,required=True)
        if name=='sbatch-command':x.add_argument('--test-only',action='store_true')
    a=p.parse_args()
    if a.action=='prepare':
        from release_tools import prepare
        print(prepare(a.batch_id,a.output))
    elif a.action=='check':
        from release_tools import validate_batch
        m=validate_batch(a.batch);print(json.dumps({'passed':True,'experiment_id':m['experiment_id'],'model_requests':0}))
    elif a.action=='sbatch-command':print(command(a.batch,a.test_only))
    else:print(json.dumps(inspect(a.batch),indent=2))
