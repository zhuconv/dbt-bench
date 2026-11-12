"""Bind one actual Spider submission to a clean reconstruction and qualification."""
from pathlib import Path
from dbt_bench import io
from difficulty_submission_v2 import verify_capture
from difficulty_spider_registry import ROOT, load, qualified_entry
from prepare_difficulty_audit_v2 import source_plan
from phase3_capture import Capture, timestamp


def fresh_build(batch, trial, root=ROOT):
    cap = Capture(trial, root)
    manifest = cap.read(batch / 'manifest.json')
    result = cap.read(trial / 'result.json')
    label = Path(result['config']['task']['path']).name
    entry = manifest['tasks'][label]
    task = batch / 'tasks' / label
    if (Path(result['config']['task']['path']).resolve() != task.resolve()
            or io.tree_hash(task) != entry['validation_copy_hash']):
        raise ValueError('Reconstruction task changed')
    for ref in [entry['native_verifier'], manifest['requests'], manifest['config'],
                *manifest['tools'], *entry['submission_source_refs']]:
        cap.checked(ref)
    request_path = cap.checked(entry['request'])
    request = cap.read(request_path)
    base = trial / 'agent/audit-rebuild'
    execution = cap.read(base / 'execution.json')
    if (execution.get('error') or execution['model_requests'] != 0
            or execution['task_key'] != entry['task_key']):
        raise ValueError('Reconstruction capture failed or belongs to another task')
    cap.checked({'path': str(request_path), 'sha256': execution['spec_sha256']})
    cap.checked({'path': str(request_path.with_name('audit_rebuild_driver.py')),
                 'sha256': execution['driver_sha256']})
    inventories = {k: cap.files(base / directory, execution[k]) for k, directory in
                   [('initial_files', 'initial-files'), ('submitted_files', 'submitted-files'),
                    ('final_files', 'final-files')]}
    if any(execution['environment'].get(k) != v for k, v in request['environment'].items()):
        raise ValueError('Rebuild environment differs from declared request')
    commands = []
    if not execution['runs'] or len(execution['runs']) > len(request['commands']):
        raise ValueError('Declared commands missing')
    for index, (run, argv) in enumerate(zip(execution['runs'], request['commands'])):
        if run['command'] != argv or run['cwd'] != request['project_root']:
            raise ValueError('Rebuild process differs')
        cap.checked({'path': str(base / run['log']), 'sha256': run['log_sha256']})
        files = cap.files(base / f'command-{index:02d}-files', run['files'])
        if run['returncode'] != 0:
            if index != len(execution['runs']) - 1 or execution.get('build_success'):
                raise ValueError('Contradictory failed build chronology')
            continue
        if not any(x in argv for x in ('run', 'build')):
            continue
        target = argv[argv.index('--target-path') + 1] if '--target-path' in argv else 'target'
        mp, rp = files[target + '/manifest.json'], files[target + '/run_results.json']
        graph, runs = cap.read(mp), cap.read(rp)
        if graph['metadata']['invocation_id'] != runs['metadata']['invocation_id']:
            raise ValueError('Fresh graph/results invocation mismatch')
        for artifact in (graph, runs):
            if not timestamp(run['started_at_utc']) - 1 <= timestamp(artifact['metadata']['generated_at']) <= timestamp(run['finished_at_utc']) + 1:
                raise ValueError('Candidate-generated dbt artifact reused')
        commands.append({'run': run, 'manifest': graph, 'run_results': runs,
                         'manifest_path': mp, 'results_path': rp, 'files': files})
    if not commands and execution.get('build_success'):
        raise ValueError('No actual clean materialization command')
    return {'capture': cap, 'entry': entry, 'request': request, 'execution': execution,
            'commands': commands, **inventories}


