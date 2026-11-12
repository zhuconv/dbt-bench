"""Real CPU-only oracle/capture/rebuild/checker conformance for frozen Spider tasks.

Every basis is explicitly audit_conformance_only before execution. These runs
are never model attempts, even when the oracle receives native/audit reward 1.
Harbor runs each actual trial; this script only prepares and reconciles the
independent adapter conformance stages.
"""
import argparse
from concurrent.futures import ThreadPoolExecutor, as_completed
from copy import deepcopy
import os
from pathlib import Path
import subprocess
import sys
import traceback

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT / 'src'), str(ROOT / 'scripts')]
from dbt_bench import io
from difficulty_spider_registry import ENTRIES


def command(argv, output, name):
    env = os.environ | {'PATH': '/opt/apps/tacc-apptainer/1.4.1/bin:' + os.environ['PATH'],
                        'PYTHONDONTWRITEBYTECODE': '1', 'PYTHONPATH': str(ROOT / 'src') + ':' + str(ROOT / 'scripts')}
    with (output / (name + '.log')).open('x') as log:
        p = subprocess.run(argv, cwd=ROOT, env=env, stdout=log, stderr=subprocess.STDOUT, timeout=2400)
    io.write_json(output / (name + '-process.json'), {'argv': argv, 'returncode': p.returncode,
        'log': io.evidence_ref(output / (name + '.log'), ROOT), 'fixture_only': True, 'model_requests': 0})
    if p.returncode:
        raise ValueError(name + ' process failed with code ' + str(p.returncode))


def one(task, output, actor='oracle'):
    from prepare_difficulty_run import prepare as prepare_basis
    from prepare_difficulty_audit_v2 import prepare as prepare_audit
    output = Path(output)
    if output.exists() or not output.resolve().is_relative_to('/scratch'):
        raise ValueError('Select a fresh SCRATCH conformance task directory')
    output.mkdir(parents=True)
    key = 'spider2__' + task
    status = {'task_key': key, 'fixture_only': True, 'model_requests': 0, 'training_prohibited': True,
              'executed_stages': [], 'native_reward': None, 'rebuild_native_reward': None,
              'audit_reward': None, 'audit_status': 'not_executed'}
    try:
        if task not in ENTRIES:
            raise ValueError('Not a registered frozen Spider task')
        basis = output / 'basis'
        prepare_basis(basis, 'spider2', key, [1])
        m = io.read_json(basis / 'manifest.json')
        m['identity']['purpose'] = 'audit_conformance_only'
        m['experiment_id'] = io.digest(m['identity'])
        m['fixture_only'] = True
        io.write_json(basis / 'manifest.json', m)
        status['basis'] = io.evidence_ref(basis / 'manifest.json', ROOT)
        status['executed_stages'].append('basis_prepared_and_marked_fixture')
        oracle = output / 'oracle'; oracle.mkdir()
        config = deepcopy(m['identity']['config'])
        config.update(agents=[{'name': actor}], n_attempts=1, n_concurrent_trials=1,
                      job_name='oracle', jobs_dir=str(oracle / 'jobs'))
        io.write_json(oracle / 'config.json', config)
        command([str(ROOT / 'runtime/.venv/bin/harbor'), 'run', '-c', str(oracle / 'config.json')], output, 'oracle')
        paths = list((oracle / 'jobs/oracle').glob('*/result.json'))
        if len(paths) != 1:
            raise ValueError('Expected one terminal oracle fixture')
        raw = io.read_json(paths[0]); status['original_result'] = io.evidence_ref(paths[0], ROOT)
        status['native_reward'] = (raw.get('verifier_result') or {}).get('rewards')
        status['original_exception'] = raw.get('exception_info')
        status['executed_stages'].append('oracle_and_native_capture')
        audit = output / 'rebuild'
        prepare_audit(basis, paths[0].parent, audit, fixture=True)
        status['executed_stages'].append('reconstruction_prepared')
        command([str(ROOT / 'runtime/.venv/bin/harbor'), 'run', '-c', str(audit / 'batch/config.yaml')], output, 'rebuild')
        rebuilt = list((audit / 'batch/jobs').glob('*/*/result.json'))
        if len(rebuilt) != 1:
            raise ValueError('Expected one terminal reconstruction')
        result = io.read_json(rebuilt[0]); status['rebuild_result'] = io.evidence_ref(rebuilt[0], ROOT)
        status['rebuild_native_reward'] = (result.get('verifier_result') or {}).get('rewards')
        status['rebuild_exception'] = result.get('exception_info')
        status['executed_stages'].append('locked_qwen_clean_reconstruction')
        command([sys.executable, str(ROOT / 'scripts/difficulty_spider_score.py'), '--audit-dir', str(audit),
                 '--output', str(output / 'scoring')], output, 'score')
        receipt = io.read_json(output / 'scoring/receipt.json')
        if receipt['fixture_only'] is not True:
            raise ValueError('Conformance receipt lost fixture marker')
        status.update(audit_reward=receipt['audit_reward'], audit_status=receipt['audit_status'],
                      receipt=io.evidence_ref(output / 'scoring/receipt.json', ROOT))
        status['executed_stages'].append('actual_submission_projection_and_frozen_checker')
        status['pipeline_complete'] = True
    except Exception as exc:
        status.update(pipeline_complete=False, error={'type': type(exc).__name__, 'message': str(exc)})
        (output / 'failure.txt').write_text(traceback.format_exc())
    status['runner'] = io.evidence_ref(Path(__file__), ROOT)
    io.write_json(output / 'outcome.json', status)
    return status


