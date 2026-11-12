"""Certify only proven absent required Spider DB/project, without rebuilding.

Complete v2 absence observations and frozen task rules are mandatory. This
entrypoint does not infer failure from an exception string or missing artifact.
"""
import argparse
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT / 'src'), str(ROOT / 'scripts')]
from dbt_bench import io
from difficulty_submission_v2 import verify_capture
from difficulty_spider_registry import load, qualified_entry, required_output_evidence
from difficulty_spider_score import save
from difficulty_failure import validate_failure


def absence_rules(capture, qualified):
    if capture.get('schema_version') != 2 or capture.get('inventory_complete') is not True:
        raise ValueError('Whole-project/database absence requires complete v2 inventory')
    evidence = required_output_evidence(qualified)
    missing_db = [x['path'] for x in capture['databases']
                  if x['path'] == qualified['raw']['database'] and x['exists'] is False]
    missing_project = capture['project_observation']['exists'] is False
    if not missing_db and not missing_project:
        raise ValueError('No independently observed required database/project absence')
    ids = evidence['rule_ids'] if missing_db else []
    contract = load(qualified['overlay']['contract'])
    if missing_project:
        workflow = next((r for r in contract['requirements'] if r['id'] == 'R-dbt-workflow'), None)
        if not workflow:
            raise ValueError('No qualified source-project requirement')
        ids = [*ids, 'R-dbt-workflow']
    return {**evidence, 'rule_ids': ids, 'missing_databases': missing_db,
            'missing_required_project': missing_project}


def score(run, trial, output, fixture=False):
    run, trial, output = map(Path, (run, trial, output))
    if output.exists() or not output.resolve().is_relative_to('/scratch'):
        raise ValueError('Select a new SCRATCH failure receipt directory')
    prepared_ref = io.evidence_ref(run / 'manifest.json', ROOT)
    prepared = load(prepared_ref)
    identity = prepared['identity']
    config = io.read_json(run / 'config.yaml')
    key, version = identity['task_key'], identity['task_version']
    if (identity['config'] != config or prepared['config_hash'] != io.file_hash(run / 'config.yaml')
            or prepared['experiment_id'] != io.digest(identity) or not prepared['training_prohibited']):
        raise ValueError('Prepared task identity changed')
    production = identity.get('reviewed_production') or identity['phase3_production']
    entry = production[key]
    review = entry.get('qualified_review') or entry['pool_row']['phase3_record']
    qualified = qualified_entry(review, key, version)
    original_ref = io.evidence_ref(trial / 'result.json', ROOT)
    original = load(original_ref)
    planned = Path(identity['config']['tasks'][0]['path'])
    if (Path(original['config']['task']['path']).resolve() != planned.resolve()
            or io.tree_hash(planned) != identity['actual_task_hash']
            or io.tree_hash(planned / 'tests') != identity['native_tests_hash']
            or io.file_hash(planned / 'instruction.md') != qualified['record']['phase3_identity']['instruction_sha256']):
        raise ValueError('Original execution uses a different task/version')
    if not fixture:
        actual, expected = original['config']['agent'], config['agents'][0]
        if (actual.get('name') != 'qwen-coder' or actual.get('import_path')
                or any(actual.get(k) != expected.get(k) for k in ('name', 'model_name', 'base_url', 'kwargs'))
                or trial.parent.resolve() != (run / 'jobs' / run.name).resolve()):
            raise ValueError('Not the planned original Qwen attempt')
    capture, archive, files = verify_capture(trial, key)
    rules = absence_rules(capture, qualified)
    capture_ref = io.evidence_ref(trial / 'artifacts/difficulty-submission/capture.json', ROOT)
    output.mkdir(parents=True)
    request = {'task_key': key, 'task_version': version, 'qualified_review': review,
               'original_result': original_ref, 'prepared_model_run': prepared_ref,
               'capture': capture_ref, 'fixture_only': fixture, 'model_requests': 0}
    request_ref = save(output / 'checker-request.json', request)
    binding_ref = save(output / 'source-binding.json', {**request,
        'scope': 'Complete original absence inventory; no source reconstruction, no image database fallback.'})
    checker_ref = io.evidence_ref(qualified['checker'], ROOT)
    result = {'task_key': key, 'target': 'audit', 'status': 'scored', 'reward': 0,
              'required_output_failure': rules, 'capture': capture_ref,
              'frozen_table_component': {'status': 'not_run', 'reason': 'Required database/project proved absent; do not manufacture a loadable table or substitute an image database.'},
              'fixture_only': fixture, 'model_requests': 0, 'training_prohibited': True}
    result_ref = save(output / 'audit-result.json', result)
    proof_ref = save(output / 'failure-evaluation.json', {
        'task_key': key, 'task_version': version, 'original_result': original_ref,
        'qualified_review': review, 'checker': checker_ref, 'evaluation_kind': 'direct_contract_failure',
        'complete_capture': True, 'model_requests': 0, 'status': 'scored', 'reward': 0,
        'checker_result': result_ref, 'rule_ids': rules['rule_ids'], 'rule_evidence': rules['rule_evidence'],
        'observations': [capture_ref], 'capture': capture_ref, 'fixture_only': fixture})
    dependencies = save(output / 'adapter-dependencies.json', {
        'code_refs': [io.evidence_ref(ROOT / 'scripts' / n, ROOT) for n in
                     ('difficulty_spider_failure.py', 'difficulty_spider_registry.py', 'difficulty_spider_score.py',
                      'difficulty_failure.py', 'difficulty_submission.py', 'difficulty_submission_v2.py')],
        'checker_dependencies': qualified['overlay']['code_refs'], 'image': entry['image']})
    receipt = {**request, 'evidence_kind': 'direct_contract_failure',
               'failure_evaluation_ref': proof_ref, 'checker': checker_ref,
               'adapter': io.evidence_ref(Path(__file__), ROOT), 'adapter_dependencies': dependencies,
               'source_binding': binding_ref, 'checker_request': request_ref, 'rebuild_result': None,
               'audit_result': result_ref, 'audit_reward': 0, 'audit_status': 'scored', 'training_prohibited': True}
    validate_failure(receipt, result, ROOT)
    save(output / 'receipt.json', receipt)
    return receipt


if __name__ == '__main__':
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--run', type=Path, required=True)
    p.add_argument('--trial', type=Path, required=True)
    p.add_argument('--output', type=Path, required=True)
    p.add_argument('--fixture', action='store_true')
    a = p.parse_args()
    print(score(a.run, a.trial, a.output, a.fixture)['audit_reward'])