def verify(audit_dir, root=ROOT):
    audit_dir = Path(audit_dir)
    batch = audit_dir / 'batch'
    manifest = io.read_json(batch / 'manifest.json')
    if (manifest.get('kind') != 'difficulty_submission_reconstruction'
            or manifest.get('model_requests') != 0 or manifest.get('training_prohibited') is not True):
        raise ValueError('Expected isolated no-model reconstruction')
    binding = load(manifest['source_binding'], root)
    key, version = binding['task_key'], binding['task_version']
    prepared = load(binding['prepared_model_run'], root)
    identity = prepared['identity']
    run = (root / binding['prepared_model_run']['path']).parent
    config = io.read_json(run / 'config.yaml')
    if (prepared['experiment_id'] != io.digest(identity) or not prepared['training_prohibited']
            or identity['task_key'] != key or identity['task_version'] != version
            or identity['config'] != config or prepared['config_hash'] != io.file_hash(run / 'config.yaml')):
        raise ValueError('Prepared original run identity changed')
    production = identity.get('reviewed_production') or identity['phase3_production']
    pool = production[key]['pool_row']
    expected_review = production[key].get('qualified_review') or pool['phase3_record']
    if binding['qualified_review'] != expected_review or pool['task_version'] != version:
        raise ValueError('Wrong task qualification')
    qualified = qualified_entry(expected_review, key, version, root)
    original = load(binding['original_result'], root)
    original_trial = (root / binding['original_result']['path']).parent
    task = Path(identity['config']['tasks'][0]['path'])
    if (io.tree_hash(task) != identity['actual_task_hash']
            or io.tree_hash(task / 'tests') != identity['native_tests_hash']
            or io.file_hash(task / 'instruction.md') != qualified['record']['phase3_identity']['instruction_sha256']
            or Path(original['config']['task']['path']).resolve() != task.resolve()):
        raise ValueError('Actual task version or native verifier changed')
    fixture = binding.get('fixture_only')
    if type(fixture) is not bool or binding.get('actual_model_trial') is not (not fixture):
        raise ValueError('Fixture and measured attempt must be explicit')
    if not fixture:
        actual, expected = original['config']['agent'], config['agents'][0]
        if (actual.get('name') != 'qwen-coder' or actual.get('import_path')
                or any(actual.get(k) != expected.get(k) for k in ('name', 'model_name', 'base_url', 'kwargs'))
                or original_trial.parent.resolve() != (run / 'jobs' / run.name).resolve()):
            raise ValueError('Not the planned actual Qwen attempt')
    capture, archive, originals = verify_capture(original_trial, key)
    if binding['extracted']['capture'] != io.evidence_ref(original_trial / 'artifacts/difficulty-submission/capture.json', root):
        raise ValueError('Different actual submission capture')
    extracted = binding['extracted']
    extracted_index = io.unique_index(extracted['source_files'], 'path')
    if set(extracted_index) != set(originals):
        raise ValueError('Extracted source inventory differs')
    for name, row in extracted_index.items():
        if row['sha256'] != originals[name]['sha256'] or not io.evidence_ok(row['captured_source'], root):
            raise ValueError('Substituted model source: ' + name)
    cfg = load(manifest['config'], root)
    job = Path(cfg['jobs_dir']) / cfg['job_name']
    results = list(job.glob('*/result.json'))
    if cfg['n_attempts'] != 1 or len(results) != 1 or not io.read_json(job / 'result.json').get('finished_at'):
        raise ValueError('Expected one finished no-model reconstruction')
    result = io.read_json(results[0])
    if result.get('exception_info') or not result.get('finished_at'):
        raise ValueError('Reconstruction has an actual exception')
    fresh = fresh_build(batch, results[0].parent, root)
    baseline = load(qualified['record']['reproduction']['trials'][0]['execution'], root)
    reviewed = qualified['record']['phase3_identity']
    reviewed_batch = load(reviewed['batch_manifest'], root)
    reviewed_requests = load(reviewed_batch['requests'], root)
    request = next(x for x in reviewed_requests['tasks'] if x['task_key'] == key)
    submission, names, plan = source_plan(extracted, baseline, request['rebuild'])
    if binding['plan'] != plan or fresh['execution']['initial_input_hashes'] != baseline['initial_input_hashes']:
        raise ValueError('Source plan or initial inputs differ')
    for field in ('submitted_files', 'final_files'):
        for name in names:
            if name not in fresh[field] or io.file_hash(fresh[field][name]) != originals[name]['sha256']:
                raise ValueError('Reconstruction changed delivered source: ' + name)
    if any(name in fresh['final_files'] for name in submission['delete_paths']):
        raise ValueError('Reconstruction restored a model-deleted file')
    return {'batch': batch, 'trial': results[0].parent, 'binding': binding, 'manifest': manifest,
            'qualified': qualified, 'original': original, 'original_trial': original_trial,
            'original_capture': capture, 'fresh': fresh, 'production': production,
            'baseline': baseline, 'request': request, 'prepared': prepared,
            'rebuild_result': io.evidence_ref(results[0], root)}
