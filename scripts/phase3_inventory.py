#!/usr/bin/env python3
"""Freeze phase 3 inputs and train exclusions without changing an older ledger."""
import argparse
from collections import Counter
from copy import deepcopy
from pathlib import Path

from dbt_bench import io
from phase3_prepare import PILOT, FORMAL, GOLD_DERIVED, task_key
from review_tools import ROOT

IMMEDIATE = {
    'deb__dbt-retail-task-07-cart-recovery': 'Replacement instruction version must complete native14 and audit14.',
    'deb__dbt-retail-task-10-trust-risk': 'Replacement instruction version must complete native14 and audit14.',
    'deb__dbt-dq-macro-enforcement': 'User-directed withdrawal: exact decimal result conflicts with floating-point native expectations.',
    'spider2__xero001': 'Retain exclusion: no observed submission jointly satisfies native and audit.',
}


def initial_candidates(base_rows, exclusions):
    result = deepcopy(base_rows)
    for row in result:
        if row['task_key'] in exclusions:
            if row['split'] != 'train':
                raise ValueError('Cannot change a test candidate')
            row.update(eligible_for_sampling=False, phase3_selection_reason=exclusions[row['task_key']])
    return result


def initialize(output, root=ROOT, pilot=PILOT):
    output, root, pilot = map(Path, (output, root, pilot))
    if output.exists():
        raise ValueError('Inventory output must be new')
    rows = io.unique_index(io.read_jsonl(root / 'catalog/tasks.jsonl'), 'task_key')
    results = io.read_json(pilot / 'results.json')
    tasks, exclusions = [], dict(IMMEDIATE)
    for name, external in sorted(results.items()):
        key = task_key(name)
        row = rows[key]
        if row['split'] != 'train':
            raise ValueError('Pilot contains a non-train task: ' + key)
        category = external['outcome']
        original = pilot / name / 'instruction.md'
        if original.read_bytes() != (root / row['run_path'] / 'instruction.md').read_bytes():
            raise ValueError('Original instruction drift: ' + key)
        selected = pilot / (name + '_v2' if category == 'patched_pass' else name)
        refs = {n.replace('.', '_'): io.evidence_ref(selected / n, root)
                for n in ('instruction.md', 'decisions.md', 'build.txt') if (selected / n).is_file()}
        triage = pilot / name / 'triage/triage.md'
        if category not in FORMAL:
            if not triage.is_file():
                raise ValueError('Missing exclusion rationale: ' + key)
            refs['triage'] = io.evidence_ref(triage, root)
            exclusions[key] = 'User-directed exclusion from supplied triage: ' + category
        tasks.append({'task_key': key, 'source': row['source'], 'split': 'train',
            'category': category, 'formal_execution_required': category in FORMAL,
            'selected_inputs': refs, 'external_result': external,
            'instruction_variant': category == 'patched_pass',
            'gold_derived_correction': key in GOLD_DERIVED,
            'previous_record': io.evidence_ref(root / row['validation_ref'], root),
            'status': 'pending' if category in FORMAL else 'excluded_by_user',
            'scope': 'External pilot is a lead; native/audit certification requires formal evidence.'})
    xero = rows['spider2__xero001']
    if xero['split'] != 'train':
        raise ValueError('Xero is outside train')
    tasks.append({'task_key': xero['task_key'], 'source': 'spider2', 'split': 'train',
        'category': 'retained_incompatible', 'formal_execution_required': False,
        'previous_record': io.evidence_ref(root / xero['validation_ref'], root),
        'status': 'excluded_by_user', 'reason': IMMEDIATE[xero['task_key']]})
    counts = dict(Counter(t['category'] for t in tasks))
    if counts != {'agree': 33, 'native_only': 4, 'patched_pass': 15, 'gold_error': 7,
                  'unstable': 1, 'boundary_float': 1, 'retained_incompatible': 1}:
        raise ValueError('Pilot category count differs from the authorized scope: ' + str(counts))
    baseline = root / 'catalog/pools/audit-phase2-v14'
    base_candidates = io.read_jsonl(baseline / 'candidates.jsonl')
    candidates = initial_candidates(base_candidates, exclusions)
    protected = [root / n for n in ('catalog/tasks.jsonl', 'catalog/splits.json',
                 'validation/ledger.jsonl', 'validation/delivery-status.json')]
    for row in rows.values():
        record = root / row['validation_ref']
        protected.extend([record, record.parent / 'contract.yaml'])
    protected.extend(p for p in baseline.iterdir() if p.is_file())
    output.mkdir(parents=True)
    io.write_json(output / 'protected-files.json', [io.evidence_ref(p, root) for p in protected])
    io.write_json(output / 'inventory.json', {'schema_version': 1, 'phase': 3,
        'model_requests': 0, 'pilot_results': io.evidence_ref(pilot / 'results.json', root),
        'baseline_pool': io.evidence_ref(baseline / 'manifest.json', root),
        'counts': counts, 'tasks': tasks, 'formal_tasks': 52,
        'test_tasks_unchanged': sum(r['split'] == 'test' for r in rows.values()),
        'protected_files': io.evidence_ref(output / 'protected-files.json', root)})
    io.write_jsonl(output / 'candidates-v00.jsonl', candidates)
    io.write_json(output / 'selection-v00.json', {'schema_version': 1,
        'version': 'train-phase3-v00', 'baseline_pool': io.evidence_ref(baseline / 'manifest.json', root),
        'candidates': io.evidence_ref(output / 'candidates-v00.jsonl', root),
        'inventory': io.evidence_ref(output / 'inventory.json', root),
        'exclusions': exclusions, 'reviewed_new_tasks': 0,
        'train_eligible_before': sum(r['split'] == 'train' and r['eligible_for_sampling'] for r in base_candidates),
        'train_eligible_now': sum(r['split'] == 'train' and r['eligible_for_sampling'] for r in candidates),
        'test_candidates_unchanged': [r for r in base_candidates if r['split'] == 'test'] == [r for r in candidates if r['split'] == 'test'],
        'model_requests': 0, 'native_rewards_modified': False})
    io.write_json(output / 'state.json', {'schema_version': 1, 'phase': 3,
        'inventory': io.evidence_ref(output / 'inventory.json', root), 'completed': {}, 'ready': {},
        'latest_freeze': io.evidence_ref(output / 'selection-v00.json', root),
        'running_batches': [], 'completed_batches': [], 'model_requests': 0})
    return {'counts': counts, 'formal_tasks': 52, 'train_eligible': sum(r['split'] == 'train' and r['eligible_for_sampling'] for r in candidates)}


if __name__ == '__main__':
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--output', required=True, type=Path)
    a = p.parse_args()
    print(initialize(a.output))
