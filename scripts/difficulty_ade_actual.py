"""Audit one immutable real ADE Qwen attempt; never invoke a model or retry it."""
from pathlib import Path
import argparse
import os
import subprocess
import sys
import traceback

ROOT=Path(__file__).resolve().parents[1]
sys.path[:0]=[str(ROOT/'src'),str(ROOT/'scripts')]
from dbt_bench import io
from difficulty_ade_prepare_audit_v2 import prepare


def command(argv,output,name):
    env=os.environ|{'PATH':'/opt/apps/tacc-apptainer/1.4.1/bin:'+os.environ['PATH'],
        'PYTHONPATH':str(ROOT/'src')+':'+str(ROOT/'scripts'),'PYTHONDONTWRITEBYTECODE':'1'}
    log=output/(name+'.log')
    with log.open('x') as stream:
        process=subprocess.run(argv,cwd=ROOT,env=env,stdout=stream,stderr=subprocess.STDOUT,timeout=2400)
    io.write_json(output/(name+'-process.json'),{'argv':argv,'returncode':process.returncode,
        'log':io.evidence_ref(log,ROOT),'actual_model_audit':True,'model_requests':0})
    if process.returncode:raise ValueError(name+' process failed: '+str(process.returncode))


def run(directory,trial,output):
    directory,trial,output=map(Path,(directory,trial,output))
    if output.exists() or not output.resolve().is_relative_to('/scratch'):
        raise ValueError('Use a fresh SCRATCH audit directory')
    manifest=io.read_json(directory/'manifest.json');original=io.read_json(trial/'result.json')
    if manifest['identity']['purpose']!='difficulty_only_no_training' or not original.get('finished_at'):
        raise ValueError('A terminal actual difficulty attempt is required')
    if original['config']['agent'].get('name')!='qwen-coder' or original['config']['agent'].get('import_path'):
        raise ValueError('Not a real native Qwen model attempt')
    output.mkdir(parents=True)
    record={'schema_version':1,'task_key':manifest['identity']['task_key'],
        'original_result':io.evidence_ref(trial/'result.json',ROOT),'prepared_run':io.evidence_ref(directory/'manifest.json',ROOT),
        'original_native_rewards':(original.get('verifier_result')or{}).get('rewards'),
        'actual_model_audit':True,'fixture_only':False,'model_requests':0,'training_prohibited':True,
        'runner':io.evidence_ref(Path(__file__),ROOT),'pipeline_complete':False,'audit_reward':None}
    try:
        prepare(directory,trial,output/'rebuild',fixture=False)
        command([str(ROOT/'runtime/.venv/bin/harbor'),'run','-c',str(output/'rebuild/batch/config.yaml')],output,'rebuild')
        command([str(ROOT/'runtime/.venv/bin/python'),str(ROOT/'scripts/difficulty_ade_score_v8.py'),
            '--audit-dir',str(output/'rebuild'),'--output',str(output/'scoring')],output,'score')
        receipt=io.read_json(output/'scoring/receipt.json')
        if receipt['fixture_only'] is not False or receipt['original_result']!=record['original_result']:
            raise ValueError('Actual audit receipt identity differs')
        record.update(receipt=io.evidence_ref(output/'scoring/receipt.json',ROOT),
            audit_reward=receipt['audit_reward'],audit_status=receipt['audit_status'],pipeline_complete=True)
    except Exception as exc:
        record.update(error={'type':type(exc).__name__,'message':str(exc)},audit_status='unknown')
        (output/'failure.txt').write_text(traceback.format_exc())
    io.write_json(output/'outcome.json',record)
    return record


if __name__=='__main__':
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('--run',type=Path,required=True)
    p.add_argument('--trial',type=Path,required=True);p.add_argument('--output',type=Path,required=True)
    a=p.parse_args();print(run(a.run,a.trial,a.output))
