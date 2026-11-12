"""Execute failure-evaluator controls using retained actual source/DB captures."""
from dbt_bench.local_config import expand as _release_expand
from pathlib import Path
from copy import deepcopy
from dbt_bench import io
from difficulty_deb_failure_v3 import evaluate, evaluate_source
from difficulty_submission_v2 import verify_capture
from phase4_capture import verify_trial
ROOT = Path.cwd()
D = Path(_release_expand('${ARTIFACT_ROOT}/difficulty/qwen36-v1'))
B = D / 'deb-adapter-v1'

def run(output):
    output = Path(output)
    if output.exists():
        raise ValueError('Control output exists')
    output.mkdir(parents=True)
    ref = lambda p: io.evidence_ref(p, ROOT)
    evaluator = ref(ROOT / 'scripts/difficulty_deb_failure_v3.py')
    checks = {}
    population = io.read_json(D / 'population-v1.json')
    row = next((x for x in population['tasks'] if x['task_key'] == 'deb__dbt-rfm-customer-segmentation'))
    review = io.read_json(ROOT / row['qualified_review']['path'])
    formal = review['reproduction']['trials'][0]
    batch = Path(review['phase4_identity']['batch_manifest']['path']).parent
    verified = verify_trial(batch, Path(formal['result']['path']).parent)
    facts = {'complete': True, 'inventory_complete': True, 'errors': [], 'project_root': verified['request']['project_root']}
    healthy_source = evaluate_source('dbt-rfm-customer-segmentation', facts, set(verified['final_files']))
    assert healthy_source['reward'] is None and 'profiles.yml' in verified['final_files']
    health = {'fixture_only': True, 'model_requests': 0, 'evaluator': evaluator, 'result': healthy_source, 'source_fact_fixture': facts, 'actual_facts': [formal['result'], formal['execution'], ref(verified['final_files']['profiles.yml']), row['qualified_review']], 'scope': 'Explicit predicate fixture from verified actual qualified clean-build source. Not an original model capture or measured trial.'}
    io.write_json(output / 'healthy-source.json', health)
    controls = {}
    for label in ['oracle', 'nop']:
        run = D / ('capture-v2-loyalty-' + label)
        trial = next((run / 'jobs' / run.name).glob('*/result.json')).parent
        capture, _, _ = verify_capture(trial, 'deb__dbt-loyalty-points-analysis')
        folder = B / 'failure-controls-v2' / label
        inv = io.read_json(folder / 'probe/inventory.json')
        req = io.read_json(folder / 'checker-request.json')
        value = evaluate(capture, inv, req['requirements'])
        assert value['reward'] == (None if label == 'oracle' else 0)
        controls[label] = {'fixture_only': True, 'model_requests': 0, 'evaluator': evaluator, 'result': value, 'capture': ref(trial / 'artifacts/difficulty-submission/capture.json'), 'inventory': ref(folder / 'probe/inventory.json'), 'actual_probe': ref(folder / 'probe/process.json')}
        io.write_json(output / (label + '-table.json'), controls[label])
        if label == 'nop':
            incomplete = deepcopy(capture)
            incomplete['inventory_complete'] = False
            missing_source = {'complete': True, 'inventory_complete': False, 'errors': [], 'project_root': '/app/dbt_project'}
            bad_table = evaluate(incomplete, inv, req['requirements'])
            bad_source = evaluate_source('dbt-rfm-customer-segmentation', missing_source, set())
            assert bad_table['reward'] is None and bad_source['reward'] is None
            io.write_json(output / 'incomplete.json', {'fixture_only': True, 'model_requests': 0, 'evaluator': evaluator, 'capture': controls[label]['capture'], 'mutation': {'inventory_complete': False}, 'table_result': bad_table, 'source_result': bad_source, 'expected_reward': None})
    source = io.read_json(B / 'failure-controls-v3/rfm-oracle/audit-result.json')
    assert source['reward'] == 0 and source['violations'][0]['rule_id'] == 'R-profile-local-file'
    checks = {'healthy_submission_rejected_as_failure': {'status': 'pass', 'evidence': [ref(output / 'healthy-source.json'), ref(output / 'oracle-table.json')]}, 'incomplete_capture_rejected': {'status': 'pass', 'evidence': [ref(output / 'incomplete.json')]}, 'contract_failure_detected': {'status': 'pass', 'evidence': [ref(output / 'nop-table.json'), ref(B / 'failure-controls-v3/rfm-oracle/audit-result.json'), ref(B / 'failure-controls-v3/rfm-oracle/source-evaluation.json')]}}
    io.write_json(output / 'qualification.json', {'schema_version': 1, 'evaluator': evaluator, 'fixture_only': True, 'model_requests': 0, 'checks': checks, 'runner': ref(Path(__file__)), 'scope': 'Actual read-only oracle/nop database controls plus actual RFM missing local profile; healthy formal source and incomplete-state controls explicitly labelled fixtures.'})
    print(output / 'qualification.json')
if __name__ == '__main__':
    import argparse
    p = argparse.ArgumentParser()
    p.add_argument('--output', type=Path, required=True)
    a = p.parse_args()
    run(a.output)
