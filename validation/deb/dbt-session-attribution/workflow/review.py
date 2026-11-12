"""Record the explicitly executed workflow batch without promoting eligibility."""
from datetime import datetime, timezone
from pathlib import Path
import shutil
import sys

ROOT = Path(__file__).resolve().parents[4]
sys.path.insert(0, str(ROOT / 'src'))
from dbt_bench.catalog import load_catalog
from dbt_bench.io import digest, evidence_ok, evidence_ref, file_hash, read_json, read_jsonl, tree_manifest, write_json
from dbt_bench.validation import identity, review_inputs, build_record, summarize


def main():
    key = 'deb__dbt-session-attribution'
    base = Path(__file__).resolve().parent.parent
    batch = ROOT / 'artifacts/validation/session-workflow-cases-v1'
    stage = ROOT / 'artifacts/validation/session-workflow-review-v1'
    out = ROOT / 'validation/evidence/session-workflow-review-v1'
    assert not out.exists()
    stage.mkdir(exist_ok=True)
    ref = lambda p: evidence_ref(p, ROOT)
    result = read_json(batch / 'reconciliation.json')
    assert result['complete'] and result['observed'] == result['planned'] == 8
    assert result['outcomes'] == {'accepted_correct': 2, 'missed_wrong': 5, 'detected_wrong': 1}
    assert all(t['proof_recomputed'] and t['exception_info'] is None for t in result['trials'])
    assert read_json(batch / 'cleanup.json')['passed']
    for trial in result['trials']:
        assert all(evidence_ok(trial[k], ROOT) for k in ['result', 'independent_proof', 'native_log'])
    rows = load_catalog(ROOT)
    row = next(r for r in rows if r['task_key'] == key)
    contract_path = base / 'contract.yaml'
    contract = read_json(contract_path)
    previous = read_json(base / 'record.json')
    assert contract == read_json(batch / 'before-contract.yaml')
    assert previous == read_json(batch / 'before-record.json')
    current = identity(ROOT, row, contract_path, 'apptainer')
    assert all(v == previous['identity'][k] for k, v in current.items() if k != 'cases')
    assert contract['reviewed_inputs'] == review_inputs(previous['identity'])
    old_cases = read_json(batch / 'before-cases.json')
    assert digest(old_cases) == previous['identity']['cases']
    current_cases = {v['path']: v for v in tree_manifest(base / 'cases')}
    assert all(current_cases.get(v['path']) == v for v in old_cases)
    additions = sorted(set(current_cases) - {v['path'] for v in old_cases})
    assert additions and all(p.startswith('workflow-v1/') for p in additions)

    out.mkdir()
    for name in ['contract.yaml', 'record.json']:
        shutil.copyfile(base / name, out / ('before-' + name))
    negatives = [t for t in result['trials'] if t['kind'] == 'negative']
    summary = {'schema_version': 1, 'timestamp_utc': datetime.now(timezone.utc).isoformat(), 'task_key': key,
        'native': 'weak', 'audit': 'pending', 'full_validation_complete': False,
        'original_input_trials': 8, 'additional_fixture_trials': 0, 'qwen_smoke_executed': False,
        'negative_detection': {'should_detect': len(negatives), 'detected': 1, 'missed': 5},
        'controls': {'independent_initializations': 2, 'additional_builds_per_initialization': 2, 'accepted': 2},
        'reconciliation': ref(batch / 'reconciliation.json'), 'cleanup': ref(batch / 'cleanup.json'),
        'source_instruction': ref(ROOT / row['run_path'] / 'instruction.md'),
        'previous_contract': ref(out / 'before-contract.yaml'), 'previous_record': ref(out / 'before-record.json'),
        'unchanged_execution_identity': {k: v for k, v in current.items() if k not in ['contract', 'cases']},
        'case_additions': additions, 'prior_cases_unchanged': True,
        'observations': [{'mode': t['mode'], 'outcome': t['outcome'], 'requirements': t['requirements'],
                          'derivation': t['independent_facts']['derivation'], 'proof': t['independent_proof'],
                          'native_result': t['result']} for t in result['trials']],
        'scope': 'Pinned original inputs. Workflow proof uses actual sources, information_schema, compiled SELECT ordering and repeated successful dbt builds. It never infers model SELECT order from table scan order. Correct output values alone do not establish workflow compliance.',
        'remaining': ['Full hidden-asset visibility inventory', 'Conflicting instruction project paths require a task-variant decision',
                      'Nullable-device, tied timestamps, repeated converting-order, subsecond and half-cent semantics',
                      'Validated supplemental checker covering the declared full contract', 'Actual trial trace/context conformance']}
    write_json(out / 'summary.json', summary)
    review_ref = ref(out / 'summary.json')
    for requirement in contract['requirements']:
        applicable = [t for t in result['trials'] if requirement['id'] in t['requirements']]
        if not applicable: continue
        coverage = requirement.setdefault('coverage', {}).setdefault('native', {})
        refs = [r for t in applicable for r in [t['independent_proof'], t['result']]]
        coverage.setdefault('evidence', []).extend(refs)
        coverage['workflow_review'] = review_ref
        coverage['original_input_workflow_cases'] = [{'mode': t['mode'], 'outcome': t['outcome']} for t in applicable]
        misses = [t for t in applicable if t['outcome'] == 'missed_wrong']
        if misses and coverage.get('status') not in ['missed_wrong', 'rejected_legal']:
            first = misses[0]
            coverage.update(status='missed_wrong', independent_proof=first['independent_proof'],
                            checker_evidence=first['result'], case_validity='business_error',
                            scope='Explicit workflow obligation on original fixed inputs',
                            reason=first['independent_facts']['derivation'])
        elif coverage.get('status') == 'pending':
            coverage.update(status='covered', scope='Named original-input workflow cases only')
        if requirement['id'] == 'R-dbt-workflow':
            requirement['rule'] = 'Create models/marts/attribution_report.sql in the existing dbt project; use existing staging models, do not create staging layer models, and materialize a table.'
    contract.setdefault('review_history', []).append(ref(out / 'before-contract.yaml'))
    contract.setdefault('review_evidence', []).append(review_ref)
    contract['workflow_review'] = review_ref
    contract['missing_contract_work'] = summary['remaining']
    contract['checks']['negatives']['evidence'].append(review_ref)
    contract['checks']['negatives']['reason'] = 'Sixteen independently established extra-input semantic negatives plus original-input duplicate and six workflow negatives executed. Workflow: one detected, five missed. Ambiguous semantic edge cases and full audit certification remain incomplete.'
    assert contract['checks']['negatives']['status'] == 'gap'
    contract['checks']['stability']['evidence'].append(review_ref)
    contract['checks']['stability']['reason'] += ' Two fresh original-input controls also each execute two additional dbt builds; all required outputs and source-query order remain identical. The incremental-append negative proves non-idempotent behavior is not rejected by native grading.'
    contract['notes'].append('Workflow v1: eight original-data trials, two accepted repeated-build controls, one detected schema violation and five missed workflow violations. Semantic correctness and the task project-path ambiguity remain separate questions; no audit or training eligibility is certified.')
    contract['reviewed_inputs'] = review_inputs(current)
    write_json(contract_path, contract)
    record = build_record(ROOT, row, contract_path, backend='apptainer')
    assert record['review_current'] and record['native']['conclusion'] == 'weak' and record['audit']['conclusion'] == 'pending'
    assert not record['sampling_eligible']
    assert {k: v['status'] for k, v in record['native']['checks'].items()} == {k: v['status'] for k, v in previous['native']['checks'].items()}
    write_json(base / 'history' / (digest(previous) + '.json'), previous)
    write_json(base / 'record.json', record)
    records = read_jsonl(ROOT / 'validation/ledger.jsonl')
    assert next(r for r in records if r['task_key'] == key) == previous
    write_json(stage / 'ledger-before.json', records)
    final = summarize(ROOT, rows, [record if r['task_key'] == key else r for r in records])
    write_json(stage / 'ledger-review.json', {'only_changed_task': key, 'other_records_unchanged': True,
        'summary': final, 'review': review_ref, 'record': ref(base / 'record.json')})
    future_path = ROOT / 'configs/submissions' / (key + '.json')
    future = read_json(future_path)
    write_json(stage / 'submission-spec-before.json', future)
    future['contract'] = ref(contract_path)
    future['path_evidence'].append(review_ref)
    write_json(future_path, future)

    previous_index_path = ROOT / 'artifacts/validation/session-semantics-v2/retained-trial-index.json'
    previous_index = read_json(previous_index_path)
    all_trials = previous_index['trials'] + [{'kind': 'native', 'result': t['result'], 'trial_id': t['trial_id']} for t in result['trials']]
    assert len({t['trial_id'] for t in all_trials}) == len(all_trials) == 1064
    assert all(evidence_ok(t['result'], ROOT) for t in all_trials)
    write_json(stage / 'retained-trial-index.json', {'schema_version': 1, 'timestamp_utc': datetime.now(timezone.utc).isoformat(),
        'counts': {'native': 1041, 'additional_fixture': 23}, 'duplicates': [], 'trials': all_trials,
        'previous': ref(previous_index_path), 'new_workflow_trials': ref(batch / 'reconciliation.json'),
        'diagnostic_replays': 10, 'model_trials': 4,
        'scope': 'All retained original-input runs, including failures and historical identities. Additional fixtures and diagnostics remain separately counted; this is not a single-experiment score denominator.'})
    print(final)


if __name__ == '__main__': main()
