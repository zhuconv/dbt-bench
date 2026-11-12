"""Real no-model conformance with the frozen legal customer source at its primary path.

A new fixture-only task copy replaces only its hidden oracle solution. Neither
production tasks nor a model run is changed, and no fixture can be measured.
"""
from dbt_bench.local_config import expand as _release_expand
from pathlib import Path
from copy import deepcopy
import shlex, shutil, sys
from dbt_bench import io
from prepare_difficulty_run_v2 import prepare as prepare_basis
from difficulty_deb_prepare_v4 import prepare as prepare_audit, plan_builds
from difficulty_deb_conformance import command
ROOT = Path.cwd()
D = Path(_release_expand('${ARTIFACT_ROOT}/difficulty/qwen36-v1'))

def run(output, task):
    output = Path(output)
    output.mkdir(parents=True, exist_ok=False)
    key = 'deb__' + task
    basis = output / 'basis'
    prepare_basis(basis, 'deb', key, [1], D / 'population-v1.json')
    m = io.read_json(basis / 'manifest.json')
    production = m['identity']['reviewed_production'][key]
    qualified = production['qualified_review']
    review = io.read_json(ROOT / qualified['path'])
    batch = io.read_json(ROOT / review['phase4_identity']['batch_manifest']['path'])
    entry = batch['tasks'][review['phase4_identity']['label']]
    request_path = ROOT / entry['request']['path']
    request = io.read_json(request_path)
    if request['project_root'] != '/app/dbt_project':
        raise ValueError('Fixture must exercise the approved primary project')
    task = Path(m['identity']['config']['tasks'][0]['path'])
    solution = task / 'solution'
    old = output / 'original-solve.sh'
    shutil.copyfile(solution / 'solve.sh', old)
    payload = solution / 'qualified-fixture'
    payload.mkdir()
    lines = ['#!/bin/bash', 'set -euo pipefail']
    lines += ['export ' + k + '=' + shlex.quote(v) for k, v in sorted(request['environment'].items())]
    lines.append('mkdir -p ' + shlex.quote(request['project_root']))
    source_refs = []
    for index, row in enumerate(request['submission']['files']):
        source = request_path.parent / row['payload_path']
        if io.file_hash(source) != row['sha256']:
            raise ValueError('Frozen blind source changed')
        dest = payload / str(index)
        shutil.copyfile(source, dest)
        runtime = Path(request['project_root']) / row['path']
        lines.extend(['mkdir -p ' + shlex.quote(str(runtime.parent)), 'cp ' + shlex.quote('/solution/qualified-fixture/' + str(index)) + ' ' + shlex.quote(str(runtime))])
        source_refs.append(io.evidence_ref(source, ROOT))
    commands = deepcopy(request)
    plan_builds(commands, key)
    lines.append('cd ' + shlex.quote(request['project_root']))
    lines += [shlex.join(c) for c in commands['commands']]
    (solution / 'solve.sh').write_text('\n'.join(lines) + '\n')
    m['identity'].update(purpose='audit_conformance_only', actual_task_hash=io.tree_hash(task), fixture_source={'qualified_review': qualified, 'original_request': entry['request'], 'source_refs': source_refs, 'original_native_solution': io.evidence_ref(old, ROOT), 'scope': 'Hidden oracle only in this isolated fixture copy; no model request, no training, no production edit.'})
    m.update(experiment_id=io.digest(m['identity']), fixture_only=True)
    io.write_json(basis / 'manifest.json', m)
    config = deepcopy(m['identity']['config'])
    config.update(agents=[{'name': 'oracle'}], n_attempts=1, n_concurrent_trials=1, job_name='oracle', jobs_dir=str(output / 'oracle/jobs'))
    io.write_json(output / 'oracle-config.json', config)
    command([str(ROOT / 'runtime/.venv/bin/harbor'), 'run', '-c', str(output / 'oracle-config.json')], output, 'oracle')
    trial = next((output / 'oracle/jobs/oracle').glob('*/result.json')).parent
    prepare_audit(basis, trial, output / 'rebuild', fixture=True)
    command([str(ROOT / 'runtime/.venv/bin/harbor'), 'run', '-c', str(output / 'rebuild/batch/config.yaml')], output, 'rebuild')
    command([sys.executable, str(ROOT / 'scripts/difficulty_deb_score_v10.py'), '--audit-dir', str(output / 'rebuild'), '--output', str(output / 'scoring')], output, 'score')
    receipt = io.read_json(output / 'scoring/receipt.json')
    print({k: receipt[k] for k in ('task_key', 'audit_reward', 'audit_status', 'fixture_only')})
if __name__ == '__main__':
    import argparse
    p = argparse.ArgumentParser()
    p.add_argument('--output', type=Path, required=True)
    p.add_argument('--task', choices=['dbt-rfm-customer-segmentation', 'dbt-loyalty-points-analysis'], required=True)
    a = p.parse_args()
    run(a.output, a.task)
