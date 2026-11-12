"""CPU conformance using frozen blind-build source as an explicit fixture.

Only the new fixture oracle payload changes. Neither production nor any native
test is edited. This control never counts as a model difficulty attempt.
"""
from dbt_bench.local_config import expand as _release_expand
from copy import deepcopy
from pathlib import Path
import argparse
import shlex
import shutil
import sys
ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT / 'src'), str(ROOT / 'scripts')]
from dbt_bench import io
from prepare_difficulty_run_v2 import prepare as prepare_basis
from difficulty_deb_prepare_v3 import prepare as prepare_audit
from difficulty_deb_conformance_v2 import command

def run(output):
    output = Path(output)
    if output.exists() or not output.resolve().is_relative_to('/scratch'):
        raise ValueError('Fresh SCRATCH fixture directory required')
    output.mkdir(parents=True)
    key = 'deb__dbt-customer-churn-early-warning'
    basis = output / 'basis'
    population = Path(_release_expand('${ARTIFACT_ROOT}/difficulty/qwen36-v1/population-v1.json'))
    prepare_basis(basis, 'deb', key, [1], population)
    manifest = io.read_json(basis / 'manifest.json')
    identity = manifest['identity']
    production = identity['reviewed_production'][key]
    review_ref = production['qualified_review']
    review = io.read_json(ROOT / review_ref['path'])
    batch = io.read_json(ROOT / review['phase4_identity']['batch_manifest']['path'])
    requests = io.read_json(ROOT / batch['requests']['path'])
    rebuild = next((x['rebuild'] for x in requests['tasks'] if x['task_key'] == key))
    submission = rebuild['submission']
    if submission['delete_paths'] or submission['reset_paths']:
        raise ValueError('This fixture only supports the reviewed additive source payload')
    task = Path(identity['config']['tasks'][0]['path'])
    tests_hash = io.tree_hash(task / 'tests')
    shutil.rmtree(task / 'solution')
    (task / 'solution').mkdir()
    commands = ['#!/bin/bash', 'set -euo pipefail', 'echo "$DB_TYPE"']
    source_refs = []
    for row in submission['files']:
        if not io.evidence_ok(row['source'], ROOT):
            raise ValueError('Frozen fixture source changed')
        relative = Path(row['path'])
        if relative.is_absolute() or '..' in relative.parts or relative.suffix not in ('.sql', '.yml', '.yaml'):
            raise ValueError('Only bounded reviewed source files may be fixture payload')
        dest = task / 'solution/source' / relative
        dest.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(ROOT / row['source']['path'], dest)
        target = Path(rebuild['project_root']) / relative
        commands.extend([shlex.join(['mkdir', '-p', str(target.parent)]), shlex.join(['cp', str(Path('/solution/source') / relative), str(target)])])
        source_refs.append(row['source'])
    for key_env, value in rebuild['environment'].items():
        commands.append('export ' + shlex.quote(key_env + '=' + value))
    commands.append(shlex.join(['cd', rebuild['project_root']]))
    commands.extend((shlex.join(argv) for argv in rebuild['commands']))
    (task / 'solution/solve.sh').write_text('\n'.join(commands) + '\n')
    if io.tree_hash(task / 'tests') != tests_hash:
        raise ValueError('Fixture changed native tests')
    identity.update(purpose='audit_conformance_only', actual_task_hash=io.tree_hash(task), blind_source_control={'sources': source_refs, 'qualified_review': review_ref, 'commands': rebuild['commands'], 'database_payloads': 0, 'native_tests_unchanged': True, 'generator': io.evidence_ref(Path(__file__), ROOT)})
    manifest.update(experiment_id=io.digest(identity), fixture_only=True)
    io.write_json(basis / 'manifest.json', manifest)
    oracle = output / 'oracle'
    oracle.mkdir()
    config = deepcopy(identity['config'])
    config.update(agents=[{'name': 'oracle'}], n_attempts=1, n_concurrent_trials=1, job_name='blind-source', jobs_dir=str(oracle / 'jobs'))
    io.write_json(oracle / 'config.json', config)
    command([str(ROOT / 'runtime/.venv/bin/harbor'), 'run', '-c', str(oracle / 'config.json')], output, 'oracle')
    results = list((oracle / 'jobs/blind-source').glob('*/result.json'))
    if len(results) != 1:
        raise ValueError('Expected one real fixture trial')
    prepare_audit(basis, results[0].parent, output / 'rebuild', fixture=True)
    command([str(ROOT / 'runtime/.venv/bin/harbor'), 'run', '-c', str(output / 'rebuild/batch/config.yaml')], output, 'rebuild')
    command([sys.executable, str(ROOT / 'scripts/difficulty_deb_score_v8.py'), '--audit-dir', str(output / 'rebuild'), '--output', str(output / 'scoring')], output, 'score')
    receipt = io.read_json(output / 'scoring/receipt.json')
    outcome = {'task_key': key, 'fixture_only': True, 'model_requests': 0, 'training_prohibited': True, 'original_result': io.evidence_ref(results[0], ROOT), 'receipt': io.evidence_ref(output / 'scoring/receipt.json', ROOT), 'native_reward': io.read_json(results[0]).get('verifier_result'), 'audit_reward': receipt['audit_reward'], 'audit_status': receipt['audit_status'], 'scope': 'Explicit frozen blind-source CPU control; original Qwen action absence remains unknown.'}
    io.write_json(output / 'outcome.json', outcome)
    return outcome
if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--output', type=Path, required=True)
    print(run(parser.parse_args().output))
