"""Verify model submission, clean reconstruction and frozen DEB identity."""
from pathlib import Path

from dbt_bench import io
from difficulty_submission import verify_capture
from difficulty_deb_registry import ROOT, load, qualified_entry
from prepare_difficulty_audit import source_plan
from phase3_capture import verify_trial


def assert_source_inventory(extracted, original_files, names, execution, root=ROOT):
    extracted_files = io.unique_index(extracted['source_files'], 'path')
    if set(extracted_files) != set(original_files):
        raise ValueError('Extracted source inventory differs from original capture')
    for name, item in extracted_files.items():
        if (item['sha256'] != original_files[name]['sha256']
                or not io.evidence_ok(item['captured_source'], root)):
            raise ValueError('Substituted extracted source: ' + name)
    for field in ('submitted_files', 'final_files'):
        inventory = io.unique_index(execution[field], 'relative_path')
        for name in names:
            item = inventory.get(name, {})
            if (not item.get('exists') or item.get('error')
                    or item['sha256'] != extracted_files[name]['sha256']):
                raise ValueError('Rebuild changed delivered source: ' + name)


def verify(audit_dir, root=ROOT):
    audit_dir = Path(audit_dir)
    batch = audit_dir / 'batch'
    manifest = io.read_json(batch / 'manifest.json')
    if (manifest.get('kind') != 'difficulty_submission_reconstruction'
            or manifest.get('model_requests') != 0 or manifest.get('training_prohibited') is not True):
        raise ValueError('Expected isolated no-model difficulty reconstruction')
    binding = load(manifest['source_binding'], root)
    key, version = binding['task_key'], binding['task_version']
    prepared = load(binding['prepared_model_run'], root)
    identity = prepared['identity']
    if (identity['task_key'] != key or identity['task_version'] != version
            or prepared['experiment_id'] != io.digest(identity)
            or not prepared['training_prohibited']):
        raise ValueError('Prepared original run identity changed')
    original_config = io.read_json((root / binding['prepared_model_run']['path']).parent / 'config.yaml')
    if original_config != identity['config']:
        raise ValueError('Prepared original configuration changed')
    pool = identity['phase3_production'][key]['pool_row']
    if binding['qualified_review'] != pool['phase3_record'] or pool['task_version'] != version:
        raise ValueError('Qualification does not match the actual planned run')
    qualified = qualified_entry(binding['qualified_review'], key, version, root)
    original = load(binding['original_result'], root)
    original_trial = (root / binding['original_result']['path']).parent
    planned_path = Path(identity['config']['tasks'][0]['path']).resolve()
    if Path(original['config']['task']['path']).resolve() != planned_path:
        raise ValueError('Original trial used a different task version')
    fixture = binding.get('fixture_only')
    if type(fixture) is not bool or binding.get('actual_model_trial') is not (not fixture):
        raise ValueError('Fixture/model status is not explicit')
    if not fixture:
        expected = original_config['agents'][0]
        actual = original['config']['agent']
        if (actual.get('name') != 'qwen-coder' or actual.get('import_path')
                or any(actual.get(k) != expected.get(k) for k in ('name', 'model_name', 'base_url', 'kwargs'))):
            raise ValueError('Original trial is not the planned Qwen model attempt')
        run = (root / binding['prepared_model_run']['path']).parent
        if original_trial.parent.resolve() != (run / 'jobs' / run.name).resolve():
            raise ValueError('Model trial is outside its planned job')
    capture, archive, original_files = verify_capture(original_trial, key)
    if binding['extracted']['capture'] != io.evidence_ref(original_trial / 'artifacts/difficulty-submission/capture.json', root):
        raise ValueError('Binding references another submission capture')
    config = load(manifest['config'], root)
    job = Path(config['jobs_dir']) / config['job_name']
    results = list(job.glob('*/result.json'))
    if (config['n_attempts'] != 1 or len(results) != 1
            or not io.read_json(job / 'result.json').get('finished_at')):
        raise ValueError('Expected one finished clean reconstruction')
    result = io.read_json(results[0])
    if not result.get('finished_at') or result.get('exception_info'):
        raise ValueError('Clean reconstruction failed; inspect separately')
    trial = results[0].parent
    if Path(result['config']['task']['path']).resolve() != Path(config['tasks'][0]['path']).resolve():
        raise ValueError('Reconstruction task differs')
    d = verify_trial(batch, trial, root)
    if (io.tree_hash(Path(config['tasks'][0]['path'])) != d['entry']['validation_copy_hash']
            or not io.evidence_ok(d['entry']['native_verifier'], root)):
        raise ValueError('Reconstruction task/native verifier changed')
    baseline = load(qualified['record']['reproduction']['trials'][0]['execution'], root)
    original_batch = load(qualified['record']['phase3_identity']['batch_manifest'], root)
    requests = load(original_batch['requests'], root)
    request = next(x for x in requests['tasks'] if x['task_key'] == key)
    submission, names, plan = source_plan(binding['extracted'], baseline, request['rebuild'])
    if plan != binding['plan']:
        raise ValueError('Source reconstruction plan changed')
    assert_source_inventory(binding['extracted'], original_files, names, d['execution'], root)
    if any(name in d['final_files'] for name in submission['delete_paths']):
        raise ValueError('Reconstruction restored a deleted source')
    if d['execution']['initial_input_hashes'] != baseline['initial_input_hashes']:
        raise ValueError('Reconstruction initial databases differ from qualified inputs')
    return {'batch': batch, 'trial': trial, 'manifest': manifest, 'binding': binding,
            'qualified': qualified, 'original': original, 'original_trial': original_trial,
            'original_capture': capture, 'prepared': prepared, 'fresh': d,
            'baseline': baseline, 'source_names': names, 'request': request,
            'rebuild_result': io.evidence_ref(results[0], root)}
