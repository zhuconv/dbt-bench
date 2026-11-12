#!/usr/bin/env python3
"""Rebuild one captured ADE customer submission and run its independent audit.

Uses a fresh native Harbor environment and the original verifier with read-only
collectors surrounding its existing dbt setup. Original trial scores are never
changed. This command is task-specific; it does not guess output tables.
"""
import argparse
import asyncio
import hashlib
import importlib.util
import json
import logging
import os
from pathlib import Path
import shutil
import sys
import traceback

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT/'src'))
from dbt_bench.backends import harbor_process_env
from dbt_bench.io import evidence_ref, file_hash, read_json, write_json
from dbt_bench.submission import inspect_submission

KEY = 'ade__ade-bench-ana-eng003'
CHECK = '''from pathlib import Path
import hashlib,json,os
root=Path('/app')
expected=json.loads(Path('/tmp/submission-receipt.json').read_text())['files']
actual=[]
for p in sorted(root.rglob('*')):
 name='submission/workspace/'+p.relative_to(root).as_posix()
 if p.is_symlink():
  assert p.resolve().is_relative_to(root.resolve())
  actual.append({'path':name,'symlink':os.readlink(p)})
 elif p.is_file():
  h=hashlib.sha256()
  with p.open('rb') as stream:
   for chunk in iter(lambda:stream.read(1048576),b''):h.update(chunk)
  actual.append({'path':name,'sha256':h.hexdigest(),'executable':bool(p.stat().st_mode & 0o111)})
assert sorted(actual,key=lambda r:r['path'])==expected,'Fresh workspace differs from captured submission'
print(json.dumps({'exact_files':len(actual),'source':'/app'}))
'''

PRUNE = '''from pathlib import Path
import json
root=Path('/app')
entries=json.loads(Path('/tmp/submission-receipt.json').read_text())['files']
expected={r['path'].removeprefix('submission/workspace/'):r for r in entries}
removed=[]
for p in sorted(root.rglob('*')):
 if not p.is_symlink() and not p.is_file():continue
 relative=p.relative_to(root).as_posix()
 if relative not in expected:
  p.unlink();removed.append(relative)
# Empty directories have no submission content. Do not recursively remove SIF
# lower-layer directories: overlay whiteouts can fail with EIO on Apptainer.
print(json.dumps({'removed_files':removed,'expected_files':len(expected)}))
'''


def instrument_tests(task, collector):
    """Preserve native scripts byte-for-byte; observers do not determine reward."""
    tests = task/'tests'
    native_script = tests/'test.sh'
    native_setup = tests/'test-setup.sh'
    before = {'test.sh':file_hash(native_script), 'test-setup.sh':file_hash(native_setup)}
    native_script.rename(tests/'native-test.sh')
    native_setup.rename(tests/'native-test-setup.sh')
    (tests/'test.sh').write_text('#!/bin/bash\npython3 /tests/customer-collector.py before || true\nexec bash /tests/native-test.sh\n')
    (tests/'test-setup.sh').write_text('#!/bin/bash\nbash /tests/native-test-setup.sh\nsetup_status=$?\npython3 /tests/customer-collector.py after-native-build "$setup_status" || true\nexit "$setup_status"\n')
    shutil.copyfile(collector, tests/'customer-collector.py')
    assert before == {'test.sh':file_hash(tests/'native-test.sh'), 'test-setup.sh':file_hash(tests/'native-test-setup.sh')}
    return before


