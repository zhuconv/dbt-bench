#!/usr/bin/env python3
"""Bind the final phase3+phase4 population without changing historical splits."""
import argparse
from collections import Counter
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT / 'src'), str(ROOT / 'scripts')]
from dbt_bench import io


def checked(ref):
    if not io.evidence_ok(ref, ROOT):
        raise ValueError('Frozen population evidence changed: ' + ref['path'])
    return ROOT / ref['path']


def build(phase4_manifest):
    rules = io.read_json(ROOT / 'configs/difficulty-v1/measurement-rules.json')
    split_rules = io.read_json(checked(rules['split_rules']))
    old = io.read_json(checked(rules['initial_pool']))
    phase4_manifest = Path(phase4_manifest)
    latest = io.read_json(phase4_manifest)
    if (not latest['phase4_review_complete'] or latest['reviewed_total'] != 37
            or latest['excluded_reviews'] != 3 or not latest['train_candidates_unchanged']):
        raise ValueError('Complete all37 formal reviews and3 exclusions before binding the final population')
    old_rows = io.unique_index(io.read_jsonl(checked(old['candidates'])), 'task_key')
    rows = io.unique_index(io.read_jsonl(checked(latest['candidates'])), 'task_key')
    catalog = io.unique_index(io.read_jsonl(checked(split_rules['catalog'])), 'task_key')
    if set(old_rows) != set(rows) or set(rows) != set(catalog):
        raise ValueError('Population/catalog task IDs differ')
    inventory = io.read_json(checked(latest['inventory']))
    if inventory['pilot_results'] != rules['phase4_input']:
        raise ValueError('Phase4 used a different external input inventory')
    formal = {x['task_key'] for x in inventory['tasks'] if x['formal_execution_required']}
    exclusions = {x['task_key']: x for x in inventory['tasks'] if not x['formal_execution_required']}
    if len(formal) != 37 or len(exclusions) != 3:
        raise ValueError('Unexpected phase4 scope')
    tasks = []
    for key, row in sorted(rows.items()):
        if row['split'] != old_rows[key]['split'] or row['task_family'] != catalog[key]['task_family']:
            raise ValueError('Historical split or family changed before the authorized new draw')
        if old_rows[key]['split'] == 'train' and row != old_rows[key]:
            raise ValueError('The completed phase3 train population changed')
        eligible = row['eligible_for_sampling'] is True
        phase = 4 if key in formal else 3
        review_ref = row.get('phase4_record' if phase == 4 else 'phase3_record')
        review = io.read_json(checked(review_ref)) if eligible else None
        if eligible and (not review['eligible_for_sampling'] or review['task_version'] != row['task_version']
                         or review['audit']['conclusion'] != 'validated'
                         or review['native']['conclusion'] == 'broken'
                         or not review['formal_candidate_evaluation']['passed']):
            raise ValueError('Candidate qualification differs from actual reviewed submission')
        if key in exclusions and eligible:
            raise ValueError('Excluded task entered difficulty population')
        tasks.append({'task_key': key, 'task_version': row.get('task_version'), 'source': row['source'],
            'task_family': row['task_family'], 'workflow': catalog[key]['workflow'],
            'historical_split': row['split'], 'eligible_for_measurement': eligible,
            'native_conclusion': row['native'], 'audit_conclusion': row['audit'],
            'exclusion_category': exclusions.get(key, {}).get('category'),
            'qualification_phase': phase if eligible else None, 'qualified_review': review_ref if eligible else None,
            'frozen_pool_row': row, 'training_prohibited': True})
    if sum(t['eligible_for_measurement'] and t['historical_split'] == 'train' for t in tasks) != 46:
        raise ValueError('Initial46 candidates were lost or added')
    return {'schema_version': 1, 'version': 'difficulty-population-v1',
        'phase4_review_complete': True, 'phase3_pool': rules['initial_pool'],
        'phase4_pool': io.evidence_ref(phase4_manifest, ROOT),
        'measurement_rules': io.evidence_ref(ROOT / 'configs/difficulty-v1/measurement-rules.json', ROOT),
        'split_rules': rules['split_rules'], 'tasks': tasks,
        'eligible_count': sum(t['eligible_for_measurement'] for t in tasks),
        'eligible_by_source': dict(Counter(t['source'] for t in tasks if t['eligible_for_measurement'])),
        'attempts_per_task': 4, 'model_requests': 0, 'training_prohibited': True,
        'old_split_preserved': True, 'new_split_drawn': False,
        'builder': io.evidence_ref(Path(__file__), ROOT)}


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--phase4-manifest', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    if args.output.exists():
        raise ValueError('Keep population manifests immutable')
    result = build(args.phase4_manifest)
    io.write_json(args.output, result)
    print({key: result[key] for key in ('eligible_count', 'eligible_by_source', 'phase4_review_complete')})
