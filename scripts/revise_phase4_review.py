#!/usr/bin/env python3
"""Add a fully rechecked audit revision without editing prior reviews/scores."""
import argparse
from copy import deepcopy
from pathlib import Path
import shutil

from dbt_bench import io
from phase4_publish import stage, verify_protected
from review_tools import ROOT


def bind_revision(prior, replacement, prior_ref, reason):
    replacement = deepcopy(replacement)
    old_native_ref = prior['native'].get('overlay')
    new_native_ref = replacement['native'].get('overlay')
    if old_native_ref and new_native_ref and old_native_ref['sha256'] == new_native_ref['sha256']:
        replacement['native']['overlay'] = deepcopy(old_native_ref)
    for field in ('task_key', 'task_version', 'phase4_identity', 'native', 'reproduction'):
        if replacement[field] != prior[field]:
            raise ValueError('Audit revision changed task/native/reproduction identity: ' + field)
    if replacement['overlays']['native'] != prior['overlays']['native']:
        raise ValueError('Native review must remain unchanged')
    if not reason.strip() or replacement['overlays']['audit'] == prior['overlays']['audit']:
        raise ValueError('Require a new audit version and an explicit revision reason')
    return {**replacement, 'previous_companion': prior_ref,
        'amendment': {'kind': 'reviewed_audit_revision', 'reason': reason,
            'new_completed_task_credit': 0, 'old_scores_preserved': True,
            'previous_checker_version': prior['audit']['checker_version']}}


def revise(review, audit, candidate, output, reason, root=ROOT):
    review, audit, candidate, output, root = map(Path, (review, audit, candidate, output, root))
    if output.exists() or not str(output.resolve()).startswith('/scratch/'):
        raise ValueError('Use a new SCRATCH revision directory')
    verify_protected(review, root)
    state_file = review / 'state.json'; state_hash = io.file_hash(state_file)
    state = io.read_json(state_file); overlay = io.read_json(audit); key = overlay['task_key']
    section = 'ready' if key in state['ready'] else 'completed'
    if key not in state[section]:
        raise ValueError('An audit revision requires an existing phase4 review')
    previous_ref = state[section][key]
    if not io.evidence_ok(previous_ref, root):
        raise ValueError('Prior companion changed')
    previous = io.read_json(root / previous_ref['path'])
    if overlay['phase4_identity'] != previous['phase4_identity']:
        raise ValueError('Audit-only revision cannot change task, instruction, input or environment identity')
    # Reuse the full shared publisher's native14/audit14, actual two-build and
    # six-stage score checks in an isolated staging directory. No old record is
    # removed, overwritten or temporarily hidden from the canonical ledger.
    output.mkdir(parents=True)
    isolated = output / 'checked-staging'; isolated.mkdir()
    shutil.copyfile(review / 'protected-files.json', isolated / 'protected-files.json')
    staging_state = deepcopy(state); staging_state.update(ready={}, completed={})
    io.write_json(isolated / 'state.json', staging_state)
    stage(isolated, root / previous['overlays']['native']['path'], audit,
        root / previous['reproduction']['reconciliation']['path'], root, candidate_review=candidate)
    replacement_ref = io.read_json(isolated / 'state.json')['ready'][key]
    replacement = io.read_json(root / replacement_ref['path'])
    # stage copies the identical native overlay to the new private directory;
    # retain the existing immutable reference in the final companion instead.
    if replacement['overlays']['native']['sha256'] != previous['overlays']['native']['sha256']:
        raise ValueError('Native overlay bytes changed')
    replacement['overlays']['native'] = previous['overlays']['native']
    updated = bind_revision(previous, replacement, previous_ref, reason)
    updated['revision_publisher'] = io.evidence_ref(Path(__file__), root)
    target = output / 'record.json'; io.write_json(target, updated)
    if io.file_hash(state_file) != state_hash:
        raise ValueError('Canonical state changed during review; preserve outputs and reconcile explicitly')
    result_ref = io.evidence_ref(target, root)
    state[section][key] = result_ref
    state.setdefault('audit_revisions', []).append({'task_key': key, 'previous': previous_ref,
                                                   'replacement': result_ref, 'new_completed_task_credit': 0})
    io.write_json(state_file, state)
    return {'task_key': key, 'audit': updated['audit']['conclusion'],
        'eligible': updated['eligible_for_sampling'], 'section': section,
        'new_completed_task_credit': 0, 'previous_record_preserved': True}


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--review', type=Path, required=True)
    parser.add_argument('--audit', type=Path, required=True)
    parser.add_argument('--candidate-review', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--reason', required=True)
    args = parser.parse_args()
    print(revise(args.review, args.audit, args.candidate_review, args.output, args.reason))
