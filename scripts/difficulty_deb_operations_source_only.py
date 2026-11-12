"""CPU control: deliver reviewed source bytes without building or writing a DB.

These are explicit oracle fixtures, never difficulty attempts. The unmodified
native verifier builds the source; the later audit independently reconstructs
exactly the captured submission. Neither execution is an original model action.
"""
import argparse
from copy import deepcopy
from pathlib import Path
import shlex
import shutil
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT / 'src'), str(ROOT / 'scripts')]
from dbt_bench import io
from dbt_bench.prepare import check_harbor
from difficulty_deb_conformance import command
from difficulty_deb_prepare_v3 import prepare as prepare_audit
from difficulty_deb_phase4_operations import TASKS


def prepare(run, output):
    run, output = Path(run), Path(output)
    if output.exists() or not output.resolve().is_relative_to('/scratch'):
        raise ValueError('Select a new SCRATCH control directory')
    old = io.read_json(run / 'manifest.json')
    identity = deepcopy(old['identity'])
    key = identity['task_key']
    if key.removeprefix('deb__') not in TASKS:
        raise ValueError('Outside reviewed operations scope')
    config = io.read_json(run / 'config.yaml')
    original_task = Path(config['tasks'][0]['path'])
    if (old['experiment_id'] != io.digest(identity) or identity['config'] != config
            or io.tree_hash(original_task) != identity['actual_task_hash']):
        raise ValueError('Prepared model source changed')
    production = identity.get('reviewed_production') or identity['phase3_production']
    entry = production[key]
    review_ref = entry.get('qualified_review') or entry['pool_row']['phase3_record']
    if not io.evidence_ok(review_ref, ROOT):
        raise ValueError('Qualification changed')
    review = io.read_json(ROOT / review_ref['path'])
    phase = review['phase4_identity']
    batch = io.read_json(ROOT / phase['batch_manifest']['path'])
    requests = io.read_json(ROOT / batch['requests']['path'])
    reviewed = next(r for r in requests['tasks'] if r['task_key'] == key)['rebuild']
    submission = reviewed['submission']
    if submission.get('delete_paths') or submission.get('reset_paths'):
        raise ValueError('This control only installs declared source files')
    output.mkdir(parents=True)
    basis = output / 'basis'
    task = basis / 'tasks' / key
    shutil.copytree(original_task, task)
    native_hash = io.tree_hash(task / 'tests')
    shutil.rmtree(task / 'solution')
    (task / 'solution/payload').mkdir(parents=True)
    shell = ['#!/bin/bash', 'set -euo pipefail', 'echo "$DB_TYPE"']
    shell += ['export ' + name + '=' + shlex.quote(value)
              for name, value in reviewed['environment'].items()]
    sources = []
    for i, item in enumerate(submission['files']):
        relative = Path(item['path'])
        if relative.is_absolute() or '..' in relative.parts:
            raise ValueError('Unsafe source destination')
        if not io.evidence_ok(item['source'], ROOT):
            raise ValueError('Reviewed source changed')
        destination = Path(reviewed['project_root']) / relative
        source = task / 'solution/payload' / str(i)
        shutil.copyfile(ROOT / item['source']['path'], source)
        shell += ['mkdir -p ' + shlex.quote(str(destination.parent)),
                  'cp ' + shlex.quote('/solution/payload/' + str(i)) + ' ' + shlex.quote(str(destination))]
        sources.append({'destination': str(destination), 'source': item['source']})
    (task / 'solution/solve.sh').write_text('\n'.join(shell) + '\n')
    if io.tree_hash(task / 'tests') != native_hash:
        raise ValueError('Native tests changed')
    config.update(tasks=[{'path': str(task)}], job_name='source-only', jobs_dir=str(output / 'jobs'))
    config = check_harbor(config, [task])
    identity.update(config=config, purpose='audit_conformance_only', fixture_only=True,
                    model_requests=0, actual_task_hash=io.tree_hash(task),
                    source_only_control={'sources': sources, 'dbt_invocations': 0,
                                         'database_payloads': 0, 'native_tests_unchanged': True,
                                         'original_run': io.evidence_ref(run / 'manifest.json', ROOT),
                                         'generator': io.evidence_ref(Path(__file__), ROOT)})
    io.write_json(basis / 'config.yaml', config)
    io.write_json(basis / 'manifest.json', {**old, 'identity': identity,
        'experiment_id': io.digest(identity), 'config_hash': io.file_hash(basis / 'config.yaml'),
        'fixture_only': True, 'model_requests': 0, 'training_prohibited': True,
        'planned': [{'task_key': key, 'fixture': 'source-only'}]})
    oracle = deepcopy(config)
    oracle.update(agents=[{'name': 'oracle'}], n_attempts=1, n_concurrent_trials=1)
    io.write_json(output / 'oracle-config.json', oracle)
    return basis, task


def execute(run, output):
    output = Path(output)
    basis, task = prepare(run, output)
    command([str(ROOT / 'runtime/.venv/bin/harbor'), 'run', '-c', str(output / 'oracle-config.json')], output, 'source-only-native')
    results = list((output / 'jobs/source-only').glob('*/result.json'))
    if len(results) != 1:
        raise ValueError('Expected one source-only fixture')
    result = io.read_json(results[0])
    capture = io.read_json(results[0].parent / 'artifacts/difficulty-submission/capture.json')
    outcome = {'task_key': io.read_json(basis / 'manifest.json')['identity']['task_key'],
        'fixture_only': True, 'model_requests': 0, 'training_prohibited': True,
        'source_only_payload': io.evidence_ref(task / 'solution/solve.sh', ROOT),
        'basis': io.evidence_ref(basis / 'manifest.json', ROOT),
        'original_result': io.evidence_ref(results[0], ROOT),
        'capture': io.evidence_ref(results[0].parent / 'artifacts/difficulty-submission/capture.json', ROOT),
        'pre_native_databases': capture['databases'], 'native_reward': result.get('verifier_result'),
        'exception': result.get('exception_info'), 'agent_dbt_invocations': 0}
    io.write_json(output / 'source-only-outcome.json', outcome)
    prepare_audit(basis, results[0].parent, output / 'rebuild', fixture=True)
    command([str(ROOT / 'runtime/.venv/bin/harbor'), 'run', '-c', str(output / 'rebuild/batch/config.yaml')], output, 'rebuild')
    rebuilt = list((output / 'rebuild/batch/jobs').glob('*/*/result.json'))
    if len(rebuilt) != 1:
        raise ValueError('Expected one exact-source rebuild')
    outcome.update(rebuild_result=io.evidence_ref(rebuilt[0], ROOT),
                   rebuild_native_reward=io.read_json(rebuilt[0]).get('verifier_result'))
    io.write_json(output / 'outcome.json', outcome)
    return outcome


if __name__ == '__main__':
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--run', type=Path, required=True)
    p.add_argument('--output', type=Path, required=True)
    a = p.parse_args()
    print(execute(a.run, a.output))
