"""Apply the experimental checker to retained case exports and one model output.

This creates new companion evidence only. It launches no benchmark or model,
does not rewrite native rewards, and does not certify task/sample eligibility.
"""
import argparse
from collections import Counter
from pathlib import Path
import sys
from checker import score

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[3]
sys.path.insert(0, str(ROOT / 'src'))
from dbt_bench.io import evidence_ref, evidence_ok, file_hash, read_json, write_json
from capture import verify_capture

BATCHES = ('workday-semantics-positive-v1', 'workday-semantics-cases-v2',
           'workday-deleted-relations-v2', 'workday-workflow-v1', 'workday-workflow-direct-v2')


def run(output):
    output = output.resolve()
    output.mkdir(exist_ok=False, parents=True)
    ref = lambda p: evidence_ref(p, ROOT)
    records, source_refs = [], []
    seen = set()
    for name in BATCHES:
        batch = ROOT / 'artifacts/validation' / name
        source = batch / 'reconciliation.json'
        reconciliation = read_json(source)
        assert reconciliation['complete'] and not reconciliation['missing']
        source_refs.append(ref(source))
        for trial in reconciliation['trials']:
            trial_id = trial['trial_id']
            assert trial_id not in seen
            seen.add(trial_id)
            result_path = ROOT / trial['result']['path']
            assert evidence_ok(trial['result'], ROOT)
            agent = result_path.parent / 'agent'
            record = {'trial_id': trial_id, 'mode': trial['mode'], 'data_scope': trial['data_scope'],
                      'native_result': trial['result'], 'native_rewards': trial['native_rewards'],
                      'independent_case_outcome': trial['outcome'], 'new_benchmark_trial': False}
            if not trial['proof_recomputed']:
                records.append({**record, 'audit': None,
                                'not_evaluated_reason': 'Incomplete case capture/proof; retained original null reward. No audit score manufactured.'})
                continue
            verify_capture(ROOT, trial)
            request = {'schema_version': 1, 'task_key': 'spider2__workday002',
                       'data_scope': trial['data_scope'], 'inputs': ref(agent / 'inputs.json'),
                       'output': ref(agent / 'output.json'), 'workflow': None}
            state_path = agent / 'workflow-state.json'
            if state_path.exists():
                state = read_json(state_path)
                case_dir = (ROOT / trial['case']['path']).parent
                request['workflow'] = {
                    'capture_kind': 'reviewed_validation_case_command_capture',
                    'executed_engine': ref(case_dir / 'engine.py'), 'observation': ref(state_path),
                    'command_logs': {c['log']: ref(agent / c['log']) for c in state['commands']},
                    'run_results': ref(agent / 'dbt-run_results.json') if (agent / 'dbt-run_results.json').exists() else None,
                    'dbt_manifest': ref(agent / 'dbt-manifest.json') if (agent / 'dbt-manifest.json').exists() else None,
                }
            manifest = output / 'inputs' / (trial_id + '.json')
            write_json(manifest, request)
            audit = score(ROOT, manifest)
            destination = output / 'audits' / (trial_id + '.json')
            write_json(destination, audit)
            records.append({**record, 'audit': ref(destination), 'input_manifest': ref(manifest),
                            'status': audit['status'], 'audit_reward': audit['audit_reward'],
                            'data_status': audit['data']['status'], 'workflow_status': audit['workflow']['status']})
    # Original model submission export was captured/replayed independently. Do
    # not infer missing workflow observations from a successful native reward.
    model = ROOT / 'artifacts/validation/workday-model-source-rebuild-v1/snapshots/captured-submission'
    model_manifest = output / 'inputs/retained-model-submission.json'
    write_json(model_manifest, {'schema_version': 1, 'task_key': 'spider2__workday002',
        'data_scope': 'original_pinned_inputs', 'inputs': ref(model / 'inputs.json'),
        'output': ref(model / 'output.json'), 'workflow': None})
    model_result = score(ROOT, model_manifest)
    write_json(output / 'audits/retained-model-submission.json', model_result)
    summary = {'schema_version': 1, 'task_key': 'spider2__workday002', 'certified': False,
        'observed_prior_case_trials': len(records), 'evaluated_case_exports': sum(r['audit'] is not None for r in records),
        'case_statuses': dict(Counter(r.get('status', 'not_evaluated') for r in records)),
        'case_data_statuses': dict(Counter(r['data_status'] for r in records if r['audit'])),
        'case_workflow_statuses': dict(Counter(r['workflow_status'] for r in records if r['audit'])),
        'records': records, 'source_reconciliations': source_refs,
        'model_submission': {'input_manifest': ref(model_manifest), 'audit': ref(output / 'audits/retained-model-submission.json'),
            'status': model_result['status'], 'audit_reward': model_result['audit_reward'],
            'unresolved_data_cells': len(model_result['data']['unknown']),
            'scope': 'Retained model output only; workflow capture not supplied and empty VARCHAR aggregation remains unknown.'},
        'checker': ref(HERE / 'checker.py'), 'input_policy': ref(HERE / 'input-policy.json'), 'runner': ref(Path(__file__)), 'capture_binding': ref(HERE / 'capture.py'),
        'new_model_trials': 0, 'new_benchmark_trials': 0, 'new_submission_replays': 0,
        'scope': 'Post-hoc experimental companion audits on retained exports. No native result changed and no task/sample certification.'}
    write_json(output / 'summary.json', summary)
    print({k: summary[k] for k in ['observed_prior_case_trials', 'evaluated_case_exports', 'case_statuses', 'case_data_statuses', 'case_workflow_statuses']})


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output-dir', type=Path, required=True)
    run(parser.parse_args().output_dir)
