#!/usr/bin/env python3
"""Replay a retained submission through the native verifier in a fresh environment.

This is a diagnostic replay, never an overwrite of the original native score.
No agent, model request, reference solution or project rebuild is introduced.
"""
import argparse
import asyncio
import json
import logging
import os
from pathlib import Path
import shutil
import sys
import traceback

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'src'))
from dbt_bench.backends import harbor_process_env
from dbt_bench.io import evidence_ref, read_json, write_json
from dbt_bench.submission import inspect_submission

REPLAY_CONTRACTS = {
    'deb__dbt-session-attribution': {'database': '/app/database/retail.duckdb',
        'schema': 'main', 'table': 'attribution_report', 'cwd': '/app'},
    'spider2__workday002': {'database': '/workspace/workday.duckdb',
        'schema': 'main', 'table': 'workday__job_overview', 'cwd': '/workspace'},
}

CHECK = '''from pathlib import Path
import hashlib,json,os
spec=json.loads(Path('/tmp/submission-spec.json').read_text())
expected=json.loads(Path('/tmp/submission-receipt.json').read_text())['files']
actual=[]
for declared in spec['artifacts']:
 root=Path(declared['source'])
 for p in sorted(root.rglob('*')):
  name=declared['destination']+'/'+p.relative_to(root).as_posix()
  if p.is_symlink():
   assert p.resolve().is_relative_to(root.resolve())
   actual.append({'path':name,'symlink':os.readlink(p)})
  elif p.is_file():
   h=hashlib.sha256()
   with p.open('rb') as stream:
    for b in iter(lambda:stream.read(1048576),b''):h.update(b)
   actual.append({'path':name,'sha256':h.hexdigest(),'executable':bool(p.stat().st_mode & 0o111)})
assert sorted(actual,key=lambda x:x['path'])==expected,'Transferred submission differs'
import duckdb
contract=json.loads(Path('/tmp/replay-contract.json').read_text())
with duckdb.connect(contract['database'],read_only=True) as con:
 present=bool(con.execute("select count(*) from information_schema.tables where table_schema=? and table_name=?",[contract['schema'],contract['table']]).fetchone()[0])
 rows=con.execute('select count(*) from "'+contract['schema']+'"."'+contract['table']+'"').fetchone()[0] if present else None
print(json.dumps({'exact_files':len(actual),'duckdb':duckdb.__version__,'output_present':present,'output_rows':rows,
 'native_project_env':os.environ.get('DBT_PROJECT_DIR_DUCKDB')}))
'''


