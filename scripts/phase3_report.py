#!/usr/bin/env python3
"""Account for every requested phase 3 task, separating frozen and staged reviews."""
import argparse
from collections import Counter
import csv
from pathlib import Path
from dbt_bench import io
from phase3_publish import verify_protected
from review_tools import ROOT


def completion_status(rows, frozen_reviews, expected=52):
    formal = [r for r in rows if r['formal_execution_required']]
    reviewed = len(formal) == expected and frozen_reviews == expected
    unresolved = [r['task_key'] for r in formal
                  if r['native_current'] in (None, 'pending') or r['audit_current'] in (None, 'pending')]
    return {'phase3_review_complete': reviewed,
            'phase3_validation_complete': reviewed and not unresolved,
            'unresolved_validation_tasks': unresolved,
            'completion_scope': 'Review completion counts executed pass/fail/gap checks; validation completion additionally requires resolved native and audit conclusions. Eligibility also requires actual candidate success.'}


def report(review, output):
    review, output = Path(review), Path(output)
    if output.exists():
        raise ValueError('Keep reports immutable; use a new output directory')
    protected = verify_protected(review)
    state = io.read_json(review / 'state.json')
    def checked(ref, label):
        if not io.evidence_ok(ref, ROOT):
            raise ValueError('Report evidence changed: ' + label)
        return ROOT / ref['path']
    checked(state['inventory'], 'inventory')
    checked(state['latest_freeze'], 'latest freeze')
    inventory = io.read_json(ROOT / state['inventory']['path'])
    latest = io.read_json(ROOT / state['latest_freeze']['path'])
    candidates = io.read_jsonl(checked(latest['candidates'], 'frozen candidates'))
    if len({r['task_key'] for r in candidates}) != len(candidates):
        raise ValueError('Duplicate frozen candidate IDs')
    records = {**state['completed'], **state['ready']}
    rows = []
    for item in inventory['tasks']:
        key = item['task_key']
        prior = io.read_json(checked(item['previous_record'], 'previous task record'))
        ref = records.get(key)
        rec = io.read_json(checked(ref, 'phase3 companion')) if ref else None
        row = {'task_key': key, 'split': 'train', 'source': item['source'], 'category': item['category'],
            'formal_execution_required': item['formal_execution_required'],
            'review_state': ('frozen' if key in state['completed'] else 'staged') if rec else item['status'],
            'native_previous': prior['native']['conclusion'],
            'native_current': rec['native']['conclusion'] if rec else None,
            'audit_current': rec['audit']['conclusion'] if rec else None,
            'formal_candidate_audit': (rec.get('formal_candidate_evaluation') or {}).get('status') if rec else None,
            'formal_candidate_review': (rec.get('formal_candidate_evaluation') or {}).get('review') if rec else None,
            'native1_both_clean_builds': rec['reproduction']['native1_both_clean_builds'] if rec else None,
            'eligible_after_review': rec['eligible_for_sampling'] if rec else False,
            'eligible_in_frozen_pool': next(r['eligible_for_sampling'] for r in candidates if r['task_key'] == key),
            'unchecked_difference_reported': item.get('external_result', {}).get('unchecked_diff', ''),
            'gold_derived_correction': item.get('gold_derived_correction', False),
            'original_record': item['previous_record'], 'selected_inputs': item.get('selected_inputs', {}),
            'phase3_record': ref,
            'scope': 'Formal executed review' if rec else ('Awaiting formal phase 3 review' if item['formal_execution_required'] else
                    'Excluded by user from supplied external triage or retained incompatibility; no new native certification asserted')}
        rows.append(row)
    if len(rows) != 62 or len({r['task_key'] for r in rows}) != 62:
        raise ValueError('Phase 3 task accounting mismatch')
    summary = {'schema_version': 1, 'model_requests': 0, 'native_rewards_modified': False,
        'inventory': state['inventory'], 'latest_freeze': state['latest_freeze'],
        'formal_tasks': 52, 'excluded_or_retained_incompatible': 10,
        'reviewed': len(records), 'frozen_reviews': len(state['completed']), 'staged_reviews': len(state['ready']),
        **completion_status(rows, len(state['completed'])),
        'current_frozen_train_eligible': sum(r['split'] == 'train' and r['eligible_for_sampling'] for r in candidates),
        'test_candidates_unchanged': latest['test_candidates_unchanged'],
        'protected_baseline_files_verified': protected,
        'counts_by_category': {category: {'total': sum(r['category'] == category for r in rows),
            'reviewed': sum(r['category'] == category and r['phase3_record'] is not None for r in rows),
            'audit_validated': sum(r['category'] == category and r['audit_current'] == 'validated' for r in rows),
            'formal_candidate_audit_failures': sum(r['category'] == category and r['formal_candidate_audit'] == 'fail' for r in rows),
            'eligible_after_review': sum(r['category'] == category and r['eligible_after_review'] for r in rows)}
            for category in inventory['counts']},
        'execution_summary': state.get('execution_summary')}
    output.mkdir(parents=True)
    io.write_jsonl(output / 'tasks.jsonl', rows)
    fields = ['task_key', 'category', 'review_state', 'native_previous', 'native_current', 'audit_current',
              'native1_both_clean_builds', 'formal_candidate_audit', 'eligible_after_review', 'eligible_in_frozen_pool', 'unchecked_difference_reported']
    with (output / 'tasks.csv').open('w', newline='') as stream:
        w = csv.DictWriter(stream, fieldnames=fields, extrasaction='ignore'); w.writeheader(); w.writerows(rows)
    summary['tasks'] = io.evidence_ref(output / 'tasks.jsonl', ROOT)
    io.write_json(output / 'summary.json', summary)
    return {k: summary[k] for k in ('reviewed', 'frozen_reviews', 'staged_reviews', 'current_frozen_train_eligible', 'phase3_review_complete', 'phase3_validation_complete')}


if __name__ == '__main__':
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--review', type=Path, required=True)
    p.add_argument('--output', type=Path, required=True)
    a = p.parse_args()
    print(report(a.review, a.output))
