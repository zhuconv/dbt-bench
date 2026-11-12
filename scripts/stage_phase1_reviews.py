#!/usr/bin/env python3
"""Snapshot root-selected executed mechanical reviews for phase1_publish.

This does not execute checks or turn template gaps into evidence. Root selects
tasks only after reviewing their actual execution and per-rule evidence.
"""
import argparse
from collections import Counter
import json
from pathlib import Path
from review_tools import ROOT, cached_hashes
from dbt_bench import io
from dbt_bench.catalog import load_catalog
from dbt_bench.validation import identity
from phase1_mechanical import MECHANICAL


def task_paused(task_key, review_root):
    path = review_root / 'spider-pause-20261106.json'
    return (task_key.startswith('spider2__') and path.exists()
            and io.read_json(path).get('pause_semantic_agents', False))


def validate_review(value, task_key, current, root):
    if value.get('task_key') != task_key or value.get('target') != 'native':
        raise ValueError('Wrong task or checker target: ' + task_key)
    if value.get('base_identity') != current or value.get('model_requests', 0) != 0:
        raise ValueError('Changed identity or model requests: ' + task_key)
    checks = value.get('checks', {})
    if set(checks) != set(MECHANICAL):
        raise ValueError('Exactly nine mechanical checks required: ' + task_key)
    for name, check in checks.items():
        if check.get('status') not in {'pass', 'fail', 'gap'} or not check.get('reason'):
            raise ValueError('Unfinished check: ' + task_key + ':' + name)
        refs = check.get('evidence', [])
        if not refs or not all(io.evidence_ok(ref, root) for ref in refs):
            raise ValueError('Missing or changed evidence: ' + task_key + ':' + name)


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--review-root', type=Path, default=ROOT / 'artifacts/validation/phase1-review-v1')
    p.add_argument('--task', action='append', required=True)
    p.add_argument('--output', type=Path, required=True)
    a = p.parse_args()
    if a.output.exists() or len(a.task) != len(set(a.task)):
        p.error('Use a new output directory and unique task IDs')
    state = io.read_json(a.review_root / 'state.json')
    groups = io.read_json(a.review_root / 'assignments.json')['groups']
    rows = {r['task_key']: r for r in load_catalog(ROOT)}
    prepared = []
    counts = Counter()
    with cached_hashes():
        for key in sorted(a.task):
            if key in state['isolated_broken'] or key in state['completed'] or task_paused(key, a.review_root):
                raise ValueError('Task is isolated, already frozen, or paused: ' + key)
            owners = [group for group, keys in groups.items() if key in keys]
            if len(owners) != 1:
                raise ValueError('Missing or ambiguous ownership: ' + key)
            path = a.review_root / owners[0] / key / 'mechanical-review.json'
            data = path.read_bytes()
            value = json.loads(data)
            current = identity(ROOT, rows[key], (ROOT / rows[key]['validation_ref']).parent / 'contract.yaml', 'apptainer')
            validate_review(value, key, current, ROOT)
            if path.read_bytes() != data:
                raise ValueError('Review changed while staging: ' + key)
            prepared.append((key, path, data))
            counts.update(c['status'] for c in value['checks'].values())
    a.output.mkdir(parents=True)
    refs = []
    for key, path, data in prepared:
        dest = a.output / key / 'mechanical.json'
        dest.parent.mkdir()
        dest.write_bytes(data)
        refs.append({'task_key': key, 'source': str(path), 'snapshot': io.evidence_ref(dest, ROOT)})
    io.write_json(a.output / 'summary.json', {'execution_backed': True,
        'scope': 'Root-selected actual worker reviews; staging validates identity and retained evidence, not semantics.',
        'template_inventory_merged': False, 'task_count': len(prepared),
        'check_dispositions': dict(counts), 'reviews': refs, 'model_requests': 0,
        'ledger_updated': False})
    print({'staged': len(prepared), 'checks': dict(counts), 'output': str(a.output)})


if __name__ == '__main__':
    main()
