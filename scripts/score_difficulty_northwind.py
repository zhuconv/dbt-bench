#!/usr/bin/env python3
"""Apply the qualified Northwind checker to a captured model-source rebuild.

The fresh build supplies trusted dbt artifacts. Its commands are never evidence
that the model itself ran those commands. This adapter is limited to the four
Northwind contracts that accept delivered dbt source.
"""
import argparse
from datetime import datetime
import importlib.util
from pathlib import Path
import sys

import yaml

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT / 'src'), str(ROOT / 'scripts')]
from dbt_bench import io
from difficulty_submission import verify_capture
from prepare_difficulty_audit import source_plan

KEYS = {'ade__ade-bench-ana-eng' + x for x in ('003', '004', '005', '006')}


def load(ref):
    if not io.evidence_ok(ref, ROOT):
        raise ValueError('Evidence changed: ' + ref['path'])
    return io.read_json(ROOT / ref['path'])


def ref(path):
    return io.evidence_ref(path, ROOT)


def save(path, value):
    if path.exists():
        raise ValueError('Refuse to overwrite audit evidence: ' + str(path))
    io.write_json(path, value)
    return ref(path)


def inventory_map(execution, field):
    return io.unique_index(execution[field], 'relative_path')


def assert_delivered_sources(execution, source_names, extracted):
    """All reconstructed source bytes must be the actual delivered bytes."""
    captured = io.unique_index(extracted['source_files'], 'path')
    submitted = inventory_map(execution, 'submitted_files')
    final = inventory_map(execution, 'final_files')
    for name in source_names:
        expected = captured[name]['sha256']
        for rows in (submitted, final):
            row = rows.get(name)
            if not row or not row.get('exists') or row.get('error') or row['sha256'] != expected:
                raise ValueError('Clean build did not preserve delivered source: ' + name)