async def run(args):
    from harbor.environments.singularity.singularity import SingularityEnvironment
    from harbor.models.task.task import Task
    from harbor.models.trial.config import ArtifactConfig
    from harbor.models.trial.paths import TrialPaths
    from harbor.trial.artifact_handler import ArtifactHandler
    from harbor.verifier.verifier import Verifier

    spec = read_json(args.spec)
    if spec['task_key'] != KEY or spec['artifacts'] != [
            {'source':'/app', 'destination':'submission/workspace', 'exclude':[]}]:
        raise ValueError('Requires the explicitly reviewed complete customer workspace declaration')
    trial = args.trial.resolve()
    native = read_json(trial/'result.json')
    capture = inspect_submission(trial, spec)
    if not capture['complete']:
        raise ValueError('Incomplete capture: '+str(capture['errors']))
    original = Task(task_dir=Path(native['config']['task']['path']))
    if original.checksum != native['task_checksum']:
        raise ValueError('Original trial task checksum changed')
    # This replay may use only the known native task, including its original setup.
    for relative in ['instruction.md','tests/test.sh','tests/test-setup.sh']:
        if file_hash(original.paths.task_dir/relative) != file_hash(ROOT/'tasks'/KEY/relative):
            raise ValueError('Unexpected task/verification variant: '+relative)
    out = args.output_dir.resolve()
    out.mkdir(parents=True, exist_ok=False)
    shutil.copyfile(Path(__file__), out/'runner.py')
    write_json(out/'submission-receipt.json', capture)
    shutil.copytree(original.paths.task_dir, out/'task', symlinks=True)
    checker_dir = ROOT/'validation/ade/ade-bench-ana-eng003'
    native_hashes = instrument_tests(out/'task', checker_dir/'collect_audit.py')
    task = Task(task_dir=out/'task')
    paths = TrialPaths(trial_dir=out/'environment')
    paths.mkdir()
    logger = logging.getLogger('customer-submission-replay')
    logger.setLevel(logging.DEBUG)
    handler = logging.FileHandler(out/'environment.log')
    logger.addHandler(handler)
    os.environ['PATH'] = harbor_process_env('apptainer')['PATH']
    env = SingularityEnvironment(environment_dir=task.paths.environment_dir,
        environment_name='customer-submission-replay', session_id=out.name,
        trial_paths=paths, task_env_config=task.config.environment,
        persistent_env={'DB_TYPE':'duckdb'}, logger=logger,
        singularity_no_mount='home,tmp,cwd,hostfs,bind-paths')
    report = {'kind':'customer_submission_diagnostic_replay', 'benchmark_trials':0, 'model_requests':0,
        'source_trial':evidence_ref(trial/'result.json',ROOT),
        'submission_spec':evidence_ref(args.spec.resolve(),ROOT), 'snapshot_hash':capture['snapshot_hash'],
        'source_lock':evidence_ref(ROOT/'sources.lock.yaml',ROOT),
        'native_scripts_preserved':native_hashes, 'original_task_checksum':native['task_checksum'],
        'replay_task_checksum':task.checksum, 'runner':evidence_ref(out/'runner.py',ROOT),
        'collector':evidence_ref(checker_dir/'collect_audit.py',ROOT),
        'original_native_result':native.get('verifier_result'), 'passed':False,
        'scope':'Same captured /app in a fresh copy of the native image. Original native setup rebuilds submitted model before hidden seeds/tests; independent audit reads those snapshots. Host/other-directory dependency changes are not reconstructed.'}

    async def checked(label, command, timeout=180):
        execution = await env.exec(command, timeout_sec=timeout)
        write_json(out/(label+'.json'), execution.model_dump(mode='json'))
        if execution.return_code:
            raise RuntimeError(label+': '+str(execution.stdout)+str(execution.stderr))
        print(label+': passed',flush=True)

    try:
        await asyncio.wait_for(env.start(force_build=False), timeout=300)
        if task.config.environment.healthcheck:
            await checked('native-healthcheck',task.config.environment.healthcheck.command)
        await checked('initial-hidden-paths','test ! -e /tests && test ! -e /solution')
        await env.upload_file(ROOT/'scripts/context_probe.py','/tmp/dbt-bench-context-probe.py')
        await checked('initial-context','python3 /tmp/dbt-bench-context-probe.py --db /app/analytics_engineering.duckdb')
        # Remove only extra files in this fresh diagnostic workspace, then prove
        # exact file content after upload; no prior trial filesystem is reused.
        await env.upload_file(out/'submission-receipt.json','/tmp/submission-receipt.json')
        prune_path = out/'prune_workspace.py'
        prune_path.write_text(PRUNE)
        await env.upload_file(prune_path,'/tmp/prune_workspace.py')
        await checked('prune-fresh-workspace','python3 /tmp/prune_workspace.py')
        artifacts = ArtifactHandler(artifacts=[ArtifactConfig(**a) for a in spec['artifacts']],logger=logger)
        await artifacts.upload_artifacts(env,trial/'artifacts',source_artifacts_dir='/logs/artifacts',target_artifacts_dir='/logs/artifacts')
        check_path = out/'check_snapshot.py'
        check_path.write_text(CHECK)
        await env.upload_file(check_path,'/tmp/check_snapshot.py')
        await checked('transferred-workspace','python3 /tmp/check_snapshot.py')
        await checked('verifier-context','python3 /tmp/dbt-bench-context-probe.py --db /app/analytics_engineering.duckdb')
        verifier = Verifier(task=task, trial_paths=paths, environment=env, logger=logger,
                            override_env={'DB_TYPE':'duckdb'})
        result = await asyncio.wait_for(verifier.verify(), timeout=task.config.verifier.timeout_sec)
        report['replay_native_result'] = result.model_dump(mode='json')
        await env.download_dir('/logs/verifier/customer-audit',out/'snapshots')
        module_spec = importlib.util.spec_from_file_location('customer_audit',checker_dir/'audit.py')
        audit = importlib.util.module_from_spec(module_spec)
        module_spec.loader.exec_module(audit)
        scored = audit.score(out/'snapshots')
        checker_sources = [checker_dir/'audit.py', checker_dir/'collect_audit.py', ROOT/'src/dbt_bench/comparison.py']
        checker_version = 'customer-key-audit-v1:'+hashlib.sha256(''.join(file_hash(p) for p in checker_sources).encode()).hexdigest()
        if checker_version != read_json(checker_dir/'contract.yaml')['audit']['checker_version']:
            raise ValueError('Audit implementation differs from its reviewed checker version')
        write_json(out/'audit.json', {'checker_version':checker_version,
            'status':'scored', 'reward':float(scored['success']), **scored,
            'code':[evidence_ref(p,ROOT) for p in checker_sources],
            'submission_snapshot_hash':capture['snapshot_hash'], 'checker_certification':'Consult current per-task validation; not automatically certified by this replay'})
        report['audit'] = evidence_ref(out/'audit.json',ROOT)
        report['audit_reward'] = float(scored['success'])
        report['native_score_reproduced'] = result.rewards == (native.get('verifier_result') or {}).get('rewards')
        report['passed'] = report['native_score_reproduced']
    except Exception as exc:
        report['error'] = {'type':type(exc).__name__, 'message':str(exc), 'traceback':traceback.format_exc()}
    finally:
        try:
            await env.stop(delete=True)
        except Exception as exc:
            report['cleanup_error'] = str(exc)
            report['passed'] = False
        handler.flush()
        handler.close()
        logger.removeHandler(handler)
        report['environment_log'] = evidence_ref(out/'environment.log',ROOT)
        write_json(out/'result.json',report)
    print(json.dumps({'passed':report['passed'], 'audit_reward':report.get('audit_reward'), 'error':report.get('error'), 'output':str(out/'result.json')}))
    return 0 if report['passed'] else 1


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--trial',type=Path,required=True)
    parser.add_argument('--spec',type=Path,required=True)
    parser.add_argument('--output-dir',type=Path,required=True)
    return asyncio.run(run(parser.parse_args()))


if __name__ == '__main__':
    raise SystemExit(main())