def batch(output, tasks, concurrent):
    output = Path(output)
    if output.exists() or not output.resolve().is_relative_to('/scratch') or concurrent not in (1, 2, 3):
        raise ValueError('Fresh SCRATCH directory and concurrency1..3 required')
    output.mkdir(parents=True); (output / 'tasks').mkdir()
    if len(tasks) != len(set(tasks)) or any(t not in ENTRIES for t in tasks):
        raise ValueError('Duplicate/unknown task')
    io.write_json(output / 'plan.json', {'tasks': tasks, 'concurrency': concurrent,
        'fixture_only': True, 'model_requests': 0, 'training_prohibited': True,
        'runner': io.evidence_ref(Path(__file__), ROOT)})
    def run(task):
        argv = [sys.executable, str(Path(__file__)), '--one', '--task', task,
                '--output', str(output / 'tasks' / task)]
        command(argv, output, task)
        return io.read_json(output / 'tasks' / task / 'outcome.json')
    records = []
    with ThreadPoolExecutor(max_workers=concurrent) as executor:
        jobs = {executor.submit(run, task): task for task in tasks}
        for future in as_completed(jobs):
            try:
                record = future.result()
            except Exception as exc:
                record = {'task_key': 'spider2__' + jobs[future], 'pipeline_complete': False,
                          'error': str(exc), 'fixture_only': True, 'model_requests': 0}
            records.append(record)
            print(record['task_key'], record.get('native_reward'), record.get('audit_reward'),
                  record.get('error'), flush=True)
    io.write_json(output / 'summary.json', {'records': records, 'expected_tasks': tasks,
        'completed_pipelines': sum(r['pipeline_complete'] for r in records),
        'fixture_only': True, 'model_requests': 0, 'training_prohibited': True})


if __name__ == '__main__':
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--actor', choices=['oracle','nop'], default='oracle'); p.add_argument('--one', action='store_true'); p.add_argument('--task', action='append', required=True)
    p.add_argument('--output', type=Path, required=True); p.add_argument('--concurrent', type=int, default=3)
    a = p.parse_args()
    if a.one:
        if len(a.task) != 1: raise ValueError('One task required')
        print(one(a.task[0], a.output, a.actor))
    else:
        batch(a.output, a.task, a.concurrent)
