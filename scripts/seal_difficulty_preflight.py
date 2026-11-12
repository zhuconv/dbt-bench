#!/usr/bin/env python3
"""Seal a prepared difficulty run before Slurm submission; no model request."""
import argparse
from pathlib import Path
import sys
import tomllib

ROOT=Path(__file__).resolve().parents[1]
sys.path[:0]=[str(ROOT/'src'),str(ROOT/'scripts')]
from dbt_bench import io
from run_difficulty_gpu import validate


def observation_only(actual, production):
    actual,production=Path(actual),Path(production)
    left={x['path']:x['sha256'] for x in io.tree_manifest(actual)}
    right={x['path']:x['sha256'] for x in io.tree_manifest(production)}
    if set(left)!=set(right) or {k for k in left if left[k]!=right[k]}!={'task.toml'}:
        raise ValueError('Only task.toml may differ from reviewed production task')
    a=tomllib.loads((actual/'task.toml').read_text())
    b=tomllib.loads((production/'task.toml').read_text())
    original=b.get('verifier',{}).get('collect',[])
    current=a.get('verifier',{}).get('collect',[])
    if len(current)!=len(original)+1 or current[:-1]!=original:
        raise ValueError('Must append exactly one native collect hook')
    if original:a['verifier']['collect']=original
    else:a['verifier'].pop('collect')
    if a!=b:raise ValueError('Native budget/environment/agent/verifier configuration changed')
    return True


def seal(run,root=ROOT):
    run,root=Path(run),Path(root)
    if (run/'preflight.json').exists():raise ValueError('Preflight already sealed')
    m,c,r=validate(run,root)
    key=m['identity']['task_key']
    observation_only(c['tasks'][0]['path'],run/'production/tasks'/key)
    proof={'schema_version':1,'manifest':io.evidence_ref(run/'manifest.json',root),
        'runner':io.evidence_ref(root/'scripts/run_difficulty_gpu.py',root),
        'slurm_script':io.evidence_ref(root/'scripts/slurm/gh-dev-difficulty.sbatch',root),
        'preflight_validator':io.evidence_ref(__file__,ROOT),
        'only_native_collect_added':True,'native_tests_unchanged':True,
        'agent':'native-qwen-coder','attempt_ids':m['identity']['attempt_ids'],
        'model_requests':0,'training_prohibited':True}
    io.write_json(run/'preflight.json',proof)
    return {'task_key':key,'attempt_ids':proof['attempt_ids'],'model_requests':0}


if __name__=='__main__':
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('--run',type=Path,required=True)
    a=p.parse_args();print(seal(a.run))
