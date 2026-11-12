#!/usr/bin/env python3
"""Rebuild captured Workday sources on pristine inputs, without a model call.

Captured dependency sources are transferred with the dbt project. The agent's
database is unavailable during reconstruction; only after its output is sealed
is a read-only copy opened to compare the original submission. Native scores
are retained independently, and this diagnostic is not a certified audit.
"""
import argparse
import asyncio
import importlib.util
import json
import logging
import os
from pathlib import Path
import shutil
import sys
import tarfile
import traceback

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'src'))
from dbt_bench.backends import binding_identity, harbor_process_env
from dbt_bench.catalog import load_catalog
from dbt_bench.io import evidence_ref, file_hash, read_json, write_json
from dbt_bench.submission import inspect_submission

KEY = 'spider2__workday002'
CODE = ROOT / 'validation/spider2/workday002'


def load(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


TRANSFER = '''from pathlib import Path, PurePosixPath
import json,tarfile
from reconstruction import source_manifest,sha
root=Path('/workspace')
expected=json.loads(Path('/tmp/workday-source-inventory.json').read_text())['files']
db=root/'workday.duckdb';before=sha(db)
with tarfile.open('/tmp/workday-sources.tar') as archive:
 members=archive.getmembers()
 assert len(members)==len(expected) and {m.name for m in members}==set(expected)
 for member in members:
  name=PurePosixPath(member.name)
  assert member.isfile() and not name.is_absolute() and '..' not in name.parts
 removed=[]
 for path in sorted(root.rglob('*')):
  if path==db:continue
  if path.is_file() or path.is_symlink():
   removed.append(str(path.relative_to(root)));path.unlink()
 archive.extractall(root,filter='data')
 assert source_manifest(root)==expected
assert sha(db)==before
print(json.dumps({'source_files':len(expected),'removed_initial_files':removed,'initial_database_preserved':True,'database_sha256':before}))
'''


async def run(args):
    from harbor.environments.singularity.singularity import SingularityEnvironment
    from harbor.models.task.task import Task
    from harbor.models.trial.paths import TrialPaths
    from harbor.verifier.verifier import Verifier
    helper = load('workday_reconstruction', CODE / 'reconstruction.py')
    independent = load('workday_independent_diagnostic', CODE / 'review_build.py')
    trial = args.trial.resolve()
    raw = read_json(trial / 'result.json')
    spec = read_json(args.spec)
    if spec['task_key'] != KEY or spec['artifacts'] != [{'source': '/workspace', 'destination': 'submission/workspace', 'exclude': []}]:
        raise ValueError('Requires the declared complete Workday workspace')
    capture = inspect_submission(trial, spec)
    if not capture['complete']:
        raise ValueError('Incomplete submission capture')
    saved = read_json(trial / 'submission-capture.json')
    if saved != capture:
        raise ValueError('Captured submission changed')
    task = Task(task_dir=Path(raw['config']['task']['path']))
    if task.checksum != raw['task_checksum']:
        raise ValueError('Original task checksum changed')
    row = next(r for r in load_catalog(ROOT) if r['task_key'] == KEY)
    source = trial / 'artifacts/submission/workspace'
    files = helper.source_manifest(source)
    required = {'dbt_project.yml', 'profiles.yml', 'models/workday__job_overview.sql', 'packages.yml', 'package-lock.yml'}
    if not required <= files.keys() or not any(name.startswith('dbt_packages/') for name in files):
        raise ValueError('Captured project or dependency sources absent')
    out = args.output_dir.resolve()
    out.mkdir(parents=True, exist_ok=False)
    shutil.copyfile(Path(__file__), out / 'runner.py')
    for name in ['reconstruction.py', 'collect_reconstruction.py', 'review_build.py']:
        shutil.copyfile(CODE / name, out / name)
    shutil.copyfile(ROOT / 'scripts/context_probe.py', out / 'context_probe.py')
    (out / 'transfer.py').write_text(TRANSFER)
    write_json(out / 'source-inventory.json', {'files': files, 'generated_not_transferred': ['target', 'logs'],
        'database_unavailable_during_build': True, 'dependencies': 'Captured installed package source bytes; no dependency re-resolution',
        'submission_receipt': evidence_ref(trial / 'submission-capture.json', ROOT)})
    with tarfile.open(out / 'sources.tar', 'w') as archive:
        for name in files:
            archive.add(source / name, arcname=name, recursive=False)
    logger = logging.getLogger('workday-source-reconstruction')
    logger.setLevel(logging.DEBUG)
    handler = logging.FileHandler(out / 'environment.log')
    logger.addHandler(handler)
    paths = TrialPaths(trial_dir=out / 'environment')
    paths.mkdir()
    os.environ['PATH'] = harbor_process_env('apptainer')['PATH']
    env = SingularityEnvironment(environment_dir=task.paths.environment_dir,
        environment_name='workday-source-reconstruction', session_id=out.name, trial_paths=paths,
        task_env_config=task.config.environment, logger=logger,
        singularity_no_mount='home,tmp,cwd,hostfs,bind-paths')
    ref = lambda p: evidence_ref(p, ROOT)
    report = {'kind': 'workday_captured_source_reconstruction', 'benchmark_trials': 0, 'model_requests': 0,
        'source_trial': ref(trial / 'result.json'), 'source_lock': ref(ROOT / 'sources.lock.yaml'),
        'source_inventory': ref(out / 'source-inventory.json'), 'submission_snapshot_hash': capture['snapshot_hash'],
        'backend_binding': binding_identity(ROOT, row, 'apptainer'), 'passed': False, 'audit_reward': None,
        'semantic_certification': False, 'original_native_result': raw.get('verifier_result'),
        'agent_database_used_as_build_input': False, 'prior_build_outputs_transferred': False,
        'scope': 'All models rebuilt from captured project and dependency source bytes on pristine native input. Captured database is read only after reconstructed output is sealed. No arbitrary outside-workspace dependency restoration or semantic/trace certification.'}

    async def execute(label, command, timeout=180, required=True):
        value = await env.exec(command, timeout_sec=timeout)
        write_json(out / (label + '.json'), value.model_dump(mode='json'))
        print(label + ': ' + str(value.return_code), flush=True)
        if required and value.return_code:
            raise RuntimeError(label + ': ' + str(value.stdout) + str(value.stderr))
        return value

    try:
        await asyncio.wait_for(env.start(force_build=False), timeout=300)
        if task.config.environment.healthcheck:
            await execute('healthcheck', task.config.environment.healthcheck.command)
        await execute('initial-hidden-paths', 'test ! -e /tests && test ! -e /solution && test ! -e /tmp/captured-workday.duckdb')
        for name in ['reconstruction.py', 'collect_reconstruction.py', 'context_probe.py', 'transfer.py']:
            await env.upload_file(out / name, '/tmp/' + name)
        collector = 'python3 /tmp/collect_reconstruction.py '
        await execute('initial-context', 'cd /workspace && python3 /tmp/context_probe.py --db /workspace/workday.duckdb')
        await execute('initial', collector + 'initial')
        await env.upload_file(out / 'source-inventory.json', '/tmp/workday-source-inventory.json')
        await env.upload_file(out / 'sources.tar', '/tmp/workday-sources.tar')
        await execute('source-transfer', 'python3 /tmp/transfer.py')
        await execute('before-build', collector + 'before-build')
        built = await execute('dbt-run-all', 'cd /workspace && dbt run --no-partial-parse --profiles-dir /workspace', timeout=900, required=False)
        report['dbt_exit_code'] = built.return_code
        await execute('after-build', collector + 'after-build')
        # Seal the independently built output on the host before exposing a copy
        # of the agent database. The raw original remains unchanged.
        await env.download_dir('/logs/agent/workday-rebuild', out / 'snapshots')
        snapshots = {phase: read_json(out / 'snapshots' / phase / 'snapshot.json')
                     for phase in ['initial', 'before-build', 'after-build']}
        for phase, snapshot in snapshots.items():
            assert snapshot['read_only'] and not snapshot['hidden_paths_present']
            for name, digest in snapshot['files'].items():
                assert file_hash(out / 'snapshots' / phase / name) == digest
        assert read_json(out / 'snapshots/initial/output.json') is None
        assert snapshots['initial']['database_sha256'] == snapshots['before-build']['database_sha256']
        assert snapshots['before-build']['source_inventory'] == snapshots['after-build']['source_inventory'] == files
        initial_inputs = snapshots['initial']['files']['inputs.json']
        assert all(s['files']['inputs.json'] == initial_inputs for s in snapshots.values())
        model_ids = helper.verify_full_run(read_json(out / 'snapshots/after-build/run_results.json'),
                                          read_json(out / 'snapshots/after-build/manifest.json'))
        assert len(model_ids) == 50
        report['all_models_run'] = model_ids
        report['sealed_rebuilt_output'] = ref(out / 'snapshots/after-build/output.json')
        write_json(out / 'build-seal.json', {'output': report['sealed_rebuilt_output'], 'source_files': len(files),
            'input_sha256': initial_inputs, 'agent_database_available_during_build': False,
            'all_models_run': model_ids, 'build_execution': ref(out / 'dbt-run-all.json')})
        await env.upload_file(source / 'workday.duckdb', '/tmp/captured-workday.duckdb')
        await execute('captured-db-readonly', 'chmod 444 /tmp/captured-workday.duckdb')
        await execute('captured-submission', collector + 'captured-submission --database /tmp/captured-workday.duckdb')
        await env.download_dir('/logs/agent/workday-rebuild/captured-submission', out / 'snapshots/captured-submission')
        captured = read_json(out / 'snapshots/captured-submission/snapshot.json')
        assert captured['database_sha256'] == file_hash(source / 'workday.duckdb')
        output = read_json(out / 'snapshots/after-build/output.json')
        original_output = read_json(out / 'snapshots/captured-submission/output.json')
        report['captured_input_matches_pristine'] = captured['files']['inputs.json'] == initial_inputs
        report['captured_output_reproduced'] = helper.exact_table_equal(output, original_output)
        inputs = read_json(out / 'snapshots/initial/inputs.json')
        expected, profile_links = independent.recompute({name: table['rows'] for name, table in inputs.items()})
        actual = { (r['source_relation'], r['job_profile_id']): r for r in output['rows'] }
        core = ['source_relation', 'job_profile_id', 'job_profile_code', 'job_title', 'private_title', 'job_summary', 'job_description']
        core_equal = len(actual) == len(output['rows']) == len(expected) and all(
            all(actual.get((row['source_relation'], row['job_profile_id']), {}).get(k) == row[k] for k in core)
            for row in expected)
        families = ['job_family_codes', 'job_family_summaries', 'job_family_group_codes', 'job_family_group_summaries']
        differences = [{'key': [row['source_relation'], row['job_profile_id']], 'column': name,
                        'documented_path_recomputation': row[name],
                        'submitted': actual.get((row['source_relation'], row['job_profile_id']), {}).get(name)}
                       for row in expected for name in families
                       if actual.get((row['source_relation'], row['job_profile_id']), {}).get(name) != row[name]]
        write_json(out / 'independent-observation.json', {'core_fields_equal': core_equal, 'core_columns': core,
            'expected_rows': expected, 'matched_profile_relationship_rows': profile_links, 'family_differences': differences,
            'semantic_certification': False, 'scope': 'Independent documented family-bridge recomputation. NULL versus empty string and alternate group relationships require contract adjudication; differences alone are not certified wrong-answer cases.'})
        await execute('verifier-context', 'cd /workspace && python3 /tmp/context_probe.py --db /workspace/workday.duckdb')
        verifier = Verifier(task=task, trial_paths=paths, environment=env, logger=logger)
        reward = await asyncio.wait_for(verifier.verify(), timeout=task.config.verifier.timeout_sec)
        report['reconstruction_native_result'] = reward.model_dump(mode='json')
        report['native_score_reproduced'] = reward.rewards == (raw.get('verifier_result') or {}).get('rewards')
        report['independent_observation'] = ref(out / 'independent-observation.json')
        report['passed'] = (built.return_code == 0 and report['captured_input_matches_pristine']
                            and report['captured_output_reproduced'] and report['native_score_reproduced'])
    except Exception as exc:
        report['error'] = {'type': type(exc).__name__, 'message': str(exc), 'traceback': traceback.format_exc()}
    finally:
        try:
            await env.stop(delete=True)
        except Exception as exc:
            report.update(passed=False, cleanup_error=str(exc))
        handler.flush()
        handler.close()
        logger.removeHandler(handler)
        report['runner'] = ref(out / 'runner.py')
        report['environment_log'] = ref(out / 'environment.log')
        report['collector_sources'] = [ref(out / name) for name in ['reconstruction.py', 'collect_reconstruction.py', 'review_build.py']]
        write_json(out / 'result.json', report)
    print(json.dumps({k: report.get(k) for k in ['passed', 'dbt_exit_code', 'captured_input_matches_pristine', 'captured_output_reproduced', 'native_score_reproduced', 'error']}))
    return 0 if report['passed'] else 1


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--trial', type=Path, required=True)
    parser.add_argument('--spec', type=Path, required=True)
    parser.add_argument('--output-dir', type=Path, required=True)
    raise SystemExit(asyncio.run(run(parser.parse_args())))
