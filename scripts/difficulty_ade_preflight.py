"""Seal new difficulty runs with exactly the registered F1009 clock observer."""
import argparse
import base64
from pathlib import Path
import shlex
import sys
import tomllib

ROOT=Path(__file__).resolve().parents[1]
sys.path[:0]=[str(ROOT/'src'),str(ROOT/'scripts')]
from dbt_bench import io
from run_difficulty_gpu_v2 import validate
from difficulty_ade_prepare_clock_v2 import assert_unlaunched
from seal_difficulty_preflight_v2 import observation_only as standard_observation


def observation_only(actual,production,identity,root=ROOT):
    observer=identity.get('ade_sql_session_observer')
    if observer is None:return standard_observation(actual,production)
    if identity['task_key']!='ade__ade-bench-f1009':raise ValueError('Clock observer is registered only for F1009')
    for name in ('source','preparer'):
        if not io.evidence_ok(observer[name],root):raise ValueError('Clock observer source changed')
    source=Path(root)/observer['source']['path']
    code="import base64;exec(compile(base64.b64decode("+repr(base64.b64encode(source.read_bytes()).decode())+"),'difficulty_ade_session_collect.py','exec'))"
    command=shlex.join(['python3','-c',code,'--project-root','/app','--database','/app/f1.duckdb',
        '--capture-root','/tmp/dbt-bench-difficulty-submission','--output','/tmp/dbt-bench-difficulty-submission/dbt-session-clock.json'])
    actual,production=Path(actual),Path(production)
    left={x['path']:x['sha256']for x in io.tree_manifest(actual)}
    right={x['path']:x['sha256']for x in io.tree_manifest(production)}
    if set(left)!=set(right)or {k for k in left if left[k]!=right[k]}!={'task.toml'}:
        raise ValueError('Only registered observation task.toml changes are allowed')
    a=tomllib.loads((actual/'task.toml').read_text());b=tomllib.loads((production/'task.toml').read_text())
    old=b.get('verifier',{}).get('collect',[]);current=a.get('verifier',{}).get('collect',[])
    if len(current)!=len(old)+2 or current[:len(old)]!=old or current[-1]!={'command':command,'timeout_sec':120}:
        raise ValueError('Require source capture followed by exactly the registered SQL-session observer')
    if old:a['verifier']['collect']=old
    else:a['verifier'].pop('collect')
    if a!=b:raise ValueError('Native task configuration changed beyond observations')
    return True


def seal(run,root=ROOT):
    run,root=Path(run),Path(root);assert_unlaunched(run)
    m,c,r=validate(run,root);key=m['identity']['task_key']
    observation_only(c['tasks'][0]['path'],run/'production/tasks'/key,m['identity'],root)
    proof={'schema_version':1,'manifest':io.evidence_ref(run/'manifest.json',root),
        'runner':io.evidence_ref(root/'scripts/run_difficulty_gpu_v2.py',root),
        'slurm_script':io.evidence_ref(root/'scripts/slurm/gh-dev-difficulty-v2.sbatch',root),
        'preflight_validator':io.evidence_ref(Path(__file__),root),
        'only_native_collect_added':True,'native_tests_unchanged':True,'agent':'native-qwen-coder',
        'attempt_ids':m['identity']['attempt_ids'],'registered_sql_session_observer':m['identity'].get('ade_sql_session_observer'),
        'model_requests':0,'training_prohibited':True}
    io.write_json(run/'preflight.json',proof)
    return {'task_key':key,'attempt_ids':proof['attempt_ids'],'model_requests':0}

if __name__=='__main__':
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('--run',required=True,type=Path)
    print(seal(p.parse_args().run))
