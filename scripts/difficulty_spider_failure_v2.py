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
from difficulty_failure_qualification import validate as validate_qualification


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


def relation_rules(capture, qualified, probe, root=ROOT):
    """Only explicit reviewed deliverables; no test-text or whole-DB guessing."""
    if not probe.get('database_and_wal_unchanged') or probe['hashes_before'] != probe['hashes_after']:
        raise ValueError('Original submission projection was not read-only')
    actual={r['path']:r['sha256'] if r['exists'] else None for r in capture['databases']}
    for path,digest in probe['hashes_before'].items():
        if path not in actual or actual[path] != digest:raise ValueError('Probe is not the original captured DB/WAL')
    base=required_output_evidence(qualified,root);contract=load(qualified['overlay']['contract'],root)
    cref=contract.get('original_contract') or qualified['overlay']['contract'];c=load(cref,root)
    required=c.get('deliverables')
    if not isinstance(required,list) or not required:raise ValueError('Explicit deliverables unavailable')
    absent=[]
    for d in required:
        relation=d.get('relation') or d.get('table')
        if not isinstance(relation,str):raise ValueError('No explicit relation in contract')
        database=d.get('database') or qualified['raw']['database']
        found=[x for x in probe['relation_inventory'] if x['database']==database and (x['relation']==relation or '.' not in relation and x['relation'].split('.')[-1]==relation or '.' not in x['relation'] and x['relation']==relation.split('.')[-1])]
        if len(found)!=1 or not found[0].get('query_succeeded'):raise ValueError('Required relation inventory incomplete/ambiguous')
        if found[0]['exists'] is False:absent.append({'database':database,'relation':relation})
    if not absent:raise ValueError('No independently observed required relation absence')
    return {**base,'missing_required_relations':absent,'rule_evidence':[*base['rule_evidence'],cref,qualified['record']['audit']['overlay']]}


def evaluate(capture,qualified,probe=None,root=ROOT):
    if probe is not None:return relation_rules(capture,qualified,probe,root)
    value=absence_rules(capture,qualified)
    value['rule_evidence']=[*value['rule_evidence'],qualified['record']['audit']['overlay']]
    return value


def score(run, trial, output, fixture=False, validation=None, probe=None, frozen_result=None):
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
    probe_ref=io.evidence_ref(Path(probe),ROOT) if probe else None
    rules = evaluate(capture, qualified, load(probe_ref) if probe_ref else None)
    if validation is None:raise ValueError("New failure evaluator must have executed qualification controls")
    validation_ref=io.evidence_ref(Path(validation),ROOT)
    capture_ref = io.evidence_ref(trial / 'artifacts/difficulty-submission/capture.json', ROOT)
    output.mkdir(parents=True)
    request = {'task_key': key, 'task_version': version, 'qualified_review': review,
               'original_result': original_ref, 'prepared_model_run': prepared_ref,
               'capture': capture_ref, 'fixture_only': fixture, 'model_requests': 0}
    request_ref = save(output / 'checker-request.json', request)
    binding_ref = save(output / 'source-binding.json', {**request,
        'scope': 'Complete original absence inventory; no source reconstruction, no image database fallback.'})
    qualified_checker = io.evidence_ref(qualified['checker'], ROOT)
    checker_ref = io.evidence_ref(Path(__file__), ROOT)
    result = {'task_key': key, 'target': 'audit', 'status': 'scored', 'reward': 0,
              'required_output_failure': rules, 'capture': capture_ref,
              'frozen_table_component': io.evidence_ref(Path(frozen_result),ROOT) if frozen_result else {'status':'not_run','reason':'No database to evaluate; frozen checker has not been assigned a fabricated score.'},
              'fixture_only': fixture, 'model_requests': 0, 'training_prohibited': True}
    result_ref = save(output / 'audit-result.json', result)
    proof_ref = save(output / 'failure-evaluation.json', {
        'task_key': key, 'task_version': version, 'original_result': original_ref,
        'qualified_review': review, 'checker': checker_ref, 'evaluation_kind': 'direct_contract_failure',
        'complete_capture': True, 'model_requests': 0, 'status': 'scored', 'reward': 0,
        'checker_result': result_ref, 'rule_ids': rules['rule_ids'], 'rule_evidence': rules['rule_evidence'],
        'observations': [capture_ref]+([probe_ref] if probe_ref else []), 'capture': capture_ref, 'fixture_only': fixture})
    dependencies = save(output / 'adapter-dependencies.json', {
        'code_refs': [io.evidence_ref(ROOT / 'scripts' / n, ROOT) for n in
                     ('difficulty_spider_failure_v2.py', 'difficulty_spider_registry.py', 'difficulty_spider_score.py',
                      'difficulty_failure.py', 'difficulty_submission.py', 'difficulty_submission_v2.py')],
        'checker_dependencies': qualified['overlay']['code_refs'], 'image': entry['image']})
    receipt = {**request, 'evidence_kind': 'direct_contract_failure',
               'failure_evaluation_ref': proof_ref, 'checker': checker_ref,
               'qualified_checker':qualified_checker,'failure_evaluator_validation_ref':validation_ref,
               'adapter': io.evidence_ref(Path(__file__), ROOT), 'adapter_dependencies': dependencies,
               'source_binding': binding_ref, 'checker_request': request_ref, 'rebuild_result': None,
               'audit_result': result_ref, 'audit_reward': 0, 'audit_status': 'scored', 'training_prohibited': True}
    validate_failure(receipt, result, ROOT)
    validate_qualification(receipt,qualified['record'],qualified['overlay'],ROOT)
    save(output / 'receipt.json', receipt)
    return receipt


if __name__ == '__main__':
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--run', type=Path, required=True)
    p.add_argument('--trial', type=Path, required=True)
    p.add_argument('--output', type=Path, required=True)
    p.add_argument('--fixture', action='store_true')
    p.add_argument('--validation',type=Path,required=True)
    p.add_argument('--probe',type=Path)
    p.add_argument('--frozen-result',type=Path)
    a = p.parse_args()
    print(score(a.run,a.trial,a.output,a.fixture,a.validation,a.probe,a.frozen_result)['audit_reward'])