async def run(args):
    from harbor.environments.singularity.singularity import SingularityEnvironment
    from harbor.models.task.task import Task
    from harbor.models.trial.config import ArtifactConfig
    from harbor.models.trial.paths import TrialPaths
    from harbor.trial.artifact_handler import ArtifactHandler
    from harbor.verifier.verifier import Verifier
    spec = read_json(args.spec)
    if spec['task_key'] not in REPLAY_CONTRACTS:
        raise ValueError('No reviewed database/output replay contract for this task')
    replay_contract = REPLAY_CONTRACTS[spec['task_key']]
    trial = args.trial.resolve()
    native = read_json(trial / 'result.json')
    capture = inspect_submission(trial, spec)
    if not capture['complete']:
        raise ValueError('Incomplete submission capture: ' + str(capture['errors']))
    task = Task(task_dir=Path(native['config']['task']['path']))
    if task.checksum != native['task_checksum']:
        raise ValueError('Replay task differs from the original trial task checksum')
    out = args.output_dir.resolve()
    out.mkdir(parents=True, exist_ok=False)
    shutil.copyfile(Path(__file__), out / 'script.py')
    write_json(out / 'submission-receipt.json', capture)
    write_json(out / 'replay-contract.json', replay_contract)
    logger = logging.getLogger('submission-replay');logger.setLevel(logging.DEBUG)
    handler = logging.FileHandler(out / 'environment.log');logger.addHandler(handler)
    paths = TrialPaths(trial_dir=out / 'environment');paths.mkdir()
    os.environ['PATH'] = harbor_process_env('apptainer')['PATH']
    env = SingularityEnvironment(environment_dir=task.paths.environment_dir,
        environment_name='submission-replay',session_id=out.name,trial_paths=paths,
        task_env_config=task.config.environment,persistent_env={'DB_TYPE':'duckdb'},logger=logger,
        singularity_no_mount='home,tmp,cwd,hostfs,bind-paths')
    report = {'schema_version':1, 'kind':'native_verifier_submission_replay', 'benchmark_trials':0,
        'model_requests':0, 'source_trial':evidence_ref(trial / 'result.json',ROOT),
        'source_lock':evidence_ref(ROOT / 'sources.lock.yaml',ROOT),
        'native_task_checksum':native['task_checksum'],
        'submission_spec':evidence_ref(args.spec.resolve(),ROOT), 'snapshot_hash':capture['snapshot_hash'],
        'script':evidence_ref(out / 'script.py',ROOT), 'passed':False,
        'replay_contract':evidence_ref(out / 'replay-contract.json',ROOT),
        'scope':'Exact declared submission file replay and unchanged native verifier. Outside-workspace dependencies are not restored; this does not certify semantic correctness.'}
    async def checked(label, command):
        result = await env.exec(command,timeout_sec=180)
        write_json(out / (label+'.json'),result.model_dump(mode='json'))
        if result.return_code:
            raise RuntimeError(label + ': ' + (result.stdout or '') + (result.stderr or ''))
        print(label + ': passed',flush=True)
    try:
        await asyncio.wait_for(env.start(force_build=False),timeout=300)
        if task.config.environment.healthcheck:
            await checked('initial-healthcheck',task.config.environment.healthcheck.command)
        artifact_handler = ArtifactHandler(artifacts=[ArtifactConfig(**a) for a in spec['artifacts']],logger=logger)
        await artifact_handler.upload_artifacts(env,trial / 'artifacts',
            source_artifacts_dir='/logs/artifacts',target_artifacts_dir='/logs/artifacts')
        checker = out / 'check_snapshot.py';checker.write_text(CHECK)
        await env.upload_file(checker,'/tmp/check_snapshot.py')
        await env.upload_file(args.spec.resolve(),'/tmp/submission-spec.json')
        await env.upload_file(out / 'submission-receipt.json','/tmp/submission-receipt.json')
        await env.upload_file(out / 'replay-contract.json','/tmp/replay-contract.json')
        await checked('before-verifier','python3 /tmp/check_snapshot.py')
        shutil.copyfile(ROOT / 'scripts/context_probe.py',out / 'context_probe.py')
        await env.upload_file(out / 'context_probe.py','/tmp/replay-context-probe.py')
        import shlex
        await checked('verifier-context','cd '+shlex.quote(replay_contract['cwd'])+
                      ' && python3 /tmp/replay-context-probe.py --db '+shlex.quote(replay_contract['database']))
        verifier = Verifier(task=task,trial_paths=paths,environment=env,logger=logger,
                            override_env={'DB_TYPE':'duckdb'})
        result = await asyncio.wait_for(verifier.verify(),timeout=task.config.verifier.timeout_sec)
        report['replay_native_result'] = result.model_dump(mode='json')
        report['original_native_result'] = native.get('verifier_result')
        await checked('after-verifier','python3 /tmp/check_snapshot.py')
        report['passed'] = result.rewards == (native.get('verifier_result') or {}).get('rewards')
    except Exception as exc:
        report['error'] = {'type':type(exc).__name__,'message':str(exc),'traceback':traceback.format_exc()}
    finally:
        try:await env.stop(delete=True)
        except Exception as exc:report.update(passed=False,cleanup_error=str(exc))
        handler.close();logger.removeHandler(handler)
        report['environment_log'] = evidence_ref(out / 'environment.log',ROOT)
        write_json(out / 'result.json',report)
    print(json.dumps({'passed':report['passed'],'output':str(out / 'result.json')}))
    return 0 if report['passed'] else 1


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--trial',type=Path,required=True)
    parser.add_argument('--spec',type=Path,required=True)
    parser.add_argument('--output-dir',type=Path,required=True)
    raise SystemExit(asyncio.run(run(parser.parse_args())))
