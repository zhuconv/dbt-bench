#!/usr/bin/env python3
"""Create an immutable phase-2 queue from a completed native freeze.

Queueing a task executes no checks and never assigns gap. The queue is separate
from both the native ledger and subsequent audit reviews.
"""
import argparse
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from dbt_bench import io
from dbt_bench.validation import CHECKS


def inventory(root, completion, assignments):
    root = Path(root)
    if not completion['phase1_complete'] or completion['active_native_pending']:
        raise ValueError('Phase 1 must finish before the phase 2 queue')
    frozen = completion['frozen_pool']
    if not io.evidence_ok(frozen, root):
        raise ValueError('Phase 1 frozen manifest changed')
    manifest = io.read_json(root / frozen['path'])
    ledger_ref = manifest['validation_snapshot']
    if not io.evidence_ok(ledger_ref, root):
        raise ValueError('Frozen ledger changed')
    records = io.read_jsonl(root / ledger_ref['path'])
    by_key = io.unique_index(records, 'task_key')
    catalog = io.unique_index(io.read_jsonl(root / 'catalog/tasks.jsonl'), 'task_key')
    if set(catalog) != set(by_key):
        raise ValueError('Catalog/frozen ledger membership differs')
    queued = []
    for key, record in by_key.items():
        if record['native']['conclusion'] != 'weak':
            continue
        checks = record['native']['checks']
        counts = Counter(c['status'] for c in checks.values())
        existing = record['audit']['conclusion'] == 'validated'
        queued.append({'task_key': key, 'source': catalog[key]['source'], 'split': catalog[key]['split'],
            'baseline_identity_hash': record['identity_hash'],
            'native_check_counts': dict(counts), 'reference_status': checks['reference']['status'],
            'witness_kind': record['witness_kind'],
            'existing_audit_version': record['audit'].get('checker_version'),
            'audit_status': 'existing_validated_retained' if existing else 'pending',
            'audit_checks': {name: record['audit'].get('checks', {}).get(name, {'status': 'pending',
                'reason': 'No phase 2 execution or review yet', 'evidence': []}) for name in CHECKS},
            'assigned_to': assignments.get(key),
            'priority_key': [0 if existing else 1, 0 if checks['reference']['status'] == 'pass' else 1,
                             counts['gap'], counts['fail'], key]})
    queued.sort(key=lambda v: v['priority_key'])
    for rank, entry in enumerate(queued, 1):
        entry['priority_rank'] = rank
    unknown = set(assignments) - {x['task_key'] for x in queued}
    if unknown:
        raise ValueError('Assignments outside weak-task queue: ' + str(sorted(unknown)))
    return {'schema_version': 1, 'phase': 2, 'created_at_utc': datetime.now(timezone.utc).isoformat(),
        'phase1_freeze': frozen, 'baseline_ledger': ledger_ref,
        'candidate_count': len(by_key), 'weak_task_count': len(queued),
        'existing_validated_audits': sum(x['audit_status'] == 'existing_validated_retained' for x in queued),
        'isolated_tasks': sorted(k for k, r in by_key.items() if r['native']['conclusion'] == 'broken'),
        'native_validated_tasks': sorted(k for k, r in by_key.items() if r['native']['conclusion'] == 'validated'),
        'priority_rationale': 'Retain existing validated audit; then reference pass, fewer executed gaps, fewer failures. Family assignments may run in parallel.',
        'scope': 'Planning and retained-state inventory, not new check execution or certification',
        'new_model_requests': 0, 'native_rewards_modified': False, 'tasks': queued}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--root', type=Path, default=Path(__file__).resolve().parents[1])
    parser.add_argument('--assignments', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    value = inventory(args.root, io.read_json(args.root / 'validation/phase1-completion.json'),
                      io.read_json(args.assignments)['task_owners'])
    if args.output.exists():
        raise ValueError('Refuse to overwrite inventory')
    io.write_json(args.output, value)
    print({'weak_tasks': value['weak_task_count'], 'existing_audits': value['existing_validated_audits'],
           'new_checks_executed': 0, 'output': io.evidence_ref(args.output, args.root)})


if __name__ == '__main__':
    main()