def score(audit_dir, output):
    audit_dir, output = Path(audit_dir), Path(output)
    if output.exists():
        raise ValueError('Use a new output directory')
    batch = audit_dir / 'batch'
    manifest = io.read_json(batch / 'manifest.json')
    if (manifest['kind'] != 'difficulty_submission_reconstruction'
            or manifest['model_requests'] != 0 or not manifest['training_prohibited']):
        raise ValueError('Not a difficulty-only clean reconstruction')
    binding = load(manifest['source_binding'])
    key = binding['task_key']
    if key not in KEYS:
        raise ValueError('This adapter only covers qualified Northwind contracts')
    original_trial = (ROOT / binding['original_result']['path']).parent
    original = load(binding['original_result'])
    original_capture, _, _ = verify_capture(original_trial, key)
    prepared = load(binding['prepared_model_run'])
    if prepared['identity']['task_key'] != key or prepared['identity']['task_version'] != binding['task_version']:
        raise ValueError('Task version mismatch')
    qualified = load(binding['qualified_review'])
    if not qualified['eligible_for_sampling'] or qualified['audit']['conclusion'] != 'validated':
        raise ValueError('Checker is not qualified')
    overlay = load(qualified['audit']['overlay'])
    for code in overlay['code_refs']:
        if not io.evidence_ok(code, ROOT):
            raise ValueError('Qualified checker changed')
    config = load(manifest['config'])
    job = Path(config['jobs_dir']) / config['job_name']
    job_result = io.read_json(job / 'result.json')
    trial_results = sorted(job.glob('*/result.json'))
    if len(trial_results) != 1 or not job_result.get('finished_at') or config['n_attempts'] != 1:
        raise ValueError('Expected exactly one complete clean reconstruction')
    result_path = trial_results[0]
    result = io.read_json(result_path)
    trial = result_path.parent
    if not result.get('finished_at') or result.get('exception_info'):
        raise ValueError('Reconstruction failed; retain unknown until its cause is reviewed')
    task = Path(result['config']['task']['path'])
    if task.resolve() != Path(config['tasks'][0]['path']).resolve():
        raise ValueError('Unexpected reconstruction task')
    entry = manifest['tasks'][task.name]
    request = load(entry['request'])
    if io.tree_hash(task) != entry['validation_copy_hash'] or not io.evidence_ok(entry['native_verifier'], ROOT):
        raise ValueError('Reconstruction task or native verifier changed')
    execution_path = trial / 'agent/audit-rebuild/execution.json'
    ex = io.read_json(execution_path)
    base = execution_path.parent
    if (ex['task_key'] != key or ex['model_requests'] != 0
            or ex['spec_sha256'] != entry['request']['sha256']
            or ex['driver_sha256'] != io.file_hash(task / 'audit-rebuild/audit_rebuild_driver.py')):
        raise ValueError('Executed reconstruction identity mismatch')
    no_model = io.read_json(trial / 'agent/no-model.json')
    if no_model['model_requests'] != 0 or no_model['solution_uploaded'] is not False:
        raise ValueError('Unexpected solution/model access')
    if not ex['build_success'] or [x['command'] for x in ex['runs']] != request['commands']:
        raise ValueError('Declared clean build did not complete')
    if any(x['returncode'] != 0 for x in ex['runs']):
        raise ValueError('Clean build has failed commands')
    baseline = load(qualified['reproduction']['trials'][0]['execution'])
    original_requests = load(io.read_json(ROOT / qualified['phase3_identity']['batch_manifest']['path'])['requests'])
    base_request = next(x for x in original_requests['tasks'] if x['task_key'] == key)
    submission, names, plan = source_plan(binding['extracted'], baseline, base_request['rebuild'])
    if plan != binding['plan']:
        raise ValueError('Reconstructed source plan changed')
    assert_delivered_sources(ex, names, binding['extracted'])
    final_rows = inventory_map(ex, 'final_files')
    if any(final_rows.get(n, {}).get('exists') for n in submission['delete_paths']):
        raise ValueError('Deleted source was restored by reconstruction')

    def captured(name):
        row = final_rows[name]
        path = io.contained(base / 'final-files', row['file'])
        if not row['exists'] or row.get('error') or io.file_hash(path) != row['sha256']:
            raise ValueError('Fresh artifact changed: ' + name)
        return path

    here = ROOT / 'validation/ade' / key.removeprefix('ade__') / 'phase3_v1'
    policy = io.read_json(here / 'policy-v3.json')
    if ex['initial_input_hashes'] != [{'path': '/app/analytics_engineering.duckdb',
                                      'sha256': policy['initial_database_sha256']}]:
        raise ValueError('Reconstruction uses different initial inputs')

    def hook(phase, filename):
        hooks = ex[phase + '_capture_hooks']
        if len(hooks) != 1 or hooks[0]['returncode'] != 0:
            raise ValueError('Independent capture hook failed')
        command = hooks[0]['command']
        return base / Path(command[command.index('--output') + 1]).name / filename

    inputs = hook('initial', 'inputs.json')
    actual = io.read_json(inputs)
    if actual['relations'] != load(policy['inputs'])['relations'] or not actual['unchanged']:
        raise ValueError('Actual initial relations differ from qualified inputs')
    snapshot = trial / 'agent/audit-submission/snapshot.json'
    project_file = captured('dbt_project.yml')
    project = yaml.safe_load(project_file.read_text())
    model_paths = project.get('model-paths', ['models'])
    model_tree = {str(Path(t['relative_path']) / f['path']): f['sha256']
                  for t in ex['final_trees'] if t['relative_path'] in model_paths
                  for f in t['files'] if f['path'].endswith('.sql')}
    sources = [{'original_file_path': n, 'file': ref(captured(n))}
               for n in names if n.endswith('.sql')
               and any(Path(n).is_relative_to(Path(p)) for p in model_paths)]
    if {r['original_file_path']: r['file']['sha256'] for r in sources} != model_tree:
        raise ValueError('Configured model inventory is incomplete')
    run = ex['runs'][-1]
    log = base / run['log']
    if io.file_hash(log) != run['log_sha256']:
        raise ValueError('Build log changed')
    output.mkdir(parents=True)
    project_ref = save(output / 'project.json', {'model_paths': model_paths, 'source': ref(project_file)})
    workflow = {'producer': 'locked_qwen_clean_rebuild', 'discarded_candidate_dbt_artifacts': True,
        'task_key': key, 'snapshot': ref(snapshot), 'project': project_ref,
        'complete_model_inventory': True, 'sources': sources,
        'manifest': ref(captured('phase3_target/manifest.json')),
        'run_results': ref(captured('phase3_target/run_results.json')),
        'invocation': {'argv': run['command'], 'returncode': run['returncode'],
            'started_unix': datetime.fromisoformat(run['started_at_utc']).timestamp(),
            'finished_unix': datetime.fromisoformat(run['finished_at_utc']).timestamp(), 'log': ref(log)},
        'trust_evidence': [ref(batch / 'manifest.json'), manifest['source_binding'], ref(execution_path)],
        'scope': 'Fresh reconstruction of actual delivered source, not proof of model-issued commands.'}
    workflow_ref = save(output / 'workflow.json', workflow)
    checker_request = {'task_key': key, 'snapshot': ref(snapshot), 'stage': 'agent-submission',
        'workflow': workflow_ref, 'actual_initial_capture': ref(inputs),
        'initial_database_sha256': policy['initial_database_sha256'],
        'clean_build_output': ref(hook('final', 'snapshot.json'))}
    request_ref = save(output / 'request.json', checker_request)
    checker = here / 'check_v3.py'
    if ref(checker) not in overlay['code_refs']:
        # References may be absolute or repository-relative, so compare paths.
        if not any((ROOT / x['path']).resolve() == checker.resolve() and x['sha256'] == io.file_hash(checker)
                   for x in overlay['code_refs']):
            raise ValueError('Scoring entrypoint is outside the qualified checker')
    spec = importlib.util.spec_from_file_location('difficulty_northwind_checker', checker)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    audit_result = module.run(checker_request, ROOT)
    result_ref = save(output / 'audit-result.json', audit_result)
    receipt = {'schema_version': 1, 'task_key': key, 'task_version': binding['task_version'],
        'original_result': binding['original_result'], 'original_native_rewards':
            (original.get('verifier_result') or {}).get('rewards'),
        'original_capture': binding['extracted']['capture'], 'source_binding': manifest['source_binding'],
        'rebuild_result': ref(result_path), 'qualified_review': binding['qualified_review'],
        'checker_version': qualified['audit']['checker_version'], 'checker': ref(checker),
        'checker_request': request_ref, 'audit_result': result_ref,
        'audit_reward': audit_result['reward'], 'audit_status': audit_result['status'],
        'fixture_only': binding['fixture_only'], 'model_requests': 0, 'training_prohibited': True,
        'adapter': ref(Path(__file__)),
        'scope': 'Contract-scoped clean source reconstruction. Original native reward is unchanged.'}
    save(output / 'receipt.json', receipt)
    return receipt


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--audit-dir', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    answer = score(args.audit_dir, args.output)
    print({key: answer[key] for key in ('task_key', 'audit_reward', 'audit_status', 'fixture_only')})
